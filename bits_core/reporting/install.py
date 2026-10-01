"""Install private report dependencies and optionally bridge an existing OCRUN."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import platform
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile

SOURCE = Path(__file__).absolute().parent
sys.path.insert(0, str(SOURCE / "payload"))
from common import COMMAND, PREFIX, VERSION, atomic, digest, directory, interpreter_path, manifest, read, regular

BRIDGE = ("#!/bin/sh\n# Managed mon-sensors-report bridge v1.\nexec " +
          str(COMMAND) + ' "$@"\n').encode("utf-8")
BACKUP = ".mon-sensors-report-original.json"


def launcher():
    interpreter = interpreter_path(sys.executable)
    return ("#!/bin/sh\n# Managed mon-sensors-report launcher v1.\nexec " +
            shlex.quote(interpreter) + " -I -S -B " + shlex.quote(str(PREFIX / "report.py")) +
            ' "$@"\n').encode("utf-8")


def application(path, spec):
    app = directory(path)
    if app == Path("/"):
        raise ValueError("OCRUN application cannot be the root directory")
    analyzer = app / "py/mon-analyse-log.py"
    if digest(read(analyzer)) not in spec["compatible_analyser_sha256"]:
        raise ValueError("Unrecognized OCRUN analyser; refusing to replace its entrypoint")
    entry = app / "mon-analyse-log"
    info = entry.lstat()
    backup = app / BACKUP
    if stat.S_ISLNK(info.st_mode) and os.readlink(str(entry)) == "py/mon-analyse-log.py":
        original = {"target": "py/mon-analyse-log.py", "uid": info.st_uid, "gid": info.st_gid}
        if backup.exists() and json.loads(read(backup).decode("utf-8")) != original:
            raise ValueError("Existing report bridge backup does not match")
        return app, original, False
    if stat.S_ISREG(info.st_mode) and read(entry) == BRIDGE:
        original = json.loads(read(backup).decode("utf-8"))
        if (original.get("target") != "py/mon-analyse-log.py" or
                type(original.get("uid")) is not int or original["uid"] < 0 or
                type(original.get("gid")) is not int or original["gid"] < 0):
            raise ValueError("Invalid report bridge backup")
        return app, original, True
    raise ValueError("Unrecognized mon-analyse-log entrypoint")


def ensure_parent(path):
    missing = []
    cursor = path
    while not cursor.exists():
        if cursor.is_symlink():
            raise ValueError("Unexpected symlink in installation path")
        missing.append(cursor)
        cursor = cursor.parent
    directory(cursor)
    for item in reversed(missing):
        item.mkdir(mode=0o755)
    directory(path)


def run_check(root):
    result = subprocess.run([sys.executable, "-I", "-S", "-B", str(root / "report.py"), "--check"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30,
                            env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8"})
    if result.returncode:
        raise ValueError("Private dependency check failed: " + result.stderr.decode("utf-8", "replace")[:2000])
    return json.loads(result.stdout.decode("utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", help="Optionally connect the existing oct analyse entrypoint")
    parser.add_argument("--check", action="store_true", help="Read-only installation preflight")
    parser.add_argument("--detach", action="store_true", help="Restore the saved original report entrypoint")
    parser.add_argument('--rollback', action='store_true', help='Restore the prior managed report launcher')
    args = parser.parse_args()
    if args.detach and args.rollback:
        parser.error('--detach and --rollback are mutually exclusive')
    if (os.geteuid() != 0 or sys.platform != "linux" or platform.machine() != "x86_64" or
            sys.version_info[:2] != (3, 6)):
        parser.error("Requires root on Linux x86_64 with CPython 3.6")
    source = SOURCE / "payload"
    spec = manifest(source)
    desired = launcher()
    app = original = managed = None
    if args.app:
        app, original, managed = application(args.app, spec)
    if args.detach and app is None:
        parser.error("--detach requires --app")
    if PREFIX.exists() or PREFIX.is_symlink():
        if manifest(PREFIX) != spec:
            raise ValueError("Existing report installation differs; refusing to overwrite")
    else:
        parent = PREFIX.parent
        while not parent.exists():
            if parent.is_symlink():
                raise ValueError("Untrusted installation ancestor")
            parent = parent.parent
        directory(parent)
    directory(COMMAND.parent)
    previous = read(COMMAND) if COMMAND.exists() or COMMAND.is_symlink() else None
    old_launcher = desired.replace(b'/0.2.0/report.py', b'/0.1.1/report.py')
    if previous is not None and previous not in (desired, old_launcher):
        raise ValueError("Existing report command is not a recognized managed launcher")
    if previous == old_launcher:
        manifest(PREFIX.parent / '0.1.1', versions=('0.1.1',))
    checked = run_check(source)
    if args.rollback:
        backup = PREFIX.parent / 'previous-0.2.0.json'
        saved = json.loads(read(backup))
        old = saved['launcher'].encode('utf-8')
        if old != old_launcher or digest(old) != saved['sha256'] or previous != desired:
            raise ValueError('Rollback launcher identity mismatch')
        manifest(PREFIX.parent / '0.1.1', versions=('0.1.1',))
    result = {"action": "detach" if args.detach else "install", "check": args.check,
              "version": VERSION, "prefix": str(PREFIX), "command": str(COMMAND),
              "app": str(app) if app else None, "environment": checked}
    if args.check:
        print(json.dumps(result, sort_keys=True))
        return
    if args.rollback:
        backup = PREFIX.parent / 'previous-0.2.0.json'
        saved = json.loads(read(backup))
        old = saved['launcher'].encode('utf-8')
        if old != old_launcher or digest(old) != saved['sha256'] or previous != desired:
            raise ValueError('Rollback launcher identity mismatch')
        manifest(PREFIX.parent / '0.1.1', versions=('0.1.1',))
        atomic(COMMAND, old, mode=0o755)
        print(json.dumps(dict(result, action='rollback')))
        return
    if args.detach:
        if managed:
            fd, temporary = tempfile.mkstemp(prefix=".report-link-", dir=str(app))
            os.close(fd)
            os.unlink(temporary)
            try:
                os.symlink(original["target"], temporary)
                os.lchown(temporary, original["uid"], original["gid"])
                os.replace(temporary, str(app / "mon-analyse-log"))
            finally:
                if os.path.lexists(temporary):
                    os.unlink(temporary)
        print(json.dumps(result, sort_keys=True))
        return
    if not PREFIX.exists():
        ensure_parent(PREFIX.parent)
        stage = Path(tempfile.mkdtemp(prefix=".report-install-", dir=str(PREFIX.parent)))
        try:
            payload = stage / "payload"
            shutil.copytree(str(source), str(payload))
            for parent, directories, files in os.walk(str(payload)):
                os.chmod(parent, 0o755)
                for name in files:
                    os.chmod(os.path.join(parent, name), 0o644)
            manifest(payload)
            run_check(payload)
            os.rename(str(payload), str(PREFIX))
        finally:
            shutil.rmtree(str(stage))
    if previous != desired:
        if previous is not None:
            backup = PREFIX.parent / 'previous-0.2.0.json'
            saved = {'launcher': previous.decode('utf-8'), 'sha256': digest(previous)}
            if backup.exists() and json.loads(read(backup)) != saved:
                raise ValueError('Previous launcher backup differs')
            atomic(backup, json.dumps(saved).encode('utf-8'))
        if (read(COMMAND) if COMMAND.exists() else None) != previous:
            raise ValueError('Report command changed during installation')
        atomic(COMMAND, desired, mode=0o755, replace=previous is not None)
    if app is not None and not managed:
        backup = app / BACKUP
        if not backup.exists():
            atomic(backup, json.dumps(original, sort_keys=True).encode("utf-8") + b"\n", replace=False)
        # Recheck original entry and renderer immediately before publication.
        application(str(app), spec)
        atomic(app / "mon-analyse-log", BRIDGE, mode=0o755)
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    try:
        if '--check' in sys.argv:
            main()
        else:
            directory(COMMAND.parent)
            fd = os.open(str(COMMAND.parent / '.mon-sensors-report-install.lock'),
                         os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            try:
                regular(os.fstat(fd), private=True)
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                main()
            finally:
                os.close(fd)
    except (OSError, ValueError, subprocess.TimeoutExpired) as error:
        print("mon-sensors-report install: " + str(error), file=sys.stderr)
        sys.exit(1)
