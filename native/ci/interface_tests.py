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
    "/opt/bits/native/0.4.0-alpha.5/worker/worker.py")
worker = importlib.util.module_from_spec(worker_spec)
worker_spec.loader.exec_module(worker)


class LiveProjection(unittest.TestCase):
    def test_hardware_whitelist_and_numeric_core_order(self):
        from sckocp_detail_fixture import OVERVIEW
        from sckocp_api.provider import parse_console
        data = {"vendor": "GenuineIntel", "family": 6,
                "sockets": [{"id": 0, "pkg_w": 120}],
                "cores": [{"cpu": i, "socket": 0, "mhz": 3300, "temp_c": 24,
                           "vid_v": .91, "c0_pct": 100, "c6_pct": 0,
                           "rmal": "FORBIDDEN"} for i in (0, 1, 10, 11, 2, 3)],
                "timings": "FORBIDDEN", "license": "FORBIDDEN"}
        extra = {"observed_at": "2026-01-01T00:00:00Z", "data": parse_console(OVERVIEW.encode(), "overview")}
        value = worker.live_hardware(data, extra)
        self.assertEqual([0, 1, 2, 3, 10, 11], [c["cpu"] for c in value["cores"]])
        s = value["sockets"][0]
        self.assertIsNone(s["temp_c"])
        self.assertEqual(120, s["package_w"])
        self.assertEqual(4000, s["extra"]["memory_mts"])
        self.assertEqual(256, s["extra"]["memory_total_gb"])
        self.assertEqual(24, s["extra"]["physical_cores"])
        self.assertEqual(0, s["extra"]["pc6_pct"])
        self.assertEqual(1.83, s["extra"]["vccin_v"])
        self.assertNotIn("FORBIDDEN", json.dumps(value))
        self.assertNotIn("sections", json.dumps(value))
        self.assertNotIn("extra", worker.live_hardware(data, None)["sockets"][0])

    def test_sparse_amd_and_multisocket_values(self):
        data = {"vendor": "AuthenticAMD", "family": 25,
                "sockets": [{"id": 1, "pkg_w": None}, {"id": 0, "pkg_w": 88}],
                "cores": [{"cpu": 2, "socket": 1, "mhz": 2000, "c0_pct": 0},
                          {"cpu": 10, "socket": 0, "mhz": 3000, "c0_pct": 100}]}
        value = worker.live_hardware(data, None)
        self.assertEqual([0, 1], [s["id"] for s in value["sockets"]])
        self.assertEqual([10, 2], [c["cpu"] for c in value["cores"]])
        self.assertIsNone(value["sockets"][1]["package_w"])
        self.assertIsNone(value["cores"][1]["temp_c"])
        self.assertIsNone(value["cores"][1]["vid_v"])
        self.assertEqual(0, value["cores"][1]["c0_pct"])

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
