"""Role-scoped, confirmed purge. Embedded with package_lifecycle.py in the CLI."""
import argparse
import errno
import grp
import os
from pathlib import Path
import pwd
import re
import shutil
import socket
import stat
import subprocess
import sys

library = {"__name__": "bits_package_lifecycle"}
exec(LIFECYCLE, library)
Lifecycle = library["Lifecycle"]
private_path = library["private_path"]
read_json = library["read_json"]
write_json = library["write_json"]
command = library["command"]

ROOT = Path("/var/lib/bits-uninstall")
SYSTEM_ROOTS = tuple(Path(p) for p in (
    "/", "/boot", "/dev", "/etc", "/home", "/lib", "/lib64", "/media",
    "/mnt", "/opt", "/proc", "/root", "/run", "/sbin", "/srv", "/sys",
    "/tmp", "/usr", "/var", "/var/lib", "/var/log", "/var/backups",
    "/etc/bits", "/var/lib/bits", "/var/backups/bits", "/opt/bits",
    "/opt/bits/native", "/opt/bits/workloads", str(ROOT), "/var/lib/bits-package"))


def overlaps(a, b):
    return a == b or a in b.parents or b in a.parents


def installed(role, kind=None):
    name = "bits-" + role
    found = []
    if kind in (None, "rpm") and shutil.which("rpm"):
        result = subprocess.run(["rpm", "-qa", "--qf", "%{NAME}\t%{VERSION}-%{RELEASE}\n"],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode == 0:
            for line in result.stdout.decode().splitlines():
                package_name, package_version = line.split("\t", 1)
                if package_name == name:
                    found.append(("rpm", package_version))
        else:
            raise RuntimeError("cannot read RPM package database")
    if kind in (None, "deb") and shutil.which("dpkg-query"):
        result = subprocess.run(["dpkg-query", "-W", "-f=${db:Status-Status}\t${Version}", name],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if result.returncode == 0:
            state, version = result.stdout.decode().split("\t", 1)
            if state != "not-installed":
                found.append(("deb", version.strip()))
        elif result.returncode != 1:
            raise RuntimeError("cannot read DEB package database")
    if len(found) > 1:
        raise RuntimeError("ambiguous BITS package registration")
    return found[0] if found else None


def machine():
    return Path("/etc/machine-id").read_text().strip()


def mounts():
    values = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        field = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), line.split()[4])
        values.append(Path(field))
    return values


def check_path(path, protected, link=False):
    path = Path(path)
    if (not path.is_absolute() or str(path) != os.path.normpath(str(path)) or ".." in path.parts or
            any(ord(c) < 32 or ord(c) == 127 for c in str(path))):
        raise RuntimeError("canonical absolute purge path required")
    if path in SYSTEM_ROOTS or any(overlaps(path, p) for p in protected):
        raise RuntimeError("protected purge path: " + str(path))
    # Do not walk a mounted volume, including a same-device bind mount.
    if any(path == p or path in p.parents for p in mounts()):
        raise RuntimeError("unmount the volume inside this purge path first: " + str(path))
    parent = path.parent
    while not os.path.lexists(str(parent)):
        parent = parent.parent
    private_path(parent, True)
    if os.path.lexists(str(path)):
        if link and path.is_symlink() and os.readlink(str(path)) == "/dev/null":
            return
        private_path(path, path.is_dir())


def fingerprint(path):
    info = path.lstat()
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]


def remove_at(parent, name, device):
    """Descriptor-relative traversal: links are unlinked, never followed."""
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if info.st_dev != device:
        raise RuntimeError("filesystem changed during purge")
    if stat.S_ISDIR(info.st_mode):
        child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            opened = os.fstat(child)
            if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                raise RuntimeError("directory changed during purge")
            for entry in os.listdir(child):
                remove_at(child, entry, device)
            current = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if (current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino):
                raise RuntimeError("directory changed during purge")
            os.rmdir(name, dir_fd=parent)
        finally:
            os.close(child)
    else:
        os.unlink(name, dir_fd=parent)


def remove_path(item, protected):
    path = Path(item["path"])
    check_path(path, protected, link=item.get("mask", False))
    if not os.path.lexists(str(path)):
        return
    if fingerprint(path) != item["identity"]:
        raise RuntimeError("purge target was replaced; retained: " + str(path))
    # Open every parent without following links, even if one changed since check.
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for name in path.parent.parts[1:]:
            next_fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        current = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        if [current.st_dev, current.st_ino, stat.S_IFMT(current.st_mode)] != item["identity"]:
            raise RuntimeError("purge target changed")
        remove_at(fd, path.name, current.st_dev)
    finally:
        os.close(fd)


