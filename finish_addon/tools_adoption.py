"""Explicitly copy selected legacy tool directories into private root-owned storage."""
import hashlib
import os
from pathlib import Path
import stat
import tempfile
import shutil
from common import atomic, directory, file_hash, json_read, save
from security import trusted_executable
from workload import PROFILES


# Original OCRUN ships these entrypoints as links into the system installation.
# Do not follow other legacy links or extend executable trust to UID 201.
SYSTEM_TARGETS = {
    'stress': ('/usr/bin/stress', '/bin/stress'),
    'stress_r2': ('/usr/bin/stress', '/bin/stress'),
    'stress-ng': ('/usr/bin/stress-ng', '/bin/stress-ng'),
    'stress-ng_r2': ('/usr/bin/stress-ng', '/bin/stress-ng'),
}
MAX_TOOL_BYTES = 1024 ** 3


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns,
            info.st_ctime_ns, info.st_uid, info.st_gid, info.st_mode, info.st_nlink)


def system_link(path, name, expected):
    """Materialize one known system executable through pinned descriptors.

    Legacy link ownership is inspected but never authorizes its target. The
    complete system target path still has to pass root executable validation.
    """
    link_fd = os.open(str(path), os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        if identity(os.fstat(link_fd)) != identity(expected):
            raise ValueError('Tool link changed while opening: ' + str(path))
        target = os.readlink('', dir_fd=link_fd)
        if target not in SYSTEM_TARGETS.get(name, ()):
            raise ValueError('Unsupported tool link target: {} -> {}'.format(path, target))
        try:
            with trusted_executable(target) as (executable_fd, unused):
                before = os.fstat(executable_fd)
                if before.st_size > MAX_TOOL_BYTES:
                    raise ValueError('System tool exceeds 1 GiB budget')
                # /proc/self/fd resolves this already validated, held descriptor;
                # never reopen through the untrusted legacy pathname.
                data_fd = os.open('/proc/self/fd/' + str(executable_fd),
                                  os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
                with os.fdopen(data_fd, 'rb') as stream:
                    if identity(os.fstat(stream.fileno())) != identity(before):
                        raise ValueError('System executable changed while opening')
                    data = stream.read(MAX_TOOL_BYTES + 1)
                    if len(data) > MAX_TOOL_BYTES or identity(os.fstat(stream.fileno())) != identity(before):
                        raise ValueError('System executable changed while reading')
        except (OSError, ValueError) as error:
            raise ValueError('System tool target is unavailable or untrusted: ' + target) from error
        if identity(path.lstat()) != identity(expected):
            raise ValueError('Tool link changed while copying: ' + str(path))
        return data, target
    finally:
        os.close(link_fd)


def adopt(app, names, check=False):
    root = app / '.mon-sensors-workloads'
    plans = []
    for name in names:
        if name not in PROFILES:
            raise ValueError('Unknown workload: ' + name)
        relative = Path(PROFILES[name][0])
        source = app / relative.parent
        for ancestor in [app] + [app.joinpath(*relative.parts[:i]) for i in range(1, len(relative.parts))]:
            info = ancestor.lstat()
            if not stat.S_ISDIR(info.st_mode) or info.st_uid not in (0, 201) or info.st_mode & 0o6022:
                raise ValueError('Untrusted tool directory: ' + str(ancestor))
        inventory = {}
        paths = list(source.rglob('*'))
        if len(paths) > 10000:
            raise ValueError('Tool directory too large for automatic adoption')
        total = 0
        for path in [source] + paths:
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                if info.st_uid not in (0, 201) or path != app / PROFILES[name][0]:
                    raise ValueError('Unsupported tool link: ' + str(path))
                # Linux symlink mode 0777 does not describe the target's access.
                data, system_target = system_link(path, name, info)
                total += len(data)
                if total > MAX_TOOL_BYTES:
                    raise ValueError('Tool copy exceeds 1 GiB budget')
                inventory[path.relative_to(source).as_posix()] = {
                    'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                    'mode': 0o755, 'system_target': system_target, 'data': data}
                continue
            if info.st_uid not in (0, 201) or info.st_mode & 0o6022:
                raise ValueError('Untrusted tool ownership/permissions: ' + str(path))
            if stat.S_ISDIR(info.st_mode):
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError('Tool links/devices are not adopted: ' + str(path))
            total += info.st_size
            if total > MAX_TOOL_BYTES:
                raise ValueError('Tool copy exceeds 1 GiB budget')
            fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                observed = os.fstat(fd)
                if (observed.st_dev, observed.st_ino, observed.st_size) != (info.st_dev, info.st_ino, info.st_size):
                    raise ValueError('Tool changed while opening')
                with os.fdopen(fd, 'rb', closefd=False) as stream:
                    data = stream.read(MAX_TOOL_BYTES + 1)
                if identity(path.lstat()) != identity(info):
                    raise ValueError('Tool changed while copying')
            finally:
                os.close(fd)
            inventory[path.relative_to(source).as_posix()] = {'sha256': hashlib.sha256(data).hexdigest(),
                'bytes': len(data), 'mode': 0o755 if info.st_mode & 0o111 else 0o644, 'data': data}
        public = {n: {k: v for k, v in value.items() if k != 'data'} for n, value in inventory.items()}
        if 'MANIFEST.json' in public:
            raise ValueError('Tool directory uses a reserved adoption manifest name')
        plan = {'task': name, 'source': str(source), 'files': public, 'bytes': total}
        plans.append(plan)
        if check:
            continue
        root.mkdir(mode=0o700, exist_ok=True)
        directory(root)
        target = root / name
        if target.exists():
            if json_read(target / 'MANIFEST.json') != plan:
                raise ValueError('Pinned tool already exists with different content; retain it and review explicitly')
            continue
        stage = Path(tempfile.mkdtemp(prefix='.adopt-', dir=str(root)))
        try:
            for n, value in inventory.items():
                dest = stage / n
                dest.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                atomic(dest, value['data'], replace=False)
                dest.chmod(value['mode'])
            save(stage / 'MANIFEST.json', plan)
            os.rename(str(stage), str(target))
        finally:
            if stage.exists():
                shutil.rmtree(str(stage))
    return {'action': 'copy_selected_workloads', 'check': check, 'originals_modified': False, 'plans': plans}


def pinned(app, name):
    target = app / '.mon-sensors-workloads' / name
    if not target.exists():
        return None
    directory(target)
    manifest = json_read(target / 'MANIFEST.json')
    if manifest['task'] != name:
        raise ValueError('Pinned workload identity differs')
    for n, value in manifest['files'].items():
        if Path(n).is_absolute() or '..' in Path(n).parts:
            raise ValueError('Invalid workload inventory')
        if file_hash(target / n) != {k: value[k] for k in ('sha256', 'bytes')}:
            raise ValueError('Pinned workload changed: ' + n)
    return target / Path(PROFILES[name][0]).name
