#!/usr/bin/env python3
"""Exercise actual UID 201 adoption on a disposable GitHub Actions Linux VM."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import sys
import tempfile
import unittest
from unittest import mock


if (sys.platform != "linux" or os.geteuid() != 0 or
        os.environ.get("GITHUB_ACTIONS") != "true"):
    raise SystemExit("This verification requires root on a disposable GitHub Actions Linux VM")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from mon_sensors_plugin import adoption
from mon_sensors_plugin import install as installer

BASELINE = ROOT / "integrations/mon-sensors/upstream"


class OriginalAdoptionRootTests(unittest.TestCase):
    RELEASE = "0730"
    BASELINE = BASELINE

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ocrun-adoption-root-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.app = self.root / "ocrun"
        self.app.mkdir(mode=0o755)
        (self.app / "py").mkdir(mode=0o755)
        for relative in adoption.SCRIPTS:
            destination = self.app / relative
            shutil.copyfile(str(self.BASELINE / relative.split("/")[-1]), str(destination))
            destination.chmod(0o755)
            os.chown(str(destination), 201, 200)
        self.unrelated = self.app / "py" / "unrelated-data"
        self.unrelated.write_bytes(b"must not be adopted or executed\n")
        os.chown(str(self.unrelated), 201, 200)
        os.chown(str(self.app / "py"), 201, 200)

    def snapshot(self):
        result = {}
        for path in [self.app] + list(self.app.rglob("*")):
            details = path.lstat()
            if stat.S_ISREG(details.st_mode):
                content = path.read_bytes()
            elif stat.S_ISLNK(details.st_mode):
                content = os.readlink(str(path))
            else:
                content = None
            result[str(path.relative_to(self.app))] = (
                details.st_uid, details.st_gid, stat.S_IMODE(details.st_mode), content)
        return result

    def source(self):
        source = self.root / "package"
        source.mkdir(mode=0o755)
        for relative in installer.FILES:
            path = source / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes((ROOT / relative).read_bytes())
            path.chmod(0o755 if relative == installer.ENTRY else 0o644)
        return source

    def assert_refused(self):
        before = self.snapshot()
        with self.assertRaises((ValueError, OSError)):
            with adoption.OriginalAdoption.prepare(self.app):
                self.fail("Unsafe or unknown original was accepted")
        self.assertEqual(before, self.snapshot())

    def test_prepare_is_read_only_and_reports_five_ownership_changes(self):
        before = self.snapshot()
        with adoption.OriginalAdoption.prepare(self.app) as prepared:
            self.assertEqual(self.RELEASE, prepared.release)
            self.assertEqual(4, prepared.plan["files_verified"])
            self.assertEqual(set(adoption.SCRIPTS) | {"py"},
                             {item["path"] for item in prepared.plan["ownership_changes"]})
            for relative in adoption.SCRIPTS:
                self.assertEqual((self.app / relative).read_bytes(), prepared.originals[relative])
                self.assertEqual({"uid": 201, "gid": 200, "mode": 0o755},
                                 prepared.metadata[relative])
        self.assertEqual(before, self.snapshot())

    def test_apply_changes_only_py_directory_and_rollback_restores_its_owner(self):
        before = self.snapshot()
        with adoption.OriginalAdoption.prepare(self.app) as prepared:
            prepared.apply()
            self.assertEqual((0, 0, 0o755), self.snapshot()["py"][:3])
            for relative in adoption.SCRIPTS:
                self.assertEqual(before[relative], self.snapshot()[relative])
            self.assertEqual(before["py/unrelated-data"], self.snapshot()["py/unrelated-data"])
            prepared.verify()
            prepared.rollback()
            prepared.rollback()  # A second rollback is harmless.
        self.assertEqual(before, self.snapshot())

    def test_context_close_preserves_successful_adoption_and_closes_fds(self):
        count = len(list(Path("/proc/self/fd").iterdir()))
        with adoption.OriginalAdoption.prepare(self.app) as prepared:
            prepared.apply()
            with self.assertRaisesRegex(ValueError, "already applied"):
                prepared.apply()
        self.assertEqual((0, 0), self.snapshot()["py"][:2])
        self.assertEqual(count, len(list(Path("/proc/self/fd").iterdir())))
        with self.assertRaisesRegex(ValueError, "closed"):
            prepared.verify()

    def test_modified_source_is_refused_without_partial_adoption(self):
        with (self.app / "ocb").open("ab") as stream:
            stream.write(b"\n# altered\n")
        self.assert_refused()

    def test_wrong_uid_and_wrong_gid_are_refused(self):
        os.chown(str(self.app / "oct"), 202, 200)
        self.assert_refused()
        os.chown(str(self.app / "oct"), 201, 201)
        self.assert_refused()

    def test_group_write_and_setid_file_modes_are_refused(self):
        for mode in (0o775, 0o777, 0o4755, 0o2755):
            (self.app / "mon-sensors").chmod(mode)
            self.assert_refused()

    def test_single_hardlink_policy_is_enforced(self):
        os.link(str(self.app / "mon-sensors"), str(self.root / "other-link"))
        self.assert_refused()

    def test_file_symlink_is_refused_even_when_target_is_known(self):
        target = self.app / "mon-sensors"
        target.rename(self.root / "original-monitor")
        target.symlink_to(self.root / "original-monitor")
        self.assert_refused()

    def test_py_directory_symlink_is_refused(self):
        (self.app / "py").rename(self.root / "other-py")
        (self.app / "py").symlink_to(self.root / "other-py", target_is_directory=True)
        self.assert_refused()

    def test_foreign_or_sticky_writable_app_is_refused(self):
        os.chown(str(self.app), 201, 200)
        self.assert_refused()
        os.chown(str(self.app), 0, 0)
        self.app.chmod(0o1777)
        self.assert_refused()

    def test_same_bytes_replacement_after_prepare_is_refused(self):
        with adoption.OriginalAdoption.prepare(self.app) as prepared:
            path = self.app / "ocb"
            replacement = self.app / "replacement"
            replacement.write_bytes(prepared.originals["ocb"])
            replacement.chmod(0o755)
            os.chown(str(replacement), 201, 200)
            replacement.replace(path)
            before = self.snapshot()
            with self.assertRaisesRegex(ValueError, "file changed"):
                prepared.apply()
            self.assertEqual(before, self.snapshot())

    def test_py_replacement_after_prepare_is_refused(self):
        with adoption.OriginalAdoption.prepare(self.app) as prepared:
            (self.app / "py").rename(self.root / "pinned-py")
            (self.app / "py").mkdir(mode=0o755)
            os.chown(str(self.app / "py"), 201, 200)
            with self.assertRaisesRegex(ValueError, "py directory changed"):
                prepared.apply()
            self.assertEqual(201, (self.root / "pinned-py").stat().st_uid)
            self.assertEqual(201, (self.app / "py").stat().st_uid)

    def test_change_during_directory_takeover_rolls_back_py_owner(self):
        native_fchown = os.fchown
        changed = [False]

        def racing_fchown(fd, uid, gid):
            native_fchown(fd, uid, gid)
            if not changed[0] and uid == 0:
                changed[0] = True
                with (self.app / "py/mon-analyse-log.py").open("ab") as stream:
                    stream.write(b"\n# changed by old file owner\n")

        with adoption.OriginalAdoption.prepare(self.app) as prepared:
            with mock.patch.object(os, "fchown", side_effect=racing_fchown):
                with self.assertRaisesRegex(ValueError, "file changed"):
                    prepared.apply()
            self.assertEqual((201, 200), self.snapshot()["py"][:2])

    def test_whole_release_match_rejects_mixed_identified_files(self):
        # Use synthetic second-release bytes here; production accepted hashes are
        # separately pinned by normal tests and never expanded by this fixture.
        first = dict(adoption.KNOWN_RELEASES[self.RELEASE])
        second = dict(first)
        new_oct = (self.app / "oct").read_bytes() + b"\n# second release oct\n"
        new_ocb = (self.app / "ocb").read_bytes() + b"\n# second release ocb\n"
        second["oct"] = hashlib.sha256(new_oct).hexdigest()
        second["ocb"] = hashlib.sha256(new_ocb).hexdigest()
        (self.app / "oct").write_bytes(new_oct)
        with mock.patch.object(adoption, "KNOWN_RELEASES", {"first": first, "second": second}):
            self.assert_refused()

    def test_installer_check_is_read_only_and_default_mode_refuses_foreign_owner(self):
        source = self.source()
        before = self.snapshot()
        for check in (True, False):
            with self.assertRaisesRegex(ValueError, "Unsafe installation file"):
                installer.install(self.app, source, check=check)
            self.assertEqual(before, self.snapshot())
        result = installer.install(self.app, source, backend="sckocp", check=True,
                                   adopt_original=True)
        self.assertEqual("checked", result["status"])
        self.assertTrue(result["read_only"])
        self.assertEqual(self.RELEASE, result["original_adoption"]["release"])
        self.assertEqual(before, self.snapshot())

    def test_installer_adoption_replaces_only_managed_targets_and_is_idempotent(self):
        source = self.source()
        before = self.snapshot()
        result = installer.install(self.app, source, backend="sckocp", adopt_original=True)
        self.assertEqual("installed", result["status"])
        after = self.snapshot()
        for relative in adoption.SCRIPTS + ("py",):
            self.assertEqual((0, 0, 0o755), after[relative][:3])
        self.assertEqual(before["py/unrelated-data"], after["py/unrelated-data"])
        backup = Path(result["backup"])
        record = json.loads((backup / "original-metadata.json").read_text())
        self.assertEqual(self.RELEASE, record["adoption"]["release"])
        for relative in adoption.SCRIPTS:
            self.assertEqual(before[relative][3], (backup / relative).read_bytes())
            self.assertEqual({"mode": 0o755, "uid": 201, "gid": 200}, record["files"][relative])
            self.assertEqual(0o600, stat.S_IMODE((backup / relative).stat().st_mode))
        repeated = installer.install(self.app, source, backend="sckocp", adopt_original=True)
        self.assertEqual("already_installed", repeated["status"])
        self.assertEqual(after, self.snapshot())

    def test_installer_rollback_restores_original_owners_data_and_py_then_retry_succeeds(self):
        source = self.source()
        before = self.snapshot()
        native_atomic = installer._atomic
        failed = [False]

        def failing_atomic(path, data, mode):
            if Path(path) == self.app / "oct" and not failed[0]:
                failed[0] = True
                raise OSError("injected atomic oct installation failure")
            return native_atomic(path, data, mode)

        with mock.patch.object(installer, "_atomic", side_effect=failing_atomic):
            with self.assertRaisesRegex(OSError, "injected atomic oct"):
                installer.install(self.app, source, backend="sckocp", adopt_original=True)
        after = self.snapshot()
        # An audit backup and installation lock may remain after failed mutation.
        # Every original object must retain its content, mode and original owner.
        for relative, details in before.items():
            self.assertEqual(details, after[relative], relative)
        self.assertFalse((self.app / installer.ENTRY).exists())
        self.assertFalse((self.app / installer.HELPER).exists())
        self.assertFalse((self.app / installer.BACKEND_FILE).exists())
        retry = installer.install(self.app, source, backend="sckocp", adopt_original=True)
        self.assertEqual("installed", retry["status"])

    def test_installer_unknown_hash_is_refused_for_check_and_install(self):
        source = self.source()
        with (self.app / "ocb").open("ab") as stream:
            stream.write(b"\n# unknown local change\n")
        before = self.snapshot()
        for check in (True, False):
            with self.assertRaisesRegex(ValueError, "complete known release"):
                installer.install(self.app, source, check=check, adopt_original=True)
            self.assertEqual(before, self.snapshot())


class OriginalAdoption024RootTests(OriginalAdoptionRootTests):
    RELEASE = "0.9.24a"
    BASELINE = BASELINE.parent / "upstream-0.9.24a"


if __name__ == "__main__":
    unittest.main(verbosity=2)
