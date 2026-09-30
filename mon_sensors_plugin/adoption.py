"""Explicit adoption of byte-identified original OCRUN files, without executing them.

The default installer must retain its normal owner checks.  This helper is only
for a root operator explicitly selecting --adopt-original with an application
directory.  It never treats UID 201 as a generally trusted software publisher.
"""
import hashlib
import os
from pathlib import Path
import stat


SCRIPTS = ("mon-sensors", "oct", "ocb", "py/mon-analyse-log.py")
MAX_FILE_BYTES = 16 * 1024 * 1024
ORIGINAL_OWNER = (201, 200)
KNOWN_RELEASES = {
    "0730": {
        "mon-sensors": "b5edf8aa6968cf651361b239d766f6fc4d1e54b558873debe2d2f19fac14de0a",
        "oct": "15dbbcd950b1d4b2eec15639f63187a32f87714f10fbd9aaf9ada5f899331f99",
        "ocb": "be66b5178081f700534da8724fc0a00960338353c5a269046469bdb36b221e93",
        "py/mon-analyse-log.py": "f7cd72229232d9b30098e3a343997d0c302ede48d59cd46afa66490a94473d0f",
    },
    "0.9.24a": {
        "mon-sensors": "0b3d54dad2b0349e0f9a7532fac78880771b7f90eb3cdafd759ab5c5feac6b2b",
        "oct": "badee4536510e3da547440068c1b4ced0e970c63f1b5679df1d43c7c6e271c15",
        "ocb": "f5e9e73e84028f4b9afd7dcf6db93995ffb083f7cc4735f73e50ebd011f452e4",
        "py/mon-analyse-log.py": "f7cd72229232d9b30098e3a343997d0c302ede48d59cd46afa66490a94473d0f",
    },
}


def _release_for_hashes(hashes):
    for release, expected in KNOWN_RELEASES.items():
        if hashes == expected:
            return release
    raise ValueError("Original OCRUN adoption requires one complete known release; "
                     "modified, mixed or unknown files were found")


def _path(app):
    app = Path(app)
    if not app.is_absolute() or app == Path("/") or ".." in app.parts:
        raise ValueError("Original OCRUN adoption needs an absolute application path without '..'")
    return app


def _directory_key(details):
    return (details.st_dev, details.st_ino, details.st_mode,
            details.st_uid, details.st_gid)


def _file_key(details):
    return (_directory_key(details), details.st_nlink, details.st_size,
            details.st_mtime_ns, details.st_ctime_ns)


def _allowed_owner(details):
    return details.st_uid == 0 or (details.st_uid, details.st_gid) == ORIGINAL_OWNER


def _read(fd):
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    size = 0
    while size <= MAX_FILE_BYTES:
        chunk = os.read(fd, min(65536, MAX_FILE_BYTES + 1 - size))
        if not chunk:
            break
        size += len(chunk)
        chunks.append(chunk)
    if size > MAX_FILE_BYTES:
        raise ValueError("Original OCRUN adoption file is too large")
    return b"".join(chunks)


