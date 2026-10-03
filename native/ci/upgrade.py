"""Upgrade actual 0.4.0 RPM/DEB packages in an isolated Linux systemd fixture."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import ssl
import sqlite3
import subprocess
import sys
import time

assert sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
os.umask(0o077)
spec = importlib.util.spec_from_file_location("acceptance", "/src/native/ci/acceptance.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
OLD, NEW = "0.4.0", "0.4.1"
ROLES = ("center", "node")


def versions(expected):
    for role in ROLES:
        assert a.run("bits-" + role, "version").strip() == "BITS " + expected


def active(role):
    return subprocess.call(["systemctl", "is-active", "--quiet", "bits-" + role]) == 0


def ready():
    for unused in range(40):
        try:
            return a.api("overview")
        except (OSError, ValueError):
            time.sleep(.5)
    raise AssertionError("Center did not become ready")


def hashes(directory):
    return {p.relative_to(directory).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in directory.rglob("*") if p.is_file()}


def main():
    address, kind = sys.argv[1:]
    assert kind in ("rpm", "deb")
    versions(OLD)
    a.install_provider()
    a.run("bits-center", "setup", "--address", address, "--network", "172.16.0.0/12",
          "--port", "18443", "--apply")
    a.run("systemctl", "enable", "--now", "bits-center")
    a.admin = json.loads(Path("/root/.bits/admin.json").read_text())
    a.context = ssl.create_default_context(cadata=a.admin["ca_pem"])
    ready()
    # Synthetic credentials, never production values; the container has no BMC.
    a.api("dispatch/bmc-profiles", {"name": "upgrade-fixture", "enabled": True,
        "networks": ["192.168.50.0/24"], "node_prefix": "BITS-", "model": "",
        "username": "ci-upgrade", "password": "CI-UPGRADE-ONLY", "cipher": 17})
    a.run("bits-center", "node-add", "--node", "BITS-CLOUD", "--serial", "CLOUD-UPGRADE",
          "--keep-on", "--bmc-profile", "upgrade-fixture", "--output", "/root/node.json")
    a.run("bits-node", "enroll", "--file", "/root/node.json")
    a.run("systemctl", "enable", "--now", "bits-node")
    for unused in range(40):
        if a.api("overview")["nodes"][0].get("last_seen"):
            break
        time.sleep(.5)
    assert a.api("overview")["nodes"][0].get("last_seen")
    first = a.new_batch("UPGRADE-BEFORE", [("stress", 4)])
    a.api("batches/" + first["id"] + "/start", {})
    old_batch = a.wait_batch(first["id"], "delivered")
    evidence = Path("/var/lib/bits/center/artifacts") / first["id"]
    old_files = hashes(evidence)
    assert old_files and "receipt.json" in old_files
    profiles = a.api("dispatch/bmc-profiles")
    private_paths = ("/etc/bits/center/config.json", "/etc/bits/node/connection.json",
                     "/root/.bits/admin.json")
    identities = {p: Path(p).read_bytes() for p in private_paths}
    packages = sorted(str(p) for p in Path("/updates").glob("*." + kind))
    assert len(packages) == 2
    command = (["dnf", "upgrade", "-y"] if kind == "rpm" else ["apt-get", "install", "-y"]) + packages
    refused = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    assert refused.returncode != 0, "An active-service upgrade must be refused"
    assert b"Stop this BITS service before changing its package" in refused.stdout
    versions(OLD)
    assert all(active(role) for role in ROLES)
    a.run("systemctl", "stop", "bits-node", "bits-center")
    private_profiles = json.loads(Path("/var/lib/bits/center/bmc-auto.json").read_text())["profiles"]
    print(a.run(*command))
    versions(NEW)
    assert not any(active(role) for role in ROLES), "Package upgrade must not start services"
    for role in ROLES:
        assert a.run("systemctl", "is-enabled", "bits-" + role).strip() == "enabled"
    assert all(Path(p).read_bytes() == value for p, value in identities.items())
    assert Path("/opt/bits/native/" + NEW + "/worker/worker.py").is_file()
    assert not Path("/opt/bits/native/" + OLD + "/worker/worker.py").exists()
    a.run("systemctl", "start", "bits-center")
    ready()
    a.run("systemctl", "start", "bits-node")
    assert a.api("dispatch/bmc-profiles") == profiles
    assert json.loads(Path("/var/lib/bits/center/bmc-auto.json").read_text())["profiles"] == private_profiles
    node = next(n for n in a.api("overview")["nodes"] if n["id"] == "BITS-CLOUD")
    assert node["bmc_profile"] == "upgrade-fixture"
    restored = a.api("batches/" + first["id"])
    assert restored["state"] == "delivered" and restored["receipt_sha256"] == old_batch["receipt_sha256"]
    assert hashes(evidence) == old_files
    backup = Path("/var/lib/bits/center/center.sqlite.before-node-operations-v3")
    assert backup.is_file() and backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(str(backup)) as previous:
        assert previous.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0] == "3"
    assert a.api("dispatch/wakes") == []
    a.wait_monitor()
    info = a.api("nodes/BITS-CLOUD/hardware-info")
    assert info["status"] == "ok" and info["snapshot"]["sections"]
    assert len(a.api("overview")["batches"]) == 1, "Monitoring must not create a batch"
    second = a.new_batch("UPGRADE-AFTER", [("stress", 4)])
    a.api("batches/" + second["id"] + "/start", {})
    a.wait_batch(second["id"], "delivered")
    a.wait_monitor()
    assert hashes(evidence) == old_files, "New monitoring or execution changed sealed evidence"
    a.api("deletions", {"request_id": "123456789abcdef0123456789abcdef0", "batches": [second["id"]]})
    for unused in range(40):
        if all(b["id"] != second["id"] for b in a.api("overview")["batches"]):
            break
        time.sleep(.5)
    assert not (Path("/var/lib/bits/center/artifacts") / second["id"]).exists()
    assert hashes(evidence) == old_files, "Deletion touched an unselected old report"
    result = {"status": "passed", "from": OLD, "to": NEW, "format": kind,
        "checks": ["active-service upgrade refused", "real package-manager upgrade",
                   "services remain stopped; enablement preserved", "node identity and TLS credentials preserved",
                   "BMC template credentials and enrollment selection preserved", "sealed report and receipt hashes unchanged",
                   "schema 3 backup and schema 4 operations migration", "idle monitoring and hardware info after upgrade", "explicit new batch delivered", "monitoring resumes without a new batch", "permanent deletion preserves unselected old evidence"],
        "hardware_readings": "synthetic; no physical BMC or sensor validation"}
    Path("/results/upgrade.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
