"""Explicit node batches and verifiable finalization; no idle polling or activation."""
import argparse
import contextlib
import csv
import fcntl
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import sys
import tempfile
import time

ROOT = Path(__file__).absolute().parent
sys.path.insert(0, str(ROOT))
from common import (ENTRY, HELPER, STATE, VERSION, atomic, digest, directory, file_hash,
                    json_read, lock, now, opened, read, regular, run, save, snapshot)
import report_sheet

REPORT = Path("/usr/local/bin/mon-sensors-report")
CASE_RE = re.compile(r"[0-9a-f]{32}\Z")
NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,219}\Z")
REMOTE_RE = re.compile(r"([A-Za-z0-9][A-Za-z0-9.-]{0,252})(?::([0-9]{1,5}))?::logs/([A-Za-z0-9][A-Za-z0-9_.-]{0,127})\Z")
WORKLOADS = frozenset(("stress", "stress-ng", "mprime", "xhpl", "mlc", "mbw", "bcr", "bcfi", "bcfd",
                       "sysjitter", "cyclictest", "unixbench", "cpu2017", "runcpu", "ptu"))
TERMINAL = frozenset(("complete", "closed_incomplete"))


def safe_name(value):
    if not NAME_RE.fullmatch(value) or ".." in value:
        raise ValueError("Invalid task or file name")
    return value


def remote_parts(value):
    match = REMOTE_RE.fullmatch(value)
    if not match or ".." in value or (match.group(2) and not 1 <= int(match.group(2)) <= 65535):
        raise ValueError("Expected host::logs/node (or host:port::logs/node)")
    return match.group(1) + "::logs/" + match.group(3), match.group(2)


def paths(app, state):
    mon = Path(state["mon"])
    directory(mon.parent)
    safe_name(mon.name)
    if not mon.name.endswith(".mon") or mon.parent == app or app in mon.parents:
        raise ValueError("Monitoring data must be outside the application directory")
    result = [mon, Path(str(mon) + ".sckocp.jsonl"), mon.with_suffix(".xlsx")]
    if state.get('detailed_report_schema') == report_sheet.SCHEMA:
        result += [mon.with_suffix('.report.json'), mon.with_suffix('.report.html')]
    elif state.get('detailed_report_schema') is not None:
        raise ValueError('Unknown detailed report schema; preserve this batch')
    return result


def root_state(app, create=False):
    directory(app)
    target = app / STATE
    if create:
        target.mkdir(mode=0o700, exist_ok=True)
    directory(target)
    if target.stat().st_mode & 0o077:
        raise ValueError("Finalization state directory must be private")
    return target


def load_case(app, case):
    if not CASE_RE.fullmatch(case):
        raise ValueError("Invalid case identifier")
    state = json_read(root_state(app) / (case + ".json"))
    if (state.get("schema") != "mon-sensors-finish-v1" or state.get("case") != case or
            state.get("app") != str(app)):
        raise ValueError("Case identity does not match this application")
    paths(app, state)
    remote_parts(state["remote"])
    return state


def persist(app, state, stage=None, error=None):
    state["updated_at"] = now()
    if stage is not None:
        state["stage"] = stage
    state["error"] = error
    event = {"at": state["updated_at"], "stage": state["stage"], "error": error}
    state["events"] = (state.get("events", []) + [event])[-128:]
    save(root_state(app) / (state["case"] + ".json"), state)


def cases(app):
    if not (app / STATE).exists():
        return []
    return [load_case(app, p.stem) for p in sorted(root_state(app).glob("*.json")) if CASE_RE.fullmatch(p.stem)]


