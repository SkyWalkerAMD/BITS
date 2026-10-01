"""Finite, identity-owned workload sessions. No process-name kill operations."""
import ctypes
import json
import os
from pathlib import Path
import signal
import stat
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).absolute().parent))
from common import directory, now, regular, json_read, save
from security import trusted_executable
from bits_layout import LAYOUT

PROFILES = {
    'stress': ('bin/stress/stress', 'timed'),
    'stress_r2': ('bin/stress/stress', 'timed'),
    'stress-ng': ('bin/stress-ng/stress-ng', 'timed'),
    'stress-ng_r2': ('bin/stress-ng/stress-ng', 'timed'),
    'bcfi': ('bin/bcfi/bcfi', 'timed'), 'bcfd': ('bin/bcfd/bcfd', 'timed'),
    'ptu': ('bin/ptu/ptu', 'timed'), 'bcr': ('bin/bcr/bcr', 'finite'),
    'mlc': ('bin/mlc/mlc', 'finite'), 'mbw': ('bin/mbw/mbw', 'finite'),
    'sysjitter': ('bin/sysjitter/sysjitter', 'finite'),
    'cyclictest': ('bin/cyclictest/cyclictest', 'native_timed'),
    'unixbench': ('bin/unixbench/Run', 'finite'),
    'cpu': ('cpu2017/run-spec', 'finite'),
    'cpu2017': ('cpu2017/run-spec', 'finite'),
}
for _kind in ('no', 'avx', 'fma3', 'avx512'):
    for _mode in ('m1', 'm2', 'm4'):
        _name = 'p95-' + _kind + '_' + _mode
        PROFILES[_name] = ('bin/' + _name + '/mprime', 'timed')


