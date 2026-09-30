import os
import re
import shutil
import sys

from .evaluation import validate_acceptance
from .safety import validate_protection

P95 = re.compile(r"^p95-(no|avx|fma3|avx512)_m(1|2|4)$")
ONE_SHOT = {"mlc", "mbw", "bcr", "sysjitter", "unixbench", "cpu2017"}
TOOLS = {"cpu-burn", "stress", "stress-ng", "mlc", "mbw", "bcr", "bcfi", "bcfd", "ptu", "sysjitter", "unixbench", "cpu2017"}


def validate_task(task):
    if not isinstance(task, dict):
        raise ValueError("Task must be an object")
    tool = task.get("tool", "")
    if not isinstance(tool, str) or (tool not in TOOLS and not P95.match(tool)):
        raise ValueError("Unsupported workload: " + str(tool))
    duration = task.get("duration_seconds")
    if type(duration) is not int or not 1 <= duration <= 604800:
        raise ValueError("duration_seconds must be 1..604800")
    for field, maximum in (("threads", 8192), ("memory_mb", 1048576)):
        if field in task and (type(task[field]) is not int or not 1 <= task[field] <= maximum):
            raise ValueError("Invalid " + field)
    if "threads" in task and tool not in {"cpu-burn", "stress", "stress-ng", "bcfi", "bcfd"}:
        raise ValueError("This tool does not accept threads; use its explicit workload configuration")
    if "memory_mb" in task and tool != "mbw":
        raise ValueError("memory_mb is only supported by mbw")
    if "protection" in task:
        validate_protection(task["protection"])
    if "acceptance" in task:
        validate_acceptance(task["acceptance"])
    return task


def command(task, tool_root):
    validate_task(task)
    tool = task["tool"]
    threads = task.get("threads", os.cpu_count() or 1)
    if threads > (os.cpu_count() or 1):
        raise ValueError("Requested threads exceed this host's logical CPU count")
    if tool == "cpu-burn":
        return [sys.executable, "-m", "ocrun.cpu_burn", "--workers", str(threads)], tool_root, False
    relative = {
        "stress": "bin/stress/stress", "stress-ng": "bin/stress-ng/stress-ng",
        "mlc": "bin/mlc/mlc", "mbw": "bin/mbw/mbw", "bcr": "bin/bc/bcr",
        "bcfi": "bin/bc/bcfi", "bcfd": "bin/bc/bcfd", "ptu": "bin/ptu/ptu",
        "sysjitter": "bin/sysjitter/sysjitter", "unixbench": "bin/unixbench/Run",
        "cpu2017": "cpu2017/bin/runcpu",
    }
    candidate = os.path.join(tool_root, "bin", tool, "mprime") if P95.match(tool) else os.path.join(tool_root, relative[tool])
    # Prefer the distribution's compatible stress programs when installed.
    if tool in ("stress", "stress-ng"):
        candidate = shutil.which(tool) or candidate
    if not os.path.isfile(candidate) or not os.access(candidate, os.X_OK):
        raise ValueError("Workload executable is unavailable: " + candidate)
    args = [candidate]
    if tool in ("stress", "stress-ng"):
        args += ["--cpu", str(threads)]
        if tool == "stress-ng":
            args += ["--verify", "--metrics-brief"]
    elif P95.match(tool):
        args += ["-t"]
    elif tool == "mbw":
        args += ["-n", "5", str(task.get("memory_mb", 1024))]
    elif tool == "ptu":
        args += ["-y"]
    elif tool == "sysjitter":
        args += ["--runtime", "10", "200"]
    elif tool in ("bcfi", "bcfd"):
        args += [str(threads), "1"]
    elif tool == "cpu2017":
        spec_root = os.path.join(tool_root, "cpu2017")
        # The old run-spec hard-coded /root/ocrun. Source the suite environment
        # from its installed location; the shell text is fixed, never user supplied.
        args = ["/bin/bash", "--noprofile", "--norc", "-c",
                "source ./shrc && exec bin/runcpu -c cpu2017 --reportable --tune=all all"]
        return args, spec_root, True
    return args, os.path.dirname(candidate), tool in ONE_SHOT
