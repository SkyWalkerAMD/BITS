"""Temporary prerequisites for disposable GitHub-hosted MLC tests, never shipped."""
import contextlib
import os
from pathlib import Path
import subprocess
import sys


@contextlib.contextmanager
def reserved():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux' or os.geteuid() != 0:
        raise SystemExit('Disposable cloud Linux root only')
    paths = sorted(Path('/sys/devices/system/node').glob('node*/hugepages/hugepages-2048kB/nr_hugepages'))
    if not paths:
        raise RuntimeError('Cloud runner has no 2 MiB hugepage support')
    original = {p: int(p.read_text()) for p in paths}
    requested = sum(max(0, 1024 - count) for count in original.values()) * 2 * 1024 * 1024
    available = next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                     if line.startswith('MemAvailable:'))
    if requested + 2 * 1024 ** 3 > available:
        raise RuntimeError('Insufficient cloud memory to reserve MLC prerequisites and retain 2 GiB for tests')
    try:
        for path, count in original.items():
            if count < 1024:
                path.write_text('1024\n')
            if int(path.read_text()) < 1024:
                raise RuntimeError('Cloud runner could not reserve 1024 hugepages per NUMA node')
        print('Cloud-only MLC prerequisite: at least 1024 2-MiB pages per NUMA node; restored on exit.', flush=True)
        yield
    finally:
        for path, count in original.items():
            path.write_text(str(count) + '\n')
        if any(int(p.read_text()) != n for p, n in original.items()):
            raise RuntimeError('Cloud hugepage reservation did not return to its original value')
        print('Cloud hugepage reservation restored.', flush=True)


if __name__ == '__main__':
    with reserved():
        subprocess.run(sys.argv[1:], check=True)