def prune(paths):
    for path in sorted(set(Path(p) for p in paths), key=lambda p: len(p.parts), reverse=True):
        try:
            private_path(path, True)
            path.rmdir()  # Empty directories only; never recurse through a parent.
        except FileNotFoundError:
            pass
        except OSError as error:
            if error.errno not in (errno.ENOTEMPTY, errno.EEXIST):
                raise


def protection(role, version):
    other = "node" if role == "center" else "center"
    peer = Lifecycle(other, version, read_only=True)
    config, data, cfg = peer.configuration(peer.properties())
    protected = [Path("/etc/bits/" + other), Path("/var/lib/bits/" + other), data,
                 Path(config), Path("/var/backups/bits/" + other), ROOT]
    if cfg and other == "center":
        protected += [Path(cfg[k]) for k in ("certificate", "private_key")]
    # These are independent installations, not payloads owned by a BITS role.
    protected += [Path(p) for p in ("/etc/sckocp", "/opt/sckocp", "/var/lib/sckocp",
                  "/root/.sckocp", "/root/sckocp", "/root/ocrun", "/etc/ocrun-node",
                  "/etc/ocrun-server")]
    return protected


def plan(life, includes):
    package = installed(life.role)
    if not package:
        raise RuntimeError("installed BITS package not found")
    if any(Path(p).exists() for p in ("/etc/bits/node/native.json", "/etc/bits/center/manifest.json",
                                     "/etc/ocrun-node/native.json", "/etc/ocrun-server/manifest.json")):
        raise RuntimeError("legacy installation retained; use its own uninstall procedure")
    if life.state_path.exists():
        raise RuntimeError("finish the pending package installation before uninstalling")
    props = life.properties()
    config, data, cfg = life.configuration(props)
    if life.running(props) and cfg is None:
        raise RuntimeError("running service has no readable configuration")
    protected = protection(life.role, life.version)
    paths = [Path("/etc/bits/" + life.role), Path("/var/lib/bits/" + life.role), data, Path(config),
             Path("/var/backups/bits/" + life.role)]
    empty = ["/etc/bits", "/var/lib/bits", "/var/backups/bits", str(Path(config).parent)]
    retained = []
    if life.role == "center":
        paths += [Path("/root/.bits/admin.json")]
        empty += ["/root/.bits"]
        if cfg:
            for key in ("certificate", "private_key"):
                certificate = Path(cfg[key])
                if certificate.parent == Path(config).parent:
                    paths.append(certificate)
                else:
                    retained.append(str(certificate) + " (external TLS file)")
        if str(data) != "/var/lib/bits/center" and data.exists():
            known = {"center.sqlite", "center.sqlite-wal", "center.sqlite-shm", "artifacts",
                     "bmc.json", "bmc-auto.json", "center.sqlite.before-dispatch-v1",
                     "center.sqlite.before-bmc-enrollment-v2", "center.sqlite.before-node-operations-v3",
                     "center.sqlite.before-system-access-v4", "ssh.json", "ssh-key.json"}
            if not (data / "center.sqlite").is_file() or any(p.name not in known for p in data.iterdir()):
                raise RuntimeError("custom data directory is not exclusively a BITS store; retained: " + str(data))
    for extra in includes:
        path = Path(extra)
        # Additional copies are explicit, role-named installation files/folders,
        # never a recursive scan of /root, /mnt or other users' directories.
        if not re.match(r"^bits-" + life.role + r"(?:[._-].+)?$", path.name):
            raise RuntimeError("--include requires a bits-" + life.role + " named installation path")
        if not os.path.lexists(str(path)):
            raise RuntimeError("included path does not exist: " + str(path))
        paths.append(path)
    paths += [Path("/etc/systemd/system") / (life.unit + ".d"), Path("/etc/systemd/system") / life.unit]
    chosen = []
    for path in sorted(set(paths), key=lambda p: (len(p.parts), str(p))):
        mask = path == Path("/etc/systemd/system") / life.unit
        check_path(path, protected, link=mask)
        if os.path.lexists(str(path)) and not any(Path(p["path"]) in path.parents for p in chosen):
            chosen.append({"path": str(path), "identity": fingerprint(path), "mask": mask})
    enabled = subprocess.run(["systemctl", "is-enabled", life.unit], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=15).stdout.decode().strip()
    return {"role": life.role, "version": life.version, "machine": machine(), "host": socket.gethostname(),
            "package": list(package), "phase": "planned", "paths": chosen, "empty": empty,
            "retained": retained, "data": str(data), "config": config, "active": life.running(props),
            "enabled": enabled, "mask_owned": False}


