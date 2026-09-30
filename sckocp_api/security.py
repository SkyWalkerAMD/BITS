"""Pin a trusted Linux executable without following unchecked path components.

This is a filesystem trust boundary, not a signature or licence verifier. A
caller with the same effective UID (or root) can still replace its own code.
Descriptors protect against pathname replacement; they do not freeze writes by
an already trusted owner. The native program remains responsible for licensing.
"""
from collections import deque
from contextlib import contextmanager
import errno
import os
import stat


class UnsafeExecutable(ValueError):
    """The executable or a traversed path component is not trusted."""


def _deny():
    # Never include a configured path or other machine details in public errors.
    raise UnsafeExecutable("The configured executable path is not trusted.")


def _trusted_owner(info, effective_uid):
    if info.st_uid not in (0, effective_uid):
        _deny()


def _directory(info, effective_uid):
    _trusted_owner(info, effective_uid)
    if not stat.S_ISDIR(info.st_mode):
        _deny()
    if info.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        # Sticky root-owned /tmp protects entries owned by root or this caller.
        # Every child is checked as well; an attacker-owned child is rejected.
        if info.st_uid != 0 or not info.st_mode & stat.S_ISVTX:
            _deny()


def _executable(info, effective_uid):
    _trusted_owner(info, effective_uid)
    if not stat.S_ISREG(info.st_mode) or info.st_mode & (
            stat.S_IWGRP | stat.S_IWOTH | stat.S_ISUID | stat.S_ISGID):
        _deny()
    if not info.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
        raise PermissionError(errno.EACCES, "The executable cannot be executed.")


def _open_executable(binary):
    if not isinstance(binary, str) or not os.path.isabs(binary) or "\0" in binary:
        _deny()
    if not hasattr(os, "O_PATH"):
        raise OSError(errno.ENOTSUP, "Pinned executable access requires Linux.")
    effective_uid = os.geteuid()
    flags = os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC
    directories = []
    try:
        root = os.open("/", flags | os.O_DIRECTORY)
        directories.append(root)
        _directory(os.fstat(root), effective_uid)
        pending = deque(binary.split("/"))
        symlinks = 0
        while pending:
            part = pending.popleft()
            if part in ("", "."):
                continue
            if part == "..":
                if len(directories) > 1:
                    os.close(directories.pop())
                continue
            # O_PATH never opens a device for I/O and cannot block on a FIFO.
            descriptor = os.open(part, flags, dir_fd=directories[-1])
            try:
                info = os.fstat(descriptor)
                if stat.S_ISLNK(info.st_mode):
                    _trusted_owner(info, effective_uid)
                    symlinks += 1
                    if symlinks > 40:
                        _deny()
                    # Linux readlinkat(AT_EMPTY_PATH) reads this pinned link,
                    # even if the directory entry is replaced concurrently.
                    target = os.readlink("", dir_fd=descriptor)
                    if target.startswith("/"):
                        while len(directories) > 1:
                            os.close(directories.pop())
                    pending.extendleft(reversed(target.split("/")))
                elif pending:
                    _directory(info, effective_uid)
                    directories.append(descriptor)
                    descriptor = None
                else:
                    _executable(info, effective_uid)
                    result = descriptor
                    descriptor = None
                    return result
            finally:
                if descriptor is not None:
                    os.close(descriptor)
        _deny()
    finally:
        for descriptor in reversed(directories):
            os.close(descriptor)


@contextmanager
def trusted_executable(binary):
    """Yield ``(fd, executable_path)`` for a validated, pinned executable.

    Use ``executable=executable_path`` and ``pass_fds=(fd,)`` in Popen. Keep this
    context alive until Popen has started the child. Inherited access also lets
    the interpreter open a trusted script through /proc/self/fd. Callers must
    supply an explicit safe environment and working directory separately.

    Root trusts root-owned components only. Other users trust root and their
    own effective UID. Group/world writable files and parents are rejected,
    except root-owned sticky directories. Symlinks are allowed only when their
    owner, their containing path, and the complete target path are trusted.
    Setuid/setgid executables are not supported; no privilege is added here.
    """
    descriptor = _open_executable(binary)
    try:
        yield descriptor, "/proc/self/fd/" + str(descriptor)
    finally:
        os.close(descriptor)
