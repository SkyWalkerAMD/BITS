"""Explicitly join one idle node; original centers and other nodes are untouched."""
import argparse
import contextlib
import fcntl
import json
import os
from pathlib import Path
import re
import socket
import stat
import sys
import tempfile

from . import safe, connection

ROOT = Path('/etc/ocrun-node')
ORIGINAL_ENV = '1f0a2d7df3eb84b42c095de2d621262bdb0e0b69a559eb0623268ad21e7965fb'
BLOCK = '# OCRUN authenticated center connection\nsource /etc/ocrun-node/connection.env || return 1\n'


def idle(app):
    monitored = {str(app / name) for name in ('ocb', 'oct', 'mon-sensors', 'mon-sensors-plugin')}
    for item in Path('/proc').iterdir():
        if not item.name.isdigit() or int(item.name) == os.getpid():
            continue
        try:
            args = (item / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError):
            continue
        if any(os.fsdecode(arg) in monitored for arg in args):
            raise ValueError('Stop this node scheduler/collector before connection changes (PID {})'.format(item.name))
    state = app / '.mon-sensors-finish'
    if state.exists():
        for path in state.glob('*.json'):
            value = safe.load(path)
            if value.get('schema') == 'mon-sensors-finish-v1' and value.get('stage') not in ('complete', 'closed_incomplete'):
                raise ValueError('Resolve pending task finalization before changing servers: ' + path.name)


def original(path):
    safe.directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid not in (0, 201) or info.st_mode & 0o7022 or info.st_nlink != 1:
            raise ValueError('Untrusted original oc.env metadata')
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            data = stream.read(1024 * 1024)
        if safe.digest(data) != ORIGINAL_ENV:
            raise ValueError('Unknown or edited oc.env; original is preserved for review')
        return data, info
    finally:
        os.close(fd)


