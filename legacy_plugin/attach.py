"""Ordered, journaled attachment of the existing components to a known node.

Each component retains its own installer validation and backup. The suite also
keeps a bounded snapshot of the exact entrypoints/modules it owns. No log,
task history, original tool directory, environment or service is removed.
"""
import base64
import contextlib
import json
import os
from pathlib import Path
import stat
import tempfile

from . import common, node

PATHS = ('mon-sensors', 'ocb', 'oct', 'py/mon-analyse-log.py', 'mon-analyse-log',
         '.bits-collector', '.bits-collector.d',
         'mon-sensors-plugin', 'mon-sensors-plugin.d', '.mon-sensors-backend',
         'mon-sensors-finish', 'mon-sensors-finish.d', '.mon-sensors-finish-install.json',
         '.mon-sensors-workload-suite.json')
REPORT = Path('/usr/local/bin/mon-sensors-report')
BRIDGE = b'#!/bin/sh\n# Managed mon-sensors-report bridge v1.\nexec /usr/local/bin/mon-sensors-report "$@"\n'


def exists(path):
    return path.exists() or path.is_symlink()


def record(path, data=True):
    if not exists(path):
        return None
    s = path.lstat()
    value = {'mode': stat.S_IMODE(s.st_mode), 'uid': s.st_uid, 'gid': s.st_gid}
    if stat.S_ISLNK(s.st_mode):
        if path.name not in ('mon-analyse-log', 'previous', 'displaced') or os.readlink(str(path)) != 'py/mon-analyse-log.py':
            raise ValueError('Unexpected link retained: ' + str(path))
        value.update(kind='link', target='py/mon-analyse-log.py')
    elif stat.S_ISDIR(s.st_mode):
        if s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError('Untrusted component directory: ' + str(path))
        value.update(kind='dir', children={})
        for child in sorted(path.iterdir()):
            value['children'][child.name] = record(child, data)
    elif stat.S_ISREG(s.st_mode) and s.st_nlink == 1 and not s.st_mode & 0o7022:
        fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            before = os.fstat(fd)
            with os.fdopen(fd, 'rb', closefd=False) as stream:
                raw = stream.read(16 * 1024 ** 2 + 1)
            after = os.fstat(fd)
            sig = lambda x: (x.st_dev, x.st_ino, x.st_size, x.st_mtime_ns, x.st_ctime_ns)
            if len(raw) > 16 * 1024 ** 2 or sig(s) != sig(before) or sig(before) != sig(after) or sig(after) != sig(path.lstat()):
                raise ValueError('Component changed or exceeds backup limit: ' + str(path))
        finally:
            os.close(fd)
        value.update(kind='file', sha256=common.digest(raw))
        if data:
            value['data'] = base64.b64encode(raw).decode('ascii')
    else:
        raise ValueError('Unsupported component file: ' + str(path))
    return value


def targets(cfg):
    app = Path(cfg['app'])
    return [app / p for p in PATHS] + [REPORT]


def capture(cfg, data=True):
    result = {str(p): record(p, data) for p in targets(cfg)}
    s = (Path(cfg['app']) / 'py').lstat()
    if not stat.S_ISDIR(s.st_mode) or s.st_mode & 0o022:
        raise ValueError('Unsafe original py directory')
    result['py_metadata'] = {'uid': s.st_uid, 'gid': s.st_gid, 'mode': stat.S_IMODE(s.st_mode)}
    return result


def without_data(value):
    if isinstance(value, dict):
        return {k: without_data(v) for k, v in value.items() if k != 'data'}
    return value


def commands(cfg):
    app = Path(cfg['app'])
    existing = (app / 'mon-sensors-plugin.d').exists()
    headless = (app / '.bits-collector.d').exists() or not existing
    existing = existing or (app / '.bits-collector.d').exists()
    collector = common.python(common.ROOT / 'components/collector/install-plugin-entry.py', '--app', str(app))
    collector += ['--modules-only'] if existing else ['--backend', 'sckocp', '--adopt-original']
    if headless:
        collector += ['--headless']
    finalizer = common.python(common.ROOT / 'components/finish/install.py', '--app', str(app))
    return collector, finalizer


def report_launcher():
    return (common.ROOT / 'report-launcher').read_bytes()


def check(cfg):
    common.root()
    common.verify('node')
    node.idle(cfg)
    common.run(['/usr/bin/bits-o-workloads', 'check'])
    collector, finalizer = commands(cfg)
    common.run(collector + ['--check'])
    if (Path(cfg['app']) / '.mon-sensors-finish-install.json').exists():
        common.run(finalizer + ['--check'])
    if exists(REPORT):
        raw = common.read(REPORT)
        if raw != report_launcher():
            # Only the known standalone launchers may be replaced, never an
            # arbitrary same-name executable. Store the exact previous bytes.
            approved = json.loads(common.read(common.ROOT / 'REPORT-LAUNCHERS.json').decode())
            if common.digest(raw) not in approved:
                raise ValueError('Unknown report command retained: ' + str(REPORT))
    entry = Path(cfg['app']) / 'mon-analyse-log'
    state = record(entry)
    if not state or not (state['kind'] == 'link' or
            (state['kind'] == 'file' and state['sha256'] == common.digest(BRIDGE))):
        raise ValueError('Unknown original report entry retained')
    return {'status': 'checked', 'role': 'node', 'app': cfg['app'],
            'steps': ['portable_report', 'collector', 'finalizer', 'workload_binding'],
            'source': common.verify('node')['source_commit'], 'original_tools_removed': False,
            'tasks_started': False, 'read_only': True}


