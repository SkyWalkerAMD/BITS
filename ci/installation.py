"""Root-only installer recovery tests on an otherwise disposable cloud VM."""
import hashlib
import io
import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tarfile
import time

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
    raise SystemExit('Requires a root process on a disposable GitHub Actions runner')
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from ocrun.common import read_json, write_json
from ocrun.queue import Queue
from ocrun.rediswire import Redis

WORK = pathlib.Path('/var/lib/ocrun-installer-cloud')
WORK.mkdir(mode=0o700, exist_ok=True)
RESULTS = ROOT / '.cloud-results'
RESULTS.mkdir(exist_ok=True)
checks = []


def install(arguments, source=ROOT, success=True):
    result = subprocess.run(['bash', str(source / 'install-client.sh'), '--skip-deps'] + arguments)
    if (result.returncode == 0) != success:
        raise AssertionError('Installer returned unexpected status: {}'.format(result.returncode))


def mark(description):
    print('PASS: ' + description, flush=True)
    checks.append(description)


def main():
    token = secrets.token_hex(32)
    redis_path = WORK / 'redis.conf'
    redis_path.write_text('bind 127.0.0.1\nport 6380\nsave ""\nappendonly no\nuser default off\nuser admin on >{} ~* +@all\n'.format(token))
    redis_path.chmod(0o600)
    unit = '[Service]\nExecStart=/usr/bin/redis-server {}\n'.format(redis_path)
    pathlib.Path('/etc/systemd/system/ocrun-installer-redis.service').write_text(unit)
    subprocess.check_call(['systemctl', 'daemon-reload'])
    subprocess.check_call(['systemctl', 'start', 'ocrun-installer-redis'])
    config = {'host_id': 'installer-node',
              'redis': {'host': '127.0.0.1', 'port': 6380, 'username': 'admin', 'password': token},
              'upload': {'host': '127.0.0.1', 'port': 1873, 'password': secrets.token_hex(32)},
              'state_dir': str(WORK / 'state'), 'log_dir': str(WORK / 'logs'),
              'required_metrics': [], 'tool_root': '/opt/ocrun/tools'}
    config_path = WORK / 'client.json'
    write_json(str(config_path), config)
    queue = Queue(Redis(config['redis']), config['host_id'])
    for _ in range(40):
        try:
            queue.pause()
            break
        except OSError:
            time.sleep(0.1)
    else:
        raise AssertionError('Test Redis failed to start')
    archive = WORK / 'tools.tar.gz'
    with tarfile.open(str(archive), 'w:gz') as bundle:
        payload = b'#!/bin/sh\nexit 0\n'
        item = tarfile.TarInfo('bin/stress/stress')
        item.mode, item.size = 0o755, len(payload)
        bundle.addfile(item, io.BytesIO(payload))
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    install(['--config', str(config_path), '--tools', str(archive), '--tools-sha256', '0' * 64], success=False)
    assert not pathlib.Path('/etc/ocrun/agent.json').exists()
    assert not pathlib.Path('/opt/ocrun/current').exists()
    mark('bad archive leaves no live configuration or runtime')
    install(['--config', str(config_path), '--tools', str(archive), '--tools-sha256', digest])
    previous = os.readlink('/opt/ocrun/current')
    assert pathlib.Path(read_json('/etc/ocrun/agent.json')['tool_root'], 'bin/stress/stress').exists()
    mark('corrected archive can immediately retry and activate real systemd agent')
    # A paused queue with an unfinished task must not permit an upgrade.
    queue.redis.execute('SET', queue.key('running'), 'busy-task')
    queue.redis.execute('HSET', queue.key('jobs'), 'busy-task', json.dumps({'id': 'busy-task', 'status': 'running'}))
    install(['--upgrade'], success=False)
    assert os.readlink('/opt/ocrun/current') == previous
    queue.redis.execute('DEL', queue.key('running'))
    queue.redis.execute('HDEL', queue.key('jobs'), 'busy-task')
    mark('upgrade refuses an unfinished task without replacing runtime')
    install(['--upgrade'])
    upgraded = os.readlink('/opt/ocrun/current')
    assert upgraded != previous and pathlib.Path(previous).exists() and queue.is_paused()
    assert pathlib.Path(upgraded, 'rollback/agent.json').exists()
    assert pathlib.Path(upgraded, 'rollback/previous-runtime.txt').read_text().strip() == previous
    mark('drained upgrade retains previous release and leaves queue paused')
    before_config = pathlib.Path('/etc/ocrun/agent.json').read_bytes()
    broken = WORK / 'broken-release'
    broken.mkdir()
    shutil.copytree(str(ROOT / 'ocrun'), str(broken / 'ocrun'))
    shutil.copytree(str(ROOT / 'mon_sensors_plugin'), str(broken / 'mon_sensors_plugin'))
    shutil.copytree(str(ROOT / 'sckocp_api'), str(broken / 'sckocp_api'))
    shutil.copytree(str(ROOT / 'systemd'), str(broken / 'systemd'))
    shutil.copyfile(str(ROOT / 'install-client.sh'), str(broken / 'install-client.sh'))
    for entry in ('mon-sensors-plugin', 'sckocp-api'):
        shutil.copy2(str(ROOT / entry), str(broken / entry))
    (broken / 'systemd/ocrun-agent.service').write_text('[Service]\nType=simple\nExecStart=/bin/false\nRestart=no\n')
    install(['--upgrade'], source=broken, success=False)
    assert os.readlink('/opt/ocrun/current') == upgraded
    assert pathlib.Path('/etc/ocrun/agent.json').read_bytes() == before_config
    subprocess.check_call(['systemctl', 'is-active', '--quiet', 'ocrun-agent'])
    mark('failed service activation restores previous runtime/configuration/service')
    return {'successful': True, 'checks': checks}


try:
    summary = main()
except Exception as error:
    summary = {'successful': False, 'checks': checks, 'error': str(error)}
    raise
finally:
    (RESULTS / 'installation.json').write_text(json.dumps(summary, indent=2) + '\n')
