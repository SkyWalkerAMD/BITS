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


worker_spec = importlib.util.spec_from_file_location("bits_native_worker_for_test",
    "/opt/bits/native/0.4.0-alpha.2/worker/worker.py")
worker = importlib.util.module_from_spec(worker_spec)
worker_spec.loader.exec_module(worker)


class LiveProjection(unittest.TestCase):
    def test_denial_never_reuses_previous_display_metrics(self):
        record = {"sequence": 4, "observed_at": "2026-01-01T00:00:00Z",
                  "provider": {"status": "license_required", "data": {"pkg_w": 999}}}
        observed = worker.live_sample(record, {"data": {"private": "stale"}})
        self.assertEqual({"sequence": 4, "observed_at": record["observed_at"],
                          "available": False}, observed)

    def test_missing_socket_is_not_a_whole_machine_total(self):
        record = {"sequence": 1, "observed_at": "2026-01-01T00:00:00Z",
                  "os": {"load1": 0}, "provider": {"status": "ok", "data": {
                    "sockets": [{"pkg_w": 100}, {"pkg_w": None}],
                    "cores": [{"mhz": 2000}, {"mhz": 3000}],
                    "private": "not-for-output"}}}
        observed = worker.live_sample(record, None)
        self.assertIsNone(observed["package_w"])
        self.assertIsNone(observed["temp_c"])
        self.assertEqual(2500, observed["mhz"])
        self.assertNotIn("not-for-output", json.dumps(observed))
        self.assertNotIn("psu_w", observed)


suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(DataOnlyBoundary))
suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(LiveProjection))
for name in ("test_sckocp_public_api", "test_sckocp_security", "test_sckocp_details"):
    suite.addTests(unittest.defaultTestLoader.loadTestsFromName(name))
result = unittest.TextTestRunner(verbosity=2).run(suite)
Path("/results/interface-tests.json").write_text(json.dumps({
    "tests": result.testsRun, "errors": len(result.errors), "failures": len(result.failures),
    "status": "passed" if result.wasSuccessful() else "failed", "python": sys.version,
    "scope": "fixed data reads, native gate denial, Primary filtering, trusted executable identity"}))
sys.exit(0 if result.wasSuccessful() else 1)
