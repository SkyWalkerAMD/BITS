"""Bounded, read-only local adapter for licensed sckocp monitoring output.

Shared by the public sckocp API and the compatibility adapter for OCRUN.
Individual unavailable readings remain null; this module does not infer watts,
temperature or operating-system utilization from unrelated counters.
"""
import argparse
import datetime
import json
import math
import os
import selectors
import signal
import subprocess
import sys
import time
import unicodedata

from .security import UnsafeExecutable, trusted_executable


SCHEMA = "ocrun-sckocp-v1"
PROVIDER_SCHEMA = "sckocp-mon-v2"
STDOUT_LIMIT = 4 * 1024 * 1024
STDERR_LIMIT = 64 * 1024
JSON_DEPTH_LIMIT = 32
JSON_NUMBER_LIMIT = 128
SOCKET_METRICS = {"temperature_c": "C", "control_temperature_c": "C",
                  "package_watts": "W", "dram_watts": "W", "tjmax_c": "C",
                  "vid_volts": "V", "base_mhz": "MHz"}
CORE_METRICS = {"active_mhz": "MHz", "c0_percent": "%", "c6_percent": "%",
                "temperature_c": "C", "vid_volts": "V"}
ERRORS = {
    "license_denied": "sckocp did not authorize monitoring with the current licence.",
    "license_unavailable": "sckocp could not obtain a valid licence lease.",
    "unkeyed_build": "sckocp has no configured activation verification key.",
    "platform_required": "sckocp platform registration is required.",
    "collection_failed": "sckocp did not complete the collection successfully.",
    "timeout": "sckocp exceeded the collection deadline.",
    "unavailable": "The configured sckocp executable is unavailable.",
    "invalid_data": "sckocp returned invalid monitoring data.",
    "unsupported_schema": "sckocp mon v2 is required; install the API extension.",
    "output_limit": "sckocp exceeded the collection output limit.",
    "invalid_configuration": "The sckocp collection configuration is invalid.",
    "unsupported_platform": "The sckocp collection adapter requires Linux.",
    "unsafe_executable": "The sckocp executable or its path does not meet the ownership and permission requirements.",
    "permission_denied": "The current user does not have permission to execute the configured sckocp program.",
}
GATE_STATUS = {10: "license_denied", 12: "license_unavailable",
               13: "unkeyed_build", 14: "platform_required"}


class UnsupportedSchema(ValueError):
    """The provider does not implement the required monitoring schema."""


class _OutputLimit(Exception):
    pass


def _number(value, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("Expected a number")
    try:
        finite = math.isfinite(value)
    except OverflowError:
        finite = False
    if not finite or (minimum is not None and value < minimum) or (
            maximum is not None and value > maximum):
        raise ValueError("Invalid numeric range")
    return value


def _integer(value, minimum=0, maximum=1048576):
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("Expected an integer")
    return _number(value, minimum, maximum)


def _string(value, maximum=256):
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ValueError("Invalid string")
    if any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in value):
        raise ValueError("Control, format and surrogate characters are not allowed")
    return value


def _object(value, required, optional=()):
    if not isinstance(value, dict) or not set(required).issubset(value) or (
            set(value) - set(required) - set(optional)):
        raise ValueError("Invalid object fields")


def _array(value, maximum, minimum=0):
    if not isinstance(value, list) or not minimum <= len(value) <= maximum:
        raise ValueError("Invalid array")


def _metrics(value, units):
    _object(value, units)
    for name, unit in units.items():
        metric = value[name]
        _object(metric, ("value", "unit", "source", "status", "age_s"))
        if metric["unit"] != unit:
            raise ValueError("Incorrect metric unit")
        if metric["status"] == "unavailable":
            if any(metric[key] is not None for key in ("value", "source", "age_s")):
                raise ValueError("An unavailable reading must remain null")
        elif metric["status"] == "ok":
            minimum = -273.15 if unit == "C" else 0
            maximum = 100 if unit == "%" else None
            _number(metric["value"], minimum, maximum)
            _string(metric["source"], 256)
            _number(metric["age_s"], 0)
        else:
            raise ValueError("Unknown reading status")