def collector_runtime(app):
    directory(app)
    if read(app / ".mon-sensors-backend", 32) != b"sckocp\n":
        raise ValueError("Automatic finalization currently requires the explicit sckocp backend")
    headless = (app / '.bits-collector.d').exists()
    modules = app / ('.bits-collector.d' if headless else 'mon-sensors-plugin.d')
    marker = json_read(modules / ".mon-sensors-plugin")
    if marker.get("owner") != "mon-sensors-plugin-v1":
        raise ValueError("A managed mon-sensors-plugin installation is required")
    for name, expected in marker["files"].items():
        if ".." in Path(name).parts or Path(name).is_absolute():
            raise ValueError("Unsafe plugin manifest")
        if digest(read(modules / name)) != expected:
            raise ValueError("Installed collector differs from its manifest")
    entry = app / ('.bits-collector' if headless else 'mon-sensors-plugin')
    if digest(read(entry)) != marker.get("launcher_sha256"):
        raise ValueError("Collector launcher differs from its manifest")
    runtime_file = modules / "mon_sensors_plugin/runtime.py"
    if "mon_sensors_plugin/runtime.py" not in marker["files"]:
        raise ValueError("Collector runtime supervision is required")
    spec = importlib.util.spec_from_file_location("finish_monitor_runtime", str(runtime_file))
    runtime = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runtime)
    # Reuse the already-installed API's strict JSON/schema validators without
    # calling its hardware collector. A private name avoids unrelated imports.
    package_name = "_finish_api_" + digest(str(modules).encode("utf-8"))[:12]
    if package_name not in sys.modules:
        package_spec = importlib.util.spec_from_file_location(package_name, str(modules / "sckocp_api/__init__.py"),
            submodule_search_locations=[str(modules / "sckocp_api")])
        package = importlib.util.module_from_spec(package_spec)
        sys.modules[package_name] = package
        package_spec.loader.exec_module(package)
    runtime.payload_validator = importlib.import_module(package_name + ".provider")
    runtime.collector_entry = entry
    return runtime


def prerequisites(app, allow_legacy_report=False):
    runtime = collector_runtime(app)
    check = json.loads(run([REPORT, "--check"], 30).decode("utf-8"))
    accepted = ('0.1.1', '0.2.0') if allow_legacy_report else ('0.2.0',)
    if check.get("status") != "ok" or check.get("version") not in accepted:
        raise ValueError("The streaming report supplement 0.2.0 is required")
    rsync = shutil.which("rsync", path="/usr/bin:/bin:/usr/local/bin")
    if not rsync:
        raise ValueError("rsync is required")
    run([rsync, "--version"], 10)
    return runtime, rsync


def require_idle():
    busy = []
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            name = (entry / "comm").read_text().strip()
            status = (entry / "stat").read_text().rsplit(")", 1)[1].split()[0]
        except (FileNotFoundError, ProcessLookupError):
            continue
        if name in WORKLOADS and status != "Z":
            busy.append(entry.name)
    if busy:
        raise ValueError("Load processes still exist; finalization did not stop them (PID {})".format(
            ",".join(busy[:20])))


@contextlib.contextmanager
def stopped_monitor(app, mon, runtime):
    """Stop only a proven supervisor for this log, then retain its application lock."""
    fd = runtime._open_lock(str(app), create=True)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            pid, identity = runtime._verified_owner(fd, str(app))
            argv = [os.fsdecode(x) for x in identity[1].split(b"\0")[:-1]]
            command = argv[argv.index("--") + 1:] if "--" in argv else []
            # Both the legacy bridge and the direct plugin supervisor put the log
            # after the interval. Never match a pathname occurring in a random flag.
            entries = (str(app / "mon-sensors"), str(app / "mon-sensors-plugin.d/mon-sensors-plugin"),
                       str(app / '.bits-collector.d/mon-sensors-plugin'))
            matched = False
            for target in entries:
                if target in command:
                    index = command.index(target)
                    matched = len(command) > index + 2 and command[index + 2] == str(mon)
            if not matched or runtime._verified_owner(fd, str(app)) != (pid, identity):
                raise ValueError("A different or unverifiable monitoring session is active")
            os.kill(pid, signal.SIGTERM)
            deadline = time.monotonic() + 12
            while True:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise ValueError("Collector stop did not release its lock")
                    time.sleep(.05)
        if runtime._inode(os.lstat(str(app / runtime.LOCK_NAME))) != runtime._inode(os.fstat(fd)):
            raise ValueError("Collector lock was replaced")
        yield
    finally:
        os.close(fd)


