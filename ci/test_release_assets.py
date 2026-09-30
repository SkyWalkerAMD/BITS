"""Publication regressions: omissions and unreviewed attachments fail closed."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from ci.release_assets import selections


class PublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.names = ['README.md', 'SOURCES.json', 'ACCEPTANCE-REPORT.md', 'CONTROL-MANUAL.md',
                      'WORKLOADS-MANUAL.md', 'SERVER-MANUAL.md', 'SCKOCP-API.md', 'OPTIMIZATION.md',
                      'sources/ocrun-source.tar.gz', 'example/report-preview.html',
                      'example/report-print-preview.pdf']
        self.names += ['standalone/' + n for n in (
            'sckocp-api-0.3.2.run', 'mon-sensors-plugin-0.12.12.run',
            'mon-sensors-finish-0.2.5.run', 'mon-sensors-control-0.2.0.run',
            'mon-sensors-report-py36-0.2.0.run', 'ocrun-workloads-0.1.0-3.el8.x86_64.rpm',
            'ocrun-workloads_0.1.0-3_amd64.deb')]
        packages = {}
        for role in ('node-rpm', 'node-deb', 'center-rpm', 'center-deb'):
            name = role + '.package'
            self.names.append(name)
            packages[role] = {'file': name, 'sha256': hashlib.sha256(name.encode()).hexdigest()}
        for name in self.names:
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(name.encode())
        (self.root / 'RELEASE.json').write_text(json.dumps({'packages': packages}))
        self.names.append('RELEASE.json')
        self.manifest()

    def manifest(self):
        (self.root / 'SHA256SUMS').write_text(''.join(
            hashlib.sha256((self.root / n).read_bytes()).hexdigest() + '  ' + n + '\n'
            for n in self.names))

    def test_all_deployable_components_are_direct_attachments(self):
        found = selections(self.root)
        self.assertEqual(len(found), 23)
        for name in self.names:
            self.assertIn(self.root / name, found)

    def test_bits_names_preserve_strict_attachment_inventory(self):
        for old, new in (('sources/ocrun-source.tar.gz', 'sources/bits-source.tar.gz'),
                         ('standalone/ocrun-workloads-0.1.0-3.el8.x86_64.rpm', 'standalone/bits-o-workloads-0.1.0-3.el8.x86_64.rpm'),
                         ('standalone/ocrun-workloads_0.1.0-3_amd64.deb', 'standalone/bits-o-workloads_0.1.0-3_amd64.deb')):
            (self.root / old).rename(self.root / new)
            self.names[self.names.index(old)] = new
        self.manifest()
        self.assertEqual(len(selections(self.root)), 23)

    def test_modified_component_cannot_be_published(self):
        (self.root / 'standalone/sckocp-api-0.3.2.run').write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'Unverified'):
            selections(self.root)

    def test_unknown_component_needs_explicit_review(self):
        name = 'standalone/unknown-1.0.0.run'
        (self.root / name).write_bytes(b'unknown')
        self.names.append(name)
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'Review new'):
            selections(self.root)

    def test_missing_component_cannot_silently_disappear(self):
        name = 'standalone/mon-sensors-report-py36-0.2.0.run'
        (self.root / name).unlink()
        self.names.remove(name)
        self.manifest()
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            selections(self.root)

    def test_symlink_cannot_replace_verified_attachment(self):
        target = self.root / 'standalone/sckocp-api-0.3.2.run'
        saved = target.read_bytes()
        target.unlink()
        (self.root / 'outside').write_bytes(saved)
        target.symlink_to('../outside')
        with self.assertRaisesRegex(ValueError, 'Unverified'):
            selections(self.root)


if __name__ == '__main__':
    unittest.main()
