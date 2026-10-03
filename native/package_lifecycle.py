"""Embedded RPM/DEB lifecycle; Python 3.6+ stdlib, runs before unpack too."""
import contextlib
import fcntl
import json
import os
from pathlib import Path
import pwd
import re
import shlex
import signal
import sqlite3
import ssl
import stat
import subprocess
import sys
import tempfile
import time
import urllib.request


def command(*args, **kwargs):
    result = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=kwargs.pop("timeout", 90), **kwargs)
    if result.returncode:
        # Subprocess output may contain configuration; never echo it as root.
        raise RuntimeError("command failed: " + args[0] + " " + args[1])
    return result.stdout.decode().strip()


def private_path(path, directory=False):
    path = Path(path)
    if not path.is_absolute() or str(path) != os.path.normpath(str(path)):
        raise RuntimeError("absolute canonical path required")
    owners = {0}
    try:
        owners.add(pwd.getpwnam("bits").pw_uid)
    except KeyError:
        pass
    for part in (path,) + tuple(path.parents):
        info = part.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_mode & 0o022 or info.st_uid not in owners:
            raise RuntimeError("untrusted package state/configuration path: " + str(part))
        if part != path and not stat.S_ISDIR(info.st_mode):
            raise RuntimeError("invalid parent directory")
    info = path.stat()
    if directory and not stat.S_ISDIR(info.st_mode):
        raise RuntimeError("directory required: " + str(path))
    if not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise RuntimeError("regular single-link file required: " + str(path))
    return path


def read_json(path):
    with private_path(path).open("rb") as source:
        data = source.read((2 << 20) + 1)
    if len(data) > 2 << 20:
        raise RuntimeError("configuration exceeds size limit")
    return json.loads(data)