def display(value):
    print("BITS " + value["role"] + " permanent uninstall on " + value["host"] + ":")
    print("  Package: bits-" + value["role"] + " " + value["package"][1])
    for item in value["paths"]:
        print("  Delete: " + item["path"])
    print("  Remove service registration; remove the standard bits account after the last role.")
    for path in value["retained"]:
        print("  Retain: " + path)
    print("Other roles, original sckocp, network settings and shared system logs are retained.", flush=True)


def clean_account():
    if installed("center") or installed("node"):
        return
    try:
        account = pwd.getpwnam("bits")
    except KeyError:
        account = None
    try:
        group = grp.getgrnam("bits")
    except KeyError:
        group = None
    if account:
        if (account.pw_uid == 0 or account.pw_dir != "/var/lib/bits/center" or
                account.pw_shell not in ("/usr/sbin/nologin", "/sbin/nologin") or
                not group or account.pw_gid != group.gr_gid or group.gr_mem):
            print("Retained customized bits system account.")
            return
        command("userdel", "bits")  # No --remove: only our planned paths are removed.
    if group and not group.gr_mem and not any(p.pw_gid == group.gr_gid for p in pwd.getpwall()):
        if any(g.gr_name == "bits" for g in grp.getgrall()):
            command("groupdel", "bits")