def check_summaries(row, payload):
    """Nonempty aggregate columns must agree with their paired provider record.

    Empty columns remain permitted: freshness filtering may deliberately omit a
    value even though the unmodified provider response is retained in the sidecar.
    """
    v1 = payload["schema"] == "sckocp-mon-v1"
    def values(items, field):
        result = [item.get(field) if v1 else item["metrics"][field]["value"]
                  if item["metrics"][field]["status"] == "ok" else None for item in items]
        return result if result and all(v is not None for v in result) else []
    sources = ((5, payload["cores"], "mhz" if v1 else "active_mhz", lambda xs: sum(xs) / len(xs)),
               (6, payload["sockets"], "temp_max_c" if v1 else "temperature_c", max),
               (8, payload["sockets"], "pkg_w" if v1 else "package_watts", sum))
    if not v1:
        sources += ((9, [payload["system"]], "psu_input_watts", sum),)
    for index, items, field, aggregate in sources:
        if row[index]:
            readings = values(items, field)
            expected = aggregate(readings) if readings else None
            if expected is None or not math.isfinite(expected) or abs(float(row[index]) - expected) > .000001:
                raise ValueError("Monitoring/JSON sensor summary differs")
    if row[7] or row[10] or (v1 and row[9]):
        raise ValueError("Unsupported sensor column contains a fabricated value")


def validate_logs(mon, sidecar, validator, statistics=None):
    """Stream paired records and preserve invalid-provider counts; no invented readings."""
    count, unavailable, task_counts = 0, 0, {}
    first_time = last_time = None
    before = [snapshot(mon), snapshot(sidecar)]
    with opened(mon, locked=True) as left, opened(sidecar, locked=True) as right:
        if not left.readline(8192).startswith(b"#-") or not left.readline(8192).startswith(b"#="):
            raise ValueError("Invalid monitoring header")
        while True:
            row_bytes = left.readline(16385)
            detail_bytes = right.readline(2 * 1024 * 1024 + 1)
            if not row_bytes and not detail_bytes:
                break
            if (not row_bytes or not detail_bytes or len(row_bytes) > 16384 or
                    len(detail_bytes) > 2 * 1024 * 1024 or
                    not row_bytes.endswith(b"\n") or not detail_bytes.endswith(b"\n")):
                raise ValueError("Monitoring/JSON rows are incomplete or unpaired")
            row = next(csv.reader([row_bytes.decode("utf-8")], strict=True))
            detail = validator._decode_json(detail_bytes)
            if not isinstance(detail, dict) or not isinstance(detail.get("os"), dict):
                raise ValueError("Invalid monitoring record structure")
            if (len(row) != 11 or row[0] not in ("sckocp-v1", "sckocp-v2") or
                    detail.get("schema") != "mon-sensors-sckocp-v1" or
                    detail.get("os", {}).get("time") != row[1] or
                    detail.get("os", {}).get("task") != row[2]):
                raise ValueError("Monitoring/JSON record identities differ")
            for value in row[3:10]:
                if value and not math.isfinite(float(value)):
                    raise ValueError("Non-finite monitoring value")
            provider = detail.get("provider", {})
            if not isinstance(provider, dict):
                raise ValueError("Invalid provider record structure")
            status = provider.get("status")
            if provider.get("schema") != "sckocp-api-v1" or status not in set(validator.ERRORS) | {"ok"}:
                raise ValueError("Missing or invalid provider status")
            if status == "ok":
                if row[0] == "sckocp-v1":
                    validator.validate_original_payload(provider.get("data"))
                else:
                    validator.validate_payload(provider.get("data"))
                if provider.get("error") is not None:
                    raise ValueError("Successful provider record contains an error")
                check_summaries(row, provider["data"])
            elif provider.get("data") is not None or any(row[5:]):
                raise ValueError("Failed samples must not contain stale measurements")
            if "details" in detail:
                if status != "ok":
                    raise ValueError("Failed base sample contains supplemental data")
                validator.validate_details(detail["details"])
            if "details_interval_s" in detail:
                validator._number(detail["details_interval_s"], 10, 3600)
            for index, key in ((3, "uptime_seconds"), (4, "load1")):
                expected = detail["os"].get(key)
                if (expected is None and row[index]) or (expected is not None and (
                        not row[index] or isinstance(expected, bool) or not isinstance(expected, (int, float)) or
                        not math.isfinite(expected) or expected < 0 or abs(float(row[index]) - expected) > .000001)):
                    raise ValueError("Monitoring/JSON system context differs")
            unavailable += int(status != "ok")
            first_time = first_time or row[1]
            last_time = row[1]
            count += 1
            if count > 4000000 or left.tell() > 16 * 1024 ** 3 or right.tell() > 16 * 1024 ** 3:
                raise ValueError("Monitoring log exceeds this report build's limits")
            task_counts[row[2]] = task_counts.get(row[2], 0) + 1
            if statistics is not None:
                statistics.consume(row, detail)
    if count == 0 or before != [snapshot(mon), snapshot(sidecar)]:
        raise ValueError("Monitoring logs are empty or changed during validation")
    return {"rows": count, "unavailable_samples": unavailable, "task_samples": task_counts,
            "measurement_validity": "provider_reported_not_independently_verified",
            "first_sample_time": first_time, "last_sample_time": last_time}


