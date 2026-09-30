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
import re
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
        commands = {"v1": ["mon", "--json"], "v2": ["mon", "--json=v2"],
                    "overview": ["mon", "--cols=1"], "info": ["info"]}
        if native_format not in commands:
            raise ValueError("Unsupported native operation")
        # Pin the checked inode until exec; never re-resolve a replaceable path
        # after checking it. Do not use preexec_fn: SDK users may have threads.
        with trusted_executable(binary) as (descriptor, executable):
            process = subprocess.Popen([binary] + commands[native_format], executable=executable,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=environment, cwd="/",
                                       pass_fds=(descriptor,), start_new_session=True,
                                       close_fds=True)
    except BaseException:
        selector.close()
        raise
    output = bytearray()
    info_filter = _PrimaryInfoFilter() if native_format == "info" else None
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
                    if key.data == "stdout" and info_filter is not None:
                        output.extend(info_filter.feed(b"", final=True))
                    selector.unregister(key.fileobj)
                    continue
                stream = key.data
                counts[stream] += len(chunk)
                if counts[stream] > (STDOUT_LIMIT if stream == "stdout" else STDERR_LIMIT):
                    raise _OutputLimit()
                if stream == "stdout":
                    output.extend(info_filter.feed(chunk) if info_filter is not None else chunk)
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


# Console supplements are an additive contract. They contain only documented
# monitoring/configuration sections, never activation files or diagnostic output.
DETAILS_SCHEMA = "sckocp-details-v1"
DETAILS_LIMIT = 256 * 1024
INFO_SECTIONS = ("Platform", "CPU", "Turbo Ratio Limits", "Thermal", "Power Limits",
                 "Power Supplies", "Memory", "Memory Timings", "Cache",
                 "Per-CCD Temperature", "SVI Rails")
SOCKET_FIELDS = {"temp_max_c": r"Temp Max", "tjmax_c": r"TjMax",
                 "vccin_v": r"VCCIN", "vid_v": r"VID", "core_mhz": r"Core",
                 "mesh_mhz": r"Mesh", "memory_temp_max_c": r"Mem Max",
                 "pkg_w": r"Pkg", "dram_w": r"DRAM"}
SOCKET_UNITS = {"temp_max_c": "°C", "tjmax_c": "°C", "vccin_v": "V", "vid_v": "V",
                "core_mhz": "MHz", "mesh_mhz": "MHz", "memory_temp_max_c": "°C",
                "pkg_w": "W", "dram_w": "W"}
PRIMARY_LINE = re.compile(r"\s*S\d+\s+Primary\s+[0-9?]+-[0-9?]+-[0-9?]+-[0-9?]+(?:\s+(?:tCWL|tRC)\s+[0-9?]+)*\s*\Z")


class _PrimaryInfoFilter:
    """Discard restricted timing lines before retaining native stdout.

    This is export minimization, not a security boundary against host root.
    Original info remains responsible for its native authorization checks.
    """
    def __init__(self):
        self.pending = b""
        self.timings = False

    def feed(self, chunk, final=False):
        data = self.pending + chunk
        rows = data.split(b"\n")
        self.pending = b"" if final else rows.pop()
        if len(self.pending) > 8192:
            raise _OutputLimit()
        output = []
        for raw in rows:
            if len(raw) > 8192:
                raise _OutputLimit()
            line = raw.decode("utf-8").rstrip("\r")
            header = re.fullmatch(r"== (.+) ==", line.strip())
            if header:
                self.timings = header.group(1).split(":", 1)[0] == "Memory Timings"
                if self.timings:
                    line = "== Memory Timings: Primary only =="
            if header or not self.timings or PRIMARY_LINE.fullmatch(line):
                output.append(line.encode("utf-8") + b"\n")
        return b"".join(output)


def _console_sections(output, operation):
    if len(output) > DETAILS_LIMIT:
        raise _OutputLimit()
    text = output.decode("utf-8")
    if any(unicodedata.category(c) in ("Cc", "Cf", "Cs") and c not in "\n\r\t" for c in text):
        raise ValueError("Console output contains control characters")
    sections, current = [], None
    for line in text.splitlines():
        if len(line) > 2048:
            raise ValueError("Console line exceeds limit")
        header = re.fullmatch(r"== (.+) ==", line.strip())
        if header:
            title = header.group(1)
            name = title.split(":", 1)[0]
            if operation == "overview" and re.fullmatch(r"(?:GenuineIntel|AuthenticAMD) fam\d+\s+Per-socket Overview", title):
                name = "Per-socket Overview"
            allowed = INFO_SECTIONS if operation == "info" else ("Per-socket Overview", "CPU")
            current = None
            if name in allowed:
                if any(s["name"] == name for s in sections):
                    raise ValueError("Duplicate console section")
                current = {"name": name, "title": "Memory Timings: Primary only" if name == "Memory Timings" else title, "lines": []}
                sections.append(current)
        elif current is not None and line.strip():
            if current["name"] == "Memory Timings" and not PRIMARY_LINE.fullmatch(line):
                continue
            if len(current["lines"]) >= 512:
                raise ValueError("Console section exceeds limit")
            current["lines"].append(line.expandtabs(8).rstrip())
    required = {"Platform", "CPU"} if operation == "info" else {"Per-socket Overview"}
    if not required.issubset({s["name"] for s in sections}):
        raise ValueError("Missing console sections")
    return sections


