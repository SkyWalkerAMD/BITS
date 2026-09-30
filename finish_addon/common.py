"""Private files and bounded child processes for the optional node finalizer."""
import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import selectors
import signal
import stat
import subprocess
import tempfile
import time

from security import trusted_executable

VERSION = "0.2.5"
HELPER = "mon-sensors-finish.d"
STATE = ".mon-sensors-finish"
ENTRY = "mon-sensors-finish"


def now():
    return datetime.datetime.utcnow().isoformat(timespec="seconds") + "Z"


def directory(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("An absolute path without '..' is required")
    for item in list(reversed(path.parents)) + [path]:
        info = item.lstat()
        sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or
                (info.st_mode & 0o022 and not sticky)):
            raise ValueError("Untrusted directory: " + str(item))
    return path


def regular(info, path, private=False):
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
            info.st_uid != os.geteuid() or info.st_mode & 0o7022 or
            (private and stat.S_IMODE(info.st_mode) != 0o600)):
        raise ValueError("Untrusted regular file: " + str(path))


@contextlib.contextmanager
def opened(path, locked=False):
    path = Path(path)
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        regular(os.fstat(fd), path)
        if locked:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            yield stream
    finally:
        os.close(fd)


def read(path, limit=4 * 1024 * 1024):
    with opened(path) as stream:
        data = stream.read(limit + 1)
    if len(data) > limit:
        raise ValueError("File exceeds size limit: " + str(path))
    return data


def json_read(path):
    return json.loads(read(path).decode("utf-8"))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def snapshot(path):
    info = Path(path).lstat()
    regular(info, path)
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def file_hash(path, limit=16 * 1024 ** 3):
    before = snapshot(path)
    total, checksum = 0, hashlib.sha256()
    with opened(path) as stream:
        if (os.fstat(stream.fileno()).st_dev, os.fstat(stream.fileno()).st_ino) != before[:2]:
            raise ValueError("File changed while opening")
        while True:
            block = stream.read(1024 * 1024)
            if not block:
                break
            total += len(block)
            if total > limit:
                raise ValueError("File exceeds size limit: " + str(path))
            checksum.update(block)
    if snapshot(path) != before:
        raise ValueError("File changed while hashing: " + str(path))
    return {"sha256": checksum.hexdigest(), "bytes": total}


def atomic(path, data, replace=True):
    path = Path(path)
    directory(path.parent)
    try:
        regular(path.lstat(), path)
    except FileNotFoundError:
        pass
    fd, name = tempfile.mkstemp(prefix=".finish-write-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(name, str(path))
        else:
            os.link(name, str(path))
            os.unlink(name)
        parent = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def save(path, value):
    atomic(path, (json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


@contextlib.contextmanager
def lock(path):
    path = Path(path)
    directory(path.parent)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        regular(os.fstat(fd), path, private=True)
        if snapshot(path)[:2] != (os.fstat(fd).st_dev, os.fstat(fd).st_ino):
            raise ValueError("Lock path changed")
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield fd
    finally:
        os.close(fd)


def run(argv, timeout=60, redis_password=None):
    """No shell, isolated environment, pinned executable, capped output and group cleanup."""
    environment = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                   "HOME": "/root", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}
    if redis_password is not None:
        import re
        if Path(argv[0]).name not in ('redis-cli', 'valkey-cli') or not re.fullmatch(r'[0-9a-f]{64}', redis_password):
            raise ValueError('Invalid scoped database credentials')
        environment['REDISCLI_AUTH'] = redis_password
    child = None
    output = [bytearray(), bytearray()]
    selector = selectors.DefaultSelector()
    try:
        with trusted_executable(str(argv[0])) as (fd, executable):
            child = subprocess.Popen([str(arg) for arg in argv], executable=executable,
                                     pass_fds=(fd,), env=environment, cwd="/", stdin=subprocess.DEVNULL,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
        selector.register(child.stdout, selectors.EVENT_READ, 0)
        selector.register(child.stderr, selectors.EVENT_READ, 1)
        deadline = time.monotonic() + timeout
        while selector.get_map():
            if time.monotonic() >= deadline:
                raise ValueError("Command timed out: " + Path(argv[0]).name)
            for key, unused in selector.select(min(.1, max(0, deadline - time.monotonic()))):
                block = os.read(key.fileobj.fileno(), 65536)
                if not block:
                    selector.unregister(key.fileobj)
                else:
                    output[key.data].extend(block)
                    if len(output[key.data]) > 1024 * 1024:
                        raise ValueError("Command output exceeded limit")
        code = child.wait(timeout=max(.01, deadline - time.monotonic()))
        if code:
            # Do not copy arbitrary child diagnostics or configuration into state files.
            raise ValueError("{} exited with code {}".format(Path(argv[0]).name, code))
        return bytes(output[0])
    finally:
        selector.close()
        if child is not None:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            child.stdout.close()
            child.stderr.close()
