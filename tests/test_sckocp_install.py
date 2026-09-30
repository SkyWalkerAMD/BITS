"""Cloud-only Linux coverage for the offline API installer."""
import contextlib
import io
import json
import importlib.util
import marshal
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from sckocp_api import install as installer


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux installation semantics")
class APIInstallTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="sckocp-install-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = self.root / "source package"
        self.package = self.source / "sckocp_api"
        self.package.mkdir(parents=True)
        original = Path(installer.__file__).parent
        for filename in installer.FILES + ('bootstrap.py',):
            target = self.package / filename
            target.write_bytes((original / filename).read_bytes())
            target.chmod(0o644)
        self.prefix = self.root / "installation path" / "api runtime"
        self.bin_dir = self.root / "command path" / "bin"

    def install(self, **kwargs):
        return installer.install(str(self.source), str(self.prefix), str(self.bin_dir), **kwargs)

    def change_version(self):
        target = self.package / "__init__.py"
        target.write_text(re.sub(r'__version__\s*=\s*[\'"][^\'"]+[\'"]',
                                 '__version__ = "99.1.2"', target.read_text()), encoding="utf-8")

    def tree(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_check_is_read_only_and_reports_missing_target_plan(self):
        before = self.tree()
        with mock.patch.object(installer.os, "mkdir", side_effect=AssertionError("unexpected write")):
            result = self.install(check=True)
        self.assertEqual("install", result["action"])
        self.assertTrue(result["check"])
        self.assertEqual(before, self.tree())
        self.assertFalse(self.prefix.parent.exists())
        self.assertFalse(self.bin_dir.parent.exists())

    def test_install_runs_from_any_cwd_and_ignores_pythonpath(self):
        result = self.install()
        self.assertEqual("install", result["action"])
        self.assertEqual(set(installer.FILES), {p.name for p in (self.prefix / "sckocp_api").iterdir()})
        poison = self.root / "poison"
        (poison / "sckocp_api").mkdir(parents=True)
        (poison / "sckocp_api" / "__init__.py").write_text("raise RuntimeError('UNTRUSTED IMPORT')")
        env = dict(os.environ, PYTHONPATH=str(poison), PYTHONSTARTUP=str(poison / "startup.py"))
        output = subprocess.check_output([str(self.bin_dir / "sckocp-api"), "--help"],
                                         cwd=str(poison), env=env, stderr=subprocess.STDOUT,
                                         timeout=15).decode("utf-8")
        self.assertIn("usage:", output)
        self.assertNotIn("UNTRUSTED IMPORT", output)
        self.assertFalse((self.prefix / "sckocp_api" / "__pycache__").exists())
        self.assertEqual(0o755, stat.S_IMODE((self.bin_dir / "sckocp-api").stat().st_mode))
        for filename in installer.FILES:
            self.assertEqual(0o644, stat.S_IMODE((self.prefix / "sckocp_api" / filename).stat().st_mode))

    def test_identical_reinstallation_is_idempotent(self):
        self.install()
        command = self.bin_dir / "sckocp-api"
        before = self.tree()
        inode = command.stat().st_ino
        result = self.install()
        self.assertEqual("unchanged", result["action"])
        self.assertEqual(inode, command.stat().st_ino)
        self.assertEqual(before, self.tree())
        self.assertNotIn("backup", result)

    def invoke(self):
        return subprocess.run([str(self.bin_dir / 'sckocp-api'), '--help'],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)

    def assert_integrity_failure(self):
        result = self.invoke()
        self.assertEqual(1, result.returncode, result.stderr)
        self.assertEqual(b'', result.stderr)
        envelope = json.loads(result.stdout.decode('utf-8'))
        self.assertEqual('integrity_error', envelope['status'])
        self.assertIsNone(envelope['data'])
        self.assertNotIn(str(self.root), result.stdout.decode('utf-8'))

    def test_changed_module_is_refused_before_any_package_code_runs(self):
        self.install()
        marker = self.root / 'executed'
        target = self.prefix / 'sckocp_api/__init__.py'
        target.write_text('open(' + repr(str(marker)) + ', "w").close()\n')
        self.assert_integrity_failure()
        self.assertFalse(marker.exists())

    def test_editing_local_manifest_does_not_authorize_changed_code(self):
        self.install()
        target = self.prefix / 'sckocp_api/cli.py'
        target.write_text('raise SystemExit("UNTRUSTED MODULE")\n')
        manifest = self.prefix / installer.MANIFEST
        data = json.loads(manifest.read_text())
        data['files']['cli.py'] = installer._digest(target.read_bytes())
        manifest.write_text(json.dumps(data))
        self.assert_integrity_failure()

    def test_unchanged_but_writable_module_or_parent_is_refused(self):
        self.install()
        for target in (self.prefix, self.prefix / 'sckocp_api', self.prefix / 'sckocp_api/provider.py'):
            mode = stat.S_IMODE(target.stat().st_mode)
            try:
                target.chmod(mode | 0o020)
                self.assert_integrity_failure()
            finally:
                target.chmod(mode)

    def test_symlink_hardlink_fifo_and_missing_module_are_refused(self):
        self.install()
        target = self.prefix / 'sckocp_api/cli.py'
        content = target.read_bytes()
        saved = self.root / 'original-cli.py'
        saved.write_bytes(content)
        for kind in ('symlink', 'hardlink', 'fifo', 'missing'):
            target.unlink()
            try:
                if kind == 'symlink':
                    target.symlink_to(saved)
                elif kind == 'hardlink':
                    os.link(str(saved), str(target))
                elif kind == 'fifo':
                    os.mkfifo(str(target), 0o600)
                self.assert_integrity_failure()
            finally:
                if os.path.lexists(str(target)):
                    target.unlink()
                target.write_bytes(content)
                target.chmod(0o644)

    def test_cli_ignores_valid_timestamp_poisoned_pyc(self):
        self.install()
        target = self.prefix / 'sckocp_api/__init__.py'
        marker = self.root / 'cached-code-executed'
        code = compile('open(' + repr(str(marker)) + ', "w").close()\nraise SystemExit(0)',
                       str(target), 'exec')
        info = target.stat()
        header = importlib.util.MAGIC_NUMBER
        if sys.version_info >= (3, 7):
            header += struct.pack('<I', 0)
        header += struct.pack('<II', int(info.st_mtime) & 0xffffffff, info.st_size & 0xffffffff)
        cache = Path(importlib.util.cache_from_source(str(target)))
        cache.parent.mkdir(mode=0o755)
        cache.write_bytes(header + marshal.dumps(code))
        # Prove this is a runnable attack fixture under ordinary -B imports.
        probe = subprocess.run([sys.executable, '-I', '-S', '-B', '-c',
            'import sys;sys.path.insert(0,' + repr(str(self.prefix)) + ');import sckocp_api'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=15)
        self.assertEqual(0, probe.returncode, probe.stderr)
        self.assertTrue(marker.exists())
        marker.unlink()
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(b'usage:', result.stdout)
        self.assertFalse(marker.exists(), 'Managed CLI executed cached code')

    def test_verified_loader_executes_captured_bytes_after_path_replacement(self):
        from sckocp_api import bootstrap
        source = b'answer = 42\n'
        fake = type(sys)('sckocp_api.fake')
        filename = self.root / 'fake.py'
        filename.write_bytes(source)
        loader = bootstrap._VerifiedModules({'sckocp_api.fake': (source, str(filename))})
        filename.write_text('raise RuntimeError("replacement ran")\n')
        loader.exec_module(fake)
        self.assertEqual(42, fake.answer)

    def test_managed_cli_does_not_load_site_startup_hooks(self):
        import venv
        environment = self.root / 'interpreter'
        venv.EnvBuilder(with_pip=False, symlinks=False).create(str(environment))
        interpreter = environment / 'bin/python3'
        marker = self.root / 'site-executed'
        # Query the child interpreter's actual site layout; distributions can
        # use lib64 or dist-packages rather than the upstream default directory.
        layout = json.loads(subprocess.check_output([str(interpreter), '-I', '-B', '-c',
            'import json,site,sys;print(json.dumps({"prefix":sys.prefix,"sites":site.getsitepackages()}))'],
            timeout=15).decode())
        self.assertEqual(str(environment), layout['prefix'], layout)
        sites = [Path(path) for path in layout['sites']
                 if os.path.commonpath((str(environment), path)) == str(environment)]
        self.assertTrue(sites, layout)
        for site in sites:
            site.mkdir(parents=True, exist_ok=True)
            # A distro's stdlib sitecustomize.py can precede the venv on
            # sys.path. A unique .pth hook demonstrates site execution without
            # shadowing or changing that pre-existing system module.
            (site / 'api-startup-fixture.pth').write_text(
                'import builtins; builtins.open(' + repr(str(marker)) + ', "w").close()\n')
        subprocess.check_call([str(interpreter), '-I', '-B', '-c', 'pass'], timeout=15)
        self.assertTrue(marker.exists(), 'Fixture must demonstrate ordinary -I still loads site hooks')
        marker.unlink()
        with mock.patch.object(installer.sys, 'executable', str(interpreter)):
            self.install()
        result = self.invoke()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse(marker.exists())

    def test_upgrade_retains_verified_previous_runtime_and_launcher(self):
        self.install()
        old_init = (self.prefix / "sckocp_api" / "__init__.py").read_bytes()
        old_command = (self.bin_dir / "sckocp-api").read_bytes()
        self.change_version()
        self.assertEqual("upgrade", self.install(check=True)["action"])
        result = self.install()
        self.assertEqual("upgrade", result["action"])
        self.assertEqual("99.1.2", result["version"])
        self.assertEqual(old_init, (Path(result["backup"]) / "sckocp_api" / "__init__.py").read_bytes())
        self.assertEqual(old_command, Path(result["command_backup"]).read_bytes())
        self.assertIn(b'99.1.2', (self.prefix / "sckocp_api" / "__init__.py").read_bytes())
        self.assertEqual("unchanged", self.install()["action"])

    def test_normal_sdk_import_cache_allows_check_reinstall_and_upgrade(self):
        self.install()
        subprocess.check_call([sys.executable, "-I", "-c",
                               "import sys;sys.path.insert(0," + repr(str(self.prefix)) +
                               ");import sckocp_api"], cwd="/", timeout=15)
        cache = self.prefix / "sckocp_api" / "__pycache__"
        self.assertTrue(list(cache.glob("*.pyc")))
        before = self.tree()
        self.assertEqual("unchanged", self.install(check=True)["action"])
        self.assertEqual("unchanged", self.install()["action"])
        self.assertEqual(before, self.tree())
        self.change_version()
        result = self.install()
        self.assertEqual("upgrade", result["action"])
        self.assertTrue((Path(result["backup"]) / "sckocp_api" / "__pycache__").is_dir())

    def test_unrecognized_or_unsafe_cache_is_preserved_and_refused(self):
        self.install()
        cache = self.prefix / "sckocp_api" / "__pycache__"
        cache.mkdir(mode=0o755)
        unexpected = cache / "customer.txt"
        unexpected.write_text("keep me")
        before = self.tree()
        with self.assertRaises(installer.InstallError):
            self.install(check=True)
        self.assertEqual(before, self.tree())
        unexpected.unlink()
        candidate = cache / "provider.cpython-311.pyc"
        candidate.write_bytes(b"cache")
        candidate.chmod(0o666)
        with self.assertRaises(installer.InstallError):
            self.install(check=True)
        candidate.chmod(0o644)
        candidate.unlink()
        unrelated = self.root / "keep.txt"
        unrelated.write_bytes(b"unrelated")
        candidate.symlink_to(unrelated)
        with self.assertRaises(OSError):
            self.install(check=True)
        self.assertEqual(b"unrelated", unrelated.read_bytes())

    def test_cache_directory_symlink_is_preserved_and_refused(self):
        self.install()
        cache = self.prefix / "sckocp_api" / "__pycache__"
        unrelated = self.root / "other cache"
        unrelated.mkdir()
        cache.symlink_to(unrelated, target_is_directory=True)
        with self.assertRaises(OSError):
            self.install(check=True)
        self.assertTrue(cache.is_symlink())
        self.assertEqual([], list(unrelated.iterdir()))

    def test_unmanaged_command_is_preserved(self):
        self.bin_dir.mkdir(parents=True)
        command = self.bin_dir / "sckocp-api"
        command.write_text("customer command\n")
        before = self.tree()
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(before, self.tree())
        self.assertFalse(self.prefix.parent.exists())

    def test_unmanaged_directory_even_empty_is_preserved(self):
        self.prefix.mkdir(parents=True)
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertTrue(self.prefix.is_dir())
        self.assertEqual([], list(self.prefix.iterdir()))

    def test_modified_managed_code_is_never_overwritten(self):
        self.install()
        code = self.prefix / "sckocp_api" / "provider.py"
        code.write_bytes(code.read_bytes() + b"\n# customer adjustment\n")
        self.change_version()
        before = self.tree()
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(before, self.tree())

    def test_modified_launcher_is_never_overwritten(self):
        self.install()
        command = self.bin_dir / "sckocp-api"
        command.write_bytes(command.read_bytes() + b"# customer adjustment\n")
        before = self.tree()
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(before, self.tree())

    def test_untracked_runtime_files_are_preserved(self):
        self.install()
        extra = self.prefix / "customer.txt"
        extra.write_text("keep me")
        self.change_version()
        before = self.tree()
        with self.assertRaises(installer.InstallError):
            self.install()
        self.assertEqual(before, self.tree())

    def test_missing_managed_launcher_can_be_repaired(self):
        self.install()
        (self.bin_dir / "sckocp-api").unlink()
        result = self.install()
        self.assertEqual("upgrade", result["action"])
        self.assertTrue((self.bin_dir / "sckocp-api").is_file())
        self.assertIn("backup", result)
        self.assertNotIn("command_backup", result)

    def test_target_symlink_is_rejected(self):
        self.prefix.parent.mkdir(parents=True)
        destination = self.root / "unrelated"
        destination.mkdir()
        self.prefix.symlink_to(destination, target_is_directory=True)
        with self.assertRaises((installer.InstallError, OSError)):
            self.install()
        self.assertTrue(self.prefix.is_symlink())
        self.assertEqual([], list(destination.iterdir()))

    def test_parent_symlink_and_unsafe_writable_parent_are_rejected(self):
        self.prefix.parent.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises((installer.InstallError, OSError)) as caught:
            self.install(check=True)
        self.assertIn(str(self.prefix.parent), str(caught.exception))
        self.prefix.parent.unlink()
        self.prefix.parent.mkdir(mode=0o777)
        self.prefix.parent.chmod(0o777)
        with self.assertRaises(installer.InstallError) as caught:
            self.install(check=True)
        self.assertIn(str(self.prefix.parent), str(caught.exception))
        self.prefix.parent.chmod(0o700)

    def test_source_symlink_hardlink_and_writable_file_are_rejected(self):
        target = self.package / "provider.py"
        original = self.root / "original.py"
        target.rename(original)
        target.symlink_to(original)
        with self.assertRaises((installer.InstallError, OSError)):
            self.install(check=True)
        target.unlink()
        os.link(str(original), str(target))
        with self.assertRaises(installer.InstallError) as caught:
            self.install(check=True)
        self.assertIn("provider.py", str(caught.exception))
        target.unlink()
        original.rename(target)
        target.chmod(0o666)
        with self.assertRaises(installer.InstallError) as caught:
            self.install(check=True)
        self.assertIn("provider.py", str(caught.exception))

    def test_untrusted_owner_is_rejected_without_requiring_root(self):
        info = mock.Mock(st_uid=987654, st_mode=stat.S_IFREG | 0o644, st_nlink=1)
        with self.assertRaises(installer.InstallError):
            installer._trusted(info)

    def test_failure_publishing_upgrade_restores_both_previous_artifacts(self):
        self.install()
        self.change_version()
        before = self.tree()
        rename = installer.os.rename

        def fail_command_publish(source, destination, *args, **kwargs):
            if source.startswith(".sckocp-api.stage-") and destination == "sckocp-api":
                # A concurrent SDK import may create an ordinary cache after
                # the new runtime is published and before the launcher fails.
                cache = self.prefix / "sckocp_api" / "__pycache__"
                cache.mkdir(mode=0o755)
                (cache / "__init__.cpython-311.pyc").write_bytes(b"private cache")
                raise OSError("injected command publication failure")
            return rename(source, destination, *args, **kwargs)

        with mock.patch.object(installer.os, "rename", side_effect=fail_command_publish):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(before, self.tree())
        self.assertFalse(list(self.prefix.parent.glob("*.backup-*")))

    def test_failure_of_initial_install_removes_new_paths(self):
        before = self.tree()
        rename = installer.os.rename

        def fail_command_publish(source, destination, *args, **kwargs):
            if destination == "sckocp-api":
                raise OSError("injected publication failure")
            return rename(source, destination, *args, **kwargs)

        with mock.patch.object(installer.os, "rename", side_effect=fail_command_publish):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(before, self.tree())
        self.assertFalse(self.prefix.parent.exists())
        self.assertFalse(self.bin_dir.parent.exists())

    def test_staging_write_failure_preserves_existing_installation(self):
        self.install()
        self.change_version()
        before = self.tree()
        write = installer._write

        def fail_provider(parent, name, *args):
            if name == "provider.py":
                raise OSError("injected storage failure")
            return write(parent, name, *args)

        with mock.patch.object(installer, "_write", side_effect=fail_provider):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(before, self.tree())
        self.assertFalse(list(self.prefix.parent.glob(".*.stage-*")))

    def test_disk_sync_failure_cleans_partial_file_before_rollback(self):
        before = self.tree()
        with mock.patch.object(installer.os, "fsync", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.install()
        self.assertEqual(before, self.tree())
        self.assertFalse(self.prefix.parent.exists())

    def test_check_reports_permissions_without_creating_directories(self):
        with mock.patch.object(installer.os, "access", return_value=False):
            with self.assertRaises(installer.InstallError) as caught:
                self.install(check=True)
        self.assertIn(str(self.prefix.parent), str(caught.exception))
        self.assertFalse(self.prefix.parent.exists())

    def test_invalid_paths_and_overlapping_targets_are_rejected(self):
        for prefix, bin_dir in (("/", str(self.bin_dir)),
                                (str(self.prefix), str(self.prefix / "bin")),
                                (str(self.prefix) + "\n", str(self.bin_dir))):
            with self.subTest(prefix=prefix):
                with self.assertRaises(installer.InstallError):
                    installer.install(str(self.source), prefix, bin_dir, check=True)

    def test_cli_prints_json_plan_and_nonzero_on_conflict(self):
        args = ["--source", str(self.source), "--prefix", str(self.prefix),
                "--bin-dir", str(self.bin_dir), "--check"]
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, installer.main(args))
        self.assertTrue(json.loads(output.getvalue())["check"])
        self.prefix.mkdir(parents=True)
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(1, installer.main(args))
