"""Exercise installed services on a disposable cloud VM; never real hardware acceptance."""
import hashlib
import io
import json
import os
import pathlib
import subprocess
import sys
import tarfile
import time
import urllib.request

if os.environ.get('GITHUB_ACTIONS') != 'true' or os.geteuid() != 0:
    raise SystemExit('Requires a root process on a disposable GitHub Actions runner')

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, '/opt/ocrun/current')
from ocrun.common import read_json, write_json
from ocrun.queue import Queue
from ocrun.rediswire import Redis

RESULTS = ROOT / '.cloud-results'
RESULTS.mkdir(exist_ok=True)
checks = []


def wait_for(description, predicate, timeout=70):
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        try:
            value = predicate()
            if value:
                print('PASS: ' + description, flush=True)
                checks.append(description)
                return value
        except (OSError, ValueError, RuntimeError) as error:
            last_error = type(error).__name__
        time.sleep(0.5)
    raise AssertionError('Timed out: {} (last error: {})'.format(description, last_error))


def service(action, *names):
    subprocess.check_call(['systemctl', action] + list(names))


def main():
    server = read_json('/etc/ocrun/server.json')
    redis = Redis(server['redis'])
    queue = Queue(redis, 'cloud-node')
    state = pathlib.Path('/var/lib/ocrun-cloud')
    logs = pathlib.Path('/var/log/ocrun-cloud')
    state.mkdir(mode=0o700, exist_ok=True)
    logs.mkdir(mode=0o700, exist_ok=True)
    client_path = state / 'client.json'
    subprocess.check_call(['/usr/local/bin/ocrun-admin', 'enroll', 'cloud-node', '--output', str(client_path)])
    client = read_json(str(client_path))
    client.update(state_dir=str(state), log_dir=str(logs), tool_root=str(state), offline_grace_seconds=3,
                  sample_interval_seconds=1, upload_interval_seconds=10,
                  required_metrics=['cpu_temperature_c'])
    write_json(str(client_path), client)
    unit = '''[Unit]
Description=Disposable OCRUN cloud integration agent
After=network.target
[Service]
Type=simple
Environment=PYTHONPATH=/opt/ocrun/current
Environment=GITHUB_ACTIONS=true
ExecStart=/usr/bin/python3 {adapter} --config {config}
KillMode=control-group
TimeoutStopSec=40
Restart=no
'''.format(adapter=ROOT / 'ci/integration_agent.py', config=client_path)
    pathlib.Path('/etc/systemd/system/ocrun-cloud-agent.service').write_text(unit)
    service('daemon-reload')
    service('start', 'ocrun-cloud-agent')
    wait_for('installed systemd agent registers heartbeat',
             lambda: redis.execute('GET', queue.key('heartbeat')))
    url = 'http://{}:8080/releases/ocrun-client-0.12.8.tar.gz'.format(server['address'])
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(url, timeout=10) as response:
        archive = response.read()
        assert response.status == 200 and len(archive) > 1000
    with tarfile.open(fileobj=io.BytesIO(archive), mode='r:gz') as bundle:
        assert {'README.md', 'FLEET.md', 'OPERATIONS.md'}.issubset(set(bundle.getnames()))
    checks.append('nginx distributes an installable client archive')
    node_redis = Redis(client['redis'])
    try:
        node_redis.execute('GET', 'ocrun:h:another-device:heartbeat')
    except RuntimeError:
        checks.append('real Redis ACL prevents reading another device namespace')
    else:
        raise AssertionError('Node ACL unexpectedly allowed another namespace')

    def submit(task_id, seconds=3, **options):
        task = {'id': task_id, 'tool': 'cpu-burn', 'threads': 1,
                'duration_seconds': seconds}
        task.update(options)
        assert queue.enqueue(task)

    def terminal(task_id, status):
        return lambda: (queue.get(task_id) or {}).get('status') == status

    def uploaded(task_id):
        root = pathlib.Path(server['data_root']) / 'logs/cloud-node' / task_id
        if not (root / 'result.json').exists() or not (root / 'manifest.json').exists():
            return False
        acknowledgement = redis.execute('GET', queue.key('log_ack:' + task_id))
        if not acknowledgement:
            return False
        digest = hashlib.sha256((root / 'manifest.json').read_bytes()).hexdigest()
        return json.loads(acknowledgement)['manifest_sha256'] == digest

    def running_workloads():
        found = []
        for process in pathlib.Path('/proc').glob('[0-9]*'):
            try:
                if b'ocrun.cpu_burn' in (process / 'cmdline').read_bytes():
                    found.append(int(process.name))
            except OSError:
                pass
        return found

    submit('cloud-complete')
    wait_for('bounded workload completes through real Redis', terminal('cloud-complete', 'completed'))
    wait_for('rsync result and manifest receive verified acknowledgement', lambda: uploaded('cloud-complete'))
    for action, name in [('dashboard', 'dashboard.html'), ('export', 'fleet.csv')]:
        artifact = RESULTS / name
        subprocess.check_call(['/usr/local/bin/ocrun-admin', action, 'cloud-node', '--output', str(artifact)])
        artifact.chmod(0o644)
        content = artifact.read_text(encoding='utf-8-sig')
        assert 'cloud-node' in content
        assert client['redis']['password'] not in content and client['upload']['password'] not in content
    checks.append('real fleet dashboard and CSV export omit enrollment secrets')

    assert not running_workloads()
    submit('cloud-protection', protection={'limits': {'cpu_temperature_c': {'max': 35}}})
    wait_for('preflight protection rejects excessive fixture temperature', terminal('cloud-protection', 'failed'))
    protected = read_json(str(logs / 'cloud-protection/result.json'))
    assert protected['protection']['code'] == 'limit_exceeded'
    assert protected['protection']['metric'] == 'cpu_temperature_c'
    assert not (logs / 'cloud-protection/workload.log').exists()
    assert not (logs / 'cloud-protection/execution.json').exists()
    assert not running_workloads()
    checks.append('protection failure occurs before workload launch and leaves no process')
    wait_for('protected task logs receive verified acknowledgement', lambda: uploaded('cloud-protection'))

    queue.pause()
    submit('cloud-cancel-queued', 90)
    assert queue.cancel('cloud-cancel-queued') == 'cancelled'
    assert queue.get('cloud-cancel-queued')['status'] == 'cancelled'
    queue.resume()
    submit('cloud-cancel-running', 90)
    wait_for('running cancellation scenario starts a real workload',
             lambda: (logs / 'cloud-cancel-running/workload.log').exists() and running_workloads())
    skipped = queue.get('cloud-cancel-queued')
    assert skipped['status'] == 'cancelled' and 'claim_token' not in skipped and 'started_at' not in skipped
    assert not (logs / 'cloud-cancel-queued').exists()
    checks.append('queued cancellation remains unexecuted after queue resumes')
    assert queue.cancel('cloud-cancel-running') == 'requested'
    wait_for('running cancellation records cancelled after stopping workload', terminal('cloud-cancel-running', 'cancelled'))
    wait_for('cancelled workload leaves no live process', lambda: not running_workloads(), timeout=30)
    wait_for('running cancellation logs receive verified acknowledgement', lambda: uploaded('cloud-cancel-running'))

    queue.pause()
    submit('cloud-persisted')
    service('restart', 'ocrun-redis')
    wait_for('AOF preserves paused pending task after Redis restart', terminal('cloud-persisted', 'pending'))
    assert queue.is_paused()
    queue.resume()
    wait_for('persisted task resumes after Redis restart', terminal('cloud-persisted', 'completed'))
    submit('cloud-offline', 90)
    wait_for('disconnect scenario reaches running', terminal('cloud-offline', 'running'))
    service('stop', 'ocrun-redis')
    local_result = logs / 'cloud-offline/result.json'
    wait_for('agent stops workload locally after controller disconnect',
             lambda: local_result.exists() and read_json(str(local_result)).get('status') == 'interrupted', timeout=65)
    assert (state / 'active.json').exists(), 'Offline result must remain journaled'
    service('start', 'ocrun-redis')
    service('start', 'ocrun-log-verifier')
    wait_for('offline result is acknowledged after reconnect', terminal('cloud-offline', 'interrupted'))
    wait_for('offline logs are uploaded after reconnect', lambda: uploaded('cloud-offline'))
    submit('cloud-agent-restart', 90)
    wait_for('restart scenario reaches running', terminal('cloud-agent-restart', 'running'))
    service('restart', 'ocrun-cloud-agent')
    wait_for('systemd restart records interrupted workload', terminal('cloud-agent-restart', 'interrupted'))
    wait_for('restart leaves no running queue record', lambda: queue.running() is None)
    service('stop', 'ocrun-cloud-agent')
    return {'successful': True, 'checks': checks, 'sensor_data': 'simulated',
            'hardware_acceptance': False, 'server_os': read_json_os()}


def read_json_os():
    from ocrun.common import os_release
    return os_release()


try:
    summary = main()
except Exception as error:
    summary = {'successful': False, 'checks': checks, 'error': str(error),
               'sensor_data': 'simulated', 'hardware_acceptance': False}
    raise
finally:
    (RESULTS / 'integration.json').write_text(json.dumps(summary, indent=2) + '\n')