def stable_sources(files, expected):
    for path in files:
        if file_hash(path) != expected[path.name]:
            raise ValueError("A sealed artifact changed: " + path.name)


def transfer(rsync, state, files, expected, stage):
    remote, port = remote_parts(state["remote"])
    options = [rsync, "--contimeout=10", "--timeout=30"]
    if port:
        options.append("--port=" + port)
    run(options + ["-t", "--checksum", "--chmod=F600", "--"] + files + [remote + "/"], 3600)
    for path in files:
        path = Path(path)
        with tempfile.TemporaryDirectory(prefix="readback-", dir=str(stage)) as temporary:
            local = Path(temporary) / path.name
            run(options + ["-t", "--no-links", "--max-size=" + str(expected[path.name]["bytes"] + 1),
                           "--", remote + "/" + path.name, str(local)], 3600)
            if file_hash(local) != expected[path.name]:
                raise ValueError("Remote read-back mismatch: " + path.name)


def begin(app, mon, remote, task_id, task_time):
    runtime, unused = prerequisites(app)
    require_idle()
    fd = runtime._open_lock(str(app), create=True)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
    safe_name(task_id)
    safe_name(task_time)
    remote_parts(remote)
    mon = Path(mon)
    candidate = {"mon": str(mon), 'detailed_report_schema': report_sheet.SCHEMA}
    files = paths(app, candidate)
    if not mon.name.endswith("_{}_{}.mon".format(task_id, task_time)):
        raise ValueError("Monitoring filename does not match the task identity")
    case = digest((str(app) + "\n" + str(mon)).encode("utf-8"))[:32]
    if (root_state(app) / (case + ".json")).exists():
        raise ValueError("This task identity already has a record; use status/retry or a new task time")
    if any(p.exists() or p.is_symlink() for p in files + [mon.with_suffix(".finish.json")]):
        raise ValueError("Task outputs already exist; use a new task time to preserve them")
    if any(s["stage"] not in TERMINAL for s in cases(app)):
        raise ValueError("Another task batch is unfinished; inspect status before starting")
    state = {"schema": "mon-sensors-finish-v1", "version": VERSION, "case": case,
             "app": str(app), "mon": str(mon), "remote": remote, "task_id": task_id,
             "task_time": task_time, "created_at": now(), "stage": "collecting",
             "steps": [], "sealed": False, "artifacts": {}, "attempts": 0,
             'detailed_report_schema': report_sheet.SCHEMA,
             'machine_snapshot': report_sheet.machine_snapshot()}
    persist(app, state)
    return state


