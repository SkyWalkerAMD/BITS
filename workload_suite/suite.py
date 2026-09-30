"""Python 3.6-compatible, root-owned workload selection and per-step configuration.

Copied into the finalizer payload too: no import of code from a user-selected tool tree.
"""
import contextlib
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat

VERSION = '0.1.0'
PREFIX = Path('/opt/ocrun-workloads/' + VERSION)
BINDING = '.mon-sensors-workload-suite.json'
EXTERNAL = Path('/var/lib/ocrun-workloads')
P95 = re.compile(r'p95-(no|avx|fma3|avx512)_m([124])\Z')
NAMES = {'stress', 'stress_r2', 'stress-ng', 'stress-ng_r2', 'mlc', 'mbw',
         'cyclictest', 'unixbench', 'cpu', 'cpu2017'}


def directory(path):
    path = Path(path)
    if not path.is_absolute() or '..' in path.parts:
        raise ValueError('Absolute trusted directory required')
    for p in list(reversed(path.parents)) + [path]:
        s = p.lstat()
        if not stat.S_ISDIR(s.st_mode) or s.st_uid != 0 or s.st_mode & 0o022:
            raise ValueError('Untrusted workload directory: ' + str(p))
    return path


@contextlib.contextmanager
def opened(path):
    path = Path(path)
    directory(path.parent)
    fd = os.open(str(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    try:
        s = os.fstat(fd)
        if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_nlink != 1 or s.st_mode & 0o7022:
            raise ValueError('Untrusted workload file: ' + str(path))
        with os.fdopen(fd, 'rb', closefd=False) as f:
            yield f
    finally:
        os.close(fd)


def metadata(path):
    with opened(path) as f:
        h = hashlib.sha256()
        size = 0
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
            size += len(block)
    return {'sha256': h.hexdigest(), 'bytes': size}


def read_json(path):
    with opened(path) as f:
        data = f.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise ValueError('Workload inventory too large')
    return json.loads(data.decode('utf-8'))


def save(path, value):
    import tempfile
    directory(path.parent)
    if path.exists() or path.is_symlink():
        with opened(path):
            pass
    fd, temp = tempfile.mkstemp(prefix='.workloads-', dir=str(path.parent))
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f, sort_keys=True, indent=2)
            f.write('\n')
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, str(path))
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def check(prefix=PREFIX):
    prefix = directory(prefix)
    if prefix != PREFIX:
        raise ValueError('Unsupported workload suite prefix/version')
    spec = read_json(prefix / 'MANIFEST.json')
    if spec.get('schema') != 'ocrun-workloads-v1' or spec.get('version') != VERSION:
        raise ValueError('Unsupported workload manifest')
    for name, expected in spec['files'].items():
        if Path(name).is_absolute() or '..' in Path(name).parts:
            raise ValueError('Invalid workload inventory path')
        if metadata(prefix / name) != expected:
            raise ValueError('Workload file was changed: ' + name)
    return spec


def require_idle(app):
    # Package binding is explicit. Never terminate processes on an operator's behalf.
    for p in Path('/proc').iterdir():
        if not p.name.isdigit() or int(p.name) == os.getpid():
            continue
        try:
            args = (p / 'cmdline').read_bytes().split(b'\0')
            comm = (p / 'comm').read_text().strip()
        except (FileNotFoundError, ProcessLookupError):
            continue
        if any(os.fsencode(str(app / n)) in args for n in ('ocb', 'oct', 'mon-sensors', 'mon-sensors-plugin')) or comm in ('mprime', 'stress', 'stress-ng', 'mlc', 'mbw', 'cyclictest', 'runcpu'):
            raise ValueError('Stop this node scheduler/workloads before binding (PID {})'.format(p.name))


