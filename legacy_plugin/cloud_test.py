"""Real RPM/DEB + Redis + old ocb + tools + rsync; sensors are explicitly synthetic."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import zipfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
    raise SystemExit('Isolated cloud Linux root only')

SRC = Path('/src')
PREFIX = Path('/opt/ocrun-plugin/0.2.0')
APP = Path('/root/ocrun')
CONFIG = Path('/etc/ocrun-plugin/config.json')
checks = []


def run(*argv, **kw):
    result = subprocess.run([str(x) for x in argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=kw.get('timeout', 120), env=kw.get('env'))
    if kw.get('good', True) and result.returncode:
        raise AssertionError('{}\n{}\n{}'.format(argv, result.stdout.decode('utf-8', 'replace')[-2000:],
                                               result.stderr.decode('utf-8', 'replace')[-3000:]))
    return result


def data(*args):
    return json.loads(run(*args).stdout.decode())


def passed(text):
    checks.append(text); print('PASS ' + text, flush=True)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def install(role):
    suffix = '.rpm' if os.environ['KIND'] == 'rpm' else '.deb'
    package = next(Path('/root/plugin-packages').glob('bits-o-' + role + '*' + suffix))
    if suffix == '.rpm':
        run('rpm', '-i', package)
    else:
        run('dpkg', '-i', package)


def remove(role, good=True, legacy=False):
    return run(*(['rpm', '-e'] if os.environ['KIND'] == 'rpm' else ['dpkg', '-r']) + [('ocrun-plugin-' if legacy else 'bits-o-') + role], good=good)


def controller_config(command='bits-o'):
    # Synthetic control env is never executed. The tested shipped baseline guard
    # correctly refuses this unknown copy. Configure the fixture explicitly as
    # root for protocol/permission tests; do not alter packaged program bytes.
    control = Path('/home/ocuser/ocrun')
    control.mkdir(parents=True, exist_ok=True)
    env = control / 'oc.env'
    env.write_text('RDBSVR1=127.0.0.1\nLOGSVR1=127.0.0.1\n')
    for name in ('occ', 'oc.profile', 'oc-mgmt-task', 'version.txt'):
        (control / name).write_text('synthetic control baseline\n')
    account = pwd.getpwnam('ocuser')
    before = {p.name: sha(p) for p in control.iterdir()}
    assert run(command, 'configure', '--role', 'control', '--rdb-server', '127.0.0.1',
               '--allow-node', 'LEGACY-CLOUD', '--check', good=False).returncode
    assert not CONFIG.exists()
    assert before == {p.name: sha(p) for p in control.iterdir()}
    CONFIG.parent.mkdir(mode=0o755, exist_ok=True)
    CONFIG.write_text(json.dumps({'schema': 'ocrun-plugin-config-v1', 'role': 'control',
        'app': str(control), 'rdb_server': '127.0.0.1', 'rdb_port': 6379,
        'environment_sha256': sha(env), 'allowed_nodes': ['LEGACY-CLOUD'],
        'operator': 'ocuser', 'operator_uid': account.pw_uid, 'results_root': '/data/cds/result',
        'source_commit': os.environ['GITHUB_SHA']}))
    CONFIG.chmod(0o644)


def upgrade_roundtrip(config_args, remote_dir, files, original_files):
    # Use actual checksum-pinned published packages, not reconstructed fixtures.
    suffix = '.rpm' if os.environ['KIND'] == 'rpm' else '.deb'
    install_command = ['rpm', '-i'] if suffix == '.rpm' else ['dpkg', '-i']
    snapshot = {name: sha(remote_dir / name) for name in files}
    def switch_tools(legacy):
        current = 'bits-o-workloads' if legacy else 'ocrun-workloads'
        new = 'ocrun-workloads' if legacy else 'bits-o-workloads'
        directory = Path('/root/plugin-packages/ci/previous' if legacy else '/root/plugin-packages')
        run(*(['rpm', '-e'] if suffix == '.rpm' else ['dpkg', '-r']) + [current])
        run(*(install_command + [next(directory.glob(new + '*' + suffix))]))
    for role in ('control', 'node'):
        old = next(Path('/root/plugin-packages/ci/previous').glob('ocrun-plugin-' + role + '*' + suffix))
        new = next(Path('/root/plugin-packages').glob('bits-o-' + role + '*' + suffix))
        if role == 'node':
            switch_tools(True)
        run(*(install_command + [old]))
        assert json.loads(Path('/opt/ocrun-plugin/0.1.1/PACKAGE.json').read_text())['version'] == '0.1.1'
        if role == 'node':
            run(*(['ocrun-plugin'] + config_args[1:]))
            run('ocrun-plugin', 'attach')
        else:
            controller_config('ocrun-plugin')
        replacement = ['rpm', '-U', str(new)] if suffix == '.rpm' else ['dpkg', '-i', str(new)]
        assert run(*replacement, good=False).returncode
        assert CONFIG.exists() and snapshot == {name: sha(remote_dir / name) for name in files}
        if role == 'node':
            run('ocrun-plugin', 'rollback')
        run('ocrun-plugin', 'unconfigure')
        remove(role, legacy=True)
        if role == 'node':
            switch_tools(False)
        install(role)
        if role == 'node':
            run(*config_args)
            run('bits-o', 'attach', '--check')
            run('bits-o', 'attach')
            run('bits-o', 'check')
            run('bits-o', 'rollback')
            for name, expected in original_files.items():
                assert sha(APP / name) == expected
        else:
            controller_config()
            # The synthetic control copy deliberately fails the real 215
            # baseline check; exercise the configured protocol without
            # weakening that guard or pretending the fixture is the original.
            run('bits-o', 'status', '--json')
        run('bits-o', 'unconfigure')
        remove(role)
        # Explicit package rollback keeps history and restores the old command.
        if role == 'node':
            switch_tools(True)
        run(*(install_command + [old]))
        run('ocrun-plugin', '--version')
        remove(role, legacy=True)
        if role == 'node':
            switch_tools(False)
        assert snapshot == {name: sha(remote_dir / name) for name in files}
    passed('published 0.1.1 control/node migrate to BITS-o after explicit rollback/removal; package rollback preserves original files and six artifacts')


def main():
    spec = json.loads((PREFIX / 'PACKAGE.json').read_text())
    assert spec['source_commit'] == os.environ['GITHUB_SHA'] and spec['role'] == 'control'
    assert not CONFIG.exists() and not APP.exists()
    run('useradd', '-m', 'ocuser')
    account = pwd.getpwnam('ocuser')
    remote = Path('/data/cds/result')
    remote.mkdir(parents=True)
    os.chown(str(remote), account.pw_uid, account.pw_gid)
    server = shutil.which('redis-server') or shutil.which('valkey-server')
    redis = subprocess.Popen([server, '--bind', '127.0.0.1', '--port', '6379', '--databases', '16',
                              '--save', '', '--appendonly', 'no'], stdout=subprocess.DEVNULL)
    sys.path.insert(0, str(SRC))
    from bits_core.center.wire import Redis
    from legacy_plugin import tasks
    rdb = Redis()
    for unused in range(100):
        try:
            rdb.call('PING'); break
        except OSError:
            time.sleep(.05)
    controller_config()
    passed('installed control package has exact source; unknown original files refused without changes')
    base = ['bits-o', 'tasks', 'add', '--node', 'LEGACY-CLOUD', '--id', 'PLUGIN-CLOUD']
    assert run(*(base + ['--task', 'strss=3']), good=False).returncode
    assert rdb.call('DBSIZE') == 0
    rdb.call('SET', 'UNRELATED', 1)
    assert run(*(base + ['--task', 'stress=3', '--check'])).returncode == 0
    assert rdb.call('GET', 'LEGACY-CLOUD') is None
    queued = data(*(base + ['--task', 'stress=3', '--task', 'stress-ng=3', '--task', 'stress=3', '--json']))
    assert queued['db'] == 2
    run('su', '-s', '/bin/sh', 'ocuser', '-c', 'bits-o tasks status --node LEGACY-CLOUD --json')
    overview = json.loads(run('su', '-s', '/bin/sh', 'ocuser', '-c', 'bits-o status --json').stdout.decode())
    assert overview['batches'][0]['id'] == 'PLUGIN-CLOUD'
    assert run('su', '-s', '/bin/sh', 'nobody', '-c', 'bits-o tasks status --node LEGACY-CLOUD', good=False).returncode
    assert run('bits-o', 'tasks', 'status', '--node', 'PRODUCTION', good=False).returncode
    assert run(*base, '--task', 'stress=3', good=False).returncode
    passed('control typo/dry-run/duplicate/allowlist/account guards; mapped empty DB reserved; repeated task accepted')
    assert remove('control', good=False).returncode
    run('bits-o', 'unconfigure')
    changed = PREFIX / 'MANUAL.md'
    original_stat = changed.stat()
    original_manual = changed.read_bytes()
    changed.write_bytes(original_manual + b'\ncloud manual change\n')
    assert remove('control', good=False).returncode
    changed.write_bytes(original_manual)
    os.utime(str(changed), ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
    remove('control')
    install('node')
    for directory in (APP, APP / 'py', Path('/root/log')):
        directory.mkdir(parents=True, exist_ok=True)
    for name in ('mon-sensors', 'ocb', 'oct', 'mon-analyse-log.py'):
        target = APP / ('py/' + name if name.endswith('.py') else name)
        shutil.copyfile(str(SRC / 'integrations/mon-sensors/upstream-0.9.24a' / name), str(target))
        target.chmod(0o755); os.chown(str(target), 201, 200)
    os.chown(str(APP / 'py'), 201, 200)
    (APP / 'mon-analyse-log').symlink_to('py/mon-analyse-log.py')
    (APP / 'version.txt').write_text('MAIN_VERSION=0.9.24a\nDEV_VERSION=0.9.24a\n')
    (APP / 'oc.env').write_text('RDBSVR1=127.0.0.1\nLOGSVR1=127.0.0.1\n'
        'RDBSVR=$RDBSVR1\nLOGSVR=$LOGSVR1\nAPPPATH=/root/ocrun\nLOGPATH=/root/log\n'
        'MB_SN=CLOUD-SERIAL\nHOSTNAME=LEGACY-CLOUD\nMEM_FREE=1024\nAPP_DATE=cloud\n')
    os.chown(str(APP / 'oc.env'), 201, 200)
    # Clearly synthetic monitoring; never shipped and never modifies sckocp activation.
    payload = {'schema': 'sckocp-mon-v1', 'version': '1.2.0', 'vendor': 'GenuineIntel', 'family': 6,
        'interval_s': 1, 'sockets': [{'id': 0, 'tjmax_c': 100, 'temp_max_c': 55, 'vid_v': 1.1,
        'core_mhz': 3000, 'base_mhz': 2500, 'pkg_w': 120}], 'cores': [{'cpu': 0, 'socket': 0,
        'mhz': 3000, 'temp_c': 55, 'vid_v': 1.1, 'c0_pct': 100, 'c6_pct': 0}]}
    sensor = Path('/usr/local/bin/sckocp')
    sys.path.insert(0, str(SRC / 'tests'))
    from sckocp_detail_fixture import INFO, OVERVIEW
    sensor.write_text('#!' + sys.executable + '\nimport time,sys\ntime.sleep(.05)\nprint(' +
        repr(INFO) + ' if sys.argv[1:]==["info"] else ' + repr(OVERVIEW) +
        ' if sys.argv[1:]==["mon","--cols=1"] else ' + repr(json.dumps(payload)) + ')\n')
    sensor.chmod(0o700)
    config_args = ['bits-o', 'configure', '--role', 'node', '--rdb-server', '127.0.0.1', '--serial', 'CLOUD-SERIAL']
    run('bits-o', 'setup', '--serial', 'CLOUD-SERIAL', '--check')
    run(*(config_args + ['--check']))
    assert not CONFIG.exists()
    run(*config_args)
    field_upgrade = len(sys.argv) > 1 and sys.argv[1] == 'field-upgrade'
    if field_upgrade:
        assert sys.version_info[:2] == (3, 6)
        old = Path('/root/plugin-packages/ci')
        run('bash', old / 'mon-sensors-plugin-0.12.9.run', '--app', APP, '--backend', 'sckocp', '--adopt-original')
        run('bash', old / 'mon-sensors-report-py36-0.2.0.run', '--app', APP)
        run('bash', old / 'mon-sensors-finish-0.2.1.run', '--app', APP)
        assert b'0.2.1' in run(APP / 'mon-sensors-finish', '--version').stdout
        passed('prepared exact field component versions: collector0.12.9 report0.2.0 finish0.2.1 on Python3.6')
    before = {name: sha(APP / name) for name in ('ocb', 'oct', 'mon-sensors', 'py/mon-analyse-log.py', 'oc.env')}
    run('bits-o', 'attach', '--check')
    assert not (APP / '.ocrun-plugin').exists()
    # Simulate process death after the collector atomically installed, before
    # the suite marked that step complete. No production fault injection flag.
    failure = '''import sys
sys.path.insert(0, '/opt/ocrun-plugin/0.2.0')
from legacy_plugin import attach, common
original = common.run
def interrupted(argv, *args, **kwargs):
    result = original(argv, *args, **kwargs)
    if any(str(x).endswith('install-plugin-entry.py') for x in argv) and '--check' not in argv:
        raise OSError('cloud interruption after collector installation')
    return result
common.run = interrupted
attach.apply(common.config())
'''
    assert run(sys.executable, '-I', '-B', '-c', failure, good=False).returncode
    assert json.loads((APP / '.ocrun-plugin/attachment.json').read_text())['status'] == 'failed'
    run('bits-o', 'attach', '--resume')
    run('bits-o', 'attach')
    run('bits-o', 'check')
    run('bits-o', 'setup', '--serial', 'CLOUD-SERIAL')
    assert sha(APP / 'oc.env') == before['oc.env']
    if not field_upgrade:
        assert sha(APP / 'mon-sensors') == before['mon-sensors']
        assert not (APP / 'mon-sensors-plugin').exists()
        assert (APP / '.bits-collector').exists()
        assert run(APP / '.bits-collector', '--once', good=False).returncode != 0
        passed('private batch collector preserves original monitor bytes and exposes no replacement hardware viewer')
    assert data('/usr/local/bin/mon-sensors-report', '--check')['version'] == '0.2.0'
    passed('fresh UID201 original node adopts safely; interrupted attachment resumes; repeat attach idempotent')
    assert remove('node', good=False).returncode
    mlc = data('bits-o', 'tools', 'list')
    assert mlc['tools']['mlc']['version'] == '3.13'
    run('bits-o', 'preflight', '--json')
    passed('packaged selected tools including MLC3.13; real node preflight before queue claim')
    rsync_config = Path('/root/cloud-rsync.conf')
    rsync_config.write_text('use chroot = no\n[logs]\npath = /data/cds/result\nread only = no\nuid = ocuser\ngid = ' + str(account.pw_gid) + '\n')
    rsync = subprocess.Popen(['/usr/bin/rsync', '--daemon', '--no-detach', '--address=127.0.0.1', '--config=' + str(rsync_config)], stdout=subprocess.DEVNULL)
    www = Path('/root/cloud-http/config'); www.mkdir(parents=True)
    (www / 'ocrun-version.txt').write_text('MAIN_VERSION=0.9.24a\n')
    http = subprocess.Popen([sys.executable, '-m', 'http.server', '80', '--bind', '127.0.0.1'],
        cwd=str(www.parent), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    legacy_menu = len(sys.argv) > 1 and sys.argv[1] == 'legacy-menu'
    if legacy_menu:
        # The read-only 215 source (oc.env rdb-add-tasks) uses SET mapping,
        # SET IDS/DATES, RPUSH TASKS, SET <name> <seconds>. No plugin owner key.
        # Reproduce only those writes in the disposable Redis, not the production env.
        rdb.call('DEL', 'TASKS', 'OCRUN_PLUGIN_BATCH', 'MON_PLUGIN_CLAIM', 'STATUS', 'CURRENT', db=queued['db'])
        rdb.call('SET', 'LEGACY-CLOUD', str(queued['db']))
        rdb.call('SET', 'IDS', 'PLUGIN-CLOUD', db=queued['db'])
        rdb.call('SET', 'DATES', queued['time'], db=queued['db'])
        for name in ('stress', 'stress-ng', 'stress'):
            rdb.call('RPUSH', 'TASKS', name, db=queued['db'])
            rdb.call('SET', name, '3', db=queued['db'])
        assert rdb.call('GET', 'OCRUN_PLUGIN_BATCH', db=queued['db']) is None
        passed('queued with original occt Redis write sequence; no BITS task submission or ownership required')
    started = data('bits-o', 'start')
    assert started['automatic_task_polling'] is False
    case = None
    for unused in range(150):
        rows = data('bits-o', 'status', '--json')['cases']
        if rows:
            case = rows[0]
            if case.get('error'):
                raise AssertionError(json.dumps(case))
            if case['stage'] == 'complete':
                break
        time.sleep(1)
    else:
        raise AssertionError(Path(started['log']).read_text())
    assert case['execution_result'] == 'completed'
    assert [s['name'] for s in case['steps']] == ['stress', 'stress-ng', 'stress']
    assert all(s['execution'] == 'duration_reached' and s['cleanup_confirmed'] for s in case['steps'])
    assert case['data_quality'] == 'readings_reported_validity_unknown'
    files = dict(case['artifacts'], **case['receipt'])
    assert len(files) == 6
    remote_dir = remote / 'LEGACY-CLOUD_CLOUD-SERIAL'
    for name, meta in files.items():
        assert sha(remote_dir / name) == meta['sha256']
    snapshot = {name: sha(remote_dir / name) for name in files}
    run('bits-o', 'retry', '--case', case['case'])
    assert snapshot == {name: sha(remote_dir / name) for name in files}
    sheet = next(remote_dir.glob('*PLUGIN-CLOUD*.report.json'))
    report = json.loads(sheet.read_text())
    assert report['statistics']['details']['snapshots'] > 0
    for artifact in remote_dir.glob('*PLUGIN-CLOUD*'):
        if artifact.name.endswith(('.jsonl', '.report.json', '.report.html')):
            for restricted in (b'tRFC', b'tREFI', b'tFAW', b'tRAStoCAS'):
                assert restricted not in artifact.read_bytes()
    passed('native unlocked fixture exports only Primary timings throughout collection, report and delivery')
    passed('unchanged old ocb initializes then runs three real timed steps, including repeated names; six artifacts SHA verified, repeated recovery stable')
    report = json.loads(Path(case['mon']).with_suffix('.report.json').read_text())
    assert report['machine']['capture_phase'] == 'batch_start'
    assert report['statistics']['metrics']['vrm_temp_c']['mean'] is None
    with zipfile.ZipFile(str(Path(case['mon']).with_suffix('.xlsx'))) as workbook:
        assert workbook.testzip() is None
    portable = Path('/root/portable-plugin')
    portable.mkdir()
    # Our own cloud-built archive; exact bytes are part of SHA256SUMS.
    with tarfile.open('/root/plugin-packages/bits-o-node-0.2.0-portable.tar.gz') as bundle:
        bundle.extractall(str(portable))
    unrelated = subprocess.Popen(['sleep', '300'])
    run(sys.executable, '-I', '-B', portable / 'bits-o-node-0.2.0/entry.py', 'maintenance-stop')
    assert unrelated.poll() is None
    unrelated.terminate(); unrelated.wait(timeout=5)
    run('flock', '-n', APP / '.mon-sensors-finish/launch.lock', '/bin/true')
    run('bits-o', 'stop-scheduler')
    assert run('bits-o', 'start', good=False).returncode
    time.sleep(1)
    assert rdb.call('LLEN', 'TASKS', db=queued['db']) == 0
    passed('HTML machine report and streaming Excel; missing sensor stays null; idle scheduler stop and no new polling')
    hook = APP / 'ocb'; original = hook.read_bytes(); hook.write_bytes(original + b'\n# hand edit\n')
    assert run('bits-o', 'rollback', '--check', good=False).returncode
    hook.write_bytes(original)
    run('bits-o', 'rollback', '--check')
    failure = '''import sys, os
sys.path.insert(0, '/opt/ocrun-plugin/0.2.0')
from legacy_plugin import attach, common
original = os.rename
def interrupted(source, target):
    original(source, target)
    if str(target).endswith('/displaced'):
        raise OSError('cloud interruption after rollback displacement')
os.rename = interrupted
attach.rollback(common.config())
'''
    assert run(sys.executable, '-I', '-B', '-c', failure, good=False).returncode
    assert json.loads((APP / '.ocrun-plugin/attachment.json').read_text())['status'] == 'rolling_back'
    run('bits-o', 'rollback')
    run('bits-o', 'rollback')
    for name, expected in before.items():
        assert sha(APP / name) == expected
    if field_upgrade:
        assert not (APP / 'mon-analyse-log').is_symlink()
        assert (APP / 'py').stat().st_uid == 0
        assert Path('/usr/local/bin/mon-sensors-report').exists()
        assert data('/usr/local/bin/mon-sensors-report', '--check')['version'] == '0.2.0'
        assert b'0.2.1' in run(APP / 'mon-sensors-finish', '--version').stdout
    else:
        assert (APP / 'mon-analyse-log').is_symlink()
        assert (APP / 'py').stat().st_uid == 201
        assert not Path('/usr/local/bin/mon-sensors-report').exists()
    assert all((remote_dir / name).exists() for name in files)
    run('bits-o', 'unconfigure')
    remove('node')
    passed('modified hooks block rollback; interrupted rollback resumes; original bytes/ownership/link restored; native removal retains results')
    install('control')
    controller_config()
    receipt = 'LEGACY-CLOUD_CLOUD-SERIAL/' + Path(case['mon']).with_suffix('.finish.json').name
    result = json.loads(run('su', '-s', '/bin/sh', 'ocuser', '-c',
        'bits-o results verify --receipt ' + receipt + ' --json').stdout.decode())
    assert result['verified_here'] and result['html_report_path'].endswith('.report.html')
    archive_args = ['bits-o', 'tasks', 'archive', '--node', 'LEGACY-CLOUD', '--id', 'PLUGIN-CLOUD', '--time', queued['time'], '--receipt', receipt]
    if legacy_menu:
        assert run(*archive_args, good=False).returncode
        passed('original occt batch completed, collected, reported and verified; BITS preserves old-menu task ownership')
        rdb.call('DEL', 'LEGACY-CLOUD')  # disposable fixture cleanup only
    else:
        run(*archive_args)
    assert rdb.call('GET', 'LEGACY-CLOUD') is None
    assert rdb.call('GET', 'UNRELATED') == '1'
    assert all((remote_dir / name).exists() for name in files)
    passed('ocuser reads six delivered artifacts through unified control entry; verified archive removes only owned task keys')
    run('bits-o', 'unconfigure'); remove('control')
    upgrade_roundtrip(config_args, remote_dir, files, before)
    for child in (http, rsync, redis):
        child.terminate(); child.wait(timeout=10)
    Path('/results/validation.json').write_text(json.dumps({'source_commit': os.environ['GITHUB_SHA'],
        'status': 'passed', 'mode': 'field-upgrade' if field_upgrade else ('legacy-menu' if legacy_menu else 'fresh'),
        'checks': checks, 'python': sys.version, 'os_release': Path('/etc/os-release').read_text(),
        'sensor_source': 'synthetic cloud fixture; no activation/hardware validation',
        'control_configuration': 'unknown baseline refusal + explicitly prepared protocol fixture; real 215 setup pending'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    finally:
        state = APP / '.mon-sensors-finish'
        for log in list(state.glob('run-*/collector.log')) + list(state.glob('scheduler.log')):
            Path('/results', log.parent.name + '-' + log.name).write_bytes(log.read_bytes()[-131072:])
