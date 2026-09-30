"""Real binaries, only in disposable cloud Linux; no sckocp/hardware acceptance."""
import importlib.util
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tarfile
import time
import unittest
from unittest import mock

sys.path[:0] = ['/src/workload_suite', '/src/finish_addon', '/src/sckocp_api', '/src']
import suite
import workload
import cli as suite_cli
from mon_sensors_plugin.collector import detect_task


def command(args, allowed=(0,), **kwargs):
    p = subprocess.run([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       timeout=30, **kwargs)
    if p.returncode not in allowed:
        raise AssertionError('{}: {}\n{}'.format(args, p.returncode, p.stdout.decode('utf-8', 'replace')))
    return p.stdout.decode('utf-8', 'replace')


class Workloads(unittest.TestCase):
    details = {}
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix='workloads-test-', dir='/root'))
        cls.app = cls.root / 'ocrun'
        cls.app.mkdir(mode=0o700)
        suite.save(cls.app / '.mon-sensors-finish-install.json', {'version': '0.2.4'})
        suite.bind(cls.app)
        cls.details = {}

    def run_spec(self, name, seconds=2):
        spec = suite.profile(self.app, name, seconds)
        step = self.root / (self.id().split('.')[-1] + '-' + name)
        spec = suite.prepare(spec, step)
        records = []
        def record(value):
            records.append(dict(value))
            if value['execution'] == 'running' and name.startswith('p95-no_m'):
                self.assertEqual('P95-M' + name[-1], detect_task())
        result = workload.execute(spec, step / 'output.log', record)
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual([], workload.members(result['process']['session']))
        self.details[name] = result
        return result, step

    def test_01_manifest_and_versions(self):
        spec = suite.check()
        for name, option, expected in [('stress', '--version', '1.0.7'),
                                       ('stress-ng', '--version', '0.22.01'),
                                       ('mprime', '-v', '30.19')]:
            output = command([suite.PREFIX / name / name, option], cwd=str(self.root))
            self.assertIn(expected, output)
            self.details[name + '_version'] = output.strip()
        for p in (suite.PREFIX / n / n for n in ('stress', 'stress-ng', 'mbw', 'cyclictest', 'mprime')):
            output = command(['ldd', p])
            self.assertNotIn('not found', output)
        self.assertEqual('6.0.1', spec['sources']['unixbench']['version'])

    def test_02_stress_deadline_and_cleanup(self):
        result, unused = self.run_spec('stress')
        self.assertEqual('duration_reached', result['execution'])
        self.assertLess(result['elapsed_s'], 6)

    def test_03_stress_ng_deadline_and_cleanup(self):
        result, unused = self.run_spec('stress-ng')
        self.assertEqual('duration_reached', result['execution'])

    def test_04_mbw_actual_output(self):
        result, step = self.run_spec('mbw', 10)
        self.assertEqual('finished', result['execution'])
        self.assertIn('MiB/s', (step / 'output.log').read_text())

    def test_05_p95_m1_m2_m4_distinct(self):
        for mode in (1, 2, 4):
            result, step = self.run_spec('p95-no_m' + str(mode))
            self.assertEqual('duration_reached', result['execution'])
            output = (step / 'output.log').read_text(errors='replace')
            self.assertNotIn('FATAL ERROR', output)
            self.assertTrue((step / 'prime.txt').exists())
            self.assertNotIn('UsePrimenet=1', (step / 'prime.txt').read_text())
        self.assertFalse((suite.PREFIX / 'mprime/prime.txt').exists())

    def test_06_cyclictest_startability(self):
        output = command([suite.PREFIX / 'cyclictest/cyclictest', '--help'], allowed=(0, 1))
        self.assertIn('duration', output.lower())
        # Containers do not establish real-time host scheduling capability.
        spec = suite.profile(self.app, 'cyclictest', 1)
        self.assertIn('--policy=fifo', spec['argv'])
        output = command([suite.PREFIX / 'cyclictest/cyclictest', '--policy=other', '--priority=0',
                          '--threads=1', '--duration=1', '--quiet'], allowed=(0, 1))
        self.details['cyclictest_container'] = output[-2048:]

    def test_07_unixbench_binaries_and_bounded_run(self):
        p = suite.PREFIX / 'unixbench'
        output = command(['/usr/bin/perl', '-c', p / 'Run'])
        self.assertIn('syntax OK', output)
        # Exercise an actual precompiled subtest under the same guardian.
        spec = {'name': 'unixbench', 'binary': str(p / 'pgms/pipe'),
                'argv': [str(p / 'pgms/pipe'), '1'], 'cwd': str(self.root),
                'runtime_s': 5, 'mode': 'finite'}
        result = workload.execute(spec, self.root / 'ub.log', lambda x: None)
        self.assertEqual('finished', result['execution'])
        # Also exercise the actual Perl entry, report output and directory
        # configuration; one short pipe pass is not a full UnixBench score.
        step = self.root / 'unixbench-front-end'
        prepared = suite.prepare(suite.profile(self.app, 'unixbench', 120), step)
        prepared['argv'] += ['-i', '1', '-c', '1', 'pipe']
        result = workload.execute(prepared, step / 'output.log', lambda x: None)
        self.assertEqual('finished', result['execution'], (step / 'output.log').read_text(errors='replace')[-3000:])
        self.assertTrue(any((step / 'results').iterdir()))
        self.details['unixbench_front_end_pipe'] = result

    def test_08_unknown_tasks_do_not_fall_back(self):
        for name in ('sysjitter', 'p95-no_m3', 'bcfi', 'strss', 'ptu'):
            with self.assertRaises(ValueError):
                suite.profile(self.app, name, 60)

    def test_09_package_tamper_rejected_then_restored(self):
        target = suite.PREFIX / 'mbw/mbw'
        original = target.read_bytes()
        attributes = target.stat()
        try:
            target.write_bytes(original + b'changed')
            with self.assertRaisesRegex(ValueError, 'changed'):
                suite.profile(self.app, 'mbw', 5)
        finally:
            target.write_bytes(original)
            os.utime(str(target), ns=(attributes.st_atime_ns, attributes.st_mtime_ns))

    def test_10_binding_rollback_keeps_originals(self):
        sentinel = self.app / 'original-tool'
        sentinel.write_text('original untouched')
        suite.bind(self.app, undo=True)
        self.assertIsNone(suite.profile(self.app, 'stress', 1))
        self.assertEqual('original untouched', sentinel.read_text())
        suite.bind(self.app)

    def test_11_early_exit_is_not_success(self):
        binary = self.root / 'early.sh'
        binary.write_text('#!/bin/sh\nexit 0\n')
        binary.chmod(0o700)
        spec = {'binary': str(binary), 'argv': [str(binary)], 'cwd': str(self.root),
                'runtime_s': 3, 'mode': 'timed'}
        result = workload.execute(spec, self.root / 'early.log', lambda x: None)
        self.assertEqual('early_exit', result['execution'])

    def test_12_duplicate_profiles_keep_mutable_state_separate(self):
        spec = suite.profile(self.app, 'p95-no_m2', 10)
        a = suite.prepare(spec, self.root / 'duplicate-a')
        b = suite.prepare(spec, self.root / 'duplicate-b')
        self.assertNotEqual(a['cwd'], b['cwd'])
        self.assertNotEqual(a['argv'], b['argv'])
        self.assertEqual((Path(a['cwd']) / 'prime.txt').read_bytes(),
                         (Path(b['cwd']) / 'prime.txt').read_bytes())

    def test_13_untrusted_import_rejected_before_writing(self):
        source = self.root / 'wrong-archive.tgz'
        source.write_bytes(b'not the pinned original archive')
        for tool in ('mlc', 'cpu2017'):
            with self.assertRaisesRegex(ValueError, 'SHA-256 differs'):
                suite_cli.import_archive(tool, source, False)
        self.assertFalse(suite.EXTERNAL.exists())

    def test_14_native_timed_early_exit_and_completion(self):
        binary = self.root / 'native-timer.sh'
        binary.write_text('#!/bin/sh\nsleep 1\n')
        binary.chmod(0o700)
        spec = {'binary': str(binary), 'argv': [str(binary)], 'cwd': str(self.root),
                'runtime_s': 4, 'mode': 'native_timed'}
        result = workload.execute(spec, self.root / 'native-early.log', lambda x: None)
        self.assertEqual('early_exit', result['execution'])
        spec['runtime_s'] = 1
        result = workload.execute(spec, self.root / 'native-complete.log', lambda x: None)
        self.assertEqual('duration_reached', result['execution'])

    def test_15_imported_spec_uses_own_output_directory(self):
        # Contract test; this is not a run of the user's licensed SPEC binaries.
        original = suite.imported
        try:
            suite.imported = lambda name: Path('/var/lib/ocrun-workloads/cpu2017-1.0.5')
            spec = suite.profile(self.app, 'cpu2017', 60)
            prepared = suite.prepare(spec, self.root / 'spec-run')
        finally:
            suite.imported = original
        self.assertIn('--output_root "$2"', prepared['argv'][4])
        self.assertEqual(str(self.root / 'spec-run/spec-output'), prepared['argv'][-1])

    def test_16_spec_import_contract_and_idempotence(self):
        source = self.root / 'synthetic-spec.tar.gz'
        files = {'version.txt': b'1.0.5\n', 'bin/runcpu': b'fixture, never executed\n',
                 'shrc': b'fixture, never sourced\n'}
        with tarfile.open(str(source), 'w:gz') as bundle:
            root = tarfile.TarInfo('ocrun/cpu2017/')
            root.type = tarfile.DIRTYPE
            bundle.addfile(root)
            for name, data in files.items():
                member = tarfile.TarInfo('ocrun/cpu2017/' + name)
                member.size, member.mode = len(data), 0o755
                bundle.addfile(member, io.BytesIO(data))
        checksum = hashlib.sha256(source.read_bytes()).hexdigest()
        inventory = suite.check()
        inventory['sources']['cpu2017']['sha256'] = checksum
        imported_root = self.root / 'imports'
        with mock.patch.object(suite, 'check', return_value=inventory), mock.patch.object(suite, 'EXTERNAL', imported_root):
            self.assertEqual('checked', suite_cli.import_archive('cpu2017', source, True)['status'])
            self.assertFalse(imported_root.exists())
            result = suite_cli.import_archive('cpu2017', source, False)
            self.assertEqual('imported', result['status'])
            destination = Path(result['path'])
            for name, data in files.items():
                self.assertEqual(data, (destination / name).read_bytes())
            self.assertEqual('already_imported', suite_cli.import_archive('cpu2017', source, False)['status'])
            (destination / 'version.txt').write_text('changed')
            with self.assertRaisesRegex(ValueError, 'content changed'):
                suite_cli.import_archive('cpu2017', source, False)
        self.assertEqual(checksum, hashlib.sha256(source.read_bytes()).hexdigest())

    def test_17_mlc_original_payload_and_version(self):
        inventory = suite.check()
        self.assertEqual('3', inventory['package_revision'])
        self.assertEqual('3.13', inventory['sources']['mlc']['version'])
        self.assertEqual('included', inventory['sources']['mlc']['delivery'])
        archive = self.root / 'mlc-original.tar.gz'
        shutil.copyfile('/src/workload_suite/vendor/mlc.tar.gz', str(archive))
        self.assertEqual(inventory['sources']['mlc']['sha256'], hashlib.sha256(archive.read_bytes()).hexdigest())
        mapping = {'Linux/mlc': 'mlc/mlc', 'Linux/redist.txt': 'licenses/mlc/redist.txt',
                   'Documentation/readme_mlc_v3.13.rst': 'mlc/readme_mlc_v3.13.rst',
                   'Intel Memory Latency Tools Outbound License Agreement.pdf':
                   'licenses/mlc/Intel Memory Latency Tools Outbound License Agreement.pdf'}
        with tarfile.open(str(archive)) as bundle:
            for original, installed in mapping.items():
                self.assertEqual(bundle.extractfile(original).read(), (suite.PREFIX / installed).read_bytes())
        self.assertEqual(0o755, (suite.PREFIX / 'mlc/mlc').stat().st_mode & 0o7777)
        output = command([suite.PREFIX / 'mlc/mlc', '-h'], allowed=(0, 1))
        self.assertIn('3.13', output)
        self.details['mlc_version'] = output[:2048]
        dependencies = command(['ldd', suite.PREFIX / 'mlc/mlc'], allowed=(0, 1))
        self.assertNotIn('not found', dependencies)
        self.assertTrue('libc.so' in dependencies or 'statically linked' in dependencies
                        or 'not a dynamic executable' in dependencies, dependencies)

    def test_18_mlc_uses_packaged_program_without_import(self):
        with mock.patch.object(suite, 'imported', side_effect=AssertionError('Unexpected external MLC import')):
            profile = suite.profile(self.app, 'mlc', 60)
        self.assertEqual(str(suite.PREFIX / 'mlc/mlc'), profile['binary'])
        self.assertEqual(['-e', '-r'], profile['argv'][1:])
        self.assertEqual('finite', profile['mode'])
        archive = self.root / 'mlc-import-original.tar.gz'
        shutil.copyfile('/src/workload_suite/vendor/mlc.tar.gz', str(archive))
        for dry_run in (True, False):
            result = suite_cli.import_archive('mlc', archive, dry_run)
            self.assertEqual('included', result['status'])
        self.assertFalse(suite.EXTERNAL.exists())

    def test_19_mlc_changes_never_fall_back_to_import(self):
        binary = suite.PREFIX / 'mlc/mlc'
        content, attributes = binary.read_bytes(), binary.stat()
        try:
            binary.write_bytes(content + b'changed')
            with self.assertRaises(ValueError), mock.patch.object(suite, 'imported', side_effect=AssertionError('Unsafe fallback')):
                suite.profile(self.app, 'mlc', 60)
            binary.unlink()
            with self.assertRaises((ValueError, OSError)), mock.patch.object(suite, 'imported', side_effect=AssertionError('Unsafe fallback')):
                suite.profile(self.app, 'mlc', 60)
        finally:
            binary.write_bytes(content)
            binary.chmod(attributes.st_mode & 0o7777)
            os.utime(str(binary), ns=(attributes.st_atime_ns, attributes.st_mtime_ns))

    def test_20_mlc_bounded_real_idle_latency(self):
        # No /dev/msr or hardware prefetch changes, only a small user-space probe.
        binary = str(suite.PREFIX / 'mlc/mlc')
        cpu = min(os.sched_getaffinity(0))
        spec = {'name': 'mlc', 'binary': binary, 'argv': [binary, '--idle_latency', '-e', '-r',
                '-b8m', '-c' + str(cpu), '-t1'], 'cwd': str(self.root), 'mode': 'finite', 'runtime_s': 15}
        output = self.root / 'mlc-latency.log'
        result = workload.execute(spec, output, lambda unused: None)
        self.assertEqual('finished', result['execution'], output.read_text(errors='replace'))
        self.assertTrue(result['cleanup_confirmed'])
        self.assertEqual([], workload.members(result['process']['session']))
        self.details['mlc_idle_latency'] = dict(result, output=output.read_text(errors='replace')[-2048:])


if __name__ == '__main__':
    if os.environ.get('GITHUB_ACTIONS') != 'true' or sys.platform != 'linux':
        raise SystemExit('Cloud Linux only')
    os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(Workloads))
    Path('/results/tests.json').write_text(json.dumps({'tests': result.testsRun, 'passed': result.wasSuccessful(),
        'failures': len(result.failures), 'errors': len(result.errors), 'source_commit': os.environ.get('OCRUN_SOURCE_COMMIT'),
        'os_release': Path('/etc/os-release').read_text(), 'details': Workloads.details,
        'scope': 'container user space, short real workloads, no hardware stability or real-time acceptance'}, indent=2))
    sys.exit(0 if result.wasSuccessful() else 1)
