import contextlib
import json
import os
import re
import shlex
import socket
import subprocess
import tempfile
import time


def identifier(value):
    if not isinstance(value, str) or not re.match(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z", value):
        raise ValueError("Identifier must contain 1-64 letters, digits, underscores or hyphens")
    return value


def read_json(path):
    with open(path, encoding="utf-8") as stream:
        return json.load(stream)


def write_json(path, value, mode=0o600):
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".ocrun-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def os_release(path="/etc/os-release"):
    values = {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.rstrip().split("=", 1)
            parts = shlex.split(value)
            values[key] = parts[0] if parts else ""
    distro, version = values.get("ID", ""), values.get("VERSION_ID", "")
    supported = ((distro == "centos" and version in ("7", "7.9", "7.9.2009")) or
                 (distro == "rocky" and version.split(".")[0] in ("8", "9", "10")) or
                 (distro == "ubuntu" and version in ("20.04", "22.04", "24.04", "26.04")))
    return {"id": distro, "version": version, "supported_family": supported}


def capture(argv, timeout=10, env=None):
    try:
        process = subprocess.run(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                 universal_newlines=True, timeout=timeout, env=env)
        return process.returncode, process.stdout, process.stderr[-1000:]
    except (OSError, subprocess.TimeoutExpired) as error:
        return -1, "", str(error)


@contextlib.contextmanager
def exclusive_lock(path):
    # Linux advisory lock survives process crashes without a stale lock-file problem.
    import fcntl
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "a+") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Another OCRUN process already holds " + path)
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def utc_timestamp():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def inventory():
    info = {"hostname": socket.gethostname(), "cpu_count": os.cpu_count(),
            "os": os_release(), "kernel": os.uname().release, "architecture": os.uname().machine}
    for name, path in (("board", "/sys/class/dmi/id/board_name"),
                       ("serial", "/sys/class/dmi/id/board_serial")):
        try:
            with open(path) as stream:
                info[name] = stream.read().strip()
        except OSError:
            info[name] = None
    try:
        with open("/proc/cpuinfo") as stream:
            cpus = stream.read().split("\n\n")
        records = [dict(line.split(":", 1) for line in record.splitlines() if ":" in line) for record in cpus if record.strip()]
        records = [{key.strip(): value.strip() for key, value in record.items()} for record in records]
        info["cpu_model"] = records[0].get("model name") if records else None
        info["cpu_flags"] = sorted(set.intersection(*(set(record.get("flags", "").split()) for record in records))) if records else []
        info["sockets"] = len({record["physical id"] for record in records if "physical id" in record}) or None
        with open("/proc/meminfo") as stream:
            info["memory_total_kb"] = next(int(line.split()[1]) for line in stream if line.startswith("MemTotal:"))
    except (OSError, StopIteration, ValueError):
        pass
    return info