@contextlib.contextmanager
def binding_lock(app):
    directory(app)
    paths = [app / '.mon-sensors-runtime.lock']
    if (app / '.mon-sensors-finish').exists():
        paths.append(app / '.mon-sensors-finish/scheduler.lock')
    held = []
    try:
        for p in paths:
            directory(p.parent)
            fd = os.open(str(p), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
            held.append(fd)
            s = os.fstat(fd)
            if not stat.S_ISREG(s.st_mode) or s.st_uid != 0 or s.st_nlink != 1 or s.st_mode & 0o7022:
                raise ValueError('Untrusted scheduler lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        require_idle(app)
        yield
    finally:
        for fd in reversed(held):
            os.close(fd)


def bind(app, dry_run=False, undo=False):
    with binding_lock(app):
        marker = app / BINDING
        if undo:
            old = read_json(marker)
            if old.get('schema') != 'ocrun-workload-binding-v1':
                raise ValueError('Unrecognized workload binding')
            if not dry_run:
                marker.unlink()
            return {'action': 'unbind', 'check': dry_run, 'original_tools_modified': False}
        spec = check()
        # Binding an old finalizer would misleadingly leave legacy programs active.
        installed = read_json(app / '.mon-sensors-finish-install.json')
        if installed.get('version') not in ('0.2.4', '0.2.5'):
            raise ValueError('Install mon-sensors-finish 0.2.4+ before binding the suite with bundled MLC')
        value = {'schema': 'ocrun-workload-binding-v1', 'version': VERSION, 'prefix': str(PREFIX),
                 'manifest': metadata(PREFIX / 'MANIFEST.json')}
        if marker.exists() and read_json(marker) != value:
            raise ValueError('Different binding exists; review and unbind explicitly')
        if not dry_run:
            save(marker, value)
        return {'action': 'bind', 'check': dry_run, 'app': str(app), 'version': VERSION,
                'source_commit': spec['source_commit'], 'original_tools_modified': False}


def available_memory_mb():
    values = {}
    for line in Path('/proc/meminfo').read_text().splitlines():
        parts = line.split()
        if parts and parts[0] in ('MemAvailable:', 'MemFree:'):
            values[parts[0]] = int(parts[1]) // 1024
    amount = values.get('MemAvailable:', values.get('MemFree:', 0))
    # Account for the node's cgroup memory limit, including a small CI/test VM.
    for limit, usage in (('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory.current'),
                         ('/sys/fs/cgroup/memory/memory.limit_in_bytes', '/sys/fs/cgroup/memory/memory.usage_in_bytes')):
        try:
            ceiling = Path(limit).read_text().strip()
            if ceiling.isdigit():
                amount = min(amount, max(0, (int(ceiling) - int(Path(usage).read_text())) // 1048576))
        except (OSError, ValueError):
            pass
    if amount < 256:
        raise ValueError('Less than 256 MiB available memory; workload not started')
    return amount


def imported(name):
    target = EXTERNAL / {'mlc': 'mlc-3.13', 'cpu2017': 'cpu2017-1.0.5'}[name]
    manifest = read_json(target / 'IMPORT.json')
    if manifest.get('tool') != name:
        raise ValueError('Imported tool identity differs')
    for n, expected in manifest['files'].items():
        if Path(n).is_absolute() or '..' in Path(n).parts:
            raise ValueError('Invalid imported tool inventory')
        if metadata(target / n) != expected:
            raise ValueError('Imported tool content changed: ' + n)
    return target


def profile(app, name, runtime):
    marker = app / BINDING
    if not marker.exists() and not marker.is_symlink():
        return None  # Legacy nodes retain their existing adoption behavior.
    binding = read_json(marker)
    if binding.get('version') != VERSION or binding.get('prefix') != str(PREFIX):
        raise ValueError('Unsupported workload binding; recheck package installation')
    if metadata(PREFIX / 'MANIFEST.json') != binding['manifest']:
        raise ValueError('Bound workload package changed; explicit rebind required')
    manifest = check()
    match = P95.fullmatch(name)
    if name not in NAMES and not match:
        hint = 'sysjitter was replaced by cyclictest; update the queued task explicitly' if name == 'sysjitter' else 'task is outside the selected tool set'
        raise ValueError('Unsupported suite task {!r}: {}'.format(name, hint))
    if type(runtime) is not int or not 1 <= runtime <= 2678400:
        raise ValueError('Invalid workload duration')
    canonical = 'cpu2017' if name == 'cpu' else name.replace('_r2', '')
    cpus = len(os.sched_getaffinity(0))
    mode, args, extra = 'timed', [], {}
    tool = 'mprime' if match else canonical
    binary = PREFIX / tool / tool
    work = PREFIX / tool
    if canonical in ('stress', 'stress-ng'):
        args = ['--cpu', str(cpus)]
        if canonical == 'stress-ng':
            args += ['--timeout', str(runtime + 5), '--verify', '--metrics-brief']
    elif match:
        kind, fft = match.groups()
        features = Path('/proc/cpuinfo').read_text().lower().split()
        feature = {'avx': 'avx', 'fma3': 'fma', 'avx512': 'avx512f'}.get(kind)
        if feature and feature not in features:
            raise ValueError('CPU/OS does not expose ' + feature + '; choose an appropriate P95 profile')
        limits = {'1': (4, 88), '2': (150, 205), '4': (4, 8192)}[fft]
        memory = min(60962, available_memory_mb() * 45 // 100) if fft == '4' else 0
        config = ['StressTester=1', 'UsePrimenet=0', 'TortureThreads=' + str(cpus),
                  'MinTortureFFT=' + str(limits[0]), 'MaxTortureFFT=' + str(limits[1]),
                  'TortureMem=' + str(memory), 'TortureTime=6']
        disabled = {'no': ['AVX512F', 'FMA3', 'AVX', 'AVX2', 'FMA4'],
                    'avx': ['AVX512F', 'FMA3', 'FMA4'], 'fma3': ['AVX512F'], 'avx512': []}[kind]
        config += ['CpuSupports' + flag + '=0' for flag in disabled]
        args = ['-t']
        extra = {'p95_config': '\n'.join(config) + '\n', 'memory_mb': memory}
    elif canonical == 'mbw':
        args = ['-n', '5', str(max(1, available_memory_mb() * 30 // 100))]
        mode = 'finite'
    elif canonical == 'cyclictest':
        # Keep kernel/preemption/IRQ configuration unchanged. Native duration permits
        # the tool to write a final summary; the guardian bounds cleanup separately.
        args = ['--policy=fifo', '--priority=80', '--threads=' + str(cpus),
                '--interval=1000', '--distance=0', '--mlockall', '--quiet', '--duration=' + str(runtime)]
        mode = 'native_timed'
    elif canonical == 'unixbench':
        binary = Path('/usr/bin/perl')
        args = [str(PREFIX / 'unixbench/Run')]
        mode = 'finite'
    elif canonical == 'mlc':
        # A bundled file is covered by check() and the bound manifest. Do not
        # silently switch to an old imported copy when packaged bytes are missing.
        work = PREFIX / 'mlc' if 'mlc/mlc' in manifest['files'] else imported('mlc')
        binary = work / 'mlc'
        args = ['-e', '-r']  # Do not change hardware prefetch MSRs in a timed guardian.
        mode = 'finite'
    elif canonical == 'cpu2017':
        work = imported('cpu2017')
        binary = Path('/bin/bash')
        args = ['--noprofile', '--norc', '-c',
                'ulimit -s unlimited; source "$1/shrc" && exec "$1/bin/runcpu" -c cpu2017 --output_root "$2" --reportable --tune=all all',
                'ocrun-spec', str(work)]
        mode = 'finite'
    return dict({'name': name, 'runtime_s': runtime, 'binary': str(binary), 'argv': [str(binary)] + args,
                 'mode': mode, 'cwd': str(work), 'suite_version': VERSION, 'tool': tool,
                 'tool_version': manifest['sources'][tool]['version'], 'suite_prepared': False}, **extra)


def prepare(spec, step_directory):
    """Create isolated mutable files after preflight; repeat task names never share a CWD."""
    if not spec.get('suite_version'):
        return spec
    directory(step_directory.parent)
    step_directory.mkdir(mode=0o700)
    result = dict(spec)
    tool = spec['tool']
    result['suite_prepared'] = True
    result['cwd'] = str(step_directory)
    if tool == 'mprime':
        (step_directory / 'prime.txt').write_text(spec['p95_config'])
        (step_directory / 'prime.txt').chmod(0o600)
        # Keep the original collector's "profile/" task-name convention even
        # though all twelve profiles now share one mprime executable.
        result['argv'] = spec['argv'] + ['-W' + str(step_directory) + '/']
    elif tool == 'unixbench':
        for n in ('tmp', 'results'):
            (step_directory / n).mkdir(mode=0o700)
        shutil.copytree(str(PREFIX / 'unixbench/testdir'), str(step_directory / 'testdir'))
        result['env'] = {'UB_BINDIR': str(PREFIX / 'unixbench/pgms'),
                         'UB_TMPDIR': str(step_directory / 'tmp'),
                         'UB_RESULTDIR': str(step_directory / 'results'),
                         'UB_TESTDIR': str(step_directory / 'testdir')}
    elif tool == 'cyclictest':
        result['argv'] = spec['argv'] + ['--json=' + str(step_directory / 'cyclictest.json')]
    elif tool == 'cpu2017':
        result['cwd'] = spec['cwd']
        # Keep the imported input tree unchanged across repeated executions.
        result['argv'] = spec['argv'] + [str(step_directory / 'spec-output')]
    return result
