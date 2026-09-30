"""Named-field acquisition. Unavailable readings remain null, never zero."""
import glob
import math
import os
import re
import shutil
import threading
import time

from .common import capture, utc_timestamp


def number(value):
    try:
        result = float(str(value).strip())
        return result if math.isfinite(result) else None
    except (ValueError, TypeError):
        return None


def parse_ipmi(text):
    sensors = {}
    for line in text.splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 5:
            continue
        raw = fields[4]
        parts = raw.split(None, 1)
        value = number(parts[0]) if parts and fields[2].lower() in ("ok", "nc", "cr", "nr") else None
        sensors[fields[0]] = {"value": value, "unit": parts[1] if len(parts) > 1 else "",
                              "status": fields[2], "raw": raw}
    return sensors


def parse_turbostat(text):
    header, rows = None, []
    for line in text.splitlines():
        values = line.split()
        if "Avg_MHz" in values or "Bzy_MHz" in values:
            header = values
            rows = []
        elif header and len(values) == len(header):
            row = dict(zip(header, values))
            if number(row.get("Avg_MHz", row.get("Bzy_MHz"))) is not None:
                rows.append(row)
    summary = next((row for row in rows if row.get("CPU") == "-"), None)
    if summary is None and len(rows) == 1:
        summary = rows[0]
    if summary is None:
        return {}
    return {name: number(summary.get(name)) for name in
            ("Avg_MHz", "Bzy_MHz", "Busy%", "PkgWatt", "PkgTmp", "CoreTmp")}


def first_sensor(sensors, names):
    for name in names:
        reading = sensors.get(name)
        if reading and reading["value"] is not None:
            return reading["value"], "ipmi:" + name
    return None, None


def cpu_sensor_readings(sensors, mapping=None):
    """Preserve individual socket readings rather than selecting CPU 0 only."""
    mapping = mapping or {}
    names = mapping.get("cpu_temperature")
    if names is None:
        names = [name for name in sensors if re.match(
            r"^CPU(?:[ _-]*(?:\d+|Package))*[ _-]*(?:Temp|Temperature)$", name, re.I)]
    return {"ipmi:" + name: sensors[name]["value"] for name in names
            if name in sensors and sensors[name]["value"] is not None}


def metrics(ipmi, turbo, mapping=None):
    mapping = mapping or {}
    cpu_readings = cpu_sensor_readings(ipmi, mapping)
    cpu_source = max(cpu_readings, key=cpu_readings.get) if cpu_readings else None
    cpu_temp = cpu_readings.get(cpu_source)
    vrm_temp, vrm_source = first_sensor(ipmi, mapping.get("vrm_temperature", [
        "VRM Temperature", "VR_P0_TEMP", "VRM Temp"]))
    if cpu_temp is None:
        cpu_temp = turbo.get("PkgTmp")
        cpu_source = "turbostat:PkgTmp" if cpu_temp is not None else None
    power, power_source = first_sensor(ipmi, mapping.get("system_power", ["SYS_POWER", "System Power"]))
    psu_names = mapping.get("psu_power")
    if psu_names is None:
        psu_names = sorted(name for name in ipmi if re.match(r"^PSU\S* Power In$", name))
    if power is None and psu_names:
        readings = [ipmi.get(name, {}).get("value") for name in psu_names]
        # Never silently treat a failed/unconnected supply reading as zero.
        if all(value is not None for value in readings):
            power, power_source = sum(readings), "ipmi:" + "+".join(psu_names)
    fans = {name: reading["value"] for name, reading in ipmi.items()
            if "fan" in name.lower() or reading["unit"].lower() == "rpm"}
    values = {"cpu_avg_mhz": turbo.get("Avg_MHz"), "cpu_busy_mhz": turbo.get("Bzy_MHz"),
              "cpu_busy_percent": turbo.get("Busy%"), "cpu_temperature_c": cpu_temp,
              "vrm_temperature_c": vrm_temp, "cpu_package_watts": turbo.get("PkgWatt"),
              "system_watts": power, "fan_rpm": fans}
    values["sources"] = {"cpu_temperature_c": cpu_source, "vrm_temperature_c": vrm_source,
                         "system_watts": power_source,
                         "cpu_package_watts": "turbostat:PkgWatt" if turbo.get("PkgWatt") is not None else None}
    for name, field in (("cpu_avg_mhz", "Avg_MHz"), ("cpu_busy_mhz", "Bzy_MHz"),
                        ("cpu_busy_percent", "Busy%")):
        values["sources"][name] = "turbostat:" + field if values[name] is not None else None
    values["sources"]["fan_rpm"] = "ipmi" if fans else None
    values["missing"] = [key for key, value in values.items() if value is None]
    return values


