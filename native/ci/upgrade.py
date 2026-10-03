"""Exercise package-owned lifecycle against immutable releases on real systemd."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import ssl
import sqlite3
import subprocess
import sys
import tarfile
import time

assert sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
os.umask(0o077)
spec = importlib.util.spec_from_file_location("acceptance", "/src/native/ci/acceptance.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)
NEW = "0.4.2"
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
    address, kind, old = sys.argv[1:]
    assert kind in ("rpm", "deb")
    versions(old)
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
    # A different center data path and port demonstrate package behavior is not
    # tied to the pilot server, its network, or the default data directory.
    a.run("systemctl", "stop", "bits-center")
    center_data = Path("/srv/bits-upgrade-center")
    shutil.move("/var/lib/bits/center", str(center_data))
    config_file = Path("/etc/bits/center/custom.json")
    Path("/etc/bits/center/config.json").rename(config_file)
    cfg = json.loads(config_file.read_text())
    cfg["data"] = str(center_data)
    config_file.write_text(json.dumps(cfg))
    override = Path("/etc/systemd/system/bits-center.service.d")
    override.mkdir(mode=0o755)
    (override / "config.conf").write_text("[Service]\nExecStart=\nExecStart=/usr/bin/bits-center serve --config " + str(config_file) + "\n")
    a.run("systemctl", "daemon-reload")
    a.run("systemctl", "start", "bits-center")
    ready()
    packages = sorted(str(p) for p in Path("/updates").glob("*." + kind))
    assert len(packages) == 2
    action = "upgrade" if old == "0.4.0" else "install"
    command = (["dnf", action, "-y"] if kind == "rpm" else ["apt-get", "install", "-y"]) + packages
    first = a.new_batch("UPGRADE-BEFORE", [("stress", 120)])
    a.api("batches/" + first["id"] + "/start", {})
    a.wait_batch(first["id"], "running")
    node_pid = a.run("systemctl", "show", "bits-node", "-p", "MainPID", "--value")
    refused = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
    Path("/results/busy-upgrade.txt").write_bytes(refused.stdout)
    assert refused.returncode != 0, "An unfinished batch must prevent package replacement"
    for role in ROLES:
        assert ("BITS " + role + " has unfinished work").encode() in refused.stdout, refused.stdout.decode(errors="replace")
    versions(old)
    assert all(active(role) for role in ROLES)
    assert a.run("systemctl", "show", "bits-node", "-p", "MainPID", "--value") == node_pid
    assert a.api("batches/" + first["id"])["state"] == "running", "Busy rejection interrupted execution"
    a.api("batches/" + first["id"] + "/cancel", {"reason": "CI explicit cancellation after busy rejection"})
    old_batch = a.wait_batch(first["id"], "delivered")
    run_path = Path("/var/lib/bits/node/runs") / first["id"] / "run.json"
    for unused in range(80):
        if json.loads(run_path.read_text())["phase"] == "done":
            break
        time.sleep(.25)
    assert json.loads(run_path.read_text())["phase"] == "done"
    evidence = center_data / "artifacts" / first["id"]
    old_files = hashes(evidence)
    assert old_files and "receipt.json" in old_files
    profiles = a.api("dispatch/bmc-profiles")
    private_paths = (str(config_file), "/etc/bits/node/connection.json",
                     "/root/.bits/admin.json")
    identities = {p: Path(p).read_bytes() for p in private_paths}
    private_profiles = json.loads((center_data / "bmc-auto.json").read_text())["profiles"]
    print(a.run(*command))
    versions(NEW)
    assert all(active(role) for role in ROLES), "Package transaction must restore running services"
    for role in ROLES:
        assert a.run("systemctl", "is-enabled", "bits-" + role).strip() == "enabled"
    assert all(Path(p).read_bytes() == value for p, value in identities.items())
    assert Path("/opt/bits/native/" + NEW + "/worker/worker.py").is_file()
    assert not Path("/opt/bits/native/" + old + "/worker/worker.py").exists()
    ready()
    assert a.api("dispatch/bmc-profiles") == profiles
    assert json.loads((center_data / "bmc-auto.json").read_text())["profiles"] == private_profiles
    node = next(n for n in a.api("overview")["nodes"] if n["id"] == "BITS-CLOUD")
    assert node["bmc_profile"] == "upgrade-fixture"
    restored = a.api("batches/" + first["id"])
    assert restored["state"] == "delivered" and restored["receipt_sha256"] == old_batch["receipt_sha256"]
    assert hashes(evidence) == old_files
    for role in ROLES:
        backups = sorted((Path("/var/backups/bits") / role).glob("*/before.tar"))
        assert len(backups) == 1 and backups[0].stat().st_mode & 0o777 == 0o600
        assert backups[0].parent.stat().st_mode & 0o777 == 0o700
        with tarfile.open(str(backups[0])) as saved:
            assert (str(config_file).lstrip("/") if role == "center" else "etc/bits/node/connection.json") in saved.getnames()
            if role == "center":
                for name, digest in old_files.items():
                    content = saved.extractfile(str(evidence / name).lstrip("/")).read()
                    assert hashlib.sha256(content).hexdigest() == digest
                before_db = Path("/root/center-before.sqlite")
                before_db.write_bytes(saved.extractfile(str(center_data / "center.sqlite").lstrip("/")).read())
                for suffix in ("-wal", "-shm"):
                    member = str(center_data / ("center.sqlite" + suffix)).lstrip("/")
                    if member in saved.getnames():
                        Path(str(before_db) + suffix).write_bytes(saved.extractfile(member).read())
                with sqlite3.connect(str(before_db)) as previous:
                    assert previous.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                    assert previous.execute("SELECT count(*) FROM batches").fetchone()[0] == 1
        assert not (Path("/var/lib/bits-package") / (role + ".json")).exists()
        assert not (Path("/run/systemd/system") / ("bits-" + role + ".service")).exists()
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
    assert not (center_data / "artifacts" / second["id"]).exists()
    assert hashes(evidence) == old_files, "Deletion touched an unselected old report"
    for unused in range(80):
        if all(json.loads(p.read_text())["phase"] == "done" for p in Path("/var/lib/bits/node/runs").glob("*/run.json")):
            break
        time.sleep(.25)
    # Reinstall exercises the new cooperative maintenance gate. Preserve both
    # active-but-disabled and deliberately stopped/disabled service choices.
    a.run("systemctl", "disable", "bits-center", "bits-node")
    reinstall = (["dnf", "reinstall", "-y"] if kind == "rpm" else ["apt-get", "install", "--reinstall", "-y"]) + packages
    print(a.run(*reinstall))
    assert all(active(role) for role in ROLES)
    a.run("systemctl", "stop", "bits-node")
    print(a.run(*reinstall))
    assert active("center") and not active("node")
    for role in ROLES:
        assert subprocess.run(["systemctl", "is-enabled", "bits-" + role], stdout=subprocess.PIPE).stdout.strip() == b"disabled"
    assert all(Path(p).read_bytes() == value for p, value in identities.items())
    # A failed backup must abort before unpack and restore the previous service.
    tar = Path("/usr/bin/tar")
    tar_bytes, tar_mode = tar.read_bytes(), tar.stat().st_mode & 0o777
    center_package = next(p for p in packages if "bits-center" in p)
    try:
        tar.write_text("#!/bin/sh\nexit 75\n")
        failed = subprocess.run(reinstall[:-2] + [center_package], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
        Path("/results/backup-failure.txt").write_bytes(failed.stdout)
        assert failed.returncode != 0 and b"command failed: tar" in failed.stdout, failed.stdout.decode(errors="replace")
    finally:
        tar.write_bytes(tar_bytes)
        tar.chmod(tar_mode)
    assert active("center") and not active("node")
    assert hashes(evidence) == old_files
    assert not Path("/var/lib/bits-package/center.json").exists()
    result = {"status": "passed", "from": old, "to": NEW, "format": kind,
        "checks": ["busy upgrade refused without interrupting the worker", "single package-manager command upgrades active idle services",
                   "automatic private full backup with readable consistent database and identical evidence", "custom center data/config paths and HTTPS port",
                   "active, stopped and disabled states preserved on reinstall", "backup failure aborts replacement and restores service", "node identity and TLS credentials preserved",
                   "BMC template credentials and enrollment selection preserved", "sealed report and receipt hashes unchanged",
                   "idle monitoring and hardware info after upgrade", "explicit new batch delivered", "monitoring resumes without a new batch", "permanent deletion preserves unselected old evidence"],
        "hardware_readings": "synthetic; no physical BMC or sensor validation"}
    Path("/results/upgrade.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