def step(app, state, name, event, runtime=None, launch_code=None):
    safe_name(name)
    if state["sealed"] or state["stage"] != "collecting":
        raise ValueError("This batch is no longer accepting load steps")
    steps = state["steps"]
    if event == "start":
        if steps and steps[-1].get("ended_at") is None:
            raise ValueError("Previous load step is unfinished")
        if runtime is None or not 0 <= runtime <= 31 * 86400 or len(steps) >= 10000:
            raise ValueError("Invalid runtime or too many steps")
        steps.append({"name": name, "runtime_s": runtime, "started_at": now()})
    else:
        if not steps or steps[-1]["name"] != name or steps[-1].get("ended_at") is not None:
            raise ValueError("No matching active load step")
        steps[-1].update({"ended_at": now(), "launch_code": launch_code})
    persist(app, state)


def finish(app, state, recovering=False):
    if state["stage"] == "closed_incomplete":
        raise ValueError("This incomplete record was explicitly closed; use a new task identity")
    if state["stage"] == "complete":
        stable_sources(paths(app, state), state["artifacts"])
        return state  # Idempotent: do not rebuild the workbook or launch any task.
    if not state["sealed"]:
        if not recovering and (not state["steps"] or any("ended_at" not in s for s in state["steps"])):
            raise ValueError("A load step is unfinished; explicit interrupted recovery is required")
        state["sealed"] = True
        state["outcome"] = ("interrupted" if recovering else "workload_launch_failed" if any(
            s.get("launch_code") != 0 for s in state["steps"]) else "scheduler_finished")
        if state.get('execution_result') not in (None, 'completed'):
            state['outcome'] = state['execution_result']
        state["sealed_at"] = now()
    state["attempts"] += 1
    persist(app, state, "stopping")
    try:
        require_idle()
        runtime = collector_runtime(app)
        files = paths(app, state)
        with stopped_monitor(app, files[0], runtime):
            for source in files[:2]:
                with opened(source) as stream:
                    os.fsync(stream.fileno())
            persist(app, state, "validating")
            statistics = report_sheet.Statistics() if state.get('detailed_report_schema') else None
            summary = validate_logs(files[0], files[1], runtime.payload_validator, statistics)
            source_hashes = {p.name: file_hash(p) for p in files[:2]}
            if state["artifacts"]:
                stable_sources(files[:2], state["artifacts"])
            else:
                state["artifacts"] = source_hashes
            state["samples"] = summary
            state['data_quality'] = 'provider_unavailable' if summary['unavailable_samples'] else 'readings_reported_validity_unknown'
            persist(app, state, "reporting")
            unused, rsync = prerequisites(app)
            if files[2].name not in state["artifacts"]:
                result = json.loads(run([REPORT, files[0], files[2]], 3600).decode("utf-8"))
                if (result.get("status") != "ok" or result.get("rows") != summary["rows"] or
                        result.get("source_sha256") != source_hashes[files[0].name]["sha256"]):
                    raise ValueError("Report result does not match the sealed monitoring file")
                output = file_hash(files[2])
                if result.get("sha256") != output["sha256"]:
                    raise ValueError("Workbook hash differs from report result")
                state["artifacts"][files[2].name] = output
                persist(app, state)
            if statistics is not None:
                persist(app, state, 'acceptance_report')
                if all(p.name in state['artifacts'] for p in files[3:]):
                    stable_sources(files[3:], state['artifacts'])
                else:
                    state['artifacts'].update(report_sheet.create(state, statistics))
                    persist(app, state)
            stable_sources(files, state["artifacts"])
            persist(app, state, "uploading_and_verifying")
            # Bounded retries never recollect data or regenerate a successful workbook.
            for attempt in range(3):
                try:
                    transfer(rsync, state, files, state["artifacts"], root_state(app))
                    break
                except (OSError, ValueError):
                    if attempt == 2:
                        raise
                    time.sleep(2 * (attempt + 1))
            stable_sources(files, state["artifacts"])
            state.setdefault("data_verified_at", now())
            persist(app, state, "publishing_receipt")
            receipt = {"schema": "mon-sensors-finish-receipt-v2" if statistics is not None else "mon-sensors-finish-receipt-v1", "case": state["case"],
                       "task_id": state["task_id"], "task_time": state["task_time"],
                       "outcome": state["outcome"], "samples": summary, "steps": state["steps"],
                       "artifacts": state["artifacts"], "data_verified_at": state["data_verified_at"],
                       "verification": "rsync-download-sha256", "version": VERSION,
                       "execution_result": state.get('execution_result', 'legacy_launch_status_only'),
                       "data_quality": state['data_quality'], "report_result": "generated",
                       "delivery_result": "data_verified"}
            if statistics is not None:
                receipt['detailed_report_schema'] = report_sheet.SCHEMA
            receipt_path = files[0].with_suffix(".finish.json")
            save(receipt_path, receipt)
            receipt_hash = {receipt_path.name: file_hash(receipt_path)}
            transfer(rsync, state, [receipt_path], receipt_hash, root_state(app))
            stable_sources(files, state["artifacts"])
            state["receipt"] = receipt_hash
            persist(app, state, "complete")
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as error:
        persist(app, state, error=str(error)[:512])
        raise
    return state


