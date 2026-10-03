"""Destructive purge acceptance ONLY inside the disposable Linux CI container."""
import contextlib
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import pwd
import shutil
import ssl
import subprocess
import sys
import time

assert sys.platform == "linux" and os.environ.get("GITHUB_ACTIONS") == "true"
assert Path("/src/native/ci/acceptance.py").is_file() and Path("/run/systemd/system").is_dir()
os.umask(0o077)
spec = importlib.util.spec_from_file_location("acceptance", "/src/native/ci/acceptance.py")
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)


def active(role):
    return subprocess.call(["systemctl", "is-active", "--quiet", "bits-" + role]) == 0


def call(role, *args, **kwargs):
    result = subprocess.run(["bits-" + role, "uninstall", "--purge"] + list(args),
                            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=150)
    if kwargs.get("ok", True):
        assert result.returncode == 0, result.stdout.decode(errors="replace")
    else:
        assert result.returncode != 0, result.stdout.decode(errors="replace")
    return result.stdout.decode()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ready():
    for unused in range(80):
        try:
            return a.api("overview")
        except (OSError, ValueError):
            time.sleep(.25)
    raise AssertionError("center did not restart")


@contextlib.contextmanager
def package_failure(kind, script):
    executable = Path(shutil.which("rpm" if kind == "rpm" else "dpkg"))
    saved = executable.with_name(executable.name + ".bits-ci-real")
    assert not saved.exists()
    # Rename rather than truncate a possibly executing package manager.
    executable.rename(saved)
    remove = "-e" if kind == "rpm" else "--purge"
    executable.write_text("#!/bin/sh\nif [ \"$1\" = " + remove + " ]; then\n" +
                          script.replace("@REAL@", str(saved)) + "\nfi\nexec " + str(saved) + " \"$@\"\n")
    executable.chmod(0o755)
    try:
        yield
    finally:
        executable.unlink()
        saved.rename(executable)


