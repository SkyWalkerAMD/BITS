"""Native distribution files: private data and explicit root-owned programs."""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile

VERSION = '0.2.4'
ROOT = Path(__file__).absolute().parent


def directory(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Absolute trusted directory required')
    for part in list(reversed(path.parents)) + [path]:
        info = part.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
            raise ValueError('Untrusted directory: ' + str(part))
    return path


def read(path, limit=8 * 1024 * 1024, private=False):
    path = Path(path)
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or
                info.st_mode & 0o7022 or (private and stat.S_IMODE(info.st_mode) != 0o600)):
            raise ValueError('Untrusted file: ' + str(path))
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            value = stream.read(limit + 1)
        if len(value) > limit:
            raise ValueError('File exceeds limit: ' + str(path))
        return value
    finally:
        os.close(fd)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def fingerprint(path, target=None, limit=16 * 1024 ** 3):
    """Bounded-memory identity/hash, optionally copy the same pinned file."""
    path = Path(path)
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != 0 or before.st_nlink != 1
                or before.st_mode & 0o7022 or before.st_size > limit):
            raise ValueError('Untrusted or oversized file: ' + str(path))
        h = hashlib.sha256()
        total = 0
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                total += len(block)
                if total > limit:
                    raise ValueError('Growing file exceeds limit: ' + str(path))
                h.update(block)
                if target is not None:
                    target.write(block)
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if identity(before) != identity(os.fstat(fd)) or identity(before) != identity(path.lstat()):
            raise ValueError('File changed while reading: ' + str(path))
        return {'identity': identity(before), 'sha256': h.hexdigest(), 'bytes': total}
    finally:
        os.close(fd)


def load(path, private=False):
    return json.loads(read(path, private=private).decode('utf-8'))


def write(path, value, mode=0o600):
    path = Path(path)
    directory(path.parent)
    if path.exists() or path.is_symlink():
        read(path)
    fd, temporary = tempfile.mkstemp(prefix='.ocrun-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(value)
            stream.flush()
            os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), mode)
        os.replace(temporary, str(path))
        parent = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def save(path, value):
    write(path, (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8'))


def sync_directory(path):
    fd = os.open(str(directory(path)), os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


@contextlib.contextmanager
def lock_existing(path):
    directory(Path(path).parent)
    fd = os.open(str(path), os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        read(path, private=True)
        if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (Path(path).lstat().st_dev, Path(path).lstat().st_ino):
            raise ValueError('Lock identity changed')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def mkdir(path):
    path = Path(path)
    if not path.exists():
        directory(path.parent)
        path.mkdir(mode=0o700)
    return directory(path)


def verify(role, require_root=True):
    if require_root and os.geteuid() != 0:
        raise ValueError('Run this setup or node operation as root')
    package = load(ROOT / 'PACKAGE.json')
    if package['version'] != VERSION or package['role'] != role:
        raise ValueError('Native package identity differs')
    for relative, checksum in package['files'].items():
        if Path(relative).is_absolute() or '..' in Path(relative).parts:
            raise ValueError('Invalid package inventory')
        if fingerprint(ROOT / relative, limit=128 * 1024 * 1024)['sha256'] != checksum:
            raise ValueError('Package file changed: ' + relative)
    return package
