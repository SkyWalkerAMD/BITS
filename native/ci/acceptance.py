"""Real package/systemd/tool execution; sckocp readings are synthetic, not hardware evidence."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request

assert os.environ.get("GITHUB_ACTIONS") == "true"
os.umask(0o077)
ROOT = Path("/src")
sys.path.insert(0, str(ROOT / "tests"))
from sckocp_detail_fixture import INFO, OVERVIEW


def run(*args):
    try:
        return subprocess.check_output(args, stderr=subprocess.STDOUT, timeout=120).decode()
    except subprocess.CalledProcessError as exc:
        print(exc.output.decode(errors="replace"))
        raise


def write(path, value, mode=0o600):
    path = Path(path)
    path.write_text(value, encoding="utf-8")
    path.chmod(mode)


def api(path, data=None):
    request = urllib.request.Request(admin["url"] + "/api/v1/" + path,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json", "X-BITS-Request": "1",
                 "Authorization": "Bearer " + admin["token"]})
    with urllib.request.urlopen(request, context=context, timeout=20) as response:
        return json.load(response)


def wait_batch(batch_id, state, seconds=100):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = api("batches/" + batch_id)
        if value["state"] == state:
            return value
        if value["state"] == "needs_attention" and state == "delivered":
            raise AssertionError(value)
        time.sleep(1)
    raise AssertionError(value)


def new_batch(label, tools):
    return api("batches", {"node": "BITS-CLOUD", "label": label,
        "steps": [{"tool": tool, "seconds": seconds} for tool, seconds in tools]})


def main():
    global admin, context
    print(run("bits-center", "version").strip())
    print("Python:", sys.version)
    assert not Path("/usr/bin/mon-sensors").exists()
    assert not Path("/usr/bin/redis-cli").exists()
    assert not Path("/usr/bin/rsync").exists()
    assert subprocess.call(["systemctl", "is-active", "--quiet", "bits-node"]) != 0
    assert subprocess.call(["systemctl", "is-active", "--quiet", "bits-center"]) != 0
    # Closed synthetic provider. Every other command, including authorization
    # commands, fails. This fixture is never included in an installation package.
    sample = {"schema": "sckocp-mon-v1", "version": "1.2.0", "vendor": "GenuineIntel",
        "family": 6, "interval_s": 1,
        "sockets": [{"id": 0, "tjmax_c": 94, "temp_max_c": 34, "vid_v": .91,
                     "core_mhz": 3300, "base_mhz": 2500, "pkg_w": 108}],
        "cores": [{"cpu": i, "socket": 0, "mhz": 3297, "temp_c": 24,
                   "vid_v": .91, "c0_pct": 100, "c6_pct": 0} for i in range(24)]}
    fake = ("#!/usr/bin/python3\nimport json,sys,time,os\n"
            "args=sys.argv[1:]\n"
            "with open('/root/bits-ci-provider-calls.jsonl','a') as f: f.write(json.dumps(args)+'\\n')\n"
            "if os.path.exists('/root/bits-ci-provider-deny'): sys.exit(10)\n"
            "if args==['info'] and os.path.exists('/root/bits-ci-provider-info-deny'): sys.exit(10)\n"
            "if args==['mon','--json']:\n"
            " time.sleep(0.2); print(" + repr(json.dumps(sample)) + ")\n"
            "elif args==['mon','--cols=1']: sys.stdout.buffer.write(" + repr(OVERVIEW.encode("utf-8")) + ")\n"
            "elif args==['info']: sys.stdout.buffer.write(" + repr(INFO.encode("utf-8")) + ")\n"
            "else: sys.exit(87)\n")
    write("/usr/bin/sckocp", fake, 0o755)
    write("/root/bits-ci-provider-calls.jsonl", "")
    address = sys.argv[1]
    protected = Path("/root/bits-ci-private-parent")
    protected.mkdir(mode=0o700)
    assert subprocess.call(["bits-center", "setup", "--address", address, "--network", "172.16.0.0/12",
                           "--config", str(protected / "center/config.json"), "--check"],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0
    assert protected.stat().st_mode & 0o777 == 0o700 and not list(protected.iterdir())
    setup = ("bits-center", "setup", "--address", address, "--network", "172.16.0.0/12", "--port", "18443")
    print(run(*(setup + ("--check",))))
    print(run(*(setup + ("--apply",))))
    run("systemctl", "start", "bits-center")
    admin = json.loads(Path("/root/.bits/admin.json").read_text())
    context = ssl.create_default_context(cadata=admin["ca_pem"])
    for attempt in range(20):
        try:
            api("overview")
            break
        except Exception:
            time.sleep(1)
    run("bits-center", "node-add", "--node", "BITS-CLOUD", "--serial", "CLOUD-SYNTHETIC",
        "--keep-on", "--output", "/root/node.json")
    Path("/root/node.json").chmod(0o640)
    assert subprocess.call(["bits-node", "enroll", "--file", "/root/node.json"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0
    Path("/root/node.json").chmod(0o600)
    run("bits-node", "enroll", "--file", "/root/node.json")
    run("bits-node", "check")
    run("systemctl", "start", "bits-node")
    first = new_batch("CLOUD-NORMAL", [("stress", 4), ("stress-ng", 4), ("stress", 3)])
    time.sleep(6)
    assert api("batches/" + first["id"])["state"] == "draft"
    api("batches/" + first["id"] + "/start", {})
    finished = wait_batch(first["id"], "delivered")
    assert finished["result"]["execution"] == "completed", finished
    assert len(finished["result"]["steps"]) == 3
    assert all(s["cleanup_confirmed"] for s in finished["result"]["steps"])
    evidence = Path("/var/lib/bits/center/artifacts") / first["id"]
    receipt = (evidence / "receipt.json").read_bytes()
    assert hashlib.sha256(receipt).hexdigest() == finished["receipt_sha256"]
    for name, meta in finished["artifacts"].items():
        content = (evidence / name).read_bytes()
        assert hashlib.sha256(content).hexdigest() == meta["sha256"]
        assert len(content) == meta["bytes"]
        if name.endswith((".json", ".jsonl", ".html", ".mon")):
            assert b"tRFC" not in content and b"tREFI" not in content and b"tRCD_WR" not in content, name
    detail = json.loads((evidence / "report.json").read_text())
    assert detail["schema"] == "bits-acceptance-report-v1"
    assert detail["statistics"]["details"]["last_info"]
    html = (evidence / "report.html").read_text()
    assert "Primary" in html and "1.83" in html and "490.00" in html
    # A crash after sealing must restore only publication, not execute again.
    run("systemctl", "stop", "bits-node")
    run_dir = Path("/var/lib/bits/node/runs") / first["id"]
    result = json.loads((run_dir / "result.json").read_text())
    result["report"] = "not_generated"
    write(run_dir / "result.json", json.dumps(result))
    python = "/usr/libexec/platform-python" if Path("/usr/libexec/platform-python").exists() else "/usr/bin/python3"
    worker = "/opt/bits/native/0.4.0-alpha.1/worker/worker.py"
    run(python, "-I", "-S", "-B", worker, "report", str(run_dir))
    assert json.loads((run_dir / "result.json").read_text())["report"] == "generated"
    assert receipt == (evidence / "receipt.json").read_bytes()
    run("systemctl", "start", "bits-node")
    cancelled = new_batch("CLOUD-CANCEL", [("stress", 40)])
    api("batches/" + cancelled["id"] + "/start", {})
    wait_batch(cancelled["id"], "running")
    time.sleep(3)
    api("batches/" + cancelled["id"] + "/cancel", {"reason": "Isolated cancellation verification"})
    cancel_result = wait_batch(cancelled["id"], "delivered")
    assert cancel_result["result"]["execution"] == "interrupted", cancel_result
    interrupted = new_batch("CLOUD-SERVICE-INTERRUPT", [("stress", 40)])
    api("batches/" + interrupted["id"] + "/start", {})
    wait_batch(interrupted["id"], "running")
    time.sleep(3)
    run("systemctl", "stop", "bits-node")
    run("systemctl", "start", "bits-node")
    interrupted = wait_batch(interrupted["id"], "delivered")
    assert interrupted["result"]["execution"] == "interrupted", interrupted
    assert len(interrupted["result"]["steps"]) == 1
    assert interrupted["result"]["steps"][0]["cleanup_confirmed"]
    calls = [json.loads(line) for line in Path("/root/bits-ci-provider-calls.jsonl").read_text().splitlines()]
    assert calls and all(call in [["mon", "--json"], ["mon", "--cols=1"], ["info"]] for call in calls)
    run("systemctl", "stop", "bits-node")
    run("systemctl", "start", "bits-node")
    time.sleep(6)
    assert api("batches/" + first["id"])["receipt_sha256"] == finished["receipt_sha256"]
    processes = run("ps", "-eo", "comm=")
    assert not any(p.strip().startswith("stress") for p in processes.splitlines()), processes
    # Native authorization denial remains a hard stop; no activation/refresh
    # operation or fallback collector may be attempted by BITS.
    write("/root/bits-ci-provider-deny", "CI gate")
    denied = new_batch("CLOUD-NATIVE-DENIAL", [("stress", 4)])
    api("batches/" + denied["id"] + "/start", {})
    denied = wait_batch(denied["id"], "needs_attention")
    assert denied["result"]["execution"] == "preflight_failed", denied
    assert not (Path("/var/lib/bits/node/runs") / denied["id"] / "execution.json").exists()
    api("batches/" + denied["id"] + "/close-incomplete", {"reason": "Synthetic native denial retained"})
    Path("/root/bits-ci-provider-deny").unlink()
    write("/root/bits-ci-provider-info-deny", "CI supplement gate")
    info_denied = new_batch("CLOUD-INFO-DENIAL", [("stress", 4)])
    api("batches/" + info_denied["id"] + "/start", {})
    info_denied = wait_batch(info_denied["id"], "needs_attention")
    assert info_denied["result"]["execution"] == "preflight_failed", info_denied
    assert not (Path("/var/lib/bits/node/runs") / info_denied["id"] / "execution.json").exists()
    api("batches/" + info_denied["id"] + "/close-incomplete", {"reason": "Synthetic supplemental denial retained"})
    Path("/root/bits-ci-provider-info-deny").unlink()
    calls = [json.loads(line) for line in Path("/root/bits-ci-provider-calls.jsonl").read_text().splitlines()]
    assert all(call in [["mon", "--json"], ["mon", "--cols=1"], ["info"]] for call in calls)
    ports = run("ss", "-lntp")
    assert ":6379 " not in ports and ":873 " not in ports
    summary = {"status": "passed", "version": "0.4.0-alpha.1", "python": sys.version,
        "os": Path("/etc/os-release").read_text(), "batch": finished["id"],
        "normal_steps": finished["result"]["steps"], "cancel_execution": cancel_result["result"]["execution"],
        "service_restart_execution": interrupted["result"]["execution"],
        "sckocp": "synthetic fixed mon/info only; Primary-filter tested",
        "services": "real systemd, HTTPS, SQLite; no Redis/rsync",
        "native_authorization_denial": "base and supplemental denial: no workload, no permission-management call",
        "hardware_validated": False}
    write("/results/acceptance.json", json.dumps(summary, ensure_ascii=False, indent=2))
    shutil.copy2(str(evidence / "report.html"), "/results/report-preview.html")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        subprocess.call(["journalctl", "-u", "bits-center", "-u", "bits-node", "--no-pager", "-n", "100"])
        for path in Path("/var/lib/bits/node").rglob("worker.log"):
            print(str(path), path.read_text(errors="replace")[-10000:])
        raise