def main():
    checks = []
    a.admin = json.loads(Path("/root/.bits/admin.json").read_text())
    a.context = ssl.create_default_context(cadata=a.admin["ca_pem"])
    ready()
    # Browser fixtures may leave real drafts; they are allowed to be purged.
    for unused in range(100):
        if all(json.loads(p.read_text())["phase"] == "done" for p in Path("/var/lib/bits/node/runs").glob("*/run.json")):
            break
        time.sleep(.2)
    assert active("center") and active("node")
    kind = "rpm" if shutil.which("rpm") else "deb"
    report_dirs = list(Path("/var/lib/bits/center/artifacts").glob("*"))
    assert report_dirs, "test must purge actual evidence, not an empty deployment"
    running = a.new_batch("PURGE-BUSY-REFUSAL", [("stress", 120)])
    a.api("batches/" + running["id"] + "/start", {})
    a.wait_batch(running["id"], "running")
    pids = {role: a.run("systemctl", "show", "bits-" + role, "-p", "MainPID", "--value") for role in ("center", "node")}
    for role in ("center", "node"):
        output = call(role, "--yes", ok=False)
        assert "unfinished work" in output, output
        assert active(role) and a.run("systemctl", "show", "bits-" + role, "-p", "MainPID", "--value") == pids[role]
        assert not (Path("/var/lib/bits-uninstall") / role).exists()
    assert a.api("batches/" + running["id"])["state"] == "running"
    a.api("batches/" + running["id"] + "/cancel", {"reason": "CI explicit cancellation after purge refusal"})
    a.wait_batch(running["id"], "delivered")
    for unused in range(100):
        if all(json.loads(p.read_text())["phase"] == "done" for p in Path("/var/lib/bits/node/runs").glob("*/run.json")):
            break
        time.sleep(.2)
    checks.append("both roles refuse unfinished work without changing agent PID or interrupting stress")
    secret = digest("/etc/bits/node/connection.json")
    provider = digest("/usr/bin/sckocp")
    for role in ("center", "node"):
        output = call(role, "--dry-run")
        assert "Delete: /var/lib/bits/" + role in output
        assert active(role)
        assert "confirmation requires a terminal" in call(role, ok=False)
        assert "--include requires" in call(role, "--dry-run", "--include", "/root", ok=False)
    assert not Path("/var/lib/bits-uninstall").exists()
    checks.append("preview and missing noninteractive confirmation leave packages, services and evidence intact")

    # Custom deployment: config and store are not limited to the pilot/default paths.
    a.run("systemctl", "stop", "bits-center")
    data = Path("/srv/bits-purge-store")
    config = Path("/etc/bits-center-ci")
    shutil.move("/var/lib/bits/center", str(data))
    shutil.move("/etc/bits/center", str(config))
    cfg = json.loads((config / "config.json").read_text())
    cfg.update(data=str(data), certificate=str(config / "center.crt"), private_key=str(config / "center.key"))
    (config / "config.json").write_text(json.dumps(cfg))
    override = Path("/etc/systemd/system/bits-center.service.d")
    override.mkdir(mode=0o755, exist_ok=True)
    (override / "config.conf").write_text("[Service]\nExecStart=\nExecStart=/usr/bin/bits-center serve --config /etc/bits-center-ci/config.json\n")
    a.run("systemctl", "daemon-reload")
    # A shared/custom store must not be accepted for recursive deletion.
    (data / "unrelated.txt").write_text("retain shared files")
    assert "not exclusively a BITS store" in call("center", "--dry-run", ok=False)
    (data / "unrelated.txt").unlink()
    a.run("systemctl", "start", "bits-center")
    ready()
    preview = call("center", "--dry-run")
    assert str(data) in preview and str(config / "config.json") in preview
    checks.append("custom service config, TLS files and data discovered; shared custom directories refused")

    for role in ("center", "node"):
        backup = Path("/var/backups/bits") / role / "ci-before"
        backup.mkdir(parents=True, mode=0o700)
        (backup / "before.tar").write_bytes(b"CI automatic backup to purge")
    extra = Path("/root/bits-center-ci-install")
    extra.mkdir(mode=0o700)
    (extra / "center-before.tar").write_bytes(b"CI explicit historical backup")
    (extra / "old-package.rpm").write_bytes(b"CI explicit installer copy")
    sibling = Path("/root/bits-node-ci-keep")
    sibling.mkdir(mode=0o700)
    (sibling / "keep.txt").write_text("other role")
    outside = Path("/root/unrelated-purge-sentinel")
    outside.mkdir(mode=0o700)
    (outside / "keep.txt").write_text("original content")
    link = data / "artifacts" / "external-link"
    link.symlink_to(outside, target_is_directory=True)
    # Refuse a bind mount even when it has the same st_dev as the data tree.
    mounted = data / "artifacts" / "mounted-volume"
    mounted.mkdir(mode=0o700)
    a.run("mount", "--bind", str(outside), str(mounted))
    try:
        assert "unmount the volume" in call("center", "--dry-run", ok=False)
    finally:
        a.run("umount", str(mounted))
        mounted.rmdir()
    assert "--include requires" in call("center", "--dry-run", "--include", str(sibling), ok=False)
    # Refuse another role's tree even through a role-named parent.
    bad = Path("/root/bits-center-link")
    bad.symlink_to("/var/lib/bits/node", target_is_directory=True)
    try:
        assert "untrusted" in call("center", "--dry-run", "--include", str(bad), ok=False)
    finally:
        bad.unlink()
    checks.append("protected roots, peer installation copies, symlink roots and same-device bind mounts rejected")

    before = digest(config / "config.json")
    with package_failure(kind, "exit 75"):
        output = call("center", "--yes", ok=False)
        assert "command failed:" in output, output
    assert active("center") and digest(config / "config.json") == before
    assert data.exists() and extra.exists()
    assert not Path("/var/lib/bits-uninstall").exists()
    ready()
    checks.append("package-manager failure retains data and restores the original service and startup setting")

    output = call("center", "--yes", "--include", str(extra))
    Path("/results/center-uninstall.txt").write_text(output)
    assert not Path("/usr/bin/bits-center").exists()
    assert not data.exists() and not config.exists() and not override.exists()
    assert not Path("/root/.bits").exists() and not Path("/var/backups/bits/center").exists()
    assert not extra.exists()
    assert active("node") and digest("/etc/bits/node/connection.json") == secret
    assert (sibling / "keep.txt").read_text() == "other role"
    assert (outside / "keep.txt").read_text() == "original content"
    assert digest("/usr/bin/sckocp") == provider and pwd.getpwnam("bits")
    checks.append("real center package, custom store, reports, TLS, credentials, backups and explicit installer copies purged; node and sckocp retained")

    # Simulate an external path replacement just after package removal. The
    # replacement must be retained; the saved in-memory remover must resume
    # after its own package and CLI have gone.
    node_backup = Path("/var/backups/bits/node")
    held = Path("/root/bits-node-held-backup")
    script = ('@REAL@ "$@" || exit $?\nmv /var/backups/bits/node /root/bits-node-held-backup\n'
              'mkdir -m 700 /var/backups/bits/node\nprintf replacement > /var/backups/bits/node/keep.txt\nexit 0')
    with package_failure(kind, script):
        output = call("node", "--yes", ok=False)
    assert "purge target was replaced" in output and "Resume: python3" in output, output
    assert not Path("/usr/bin/bits-node").exists()
    assert (node_backup / "keep.txt").read_text() == "replacement"
    assert (held / "ci-before/before.tar").exists()
    resume = Path("/var/lib/bits-uninstall/node/resume.py")
    assert resume.stat().st_mode & 0o777 == 0o600 and resume.parent.stat().st_mode & 0o777 == 0o700
    # Restore the original inode, representing an operator resolving the conflict.
    (node_backup / "keep.txt").unlink()
    node_backup.rmdir()
    held.rename(node_backup)
    resumed = a.run("python3", str(resume), "--yes")
    Path("/results/node-uninstall-resumed.txt").write_text(resumed)
    for path in ("/etc/bits", "/var/lib/bits", "/var/backups/bits", "/var/lib/bits-uninstall",
                 "/var/lib/bits-package", "/opt/bits", "/usr/bin/bits-center", "/usr/bin/bits-node"):
        assert not Path(path).exists(), path
    for role in ("center", "node"):
        assert not active(role)
        assert not (Path("/etc/systemd/system/multi-user.target.wants") / ("bits-" + role + ".service")).exists()
    assert not any(p.pw_name == "bits" for p in pwd.getpwall())
    assert digest("/usr/bin/sckocp") == provider and (outside / "keep.txt").read_text() == "original content"
    assert (sibling / "keep.txt").read_text() == "other role"
    checks.append("path replacement interrupts purge without deleting replacement; private helper resumes after its CLI is uninstalled")
    checks.append("last-role purge removes payload, data, backups, service links, standard account and recovery state; independent files retained")
    # A package installed for an image but never initialized is also removable.
    for role in ("center", "node"):
        package = next(p for p in Path("/src/native-dist").glob("bits-" + role + "*." + kind))
        install = ["dnf", "install", "-y"] if kind == "rpm" else ["apt-get", "install", "-y"]
        a.run(*(install + [str(package)]))
        assert not active(role)
        call(role, "--yes")
        assert not Path("/usr/bin/bits-" + role).exists()
        assert not Path("/var/lib/bits-uninstall").exists()
    checks.append("never-configured packages install and purge without setup or enrollment")
    result = {"status": "passed", "version": "0.4.3", "format": kind, "checks": checks,
              "scope": "disposable Linux container; no production uninstall"}
    Path("/results/uninstall.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
