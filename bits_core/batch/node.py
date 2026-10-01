"""One explicit batch run against the unchanged OCRUN Redis task layout."""
import argparse
import importlib.util
import json
import math
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import time

from common import STATE, atomic, directory, json_read, lock, now, read, run, save
import finish
# Avoid the standard library module of the same name.
_spec = importlib.util.spec_from_file_location('node_queue', str(Path(__file__).parent / 'queue.py'))
_queue = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_queue)
from workload import profile, parent_death, identity

MAX_SOURCE_BYTES = 16 * 1024 ** 3
MAX_ROWS = 4000000
RESERVE_BYTES = 512 * 1024 ** 2


def capacity(logs, tasks):
    # 2-second sampling, conservative per-CPU JSON estimate, plus report/temp copies.
    samples = sum(t['runtime_s'] for t in tasks) // 2 + 100
    estimate = samples * (1024 + len(os.sched_getaffinity(0)) * 256)
    required = estimate * 3 + RESERVE_BYTES
    free = shutil.disk_usage(str(logs)).free
    if samples > MAX_ROWS or estimate > MAX_SOURCE_BYTES:
        raise ValueError('Batch exceeds the bounded long-task budget; split into separate task batches')
    if free < required:
        raise ValueError('Insufficient log/report space: need {} bytes, available {}'.format(required, free))
    return {'estimated_samples': samples, 'estimated_json_bytes': estimate,
            'required_free_bytes': required, 'available_bytes': free}


def inspect(app, queue, logs, node, serial):
    import install_guard
    install_guard.verify(app)
    finish.prerequisites(app)
    finish.require_idle()
    pending = [s['case'] for s in finish.cases(app) if s['stage'] not in finish.TERMINAL]
    if pending:
        raise ValueError('Unfinished batches: ' + ','.join(pending))
    snapshot = queue.snapshot()
    if not snapshot or not snapshot['tasks']:
        return {'empty': True, 'snapshot': snapshot, 'tasks': []}
    finish.safe_name(snapshot['id'])
    finish.safe_name(snapshot['time'])
    finish.safe_name(node)
    finish.safe_name(serial)
    tasks = []
    for item in snapshot['tasks']:
        value = item.get('runtime')
        if not isinstance(value, str) or not value.isdigit():
            raise ValueError('Invalid duration for task ' + repr(item.get('name')))
        tasks.append(profile(app, item['name'], int(value)))
    mon = logs / '{}_{}_{}_{}.mon'.format(node, serial, snapshot['id'], snapshot['time'])
    finish.safe_name(mon.name)
    if any(p.exists() or p.is_symlink() for p in
           (mon, Path(str(mon) + '.sckocp.jsonl'), mon.with_suffix('.xlsx'), mon.with_suffix('.finish.json'),
            mon.with_suffix('.report.json'), mon.with_suffix('.report.html'))):
        raise ValueError('This batch already has output files; choose a new task identity')
    return {'empty': False, 'snapshot': snapshot, 'tasks': tasks, 'mon': str(mon),
            'capacity': capacity(logs, tasks)}


def owned_collector_health(child, mon):
    if child.poll() is not None:
        raise ValueError('Collector exited; workload is being stopped')
    if shutil.disk_usage(str(mon.parent)).free < RESERVE_BYTES:
        raise ValueError('Log disk reserve reached; workload is being stopped, data retained')
    for path in (mon, Path(str(mon) + '.sckocp.jsonl')):
        if path.exists() and path.stat().st_size > MAX_SOURCE_BYTES:
            raise ValueError('Log byte budget reached; data retained')
    if mon.exists() and time.time() - mon.stat().st_mtime > 90:
        raise ValueError('No new monitoring sample for 90 seconds')


