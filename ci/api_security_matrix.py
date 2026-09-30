"""Security regression and previous-release upgrade, in disposable Linux only."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def run(args, env=None):
    result = subprocess.run([str(x) for x in args], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, env=env, timeout=45)
    if result.returncode:
        raise AssertionError('Fixture command failed: ' + result.stderr.decode('utf-8', 'replace')[-3000:])
    return result.stdout


def main():
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux' or os.geteuid() != 0:
        raise SystemExit('Disposable cloud Linux root only')
    out = Path('/results')
    out.mkdir(exist_ok=True)
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_sckocp*.py')
    suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_plugin*.py'))
    suite.addTests(unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern='test_mon_sensors.py'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    summary = {'status': 'failed', 'tests': result.testsRun, 'failures': len(result.failures),
        'errors': len(result.errors), 'skipped': len(result.skipped),
        'python': sys.version, 'os_release': Path('/etc/os-release').read_text(),
        'source_commit': os.environ['GITHUB_SHA'], 'hardware': 'synthetic fixtures only',
        'checks': []}
    try:
        if not result.wasSuccessful() or result.skipped or result.testsRun < 100:
            raise AssertionError('Security regression failed or skipped tests')
        with tempfile.TemporaryDirectory(prefix='api-security-') as temporary:
            path = Path(temporary)
            prefix, bindir = path / 'api', path / 'bin'
            options = ['--prefix', prefix, '--bin-dir', bindir]
            old = Path('/packages/old-sckocp-api-0.3.1.run')
            new = Path('/packages/sckocp-api-0.3.2.run')
            run(['/bin/bash', old] + options)
            original = prefix / 'sckocp_api/__init__.py'
            before = original.read_bytes()
            marker = path / 'old-import-executed'
            original.write_text('open(' + repr(str(marker)) + ', "w").close()\n' + before.decode())
            run([bindir / 'sckocp-api', '--help'])
            assert marker.exists(), 'Old CLI fixture did not reproduce unchecked module import'
            marker.unlink()
            original.write_bytes(before)
            summary['checks'].append('0.3.1 unchecked module import reproduced in root-private fixture')
            injected = dict(os.environ, TAR_OPTIONS='--help', GZIP='--invalid-api-fixture')
            run(['/bin/bash', new] + options + ['--check'], env=injected)
            assert original.read_bytes() == before
            installed = json.loads(run(['/bin/bash', new] + options, env=injected))
            assert Path(installed['backup']).is_dir() and Path(installed['command_backup']).is_file()
            assert b'0.3.2' in run([bindir / 'sckocp-api', '--version'])
            summary['checks'].append('0.3.1 upgrades without native calls; archive environment options ignored; backup retained')
            current = original.read_bytes()
            original.write_text('open(' + repr(str(marker)) + ', "w").close()\n' + current.decode())
            bad = subprocess.run([str(bindir / 'sckocp-api'), '--help'], stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=15)
            assert bad.returncode == 1 and not bad.stderr
            assert json.loads(bad.stdout.decode())['status'] == 'integrity_error'
            assert not marker.exists()
            original.write_bytes(current)
            summary['checks'].append('0.3.2 rejects replacement before package code executes')
            run(['/bin/bash', old] + options)
            assert b'0.3.1' in run([bindir / 'sckocp-api', '--version'])
            assert original.read_bytes() == before
            run(['/bin/bash', new] + options)
            assert b'0.3.2' in run([bindir / 'sckocp-api', '--version'])
            summary['checks'].append('Trusted old installer rolls back new seven-file runtime; re-upgrade succeeds')
        summary['status'] = 'passed'
    finally:
        (out / 'validation.json').write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps(summary))


if __name__ == '__main__':
    main()