def validate_payload(payload):
    """Validate the complete provider contract; return it without changing data.

    Unknown fields are rejected so unexpected diagnostics or licence material
    cannot silently become part of an uploaded sample.  Freshness decisions are
    made by the monitoring client using each descriptor's ``age_s``.
    """
    if not isinstance(payload, dict):
        raise ValueError("Expected a monitoring object")
    if payload.get("schema") != PROVIDER_SCHEMA:
        raise UnsupportedSchema("Unsupported provider schema")
    _object(payload, ("schema", "version", "vendor", "family", "interval_s",
                      "sampled_at_unix_ms", "duration_s", "read_only", "sockets",
                      "cores", "system", "notes"), ("extension",))
    _string(payload["version"], 64)
    _string(payload["vendor"], 64)
    if payload["vendor"] not in ("GenuineIntel", "AuthenticAMD"):
        raise ValueError("Unsupported CPU vendor")
    if "extension" in payload:
        _string(payload["extension"], 64)
    _integer(payload["family"], 0, 65535)
    _number(payload["interval_s"], 0.05, 3600)
    _integer(payload["sampled_at_unix_ms"], 1, 9007199254740991)
    _number(payload["duration_s"], 0.000000001, 3600)
    if payload["read_only"] is not True:
        raise ValueError("Read-only collection is required")

    _array(payload["sockets"], 256, 1)
    sockets = set()
    for socket in payload["sockets"]:
        _object(socket, ("id", "metrics", "flags"))
        socket_id = _integer(socket["id"])
        if socket_id in sockets:
            raise ValueError("Duplicate socket")
        sockets.add(socket_id)
        _metrics(socket["metrics"], SOCKET_METRICS)
        _object(socket["flags"], ("thermal_throttling",))
        flag = socket["flags"]["thermal_throttling"]
        if flag is not None and not isinstance(flag, bool):
            raise ValueError("Invalid thermal flag")

    _array(payload["cores"], 4096)
    cpus = set()
    for core in payload["cores"]:
        _object(core, ("cpu", "socket", "metrics"))
        cpu = _integer(core["cpu"])
        socket_id = _integer(core["socket"])
        if cpu in cpus or socket_id not in sockets:
            raise ValueError("Invalid core topology")
        cpus.add(cpu)
        _metrics(core["metrics"], CORE_METRICS)

    system = payload["system"]
    _object(system, ("metrics", "psus", "psu_present_count", "psu_reporting_count",
                     "redundancy", "redundancy_age_s"))
    _metrics(system["metrics"], {"psu_input_watts": "W"})
    _array(system["psus"], 64)
    present = _integer(system["psu_present_count"], 0, 64)
    reporting = _integer(system["psu_reporting_count"], 0, 64)
    if reporting > present or reporting != len(system["psus"]):
        raise ValueError("Inconsistent PSU counts")
    names = set()
    for psu in system["psus"]:
        _object(psu, ("name", "metrics"))
        name = _string(psu["name"], 128)
        if name in names:
            raise ValueError("Duplicate PSU name")
        names.add(name)
        _metrics(psu["metrics"], {"input_watts": "W"})
        if psu["metrics"]["input_watts"]["status"] != "ok":
            raise ValueError("A reporting PSU needs a valid reading")
    if system["metrics"]["psu_input_watts"]["status"] == "ok" and (
            not present or reporting != present):
        raise ValueError("A partial PSU total is not system input power")
    if system["redundancy"] is not None:
        _string(system["redundancy"], 128)
    if system["redundancy_age_s"] is not None:
        _number(system["redundancy_age_s"], 0)
        if system["redundancy"] is None:
            raise ValueError("Redundancy age needs a reading")
    _array(payload["notes"], 64)
    for note in payload["notes"]:
        _string(note, 512)
    return payload


