"""Read untrusted result files, never execute them or alter their permissions.

Root-owned program checks belong in the installer, not in this data reader.
The existing NFS results are commonly owned by ocuser and mode 0600.
"""
import contextlib
import hashlib
import json
import os
import re
import stat

COMPONENT = re.compile(r"[A-Za-z0-9_.-]{1,255}\Z")
MAX_FILE = 16 * 1024 ** 3
MAX_JSON = 1024 ** 2


def parts(value):
    if not isinstance(value, str) or value.startswith("/"):
        raise ValueError("Use a path relative to the configured results directory")
    result = value.split("/")
    if any(p in ("", ".", "..") or not COMPONENT.fullmatch(p) for p in result):
        raise ValueError("Invalid path component; traversal and special characters are forbidden")
    return result


def signature(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def no_duplicates(items):
    value = {}
    for key, item in items:
        if key in value:
            raise ValueError("Duplicate JSON key: " + key[:80])
        value[key] = item
    return value


def invalid_constant(value):
    raise ValueError("Non-finite JSON number")


def decode(data):
    try:
        return json.loads(data.decode("utf-8"), object_pairs_hook=no_duplicates,
                          parse_constant=invalid_constant)
    except (RecursionError, UnicodeError) as exc:
        raise ValueError("Invalid or excessively nested JSON") from exc


class Reader:
    def __init__(self, root):
        root = os.fspath(root)
        if not root.startswith("/") or ".." in root.split("/"):
            raise ValueError("The results directory must be an absolute path without '..'")
        self.root = root.rstrip("/")
        if not self.root:
            raise ValueError("Do not use the filesystem root as a results directory")
        # Walk with directory descriptors: intermediate links are refused too.
        fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            for item in self.root.split("/")[1:]:
                if not item or item == ".":
                    raise ValueError("Use a normalized absolute directory path")
                child = os.open(item, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=fd)
                os.close(fd)
                fd = child
            self.fd = fd
        except BaseException:
            os.close(fd)
            raise

    def __enter__(self):
        return self

    def __exit__(self, *unused):
        os.close(self.fd)

    @contextlib.contextmanager
    def directory(self, relative):
        fd = os.dup(self.fd)
        try:
            for item in parts(relative):
                child = os.open(item, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                                dir_fd=fd)
                os.close(fd)
                fd = child
            yield fd
        finally:
            os.close(fd)

    @contextlib.contextmanager
    def file(self, relative, stable=True, maximum=MAX_FILE):
        names = parts(relative)
        parent = self.directory("/".join(names[:-1])) if len(names) > 1 else same_descriptor(self.fd)
        with parent as parent_fd:
            pinned = os.open(names[-1], os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent_fd)
            fd = None
            try:
                before = os.fstat(pinned)
                if not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
                    raise ValueError("Expected a bounded regular data file: " + relative)
                fd = os.open("/proc/self/fd/" + str(pinned), os.O_RDONLY | os.O_CLOEXEC)
                yield fd, before
                if stable and (signature(os.fstat(fd)) != signature(before) or
                               signature(os.stat(names[-1], dir_fd=parent_fd, follow_symlinks=False)) != signature(before)):
                    raise ValueError("File changed during reading; retry after delivery: " + relative)
            finally:
                if fd is not None:
                    os.close(fd)
                os.close(pinned)

    def read(self, relative, maximum=MAX_JSON):
        with self.file(relative, maximum=maximum) as (fd, unused):
            with os.fdopen(os.dup(fd), "rb") as stream:
                data = stream.read(maximum + 1)
            if len(data) > maximum:
                raise ValueError("File exceeds the read limit")
            return data

    def digest(self, relative):
        with self.file(relative) as (fd, before):
            digest, count = hashlib.sha256(), 0
            while True:
                data = os.read(fd, 1024 ** 2)
                if not data:
                    break
                count += len(data)
                if count > before.st_size:
                    raise ValueError("File grew during verification: " + relative)
                digest.update(data)
            return {"bytes": count, "sha256": digest.hexdigest()}

    def tail(self, relative, limit=2 * 1024 ** 2):
        # A live file is a snapshot, not a verified sealed artifact. Ignore a
        # partially appended last line; never scan an entire multi-day log.
        with self.file(relative, stable=False) as (fd, before):
            offset = max(0, before.st_size - limit)
            os.lseek(fd, offset, os.SEEK_SET)
            with os.fdopen(os.dup(fd), "rb") as stream:
                data = stream.read(min(before.st_size, limit))
            if offset:
                data = data.partition(b"\n")[2]
            return data.split(b"\n")[:-1]


@contextlib.contextmanager
def same_descriptor(fd):
    yield fd