def replace_known(path, data, uid=0, gid=0, mode=0o644):
    safe.directory(path.parent)
    fd, temporary = tempfile.mkstemp(prefix='.node-connect-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chown(temporary, uid, gid)
        os.chmod(temporary, mode)
        os.replace(temporary, str(path))
        parent = os.open(str(path.parent), os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def execute(args):
    app = safe.directory(Path(args.app))
    idle(app)
    marker = ROOT / 'install.json'
    if args.rollback:
        installed = safe.load(marker)
        try:
            current = safe.read(app / 'oc.env')
        except ValueError:
            current, unused = original(app / 'oc.env')
        if installed['app'] != str(app) or safe.digest(current) not in (installed['before'], installed['after']):
            raise ValueError('Connected oc.env changed; preserved for inspection')
        for name, checksum in installed['files'].items():
            if (ROOT / name).exists() and safe.digest(safe.read(ROOT / name)) != checksum:
                raise ValueError('Connection file changed: ' + name)
        if safe.digest(current) == installed['after']:
            backup = safe.read(ROOT / 'oc.env.before')
            if safe.digest(backup) != installed['before']:
                raise ValueError('Connection backup changed')
            replace_known(app / 'oc.env', backup, installed['uid'], installed['gid'], installed['mode'])
        for name in installed['files']:
            if (ROOT / name).exists() or (ROOT / name).is_symlink():
                (ROOT / name).unlink()
        marker.unlink()
        print(json.dumps({'status': 'rolled_back', 'app': str(app), 'scheduler_started': False}))
        return
    if not args.config:
        raise ValueError('--config private-connection.json is required')
    path = Path(args.config).absolute()
    raw = safe.read(path, 4096)
    if path.stat().st_mode & 0o077:
        raise ValueError('Connection file must have private mode 0600')
    value = connection.validate(json.loads(raw.decode('utf-8')))
    if value['node'] != socket.gethostname():
        raise ValueError('Connection file belongs to a different node')
    finalizer = safe.load(app / '.mon-sensors-finish-install.json')
    if finalizer.get('version') not in ('0.2.2', '0.2.3', '0.2.4', '0.2.5', '0.2.6', '0.3.0'):
        raise ValueError('Install a supported BITS-o finalizer with authenticated connection support first')
    if marker.exists() or marker.is_symlink():
        installed = safe.load(marker)
        if any(not (ROOT / name).exists() for name in installed['files']):
            raise ValueError('Interrupted connection setup; use --rollback to restore the recorded original before retrying')
        if installed['app'] != str(app) or safe.digest(safe.read(app / 'oc.env')) != installed['after'] or safe.read(ROOT / 'connection.json') != raw:
            raise ValueError('Existing connection differs; inspect and explicitly roll back before changing it')
        for name, checksum in installed['files'].items():
            if safe.digest(safe.read(ROOT / name)) != checksum:
                raise ValueError('Connection file changed: ' + name)
        print(json.dumps({'status': 'checked', 'already_connected': True, 'node': value['node']}))
        return
    data, info = original(app / 'oc.env')
    text = data.decode('utf-8')
    for name, address in (('LOGSVR1', value['log_server']), ('LOGSVR2', value['log_server']),
                          ('RDBSVR1', value['rdb_server']), ('RDBSVR2', value['rdb_server'])):
        text, count = re.subn(r'^' + name + r'=[^\n]*$', name + '=' + address, text, flags=re.M)
        if count != 1:
            raise ValueError('Ambiguous original server assignment: ' + name)
    after = (BLOCK + text).encode('utf-8')
    result = {'status': 'checked', 'node': value['node'], 'app': str(app), 'rdb_server': value['rdb_server'],
              'log_server': value['log_server'], 'authentication': 'required', 'scheduler_started': False,
              'original_sha256': safe.digest(data), 'patched_sha256': safe.digest(after)}
    if args.check:
        print(json.dumps(result, sort_keys=True))
        return
    safe.mkdir(ROOT, 0o700)
    for filename in ('connection.json', 'connection.env', 'oc.env.before'):
        if (ROOT / filename).exists() or (ROOT / filename).is_symlink():
            raise ValueError('Partial or unknown connection setup preserved: ' + filename)
    environment = ("export REDISCLI_AUTH='" + value['password'] + "'\n").encode('ascii')
    files = {'connection.json': raw, 'connection.env': environment, 'oc.env.before': data}
    # Record the recovery inventory before replacing the application entry.
    installed = {'app': str(app), 'before': safe.digest(data), 'after': safe.digest(after),
                 'uid': info.st_uid, 'gid': info.st_gid, 'mode': info.st_mode & 0o777,
                 'files': {name: safe.digest(contents) for name, contents in files.items()}}
    safe.save(marker, installed)
    for name, contents in files.items():
        safe.write(ROOT / name, contents)
    idle(app)
    original(app / 'oc.env')
    replace_known(app / 'oc.env', after)
    result['status'] = 'connected'
    print(json.dumps(result, sort_keys=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', default='/root/ocrun')
    parser.add_argument('--config')
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument('--check', action='store_true')
    action.add_argument('--apply', action='store_true')
    action.add_argument('--rollback', action='store_true')
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.error('Root required on the specified idle node')
    app = safe.directory(Path(args.app))
    with contextlib.ExitStack() as stack:
        for path in (app / '.mon-sensors-install.lock', app / '.mon-sensors-runtime.lock'):
            stack.enter_context(safe.lock(path))
        state = app / '.mon-sensors-finish'
        if state.exists():
            for name in ('start.lock', 'scheduler.lock', 'operation.lock'):
                stack.enter_context(safe.lock(state / name))
        execute(args)


if __name__ == '__main__':
    try:
        safe.directory(Path('/run'))
        descriptor = os.open('/run/ocrun-node-connect.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        with os.fdopen(descriptor, 'a') as guard:
            info = os.fstat(guard.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1 or info.st_mode & 0o077:
                raise ValueError('Untrusted node connection lock')
            fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            main()
    except (OSError, ValueError, KeyError) as error:
        print('ocrun-node-connect: ' + str(error), file=sys.stderr)
        sys.exit(1)