def validate_original_payload(payload):
    """Validate original 1.1.0/1.2.0 JSON without claiming sensor validity/coverage.

    Keep the original schema and values: v1 has no individual validity, age,
    hardware-read status, PSU inventory or AMD temperature in its JSON output.
    In particular, its zero readings must not be promoted to verified metrics.
    """
    if not isinstance(payload, dict):
        raise ValueError("Expected a monitoring object")
    if payload.get("schema") != "sckocp-mon-v1":
        raise UnsupportedSchema("Original sckocp mon JSON required")
    _object(payload, ("schema", "version", "vendor", "family", "interval_s", "sockets", "cores"))
    _string(payload["version"], 64)
    _string(payload["vendor"], 64)
    if payload["vendor"] not in ("GenuineIntel", "AuthenticAMD"):
        raise ValueError("Unsupported CPU vendor")
    _integer(payload["family"], 0, 65535)
    _number(payload["interval_s"], .05, 60)
    _array(payload["sockets"], 256, 1)
    _array(payload["cores"], 8192)
    intel = payload["vendor"] == "GenuineIntel"
    socket_fields = ("id", "tjmax_c", "temp_max_c", "vid_v", "core_mhz", "base_mhz", "pkg_w") if intel else ("id", "pkg_w")
    core_fields = ("cpu", "socket", "mhz", "temp_c", "vid_v", "c0_pct", "c6_pct") if intel else ("cpu", "socket", "mhz", "c0_pct")
    sockets, cpus = set(), set()
    for socket in payload["sockets"]:
        _object(socket, socket_fields)
        socket_id = _integer(socket["id"])
        if socket_id in sockets:
            raise ValueError("Duplicate socket")
        sockets.add(socket_id)
        for name in socket_fields:
            if name == "id" or (name == "pkg_w" and socket[name] is None and not intel):
                continue
            _number(socket[name], -273.15 if name.endswith("_c") else 0)
    for core in payload["cores"]:
        _object(core, core_fields)
        cpu = _integer(core["cpu"])
        socket_id = _integer(core["socket"])
        if cpu in cpus or socket_id not in sockets:
            raise ValueError("Invalid core topology")
        cpus.add(cpu)
        for name in core_fields:
            if name not in ("cpu", "socket"):
                _number(core[name], -273.15 if name == "temp_c" else 0,
                        100 if name.endswith("_pct") else None)
    return payload


def _kill_session(process):
    # Native run_capture gives helpers their own process groups. All remain in
    # the isolated session created here, so cleaning up only the leader's group
    # would leave ipmitool alive after an adapter timeout.
    try:
        os.killpg(process.pid, signal.SIGSTOP)
    except ProcessLookupError:
        pass
    groups = {process.pid}
    try:
        with os.scandir("/proc") as entries:
            for entry in entries:
                if entry.name.isdigit():
                    try:
                        pid = int(entry.name)
                        if os.getsid(pid) == process.pid:
                            groups.add(os.getpgid(pid))
                    except (ProcessLookupError, PermissionError):
                        pass
    except OSError:
        pass
    for group in groups:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        # Even SIGKILL cannot immediately reap a process stuck in kernel I/O.
        # Keep the adapter deadline bounded instead of waiting indefinitely.
        pass