def run_batch(app, queue, plan, remote):
    if plan['empty']:
        return {'status': 'idle', 'automatic_task_polling': False}
    snapshot, tasks = plan['snapshot'], plan['tasks']
    mon = Path(plan['mon'])
    runtime = finish.collector_runtime(app)
    with lock(finish.root_state(app) / 'operation.lock'):
        state = finish.begin(app, str(mon), remote, snapshot['id'], snapshot['time'])
        state['queue_snapshot'] = snapshot
        state['capacity'] = plan['capacity']
        state['execution_result'] = 'not_started'
        state['scheduler_process'] = identity(os.getpid())
        state['boot_id'] = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
        finish.persist(app, state)
    case = state['case']
    case_dir = finish.root_state(app) / ('run-' + case)
    case_dir.mkdir(mode=0o700)
    stopped = [False]
    previous = {}
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        previous[sig] = signal.signal(sig, lambda *args: stopped.__setitem__(0, True))
    parent = os.getppid()
    collector = worker = None
    collector_log = open(str(case_dir / 'collector.log'), 'xb')
    try:
        collector = subprocess.Popen([str(runtime.collector_entry), '2', str(mon)],
            stdout=collector_log, stderr=collector_log, stdin=subprocess.DEVNULL, start_new_session=True,
            preexec_fn=parent_death,
            env={'PATH': '/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin', 'HOME': '/root',
                 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'})
        deadline = time.monotonic() + 40
        while not mon.exists() or not Path(str(mon) + '.sckocp.jsonl').exists():
            owned_collector_health(collector, mon)
            if stopped[0] or time.monotonic() > deadline:
                raise ValueError('Collector did not become ready before workload start')
            time.sleep(.2)
        # A header alone does not demonstrate a licensed successful sample.
        while True:
            owned_collector_health(collector, mon)
            with open(str(mon) + '.sckocp.jsonl', 'rb') as stream:
                first = stream.readline(2 * 1024 * 1024 + 1)
            if first.endswith(b'\n'):
                if json.loads(first)['provider']['status'] != 'ok':
                    raise ValueError('Initial monitoring sample unavailable; task queue preserved')
                break
            if stopped[0] or time.monotonic() > deadline:
                raise ValueError('No complete initial monitoring sample')
            time.sleep(.2)
        for index, spec in enumerate(tasks):
            if stopped[0] or os.getppid() != parent:
                raise ValueError('Scheduler interrupted before task claim')
            owned_collector_health(collector, mon)
            # Prepare writable task configuration before dequeuing. Read-only preflight
            # did not create anything and a preparation failure leaves the queue intact.
            import suite
            spec = suite.prepare(spec, case_dir / ('step-{}-{}'.format(index, spec['name'])))
            binary_hash = finish.report_sheet.executable_hash(spec['binary'])
            token = '{}:{}'.format(case, index)
            # Durable intent precedes the atomic queue mutation. A crash or lost
            # Redis reply remains an explicit pending case, never an automatic rerun.
            state['claim'] = {'token': token, 'index': index, 'state': 'prepared', 'at': now()}
            finish.persist(app, state)
            queue.claim(snapshot, snapshot['tasks'][index:], token)
            state['claim']['state'] = 'claimed'
            state['execution_result'] = 'running'
            finish.step(app, state, spec['name'], 'start', spec['runtime_s'])
            state['steps'][-1].update({'binary': spec['binary'], 'binary_sha256': binary_hash,
                                      'tool_version': spec.get('tool_version', 'unknown')})
            finish.persist(app, state)
            spec_path = case_dir / ('step-{}.json'.format(index))
            result_path = case_dir / ('step-{}.result.json'.format(index))
            spec = dict(spec, log=str(mon.parent / (mon.stem + '_{:04d}_'.format(index) + spec['name'] + '.log')))
            save(spec_path, spec)
            diagnostics = open(str(case_dir / ('step-{}.stderr'.format(index))), 'xb')
            try:
                worker = subprocess.Popen([os.path.realpath(sys.executable), '-I', '-S', '-B',
                    str(Path(__file__).parent / 'workload.py'), str(spec_path), str(result_path)],
                    stdin=subprocess.DEVNULL, stdout=diagnostics, stderr=diagnostics,
                    start_new_session=True, preexec_fn=parent_death)
                while worker.poll() is None:
                    if stopped[0] or os.getppid() != parent:
                        raise ValueError('Scheduler interrupted during workload')
                    owned_collector_health(collector, mon)
                    time.sleep(.25)
                result = json_read(result_path)
            finally:
                if worker is not None and worker.poll() is None:
                    worker.terminate()
                    worker.wait(timeout=12)
                diagnostics.close()
            state['steps'][-1].update(result)
            state['claim']['state'] = 'executed'
            finish.persist(app, state)
            if result['execution'] not in ('duration_reached', 'finished') or not result['cleanup_confirmed']:
                state['execution_result'] = result['execution']
                finish.persist(app, state)
                raise ValueError('Workload did not complete its contract: ' + result['execution'])
        state['execution_result'] = 'completed'
        finish.persist(app, state)
        queue.update(snapshot, token, 'finalizing')
        finish.finish(app, state)
        queue.update(snapshot, token, 'delivered')
        return {'case': case, 'status': 'complete', 'execution_result': 'completed'}
    except BaseException as error:
        if worker is not None and worker.poll() is None:
            worker.terminate()
            worker.wait(timeout=12)
        if state['steps'] and 'ended_at' not in state['steps'][-1] and 'result_path' in locals() and result_path.exists():
            state['steps'][-1].update(json_read(result_path))
        if state.get('execution_result') in ('not_started', 'running'):
            state['execution_result'] = 'interrupted'
        if state.get('execution_result') != 'completed':
            state['execution_error'] = str(error)[:512]
        if not state['sealed']:
            state['outcome'] = 'interrupted'
        finish.persist(app, state, error=str(error)[:512])
        try:
            if 'token' in locals():
                queue.update(snapshot, token, 'attention_required')
        except (OSError, ValueError):
            pass
        raise
    finally:
        try:
            if collector is not None and collector.poll() is None:
                runtime = finish.collector_runtime(app)
                with finish.stopped_monitor(app, mon, runtime):
                    pass
                collector.wait(timeout=5)
        finally:
            collector_log.close()
            for sig, handler in previous.items():
                signal.signal(sig, handler)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', required=True)
    parser.add_argument('--rdb-server', required=True)
    parser.add_argument('--rdb-port', type=int, default=6379)
    parser.add_argument('--log-server', required=True)
    parser.add_argument('--log-dir', required=True)
    parser.add_argument('--node', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    app, logs = directory(args.app), directory(args.log_dir)
    queue = _queue.Queue(args.rdb_server, args.node, args.rdb_port)
    remote = '{}::logs/{}_{}'.format(args.log_server, args.node, args.serial)
    finish.remote_parts(remote)
    with lock(finish.root_state(app) / 'scheduler.lock'):
        plan = inspect(app, queue, logs, args.node, args.serial)
        if args.check:
            result = dict(plan, status='checked', read_only=True)
        else:
            result = run_batch(app, queue, plan, remote)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0
