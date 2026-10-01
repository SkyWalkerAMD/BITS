"""Fresh-node packaging entry; reuses the tested collector and batch executor."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).absolute().parent
sys.path.insert(0, str(ROOT))
import common
from bits_layout import LAYOUT

CONFIG = Path('/etc/bits/node')
DATA = Path('/var/lib/bits/node')
APP = DATA / 'app'
LOGS = Path('/var/log/bits/node')
NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z')


def call(argv, **kwargs):
    return subprocess.run([str(v) for v in argv], check=True, **kwargs)


def payload(relative):
    return common.read(ROOT / relative, 128 * 1024 * 1024)


def configured():
    package = common.verify('node')
    value = common.load(CONFIG / 'native.json', private=True)
    if value.get('version') != common.VERSION or value.get('app') != str(APP) or value.get('node') != socket.gethostname():
        raise ValueError('Node identity differs from its explicit configuration')
    connection = common.load(CONFIG / 'connection.json', private=True)
    if common.digest(common.read(CONFIG / 'connection.json', private=True)) != value['connection_sha256']:
        raise ValueError('Private connection changed; keep existing task evidence and review it')
    if common.digest(common.read(ROOT / 'PACKAGE.json')) != value['package_sha256']:
        raise ValueError('Installed package changed; explicit node upgrade is required')
    for name, expected in value['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Invalid managed node path')
        if common.digest(common.read(APP / name)) != expected:
            raise ValueError('Managed node file changed: ' + name)
    return value, connection, package


def finish(arguments, **kwargs):
    return call([APP / LAYOUT.entry] + list(arguments), **kwargs)


def queue_arguments(action, value, connection):
    return [action, '--rdb-server', connection['rdb_server'], '--rdb-port', str(connection['rdb_port']),
            '--log-server', connection['log_server'], '--log-dir', str(LOGS), '--node', value['node'], '--serial', value['serial']]


def active_scheduler():
    for item in Path('/proc').iterdir():
        if not item.name.isdigit() or int(item.name) == os.getpid():
            continue
        try:
            argv = (item / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError):
            continue
        if (os.fsencode(str(APP / LAYOUT.scheduler)) in argv or
                (os.fsencode(str(ROOT / 'node.py')) in argv and b'run' in argv)):
            raise ValueError('This node scheduler is still running (PID {})'.format(item.name))


def idle():
    active_scheduler()
    spec = importlib.util.spec_from_file_location('native_suite', '/opt/bits/workloads/0.1.0/suite.py')
    suite = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(suite)
    suite.require_idle(APP)
    if (APP / LAYOUT.state).exists():
        for path in (APP / LAYOUT.state).glob('*.json'):
            state = common.load(path)
            if state.get('schema') == 'mon-sensors-finish-v1' and state.get('stage') not in ('complete', 'closed_incomplete'):
                raise ValueError('Unfinished batch: ' + path.stem + '; use status and recover/retry')


def initialize(args):
    common.verify('node')
    active_scheduler()
    if Path('/root/ocrun/ocb').exists():
        raise ValueError('Original OCRUN node exists. Use the compatibility components for that node; fresh-node initialization preserved it.')
    if Path('/etc/ocrun-node/native.json').exists():
        raise ValueError('A pre-0.3 BITS node is configured. Export and detach it before migration; its tasks and results were preserved.')
    sys.path.insert(0, str(ROOT / 'center-code'))
    from bits_core.center.connection import validate
    connection_bytes = common.read(Path(args.config).absolute(), private=True)
    connection = validate(json.loads(connection_bytes.decode('utf-8')))
    if connection['node'] != socket.gethostname():
        raise ValueError('Connection belongs to a different hostname')
    if not NAME.fullmatch(args.serial) or '..' in args.serial:
        raise ValueError('Supply a stable plain hardware/asset serial')
    for path in (CONFIG, DATA, LOGS):
        if path.exists() or path.is_symlink():
            common.directory(path)
    if (CONFIG / 'native.json').exists():
        value, installed, unused = configured()
        if installed != connection or value['serial'] != args.serial or value['keep_on'] != args.keep_on:
            raise ValueError('Existing node configuration differs; preserved')
        call(['/usr/libexec/bits-workloads', 'bind', '--app', APP] + (['--check'] if args.check else []), stdout=subprocess.PIPE)
        if not args.check and (DATA / 'setup.json').exists():
            (DATA / 'setup.json').unlink()
            common.sync_directory(DATA)
        print(json.dumps({'status': 'already_configured', 'node': value['node'], 'scheduler_started': False}))
        return
    journal = DATA / 'setup.json'
    intent = {'node': connection['node'], 'serial': args.serial, 'keep_on': args.keep_on,
              'connection_sha256': common.digest(connection_bytes),
              'package_sha256': common.digest(common.read(ROOT / 'PACKAGE.json'))}
    if journal.exists() and common.load(journal, private=True) != intent:
        raise ValueError('Interrupted setup belongs to different input; existing paths preserved')
    if not journal.exists() and (APP.exists() or APP.is_symlink() or (CONFIG / 'connection.json').exists() or (CONFIG / 'connection.json').is_symlink()):
        raise ValueError('Unmanaged/interrupted node paths exist; preserve them for recovery')
    result = {'status': 'checked', 'version': common.VERSION, 'node': connection['node'],
              'app': str(APP), 'logs': str(LOGS), 'shutdown_after_seconds': None if args.keep_on else 1800,
              'automatic_task_polling': False, 'scheduler_started': False}
    if args.check:
        print(json.dumps(result))
        return
    for path in (CONFIG.parent, DATA.parent, LOGS.parent, CONFIG, DATA, LOGS):
        common.mkdir(path)
    fd = os.open(str(DATA / '.setup.lock'), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    with os.fdopen(fd, 'r+b') as guard:
        import fcntl
        common.read(DATA / '.setup.lock', private=True)
        if os.fstat(guard.fileno()).st_ino != (DATA / '.setup.lock').lstat().st_ino:
            raise ValueError('Setup lock identity changed')
        fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (CONFIG / 'native.json').exists():
            raise ValueError('Another setup completed; repeat the configuration check')
        common.save(journal, intent)
        stage = Path(tempfile.mkdtemp(prefix='.app-', dir=str(DATA)))
        try:
            for path in (ROOT / 'template').rglob('*'):
                relative = path.relative_to(ROOT / 'template')
                target = stage / relative
                if path.is_dir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    common.write(target, payload('template/' + relative.as_posix()), 0o755 if path.stat().st_mode & 0o111 else 0o600)
            (stage / LAYOUT.state).mkdir(mode=0o700)
            if APP.exists() or APP.is_symlink():
                common.directory(APP)
                expected = {p.relative_to(stage).as_posix(): common.digest(common.read(p))
                            for p in stage.rglob('*') if p.is_file()}
                actual = {p.relative_to(APP).as_posix(): common.digest(common.read(p))
                          for p in APP.rglob('*') if not p.is_dir()}
                if actual != expected:
                    raise ValueError('Interrupted setup files differ; retained for inspection')
            else:
                os.rename(str(stage), str(APP))
                common.sync_directory(DATA)
            target = CONFIG / 'connection.json'
            if target.exists() or target.is_symlink():
                if common.read(target, private=True) != connection_bytes:
                    raise ValueError('Interrupted connection differs; retained')
            else:
                common.write(target, connection_bytes)
            files = {p.relative_to(APP).as_posix(): common.digest(common.read(p))
                     for p in APP.rglob('*') if p.is_file()}
            common.save(CONFIG / 'native.json', dict(result, status='configured', serial=args.serial,
                keep_on=args.keep_on, connection_sha256=common.digest(connection_bytes), files=files,
                package_sha256=common.digest(common.read(ROOT / 'PACKAGE.json'))))
            call(['/usr/libexec/bits-workloads', 'bind', '--app', APP], stdout=subprocess.PIPE)
            journal.unlink()
            common.sync_directory(DATA)
            result['status'] = 'configured'
            print(json.dumps(result))
        finally:
            if stage.exists():
                shutil.rmtree(str(stage))


def run_batch():
    value, connection, unused = configured()
    import fcntl
    lock = DATA / '.run.lock'
    lockfd = os.open(str(lock), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    common.read(lock, private=True)
    if os.fstat(lockfd).st_ino != lock.lstat().st_ino:
        raise ValueError('Run lock identity changed')
    fcntl.flock(lockfd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    # Held until this short-lived scheduler process exits, not inherited by workers.
    stamp = Path('/proc/self/stat').read_text().rsplit(')', 1)[1].split()
    common.save(DATA / 'scheduler.json', {'pid': os.getpid(), 'start_ticks': stamp[19],
        'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip()})
    print('OCRUN: waiting 60 seconds for node initialization', flush=True)
    time.sleep(60)
    finish(queue_arguments('run-queue', value, connection))
    # Only a successful, fully delivered batch reaches the original 30-minute policy.
    if value['keep_on']:
        return
    print('OCRUN: successful batch; original 30-minute idle shutdown policy active', flush=True)
    remaining = 6
    loads = re.compile(r'^(stress(?:-ng.*)?|mprime|mlc|mbw|cyclictest|runcpu|unixbench)$')
    while remaining:
        remaining -= 1
        users = subprocess.run(['/usr/bin/who'], stdout=subprocess.PIPE)
        processes = subprocess.run(['/usr/bin/ps', '-eo', 'comm='], stdout=subprocess.PIPE)
        if users.returncode or processes.returncode:
            raise ValueError('Cannot confirm idle system; automatic shutdown cancelled')
        if users.stdout.strip() or any(loads.fullmatch(p.strip()) for p in processes.stdout.decode().splitlines()):
            remaining = 6
        time.sleep(300)
    finish(['check', '--scheduler'])
    call(['/usr/sbin/poweroff'])


def stop_scheduler():
    value, connection, unused = configured()
    for path in (APP / LAYOUT.state).glob('*.json'):
        state = common.load(path)
        if state.get('schema') == 'mon-sensors-finish-v1' and state.get('stage') not in ('complete', 'closed_incomplete'):
            raise ValueError('Batch still pending; use stop --case then explicit recovery')
    entry = DATA / 'scheduler.json'
    if not entry.exists():
        print(json.dumps({'status': 'not_running'}))
        return
    saved = common.load(entry, private=True)
    process = Path('/proc') / str(saved['pid'])
    if not process.exists() or saved['boot_id'] != Path('/proc/sys/kernel/random/boot_id').read_text().strip():
        print(json.dumps({'status': 'not_running'}))
        return
    if (process / 'stat').read_text().rsplit(')', 1)[1].split()[19] != saved['start_ticks']:
        raise ValueError('PID identity changed; no process was signalled')
    argv = (process / 'cmdline').read_bytes().split(b'\0')
    if os.fsencode(str(ROOT / 'node.py')) not in argv or b'run' not in argv:
        raise ValueError('Unexpected scheduler command; no process was signalled')
    os.kill(saved['pid'], signal.SIGTERM)
    for unused in range(50):
        if not process.exists() or (process / 'stat').read_text().rsplit(')', 1)[1].split()[0] == 'Z':
            print(json.dumps({'status': 'stopped'}))
            return
        time.sleep(.1)
    raise ValueError('Scheduler exit not confirmed')


def detach(check):
    journal = DATA / 'detach.json'
    active_scheduler()
    if journal.exists():
        state = common.load(journal, private=True)
        name = state['backup']
        if Path(name).name != name or not name.startswith('detached-'):
            raise ValueError('Invalid detach recovery path')
        backup = common.directory(DATA / name)
        for item, checksum in state['files'].items():
            if item not in ('native.json', 'connection.json') or common.digest(common.read(backup / item, private=True)) != checksum:
                raise ValueError('Detach backup differs; retained')
            if (CONFIG / item).exists() and common.digest(common.read(CONFIG / item, private=True)) != checksum:
                raise ValueError('Configuration changed during detach; retained')
        if APP.exists():
            configured()
            idle()
    else:
        configured()
        idle()
        if check:
            print(json.dumps({'status': 'checked', 'results_preserved': True}))
            return
        backup = Path(tempfile.mkdtemp(prefix='detached-', dir=str(DATA)))
        inventory = {}
        # /etc and /var may be different filesystems. Pin and copy first, then
        # journal the prepared backup before any installed path is detached.
        for name in ('native.json', 'connection.json'):
            value = common.read(CONFIG / name, private=True)
            common.write(backup / name, value)
            inventory[name] = common.digest(value)
        common.save(journal, {'backup': backup.name, 'files': inventory})
    if check:
        print(json.dumps({'status': 'checked', 'recovery': True, 'backup': str(backup)}))
        return
    if APP.exists():
        os.rename(str(APP), str(backup / 'app'))
        common.sync_directory(backup)
        common.sync_directory(DATA)
    else:
        common.directory(backup / 'app')
    saved = common.load(backup / 'native.json', private=True)
    for name, checksum in saved['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts or common.digest(common.read(backup / 'app' / name)) != checksum:
            raise ValueError('Detached application differs; configuration retained')
    for name in ('connection.json', 'native.json'):
        if (CONFIG / name).exists():
            (CONFIG / name).unlink()
    common.sync_directory(CONFIG)
    journal.unlink()
    common.sync_directory(DATA)
    print(json.dumps({'status': 'detached', 'backup': str(backup), 'results_preserved': True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version', action='version', version='bits-node ' + common.VERSION)
    sub = parser.add_subparsers(dest='action')
    setup = sub.add_parser('configure', aliases=['setup'])
    setup.add_argument('--config', required=True)
    setup.add_argument('--serial', help='Defaults to the fixed sysfs motherboard serial; override placeholders explicitly')
    setup.add_argument('--keep-on', action='store_true', help='Explicitly disable automatic shutdown on this new node')
    setup.add_argument('--check', action='store_true')
    for name in ('check', 'start', 'run', 'preflight', 'stop-scheduler'):
        sub.add_parser(name)
    status = sub.add_parser('status')
    status.add_argument('--case')
    status.add_argument('--human', action='store_true')
    for name in ('stop', 'retry', 'recover', 'close-incomplete'):
        item = sub.add_parser(name)
        item.add_argument('--case', required=True)
        if name == 'recover':
            item.add_argument('--interrupted', action='store_true', required=True)
        if name == 'close-incomplete':
            item.add_argument('--reason', required=True)
    detaching = sub.add_parser('detach')
    detaching.add_argument('--check', action='store_true')
    tools = sub.add_parser('tools')
    tools.add_argument('args', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    common.verify('node')
    if args.action in ('configure', 'setup'):
        if not args.serial:
            args.serial = common.read(Path('/sys/devices/virtual/dmi/id/board_serial'), 1024).decode('ascii').strip()
            if args.serial.lower() in ('', 'none', 'unknown', 'default string', 'to be filled by o.e.m.', 'not specified', 'system serial number'):
                raise ValueError('Board serial unavailable; supply --serial VERIFIED_SERIAL')
            if not NAME.fullmatch(args.serial):
                raise ValueError('Board serial needs a supported explicit --serial identifier')
        initialize(args)
    elif args.action == 'tools':
        if not args.args or args.args[0] not in ('list', 'check', 'import-spec'):
            raise ValueError('Use tools list/check/import-spec')
        call(['/usr/libexec/bits-workloads'] + args.args)
    elif args.action == 'check' and not (CONFIG / 'native.json').exists():
        tools = json.loads(call(['/usr/libexec/bits-workloads', 'check'], stdout=subprocess.PIPE).stdout.decode('utf-8'))
        report = json.loads(call([LAYOUT.report, '--check'], stdout=subprocess.PIPE).stdout.decode('utf-8'))
        print(json.dumps({'status': 'not_configured', 'tools': tools, 'report': report,
            'next': 'bits-node configure --config PRIVATE.json --serial SERIAL', 'version': common.VERSION}))
    elif args.action == 'detach':
        with common.lock_existing(DATA / '.setup.lock'):
            detach(args.check)
    elif args.action == 'run':
        run_batch()
    elif args.action == 'stop-scheduler':
        stop_scheduler()
    elif args.action == 'preflight':
        value, connection, unused = configured()
        finish(queue_arguments('preflight', value, connection))
    elif args.action:
        configured()
        arguments = [args.action]
        if args.action == 'check':
            arguments.append('--scheduler')
        if getattr(args, 'human', False):
            arguments.append('--human')
        if getattr(args, 'case', None):
            arguments += ['--case', args.case]
        if getattr(args, 'interrupted', False):
            arguments.append('--interrupted')
        if getattr(args, 'reason', None):
            arguments += ['--reason', args.reason]
        finish(arguments)
    else:
        parser.error('Choose configure, check, start, status, stop, retry, recover, stop-scheduler or detach')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        print('bits-node: ' + str(error), file=sys.stderr)
        sys.exit(1)