class OriginalAdoption:
    """Pinned read-only snapshot followed by optional directory ownership change.

    Use prepare() inside a context manager.  Backups must be made from originals,
    not by re-reading paths owned by another user.  apply() verifies the snapshot
    and takes ownership of only the py directory.  The installer then replaces
    the four target files atomically and records metadata for its own rollback.
    Call rollback() after restoring target files if the installer transaction
    fails.  Exiting the context only closes descriptors; it does not roll back a
    successful installation.
    """

    @classmethod
    def prepare(cls, app):
        if os.geteuid() != 0:
            raise PermissionError("--adopt-original requires root")
        item = cls()
        item.app = _path(app)
        item.originals = {}
        item.metadata = {}
        item._fds = []
        item._directories = []
        item._files = {}
        item._applied = False
        item._changed_py = False
        try:
            item._prepare()
            return item
        except BaseException:
            item.close()
            raise

    def _open(self, name, flags, parent=None):
        fd = os.open(str(name), flags | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=parent)
        self._fds.append(fd)
        return fd

    def _prepare(self):
        flags = os.O_RDONLY | os.O_DIRECTORY
        parent = None
        for index, name in enumerate(self.app.parts):
            fd = self._open(name, flags, parent)
            details = os.fstat(fd)
            is_app = index == len(self.app.parts) - 1
            sticky_ancestor = not is_app and details.st_mode & stat.S_ISVTX
            if (not stat.S_ISDIR(details.st_mode) or details.st_uid != 0 or
                    (details.st_mode & 0o022 and not sticky_ancestor)):
                raise ValueError("Unsafe original OCRUN application ancestor: " + str(name))
            self._directories.append((parent, name, fd, _directory_key(details)))
            parent = fd
        self._app_fd = parent
        self._py_fd = self._open("py", flags, self._app_fd)
        py_details = os.fstat(self._py_fd)
        if (not stat.S_ISDIR(py_details.st_mode) or not _allowed_owner(py_details) or
                stat.S_IMODE(py_details.st_mode) != 0o755):
            raise ValueError("Original OCRUN py directory needs root or UID 201:GID 200 and mode 0755")
        self._py_original = py_details
        self._py_expected = _directory_key(py_details)
        hashes = {}
        for relative in SCRIPTS:
            directory = self._py_fd if relative.startswith("py/") else self._app_fd
            leaf = relative.split("/")[-1]
            fd = self._open(leaf, os.O_RDONLY | os.O_NONBLOCK, directory)
            details = os.fstat(fd)
            if (not stat.S_ISREG(details.st_mode) or details.st_nlink != 1 or
                    not _allowed_owner(details) or stat.S_IMODE(details.st_mode) != 0o755):
                raise ValueError("Unsafe original OCRUN file (owner, mode or links): " + relative)
            data = _read(fd)
            if _file_key(os.fstat(fd)) != _file_key(details):
                raise ValueError("Original OCRUN file changed while reading: " + relative)
            self.originals[relative] = data
            self.metadata[relative] = {"uid": details.st_uid, "gid": details.st_gid,
                                       "mode": stat.S_IMODE(details.st_mode)}
            self._files[relative] = (directory, leaf, fd, _file_key(details))
            hashes[relative] = hashlib.sha256(data).hexdigest()
        self.release = _release_for_hashes(hashes)
        self.verify()

    @property
    def plan(self):
        changes = []
        for relative, details in self.metadata.items():
            if details["uid"] != 0 or details["gid"] != 0:
                changes.append({"path": relative, "from_uid": details["uid"],
                                "from_gid": details["gid"], "to_uid": 0, "to_gid": 0,
                                "method": "atomic replacement"})
        if self._py_original.st_uid != 0:
            changes.append({"path": "py", "from_uid": self._py_original.st_uid,
                            "from_gid": self._py_original.st_gid, "to_uid": 0, "to_gid": 0,
                            "method": "pinned directory ownership"})
        return {"release": self.release, "files_verified": len(SCRIPTS),
                "ownership_changes": changes}

    def _verify_directories(self):
        if not self._fds:
            raise ValueError("Original OCRUN adoption context is closed")
        for parent, name, fd, expected in self._directories:
            by_path = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if _directory_key(by_path) != expected or _directory_key(os.fstat(fd)) != expected:
                raise ValueError("Original OCRUN application directory changed during adoption")
        py_path = os.stat("py", dir_fd=self._app_fd, follow_symlinks=False)
        if (_directory_key(py_path) != self._py_expected or
                _directory_key(os.fstat(self._py_fd)) != self._py_expected):
            raise ValueError("Original OCRUN py directory changed during adoption")

    def verify(self):
        """Recheck pinned path identities and bytes before target file mutations."""
        self._verify_directories()
        expected_hashes = KNOWN_RELEASES[self.release] if hasattr(self, "release") else None
        for relative, (parent, leaf, fd, expected) in self._files.items():
            by_path = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
            if _file_key(by_path) != expected or _file_key(os.fstat(fd)) != expected:
                raise ValueError("Original OCRUN file changed during adoption: " + relative)
            data = _read(fd)
            if (_file_key(os.fstat(fd)) != expected or
                    (expected_hashes is not None and
                     hashlib.sha256(data).hexdigest() != expected_hashes[relative])):
                raise ValueError("Original OCRUN file changed during adoption: " + relative)

    def apply(self):
        if self._applied:
            raise ValueError("Original OCRUN adoption was already applied")
        self.verify()
        if self._py_original.st_uid != 0:
            os.fchown(self._py_fd, 0, 0)
            self._changed_py = True
            self._py_expected = _directory_key(os.fstat(self._py_fd))
        self._applied = True
        try:
            self.verify()
        except BaseException:
            self.rollback()
            raise

    def rollback(self):
        """Restore py ownership only; the caller separately restores file snapshots."""
        if not self._changed_py:
            return
        self._verify_directories()
        os.fchown(self._py_fd, self._py_original.st_uid, self._py_original.st_gid)
        self._py_expected = _directory_key(os.fstat(self._py_fd))
        self._changed_py = False
        self._applied = False

    def close(self):
        while self._fds:
            os.close(self._fds.pop())

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()
