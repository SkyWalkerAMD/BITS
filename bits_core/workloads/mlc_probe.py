"""Fail early on the pinned MLC program before compiling the larger tool suite."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile

if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
    raise SystemExit('Cloud Linux only')
root = Path(__file__).absolute().parent
entry = json.loads((root / 'sources.json').read_text())['sources']['mlc']
archive = root / 'vendor/mlc.tar.gz'
assert hashlib.sha256(archive.read_bytes()).hexdigest() == entry['sha256']
with tempfile.TemporaryDirectory(prefix='ocrun-mlc-probe-') as temporary:
    binary = Path(temporary) / 'mlc'
    with tarfile.open(str(archive)) as bundle:
        binary.write_bytes(bundle.extractfile('Linux/mlc').read())
    binary.chmod(0o755)
    cpu = min(os.sched_getaffinity(0))
    for args in (['-h'], ['--idle_latency', '-e', '-r', '-b8m', '-c' + str(cpu), '-t1']):
        result = subprocess.run([str(binary)] + args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                cwd=temporary, timeout=20)
        output = result.stdout.decode('utf-8', 'replace')
        print(output, flush=True)
        if result.returncode not in ((0, 1) if args == ['-h'] else (0,)) or '3.13' not in output:
            raise SystemExit('Pinned MLC startability probe failed (exit {})'.format(result.returncode))
