"""Install only the independent reader; never source or patch the old OCRUN."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile

VERSION = "0.2.0"
PREFIX = Path("/opt/mon-sensors-control")
DEST = PREFIX / VERSION
COMMAND = Path("/usr/local/bin/mon-sensors-control")
SOURCE = Path(__file__).absolute().parent
PREVIOUS_FILES = {
    'CONTROL-MANUAL.md': 'fe4523c00c7980e18d4a0b7eaf1bafcb5816f9553bc176c86a92ff2a24b8d616',
    'control_addon/__init__.py': '75c596fb0b7a210667ed885cddaa3eecb16653986a166affab997cab22e19d0e',
    'control_addon/baseline.json': 'b44619c91594a8b7268ce939d03270001c0ac0bc9940b51321397b69f12e5253',
    'control_addon/control.py': '507eb24b8d221b7fd1442623f9758a55798a4f0413141e128d798e7ace8b1a9c',
    'control_addon/data.py': '5e99f3311da1dd0e3862d61373af3ae24404f576ccb3e57d6f6d020b3cbf1899'}


def directory(path):
    path = Path(path).absolute()
    for item in list(reversed(path.parents)) + [path]:
        info = item.lstat()
        sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or (info.st_mode & 0o022 and not sticky):
            raise ValueError("Untrusted program directory: " + str(item))
    return path


def read(path, system=False):
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o6022 or
                (not system and info.st_nlink != 1)):
            raise ValueError("Untrusted program file: " + str(path))
        if system:
            if not info.st_mode & stat.S_IXUSR:
                raise ValueError("Python is not executable")
            return None  # System Python hard links are allowed; never write it.
        with os.fdopen(os.dup(fd), "rb") as stream:
            value = stream.read(2 * 1024 ** 2 + 1)
        if len(value) > 2 * 1024 ** 2:
            raise ValueError("Unexpectedly large program file")
        return value
    finally:
        os.close(fd)


def payload(root, version=VERSION):
    directory(root)
    manifest = json.loads(read(root / "PACKAGE.json").decode("utf-8"))
    expected = {"control_addon/__init__.py", "control_addon/control.py", "control_addon/data.py",
                "control_addon/baseline.json", "CONTROL-MANUAL.md"}
    if manifest.get("version") != version or set(manifest.get("files", {})) != expected:
        raise ValueError("Unknown control plugin package")
    if version == '0.1.0' and manifest['files'] != PREVIOUS_FILES:
        raise ValueError('Previous package differs from the published 0.1.0 inventory')
    actual = set()
    for base, dirs, files in os.walk(str(root), followlinks=False):
        directory(Path(base))
        for name in dirs:
            child = Path(base) / name
            if child.relative_to(root).as_posix() != "control_addon":
                raise ValueError("Unexpected program directory: " + str(child))
            directory(child)
        for name in files:
            path = Path(base) / name
            relative = path.relative_to(root).as_posix()
            if relative == "PACKAGE.json":
                continue
            actual.add(relative)
            data = read(path)
            if hashlib.sha256(data).hexdigest() != manifest["files"].get(relative):
                raise ValueError("Program differs from package: " + relative)
    if actual != expected:
        raise ValueError("Missing or unexpected plugin program files")
    return manifest


def launcher(version=VERSION):
    # Only system package locations; no PATH/Conda/venv interpreter selection.
    python = Path(os.path.realpath(sys.executable))
    if not str(python).startswith(("/usr/bin/", "/usr/libexec/")):
        raise ValueError("Use the distribution Python, not a user or Conda Python")
    read(python, system=True)
    return ("#!/bin/sh\n# mon-sensors-control " + version + " managed entry\nexec " + str(python) +
            " -I -B -c 'import runpy,sys;sys.path.insert(0,\"" + str(PREFIX / version) +
            "\");runpy.run_module(\"control_addon.control\",run_name=\"__main__\")' \"$@\"\n").encode("utf-8")


def write_new(path, data, mode=0o644):
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    try:
        with os.fdopen(os.dup(fd), "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    finally:
        os.close(fd)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--check", action="store_true")
    action.add_argument("--apply", action="store_true")
    action.add_argument("--remove", action="store_true")
    action.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0 or sys.platform != "linux" or sys.version_info < (3, 6):
        parser.error("Use root on Linux with the system Python 3.6+")
    spec = payload(SOURCE / "payload")
    desired = launcher('0.1.0' if args.rollback else VERSION)
    directory(COMMAND.parent)
    directory(PREFIX if PREFIX.exists() or PREFIX.is_symlink() else PREFIX.parent)
    if PREFIX.exists() and any(p.name not in (VERSION, '0.1.0') for p in PREFIX.iterdir()):
        raise ValueError("Unknown entries in the plugin prefix; preserve them and inspect")
    old = PREFIX / '0.1.0'
    old_exists = old.exists() or old.is_symlink()
    if old_exists:
        payload(old, '0.1.0')
    if args.rollback and not old_exists:
        raise ValueError('No validated 0.1.0 payload for rollback')
    exists = DEST.exists() or DEST.is_symlink()
    if exists and payload(DEST) != spec:
        raise ValueError("Existing plugin is from another source version; preserve it")
    previous = read(COMMAND) if COMMAND.exists() or COMMAND.is_symlink() else None
    if previous is not None and previous not in (launcher(), launcher('0.1.0')):
        raise ValueError("Existing command is not this managed launcher; no overwrite")
    if previous == launcher() and not exists or previous == launcher('0.1.0') and not old_exists:
        raise ValueError("Command exists without its matching payload")
    result = {"version": VERSION, "action": "check" if args.check else "remove" if args.remove else "rollback" if args.rollback else "install",
              "original_ocrun_modified": False, "services_started": False,
              "prefix": str(DEST), "command": str(COMMAND), "source_commit": spec["source_commit"]}
    if args.check:
        print(json.dumps(result, sort_keys=True))
        return
    # Serialize separate installers without adding a lock file to the old app.
    lock = os.open(str(COMMAND.parent), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        current = read(COMMAND) if COMMAND.exists() or COMMAND.is_symlink() else None
        if current != previous or (DEST.exists() or DEST.is_symlink()) != exists:
            raise ValueError("Installation changed concurrently; rerun the preflight")
        if exists:
            payload(DEST)
        if old_exists:
            payload(old, '0.1.0')
        if args.remove:
            if previous is not None:
                COMMAND.unlink()
            if exists:
                # Only the fully validated fixed plugin tree is removed.
                shutil.rmtree(str(DEST))
            if old_exists:
                shutil.rmtree(str(old))
            if PREFIX.exists():
                PREFIX.rmdir()
        else:
            if not exists and not args.rollback:
                PREFIX.mkdir(mode=0o755, exist_ok=True)
                directory(PREFIX)
                stage = Path(tempfile.mkdtemp(prefix=".install-", dir=str(PREFIX)))
                try:
                    (stage / "control_addon").mkdir(mode=0o755)
                    for name in sorted(spec["files"]):
                        write_new(stage / name, read(SOURCE / "payload" / name))
                    write_new(stage / "PACKAGE.json", read(SOURCE / "payload/PACKAGE.json"))
                    stage.chmod(0o755)
                    payload(stage)
                    os.rename(str(stage), str(DEST))
                finally:
                    if stage.exists():
                        shutil.rmtree(str(stage))
            if previous != desired:
                # Hard-link publication is atomic and cannot overwrite an entry
                # created concurrently. Remove the temporary name immediately.
                fd, name = tempfile.mkstemp(prefix=".mon-control-", dir=str(COMMAND.parent))
                try:
                    os.fchmod(fd, 0o755)
                    os.write(fd, desired)
                    os.fsync(fd)
                    if previous is None:
                        os.link(name, str(COMMAND))
                    else:
                        if read(COMMAND) != previous:
                            raise ValueError('Launcher changed during publication')
                        os.replace(name, str(COMMAND))
                finally:
                    os.close(fd)
                    if os.path.exists(name):
                        os.unlink(name)
        print(json.dumps(result, sort_keys=True))
    finally:
        os.close(lock)


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError) as exc:
        print("mon-sensors-control install: " + str(exc), file=sys.stderr)
        sys.exit(2)
