"""Import terminal native-node history from a verified pre-0.3 detach backup.

No old program is executed, no task is claimed, and no result file is moved or
rewritten. Original state JSON is retained alongside its hash as provenance.
"""
import fcntl
import contextlib
import io
import json
import os
from pathlib import Path
import re
from types import SimpleNamespace

import common

OLD_DATA = Path('/var/lib/ocrun-node')
OLD_APP = OLD_DATA / 'app'
OLD_LOGS = Path('/var/log/ocrun-node')
CASE = re.compile(r'[0-9a-f]{32}\Z')


def inspect_backup(source, hostname):
    source = Path(source).absolute()
    if source.parent != OLD_DATA or not source.name.startswith('detached-'):
        raise ValueError('Use the detached-* backup printed by the previous bits-node detach command')
    common.directory(source)
    config = common.load(source / 'native.json', private=True)
    if (config.get('version') not in ('0.2.2', '0.2.3', '0.2.4', '0.2.5')
            or config.get('app') != str(OLD_APP) or config.get('node') != hostname):
        raise ValueError('Unsupported prior native-node version or identity')
    connection = common.read(source / 'connection.json', private=True)
    if common.digest(connection) != config.get('connection_sha256'):
        raise ValueError('Prior private connection was modified')
    if not config.get('files'):
        raise ValueError('Missing prior managed-file inventory')
    for name, expected in config['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Unsafe prior file inventory')
        if common.digest(common.read(source / 'app' / name)) != expected:
            raise ValueError('Detached program was modified: ' + name)
    states = []
    state_dir = common.directory(source / 'app/.mon-sensors-finish')
    for path in sorted(state_dir.glob('*.json')):
        if not CASE.fullmatch(path.stem):
            continue
        raw = common.read(path, private=True)
        state = json.loads(raw.decode('utf-8'))
        if (state.get('schema') != 'mon-sensors-finish-v1' or state.get('case') != path.stem
                or state.get('app') != str(OLD_APP)):
            raise ValueError('Prior batch identity differs: ' + path.stem)
        if state.get('stage') not in ('complete', 'closed_incomplete'):
            raise ValueError('Resolve unfinished batch in the previous installation: ' + path.stem)
        mon = Path(state['mon'])
        if mon.parent != OLD_LOGS or '..' in mon.parts or not mon.name.endswith('.mon'):
            raise ValueError('Prior batch log lies outside its expected directory')
        if state['stage'] == 'complete' and (not state.get('artifacts') or not state.get('receipt')):
            raise ValueError('Completed prior batch lacks delivery evidence')
        for section in ('artifacts', 'receipt'):
            for name, expected in state.get(section, {}).items():
                if Path(name).name != name or '..' in name:
                    raise ValueError('Unsafe prior result filename')
                actual = common.fingerprint(OLD_LOGS / name)
                if actual['sha256'] != expected['sha256'] or actual['bytes'] != expected['bytes']:
                    raise ValueError('Prior result differs from sealed evidence: ' + name)
        states.append((path.name, state, raw))
    return config, states


def planned_states(node, source, states):
    planned = []
    for name, old, raw in states:
        adapted = dict(old, app=str(node.APP), migration={
            'source': str(Path(source) / 'app/.mon-sensors-finish' / name),
            'source_sha256': common.digest(raw), 'results_rewritten': False})
        target = node.APP / 'state' / name
        evidence = node.DATA / 'migration-history' / (Path(name).stem + '.' + common.digest(raw) + '.json')
        if target.exists() or target.is_symlink():
            if common.load(target, private=True) != adapted:
                raise ValueError('Batch ID collision; existing history retained: ' + name)
        if evidence.exists() or evidence.is_symlink():
            if common.read(evidence, private=True) != raw:
                raise ValueError('Migration provenance differs')
        planned.append((target, adapted, evidence, raw))
    return planned


def migrate(node, source, check):
    node.active_scheduler()
    config, states = inspect_backup(source, node.socket.gethostname())
    planned_states(node, source, states)
    options = SimpleNamespace(config=str(Path(source) / 'connection.json'),
        serial=config['serial'], keep_on=config['keep_on'], check=check)
    # Keep a single JSON response for both configure + history import.
    with contextlib.redirect_stdout(io.StringIO()):
        node.initialize(options)
    result = {'status': 'checked' if check else 'migrated', 'source': str(source),
        'cases': len(states), 'tasks_started': False, 'results_rewritten': False,
        'historical_log_directory': str(OLD_LOGS)}
    if check:
        return result
    # The same lock held by the scheduler prevents execution during import.
    lock_path = node.DATA / '.run.lock'
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        common.read(lock_path, private=True)
        if (os.fstat(fd).st_dev, os.fstat(fd).st_ino) != (lock_path.lstat().st_dev, lock_path.lstat().st_ino):
            raise ValueError('Migration lock changed')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        node.active_scheduler()
        common.mkdir(node.DATA / 'migration-history')
        common.directory(node.APP / 'state')
        # Validate again after initialization and before any imported state.
        second_config, second_states = inspect_backup(source, node.socket.gethostname())
        if second_config != config or second_states != states:
            raise ValueError('Prior installation changed during migration; retained')
        planned = planned_states(node, source, states)
        for target, adapted, evidence, raw in planned:
            if not evidence.exists():
                common.write(evidence, raw)
            if not target.exists():
                common.save(target, adapted)
        common.save(node.DATA / 'migration.json', dict(result, source_version=config['version']))
    finally:
        os.close(fd)
    return result
