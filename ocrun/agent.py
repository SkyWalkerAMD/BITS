import argparse
import json
import logging
import os
import signal
import shutil
import subprocess
import threading
import time
import uuid

from . import __version__
from .common import capture, exclusive_lock, identifier, inventory, read_json, utc_timestamp, write_json
from .evaluation import ErrorScanner, evaluate, validate_acceptance
from .logs import LogStore, seal
from .queue import Queue
from .rediswire import Redis
from .sensors import Collector
from .safety import SafetyMonitor, protection_policy
from .workloads import command, validate_task

LOG = logging.getLogger("ocrun")


class SessionLost(RuntimeError):
    pass


def stop_process(process, grace=10):
    if process is None:
        return
    # A workload can leave child processes after its launcher exits.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        process.poll()
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


class Agent:
    def __init__(self, config, queue=None):
        self.config = config
        self.host = identifier(config["host_id"])
        self.queue = queue or Queue(Redis(config["redis"]), self.host)
        self.state_dir = config.get("state_dir", "/var/lib/ocrun-agent")
        self.log_dir = config.get("log_dir", "/var/log/ocrun")
        self.tool_root = config.get("tool_root", "/opt/ocrun/tools")
        self.journal = os.path.join(self.state_dir, "active.json")
        self.stop = threading.Event()
        self.info = None
        self.active = None
        self.sync_error = None
        self.last_upload_at = None
        self.sync_stop = threading.Event()
        self.sync_worker = None
        self.last_heartbeat = 0
        self.last_contact = time.monotonic()
        self.session = None
        self.session_ttl = max(180, int(config.get("offline_grace_seconds", 120)) + 60)
        self.collect_failed = threading.Event()
        self.safety_failure = None
        self.latest_metrics = None
        self.last_sample = 0
        self.collector = None
        self.protection = None
        self.process = None
        self.process_stop_lock = threading.Lock()
        self.stopped_process = None
        self.log_store = LogStore(config, self.queue)
        self.log_status = {}
        self.upload_ready = threading.Event()

    def terminate_workload(self, process):
        with self.process_stop_lock:
            if process is not None and process is self.stopped_process:
                return
            stop_process(process)
            self.stopped_process = process

    def heartbeat(self, force=False):
        if force or time.monotonic() - self.last_heartbeat >= 15:
            if self.session and not self.queue.refresh_session(self.session, self.session_ttl):
                raise SessionLost("Another client owns this device identity")
            self.queue.heartbeat({"version": __version__, "inventory": self.info,
                                  "task_id": self.active["id"] if self.active else None,
                                  "sync_error": self.sync_error, "last_upload_at": self.last_upload_at,
                                  "metrics_summary": self.latest_metrics, "protection": self.safety_failure,
                                  "logs": self.log_status})
            self.last_heartbeat = time.monotonic()
            self.last_contact = time.monotonic()

    def flush_result(self):
        if not os.path.exists(self.journal):
            return
        record = read_json(self.journal)
        if "result" not in record:
            record.update(status="interrupted", result={"reason": "Agent restarted before completion",
                                                         "verdict": "insufficient_data"})
            write_json(self.journal, record)
        directory = os.path.join(self.log_dir, identifier(record["task"]["id"]))
        if not os.path.isfile(os.path.join(directory, "manifest.json")):
            os.makedirs(directory, exist_ok=True)
            write_json(os.path.join(directory, "task.json"), record["task"])
            write_json(os.path.join(directory, "result.json"), dict(record["result"], status=record["status"], finished_at=utc_timestamp()))
            seal(directory)
        try:
            self.queue.finish(record["task"], record["status"], record["result"])
        except RuntimeError:
            remote = self.queue.get(record["task"]["id"])
            if not remote or remote["status"] in ("pending", "running"):
                raise
            write_json(os.path.join(self.state_dir, "unacknowledged-" + record["task"]["id"] + ".json"), record)
            LOG.warning("Result retained locally; controller already recorded a terminal state")
        os.unlink(self.journal)
        self.upload_ready.set()

    def recover(self):
        self.flush_result()
        # Covers a lost response/crash between atomic claim and local journal write.
        task = self.queue.running()
        if task:
            record = {"task": task, "status": "interrupted",
                      "result": {"reason": "Recovered unacknowledged previous execution", "verdict": "insufficient_data"}}
            write_json(self.journal, record)
            self.flush_result()

    def upload(self):
        self.log_status = self.log_store.upload(self.active["id"] if self.active else None)
        self.sync_error = self.log_status["error"]
        if not self.sync_error:
            self.last_upload_at = utc_timestamp()

    def upload_loop(self):
        while not self.sync_stop.is_set():
            try:
                self.upload()
            except Exception:
                self.sync_error = "Log upload failed; local files retained"
            self.upload_ready.wait(max(10, int(self.config.get("upload_interval_seconds", 60))))
            self.upload_ready.clear()

    def collect_loop(self, path, task_id, stop_event):
        interval = max(1.0, float(self.config.get("sample_interval_seconds", 5)))
        collector = self.collector
        deadline = time.monotonic()
        try:
            with open(path, "a", encoding="utf-8") as stream:
                while not stop_event.is_set():
                    sample = collector.sample(task_id)
                    if stop_event.is_set():
                        break
                    sample["elapsed_seconds"] = round(time.monotonic() - self.task_started, 3)
                    self.last_sample = time.monotonic()
                    stream.write(json.dumps(sample, ensure_ascii=False, allow_nan=False) + "\n")
                    stream.flush()
                    self.latest_metrics = {name: sample.get(name) for name in
                                           ("timestamp", "cpu_temperature_c", "cpu_control_temperature_c",
                                            "cpu_busy_percent", "cpu_package_watts", "cpu_dram_watts",
                                            "cpu_core_active_mean_mhz", "cpu_core_active_max_mhz",
                                            "cpu_core_c0_mean_percent", "cpu_core_c0_max_percent",
                                            "system_watts", "fan_rpm", "metric_status", "source_status",
                                            "sckocp_available", "sckocp")}
                    failure = self.protection.check(sample)
                    if failure:
                        self.safety_failure = failure
                        self.collect_failed.set()
                        # Local protection remains responsive while controller I/O is blocked.
                        if self.process is not None:
                            self.terminate_workload(self.process)
                        break
                    deadline += interval
                    if deadline < time.monotonic():
                        deadline = time.monotonic()
                    stop_event.wait(max(0, deadline - time.monotonic()))
        except Exception as error:
            self.safety_failure = {"code": "collector_error", "reason": "Metric collection stopped: " + type(error).__name__}
            self.collect_failed.set()
            LOG.error("Metric collection stopped: %s", type(error).__name__)
            if self.process is not None:
                try:
                    self.terminate_workload(self.process)
                except Exception:
                    LOG.exception("Workload termination will be retried by main loop")

    def execute_task(self, task):
        self.active = task
        write_json(self.journal, {"task": task})
        directory = os.path.join(self.log_dir, identifier(task["id"]))
        os.makedirs(directory, exist_ok=True)
        write_json(os.path.join(directory, "task.json"), task)
        process, worker = None, None
        sample_stop = threading.Event()
        started = time.monotonic()
        self.task_started = started
        status = "failed"
        result = {"verdict": "insufficient_data"}
        scanner = ErrorScanner(directory, task["tool"])
        acceptance = task.get("acceptance", self.config.get("acceptance", {}))
        try:
            self.collect_failed.clear()
            self.safety_failure = None
            self.latest_metrics = None
            self.process = None
            self.stopped_process = None
            validate_acceptance(acceptance)
            policy = protection_policy(self.config.get("protection"), task.get("protection"))
            self.protection = SafetyMonitor(policy, self.config.get("required_metrics", ["cpu_temperature_c"]))
            if shutil.disk_usage(directory).free < int(self.config.get("minimum_free_mb", 256)) * 1024 * 1024:
                raise ValueError("Insufficient free space for workload logs")
            argv, cwd, one_shot = command(task, self.tool_root)
            self.collector = Collector(self.config.get("sensors"))
            initial = self.collector.sample(task["id"])
            write_json(os.path.join(directory, "preflight.json"), initial)
            failure = self.protection.check(initial, preflight=True)
            if failure:
                self.safety_failure = failure
                raise ValueError(failure["reason"])
            if self.stop.is_set():
                raise InterruptedError("Agent stopping before workload launch")
            if task["tool"].startswith("p95-"):
                # Each run gets its own mutable configuration and result files.
                run_dir = os.path.join(directory, "work")
                os.makedirs(run_dir, exist_ok=True)
                for name in ("local.txt", "prime.txt"):
                    source = os.path.join(cwd, name)
                    if os.path.isfile(source):
                        shutil.copyfile(source, os.path.join(run_dir, name))
                library_dir, cwd = cwd, run_dir
            else:
                library_dir = cwd
            if task["tool"] == "mbw":
                with open("/proc/meminfo") as stream:
                    available = next(int(line.split()[1]) for line in stream if line.startswith("MemAvailable:"))
                if task.get("memory_mb", 1024) * 1024 * 3 > available:
                    raise ValueError("MBW's three buffers would exceed available memory")
            self.heartbeat(force=True)
            self.task_started = time.monotonic()
            self.last_sample = self.task_started
            write_json(os.path.join(directory, "execution.json"), {
                "version": __version__, "argv": argv, "working_directory": cwd,
                "protection": policy, "acceptance": acceptance, "inventory": self.info})
            worker = threading.Thread(target=self.collect_loop,
                                      args=(os.path.join(directory, "metrics.jsonl"), task["id"], sample_stop), daemon=True)
            worker.start()
            with open(os.path.join(directory, "workload.log"), "ab", buffering=0) as output:
                child_env = dict(os.environ, LC_ALL="C", LD_LIBRARY_PATH=library_dir)
                process = subprocess.Popen(argv, cwd=cwd, stdout=output, stderr=subprocess.STDOUT,
                                           start_new_session=True, env=child_env)
                self.process = process
                deadline = time.monotonic() + task["duration_seconds"]
                next_cancel, next_error_scan = 0, 0
                while True:
                    code = process.poll()
                    if self.stop.is_set():
                        status, result["reason"] = "interrupted", "Agent stopping"
                        break
                    if self.collect_failed.is_set():
                        status, result["reason"] = "failed", (self.safety_failure or {}).get("reason", "Metric collector failed")
                        break
                    if time.monotonic() - self.last_sample > policy["max_sample_gap_seconds"]:
                        status, result["reason"] = "failed", "Metric collection stalled beyond protection limit"
                        self.safety_failure = {"code": "collector_stalled", "reason": result["reason"]}
                        break
                    if time.monotonic() >= next_error_scan:
                        next_error_scan = time.monotonic() + 5
                        if scanner.scan():
                            status, result["reason"] = "failed", "Workload reported verification errors"
                            break
                    if shutil.disk_usage(directory).free < int(self.config.get("minimum_free_mb", 256)) * 1024 * 1024:
                        status, result["reason"] = "failed", "Insufficient free space for workload logs"
                        break
                    code = process.poll()
                    if code is not None:
                        status = "completed" if code == 0 and one_shot else "failed"
                        result.update(reason="Workload exited", return_code=code)
                        break
                    if time.monotonic() >= deadline:
                        code = process.poll()
                        if code is not None:
                            status = "completed" if code == 0 and one_shot else "failed"
                            result.update(reason="Workload exited", return_code=code)
                        else:
                            status = "timed_out" if one_shot else "completed"
                            result["reason"] = "Configured duration reached"
                        break
                    try:
                        self.heartbeat()
                        if time.monotonic() >= next_cancel:
                            next_cancel = time.monotonic() + max(1, float(self.config.get("cancel_poll_seconds", 2)))
                            cancelled = self.queue.cancelled(task["id"])
                            self.last_contact = time.monotonic()
                            if cancelled:
                                status, result["reason"] = "cancelled", "Cancellation requested"
                                break
                    except SessionLost:
                        status, result["reason"] = "interrupted", "Device identity session lost"
                        break
                    except Exception as error:
                        LOG.warning("Controller unavailable (%s)", type(error).__name__)
                    if time.monotonic() - self.last_contact > int(self.config.get("offline_grace_seconds", 120)):
                        status, result["reason"] = "interrupted", "Controller unreachable beyond offline grace period"
                        break
                    self.stop.wait(1)
        except InterruptedError as error:
            status, result["reason"] = "interrupted", str(error)
        except Exception as error:
            result["reason"] = str(error)
            LOG.error("Task failed: %s", error)
        finally:
            execution_ended = time.monotonic()
            sample_stop.set()
            termination_failed = False
            if process is not None and status == "completed" and not one_shot:
                code = process.poll()
                if code is not None:
                    status = "failed"
                    result.update(reason="Workload exited before controlled termination", return_code=code)
            try:
                self.terminate_workload(process)
            except Exception as error:
                termination_failed = True
                status, result["reason"] = "failed", "Workload termination failed: " + type(error).__name__
                self.stop.set()
            finally:
                if worker:
                    worker.join(timeout=20)
                worker_stuck = worker is not None and worker.is_alive() is True
                unsealed = worker_stuck or termination_failed
                if worker_stuck:
                    self.stop.set()
                    status, result["reason"] = "failed", "Collector did not stop; logs retained for recovery"
                self.process = None
                if self.collector is not None:
                    self.collector.close()
                    self.collector = None
                result["elapsed_seconds"] = round(execution_ended - self.task_started, 3)
                if self.safety_failure:
                    result["protection"] = self.safety_failure
                    if status == "completed":
                        status, result["reason"] = "failed", self.safety_failure["reason"]
                try:
                    result.update(evaluate(directory, task, status, result, acceptance, scanner))
                except Exception as error:
                    result.update(verdict="failed" if status == "failed" else "insufficient_data",
                                  evaluation_error=type(error).__name__)
                # Execution status and acceptance verdict are deliberately separate.
                write_json(os.path.join(directory, "result.json"), dict(result, status=status, finished_at=utc_timestamp()))
                write_json(self.journal, {"task": task, "status": status, "result": result})
                if not unsealed:
                    try:
                        seal(directory)
                    except Exception as error:
                        LOG.error("Logs retained unsealed: %s", type(error).__name__)
                self.active = None
                self.upload_ready.set()
        if not unsealed:
            self.flush_result()

    def run(self):
        os.makedirs(self.state_dir, exist_ok=True)
        os.makedirs(self.log_dir, exist_ok=True)
        with exclusive_lock(os.path.join(self.state_dir, "agent.lock")):
            self.info = inventory()
            if not self.info["os"]["supported_family"]:
                raise RuntimeError("Unsupported operating system: " + str(self.info["os"]))
            if self.info["architecture"] != "x86_64":
                raise RuntimeError("The supplied OCRUN tool package targets x86_64")
            recovered = False
            self.session = uuid.uuid4().hex
            owned = False
            try:
                while not self.stop.is_set():
                    try:
                        if not owned:
                            if not self.queue.acquire_session(self.session, self.session_ttl):
                                LOG.warning("Device identity already active; waiting without starting workloads")
                                self.stop.wait(10)
                                continue
                            owned = True
                            recovered = False
                            if self.sync_worker is None:
                                self.sync_worker = threading.Thread(target=self.upload_loop, daemon=True)
                                self.sync_worker.start()
                        if not recovered:
                            self.recover()
                            recovered = True
                        self.flush_result()
                        self.heartbeat()
                        task = self.queue.claim(uuid.uuid4().hex, self.session)
                        if task:
                            self.execute_task(task)
                        else:
                            # Also recover claims whose replies were lost within this process.
                            if self.queue.running():
                                self.recover()
                            self.stop.wait(5)
                    except SessionLost:
                        owned = False
                    except Exception as error:
                        LOG.warning("Retrying after %s: %s", type(error).__name__, error)
                        self.stop.wait(5)
            finally:
                self.sync_stop.set()
                self.upload_ready.set()
                if self.sync_worker:
                    self.sync_worker.join(timeout=125)
                try:
                    self.queue.release_session(self.session)
                except Exception:
                    pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="/etc/ocrun/agent.json")
    parser.add_argument("--check", action="store_true", help="Inventory and sample only; no workload")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    config = read_json(args.config)
    if args.check:
        collector = Collector(config.get("sensors"))
        try:
            print(json.dumps({"inventory": inventory(), "sample": collector.sample()}, ensure_ascii=False, indent=2))
        finally:
            collector.close()
        return
    agent = Agent(config)
    for signum in (signal.SIGTERM, signal.SIGINT):
        signal.signal(signum, lambda *_: agent.stop.set())
    agent.run()


if __name__ == "__main__":
    main()