def close_incomplete(app, state, reason):
    if state["stage"] in TERMINAL:
        raise ValueError("This record is already closed")
    if not reason.strip() or len(reason) > 512 or any(ord(char) < 32 for char in reason):
        raise ValueError("A plain, nonempty reason of at most 512 characters is required")
    require_idle()
    runtime = collector_runtime(app)
    with stopped_monitor(app, paths(app, state)[0], runtime):
        state["failure_before_close"] = {"stage": state["stage"], "error": state.get("error")}
        state["closure_reason"] = reason
        state["closed_at"] = now()
        state["sealed"] = True
        state["outcome"] = "operator_closed_incomplete"
        # Preserve data, existing reports, hashes and prior events. No upload or
        # successful receipt is manufactured for an unrecoverable collection.
        persist(app, state, "closed_incomplete")
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", required=True)
    parser.add_argument("--version", action="version", version=ENTRY + " " + VERSION)
    sub = parser.add_subparsers(dest="action")
    sub.add_parser("check").add_argument("--scheduler", action="store_true")
    status = sub.add_parser("status")
    status.add_argument('--case')
    status.add_argument('--human', action='store_true')
    sub.add_parser('start')
    adoption = sub.add_parser('adopt-workloads')
    adoption.add_argument('--task', action='append', required=True)
    adoption.add_argument('--check', action='store_true')
    for name in ('preflight', 'run-queue'):
        node_command = sub.add_parser(name)
        for flag in ('rdb-server', 'log-server', 'log-dir', 'node', 'serial'):
            node_command.add_argument('--' + flag, required=True)
        node_command.add_argument('--rdb-port', type=int, default=6379)
    start = sub.add_parser("begin")
    for flag in ("mon", "remote", "task-id", "task-time"):
        start.add_argument("--" + flag, required=True)
    start.add_argument("--print-id", action="store_true")
    item = sub.add_parser("step")
    item.add_argument("--case", required=True)
    item.add_argument("--name", required=True)
    item.add_argument("--event", required=True, choices=("start", "end"))
    item.add_argument("--runtime", type=int)
    item.add_argument("--launch-code", type=int, default=0)
    for name in ("finish", "retry", "recover", "interrupted", "close-incomplete", "stop"):
        command = sub.add_parser(name)
        command.add_argument("--case", required=True)
        if name == "recover":
            command.add_argument("--interrupted", action="store_true", required=True)
        if name == "close-incomplete":
            command.add_argument("--reason", required=True)
    args = parser.parse_args()
    if not args.action:
        parser.error("An action is required")
    if sys.platform != "linux" or os.geteuid() != 0:
        parser.error("Run as root on the test node")
    app = directory(Path(args.app))
    if args.action == 'adopt-workloads':
        import tools_adoption
        require_idle()
        with lock(app / '.mon-sensors-install.lock'):
            print(json.dumps(tools_adoption.adopt(app, args.task, args.check), ensure_ascii=False))
        return 0
    if args.action in ('preflight', 'run-queue'):
        import node
        values = ['--app', str(app)]
        for flag in ('rdb-server', 'rdb-port', 'log-server', 'log-dir', 'node', 'serial'):
            values += ['--' + flag, str(getattr(args, flag.replace('-', '_')))]
        if args.action == 'preflight':
            values += ['--check']
        return node.main(values)
    if args.action == 'start':
        import operator_cli
        print(json.dumps(operator_cli.start(app)))
        return 0
    if args.action == 'stop':
        from workload import identity
        state = load_case(app, args.case)
        expected = state.get('scheduler_process')
        if not expected or state.get('boot_id') != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
            raise ValueError('No matching scheduler in this boot; inspect status and recover')
        current = identity(expected['pid'])
        if not current or any(current[k] != expected[k] for k in ('pid', 'start_ticks', 'session')):
            raise ValueError('Recorded scheduler no longer exists; inspect status and recover')
        argv = Path('/proc/{}/cmdline'.format(expected['pid'])).read_bytes().split(b'\0')
        if os.fsencode(str(app / HELPER / 'finish.py')) not in argv or b'run-queue' not in argv:
            raise ValueError('Scheduler command identity differs')
        os.kill(expected['pid'], signal.SIGTERM)
        print(json.dumps({'case': args.case, 'status': 'stop_requested'}))
        return 0
    if args.action == "status":
        selected = [load_case(app, args.case)] if args.case else cases(app)
        result = {"version": VERSION, "cases": selected}
        if args.human:
            if not selected:
                print('没有收尾记录。使用 start 明确启动当前任务批次。')
            for state in selected:
                print('{}  {}  阶段={}  执行={}  数据={}  错误={}'.format(
                    state['case'], state['task_id'], state['stage'],
                    state.get('execution_result', state.get('outcome', 'unknown')),
                    state.get('data_quality', '旧记录未提供质量字段' if state.get('version') == '0.1.0'
                              else '尚未核验'), state.get('error') or '无'))
                if state['stage'] not in TERMINAL:
                    print('  恢复入口: {} {} --case {}{}'.format(app / ENTRY,
                        'retry' if state['sealed'] else 'recover', state['case'],
                        '' if state['sealed'] else ' --interrupted'))
                report = Path(state['mon']).with_suffix('.report.html')
                if report.name in state.get('artifacts', {}):
                    print('  详细报告: {}'.format(report))
            return 0
    elif args.action == "check":
        import install_guard
        install_guard.verify(app)
        prerequisites(app)
        pending = [s["case"] for s in cases(app) if s["stage"] not in TERMINAL]
        if args.scheduler and pending:
            raise ValueError("Unfinished batches prevent another scheduler run; inspect status and retry/recover: " + ",".join(pending))
        result = {"status": "ok", "version": VERSION, "pending": pending}
    else:
        root_state(app, create=True)
        with contextlib.ExitStack() as stack:
            stack.enter_context(lock(root_state(app) / 'scheduler.lock'))
            stack.enter_context(lock(root_state(app) / 'operation.lock'))
            if args.action == "begin":
                result = begin(app, args.mon, args.remote, args.task_id, args.task_time)
                if args.print_id:
                    print(result["case"])
                    return 0
            else:
                state = load_case(app, args.case)
                if args.action == "step":
                    step(app, state, args.name, args.event, args.runtime, args.launch_code)
                    result = state
                elif args.action == "interrupted":
                    persist(app, state, error="Scheduler interrupted; task completion is unconfirmed")
                    result = state
                elif args.action == "close-incomplete":
                    result = close_incomplete(app, state, args.reason)
                else:
                    if args.action == "retry" and not state["sealed"]:
                        raise ValueError("This batch was not sealed; inspect it and use recover --interrupted")
                    result = finish(app, state, args.action == "recover")
    if args.action in ("finish", "retry", "recover", "close-incomplete"):
        result = {key: result.get(key) for key in ("case", "stage", "outcome", "samples", "receipt", "error")}
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as error:
        print(ENTRY + ": " + str(error), file=sys.stderr)
        raise SystemExit(1)
