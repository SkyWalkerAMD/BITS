"""Native packages plus real authenticated services; only sensor hardware is simulated."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import time
from types import SimpleNamespace
import urllib.request
import zipfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
    raise SystemExit('Disposable cloud Linux root only')
NODE = Path('/opt/bits/node/0.3.0')
CENTER = Path('/opt/bits/center/0.3.0')
APP = Path('/var/lib/bits/node/app')
checks = []


def run(*argv, **kwargs):
    result = subprocess.run([str(v) for v in argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=kwargs.get('timeout', 120))
    if kwargs.get('good', True) and result.returncode:
        raise AssertionError('{}: {} {}'.format(argv[0], result.stdout.decode('utf-8', 'replace')[-1000:], result.stderr.decode('utf-8', 'replace')[-3000:]))
    return result


def data(*argv):
    return json.loads(run(*argv).stdout.decode())


def passed(name):
    checks.append(name)
    print('PASS ' + name, flush=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migration_check(extension, package, private, redis, tasks, host, address):
    """Use published 0.2.5 bytes, not a reconstructed old installation."""
    installer = ['rpm', '-U'] if extension == '.rpm' else ['dpkg', '-i']
    remover = ['rpm', '-e', 'bits-node'] if extension == '.rpm' else ['dpkg', '-r', 'bits-node']
    old_package = next(Path('/src/previous-native025').glob('*' + extension))
    run(*remover)
    run(*(installer + [old_package]))
    run('bits-node', 'configure', '--config', private, '--serial', 'CLOUD-SERIAL', '--keep-on')
    tasks.delete(redis, host, 'NEXT')
    tasks.add(redis, host, 'MIGRATE-025', 'cloud', ['stress=3'])
    old_app = Path('/var/lib/ocrun-node/app')
    run(old_app / 'mon-sensors-finish', 'run-queue', '--rdb-server', address,
        '--log-server', address, '--log-dir', '/var/log/ocrun-node', '--node', host,
        '--serial', 'CLOUD-SERIAL')
    old = data('bits-node', 'status')['cases'][0]
    assert old['stage'] == 'complete'
    artifacts = dict(old['artifacts'], **old['receipt'])
    originals = {name: sha(Path('/var/log/ocrun-node') / name) for name in artifacts}
    detached = data('bits-node', 'detach')
    backup = Path(detached['backup'])
    source = backup / 'app/.mon-sensors-finish' / (old['case'] + '.json')
    state_bytes = source.read_bytes()
    run(*remover)
    run(*(installer + [package]))
    command = ['bits-node', 'migrate', '--from', str(backup)]
    # Failure must leave both the old evidence and new deployment untouched.
    source.write_text(json.dumps(dict(old, stage='collecting')))
    assert run(*(command + ['--check']), good=False).returncode
    assert not Path('/etc/bits/node/native.json').exists()
    source.write_bytes(state_bytes)
    mon = Path(old['mon'])
    mon_bytes = mon.read_bytes()
    mon.write_bytes(mon_bytes + b'corrupt fixture\n')
    assert run(*(command + ['--check']), good=False).returncode
    assert not Path('/etc/bits/node/native.json').exists()
    mon.write_bytes(mon_bytes)
    assert data(*(command + ['--check']))['cases'] == 1
    assert not Path('/etc/bits/node/native.json').exists()
    assert data(*command)['status'] == 'migrated'
    assert data(*command)['cases'] == 1
    imported = data('bits-node', 'status', '--case', old['case'])['cases'][0]
    assert imported['stage'] == 'complete' and imported['app'] == str(APP)
    assert imported['artifacts'] == old['artifacts']
    assert imported['migration']['source_sha256'] == hashlib.sha256(state_bytes).hexdigest()
    assert source.read_bytes() == state_bytes
    run('bits-node', 'retry', '--case', old['case'])
    assert originals == {name: sha(Path('/var/log/ocrun-node') / name) for name in artifacts}
    imported_path = APP / 'state' / source.name
    imported_bytes = imported_path.read_bytes()
    imported_path.write_text(json.dumps(dict(imported, task_id='COLLISION')))
    assert run(*(command + ['--check']), good=False).returncode
    assert run(*command, good=False).returncode
    assert json.loads(imported_path.read_text())['task_id'] == 'COLLISION'
    imported_path.write_bytes(imported_bytes)
    passed('published 0.2.5 history migration rejects unfinished/corrupt evidence and collisions; repeat import/retry keeps sealed bytes')
    run('bits-node', 'detach')
    run(*remover)
    run(*(installer + [old_package]))
    # Explicit rollback from the retained detach backup in this disposable VM.
    shutil.copytree(str(backup / 'app'), str(old_app))
    for name in ('native.json', 'connection.json'):
        shutil.copy2(str(backup / name), '/etc/ocrun-node/' + name)
    run('bits-node', 'check')
    assert data('bits-node', 'status', '--case', old['case'])['cases'][0]['artifacts'] == old['artifacts']
    assert originals == {name: sha(Path('/var/log/ocrun-node') / name) for name in artifacts}
    run('bits-node', 'detach')
    run(*remover)
    run(*(installer + [package]))
    passed('rollback to the real 0.2.5 package restores its managed app and configuration without rewriting reports')


def main():
    address = sys.argv[1]
    for root in (NODE, CENTER):
        assert json.loads((root / 'PACKAGE.json').read_text())['source_commit'] == os.environ['OCRUN_SOURCE_COMMIT']
    assert data('bits-center', 'check')['status'] == 'not_configured'
    assert data('bits-node', 'check')['status'] == 'not_configured'
    assert not Path('/etc/bits/center/server.json').exists()
    assert not APP.exists()
    passed('native package installation has matching revision and does not configure services or start tasks')
    tools = data('/usr/libexec/bits-workloads', 'list')
    assert tools['package_revision'] == '4'
    assert tools['tools']['mlc'] == {'version': '3.13', 'delivery': 'included'}
    mlc = Path('/opt/bits/workloads/0.1.0/mlc/mlc')
    version = run(mlc, '-h', good=False)
    assert version.returncode in (0, 1) and b'3.13' in version.stdout + version.stderr
    assert not Path('/var/lib/ocrun-workloads/mlc-3.13').exists()
    passed('native node includes usable MLC 3.13 without an external import')
    # Selection rules run on every target Python, including EL8 Python 3.6.
    run(sys.executable, '-I', '-B', '-c',
        "import sys,unittest;sys.path.insert(0,'/src');unittest.main(module='distribution.test_network',verbosity=2)")
    before_network = run('ip', '-j', '-4', 'address', 'show').stdout
    candidates = data('bits-center', 'network', '--json')
    assert candidates['read_only'] and not candidates['nic_changes']
    usable = [entry for entry in candidates['interfaces'] if entry['eligible']]
    assert len(usable) == 1 and usable[0]['address'] == address
    discovered = json.loads(run('bits-center', 'setup', '--auto', '--skip-deps', '--check').stdout.decode().splitlines()[-1])
    assert discovered['address'] == address and discovered['network'] == usable[0]['network']
    assert not Path('/etc/bits/center/server.json').exists()
    # A second private interface makes automatic selection ambiguous. Applying
    # must fail before writing BITS configuration or starting any service.
    run('ip', 'link', 'add', 'bitscheck0', 'type', 'dummy')
    try:
        run('ip', 'address', 'add', '192.168.253.10/24', 'dev', 'bitscheck0')
        run('ip', 'link', 'set', 'bitscheck0', 'up')
        ambiguous = run('bits-center', 'setup', '--auto', '--skip-deps', '--apply', good=False)
        assert ambiguous.returncode and b'Multiple network candidates' in ambiguous.stderr
        assert not Path('/etc/bits/center/server.json').exists()
        selected = json.loads(run('bits-center', 'setup', '--auto', '--interface', usable[0]['interface'], '--skip-deps', '--check').stdout.decode().splitlines()[-1])
        assert selected['address'] == address
    finally:
        run('ip', 'link', 'delete', 'bitscheck0')
    assert run('ip', '-j', '-4', 'address', 'show').stdout == before_network
    passed('automatic network discovery, multi-NIC refusal, explicit selection and read-only preflight preserve NIC configuration')
    command = ['bits-center', 'setup', '--auto', '--network', address + '/32', '--skip-deps']
    run(*(command + ['--check']))
    assert not Path('/etc/bits/center/server.json').exists()
    run(*(command + ['--apply']))
    assert data('bits-center', 'check')['status'] == 'ok'
    packages = [json.loads(line) for line in run('bits-center', 'publish-node').stdout.decode().splitlines()]
    assert len(packages) == 2
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    for item in packages:
        assert hashlib.sha256(opener.open(item['url'], timeout=15).read()).hexdigest() == item['sha256']
    passed('native center activates real systemd services and publishes both complete node package formats by exact hash')
    sys.path.insert(0, str(CENTER / 'server'))
    from bits_core.center import safe, tasks
    from bits_core.center.wire import Redis, ProtocolError
    config = safe.load('/etc/bits/center/server.json')
    redis = Redis(password=config['password'])
    try:
        Redis(host=address).call('PING')
        raise AssertionError('Unauthenticated database access')
    except ProtocolError:
        pass
    host = socket.gethostname()
    private = Path('/root/native-connection.json')
    run('bits-center', 'node-config', '--node', host, '--output', private)
    assert private.stat().st_mode & 0o777 == 0o600
    passed('protected database and private node credential export remain active')
    configure = ['bits-node', 'configure', '--config', str(private), '--serial', 'CLOUD-SERIAL', '--keep-on']
    Path('/root/ocrun').mkdir()
    original = Path('/root/ocrun/ocb')
    original.write_text('original preserved\n')
    assert run(*(configure + ['--check']), good=False).returncode != 0
    assert original.read_text() == 'original preserved\n'
    original.unlink()
    original.parent.rmdir()
    assert run(*(configure + ['--check'])).returncode == 0
    assert not APP.exists()
    native_spec = importlib.util.spec_from_file_location('native_fixture', str(NODE / 'node.py'))
    native = importlib.util.module_from_spec(native_spec)
    native_spec.loader.exec_module(native)
    rename = os.rename
    def interrupted_setup(source, target):
        rename(source, target)
        if str(target) == str(APP):
            raise OSError('simulated setup interruption after application placement')
    os.rename = interrupted_setup
    try:
        try:
            native.initialize(SimpleNamespace(config=str(private), serial='CLOUD-SERIAL', keep_on=True, check=False))
            raise AssertionError('Setup interruption missing')
        except OSError as error:
            assert 'simulated' in str(error)
    finally:
        os.rename = rename
    assert (APP.parent / 'setup.json').exists()
    run(*configure)
    before = sha(Path('/etc/bits/node/native.json'))
    run(*configure)
    assert sha(Path('/etc/bits/node/native.json')) == before
    run('bits-node', 'check')
    assert not (APP / 'mon-sensors').exists()
    assert not (APP / 'mon-sensors-plugin').exists()
    assert (APP / 'collect').is_file()
    assert run(APP / 'collect', '--once', good=False).returncode != 0
    passed('native BITS uses a private batch worker with no mon-sensors command or interactive hardware viewer')
    passed('original node protection, read-only setup, interrupted setup recovery and repeatable explicit tool binding')
    tasks.add(redis, host, 'TYPO', 'cloud', ['stress=3'])
    db = int(redis.call('GET', host))
    redis.call('LSET', 'TASKS', 0, 'strss', db=db)
    redis.call('SET', 'strss', 3, db=db)
    invalid = run('bits-node', 'preflight', good=False)
    assert invalid.returncode != 0 and b'strss' in invalid.stderr
    assert redis.call('LRANGE', 'TASKS', 0, -1, db=db) == ['strss']
    tasks.delete(redis, host, 'TYPO')
    passed('invalid original-protocol queue entry rejected before taking the task')
    # Clearly synthetic data used only in a disposable cloud fixture. Never shipped.
    payload = {'schema': 'sckocp-mon-v1', 'version': '1.2.0', 'vendor': 'GenuineIntel', 'family': 6,
               'interval_s': 1, 'sockets': [{'id': 0, 'tjmax_c': 100, 'temp_max_c': 55, 'vid_v': 1.1,
               'core_mhz': 3000, 'base_mhz': 2500, 'pkg_w': 120}], 'cores': [{'cpu': 0, 'socket': 0,
               'mhz': 3000, 'temp_c': 55, 'vid_v': 1.1, 'c0_pct': 100, 'c6_pct': 0}]}
    provider = Path('/usr/local/bin/sckocp')
    sys.path.insert(0, '/src/tests')
    sys.path.insert(0, '/src')
    from test_sckocp_details import INFO, OVERVIEW
    provider.write_text('#!' + sys.executable + '\nimport time,sys\ntime.sleep(.05)\nprint(' +
        repr(INFO) + ' if sys.argv[1:]==["info"] else ' + repr(OVERVIEW) +
        ' if sys.argv[1:]==["mon","--cols=1"] else ' + repr(json.dumps(payload)) + ')\n')
    provider.chmod(0o700)
    tasks.add(redis, host, 'MLC-PREFLIGHT', 'cloud', ['mlc=60'])
    mlc_check = data('bits-node', 'preflight')
    assert mlc_check['tasks'][0]['binary'] == str(mlc)
    assert mlc_check['tasks'][0]['tool_version'] == '3.13'
    assert redis.call('LRANGE', 'TASKS', 0, -1, db=db) == ['mlc']
    tasks.delete(redis, host, 'MLC-PREFLIGHT')
    passed('installed node selects verified bundled MLC in preflight without claiming the queue')
    tasks.add(redis, host, 'NATIVE-BATCH', 'cloud', ['stress=3', 'stress-ng=3'])
    assert data('bits-node', 'preflight')['status'] == 'checked'
    started = data('bits-node', 'start')
    assert started['automatic_task_polling'] is False
    assert run('bits-node', 'start', good=False).returncode != 0
    until = time.monotonic() + 150
    case = None
    while time.monotonic() < until:
        values = data('bits-node', 'status')['cases']
        if values:
            case = values[0]
            if case['stage'] == 'complete':
                break
            if case.get('error'):
                log = APP / 'state' / ('run-' + case['case']) / 'collector.log'
                detail = log.read_bytes()[-4096:].decode('utf-8', 'replace') if log.exists() else ''
                raise AssertionError(case['error'] + '\n' + detail)
        time.sleep(1)
    else:
        raise AssertionError('Native batch did not complete: ' + Path(started['log']).read_text())
    assert case['execution_result'] == 'completed'
    assert [s['name'] for s in case['steps']] == ['stress', 'stress-ng']
    assert all(s['cleanup_confirmed'] and s['execution'] == 'duration_reached' for s in case['steps'])
    assert case['data_quality'] == 'readings_reported_validity_unknown'
    passed('explicit single batch with real packaged stress and stress-ng, bounded duration and cleanup')
    files = dict(case['artifacts'], **case['receipt'])
    remote = Path('/data/cds/result') / (host + '_CLOUD-SERIAL')
    for name, receipt in files.items():
        assert sha(remote / name) == receipt['sha256']
        run('su', '-s', '/bin/sh', 'bits', '-c', 'test -r ' + str(remote / name))
    assert len(files) == 6
    acceptance = json.loads(Path(case['mon']).with_suffix('.report.json').read_text())
    assert acceptance['schema'] == 'ocrun-acceptance-report-v1'
    assert acceptance['case'] == case['case']
    assert acceptance['machine']['capture_phase'] == 'batch_start'
    assert acceptance['statistics']['metrics']['vrm_temp_c']['mean'] is None
    assert acceptance['hardware_acceptance'] == 'not_automatically_assessed'
    assert all(s['binary_sha256'] and s['tool_version'] != 'unknown' for s in acceptance['steps'])
    checked = data('bits-center', 'results', 'verify', '--receipt',
                   (host + '_CLOUD-SERIAL/') + Path(case['mon']).with_suffix('.finish.json').name, '--json')
    assert checked['verified_here'] and checked['html_report_path'].endswith('.report.html')
    receipt_name = (host + '_CLOUD-SERIAL/') + Path(case['mon']).with_suffix('.finish.json').name
    reader = json.loads(run('su', '-s', '/bin/sh', 'bits', '-c',
        'bits-center results verify --receipt ' + receipt_name + ' --json').stdout.decode())
    assert reader['verified_here'] and reader['html_report_path'] == checked['html_report_path']
    assert run('su', '-s', '/bin/sh', 'nobody', '-c',
        'bits-center results verify --receipt ' + receipt_name + ' --json', good=False).returncode
    for command in ('rollback', 'publish-node'):
        assert run('su', '-s', '/bin/sh', 'bits', '-c', 'bits-center ' + command, good=False).returncode
    passed('management account verifies results without sudo; unauthorized reader and service mutations remain denied')
    workbook = Path(case['mon']).with_suffix('.xlsx')
    with zipfile.ZipFile(str(workbook)) as z:
        assert z.testzip() is None
        assert 'xl/worksheets/sheet1.xml' in z.namelist()
        text = z.read('xl/worksheets/sheet1.xml').decode()
        assert '<c r="H2"' not in text  # Missing VRM stays blank.
    passed('streaming workbook and detailed acceptance HTML/JSON, six hashes, center reader and management account access')
    snapshot = {name: sha(remote / name) for name in files}
    run('bits-node', 'retry', '--case', case['case'])
    assert snapshot == {name: sha(remote / name) for name in files}
    time.sleep(1)
    run('bits-node', 'stop-scheduler')
    passed('repeat finalization retains confirmed data bytes and scheduler stops without name-based process killing')
    tasks.delete(redis, host, 'NATIVE-BATCH')
    tasks.add(redis, host, 'NEXT', 'cloud', ['stress=3'])
    time.sleep(2)
    db = int(redis.call('GET', host))
    assert redis.call('LRANGE', 'TASKS', 0, -1, db=db) == ['stress']
    assert len(data('bits-node', 'status')['cases']) == 1
    passed('completed idle node does not claim a newly added batch')
    extension = '.rpm' if shutil.which('rpm') else '.deb'
    package = next(Path('/root/native-packages').rglob('bits-node*' + extension))
    reinstall = ['rpm', '-U', '--replacepkgs', str(package)] if extension == '.rpm' else ['dpkg', '-i', str(package)]
    assert run(*reinstall, good=False).returncode != 0
    assert snapshot == {name: sha(remote / name) for name in files}
    passed('configured native node refuses package replacement and retains evidence')
    pending = data(APP / 'batch', 'begin', '--mon',
                   '/var/log/bits/node/' + host + '_CLOUD-SERIAL_PENDING_cloud.mon',
                   '--remote', address + '::logs/' + host + '_CLOUD-SERIAL', '--task-id', 'PENDING', '--task-time', 'cloud')
    assert run('bits-node', 'detach', good=False).returncode != 0
    run('bits-node', 'close-incomplete', '--case', pending['case'], '--reason', 'cloud empty fixture deliberately closed')
    assert data('bits-node', 'status', '--case', pending['case'])['cases'][0]['stage'] == 'closed_incomplete'
    def interrupted_detach(source, target):
        rename(source, target)
        if str(source) == str(APP):
            raise OSError('simulated detach interruption after application move')
    os.rename = interrupted_detach
    try:
        try:
            native.detach(False)
            raise AssertionError('Detach interruption missing')
        except OSError as error:
            assert 'simulated' in str(error)
    finally:
        os.rename = rename
    detached = data('bits-node', 'detach')
    assert Path(detached['backup']).is_dir()
    assert all((Path('/var/log/bits/node') / n).exists() for n in files)
    passed('unfinished batches block detach; explicit closure and interrupted detach recovery preserve data')
    changed = NODE / 'MANUAL.md'
    original = changed.read_bytes()
    original_stat = changed.stat()
    changed.write_bytes(original + b'\nchanged\n')
    assert run(*reinstall, good=False).returncode != 0
    remove_node = ['rpm', '-e', 'bits-node'] if extension == '.rpm' else ['dpkg', '-r', 'bits-node']
    assert run(*remove_node, good=False).returncode != 0
    assert changed.read_bytes() == original + b'\nchanged\n'
    changed.write_bytes(original)
    os.utime(str(changed), ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    if extension == '.deb':
        if run('dpkg-query', '-W', '-f=${db:Status-Status}', 'bits-node').stdout.decode() != 'installed':
            run('dpkg', '--configure', 'bits-node')
    run(*reinstall)
    passed('modified native files refuse replacement and removal; restored known package can reinstall after detach')
    entry = Path('/usr/bin/bits-node')
    entry_mode = entry.stat().st_mode & 0o7777
    entry.chmod(entry_mode | 0o020)
    assert run(*reinstall, good=False).returncode != 0
    assert run(*remove_node, good=False).returncode != 0
    assert entry.stat().st_mode & 0o020
    entry.chmod(entry_mode)
    if extension == '.deb':
        if run('dpkg-query', '-W', '-f=${db:Status-Status}', 'bits-node').stdout.decode() != 'installed':
            run('dpkg', '--configure', 'bits-node')
    passed('package changes refuse a writable command entry even when its content checksum matches')
    migration_check(extension, package, private, redis, tasks, host, address)
    run('bits-center', 'rollback')
    if extension == '.rpm':
        run('rpm', '-e', 'bits-node', 'bits-center')
    else:
        run('dpkg', '-r', 'bits-node', 'bits-center')
    assert not Path('/usr/bin/bits-node').exists()
    assert not Path('/usr/bin/bits-center').exists()
    assert all((Path('/var/log/bits/node') / n).exists() for n in files)
    passed('native removal after explicit detach and center rollback retains task results')
    # Install actual published v0.2.2, then upgrade the detached roles to this build.
    old_packages = sorted(Path('/src/previous-native').glob('*' + extension))
    new_packages = sorted(Path('/root/native-packages').rglob('*' + extension))
    assert len(old_packages) == len(new_packages) == 2
    installer = ['rpm', '-U'] if extension == '.rpm' else ['dpkg', '-i']
    run(*(installer + old_packages))
    old_node = Path('/opt/ocrun-node/0.2.2')
    assert json.loads((old_node / 'PACKAGE.json').read_text())['version'] == '0.2.2'
    # Unknown files in a new version directory are never treated as owned by the old package.
    NODE.mkdir()
    sentinel = NODE / 'unmanaged.txt'
    sentinel.write_text('preserve unknown content')
    assert run(*(installer + [package]), good=False).returncode != 0
    assert sentinel.read_text() == 'preserve unknown content'
    sentinel.unlink()
    NODE.rmdir()
    changed = old_node / 'MANUAL.md'
    content, attributes = changed.read_bytes(), changed.stat()
    changed.write_bytes(content + b'\nlocal change\n')
    assert run(*(installer + [package]), good=False).returncode != 0
    assert changed.read_bytes() == content + b'\nlocal change\n'
    changed.write_bytes(content)
    os.utime(str(changed), ns=(attributes.st_atime_ns, attributes.st_mtime_ns))
    # Brand migration is deliberately explicit; no native transaction silently
    # removes an attached old role. The unchanged paths preserve case history.
    remover = ['rpm', '-e'] if extension == '.rpm' else ['dpkg', '-r']
    run(*(remover + ['ocrun-node', 'ocrun-center']))
    run(*(installer + new_packages))
    assert data('bits-node', 'check')['version'] == '0.3.0'
    assert data('/usr/libexec/bits-workloads', 'list')['tools']['mlc']['delivery'] == 'included'
    assert not old_node.exists() and not Path('/opt/ocrun-center/0.2.2').exists()
    passed('published 0.2.2 migrates after explicit detach/removal; coinstallation and unmanaged paths are refused')
    # Rollback uses explicit removal and the original retained artifacts, not forced downgrade.
    remover = ['rpm', '-e'] if extension == '.rpm' else ['dpkg', '-r']
    run(*(remover + ['bits-node', 'bits-center']))
    run(*(installer + old_packages))
    assert json.loads((old_node / 'PACKAGE.json').read_text())['version'] == '0.2.2'
    assert all((Path('/var/log/bits/node') / n).exists() for n in files)
    run(*(remover + ['ocrun-node', 'ocrun-center']))
    passed('explicit package rollback to published 0.2.2 preserves historical result files')
    Path('/results/native.json').write_text(json.dumps({'status': 'passed', 'source_commit': os.environ['OCRUN_SOURCE_COMMIT'],
        'checks': checks, 'sensor_data': 'synthetic cloud fixture; not hardware validation',
        'kernel': 'shared cloud host kernel', 'native_packages': ['bits-center', 'bits-node']}, indent=2))


if __name__ == '__main__':
    try:
        main()
    finally:
        state = APP / 'state'
        for log in list(state.glob('run-*/collector.log')) + list(state.glob('scheduler.log')):
            # Only bounded execution diagnostics, never the private connection.
            (Path('/results') / (log.parent.name + '-' + log.name)).write_bytes(log.read_bytes()[-262144:])
