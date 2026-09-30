"""Trusted configuration versus untrusted uploaded result data."""
import hashlib
import contextlib
import fcntl
import json
import os
from pathlib import Path
import stat
import tempfile


def directory(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("Expected an absolute normalized directory: " + str(path))
    for item in list(reversed(path.parents)) + [path]:
        info = item.lstat()
        sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or (info.st_mode & 0o022 and not sticky):
            raise ValueError("Untrusted root-owned directory: " + str(item))
    return path


def read(path, limit=8 * 1024 * 1024, executable=False):
    path = Path(path)
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o7022 or
                (not executable and info.st_nlink != 1)):
            raise ValueError("Untrusted file: " + str(path))
        # System executables may have distribution-owned hard links. Writable
        # state/configuration continues to require exactly one link.
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        after = os.fstat(fd)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")
        if len(data) > limit or any(getattr(info, k) != getattr(after, k) for k in fields):
            raise ValueError("Oversized or changing file: " + str(path))
        return data
    finally:
        os.close(fd)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, data, mode=0o600):
    path = Path(path)
    directory(path.parent)
    if path.exists() or path.is_symlink():
        read(path)
    fd, temporary = tempfile.mkstemp(prefix=".ocrun-server-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, str(path))
        descriptor = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path, value):
    write(path, (json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n").encode("utf-8"))


def load(path):
    return json.loads(read(path).decode("utf-8"))


def mkdir(path, mode=0o755):
    path = Path(path)
    if path.exists() or path.is_symlink():
        return directory(path)
    directory(path.parent)
    path.mkdir(mode=mode)
    path.chmod(mode)
    return directory(path)


@contextlib.contextmanager
def lock(path):
    path = Path(path)
    directory(path.parent)
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o077:
            raise ValueError('Untrusted operation lock: ' + str(path))
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)