@contextlib.contextmanager
def journal_lock(cfg):
    state = common.mkdir(Path(cfg['app']) / '.ocrun-plugin')
    path = state / 'attach.lock'
    if not exists(path):
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        os.close(fd)
    with common.lock_existing(path):
        yield state


def apply(cfg, resume=False):
    common.root()
    common.verify('node')
    node.idle(cfg)
    with journal_lock(cfg) as state:
        path = state / 'attachment.json'
        if exists(path):
            journal = json.loads(common.read(path, limit=64 * 1024 ** 2, private=True).decode())
            if journal['config'] != cfg and not (journal['status'] == 'rolled_back' and journal['config']['app'] == cfg['app']):
                raise ValueError('Attachment configuration changed; retained journal requires inspection')
            if journal['status'] == 'attached':
                if capture(cfg, False) != journal['after']:
                    raise ValueError('Attached component files changed; no overwrite')
                return {'status': 'already_attached', 'app': cfg['app']}
            if journal['status'] == 'rolled_back':
                previous = without_data(journal['before'])
                # Older rollback inventories predate the private worker. An
                # omitted NEW path means absent, never permission to replace it.
                for name in ('.bits-collector', '.bits-collector.d'):
                    previous.setdefault(str(Path(cfg['app']) / name), None)
                if capture(cfg, False) != previous:
                    raise ValueError('Original component files changed since rollback; review before reattaching')
                retained = state / ('attachment.' + common.digest(common.read(path, limit=64 * 1024 ** 2))[:16] + '.json')
                if retained.exists():
                    raise ValueError('Attachment history collision; retained')
                os.rename(str(path), str(retained))
                common.sync_directory(state)
                journal = None
            elif not resume or journal['status'] not in ('applying', 'failed'):
                raise ValueError('Existing attachment journal; use attach --resume or inspect rollback status')
        else:
            journal = None
        if journal is None:
            check(cfg)
            journal = {'schema': 'bits-o-attachment-v1', 'config': cfg, 'status': 'applying',
                       'before': capture(cfg), 'completed_steps': [], 'step': None}
            encoded = json.dumps(journal).encode()
            if len(encoded) > 48 * 1024 ** 2:
                raise ValueError('Backup exceeds limit; original installation retained')
            common.save(path, journal)
        collector, finalizer = commands(cfg)
        def report():
            old = journal['before'][str(REPORT)]
            current = record(REPORT)
            if current and current != old and current.get('sha256') != common.digest(report_launcher()):
                raise ValueError('Report command changed; retained')
            common.write(REPORT, report_launcher(), 0o755)
            entry = Path(cfg['app']) / 'mon-analyse-log'
            if entry.is_symlink():
                if os.readlink(str(entry)) != 'py/mon-analyse-log.py':
                    raise ValueError('Report entry changed')
                fd, temporary = tempfile.mkstemp(prefix='.plugin-report-', dir=str(entry.parent))
                try:
                    with os.fdopen(fd, 'wb') as stream:
                        stream.write(BRIDGE); stream.flush(); os.fsync(stream.fileno())
                        os.fchmod(stream.fileno(), 0o755)
                    os.replace(temporary, str(entry))
                    common.sync_directory(entry.parent)
                finally:
                    if os.path.exists(temporary):
                        os.unlink(temporary)
            elif common.read(entry) != BRIDGE:
                raise ValueError('Report entry changed')
            common.run([str(REPORT), '--check'])
        steps = [('portable_report', report),
                 ('collector', lambda: common.run(collector)),
                 ('finalizer', lambda: common.run(finalizer)),
                 ('workload_binding', lambda: common.run(['/usr/bin/bits-o-workloads', 'bind', '--app', cfg['app']]))]
        try:
            for label, action in steps:
                if label in journal['completed_steps']:
                    continue
                journal.update(status='applying', step=label)
                common.save(path, journal)
                action()
                journal['completed_steps'].append(label)
                journal['after'] = capture(cfg, False)
                common.save(path, journal)
            node.finish(cfg, ['check'], capture=True)
            journal.update(status='attached', step=None, error=None)
            common.save(path, journal)
        except Exception as exc:
            journal.update(status='failed', error=str(exc))
            common.save(path, journal)
            raise ValueError('Attachment stopped at {}: {}. Components kept their backups; fix the cause and use attach --resume.'.format(journal['step'], exc))
        return {'status': 'attached', 'app': cfg['app'], 'journal': str(path), 'tasks_started': False}


