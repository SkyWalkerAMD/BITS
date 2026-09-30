"""Known-release and refusal checks; Linux root behavior is a separate CI job."""
import hashlib
import os
from pathlib import Path
import unittest
from unittest import mock

from mon_sensors_plugin import adoption


BASELINE = Path(__file__).resolve().parents[1] / "integrations/mon-sensors/upstream"


class PluginAdoptionManifestTests(unittest.TestCase):
    def test_0730_manifest_identifies_existing_reviewed_fixture(self):
        hashes = {}
        for relative in adoption.SCRIPTS:
            fixture = BASELINE / relative.split("/")[-1]
            hashes[relative] = hashlib.sha256(fixture.read_bytes()).hexdigest()
        self.assertEqual("0730", adoption._release_for_hashes(hashes))
        self.assertEqual(adoption.KNOWN_RELEASES["0730"], hashes)

    def test_complete_024_manifest_is_recognized(self):
        hashes = {}
        for relative in adoption.SCRIPTS:
            fixture = BASELINE.parent / "upstream-0.9.24a" / relative.split("/")[-1]
            hashes[relative] = hashlib.sha256(fixture.read_bytes()).hexdigest()
        self.assertEqual("0.9.24a", adoption._release_for_hashes(hashes))
        self.assertEqual(adoption.KNOWN_RELEASES["0.9.24a"], hashes)

    def test_mixed_modified_partial_and_extra_manifests_are_refused(self):
        cases = []
        mixed = dict(adoption.KNOWN_RELEASES["0730"])
        mixed["oct"] = adoption.KNOWN_RELEASES["0.9.24a"]["oct"]
        cases.append(mixed)
        modified = dict(adoption.KNOWN_RELEASES["0730"])
        modified["ocb"] = "0" * 64
        cases.append(modified)
        partial = dict(adoption.KNOWN_RELEASES["0730"])
        del partial["mon-sensors"]
        cases.append(partial)
        extra = dict(adoption.KNOWN_RELEASES["0730"])
        extra["arbitrary.sh"] = "0" * 64
        cases.append(extra)
        for hashes in cases:
            with self.assertRaisesRegex(ValueError, "complete known release"):
                adoption._release_for_hashes(hashes)

    def test_nonroot_is_refused_before_opening_any_path(self):
        with mock.patch.object(os, "geteuid", return_value=1234), \
                mock.patch.object(os, "open") as opened:
            with self.assertRaisesRegex(PermissionError, "requires root"):
                adoption.OriginalAdoption.prepare("/root/ocrun")
        opened.assert_not_called()

    def test_unsafe_path_is_refused_before_opening_any_path(self):
        for path in ("relative/ocrun", "/", "/root/../ocrun"):
            with mock.patch.object(os, "geteuid", return_value=0), \
                    mock.patch.object(os, "open") as opened:
                with self.assertRaisesRegex(ValueError, "absolute application path"):
                    adoption.OriginalAdoption.prepare(path)
            opened.assert_not_called()


if __name__ == "__main__":
    unittest.main()