def _reading(text, label, unit):
    match = re.search(r"(?<!\w)" + label + r"\s+(-?\d+(?:\.\d+)?)\s*" + re.escape(unit) + r"(?!\w)", text)
    if not match:
        return None
    return _number(float(match.group(1)), -273.15 if unit == "°C" else 0, 1e9)


def _identity_add(rows, item, key, limit=128):
    if len(rows) >= limit or any(r[key] == item[key] for r in rows):
        raise ValueError("Duplicate or excessive console identities")
    rows.append(item)


def parse_console(output, operation):
    """Parse bounded native text; export only the Primary timing group.

    Native absence is null, not zero. Source text is retained to preserve timing
    groups and platform-dependent fields without guessing undocumented registers.
    """
    if operation not in ("overview", "info"):
        raise ValueError("Unsupported console operation")
    sections = _console_sections(output, operation)
    by_name = {s["name"]: s for s in sections}
    lines = lambda name: by_name.get(name, {}).get("lines", [])
    if operation == "overview":
        sockets, current = [], None
        for line in lines("Per-socket Overview"):
            # Multi-socket power summaries may place S0 and S1 on one line.
            boundaries = [m.start() for m in re.finditer(r"\bS\d+\s", line)]
            boundaries = sorted(set([0] + boundaries + [len(line)]))
            for segment in (line[a:b] for a, b in zip(boundaries, boundaries[1:])):
                identity = re.match(r"\s*S(\d+)\s", segment)
                if identity:
                    sid = _integer(int(identity.group(1)), maximum=4095)
                    current = next((r for r in sockets if r["id"] == sid), None)
                    if current is None:
                        current = dict((f, None) for f in SOCKET_FIELDS)
                        current["id"] = sid
                        _identity_add(sockets, current, "id")
                if current is not None:
                    for field, label in SOCKET_FIELDS.items():
                        value = _reading(segment, label, SOCKET_UNITS[field])
                        if value is not None:
                            if current[field] is not None and current[field] != value:
                                raise ValueError("Conflicting socket readings")
                            current[field] = value
        if not sockets:
            raise ValueError("No socket overview")
        whole = "\n".join(lines("Per-socket Overview"))
        power = re.findall(r"PSU In\s+(\d+(?:\.\d+)?)\s+W", whole)
        if len(set(power)) > 1:
            raise ValueError("Conflicting system power totals")
        age = re.search(r"\b(\d+)s old\b", whole)
        system = {"psu_input_w_reported": _number(float(power[0]), 0, 1e9) if power else None,
                  "age_s_reported": int(age.group(1)) if age else None,
                  "coverage": "not_reported"}
        return {"sections": sections, "sockets": sorted(sockets, key=lambda r: r["id"]), "system": system}
    cpus = []
    for line in lines("CPU"):
        match = re.match(r"\s*S(\d+)\s+(.+?)\s+(\d+)C/(\d+)T\s+fam(\d+) model (\d+) stepping (\d+)\s+ucode (\S+)", line)
        if match:
            sid, model, cores, threads, family, model_id, stepping, ucode = match.groups()
            _identity_add(cpus, {"id": int(sid), "model": model, "cores": int(cores),
                                "threads": int(threads), "family": int(family), "model_id": int(model_id),
                                "stepping": int(stepping), "microcode": ucode}, "id")
    memory, dimms = lines("Memory"), []
    if memory and memory[0].lstrip().startswith("DIMM"):
        columns = [(m.group(), m.start()) for m in re.finditer(r"DIMM|Part Number|Speed|JEDEC|VDDQ|Size|Temp", memory[0])]
        for line in memory[1:]:
            if not line.strip():
                continue
            values = {name: line[start:columns[i + 1][1] if i + 1 < len(columns) else len(line)].strip()
                      for i, (name, start) in enumerate(columns)}
            slot = values.get("DIMM")
            if not slot:
                raise ValueError("Missing DIMM identity")
            # Fixed-width columns come from the same native header; keep their
            # text too (including dual VDDQ rails), rather than invent a scalar.
            temp = _reading("Temp " + values.get("Temp", ""), "Temp", "°C")
            _identity_add(dimms, {"slot": slot, "fields": values, "temp_c": temp}, "slot", 512)
    supplies = []
    psu = "\n".join(lines("Power Supplies"))
    for line in lines("Power Supplies"):
        match = re.fullmatch(r"\s*(\S+)\s+(\d+(?:\.\d+)?)\s+W", line)
        if match and match.group(1) != "Wall":
            _identity_add(supplies, {"name": match.group(1), "watts": _number(float(match.group(2)), 0, 1e9)}, "name", 32)
    coverage = re.search(r"(\d+) of (\d+) supplies reporting", psu)
    age = re.search(r"Readings (\d+) s old", psu)
    arrangement = re.search(r"Arrangement (.+)", psu)
    system = {"psu_input_w_reported": _reading(psu, "Wall", "W"),
              "supplies_reporting": int(coverage.group(1)) if coverage else None,
              "supplies_present": int(coverage.group(2)) if coverage else None,
              "coverage": "partial" if coverage and int(coverage.group(1)) < int(coverage.group(2)) else "not_reported",
              "age_s_reported": int(age.group(1)) if age else None,
              "arrangement": arrangement.group(1) if arrangement else None}
    return {"sections": sections, "cpus": cpus, "dimms": dimms, "power_supplies": supplies, "system": system}