def save_helper(directory, role, version):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    private_path(directory, True)
    if directory.stat().st_uid or directory.stat().st_mode & 0o077:
        raise RuntimeError("uninstall recovery directory must be root-only")
    source = ("LIFECYCLE = " + repr(LIFECYCLE) + "\nUNINSTALL = " + repr(UNINSTALL) +
              "\nimport sys\nsys.argv[1:1] = " + repr([role, version, "--resume"]) + "\nexec(UNINSTALL)\n")
    target = directory / "resume.py"
    if os.path.lexists(str(target)):
        private_path(target)
        if target.read_text() != source:
            raise RuntimeError("recovery helper differs; use the saved helper to resume")
    else:
        with os.fdopen(os.open(str(target), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600), "w") as output:
            output.write(source)
            output.flush()
            os.fsync(output.fileno())
    for parent in (directory, ROOT, ROOT.parent):
        fd = os.open(str(parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def restore_service(life, value):
    life.unmask(value)
    if value["enabled"] in ("enabled", "enabled-runtime"):
        args = ["systemctl", "enable"] + (["--runtime"] if value["enabled"] == "enabled-runtime" else [])
        command(*(args + [life.unit]))
    life.restore(value)


def no_manual_process(life):
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if os.readlink(str(process / "exe")) not in (life.binary, life.binary + " (deleted)"):
                continue
            args = (process / "cmdline").read_bytes().split(b"\0")
            if len(args) > 1 and args[1] in (b"serve", b"agent"):
                raise RuntimeError("BITS service still has a process outside systemd; stop it before uninstalling")
        except (FileNotFoundError, ProcessLookupError):
            pass


def execute(life, value, directory):
    state_path = directory / "plan.json"
    life.state_path = state_path
    protected = protection(life.role, life.version)
    if value["phase"] in ("planned", "stopping"):
        for item in value["paths"]:
            path = Path(item["path"])
            check_path(path, protected, link=item.get("mask", False))
            if not os.path.lexists(str(path)) or fingerprint(path) != item["identity"]:
                raise RuntimeError("purge target changed; rerun the preview")
        try:
            value["phase"] = "stopping"
            write_json(state_path, value)
            life.stop_idle(life.properties(), Path(value["data"]), value)
            if life.properties().get("ActiveState") not in ("inactive", "failed"):
                raise RuntimeError("service has not stopped")
            no_manual_process(life)
            command("systemctl", "disable", life.unit)
        except BaseException:
            restore_service(life, value)
            raise
        value["phase"] = "removing-package"
        write_json(state_path, value)
    current = installed(life.role, value["package"][0])
    if current and list(current) != value["package"]:
        raise RuntimeError("installed package changed; retained for review")
    if current:
        try:
            if current[0] == "rpm":
                command("rpm", "-e", "bits-" + life.role, timeout=None)
            else:
                command("dpkg", "--purge", "bits-" + life.role, timeout=None)
        except BaseException:
            # No user data has been deleted. If the old package remains intact,
            # recover its original running/enabled state before returning.
            if installed(life.role, value["package"][0]) == current and Path(life.binary).exists():
                restore_service(life, value)
            raise
    if installed(life.role, value["package"][0]):
        raise RuntimeError("package removal is incomplete; data retained")
    if Path(life.binary).exists():
        raise RuntimeError("BITS binary still exists after package removal; data retained")
    no_manual_process(life)
    value["phase"] = "purging-data"
    write_json(state_path, value)
    for item in value["paths"]:
        remove_path(item, protected)
    life.unmask(value)
    subprocess.run(["systemctl", "reset-failed", life.unit], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
    # Package-owned parent directories can be shared with the other role.
    parents = value["empty"] + ["/opt/bits/native/" + life.version, "/opt/bits/native",
                               "/opt/bits/workloads", "/opt/bits"]
    prune(parents)
    clean_account()
    value["phase"] = "done"
    write_json(state_path, value)


def uninstall_main():
    os.umask(0o077)
    os.environ["PATH"] = "/usr/sbin:/usr/bin:/sbin:/bin"
    role, version = sys.argv[1:3]
    if role not in ("center", "node") or os.geteuid() != 0:
        raise RuntimeError("uninstall requires root and an installed BITS role")
    parser = argparse.ArgumentParser(prog="bits-" + role + " uninstall")
    parser.add_argument("--purge", action="store_true", help="permanently remove package, configuration, data and automatic backups")
    parser.add_argument("--dry-run", action="store_true", help="print the removal plan without changes")
    parser.add_argument("--yes", action="store_true", help="confirm the displayed permanent removal without prompting")
    parser.add_argument("--include", action="append", default=[], metavar="PATH", help="also delete this exact bits-" + role + " installation copy or backup")
    parser.add_argument("--resume", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(sys.argv[3:])
    if not args.purge and not args.resume:
        parser.error("--purge is required; use --dry-run to preview")
    directory = ROOT / role
    state_path = directory / "plan.json"
    life = Lifecycle(role, version, read_only=True)
    value = read_json(state_path) if state_path.exists() else plan(life, args.include)
    if value["role"] != role or value["version"] != version or value["machine"] != machine():
        raise RuntimeError("uninstall plan belongs to another role, version or host")
    if state_path.exists() and args.include:
        raise RuntimeError("resume uses the already confirmed paths; omit --include")
    display(value)
    if args.dry_run:
        return
    if not args.yes:
        if not sys.stdin.isatty():
            raise RuntimeError("confirmation requires a terminal; use --yes for automation")
        expected = "PURGE " + socket.gethostname() + " " + role
        if input("Type '" + expected + "' to permanently delete these files: ") != expected:
            print("Cancelled; no changes.")
            return
    # Both role locks serialize this with other purges and package upgrades.
    guards = [Lifecycle(r, version) for r in ("center", "node")]
    life = guards[0 if role == "center" else 1]
    if life.state_path.exists():
        raise RuntimeError("finish the pending package installation first")
    if not state_path.exists():
        # Rediscover after confirmation so configuration changes cannot expand
        # the set of confirmed targets or silently redirect the data directory.
        fresh = plan(life, args.include)
        if fresh != value:
            raise RuntimeError("uninstall plan changed; preview and confirm again")
    save_helper(directory, role, version)
    write_json(state_path, value)
    try:
        execute(life, value, directory)
    except BaseException:
        if state_path.exists():
            print("Resume: python3 " + str(directory / "resume.py"), file=sys.stderr)
        else:
            (directory / "resume.py").unlink()
            prune([directory, ROOT])
        raise
    for filename in ("plan.json", "resume.py"):
        (directory / filename).unlink()
    # Remove only the selected role's empty maintenance state. A remaining peer
    # retains its own lock and deployment, including the shared account.
    (life.root / (role + ".lock")).unlink()
    other = "node" if role == "center" else "center"
    if not installed(other) and not (life.root / (other + ".json")).exists():
        (life.root / (other + ".lock")).unlink()
    prune([directory, ROOT, life.root])
    print("BITS " + role + " package, configured data and selected backups removed.")


if __name__ == "__main__":
    try:
        uninstall_main()
    except (Exception, KeyboardInterrupt) as error:
        print("BITS uninstall: " + str(error), file=sys.stderr)
        sys.exit(1)