def hwmon_cpu_temperatures(root="/sys/class/hwmon"):
    readings = {}
    for directory in glob.glob(os.path.join(root, "hwmon*")):
        try:
            with open(os.path.join(directory, "name")) as stream:
                driver = stream.read().strip()
            if driver not in ("coretemp", "k10temp", "zenpower"):
                continue
        except OSError:
            continue
        for path in glob.glob(os.path.join(directory, "temp*_input")):
            try:
                with open(path.replace("_input", "_label")) as stream:
                    label = stream.read().strip()
                if not (label.startswith("Package id") or label in ("Tdie", "Tctl")):
                    continue
                with open(path) as stream:
                    value = number(stream.read())
                if value is not None:
                    readings[os.path.basename(directory) + ":" + label] = value / 1000
            except OSError:
                # An absent optional label must not hide another package sensor.
                continue
    return readings


def _setting(settings, name, default, lower, upper):
    value = number(settings.get(name, default))
    if value is None or not lower <= value <= upper:
        raise ValueError("Invalid sensor setting: " + name)
    return value


class _SourceWorker:
    """One bounded command at a time; failed refreshes never reset reading age."""
    def __init__(self, name, read, retain_on_error=True, age_from_completion=False):
        self.name, self.read = name, read
        self.retain_on_error = retain_on_error
        self.age_from_completion = age_from_completion
        self.lock = threading.Lock()
        self.requested = threading.Event()
        self.finished = threading.Event()
        self.finished.set()
        self.stopped = threading.Event()
        self.data, self.observed_at, self.observed_mono = {}, None, None
        self.last_started, self.elapsed = None, None
        self.error, self.in_flight = None, False
        self.thread = threading.Thread(target=self._run, name="ocrun-" + name)
        self.thread.daemon = True
        self.thread.start()

    def request(self, interval, retry_interval):
        with self.lock:
            now = time.monotonic()
            delay = retry_interval if self.error else interval
            if self.stopped.is_set() or self.in_flight or (
                    self.last_started is not None and now - self.last_started < delay):
                return
            self.last_started = now
            self.in_flight = True
            self.finished.clear()
            self.requested.set()

    def _run(self):
        while not self.stopped.is_set():
            self.requested.wait()
            self.requested.clear()
            if self.stopped.is_set():
                break
            started, observed_at = time.monotonic(), utc_timestamp()
            try:
                data, error = self.read()
            except Exception as failure:
                # Keep the collector alive if a tool's output changes unexpectedly.
                data, error = {}, str(failure)
            elapsed = time.monotonic() - started
            with self.lock:
                if data and not error:
                    self.data = data
                    self.observed_at = utc_timestamp() if self.age_from_completion else observed_at
                    self.observed_mono = time.monotonic() if self.age_from_completion else started
                elif not self.retain_on_error:
                    self.data, self.observed_at, self.observed_mono = {}, None, None
                self.error = error
                self.elapsed = elapsed
                self.in_flight = False
                self.finished.set()

    def snapshot(self, max_age):
        with self.lock:
            age = None if self.observed_mono is None else max(0, time.monotonic() - self.observed_mono)
            stale = age is None or age > max_age
            status = ("stale" if self.data else "pending" if self.in_flight else "unavailable") if stale else (
                "cached" if self.in_flight or self.error or age > 1 else "fresh")
            meta = {"observed_at": self.observed_at,
                    "age_seconds": None if age is None else round(age, 3),
                    "stale": stale, "status": status,
                    "max_age_seconds": max_age, "in_flight": self.in_flight,
                    "collection_seconds": None if self.elapsed is None else round(self.elapsed, 3),
                    "error": self.error}
            return (dict(self.data) if not stale else {}), meta

    def stop(self):
        self.stopped.set()
        self.requested.set()