def _capture(binary, interval, timeout, native_format="v2"):
    # Give native curl/wget attempts a bounded setting inside the outer deadline.
    # wget may retry, so this does not replace the hard process deadline below.
    transport_seconds = max(1, min(10, int(max(0, timeout - interval - 1) / 2)))
    environment = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C",
                   "LC_ALL": "C", "SCKOCP_MODE": "ro", "SCKOCP_MODPROBE": "0",
                   "SCKOCP_TIMEOUT": str(transport_seconds),
                   "INT": str(interval), "BMCPROBET": "2", "BMCREADT": "1",
                   "BMCTTL": "0", "BMCSDRTTL": "0"}
    deadline = time.monotonic() + timeout
    selector = selectors.DefaultSelector()
    try:
        option = "--json" if native_format == "v1" else "--json=v2"
        # Pin the checked inode until exec; never re-resolve a replaceable path
        # after checking it. Do not use preexec_fn: SDK users may have threads.
        with trusted_executable(binary) as (descriptor, executable):
            process = subprocess.Popen([binary, "mon", option], executable=executable,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=environment, cwd="/",
                                       pass_fds=(descriptor,), start_new_session=True,
                                       close_fds=True)
    except BaseException:
        selector.close()
        raise
    output = bytearray()
    counts = {"stdout": 0, "stderr": 0}
    completed = False
    try:
        selector.register(process.stdout, selectors.EVENT_READ, "stdout")
        selector.register(process.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(binary, timeout)
            for key, _ in selector.select(remaining):
                chunk = os.read(key.fileobj.fileno(), 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                stream = key.data
                counts[stream] += len(chunk)
                if counts[stream] > (STDOUT_LIMIT if stream == "stdout" else STDERR_LIMIT):
                    raise _OutputLimit()
                if stream == "stdout":
                    output.extend(chunk)
        # Observe exit without reaping. Keeping this child (even as a zombie)
        # pins its PID/session ID until all native helper groups are cleaned;
        # an unrelated process cannot acquire that ID before cleanup signals.
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(binary, timeout)
            ended = os.waitid(os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if ended is not None:
                break
            time.sleep(min(.02, remaining))
        code = ended.si_status if ended.si_code == os.CLD_EXITED else -ended.si_status
        # A helper can close stdout/stderr and outlive a successful leader.
        # Cleanup is required for every outcome, not just adapter timeouts.
        _kill_session(process)
        completed = True
        return code, bytes(output)
    finally:
        selector.close()
        if not completed:
            _kill_session(process)
        process.stdout.close()
        process.stderr.close()


def _no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON field")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError("Non-finite JSON constant")


def _json_number(value, integer=False):
    if len(value) > JSON_NUMBER_LIMIT:
        raise ValueError("Oversized JSON number")
    result = int(value) if integer else float(value)
    if not integer and not math.isfinite(result):
        raise ValueError("Non-finite JSON number")
    return result


def _decode_json(output):
    """Bound parser recursion and numeric work before contract validation."""
    if len(output) > STDOUT_LIMIT:
        raise _OutputLimit()
    depth, quoted, escaped = 0, False, False
    for char in output:
        if quoted:
            if escaped:
                escaped = False
            elif char == 92:
                escaped = True
            elif char == 34:
                quoted = False
        elif char == 34:
            quoted = True
        elif char in (91, 123):
            depth += 1
            if depth > JSON_DEPTH_LIMIT:
                raise ValueError("JSON nesting exceeds the supported contract")
        elif char in (93, 125):
            depth -= 1
            if depth < 0:
                raise ValueError("Unbalanced JSON")
    return json.loads(output.decode("utf-8"), object_pairs_hook=_no_duplicates,
                      parse_constant=_reject_constant,
                      parse_int=lambda value: _json_number(value, integer=True),
                      parse_float=_json_number)


def _envelope(status, data=None):
    return {"schema": SCHEMA, "status": status,
            "observed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(
                timespec="milliseconds").replace("+00:00", "Z"),
            "data": data, "error": ERRORS.get(status)}


def collect(binary="/usr/bin/sckocp", interval=1.0, timeout=20.0, native_format="v2"):
    """Execute exactly one licensed collection and return a safe JSON envelope."""
    try:
        if native_format not in ("v1", "v2"):
            raise ValueError("Unknown native format")
        if (not isinstance(binary, str) or not os.path.isabs(binary) or len(binary) > 4096 or
                any(unicodedata.category(char) in ("Cc", "Cf", "Cs") for char in binary)):
            raise ValueError("An absolute executable path is required")
        interval = _number(interval, 0.05, 60)
        timeout = _number(timeout, 0.1, 120)
        if timeout <= interval:
            raise ValueError("Timeout must exceed the sampling interval")
    except ValueError:
        return _envelope("invalid_configuration")
    if not sys.platform.startswith("linux"):
        return _envelope("unsupported_platform")
    try:
        code, output = (_capture(binary, interval, timeout) if native_format == "v2" else
                        _capture(binary, interval, timeout, native_format))
    except subprocess.TimeoutExpired:
        return _envelope("timeout")
    except _OutputLimit:
        return _envelope("output_limit")
    except UnsafeExecutable:
        return _envelope("unsafe_executable")
    except PermissionError:
        return _envelope("permission_denied")
    except OSError:
        return _envelope("unavailable")
    if code != 0:
        return _envelope(GATE_STATUS.get(code, "collection_failed"))
    try:
        payload = _decode_json(output)
        (validate_payload if native_format == "v2" else validate_original_payload)(payload)
    except UnsupportedSchema:
        result = _envelope("unsupported_schema")
        if native_format == "v1":
            result["error"] = "The installed sckocp did not return its original mon JSON format."
        return result
    except _OutputLimit:
        return _envelope("output_limit")
    except (ValueError, TypeError, RecursionError, OverflowError):
        return _envelope("invalid_data")
    return _envelope("ok", payload)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", default="/usr/bin/sckocp")
    parser.add_argument("--interval", type=float, default=1.0,
                        help="Native sampling interval in seconds (0.05-60)")
    parser.add_argument("--timeout", type=float, default=20.0,
                        help="Deadline including activation and BMC collection (0.1-120)")
    args = parser.parse_args(argv)
    result = collect(args.binary, args.interval, args.timeout)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return 0 if result["data"] is not None else 1


if __name__ == "__main__":
    sys.exit(main())
