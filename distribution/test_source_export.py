"""Meaningful release-boundary regressions; run in cloud Linux only."""
import hashlib
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest

from distribution.source_export import checked_names, export_sources, ROOT


class CustomerSourceTests(unittest.TestCase):
    def test_rejects_private_sources_links_and_patterns(self):
        for name in ('research/sckocp120/src/main.c', 'research/sckocp/activation/server/sign.c',
                     'research/other.c', 'ci/archive/workloads-release.py.txt',
                     'integrations/sckocp/src/main.c', 'docs/security/SCKOCP-NATIVE-SECURITY.md',
                     'drafts/notes.md', '../x', '/tmp/x', 'distribution/', 'distribution/*.py',
                     'secret.key', 'sckocp_api/__pycache__/x.pyc'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                checked_names([name])

    def test_export_from_commit_excludes_native_reference_and_untracked_files(self):
        commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=str(ROOT)).decode().strip()
        with tempfile.TemporaryDirectory() as tmp:
            first, second = Path(tmp) / 'one.tar.gz', Path(tmp) / 'two.tar.gz'
            result = export_sources(first, commit)
            export_sources(second, commit)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with tarfile.open(str(first)) as archive:
                names = archive.getnames()
                self.assertFalse(any(n.startswith(('research/', 'ci/', 'integrations/sckocp', 'drafts/', '.git/')) for n in names))
                self.assertIn('bits_core/workloads/vendor/stress.tar.gz', names)
                self.assertIn('bits_core/workloads/vendor/stress-ng.tar.gz', names)
                self.assertIn('sckocp_api/security.py', names)
                manifest = json.load(archive.extractfile('SOURCE-MANIFEST.json'))
                self.assertEqual(commit, manifest['source_commit'])
                self.assertEqual(result['files'], len(manifest['files']))
                for name, entry in manifest['files'].items():
                    data = archive.extractfile(name).read()
                    self.assertEqual(hashlib.sha256(data).hexdigest(), entry['sha256'])
                    self.assertEqual(len(data), entry['bytes'])

    def test_symlink_and_missing_source_refuse_release(self):
        for symlink in (True, False):
            with self.subTest(symlink=symlink), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'distribution').mkdir()
                if symlink:
                    (root / 'file.py').symlink_to('/etc/passwd')
                (root / 'distribution/customer-sources.json').write_text(json.dumps({
                    'schema': 'ocrun-customer-sources-v1',
                    'files': ['file.py', 'distribution/customer-sources.json']}))
                for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Cloud', '-c', 'user.email=cloud@example.invalid',
                                                        'commit', '-qm', 'fixture']):
                    subprocess.run(['git'] + args, cwd=tmp, check=True)
                commit = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=tmp).decode().strip()
                with self.assertRaisesRegex(ValueError, 'non-regular'):
                    export_sources(root / 'out.tar.gz', commit, root)


if __name__ == '__main__':
    unittest.main()
