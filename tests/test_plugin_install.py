"""Device installer automation and safe refusal checks; run only in cloud Linux."""
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import unittest
from unittest import mock

from bits_core.collector import install as installer


ROOT = Path(__file__).resolve().parents[1]
BASELINE = ROOT / "integrations" / "mon-sensors" / "upstream"


class PluginInstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "package"
        self.source.mkdir()
        for name in installer.FILES + tuple(installer.COMPATIBILITY_FILES.values()):
            path = self.source / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / name).read_bytes())
            path.chmod(0o755 if name == installer.ENTRY else 0o644)
        self.app = self.device("device")

    def device(self, name):
        app = self.root / name / "ocrun"
        app.mkdir(parents=True)
        (app / "py").mkdir()
        for relative in installer.SCRIPTS:
            source = BASELINE / ("mon-analyse-log.py" if relative.startswith("py/") else relative)
            shutil.copyfile(str(source), str(app / relative))
            (app / relative).chmod(0o755)
        (app / "oc.env").write_text("exit 97 # must never be sourced by the installer\n")
        return app

    def snapshot(self, path):
        return {str(item.relative_to(path)): (stat.S_IMODE(item.lstat().st_mode),
                item.read_bytes() if item.is_file() and not item.is_symlink() else None)
                for item in path.rglob("*")}

    def cli(self, arguments, app=None):
        output = io.StringIO()
        with mock.patch.object(installer, "discover_app", return_value=app or self.app), \
                mock.patch("sys.stdout", output):
            installer.main(["--source", str(self.source)] + arguments)
        return json.loads(output.getvalue())

    def test_check_is_read_only_and_does_not_create_lock_or_backup(self):
        before = self.snapshot(self.root)
        result = self.cli(["--check"])
        self.assertEqual("checked", result["status"])
        self.assertEqual("install", result["action"])
        self.assertTrue(result["discovered"])
        self.assertTrue(result["read_only"])
        self.assertEqual("sckocp", result["backend"])
        self.assertEqual("not_checked", result["native_authorization"])
        self.assertEqual(before, self.snapshot(self.root))

    def test_automatic_first_install_enables_provider_then_preserves_user_selection(self):
        result = self.cli([])
        self.assertEqual("installed", result["status"])
        self.assertEqual(b"sckocp\n", (self.app / installer.BACKEND_FILE).read_bytes())
        installer.install(self.app, self.source, "legacy")
        before = self.snapshot(self.app)
        result = self.cli([])
        self.assertEqual("already_installed", result["status"])
        self.assertEqual("legacy", result["backend"])
        self.assertEqual(before, self.snapshot(self.app))

    def test_explicit_app_and_python_api_keep_legacy_default(self):
        result = self.cli(["--app", str(self.app)])
        self.assertFalse(result["discovered"])
        self.assertEqual("legacy", result["backend"])
        self.assertFalse((self.app / installer.BACKEND_FILE).exists())
        other = self.device("api")
        self.assertEqual("legacy", installer.install(other, self.source)["backend"])
        # An older installation without a configuration must not switch backends.
        result = self.cli([])
        self.assertEqual("legacy", result["backend"])
        self.assertFalse((self.app / installer.BACKEND_FILE).exists())

    def test_explicit_backend_overrides_automatic_install_default(self):
        self.assertEqual("auto", self.cli(["--backend", "auto"])["backend"])
        self.assertEqual(b"auto\n", (self.app / installer.BACKEND_FILE).read_bytes())

    def test_modules_only_preserves_later_scheduler_and_report_hooks(self):
        installer.install(self.app, self.source, 'sckocp')
        for name in installer.SCRIPTS:
            with (self.app / name).open('ab') as stream:
                stream.write(b'# later managed scheduler/report hook\n')
        scripts = {n: (self.app / n).read_bytes() for n in installer.SCRIPTS}
        info = {n: (self.app / n).stat().st_ino for n in installer.SCRIPTS}
        # Simulate a newer collector payload, leaving the installed manifest intact.
        with (self.source / 'bits_core/collector/collector.py').open('ab') as stream:
            stream.write(b'\n# updated module\n')
        before = self.snapshot(self.app)
        installer.install(self.app, self.source, modules_only=True, check=True)
        self.assertEqual(before, self.snapshot(self.app))
        result = installer.install(self.app, self.source, modules_only=True)
        self.assertTrue(result['modules_only'])
        self.assertEqual(scripts, {n: (self.app / n).read_bytes() for n in installer.SCRIPTS})
        self.assertEqual(info, {n: (self.app / n).stat().st_ino for n in installer.SCRIPTS})
        self.assertEqual(b'sckocp\n', (self.app / installer.BACKEND_FILE).read_bytes())

    def test_modules_only_requires_managed_install_and_refuses_edits(self):
        with self.assertRaisesRegex(ValueError, 'existing managed plugin'):
            installer.install(self.app, self.source, modules_only=True)
        installer.install(self.app, self.source)
        module = self.app / installer.HELPER / 'bits_core/collector/collector.py'
        with module.open('ab') as stream:
            stream.write(b'\n# local edit\n')
        with self.assertRaisesRegex(ValueError, 'Locally modified'):
            installer.install(self.app, self.source, modules_only=True)

    def test_old_inventory_is_verified_before_filename_migration(self):
        installer.install(self.app, self.source, 'sckocp')
        helper = self.app / installer.HELPER
        marker_path = helper / installer.MARKER
        marker = json.loads(marker_path.read_text())
        legacy_file = helper / 'mon_sensors_plugin/collector.py'
        legacy_file.write_bytes(b'# previously managed legacy module\n')
        marker['files']['mon_sensors_plugin/collector.py'] = hashlib.sha256(legacy_file.read_bytes()).hexdigest()
        marker_path.write_text(json.dumps(marker))
        with legacy_file.open('ab') as stream:
            stream.write(b'# local customization\n')
        before = self.snapshot(self.app)
        with self.assertRaisesRegex(ValueError, 'Locally modified'):
            installer.install(self.app, self.source, modules_only=True)
        self.assertEqual(before, self.snapshot(self.app))
        legacy_file.write_bytes(b'# previously managed legacy module\n')
        # Force a new payload so the verified old helper is archived/replaced.
        with (self.source / 'bits_core/collector/collector.py').open('ab') as stream:
            stream.write(b'\n# new release\n')
        installer.install(self.app, self.source, modules_only=True)
        self.assertFalse(legacy_file.exists())
        self.assertTrue((helper / 'mon_sensors_plugin/runtime.py').is_file())

    def test_automatic_upgrade_preserves_old_bridge_without_backend_file(self):
        path = self.app / "mon-sensors"
        first, rest = path.read_text().split("\n", 1)
        path.write_text(first + "\n" + installer.V2_DISPATCH + rest)
        result = self.cli([])
        self.assertEqual("legacy", result["backend"])
        self.assertFalse((self.app / installer.BACKEND_FILE).exists())
        self.assertIn(installer.DISPATCH, path.read_text())

    def test_discovery_deduplicates_and_refuses_ambiguity(self):
        self.assertEqual(self.app, installer.discover_app([self.root / "missing", self.app, self.app]))
        other = self.device("other")
        with self.assertRaisesRegex(ValueError, "Multiple.*--app"):
            installer.discover_app([self.app, other])
        (other / "oct").write_text("unknown customization\n")
        self.assertEqual(self.app, installer.discover_app([other, self.app]))
        with self.assertRaisesRegex(ValueError, "No compatible.*--app"):
            installer.discover_app([other])

    def test_discovery_reads_home_and_command_links_without_running_commands(self):
        commands = self.root / "commands"
        commands.mkdir()
        (commands / "oct").symlink_to(self.app / "oct")
        with mock.patch.object(Path, "home", return_value=self.root / "missing"), \
                mock.patch.object(shutil, "which", side_effect=lambda name: str(commands / "oct")
                                  if name == "oct" else None):
            self.assertEqual(self.app, installer.discover_app())
        self.assertEqual("exit 97 # must never be sourced by the installer\n",
                         (self.app / "oc.env").read_text())

    def test_active_monitor_refuses_install_and_check_without_stopping_it(self):
        before = self.snapshot(self.root)
        with mock.patch.object(installer, "active_monitors", return_value=[12345]), \
                mock.patch.object(os, "kill") as kill:
            for check in (False, True):
                with self.assertRaisesRegex(ValueError, "Monitoring is active.*12345"):
                    installer.install(self.app, self.source, check=check)
            kill.assert_not_called()
        self.assertEqual(before, self.snapshot(self.root))

    def test_process_matching_scopes_interpreter_and_relative_commands_to_device(self):
        proc = self.root / "proc"
        proc.mkdir()
        commands = [
            ["/bin/bash", str(self.app / "mon-sensors"), "2"],
            ["/usr/bin/python3.6", "-B", str(self.app / installer.ENTRY), "2"],
            ["python3", "mon-sensors-plugin", "2"],
            [str(self.app / installer.HELPER / installer.ENTRY), "2"],
            ["tee", str(self.app / installer.ENTRY)],
            ["bash", "-c", str(self.app / "mon-sensors")],
            ["python3", str(self.app / installer.ENTRY), "--stop-app", str(self.app)],
            ["bash", str(self.root / "other" / "mon-sensors")],
        ]
        for index, arguments in enumerate(commands):
            folder = proc / str(900001 + index)
            folder.mkdir()
            (folder / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in arguments) + b"\0")
            (folder / "cwd").symlink_to(self.app, target_is_directory=True)
        self.assertEqual([900001, 900002, 900003, 900004], installer.active_monitors(self.app, str(proc)))

    def test_locally_modified_helper_refused_without_overwriting_it(self):
        installer.install(self.app, self.source)
        module = self.app / installer.HELPER / "sckocp_api/provider.py"
        module.write_text("# customer customization\n")
        before = self.snapshot(self.app)
        for check in (False, True):
            with self.assertRaisesRegex(ValueError, "Locally modified plugin module"):
                installer.install(self.app, self.source, check=check)
        self.assertEqual(before, self.snapshot(self.app))

    def test_upgrade_accepts_new_package_and_repairs_missing_managed_dependencies(self):
        installer.install(self.app, self.source)
        relative = "sckocp_api/provider.py"
        updated = (self.source / relative).read_bytes() + b"\n# updated packaged provider\n"
        (self.source / relative).write_bytes(updated)
        self.assertEqual("installed", installer.install(self.app, self.source)["status"])
        self.assertEqual(updated, (self.app / installer.HELPER / relative).read_bytes())
        (self.app / installer.HELPER / relative).unlink()
        self.assertEqual("installed", installer.install(self.app, self.source)["status"])
        self.assertEqual(updated, (self.app / installer.HELPER / relative).read_bytes())

    def test_unmanaged_entrypoint_and_symlinked_helper_are_preserved(self):
        entry = self.app / installer.ENTRY
        entry.write_text("# unrelated local command\n")
        with self.assertRaisesRegex(ValueError, "unchanged managed entrypoint"):
            installer.install(self.app, self.source)
        self.assertEqual("# unrelated local command\n", entry.read_text())
        entry.unlink()
        (self.app / installer.HELPER).symlink_to(self.source, target_is_directory=True)
        before = self.snapshot(self.source)
        with self.assertRaisesRegex(ValueError, "Unsafe existing plugin"):
            installer.install(self.app, self.source)
        self.assertEqual(before, self.snapshot(self.source))

    def test_writable_source_or_target_ancestors_and_directory_aliases_are_rejected(self):
        for directory in (self.source, self.app.parent):
            directory.chmod(0o777)
            try:
                with self.assertRaisesRegex(ValueError, "Unsafe installation directory"):
                    installer.install(self.app, self.source, check=True)
            finally:
                directory.chmod(0o755)
        alias = self.root / "alias"
        alias.symlink_to(self.app, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "Unsafe installation directory"):
            installer.install(alias, self.source, check=True)

    def test_unsafe_source_files_and_cross_boundary_source_are_rejected(self):
        path = self.source / installer.ENTRY
        path.chmod(0o777)
        with self.assertRaisesRegex(ValueError, "Unsafe installation file"):
            installer.install(self.app, self.source, check=True)
        path.chmod(0o755)
        alias = self.source / "shared-entry"
        os.link(str(path), str(alias))
        with self.assertRaisesRegex(ValueError, "Unsafe installation file"):
            installer.install(self.app, self.source, check=True)
        alias.unlink()
        with self.assertRaisesRegex(ValueError, "separate directories"):
            installer.install(self.app, self.app, check=True)

    def test_lock_rejects_symlinks_hardlinks_and_simultaneous_install(self):
        path = self.app / ".mon-sensors-install.lock"
        foreign = self.root / "protected"
        foreign.write_bytes(b"protected\n")
        path.symlink_to(foreign)
        for check in (False, True):
            with self.assertRaises((OSError, ValueError)):
                installer.install(self.app, self.source, check=check)
        self.assertEqual(b"protected\n", foreign.read_bytes())
        path.unlink()
        os.link(str(foreign), str(path))
        with self.assertRaisesRegex(ValueError, "Unsafe installation file"):
            installer.install(self.app, self.source)
        path.unlink()
        fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with self.assertRaises(BlockingIOError):
                installer.install(self.app, self.source)
        finally:
            os.close(fd)
        self.assertFalse((self.app / installer.HELPER).exists())

    def test_check_existing_install_leaves_backups_and_selection_untouched(self):
        installer.install(self.app, self.source, "sckocp")
        before = self.snapshot(self.app)
        result = installer.install(self.app, self.source, check=True)
        self.assertEqual("already_installed", result["action"])
        self.assertEqual("sckocp", result["backend"])
        self.assertEqual(before, self.snapshot(self.app))

    def test_v4_bridge_and_python_entry_upgrade_to_fixed_launcher(self):
        installer.install(self.app, self.source, "sckocp")
        mon = self.app / "mon-sensors"
        mon.write_bytes(mon.read_bytes().replace(installer.DISPATCH.encode(), installer.V4_DISPATCH.encode()))
        oct_path = self.app / "oct"
        oct_path.write_bytes(oct_path.read_bytes().replace(
            b'"${APPPATH}/mon-sensors-plugin" --stop-app',
            b'python3 "${APPPATH}/mon-sensors-plugin" --stop-app'))
        old_source = (self.source / installer.ENTRY).read_bytes()
        (self.app / installer.ENTRY).write_bytes(old_source)
        marker = self.app / installer.HELPER / installer.MARKER
        record = json.loads(marker.read_text())
        record.pop("launcher_sha256")
        record["files"][installer.ENTRY] = hashlib.sha256(old_source).hexdigest()
        marker.write_text(json.dumps(record))
        self.assertEqual("installed", installer.install(self.app, self.source)["status"])
        self.assertTrue((self.app / installer.ENTRY).read_bytes().startswith(b"#!/bin/sh\n"))
        self.assertIn(installer.DISPATCH, mon.read_text())
        self.assertNotIn('python3 "${APPPATH}/mon-sensors-plugin"', oct_path.read_text())
        self.assertEqual("already_installed", installer.install(self.app, self.source)["status"])


if __name__ == "__main__":
    unittest.main()