class Collector:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.timeout = _setting(self.settings, "command_timeout_seconds", 8, 0.1, 30)
        self.initial_wait = _setting(self.settings, "initial_wait_seconds", self.timeout + 0.5, 0, 91)
        self.sample_wait = _setting(self.settings, "sample_wait_seconds", 0.5, 0, 5)
        self.ipmi_interval = _setting(self.settings, "ipmi_interval_seconds", 10, 0, 300)
        self.retry_interval = _setting(self.settings, "retry_interval_seconds", 30, 0, 300)
        self.max_age = {
            "turbostat": _setting(self.settings, "turbostat_max_age_seconds", 10, 0.1, 600),
            "ipmi": _setting(self.settings, "ipmi_max_age_seconds", 20, 0.1, 600)}
        self.sckocp = self.settings.get("sckocp", {})
        if not isinstance(self.sckocp, dict) or not isinstance(self.sckocp.get("enabled", False), bool):
            raise ValueError("Invalid sensor setting: sckocp")
        self.sckocp_enabled = self.sckocp.get("enabled", False)
        if self.sckocp_enabled:
            self.sckocp_binary = self.sckocp.get("binary", "/usr/bin/sckocp")
            if not isinstance(self.sckocp_binary, str) or not os.path.isabs(self.sckocp_binary):
                raise ValueError("sckocp binary must be an absolute path")
            self.sckocp_timeout = _setting(self.sckocp, "timeout_seconds", 20, 0.1, 90)
            self.sckocp_window = _setting(self.sckocp, "interval_seconds", 1, 0.05, 60)
            if self.sckocp_timeout <= self.sckocp_window:
                raise ValueError("sckocp timeout must exceed its sampling interval")
            self.sckocp_interval = _setting(self.sckocp, "poll_interval_seconds", 5, 0.1, 300)
            self.max_age["sckocp"] = _setting(self.sckocp, "max_age_seconds", 30, 0.1, 600)
            if "initial_wait_seconds" not in self.settings:
                self.initial_wait = max(self.timeout, self.sckocp_timeout) + 0.5
        self.workers = {}
        self.first_sample, self.closed = True, False

    def _read_turbostat(self):
        if not shutil.which("turbostat"):
            return {}, "Tool unavailable"
        code, output, error = capture(
            ["turbostat", "--quiet", "--num_iterations", "1", "--interval", "0.2"],
            self.timeout, dict(os.environ, LC_ALL="C"))
        if code != 0:
            return {}, error or "Collection failed"
        data = parse_turbostat(output)
        return data, None if data else "No recognizable summary row"

    def _read_ipmi(self):
        if not shutil.which("ipmitool"):
            return {}, "Tool unavailable"
        argv, env = ["ipmitool"], dict(os.environ, LC_ALL="C")
        config = self.settings.get("ipmi", {})
        if config.get("host"):
            try:
                with open(config["password_file"]) as stream:
                    env["IPMI_PASSWORD"] = stream.read().strip()
                argv += ["-I", "lanplus", "-H", config["host"], "-U", config["username"], "-E"]
            except (KeyError, OSError):
                return {}, "Remote IPMI credentials unavailable"
        code, output, error = capture(argv + ["sdr", "elist"], self.timeout, env)
        if code != 0:
            return {}, error or "Collection failed"
        data = parse_ipmi(output)
        return data, None if data else "No recognizable sensors"

    def close(self, timeout=None):
        self.closed = True
        command_timeout = max(self.timeout, self.sckocp_timeout if self.sckocp_enabled else 0)
        deadline = time.monotonic() + (command_timeout + 0.5 if timeout is None else timeout)
        for worker in self.workers.values():
            worker.stop()
        for worker in self.workers.values():
            worker.thread.join(max(0, deadline - time.monotonic()))

    @staticmethod
    def _metric_status(result):
        unavailable = {"observed_at": None, "age_seconds": None, "stale": True, "status": "unavailable"}
        statuses = {}
        for metric, source in result["sources"].items():
            name = source.split(":", 1)[0] if source else None
            statuses[metric] = dict(result["source_status"].get(name, unavailable))
        for metric, readings in (("cpu_temperatures_c", result["cpu_temperatures_c"]),
                                 ("cpu_control_temperatures_c", result["cpu_control_temperatures_c"])):
            details = {}
            for name in readings:
                source = "hwmon" if name.startswith("hwmon") else name.split(":", 1)[0]
                details[name] = dict(result["source_status"].get(source, unavailable))
            oldest = max(details.values(), key=lambda item: item["age_seconds"] or 0) if details else unavailable
            statuses[metric] = dict(oldest, readings=details)
        return statuses

    def sample(self, task_id=None):
        if self.closed:
            raise RuntimeError("Collector is closed")
        started = time.monotonic()
        result = {"schema_version": 2, "timestamp": utc_timestamp(), "task_id": task_id,
                  "errors": {}, "load_average_1m": None, "system_uptime_seconds": None}
        try:
            result["load_average_1m"] = os.getloadavg()[0]
            with open("/proc/uptime") as stream:
                result["system_uptime_seconds"] = number(stream.read().split()[0])
        except (OSError, AttributeError):
            pass
        if not self.workers:
            self.workers = {"turbostat": _SourceWorker("turbostat", self._read_turbostat),
                            "ipmi": _SourceWorker("ipmi", self._read_ipmi)}
            if self.sckocp_enabled:
                self.workers["sckocp"] = _SourceWorker("sckocp", self._read_sckocp,
                                                      retain_on_error=False, age_from_completion=True)
        for name, worker in self.workers.items():
            interval = self.sckocp_interval if name == "sckocp" else self.ipmi_interval if name == "ipmi" else 0
            worker.request(interval, self.retry_interval)
        deadline = time.monotonic() + (self.initial_wait if self.first_sample else self.sample_wait)
        for worker in self.workers.values():
            worker.finished.wait(max(0, deadline - time.monotonic()))
        self.first_sample = False
        turbo, turbo_meta = self.workers["turbostat"].snapshot(self.max_age["turbostat"])
        ipmi, ipmi_meta = self.workers["ipmi"].snapshot(self.max_age["ipmi"])
        result["source_status"] = {"turbostat": turbo_meta, "ipmi": ipmi_meta}
        for name, meta in result["source_status"].items():
            if meta["error"]:
                result["errors"][name] = meta["error"]
            elif meta["stale"]:
                result["errors"][name] = "Reading " + meta["status"]
        result.update(metrics(ipmi, turbo, self.settings.get("mapping")))
        hwmon_started = time.monotonic()
        hwmon_timestamp = utc_timestamp()
        result["hwmon_cpu_temperatures_c"] = hwmon_cpu_temperatures()
        hwmon_elapsed = round(time.monotonic() - hwmon_started, 3)
        result["source_status"]["hwmon"] = {
            "observed_at": hwmon_timestamp, "age_seconds": hwmon_elapsed,
            "stale": not bool(result["hwmon_cpu_temperatures_c"]),
            "status": "fresh" if result["hwmon_cpu_temperatures_c"] else "unavailable",
            "collection_seconds": hwmon_elapsed}
        physical = {name: value for name, value in result["hwmon_cpu_temperatures_c"].items()
                    if not name.endswith(":Tctl")}
        controls = {name: value for name, value in result["hwmon_cpu_temperatures_c"].items()
                    if name.endswith(":Tctl")}
        result["cpu_temperatures_c"] = cpu_sensor_readings(ipmi, self.settings.get("mapping"))
        if turbo.get("PkgTmp") is not None:
            result["cpu_temperatures_c"]["turbostat:PkgTmp"] = turbo["PkgTmp"]
        result["cpu_temperatures_c"].update(physical)
        result["cpu_control_temperatures_c"] = controls
        result["cpu_control_temperature_c"] = max(controls.values()) if controls else None
        result["sources"]["cpu_control_temperature_c"] = "hwmon:max-control-temperature" if controls else None
        # Direct package/die readings avoid delayed BMC updates. An explicit mapping
        # keeps its precedence; Tctl is never substituted for physical temperature.
        if physical and not self.settings.get("mapping", {}).get("cpu_temperature"):
            result["cpu_temperature_c"] = max(physical.values())
            result["sources"]["cpu_temperature_c"] = "hwmon:max-package-or-die-temperature"
        elif physical and result["cpu_temperature_c"] is None:
            result["cpu_temperature_c"] = max(physical.values())
            result["sources"]["cpu_temperature_c"] = "hwmon:max-package-or-die-temperature"
        result["metric_status"] = self._metric_status(result)
        if self.sckocp_enabled:
            from .sckocp_metrics import merge_sample
            envelope, meta = self.workers["sckocp"].snapshot(self.max_age["sckocp"])
            merge_sample(result, envelope, meta, self.settings.get("mapping", {}))
        result["missing"] = [name for name in result["sources"] if result.get(name) is None]
        result["ipmi_sensors"] = ipmi
        result["sensor_alerts"] = [name for name, reading in ipmi.items() if reading["status"].lower() in ("nc", "cr", "nr")]
        result["collection_seconds"] = round(time.monotonic() - started, 3)
        return result

    def _read_sckocp(self):
        from .sckocp import collect
        envelope = collect(self.sckocp_binary, self.sckocp_window, self.sckocp_timeout)
        return (envelope, None) if envelope.get("data") is not None else ({}, envelope["status"])
