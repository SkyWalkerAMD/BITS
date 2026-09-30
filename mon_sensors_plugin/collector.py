"""Independent mon-sensors-plugin command with the 0730 eleven-column log."""
import argparse
import contextlib
import csv
import datetime
import fcntl
import json
import math
import os
import re
import shutil
import signal
import socket
import stat
import sys
import time

import sckocp_api
from sckocp_api.provider import ERRORS, STDOUT_LIMIT


HEADERS = ["#=类型", "时间", "当前系统任务", "运行时长", "负载", "主频",
           "CPU温度(C)", "VRM温度(C)", "CPU功耗(W)", "整机功耗(W)", "风扇转速"]
FORMATS = {
    "v1": ("sckocp-v1", "reported-core-mean-MHz", "reported-validity-and-age-unknown"),
    "v2": ("sckocp-v2", "physical-core-active-mean-MHz", "per-metric-status-and-age"),
}
RETRYABLE_STATUSES = frozenset(("timeout", "collection_failed"))
WORKLOADS = {"stress": "Stress", "stress-ng": "Stress-NG", "mlc": "MLC", "mlc-3.13": "MLC",
             "mbw": "MBW", "bcr": "BC-Result", "bcfi": "BC-ACC", "bcfd": "BC-Pi",
             "cpu2017": "SPEC2017", "cpu2017-1.0.5": "SPEC2017", "ptu": "PTU",
             "cyclictest": "Cyclictest", "unixbench": "UnixBench"}
for _instruction, _label in (("no", "P95"), ("avx", "P95-AVX"),
                             ("fma3", "P95-FMA3"), ("avx512", "P95-AVX512")):
    for _mode in range(1, 5):
        WORKLOADS["p95-{}_m{}".format(_instruction, _mode)] = "{}-M{}".format(_label, _mode)


def _text(value, maximum=128):
    # The original analyzer uses split(','); quotes cannot escape a comma there.
    return "".join(char if char.isprintable() and char not in ',"|'
                   else " " for char in str(value))[:maximum].strip()


def _read(path, limit=65536):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as stream:
            return stream.read(limit)
    except OSError:
        return ""


def detect_task(proc_root="/proc"):
    found = set()
    try:
        with os.scandir(proc_root) as entries:
            for entry in entries:
                if not entry.name.isdigit() or int(entry.name) == os.getpid():
                    continue
                arguments = _read(os.path.join(entry.path, "cmdline")).lower()
                for name, label in WORKLOADS.items():
                    if name + "/" in arguments:
                        found.add(label)
    except OSError:
        return "Unknown"
    return "Multi-Load" if len(found) > 1 else next(iter(found), "IDIE")


def os_context(task=None):
    try:
        uptime = float(_read("/proc/uptime").split()[0])
        if not math.isfinite(uptime) or uptime < 0:
            uptime = None
    except (ValueError, IndexError):
        uptime = None
    try:
        load = os.getloadavg()[0]
        if not math.isfinite(load) or load < 0:
            load = None
    except OSError:
        load = None
    return {"time": datetime.datetime.now().strftime("%Y%m%d_%H%M%S"),
            "task": task if task is not None else detect_task(),
            "uptime_seconds": uptime, "load1": load}


def make_row(envelope, context, max_age=30, elapsed=0, native_format="v2"):
    data = envelope.get("data") if envelope.get("status") == "ok" else None
    if data:
        native_format = {"sckocp-mon-v1": "v1", "sckocp-mon-v2": "v2"}[data["schema"]]

    def aggregate(items, field, operation):
        if not items:
            return None
        values = []
        for item in items:
            metric = item["metrics"][field]
            if metric["status"] != "ok" or metric["age_s"] + elapsed > max_age:
                return None
            values.append(metric["value"])
        result = operation(values)
        return result if math.isfinite(result) else None

    frequency = temperature = package = power = None
    if data and native_format == "v1" and elapsed <= max_age:
        # Original v1 has no sensor validity, read age or expected core count.
        # Display only the values it reports; never manufacture v2 descriptors.
        def reported(items, field, operation):
            values = [item.get(field) for item in items]
            if not values or any(value is None for value in values):
                return None
            result = operation(values)
            return result if math.isfinite(result) else None

        frequency = reported(data["cores"], "mhz", lambda values: sum(values) / len(values))
        temperature = reported(data["sockets"], "temp_max_c", max)
        package = reported(data["sockets"], "pkg_w", sum)
    elif data and native_format == "v2":
        frequency = aggregate(data["cores"], "active_mhz", lambda values: sum(values) / len(values))
        temperature = aggregate(data["sockets"], "temperature_c", max)
        package = aggregate(data["sockets"], "package_watts", sum)
        power = aggregate([data["system"]], "psu_input_watts", sum)

    def number(value):
        return "" if value is None else "{:.6f}".format(value).rstrip("0").rstrip(".")

    return [FORMATS[native_format][0], context["time"], _text(context["task"]),
            number(context["uptime_seconds"]), number(context["load1"]),
            number(frequency), number(temperature), "", number(package), number(power), ""]