def write_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(value, output, indent=2)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, str(path))
        fd = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class Lifecycle:
    def __init__(self, role, version):
        self.role, self.version = role, version
        self.unit = "bits-" + role + ".service"
        self.binary = "/usr/bin/bits-" + role
        self.root = Path("/var/lib/bits-package")
        self.root.mkdir(mode=0o700, exist_ok=True)
        private_path(self.root, True)
        if self.root.stat().st_uid != 0 or self.root.stat().st_mode & 0o077:
            raise RuntimeError("package state directory must be root-only")
        self.state_path = self.root / (role + ".json")
        self.mask = Path("/run/systemd/system") / self.unit
        lock = self.root / (role + ".lock")
        self.lock = os.open(str(lock), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def properties(self):
        result = subprocess.run(["systemctl", "show", self.unit,
                         "-p", "ActiveState", "-p", "MainPID", "-p", "ExecStart", "-p", "LoadState"],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        values = dict(line.split("=", 1) for line in result.stdout.decode().splitlines() if "=" in line)
        if "LoadState" not in values:
            raise RuntimeError("systemd is unavailable; install on a booted systemd host")
        return values

    def configuration(self, props):
        config = "/etc/bits/" + self.role + ("/config.json" if self.role == "center" else "/connection.json")
        # Honor administrator --config overrides; refuse wrappers/unknown flags.
        entry = props.get("ExecStart", "")
        if entry:
            match = re.search(r"argv\[\]=(.*?) ;", entry)
            if not match:
                raise RuntimeError("cannot determine BITS service arguments")
            args = shlex.split(match.group(1))
            if args[:2] != [self.binary, "serve" if self.role == "center" else "agent"]:
                raise RuntimeError("custom service wrapper requires explicit maintenance")
            remaining = args[2:]
            if remaining:
                if len(remaining) == 2 and remaining[0] in ("--config", "-config"):
                    config = remaining[1]
                elif len(remaining) == 1 and remaining[0].startswith(("--config=", "-config=")):
                    config = remaining[0].split("=", 1)[1]
                else:
                    raise RuntimeError("unsupported service arguments; retain configuration for review")
        cfg = read_json(config) if os.path.lexists(config) else None
        data = Path(cfg["data"] if cfg and self.role == "center" else "/var/lib/bits/" + self.role)
        if data.exists():
            private_path(data, True)
        if not data.is_absolute() or ".." in data.parts or len(data.parts) < 4:
            raise RuntimeError("invalid BITS data directory")
        backups = Path("/var/backups/bits")
        if data == backups or data in backups.parents or backups in data.parents:
            raise RuntimeError("data and package backups must be separate")
        return config, data, cfg

    def node_idle(self, data):
        runs = data / "runs"
        if not runs.exists():
            return
        private_path(runs, True)
        for run in runs.iterdir():
            private_path(run, True)
            value = read_json(run / "run.json")
            if value.get("phase") != "done":
                raise RuntimeError("BITS node has unfinished work; finish or close the batch, then retry")

    @contextlib.contextmanager
    def center_idle(self, data):
        path = data / "center.sqlite"
        if not path.exists():
            yield
            return
        private_path(path)
        db = sqlite3.connect(str(path), timeout=12)
        try:
            # Reserve writes before checking. New dispatch/claims cannot race the stop.
            db.execute("BEGIN IMMEDIATE")
            schema = db.execute("SELECT value FROM metadata WHERE key='schema'").fetchone()[0]
            if schema not in ("1", "2", "3", "4"):
                raise RuntimeError("unsupported center schema; keep the installed package")
            busy = db.execute("SELECT count(*) FROM batches WHERE state NOT IN "
                              "('draft','delivered','cancelled','closed_incomplete','deleting')").fetchone()[0]
            if schema == "4":
                busy += db.execute("SELECT count(*) FROM wake_members WHERE state IN "
                                   "('pending','command_requested','waiting_agent')").fetchone()[0]
                busy += db.execute("SELECT count(*) FROM deleted_batches WHERE state != 'done'").fetchone()[0]
            if busy:
                raise RuntimeError("BITS center has unfinished work; finish or close it, then retry")
            yield
        finally:
            db.rollback()
            db.close()

    def running(self, props):
        state = props.get("ActiveState", "inactive")
        if state in ("activating", "deactivating", "reloading"):
            raise RuntimeError("BITS service is changing state; retry after it settles")
        return state == "active"

    def mask_service(self, state):
        if not os.path.lexists(str(self.mask)):
            # Record intent before creating our runtime-only mask.
            state["mask_owned"] = True
            write_json(self.state_path, state)
            command("systemctl", "mask", "--runtime", self.unit)
        command("systemctl", "daemon-reload")

    def unmask(self, state):
        if state.get("mask_owned") and self.mask.is_symlink() and os.readlink(str(self.mask)) == "/dev/null":
            self.mask.unlink()
        command("systemctl", "daemon-reload")

    def stop_node(self, props, data, state):
        self.node_idle(data)
        if not state["active"]:
            self.mask_service(state)
            return
        installed = state["from"]
        if installed not in ("0.4.0", "0.4.1") and "alpha" not in installed:
            gate = data / "maintenance.lock"
            with os.fdopen(os.open(str(gate), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600), "r+") as lock:
                deadline = time.monotonic() + 25
                while True:
                    try:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        self.node_idle(data)
                        if time.monotonic() >= deadline:
                            raise RuntimeError("BITS node is busy; retry when idle")
                        time.sleep(.1)
                self.node_idle(data)
                self.mask_service(state)
                command("systemctl", "stop", self.unit)
            return
        # Immutable pre-0.4.2 agents have no maintenance gate. Pause only their
        # main process, then inspect durable execution intent. Busy workers keep
        # running; the agent is immediately resumed if any work is unfinished.
        # An idle legacy agent is terminated while paused so it cannot claim a
        # new job between validation and systemd stopping its monitor children.
        pid = int(props["MainPID"])
        proc = Path("/proc") / str(pid)
        if pid < 2 or os.readlink(str(proc / "exe")) != self.binary:
            raise RuntimeError("cannot pin the installed node agent")
        identity = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
        paused = False
        try:
            os.kill(pid, signal.SIGSTOP)
            paused = True
            deadline = time.monotonic() + 3
            while (proc / "stat").read_text().rsplit(")", 1)[1].split()[0] != "T":
                if time.monotonic() > deadline:
                    raise RuntimeError("cannot pause legacy agent for maintenance")
                time.sleep(.01)
            self.node_idle(data)
            self.mask_service(state)
            command("systemctl", "stop", "--no-block", self.unit)
            if (proc / "stat").read_text().rsplit(")", 1)[1].split()[19] != identity:
                raise RuntimeError("node process changed during maintenance")
            os.kill(pid, signal.SIGKILL)
            paused = False
            command("systemctl", "stop", self.unit)
        finally:
            if paused and proc.exists() and (proc / "stat").read_text().rsplit(")", 1)[1].split()[19] == identity:
                os.kill(pid, signal.SIGCONT)

    def backup(self, state, config, data, cfg):
        paths = [Path(config), data]
        if self.role == "center":
            paths += [Path("/root/.bits")]
            if cfg:
                paths += [Path(cfg[key]) for key in ("certificate", "private_key")]
        # Include service overrides, preserving administrator deployment choices.
        paths += [Path("/etc/systemd/system") / (self.unit + ".d")]
        chosen = []
        for path in sorted(set(paths), key=lambda p: len(p.parts)):
            if os.path.lexists(str(path)) and not any(p == path or p in path.parents for p in chosen):
                private_path(path, path.is_dir())
                chosen.append(path)
        if not chosen:
            return
        base = Path("/var/backups/bits") / self.role
        base.mkdir(mode=0o700, parents=True, exist_ok=True)
        private_path(base, True)
        if base.stat().st_uid or base.stat().st_mode & 0o077:
            raise RuntimeError("backup directory must be root-only")
        target = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=str(base)))
        state["backup"] = str(target)
        write_json(self.state_path, state)
        archive = target / "before.tar"
        command("tar", "--acls", "--xattrs", "--selinux", "--numeric-owner", "-cpf", str(archive),
                "-C", "/", "--", *(str(p).lstrip("/") for p in chosen), timeout=None)
        with archive.open("rb") as f:
            os.fsync(f.fileno())
        write_json(target / "manifest.json", {"role": self.role, "from": state["from"],
                   "to": self.version, "active": state["active"], "paths": [str(p) for p in chosen]})
        print("BITS " + self.role + " backup: " + str(archive), flush=True)

    def prepare(self, operation="upgrade"):
        for old in ("/etc/bits/node/native.json", "/etc/bits/center/manifest.json",
                    "/etc/ocrun-node/native.json", "/etc/ocrun-server/manifest.json"):
            if os.path.lexists(old):
                raise RuntimeError("earlier deployment retained; explicit migration required")
        previous = read_json(self.state_path) if self.state_path.exists() else None
        if previous and previous.get("offline") and not Path(previous["config"]).exists():
            if previous["to"] != self.version:
                raise RuntimeError("retry the unfinished package installation first")
            return
        try:
            props = self.properties()
        except RuntimeError:
            # Image/chroot installation has no running service to maintain.
            # Never apply this exception to an already configured installation.
            if previous or Path(self.binary).exists() or Path("/etc/bits/" + self.role).exists() or Path("/var/lib/bits/" + self.role).exists():
                raise
            write_json(self.state_path, {"to": self.version, "phase": "prepared", "offline": True,
                       "from": "", "operation": operation, "mask_owned": False,
                       "active": False, "config": "/etc/bits/" + self.role +
                       ("/config.json" if self.role == "center" else "/connection.json")})
            return
        if previous:
            if previous["to"] != self.version or previous["operation"] != operation:
                raise RuntimeError("unfinished package operation; retry the same package first")
            if previous["phase"] == "prepared" and not self.running(props):
                return
            self.recover()
            props = self.properties()
        config, data, cfg = self.configuration(props)
        active = self.running(props)
        if active and cfg is None:
            raise RuntimeError("running BITS service has no readable configuration")
        installed = command(self.binary, "version").replace("BITS ", "") if Path(self.binary).exists() else ""
        state = {"to": self.version, "from": installed, "active": active, "config": config,
                 "operation": operation, "phase": "preparing", "mask_owned": False}
        write_json(self.state_path, state)
        try:
            if self.role == "center":
                with self.center_idle(data):
                    self.mask_service(state)
                    if active:
                        command("systemctl", "stop", self.unit)
            else:
                self.stop_node(props, data, state)
            if self.properties().get("ActiveState") not in ("inactive", "failed"):
                raise RuntimeError("service has not stopped")
            self.backup(state, config, data, cfg)
            state["phase"] = "prepared"
            write_json(self.state_path, state)
        except BaseException:
            self.restore(state)
            raise

    def restore(self, state):
        self.unmask(state)
        if state["active"]:
            command("systemctl", "start", self.unit)
        self.state_path.unlink()

    def finish(self, removal=False):
        if not self.state_path.exists():
            return
        state = read_json(self.state_path)
        if state["to"] != self.version or state["phase"] != "prepared":
            raise RuntimeError("package preparation is incomplete")
        if not state.get("offline"):
            self.unmask(state)
        if removal:
            command("systemctl", "disable", self.unit)
        else:
            if command(self.binary, "version") != "BITS " + self.version:
                raise RuntimeError("installed binary version differs from the package")
            if state["active"]:
                if self.role == "node":
                    command(self.binary, "check", "--config", state["config"])
                command("systemctl", "start", self.unit)
                self.ready(state)
            elif not Path(state["config"]).exists():
                hint = "setup --address <LAN-IP> --network <CIDR> --apply" if self.role == "center" else "enroll --file <connection.json>"
                print("BITS installed. Configure once: bits-" + self.role + " " + hint)
        self.state_path.unlink()

    def ready(self, state):
        deadline = time.monotonic() + 45
        cfg = read_json(state["config"])
        while time.monotonic() < deadline:
            if self.properties().get("ActiveState") == "active":
                if self.role == "node":
                    time.sleep(2)
                    if self.properties().get("ActiveState") == "active":
                        return
                else:
                    try:
                        ctx = ssl.create_default_context(cafile=cfg["certificate"])
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=ctx))
                        with opener.open(cfg["url"] + "/", timeout=2) as response:
                            if response.status == 200:
                                return
                    except (OSError, ValueError):
                        pass
            time.sleep(.5)
        raise RuntimeError("BITS service did not become ready; inspect journalctl -u " + self.unit)

    def recover(self):
        if not self.state_path.exists():
            return
        state = read_json(self.state_path)
        current = command(self.binary, "version").replace("BITS ", "") if Path(self.binary).exists() else ""
        if current != state["from"]:
            raise RuntimeError("package files changed; retry package-manager installation")
        if state.get("offline"):
            self.state_path.unlink()
        else:
            self.restore(state)


def main():
    os.umask(0o077)
    if os.geteuid() != 0:
        raise RuntimeError("package lifecycle requires root")
    role, version, phase = sys.argv[1:4]
    if role not in ("center", "node"):
        raise RuntimeError("unknown package role")
    lifecycle = Lifecycle(role, version)
    if phase == "prepare":
        lifecycle.prepare()
    elif phase == "remove-prepare":
        lifecycle.prepare("remove")
    elif phase == "finish":
        lifecycle.finish()
    elif phase == "remove-finish":
        lifecycle.finish(True)
    elif phase == "recover":
        lifecycle.recover()
    else:
        raise RuntimeError("unknown lifecycle phase")


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("BITS package: " + str(error), file=sys.stderr)
        sys.exit(1)
