"""Local BITS hardware worker. No sockets, queue access or arbitrary commands.

The center supplies a typed immutable plan. Only package-verified tool profiles
can execute. This adapter reuses verified sensor parsing, process supervision
and report rendering, never the OCRUN scheduler, Redis keys or rsync hooks.
"""
import csv
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import threading
import time

ROOT = Path(__file__).absolute().parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

import common
import collector
import data_api
import report_sheet
import sckocp_api
import streaming
import suite
import workload

VERSION = "0.4.0-alpha.1"
# 128 explicitly identified steps plus the initial preparing sample.
report_sheet.MAX_GROUPS = 129
STOP = threading.Event()
CURRENT = {"label": "Preparing", "step_id": None}


def result(directory, execution, report="not_generated", error="", steps=None, quality=None):
    value = {"execution": execution, "quality": quality or "readings_reported_validity_unknown",
             "report": report, "error": error, "steps": steps or []}
    common.save(directory / "result.json", value)
    return value


def request(directory):
    common.directory(directory)
    value = common.json_read(directory / "request.json")
    batch = value["batch"]
    if (batch["plan"]["node"] != os.uname().nodename or
            directory.name != batch["id"] or len(batch["plan"]["steps"]) > 128):
        raise ValueError("Immutable batch/host identity differs")
    return value


def specs(value):
    selected = []
    for index, step in enumerate(value["batch"]["plan"]["steps"], 1):
        if step["id"] != "step-{:03d}".format(index):
            raise ValueError("Step identity differs")
        selected.append(suite.installed_profile(step["tool"], step["seconds"]))
    if not selected:
        raise ValueError("Empty batch")
    return selected


def preflight(directory, value):
    selected = specs(value)
    # Reserve canonical samples, compatibility exports, temporary report and
    # upload/readback margin. Nothing is silently deleted to create space.
    samples = sum(s["runtime_s"] for s in selected) // 2 + 128
    required = (512 << 20) + samples * 16384
    if required > 64 << 30:
        raise ValueError("Estimated evidence exceeds 64 GiB; split this batch")
    if shutil.disk_usage(str(directory)).free < required:
        raise ValueError("Insufficient free space for this batch and report")
    import xlsxwriter
    sample = data_api.sample(include_details=True)
    if sample["status"] != "ok":
        raise ValueError("Licensed sckocp preflight is unavailable: " + sample["status"])
    common.save(directory / "preflight.json", {"checked_at": common.now(), "required_free_bytes": required,
                "profiles": selected, "xlsxwriter": xlsxwriter.__version__,
                "data_quality": "readings_reported_validity_unknown"})