@contextlib.contextmanager
def _locked_file(path):
    # Resolve directory aliases for locking, but never follow the final symlink.
    path = os.path.join(os.path.realpath(os.path.dirname(os.path.abspath(path))),
                        os.path.basename(path))
    descriptor = os.open(path, os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    stream = None
    try:
        details = _log_details(descriptor)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Another mon-sensors process is writing this log")
        if details.st_size and os.pread(descriptor, 1, details.st_size - 1) != b"\n":
            raise ValueError("Log ends with an incomplete line")
        stream = os.fdopen(descriptor, "a+", encoding="utf-8", newline="")
        yield stream
    finally:
        if stream is not None:
            stream.close()
        else:
            os.close(descriptor)


def _log_details(descriptor):
    details = os.fstat(descriptor)
    if not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
        raise ValueError("Log must be a regular file with a single link")
    if details.st_uid != os.geteuid() or details.st_mode & 0o7022:
        raise ValueError("Log must belong to the current user and forbid writes by other users")
    return details


def _private_log(stream):
    # Recheck the opened inode; never chmod a pathname that could have changed.
    # Call only after format validation so unrelated existing files stay intact.
    _log_details(stream.fileno())
    os.fchmod(stream.fileno(), 0o600)


def _check_sidecar(stream):
    stream.seek(0)
    # A stored frame contains one bounded provider response plus its envelope.
    first = stream.readline(STDOUT_LIMIT * 2 + 8192)
    if first:
        if not first.endswith("\n"):
            raise ValueError("Existing sidecar record exceeds the size limit")
        try:
            record = json.loads(first)
        except (ValueError, RecursionError):
            raise ValueError("Existing sidecar is not a monitoring log")
        if (not isinstance(record, dict) or record.get("schema") != "mon-sensors-sckocp-v1"
                or not isinstance(record.get("provider"), dict)):
            raise ValueError("Existing sidecar is not a monitoring log")
    stream.seek(0, os.SEEK_END)


def _prepare_log(stream, native_format="v2"):
    provider, frequency, quality = FORMATS[native_format]
    stream.seek(0)
    first = stream.readline(4096)
    if first:
        if (not first.startswith("#-主机名:") or "provider=" + provider not in first.split()
                or stream.readline(4096).rstrip("\r\n").split(",") != HEADERS):
            raise ValueError("Existing log is not a compatible sckocp mon-sensors log")
    else:
        stream.write("#-主机名: {} provider={} frequency={} quality={}\n".format(
            _text(socket.gethostname()), provider, frequency, quality))
        csv.writer(stream, lineterminator="\n").writerow(HEADERS)
        stream.flush()
    stream.seek(0, os.SEEK_END)


def _interrupt(signum, frame):
    raise KeyboardInterrupt()


def _script_path(value):
    if not os.path.isabs(value) or "\0" in value:
        return None
    # Resolve directory aliases, not the final file: two application launchers
    # may point to the same installed code but belong to different applications.
    return os.path.join(os.path.realpath(os.path.dirname(value)), os.path.basename(value))


def _script_argument(argv):
    """Extract a script operand without mistaking -c/-m arguments for a file."""
    if not argv:
        return None
    executable = os.path.basename(argv[0])
    python = re.fullmatch(r"(?:python3(?:\.[0-9]+)?|platform-python(?:3(?:\.[0-9]+)?)?)",
                          executable)
    shell = executable in ("bash", "sh")
    if not python and not shell:
        return argv[0]
    arguments = argv[1:]
    allowed = ("-I", "-B", "-E", "-s", "-S", "-u") if python else ()
    while arguments and arguments[0] in allowed:
        arguments = arguments[1:]
    if arguments and arguments[0] == "--":
        arguments = arguments[1:]
    if not arguments or arguments[0].startswith("-"):
        return None
    return arguments[0]


def _process_snapshot(path):
    try:
        with open(os.path.join(path, "cmdline"), "rb") as stream:
            command = stream.read(65537)
        if not command or len(command) > 65536 or not command.endswith(b"\0"):
            return None
        with open(os.path.join(path, "stat"), "r") as stream:
            fields = stream.read(8192).rsplit(")", 1)[1].split()
        if len(fields) < 20 or not fields[19].isdigit():
            return None
        return command, fields[19]  # /proc stat field 22: process start time.
    except (OSError, IndexError):
        return None


def stop_monitors(app, proc_root="/proc"):
    """Stop exact application entrypoints, never commands mentioning a log name."""
    if not isinstance(app, str) or not os.path.isabs(app) or "\0" in app:
        raise ValueError("Application path must be absolute")
    app = os.path.realpath(app)
    if app == os.path.dirname(app) or not os.path.isdir(app):
        raise ValueError("Application path must be an existing non-root directory")
    # The guardian owns the backend's process group. Let it clean up completely
    # before applying the existing exact-entrypoint fallback to older monitors.
    from .runtime import _runtime_argv, stop_runtime
    stop_runtime(app)
    targets = {_script_path(os.path.join(app, relative)) for relative in
               ("mon-sensors", "mon_sensors", "mon-sensors-plugin",
                "mon-sensors-plugin.d/mon-sensors-plugin", "sckocp-collector/mon-sensors")}
    denied = False
    with os.scandir(proc_root) as entries:
        for entry in entries:
            if not entry.name.isdigit() or int(entry.name) <= 1 or int(entry.name) == os.getpid():
                continue
            snapshot = _process_snapshot(entry.path)
            if snapshot is None:
                continue
            argv = [os.fsdecode(part) for part in snapshot[0].split(b"\0")[:-1]]
            if "--stop-app" in argv:
                continue
            script = _script_argument(argv)
            if script is None or _script_path(script) not in targets:
                continue
            # stop_runtime already stopped the verified supervisor and waited
            # for its lock. It may still be returning with default handlers
            # restored: a second TERM here would replace its cleanup exit code
            # with -SIGTERM. Supervision is never handled by this legacy scan.
            if _runtime_argv(snapshot[0], app):
                continue
            # Check both start time and argv again immediately before signalling,
            # reducing PID-reuse risk on Python 3.6 (which has no pidfd API).
            if _process_snapshot(entry.path) != snapshot:
                continue
            try:
                os.kill(int(entry.name), signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError:
                denied = True
    if denied:
        raise PermissionError("Cannot stop an application monitoring process")
    return 0


def _arguments(argv):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("interval", nargs="?", type=float, default=5)
    parser.add_argument("logfile", nargs="?", default="view")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--binary", default=os.environ.get("SCKOCP_BINARY") or shutil.which("sckocp") or "/usr/bin/sckocp")
    parser.add_argument("--format", choices=sorted(FORMATS),
                        default=os.environ.get("SCKOCP_FORMAT") or "v1",
                        help="v1 reads original sckocp; v2 requires an existing native extension")
    parser.add_argument("--timeout", type=float, default=20)
    parser.add_argument("--sample-window", type=float, default=1)
    parser.add_argument("--max-age", type=float, default=30)
    parser.add_argument("--details-interval", type=float, default=10,
                        help="Seconds between bounded mon/info supplements (10-3600; 0 disables)")
    parser.add_argument("--retries", type=int, default=2,
                        help="Retry at most this many consecutive transient collection failures (0-5)")
    parser.add_argument("--retry-delay", type=float, default=1,
                        help="Seconds before a transient retry (0.1-30); --once never retries")
    parser.add_argument("--task")
    parser.add_argument("--stop-app", help="Stop only monitoring entrypoints under this absolute application directory")
    args = parser.parse_args(argv)
    if args.stop_app is not None:
        return args
    if args.format not in FORMATS:
        parser.error("SCKOCP_FORMAT must be v1 or v2")
    for name, low, high in (("interval", .05, 3600), ("timeout", .1, 120),
                            ("sample_window", .05, 60), ("max_age", .05, 600),
                            ("retry_delay", .1, 30)):
        value = getattr(args, name)
        if not math.isfinite(value) or not low <= value <= high:
            parser.error("{} must be finite and between {} and {}".format(name, low, high))
    if not 0 <= args.retries <= 5:
        parser.error("retries must be between 0 and 5")
    if not math.isfinite(args.details_interval) or (args.details_interval != 0 and not 10 <= args.details_interval <= 3600):
        parser.error("details-interval must be 0 or between 10 and 3600")
    if args.timeout <= args.sample_window:
        parser.error("timeout must exceed sample-window")
    if not os.path.isabs(args.binary) or "\0" in args.binary:
        parser.error("binary must be an absolute path")
    if args.task is not None and (not args.task or args.task != _text(args.task)):
        parser.error("task must be a plain label without commas or control characters")
    return args


def main(argv=None):
    args = _arguments(argv)
    if args.stop_app is not None:
        try:
            return stop_monitors(args.stop_app)
        except (OSError, ValueError):
            print("mon-sensors-plugin: Cannot safely stop monitoring for this application.", file=sys.stderr)
            return 1
    previous = {sig: signal.signal(sig, _interrupt) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        with contextlib.ExitStack() as stack:
            writer = sidecar = None
            if args.logfile != "view":
                stream = stack.enter_context(_locked_file(args.logfile))
                sidecar = stack.enter_context(_locked_file(args.logfile + ".sckocp.jsonl"))
                _check_sidecar(sidecar)
                _prepare_log(stream, args.format)
                _private_log(stream)
                _private_log(sidecar)
                writer = csv.writer(stream, lineterminator="\n")
            else:
                if args.format == "v1":
                    print("## sckocp mon v1 | 主频=原版报告的核心频率均值(MHz) | "
                          "有效性和读数年龄未知; 零值不保证测量成功 | 空白=不可用")
                else:
                    print("## sckocp mon v2 | 主频=物理核心活动频率均值(MHz) | 空白=不可用")
                print(" | ".join(HEADERS))
            last_status = None
            consecutive_failures = 0
            next_details = 0
            last_detail_status = None
            while True:
                started = time.monotonic()
                envelope = sckocp_api.collect(args.binary, args.sample_window, args.timeout,
                                            native_format=args.format)
                status = envelope["status"]
                if status != "ok":
                    # A failed attempt must never expose an old sample or raw
                    # provider diagnostics through the persistent status log.
                    envelope = dict(envelope, data=None,
                                    error=ERRORS.get(status, "Monitoring unavailable."))
                received = time.monotonic()
                context = os_context(args.task)
                row = make_row(envelope, context, args.max_age, time.monotonic() - received,
                               native_format=args.format)
                supplemental = None
                if status == "ok" and args.details_interval and received >= next_details:
                    # Each source carries its own time. Never copy a previous
                    # supplement into a later row or imply synchronized sampling.
                    supplemental = sckocp_api.provider.collect_details(
                        args.binary, args.sample_window, min(args.timeout, 10))
                    next_details = time.monotonic() + args.details_interval
                    detail_status = tuple(supplemental["parts"][k]["status"] for k in ("overview", "info"))
                    if detail_status != last_detail_status:
                        print("mon-sensors-plugin: supplementary mon/info status=" + "/".join(detail_status), file=sys.stderr, flush=True)
                        last_detail_status = detail_status
                if sidecar is not None:
                    record = {"schema": "mon-sensors-sckocp-v1", "os": context,
                              "reading_quality": FORMATS[args.format][2], "provider": envelope}
                    if args.details_interval:
                        record["details_interval_s"] = args.details_interval
                    if supplemental is not None:
                        record["details"] = supplemental
                    sidecar.write(json.dumps(record, ensure_ascii=False,
                                             allow_nan=False, separators=(",", ":")) + "\n")
                    sidecar.flush()
                    writer.writerow(row)
                    stream.flush()
                else:
                    print(" | ".join(row), flush=True)
                if status != last_status:
                    message = ("Monitoring available; v1 sensor validity and age are unknown."
                               if status == "ok" and args.format == "v1" else
                               "Monitoring available." if status == "ok" else
                               ERRORS.get(status, "Monitoring unavailable."))
                    print("mon-sensors-plugin: " + message, file=sys.stderr, flush=True)
                    last_status = status
                if status != "ok":
                    consecutive_failures += 1
                    if (args.once or status not in RETRYABLE_STATUSES
                            or consecutive_failures > args.retries):
                        return 1
                    print("mon-sensors-plugin: Retrying collection in {} seconds ({}/{}).".format(
                        args.retry_delay, consecutive_failures, args.retries),
                        file=sys.stderr, flush=True)
                    time.sleep(args.retry_delay)
                    continue
                consecutive_failures = 0
                if args.once:
                    return 0
                # Skip missed deadlines; collection never overlaps or catches up.
                remaining = args.interval - (time.monotonic() - started)
                if remaining > 0:
                    time.sleep(remaining)
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError):
        print("mon-sensors-plugin: Cannot safely open or write monitoring logs.", file=sys.stderr)
        return 1
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)


if __name__ == "__main__":
    sys.exit(main())
