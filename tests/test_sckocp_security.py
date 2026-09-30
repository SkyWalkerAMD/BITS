"""Cloud Linux checks for the executable trust and pathname race boundary."""
import errno
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

from sckocp_api import interface, security


@unittest.skipUnless(sys.platform.startswith("linux"), "Linux descriptor semantics")
class TrustedExecutableTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.binary = self.program(self.root / "sckocp", "trusted")

    def program(self, path, message):
        path.write_text("#!/bin/sh\nprintf '%s\\n' '" + message + "'\n", encoding="utf-8")
        path.chmod(0o700)
        return path

    def execute(self, descriptor, executable):
        return subprocess.check_output(
            [str(self.binary)], executable=executable, pass_fds=(descriptor,),
            env={"PATH": "/usr/bin:/bin", "LC_ALL": "C"}, cwd="/", timeout=5)

    def assert_denied(self, path):
        with self.assertRaises(security.UnsafeExecutable) as caught:
            with security.trusted_executable(str(path)):
                self.fail("Untrusted executable was accepted")
        self.assertNotIn(str(path), str(caught.exception))

    def test_pinned_script_executes_and_descriptor_closes(self):
        with security.trusted_executable(str(self.binary)) as (descriptor, executable):
            self.assertEqual(b"trusted\n", self.execute(descriptor, executable))
            self.assertFalse(os.get_inheritable(descriptor))
        with self.assertRaises(OSError) as caught:
            os.fstat(descriptor)
        self.assertEqual(errno.EBADF, caught.exception.errno)

    def test_exception_in_caller_still_closes_descriptor(self):
        with self.assertRaises(RuntimeError):
            with security.trusted_executable(str(self.binary)) as (descriptor, _):
                raise RuntimeError("caller failed")
        with self.assertRaises(OSError):
            os.fstat(descriptor)

    def test_file_replacement_after_validation_cannot_redirect_execution(self):
        replacement = self.program(self.root / "replacement", "replacement")
        with security.trusted_executable(str(self.binary)) as (descriptor, executable):
            os.replace(str(replacement), str(self.binary))
            self.assertEqual(b"trusted\n", self.execute(descriptor, executable))

    def test_directory_replacement_after_validation_cannot_redirect_execution(self):
        directory = self.root / "bin"
        directory.mkdir(mode=0o700)
        binary = self.program(directory / "sckocp", "trusted")
        with security.trusted_executable(str(binary)) as (descriptor, executable):
            directory.rename(self.root / "old-bin")
            directory.mkdir(mode=0o700)
            self.program(directory / "sckocp", "replacement")
            self.assertEqual(b"trusted\n", self.execute(descriptor, executable))

    def test_directory_replacement_during_traversal_uses_open_directory(self):
        directory = self.root / "bin"
        directory.mkdir(mode=0o700)
        binary = self.program(directory / "sckocp", "trusted")
        original_open = os.open

        def replace_after_open(path, flags, *args, **kwargs):
            descriptor = original_open(path, flags, *args, **kwargs)
            if path == "bin":
                directory.rename(self.root / "old-bin")
                directory.mkdir(mode=0o700)
                self.program(directory / "sckocp", "replacement")
            return descriptor

        with mock.patch.object(security.os, "open", side_effect=replace_after_open):
            with security.trusted_executable(str(binary)) as (descriptor, executable):
                self.assertEqual(b"trusted\n", self.execute(descriptor, executable))

    def test_group_or_world_writable_binary_is_rejected(self):
        for mode in (0o720, 0o702, 0o777):
            with self.subTest(mode=oct(mode)):
                self.binary.chmod(mode)
                self.assert_denied(self.binary)

    def test_group_or_world_writable_parent_is_rejected(self):
        for mode in (0o770, 0o707, 0o777):
            with self.subTest(mode=oct(mode)):
                self.root.chmod(mode)
                try:
                    self.assert_denied(self.binary)
                finally:
                    self.root.chmod(0o700)

    def test_root_owned_sticky_temporary_parent_is_allowed(self):
        info = os.stat("/tmp")
        self.assertEqual(0, info.st_uid, "Cloud image /tmp must be owned by root")
        self.assertTrue(info.st_mode & stat.S_ISVTX, "Cloud image /tmp must be sticky")
        with tempfile.TemporaryDirectory(dir="/tmp") as directory:
            binary = self.program(Path(directory) / "sckocp", "trusted")
            with security.trusted_executable(str(binary)) as (descriptor, executable):
                self.assertEqual(b"trusted\n", self.execute(descriptor, executable))

    def test_untrusted_sticky_directory_owner_is_rejected(self):
        info = SimpleNamespace(st_uid=12345, st_mode=stat.S_IFDIR | 0o1777)
        with self.assertRaises(security.UnsafeExecutable):
            security._directory(info, 0)
        with self.assertRaises(security.UnsafeExecutable):
            security._directory(info, 12345)

    def test_root_rejects_nonroot_owner_for_all_components(self):
        for check, mode in ((security._directory, stat.S_IFDIR | 0o700),
                            (security._executable, stat.S_IFREG | 0o700),
                            (security._trusted_owner, stat.S_IFLNK | 0o777)):
            with self.subTest(mode=mode):
                with self.assertRaises(security.UnsafeExecutable):
                    check(SimpleNamespace(st_uid=1000, st_mode=mode), 0)

    def test_root_rejects_actual_foreign_owned_binary(self):
        if os.geteuid() == 0:
            os.chown(str(self.binary), 12345, -1)
            self.assert_denied(self.binary)
        else:
            # Nonroot cloud jobs cannot chown. Model the same kernel metadata
            # while still exercising real openat traversal and FD cleanup.
            binary_info = os.stat(str(self.binary))
            original_fstat = os.fstat

            def root_view(descriptor):
                info = original_fstat(descriptor)
                fields = list(info)
                fields[4] = (12345 if (info.st_dev, info.st_ino) ==
                             (binary_info.st_dev, binary_info.st_ino) else 0)
                return os.stat_result(fields)

            with mock.patch.object(security.os, "geteuid", return_value=0), mock.patch.object(
                    security.os, "fstat", side_effect=root_view):
                self.assert_denied(self.binary)

    def test_nonroot_accepts_itself_or_root_but_no_other_owner(self):
        for owner in (0, 1000):
            security._executable(
                SimpleNamespace(st_uid=owner, st_mode=stat.S_IFREG | 0o755), 1000)
        with self.assertRaises(security.UnsafeExecutable):
            security._executable(
                SimpleNamespace(st_uid=1001, st_mode=stat.S_IFREG | 0o755), 1000)

    def test_fifo_and_directory_are_rejected_without_read_open(self):
        fifo = self.root / "fifo"
        os.mkfifo(str(fifo), 0o700)
        original_open = os.open

        def path_only(path, flags, *args, **kwargs):
            self.assertTrue(flags & os.O_PATH, "Special files must never be opened for I/O")
            self.assertTrue(flags & os.O_NOFOLLOW)
            return original_open(path, flags, *args, **kwargs)

        with mock.patch.object(security.os, "open", side_effect=path_only):
            self.assert_denied(fifo)
            self.assert_denied(self.root)

    def test_no_execute_permission_is_an_os_error(self):
        self.binary.chmod(0o600)
        with self.assertRaises(PermissionError):
            with security.trusted_executable(str(self.binary)):
                self.fail("Non-executable accepted")

    def test_missing_binary_keeps_file_not_found_classification(self):
        with self.assertRaises(FileNotFoundError):
            with security.trusted_executable(str(self.root / "missing")):
                self.fail("Missing binary accepted")

    def test_setuid_and_setgid_are_rejected(self):
        for mode in (0o4700, 0o2700):
            with self.subTest(mode=oct(mode)):
                self.binary.chmod(mode)
                self.assert_denied(self.binary)

    def test_relative_and_absolute_trusted_symlinks_are_supported(self):
        for name, target in (("relative", "sckocp"), ("absolute", str(self.binary))):
            link = self.root / name
            link.symlink_to(target)
            with security.trusted_executable(str(link)) as (descriptor, executable):
                self.assertEqual(b"trusted\n", self.execute(descriptor, executable))

    def test_directory_symlink_and_relative_dotdot_target_are_supported(self):
        subdirectory = self.root / "sub"
        subdirectory.mkdir(mode=0o700)
        (subdirectory / "sckocp").symlink_to("../sckocp")
        (self.root / "alias").symlink_to("sub")
        with security.trusted_executable(str(self.root / "alias" / "sckocp")) as (
                descriptor, executable):
            self.assertEqual(b"trusted\n", self.execute(descriptor, executable))

    def test_mutable_symlink_containing_directory_is_rejected(self):
        unsafe = self.root / "mutable"
        unsafe.mkdir(mode=0o700)
        link = unsafe / "sckocp"
        link.symlink_to(self.binary)
        unsafe.chmod(0o777)
        self.assert_denied(link)

    def test_mutable_symlink_target_directory_is_rejected(self):
        unsafe = self.root / "mutable"
        unsafe.mkdir(mode=0o700)
        target = self.program(unsafe / "sckocp", "unsafe")
        link = self.root / "alias"
        link.symlink_to(target)
        unsafe.chmod(0o777)
        self.assert_denied(link)

    def test_dotdot_does_not_skip_validation_of_earlier_component(self):
        unsafe = self.root / "mutable"
        unsafe.mkdir(mode=0o777)
        unsafe.chmod(0o777)
        self.assert_denied(str(unsafe) + "/../sckocp")

    def test_symlink_replacement_during_resolution_uses_opened_link(self):
        link = self.root / "alias"
        link.symlink_to("sckocp")
        self.program(self.root / "replacement", "replacement")
        original_readlink = os.readlink

        def replace_then_read(path, *args, **kwargs):
            self.assertEqual("", path)
            link.unlink()
            link.symlink_to("replacement")
            return original_readlink(path, *args, **kwargs)

        with mock.patch.object(security.os, "readlink", side_effect=replace_then_read):
            with security.trusted_executable(str(link)) as (descriptor, executable):
                self.assertEqual(b"trusted\n", self.execute(descriptor, executable))

    def test_symlink_loop_is_bounded_and_closes_descriptors(self):
        link = self.root / "loop"
        link.symlink_to("loop")
        before = set(os.listdir("/proc/self/fd"))
        self.assert_denied(link)
        self.assertEqual(before, set(os.listdir("/proc/self/fd")))

    def test_rejected_binary_closes_parent_and_file_descriptors(self):
        self.binary.chmod(0o777)
        before = set(os.listdir("/proc/self/fd"))
        self.assert_denied(self.binary)
        self.assertEqual(before, set(os.listdir("/proc/self/fd")))

    def test_nonabsolute_and_nul_paths_are_rejected(self):
        for path in ("sckocp", "", "/tmp/secret\0path"):
            with self.subTest(path=path):
                # Empty strings occur in every error message, so use a direct
                # assertion here rather than the path-disclosure helper.
                with self.assertRaises(security.UnsafeExecutable):
                    with security.trusted_executable(path):
                        self.fail("Invalid configured path accepted")

    def test_public_collect_never_executes_writable_program(self):
        marker = self.root / "unsafe-program-executed"
        self.binary.write_text(
            "#!" + sys.executable + "\nfrom pathlib import Path\nPath(" +
            repr(str(marker)) + ").write_text('unexpected')\n", encoding="utf-8")
        self.binary.chmod(0o777)
        result = interface.collect(str(self.binary), interval=0.05, timeout=2)
        self.assertEqual("unsafe_executable", result["status"])
        self.assertIsNone(result["data"])
        self.assertFalse(marker.exists(), "Rejected provider was executed")
        self.assertNotIn(str(self.binary), json.dumps(result))

    def test_public_collect_executes_trusted_symlink_using_pinned_fd(self):
        sample = {"schema": "sckocp-mon-v1", "version": "1.2.0",
                  "vendor": "AuthenticAMD", "family": 25, "interval_s": 0.05,
                  "sockets": [{"id": 0, "pkg_w": None}], "cores": []}
        self.binary.write_text(
            "#!" + sys.executable + "\nimport sys\n" +
            "assert sys.argv[1:] == ['mon', '--json']\n" +
            "assert sys.argv[0].startswith('/proc/self/fd/')\n" +
            "print(" + repr(json.dumps(sample)) + ")\n", encoding="utf-8")
        self.binary.chmod(0o700)
        link = self.root / "public-link"
        link.symlink_to("sckocp")
        result = interface.collect(str(link), interval=0.05, timeout=5)
        self.assertEqual("ok", result["status"])
        self.assertEqual(sample, result["data"])


if __name__ == "__main__":
    unittest.main()