class Sampling:
    def __init__(self, directory):
        self.directory = directory
        self.done = threading.Event()
        self.ready = threading.Event()
        self.error = None
        self.rows = 0
        self.unavailable = 0
        self.thread = threading.Thread(target=self.capture, name="BITS-local-sampling")

    def capture(self):
        stream = None
        try:
            next_details, failures = 0, 0
            while not self.done.is_set():
                began = time.monotonic()
                observed_step = dict(CURRENT)
                if self.rows % 5000 == 0:
                    if stream is not None:
                        stream.flush()
                        os.fsync(stream.fileno())
                        stream.close()
                    name = "telemetry-{:05d}.jsonl".format(self.rows // 5000 + 1)
                    fd = os.open(str(self.directory / name),
                                 os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                    stream = os.fdopen(fd, "w", encoding="utf-8")
                include_details = time.monotonic() >= next_details
                envelope = data_api.sample(include_details=include_details)
                supplemental = envelope.pop("details", None)
                if envelope["status"] != "ok":
                    envelope["data"] = None
                    failures += 1
                    self.unavailable += 1
                else:
                    failures = 0
                context = collector.os_context(observed_step["label"])
                record = {"schema": "bits-telemetry-v1", "sequence": self.rows + 1,
                          "step_id": observed_step["step_id"], "observed_at": common.now(),
                          "os": context, "provider": envelope,
                          "reading_quality": "reported-validity-and-age-unknown"}
                if envelope["status"] == "ok" and supplemental is not None:
                    record["details"] = supplemental
                    next_details = time.monotonic() + 30
                encoded = json.dumps(record, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
                if len(encoded.encode("utf-8")) > 2 << 20:
                    raise ValueError("Sensor record exceeds 2 MiB")
                stream.write(encoded + "\n")
                stream.flush()
                os.fsync(stream.fileno())
                self.rows += 1
                self.ready.set()
                if failures >= 3 or envelope["status"] not in ({"ok"} | collector.RETRYABLE_STATUSES):
                    raise ValueError("Sensor collection unavailable; stopping this batch")
                if shutil.disk_usage(str(self.directory)).free < 128 << 20:
                    raise ValueError("Free space reached 128 MiB reserve; stopping safely")
                self.done.wait(max(0, 2 - (time.monotonic() - began)))
        except BaseException as exc:
            self.error = str(exc)[:1024]
            self.ready.set()
        finally:
            if stream is not None and not stream.closed:
                stream.flush()
                os.fsync(stream.fileno())
                stream.close()

    def health(self):
        if self.error:
            raise ValueError(self.error)

    def close(self):
        self.done.set()
        self.thread.join(35)
        if self.thread.is_alive():
            raise ValueError("Sampling has not stopped; evidence cannot be sealed")


def execute(directory, value):
    # Reject existing execution intent even after an interrupted launch. Recovery
    # may finish evidence but may never re-enter this function.
    journal = directory / "execution.json"
    if journal.exists() or journal.is_symlink():
        raise ValueError("Execution intent already exists; use evidence recovery")
    evidence = directory / "evidence"
    evidence.mkdir(mode=0o700)
    work = directory / "steps"
    work.mkdir(mode=0o700)
    state = {"batch": value["batch"]["id"], "started_at": common.now(), "steps": [],
             "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
             "machine": report_sheet.machine_snapshot(), "execution": "running"}
    common.save(journal, state)
    sampling = Sampling(evidence)
    sampling.thread.start()
    failure = ""
    outcome = "completed"
    try:
        if not sampling.ready.wait(35):
            raise ValueError("Initial monitoring sample timed out")
        sampling.health()
        result(directory, "running")
        for step, profile in zip(value["batch"]["plan"]["steps"], specs(value)):
            if STOP.is_set():
                outcome = "interrupted"
                break
            CURRENT.update({"label": step["id"] + " " + step["tool"], "step_id": step["id"]})
            profile = suite.prepare(profile, work / step["id"])
            record = {"id": step["id"], "name": step["tool"], "runtime_s": step["seconds"],
                      "binary": profile["binary"], "binary_sha256": common.file_hash(Path(profile["binary"]))["sha256"]}
            state["steps"].append(record)

            def save_step(event):
                record.update(event)
                common.save(journal, state)
                result(directory, "running", steps=state["steps"])

            observed = workload.execute(profile, evidence / (step["id"] + ".log"), save_step,
                                        health=sampling.health, cancelled=STOP.is_set)
            if observed["execution"] not in ("duration_reached", "finished") or not observed["cleanup_confirmed"]:
                outcome = "interrupted" if STOP.is_set() else "failed"
                failure = "Step {}: {}".format(step["id"], observed["execution"])
                break
    except BaseException as exc:
        outcome, failure = "interrupted" if STOP.is_set() else "failed", str(exc)[:1024]
    finally:
        sampling.close()
        if sampling.error:
            outcome, failure = "failed", sampling.error
        state.update({"execution": outcome, "ended_at": common.now(), "error": failure,
                      "samples": sampling.rows, "unavailable_samples": sampling.unavailable})
        common.save(journal, state)
        result(directory, outcome, error=failure, steps=state["steps"],
               quality="partial" if sampling.unavailable else "readings_reported_validity_unknown")


def quiescent(state):
    boot = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    if state.get("boot_id") == boot:
        for step in state["steps"]:
            process = step.get("process")
            if not process:
                continue
            current = workload.identity(process["pid"])
            if current and current["start_ticks"] != process["start_ticks"]:
                continue
            if workload.members(process["session"]):
                raise ValueError("Recorded workload session still has processes; stop the BITS node service before recovery")


def recover(directory, value):
    journal = directory / "execution.json"
    if not journal.exists():
        result(directory, "preflight_failed", error="Node restarted before execution was durably accepted",
               quality="not_collected")
        raise ValueError("No execution evidence; close the unstarted batch explicitly")
    state = common.json_read(journal)
    quiescent(state)
    if state["execution"] == "running":
        state.update({"execution": "interrupted", "ended_at": common.now(),
                      "error": "Node execution interrupted; no workload was restarted"})
        common.save(journal, state)
    result(directory, state["execution"], error=state.get("error", ""), steps=state["steps"], quality="partial")


def samples(evidence):
    expected = 1
    for segment in sorted(evidence.glob("telemetry-*.jsonl")):
        with common.opened(segment) as stream:
            while True:
                raw = stream.readline((2 << 20) + 1)
                if not raw:
                    break
                if len(raw) > 2 << 20 or not raw.endswith(b"\n"):
                    raise ValueError("Incomplete telemetry record retained: " + segment.name)
                item = json.loads(raw.decode("utf-8"))
                if item.get("schema") != "bits-telemetry-v1" or item.get("sequence") != expected:
                    raise ValueError("Telemetry sequence gap or invalid schema")
                expected += 1
                provider = item["provider"]
                if provider["status"] == "ok":
                    sckocp_api.provider.validate_original_payload(provider["data"])
                elif provider.get("data") is not None:
                    raise ValueError("Unavailable sample contains stale readings")
                if "details" in item:
                    sckocp_api.provider.validate_details(item["details"])
                yield item


def report(directory, value):
    # Rendering and retry operate solely on sealed batch evidence, never invoke
    # sckocp again and never use present hardware as a historical snapshot.
    state = common.json_read(directory / "execution.json")
    if state["execution"] == "running":
        raise ValueError("Execution is not sealed")
    quiescent(state)
    evidence = directory / "evidence"
    manifest = directory / "artifacts.json"
    if manifest.exists():
        for name, meta in common.json_read(manifest).items():
            if common.file_hash(evidence / name) != meta:
                raise ValueError("Sealed artifact changed: " + name)
        # A crash can happen between the immutable manifest and result update.
        # Restore only publication state from already sealed report evidence.
        record = common.json_read(evidence / "report.json")
        result(directory, state["execution"], report="generated", error=state.get("error", ""),
               steps=state["steps"], quality=record["data_quality"])
        return
    statistics = report_sheet.Statistics()
    export = evidence / "monitor.mon"
    temporary = evidence / ".monitor.tmp"
    count = 0
    try:
        with open(str(temporary), "w", encoding="utf-8", newline="") as stream:
            os.chmod(str(temporary), 0o600)
            stream.write("#-BITS native export; canonical source is segmented bits-telemetry-v1\n")
            writer = csv.writer(stream)
            writer.writerow(collector.HEADERS)
            for item in samples(evidence):
                row = collector.make_row(item["provider"], item["os"], native_format="v1")
                writer.writerow(row)
                statistics.consume(row, item)
                count += 1
            stream.flush()
            os.fsync(stream.fileno())
        if not count:
            raise ValueError("No monitoring samples; no successful report is possible")
        os.replace(str(temporary), str(export))
        xlsx = evidence / "monitor.xlsx"
        if not xlsx.exists():
            tmpxlsx = evidence / ".monitor.tmp.xlsx"
            streaming.render(export, tmpxlsx, "Monitoring")
            os.chmod(str(tmpxlsx), 0o600)
            os.replace(str(tmpxlsx), str(xlsx))
        quality = "partial" if statistics.statuses.get("ok", 0) != count else "readings_reported_validity_unknown"
        artifacts = {p.name: common.file_hash(p) for p in evidence.iterdir()
                     if p.is_file() and not p.name.startswith(".")}
        adapted = {"case": value["batch"]["id"], "task_id": value["batch"]["plan"]["label"],
                   "task_time": value["batch"]["created_at"], "created_at": state["started_at"],
                   "sealed_at": state["ended_at"], "version": VERSION, "steps": state["steps"],
                   "remote": value["batch"]["plan"]["node"] + "_" + value["serial"],
                   "machine_snapshot": state["machine"], "execution_result": state["execution"],
                   "execution_error": state.get("error"), "data_quality": quality, "artifacts": artifacts}
        record = report_sheet.make_record(adapted, statistics)
        record["schema"] = "bits-acceptance-report-v1"
        record["statistics"]["details"]["schema"] = "bits-report-details-v1"
        record["plan"] = value["batch"]["plan"]
        record["source_artifacts"] = artifacts
        record["statistics"]["task_grouping"] = "unique step_id and tool; repeated tools remain separate"
        record["delivery"] = "Verify the separate BITS receipt.json and each artifact SHA-256."
        record["time_basis"] = "Native records include UTC observed_at, sequence and unique step_id; export preserves node-local display time."
        common.save(evidence / "report.json", record)
        common.atomic(evidence / "report.html", report_sheet.render(record))
        artifacts.update({n: common.file_hash(evidence / n) for n in ("report.json", "report.html")})
        common.save(manifest, artifacts)
        result(directory, state["execution"], report="generated", error=state.get("error", ""),
               steps=state["steps"], quality=quality)
    except BaseException as exc:
        result(directory, state["execution"], report="failed", error=str(exc)[:1024],
               steps=state["steps"], quality="partial")
        raise


def main():
    os.umask(0o077)
    if os.geteuid() != 0:
        raise ValueError("Node hardware worker requires root")
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *args: STOP.set())
    if len(sys.argv) != 3 or sys.argv[1] not in ("preflight", "execute", "recover", "report"):
        raise ValueError("Use the BITS node agent")
    action, directory = sys.argv[1], Path(sys.argv[2])
    value = request(directory)
    globals()[action](directory, value)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print("BITS worker: " + str(exc), file=sys.stderr)
        sys.exit(1)