def restore(path, previous):
    # Caller has compared the entire current tree against the saved after-state.
    # Delete only these exact checked component paths, never the application.
    if exists(path):
        if path.is_dir() and not path.is_symlink():
            for child in list(path.iterdir()):
                restore(child, None)
            path.rmdir()
        else:
            path.unlink()
    if previous is None:
        return
    if previous['kind'] == 'dir':
        path.mkdir(mode=0o700)
        for child, value in previous['children'].items():
            if Path(child).name != child or child in ('.', '..'):
                raise ValueError('Invalid saved path')
            restore(path / child, value)
        path.chmod(previous['mode'])
        os.chown(str(path), previous['uid'], previous['gid'])
    elif previous['kind'] == 'link':
        os.symlink(previous['target'], str(path))
        os.lchown(str(path), previous['uid'], previous['gid'])
    else:
        raw = base64.b64decode(previous['data'].encode('ascii'), validate=True)
        if common.digest(raw) != previous['sha256']:
            raise ValueError('Backup contents changed')
        # py may have its original uid 201. The node application and this journal
        # remain root-owned; restoration never trusts an arbitrary caller path.
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
            os.fchmod(stream.fileno(), previous['mode'])
            os.fchown(stream.fileno(), previous['uid'], previous['gid'])


def rollback(cfg, check_only=False):
    common.root()
    node.idle(cfg)
    with journal_lock(cfg) as state:
        path = state / 'attachment.json'
        journal = json.loads(common.read(path, limit=64 * 1024 ** 2, private=True).decode())
        if journal['config'] != cfg:
            raise ValueError('Attachment configuration differs')
        if journal['status'] == 'rolled_back':
            return {'status': 'already_rolled_back'}
        if journal['status'] not in ('attached', 'rolling_back'):
            raise ValueError('Interrupted attachment: finish attach --resume before rollback; no guessed restoration')
        if journal['status'] == 'attached':
            node.finish(cfg, ['check', '--scheduler'], capture=True)
            if capture(cfg, False) != journal['after']:
                raise ValueError('Component changed after attachment; rollback refuses to overwrite it')
        # Validate every backup before changing anything.
        def validate(value):
            if isinstance(value, dict):
                if value.get('kind') == 'file' and common.digest(base64.b64decode(value['data'])) != value['sha256']:
                    raise ValueError('Backup checksum differs')
                for child in value.values():
                    validate(child)
        validate(journal['before'])
        if check_only:
            return {'status': 'checked', 'action': 'rollback', 'logs_and_history_retained': True}
        journal.update(status='rolling_back')
        common.save(path, journal)
        for target in reversed(targets(cfg)):
            key = str(target)
            if key in journal.get('restored', []):
                if record(target, False) != without_data(journal['before'][key]):
                    raise ValueError('Restored component changed; retained')
                continue
            actual = record(target, False)
            operation = journal.setdefault('restore_operations', {}).get(key)
            if not operation:
                if actual != journal['after'][key]:
                    raise ValueError('Component changed during rollback: ' + key)
                # Stage in the target's own filesystem, then retain the displaced
                # new version. No recursive removal of live component trees.
                temporary = Path(tempfile.mkdtemp(prefix='.ocrun-restore-', dir=str(target.parent)))
                restore(temporary / 'previous', journal['before'][key])
                operation = {'directory': str(temporary), 'state': 'prepared'}
                journal['restore_operations'][key] = operation
                common.save(path, journal)
            temporary = Path(operation['directory'])
            if temporary.parent != target.parent or not temporary.name.startswith('.ocrun-restore-'):
                raise ValueError('Invalid rollback staging path')
            common.directory(temporary)
            displaced = temporary / 'displaced'
            previous = without_data(journal['before'][key])
            if operation['state'] == 'prepared':
                if record(displaced, False) is None and actual == journal['after'][key]:
                    if exists(target):
                        os.rename(str(target), str(displaced))
                        common.sync_directory(target.parent)
                elif actual is not None or record(displaced, False) != journal['after'][key]:
                    raise ValueError('Rollback displacement differs; retained for inspection')
                operation['state'] = 'displaced'
                common.save(path, journal)
            if exists(temporary / 'previous'):
                if record(temporary / 'previous', False) != previous or exists(target):
                    raise ValueError('Staged rollback data changed')
                os.rename(str(temporary / 'previous'), str(target))
                common.sync_directory(target.parent)
            if record(target, False) != previous:
                raise ValueError('Rollback result differs')
            journal.setdefault('restored', []).append(key)
            common.save(path, journal)
        py = Path(cfg['app']) / 'py'
        meta = journal['before']['py_metadata']
        os.chown(str(py), meta['uid'], meta['gid']); py.chmod(meta['mode'])
        journal.update(status='rolled_back')
        common.save(path, journal)
        return {'status': 'rolled_back', 'logs_and_history_retained': True,
                'old_scheduler_started': False, 'backup': str(path)}
