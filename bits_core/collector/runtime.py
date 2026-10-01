"""Linux supervision and per-application locking for both monitoring backends.

The bridge calls ``--check APP`` before dispatch. Exit 3 requests a new
``--run APP -- COMMAND ...`` supervisor; exit 0 proves an inherited lock.
The guard is an open file description, never a trusted environment flag.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

# Invoked by absolute filename with Python -I as well as imported by the entry.
# Only this module's package root may supply the manifest-covered layout.
sys.path.insert(0, str(Path(__file__).absolute().parents[2]))
from bits_layout import LAYOUT
DISPLAY_NAME = "bits-collector" if LAYOUT.native else "mon-sensors-plugin"
LOCK_NAME = LAYOUT.runtime_lock
FD_ENV = LAYOUT.runtime_fd
NEEDS_SUPERVISOR = 3
ALREADY_RUNNING = 75
STOP_GRACE = 5.0


def _application(app):
    path = Path(app)
    if not path.is_absolute() or path == Path("/") or ".." in path.parts:
        raise ValueError("Application must be an existing absolute non-root directory")
    for directory in list(reversed(path.parents)) + [path]:
        details = directory.lstat()
        sticky_root = details.st_uid == 0 and details.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(details.st_mode) or
                details.st_uid not in (0, os.geteuid()) or
                (details.st_mode & 0o022 and not sticky_root)):
            raise ValueError("Unsafe application directory")
    return str(path)


def _details(fd):
    details = os.fstat(fd)
    if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or
            details.st_uid not in (0, os.geteuid()) or
            stat.S_IMODE(details.st_mode) != 0o600):
        raise ValueError("Runtime lock must be a private regular single-link file")
    return details


def _inode(details):
    return details.st_dev, details.st_ino


def _open_lock(app, create=False):
    flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
    if create:
        flags |= os.O_CREAT
    fd = os.open(os.path.join(app, LOCK_NAME), flags, 0o600)
    try:
        _details(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


def _installation_guard(app):
    path = os.path.join(app, LAYOUT.install_lock)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except FileNotFoundError:
        return None  # Source-only runs have no installed bridge to protect.
    try:
        details = _details(fd)
        if _inode(os.lstat(path)) != _inode(details):
            raise ValueError("Installation lock was replaced")
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError("Application installation is in progress; monitoring was not started")
        return fd
    except BaseException:
        os.close(fd)
        raise


def guard_valid(app, environ=None):
    """Verify the inherited FD owns this application's flock.

    A separately opened descriptor must conflict while flock on the inherited
    descriptor succeeds. This distinguishes the shared locked description from
    a descriptor which merely references the same inode. No lock is released.
    """
    app = _application(app)
    environ = os.environ if environ is None else environ
    value = environ.get(FD_ENV, "")
    if not value.isdigit() or not 3 <= int(value) <= 1048575:
        return False
    fd = int(value)
    probe = None
    try:
        details = _details(fd)
        probe = _open_lock(app)
        if _inode(details) != _inode(_details(probe)):
            return False
        # Prove a lock already exists before touching the inherited descriptor.
        # Otherwise an arbitrary unlocked FD for this inode could acquire its
        # own lock here and incorrectly pass without a supervising process.
        try:
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Only a description sharing ownership of the existing lock can
            # succeed; a separate FD for the same inode still conflicts.
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        fcntl.flock(probe, fcntl.LOCK_UN)
        return False
    except (OSError, ValueError):
        return False
    finally:
        if probe is not None:
            os.close(probe)


def _process_snapshot(pid):
    try:
        with open("/proc/{}/stat".format(pid), "r") as stream:
            fields = stream.read(8192).rsplit(")", 1)[1].split()
        with open("/proc/{}/cmdline".format(pid), "rb") as stream:
            command = stream.read(65537)
        if (len(fields) < 20 or not fields[19].isdigit() or not command or
                len(command) > 65536 or not command.endswith(b"\0")):
            return None
        return fields[19], command
    except (OSError, IndexError):
        return None


def _owner_record(fd, app):
    snapshot = _process_snapshot(os.getpid())
    if snapshot is None:
        raise ValueError("Cannot determine supervisor process identity")
    record = {"version": 1, "app": app, "pid": os.getpid(),
              "start": snapshot[0], "fd": fd}
    data = json.dumps(record, sort_keys=True).encode("ascii") + b"\n"
    os.ftruncate(fd, 0)
    os.lseek(fd, 0, os.SEEK_SET)
    offset = 0
    while offset < len(data):
        offset += os.write(fd, data[offset:])
    os.fsync(fd)


def _signal_group(child, signum):
    # start_new_session=True makes the child its own process-group leader.
    try:
        os.killpg(child.pid, signum)
    except ProcessLookupError:
        pass


def _group_exists(child):
    try:
        os.killpg(child.pid, 0)
        return True
    except ProcessLookupError:
        return False


def supervise(app, command):
    """Own the application lock until the launched monitoring group has ended."""
    app = _application(app)
    if not command or not os.path.isabs(command[0]):
        raise ValueError("Supervisor requires an absolute executable path")
    fd = _open_lock(app, create=True)
    installation_fd = None
    child = None
    previous = {}
    stopped = [None]

    def request_stop(signum, frame):
        # The handler does no I/O and cannot interrupt child creation/cleanup.
        if stopped[0] is None:
            stopped[0] = signum

    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print(DISPLAY_NAME + ": monitoring already runs for this application", file=sys.stderr)
            return ALREADY_RUNNING
        # Lock order is runtime EX then installation SH. The installer holds
        # installation EX and refuses active collectors, so neither can cross
        # the other's final idle/start decision. This acquisition never waits.
        installation_fd = _installation_guard(app)
        for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
            previous[signum] = signal.signal(signum, request_stop)
        _owner_record(fd, app)
        environ = os.environ.copy()
        environ[FD_ENV] = str(fd)
        inherited = (fd,) if installation_fd is None else (fd, installation_fd)
        child = subprocess.Popen(command, env=environ, pass_fds=inherited,
                                 start_new_session=True)
        while stopped[0] is None and child.poll() is None:
            time.sleep(0.05)
        if stopped[0] is not None:
            _signal_group(child, signal.SIGTERM)
        # A shell can exit before its hardware-reading child. Keep supervision
        # and the lock until the whole launched group has received cleanup.
        if child.poll() is not None and _group_exists(child):
            _signal_group(child, signal.SIGTERM)
        deadline = time.monotonic() + STOP_GRACE
        while _group_exists(child) and time.monotonic() < deadline:
            child.poll()
            time.sleep(0.05)
        if _group_exists(child):
            _signal_group(child, signal.SIGKILL)
        returncode = child.wait()
        if stopped[0] is not None:
            # The collector handles TERM itself and closes provider/log state.
            # Preserve its successful graceful-stop status through supervision;
            # only a terminated/killed backend reports the supervisor signal.
            if returncode == 0:
                return 0
            return 128 + stopped[0]
        return returncode if returncode >= 0 else 128 - returncode
    finally:
        if child is not None and child.poll() is None:
            _signal_group(child, signal.SIGTERM)
            try:
                child.wait(timeout=STOP_GRACE)
            except subprocess.TimeoutExpired:
                _signal_group(child, signal.SIGKILL)
                child.wait()
        # Do not unlink the lock file: unlinking could split a live lock into
        # multiple inodes. Stale owner metadata is ignored whenever flock is free.
        os.close(fd)
        if installation_fd is not None:
            os.close(installation_fd)
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _runtime_argv(command, app):
    argv = [os.fsdecode(part) for part in command.split(b"\0")[:-1]]
    # Retain recognition of older launchers while new launchers disable site.
    # Accepted: python [-I] [-S] [-B] /absolute/runtime.py --run APP,
    # or python [-I] [-S] [-B] -m bits_core.collector.runtime --run APP.
    cursor = 1
    while cursor < len(argv) and argv[cursor] in ("-I", "-S", "-B"):
        cursor += 1
    expected_mode = "--run"
    if argv[cursor:cursor + 2] == ["-m", "bits_core.collector.runtime"]:
        cursor += 2
    elif cursor < len(argv) and os.path.isabs(argv[cursor]) and (
            os.path.realpath(argv[cursor]) == os.path.realpath(__file__)):
        cursor += 1
    elif cursor < len(argv) and os.path.isabs(argv[cursor]) and (
            os.path.realpath(argv[cursor]) == os.path.realpath(os.path.join(
                str(Path(__file__).absolute().parents[2]), LAYOUT.collector_program))):
        cursor += 1
        expected_mode = "--guard-run"
    else:
        return False
    return (len(argv) >= cursor + 3 and argv[cursor] == expected_mode and
            argv[cursor + 2] == "--" and
            os.path.normpath(argv[cursor + 1]) == app)


def _verified_owner(fd, app):
    try:
        data = os.pread(fd, 4097, 0)
        if len(data) > 4096:
            raise ValueError("Oversized runtime lock owner record")
        record = json.loads(data.decode("ascii"))
        if (not isinstance(record, dict) or record.get("version") != 1 or
                record.get("app") != app or type(record.get("pid")) is not int or
                record["pid"] <= 1 or record["pid"] == os.getpid() or
                type(record.get("fd")) is not int or not 3 <= record["fd"] <= 1048575 or
                not isinstance(record.get("start"), str)):
            raise ValueError("Invalid runtime lock owner record")
        pid = record["pid"]
        snapshot = _process_snapshot(pid)
        if (snapshot is None or snapshot[0] != record["start"] or
                not _runtime_argv(snapshot[1], app)):
            raise ValueError("Runtime owner identity cannot be verified")
        owner_fd = os.stat("/proc/{}/fd/{}".format(pid, record["fd"]))
        if _inode(owner_fd) != _inode(_details(fd)):
            raise ValueError("Runtime owner no longer references the lock")
        return pid, snapshot
    except (UnicodeError, TypeError, KeyError):
        raise ValueError("Invalid runtime lock owner record")


def stop_runtime(app, timeout=7.0):
    """Stop the verified supervisor for APP and wait for its lock to release.

    Return False if no supervisor holds the lock; return True on a completed
    stop. Refuse unknown lock holders rather than signalling guessed PIDs.
    This does not stop pre-plugin legacy processes; callers may separately use
    their existing exact-application legacy process matching for that purpose.
    """
    app = _application(app)
    try:
        fd = _open_lock(app)
    except FileNotFoundError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return False
        except BlockingIOError:
            pass
        # Verify process start time, argv and the exact inherited inode twice.
        pid, snapshot = _verified_owner(fd, app)
        if _verified_owner(fd, app) != (pid, snapshot):
            raise ValueError("Runtime owner changed while stopping")
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise ValueError("Monitoring stop did not release its runtime lock")
                time.sleep(0.05)
    finally:
        os.close(fd)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", metavar="APP")
    mode.add_argument("--run", metavar="APP")
    mode.add_argument("--stop", metavar="APP")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    try:
        if args.check is not None:
            if args.command:
                parser.error("--check accepts only the application path")
            return 0 if guard_valid(args.check) else NEEDS_SUPERVISOR
        if args.stop is not None:
            if args.command:
                parser.error("--stop accepts only the application path")
            stop_runtime(args.stop)
            return 0
        command = args.command[1:] if args.command[:1] == ["--"] else args.command
        return supervise(args.run, command)
    except (OSError, ValueError) as error:
        print(DISPLAY_NAME + ": {}".format(error), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
