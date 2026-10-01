"""Filesystem checks shared by the offline, read-only-data report add-on."""
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

VERSION = "0.2.0"
PREFIX = Path("/opt/mon-sensors-report") / VERSION
COMMAND = Path("/usr/local/bin/mon-sensors-report")


def directory(path):
    path = Path(os.path.abspath(str(path)))
    for part in list(reversed(path.parents)) + [path]:
        info = part.lstat()
        sticky = info.st_uid == 0 and info.st_mode & stat.S_ISVTX
        if (not stat.S_ISDIR(info.st_mode) or
                info.st_uid not in (0, os.geteuid()) or
                (info.st_mode & 0o022 and not sticky)):
            raise ValueError("Untrusted directory: " + str(part))
    return path


def regular(info, private=False, path=None):
    problems = []
    if not stat.S_ISREG(info.st_mode):
        problems.append("not a regular file")
    if info.st_nlink != 1:
        problems.append("data file must have exactly one hard link")
    if info.st_uid not in (0, os.geteuid()):
        problems.append("owner must be root or the current user")
    if info.st_mode & 0o6022:
        problems.append("group/world write or set-ID bits are forbidden")
    if private and stat.S_IMODE(info.st_mode) != 0o600:
        problems.append("private file must have mode 0600")
    if problems:
        raise ValueError("Untrusted file {}: {}; uid={} mode={:04o} links={}".format(
            path if path is not None else "(open descriptor)", "; ".join(problems),
            info.st_uid, stat.S_IMODE(info.st_mode), info.st_nlink))


def interpreter_path(path):
    """Pin a trusted executable; system-package hard links are permitted.

    Unlike data/output files, this inode is never written by the add-on.
    Every hard-link name shares the same root ownership and permissions.
    """
    path = Path(os.path.realpath(str(path)))
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or
                info.st_mode & 0o6022 or not info.st_mode & stat.S_IXUSR or
                info.st_nlink < 1):
            raise ValueError(
                "Untrusted Python executable {}: requires a root-owned regular "
                "executable without group/world write or set-ID bits; "
                "uid={} mode={:04o} links={}".format(
                    path, info.st_uid, stat.S_IMODE(info.st_mode), info.st_nlink))
    finally:
        os.close(fd)
    return str(path)


def read(path, limit=128 * 1024 * 1024):
    path = Path(path)
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        regular(os.fstat(fd), path=path)
        with os.fdopen(fd, "rb", closefd=False) as stream:
            data = stream.read(limit + 1)
        if len(data) > limit:
            raise ValueError("File exceeds the configured size limit: " + str(path))
        return data
    finally:
        os.close(fd)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def atomic(path, data, mode=0o600, replace=True):
    path = Path(path)
    directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix=".report-write-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode)
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, str(path))
        else:
            os.link(temporary, str(path))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def manifest(root, versions=None):
    root = directory(root)
    spec = json.loads(read(root / "MANIFEST.json", 2 * 1024 * 1024).decode("utf-8"))
    if spec.get("version") not in (versions or (VERSION,)) or not isinstance(spec.get("files"), dict):
        raise ValueError("Invalid report package manifest")
    actual = set()
    for parent, directories, files in os.walk(str(root), followlinks=False):
        directory(parent)
        for name in directories:
            directory(Path(parent) / name)
        for name in files:
            path = Path(parent) / name
            relative = str(path.relative_to(root))
            if relative == "MANIFEST.json":
                continue
            actual.add(relative)
            if spec["files"].get(relative) != digest(read(path)):
                raise ValueError("Report package file mismatch: " + relative)
    if actual != set(spec["files"]):
        raise ValueError("Report package file set does not match its manifest")
    return spec
