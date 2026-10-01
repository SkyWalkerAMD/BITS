"""Package boundary regression tests, executed only by Linux CI."""
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from bits_core.layout import LEGACY, NATIVE
from bits_core.packaging import layout_bytes


class LayoutTests(unittest.TestCase):
    def test_native_selection_is_build_time_not_environment(self):
        for native, expected in ((True, NATIVE), (False, LEGACY)):
            scope = {}
            with patch.dict(os.environ, {'BITS_LAYOUT': 'ocrun-compat' if native else 'bits-v1'}):
                exec(compile(layout_bytes(native), '<verified-package-layout>', 'exec'), scope)
            self.assertEqual(expected, scope['LAYOUT'])

    def test_legacy_contract_retains_original_entrypoints_and_locks(self):
        self.assertEqual('ocb', LEGACY.scheduler)
        self.assertEqual('.mon-sensors-runtime.lock', LEGACY.runtime_lock)
        self.assertEqual('/etc/ocrun-node/connection.json', LEGACY.connection)
        self.assertEqual('.mon-sensors-finish', LEGACY.state)
        self.assertFalse(LEGACY.native)

    def test_native_template_has_no_legacy_executable_or_hook(self):
        from distribution.build import template, sha
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            template(root)
            app = root / 'template'
            names = {p.relative_to(app).as_posix() for p in app.rglob('*') if p.is_file()}
            self.assertTrue({'scheduler', 'batch', 'collect', 'collector.conf', 'installation.json'} <= names)
            self.assertFalse(any('mon-sensors' in n or n in ('ocb', 'oct') or n.endswith('hook.sh') for n in names))
            self.assertFalse(any('mon_sensors_plugin/' in n or n.endswith('legacy_runtime.py') for n in names))
            manifest = json.loads((app / 'installation.json').read_text())
            self.assertEqual(names - {'installation.json'}, set(manifest['managed_files']))
            for name, expected in manifest['managed_files'].items():
                self.assertEqual(expected, sha(app / name))
            collector = app / NATIVE.collector_dir
            inventory = json.loads((collector / NATIVE.collector_marker).read_text())
            self.assertIn('bits_layout.py', inventory['files'])
            self.assertEqual(hashlib.sha256(layout_bytes(True)).hexdigest(), inventory['files']['bits_layout.py'])
            self.assertIn('collect.py', inventory['files'])

    def test_native_layout_has_dedicated_paths(self):
        for value in NATIVE:
            if isinstance(value, str):
                self.assertNotIn('ocrun', value)
                self.assertNotIn('mon-sensors', value)
        self.assertNotEqual(LEGACY.runtime_fd, NATIVE.runtime_fd)
        self.assertNotEqual(LEGACY.workload_prefix, NATIVE.workload_prefix)


if __name__ == '__main__':
    unittest.main()