def collect_details(binary="/usr/bin/sckocp", interval=1.0, timeout=20.0):
    """Two fixed licensed read-only commands, sharing one bounded deadline.

    No previous sample is cached. One failed source does not make the other
    source current or valid. An authorization failure prevents further commands.
    """
    started = time.monotonic()
    result = {"schema": DETAILS_SCHEMA, "parts": {}, "quality": "reported_validity_unknown"}
    for operation in ("overview", "info"):
        part = _envelope("invalid_configuration")
        part["started_at"] = part["observed_at"]
        try:
            if (not isinstance(binary, str) or not os.path.isabs(binary) or len(binary) > 4096 or
                    any(unicodedata.category(c) in ("Cc", "Cf", "Cs") for c in binary)):
                raise ValueError("Invalid binary")
            _number(interval, .05, 60)
            _number(timeout, .1, 120)
        except ValueError:
            result["parts"][operation] = part
            continue
        status, data = "ok", None
        if not sys.platform.startswith("linux"):
            status = "unsupported_platform"
        elif any(p["status"] in GATE_STATUS.values() for p in result["parts"].values()):
            status = next(p["status"] for p in result["parts"].values() if p["status"] in GATE_STATUS.values())
        else:
            try:
                remaining = timeout - (time.monotonic() - started)
                if remaining <= .05:
                    raise subprocess.TimeoutExpired(binary, timeout)
                code, output = _capture(binary, interval, remaining, operation)
                status = GATE_STATUS.get(code, "collection_failed") if code else "ok"
                if not code:
                    data = parse_console(output, operation)
            except subprocess.TimeoutExpired:
                status = "timeout"
            except _OutputLimit:
                status = "output_limit"
            except UnsafeExecutable:
                status = "unsafe_executable"
            except PermissionError:
                status = "permission_denied"
            except OSError:
                status = "unavailable"
            except (ValueError, TypeError, OverflowError, RecursionError):
                status = "invalid_data"
        finished = _envelope(status, data if status == "ok" else None)
        finished["started_at"] = part["started_at"]
        result["parts"][operation] = finished
    return result


def validate_details(value):
    """Re-parse allowlisted source sections; derived values cannot drift silently."""
    _object(value, ("schema", "parts", "quality"))
    if value["schema"] != DETAILS_SCHEMA or value["quality"] != "reported_validity_unknown":
        raise ValueError("Invalid supplemental schema")
    _object(value["parts"], ("overview", "info"))
    for operation, part in value["parts"].items():
        _object(part, ("schema", "status", "started_at", "observed_at", "data", "error"))
        if part["schema"] != SCHEMA or part["status"] not in set(ERRORS) | {"ok"}:
            raise ValueError("Invalid supplemental status")
        for key in ("started_at", "observed_at"):
            _string(part[key], 32)
            datetime.datetime.strptime(part[key], "%Y-%m-%dT%H:%M:%S.%fZ")
        if part["observed_at"] < part["started_at"]:
            raise ValueError("Supplemental clock reversed")
        if part["status"] != "ok":
            if part["data"] is not None or part["error"] != ERRORS[part["status"]]:
                raise ValueError("Failed supplemental reading contains data")
            continue
        if part["error"] is not None or not isinstance(part["data"], dict):
            raise ValueError("Invalid supplemental payload")
        sections = part["data"].get("sections")
        _array(sections, len(INFO_SECTIONS), 1)
        text = []
        for section in sections:
            _object(section, ("name", "title", "lines"))
            _string(section["title"], 256)
            _array(section["lines"], 512)
            text.append("== " + section["title"] + " ==")
            for line in section["lines"]:
                _string(line, 2048)
                text.append(line)
        if parse_console("\n".join(text).encode("utf-8"), operation) != part["data"]:
            raise ValueError("Supplemental source and derived fields differ")


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