def profile(app, name, runtime):
    import suite
    selected = suite.profile(app, name, runtime)
    if selected is not None:
        return selected
    if LAYOUT.native:
        raise ValueError('BITS workload suite is not bound; run bits-node check. No legacy tool fallback is permitted.')
    if name not in PROFILES:
        raise ValueError('Unsupported task {!r}; use one of: {}'.format(name, ', '.join(sorted(PROFILES))))
    if type(runtime) is not int or not 1 <= runtime <= 31 * 86400:
        raise ValueError('Task duration must be 1..2678400 seconds')
    relative, mode = PROFILES[name]
    binary = app / relative
    from tools_adoption import pinned
    binary = pinned(app, name) or binary
    try:
        with trusted_executable(str(binary)):
            pass
    except (OSError, ValueError):
        raise ValueError('Workload executable is missing or untrusted: {}. Inspect adopt-workloads --task {} --check.'.format(binary, name))
    cpus = str(len(os.sched_getaffinity(0)))
    args = []
    if name.startswith('stress'):
        args = ['--cpu', cpus]
        # stress-ng otherwise applies its own one-day default even when the
        # requested batch is longer. The guardian remains the primary timer;
        # the native deadline is a secondary bound with cleanup grace.
        if name.startswith('stress-ng'):
            args += ['--timeout', str(runtime + 5)]
    elif name.startswith('p95-'):
        args = ['-t']
    elif name == 'ptu':
        args = ['-y']
    elif name == 'mbw':
        available = next(int(line.split()[1]) for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:')) // 1024
        args = ['-n', '5', str(max(1, available * 45 // 100))]
    elif name == 'sysjitter':
        args = ['--runtime', str(min(10, runtime)), '200']
    elif name == 'cyclictest':
        args = ['--policy=fifo', '--priority=80', '--threads=' + cpus, '--interval=1000',
                '--distance=0', '--mlockall', '--quiet', '--duration=' + str(runtime)]
    elif name in ('cpu', 'cpu2017'):
        args = ['all']
    return {'name': name, 'runtime_s': runtime, 'binary': str(binary), 'argv': [str(binary)] + args,
            'mode': mode, 'cwd': str(binary.parent)}


def identity(pid):
    try:
        fields = Path('/proc/{}/stat'.format(pid)).read_text().rsplit(')', 1)[1].split()
        return {'pid': pid, 'start_ticks': fields[19], 'pgrp': int(fields[2]),
                'session': int(fields[3]), 'status': fields[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def members(session):
    result = []
    for entry in Path('/proc').iterdir():
        if entry.name.isdigit():
            item = identity(int(entry.name))
            if item and item['session'] == session and item['status'] != 'Z':
                result.append(item)
    return result


def parent_death():
    """Single-threaded worker spawn: kernel notifies the guardian if scheduler dies."""
    parent = os.getppid()
    if ctypes.CDLL(None).prctl(1, signal.SIGTERM, 0, 0, 0) != 0:
        os._exit(126)
    if os.getppid() != parent:
        os._exit(126)


def stop_session(child):
    # The direct child is deliberately not reaped before cleanup; its PID/session
    # cannot be reused. Signal only individually rechecked session members.
    for sig, seconds in ((signal.SIGTERM, 5), (signal.SIGKILL, 3)):
        deadline = time.monotonic() + seconds
        while True:
            active = members(child.pid)
            if not active:
                return
            for item in active:
                current = identity(item['pid'])
                if current and all(current[k] == item[k] for k in ('pid', 'start_ticks', 'session')):
                    try:
                        os.kill(item['pid'], sig)
                    except ProcessLookupError:
                        pass
            if time.monotonic() >= deadline:
                break
            time.sleep(.1)
    if members(child.pid):
        raise ValueError('Owned workload processes did not stop')


def execute(spec, log, record, health=lambda: None, cancelled=lambda: False):
    if spec.get('suite_version') and not spec.get('suite_prepared'):
        raise ValueError('Suite task has not prepared an isolated runtime directory')
    directory(log.parent)
    child = None
    began = time.monotonic()
    result = {'started_at': now(), 'runtime_s': spec['runtime_s'], 'launch_code': None,
              'execution': 'starting', 'exit_code': None, 'cleanup_confirmed': False}
    for key in ('suite_version', 'tool_version', 'tool', 'memory_mb'):
        if key in spec:
            result[key] = spec[key]
    environment = {'PATH': '/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin',
                   'HOME': spec['cwd'], 'LANG': 'C.UTF-8', 'LC_ALL': 'C.UTF-8'}
    for key, value in spec.get('env', {}).items():
        if key not in ('UB_BINDIR', 'UB_TMPDIR', 'UB_RESULTDIR', 'UB_TESTDIR'):
            raise ValueError('Unexpected workload environment setting')
        environment[key] = value
    fd = os.open(str(log), os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        regular(os.fstat(fd), log)
        with trusted_executable(spec['binary']) as (exefd, executable):
            child = subprocess.Popen(spec['argv'], executable=executable, pass_fds=(exefd,),
                cwd=spec['cwd'], start_new_session=True, stdin=subprocess.DEVNULL, stdout=fd, stderr=fd,
                env=environment)
        result.update({'launch_code': 0, 'process': identity(child.pid), 'execution': 'running'})
        record(result)
        deadline = began + spec['runtime_s'] + (3 if spec['mode'] == 'native_timed' else 0)
        while True:
            health()
            if os.fstat(fd).st_size > 64 * 1024 ** 2:
                result['execution'] = 'log_limit_exceeded'
                break
            if cancelled():
                result['execution'] = 'interrupted'
                break
            # WNOWAIT preserves the leader PID until all its session members stop.
            ended = os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT)
            if ended:
                code = ended.si_status if ended.si_code == os.CLD_EXITED else -ended.si_status
                result['exit_code'] = code
                result['execution'] = ('finished' if spec['mode'] == 'finite' and code == 0 else 'early_exit')
                if spec['mode'] == 'native_timed' and code == 0 and time.monotonic() - began >= spec['runtime_s'] - .5:
                    result['execution'] = 'duration_reached'
                break
            if time.monotonic() >= deadline:
                result['execution'] = 'duration_reached' if spec['mode'] == 'timed' else 'timed_out'
                break
            time.sleep(.2)
    except BaseException:
        result['execution'] = 'failed'
        raise
    finally:
        try:
            if child is not None:
                stop_session(child)
                result['exit_code'] = child.wait(timeout=3)
            result['cleanup_confirmed'] = True
        finally:
            os.close(fd)
            result.update({'ended_at': now(), 'elapsed_s': round(time.monotonic() - began, 3)})
            record(result)
    return result


if __name__ == '__main__':
    stopped = [False]
    for _sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(_sig, lambda *args: stopped.__setitem__(0, True))
    try:
        specification = json_read(Path(sys.argv[1]))
        result_path = Path(sys.argv[2])
        result = execute(specification, Path(specification['log']),
                         lambda value: save(result_path, value), cancelled=lambda: stopped[0])
        sys.exit(0 if result['execution'] in ('finished', 'duration_reached') else 1)
    except (OSError, ValueError) as error:
        print('workload: ' + str(error), file=sys.stderr)
        sys.exit(1)
