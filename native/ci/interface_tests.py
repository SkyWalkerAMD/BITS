"""Source regression of the data-only boundary, in disposable Linux only."""
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import importlib.util

assert os.environ.get("GITHUB_ACTIONS") == "true" and sys.platform == "linux"
root = Path("/src")
sys.path[:0] = [str(root), str(root / "tests")]
suite = unittest.TestSuite()
spec = importlib.util.spec_from_file_location("native_data_api", str(root / "native/worker/data_api.py"))
api = importlib.util.module_from_spec(spec)
spec.loader.exec_module(api)


class DataOnlyBoundary(unittest.TestCase):
    def test_supplement_denial_discards_earlier_reading(self):
        for status in api.GATE_STATUS.values():
            envelope = {"schema": "sckocp-api-v1", "status": "ok", "data": {"earlier": "reading"},
                        "details": {"parts": {"info": {"status": status, "observed_at": "now", "error": "denied"}}}}
            with patch.object(api.shutil, "which", return_value="/usr/bin/sckocp"), patch.object(api, "collect", return_value=envelope):
                observed = api.sample(True)
            self.assertEqual(status, observed["status"])
            self.assertIsNone(observed["data"])
            self.assertNotIn("details", observed)

    def test_only_sampling_flag_is_accepted(self):
        with patch.object(api, "collect") as invocation:
            for operation in ("activate", "rmal", {"command": "info"}, 1):
                with self.assertRaises(ValueError):
                    api.sample(operation)
            invocation.assert_not_called()


suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(DataOnlyBoundary))
for name in ("test_sckocp_public_api", "test_sckocp_security", "test_sckocp_details"):
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
result = unittest.TextTestRunner(verbosity=2).run(suite)
Path("/results/interface-tests.json").write_text(json.dumps({
    "tests": result.testsRun, "errors": len(result.errors), "failures": len(result.failures),
    "status": "passed" if result.wasSuccessful() else "failed", "python": sys.version,
    "scope": "fixed data reads, native gate denial, Primary filtering, trusted executable identity"}))
sys.exit(0 if result.wasSuccessful() else 1)
