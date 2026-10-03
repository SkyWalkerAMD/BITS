"""Source regression of the data-only boundary, in disposable Linux only."""
import json
import os
from pathlib import Path
import sys
import tempfile
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
    "/opt/bits/native/0.4.2/worker/worker.py")
worker = importlib.util.module_from_spec(worker_spec)
worker_spec.loader.exec_module(worker)


class LiveProjection(unittest.TestCase):
    def test_idle_monitor_replaces_snapshot_clears_denial_and_recovers_without_batch(self):
        clock, snapshots, detail_reads = [0.0], [], []
        original_save = worker.common.save

        def sample(include_details):
            detail_reads.append(include_details)
            if len(detail_reads) == 3:
                raise OSError("synthetic unavailable provider")
            denied = len(detail_reads) == 2
            return {"status": "license_required" if denied else "ok", "data": {
                "sockets": [{"id": 0, "pkg_w": 108, "temp_max_c": 34}],
                "cores": [{"cpu": 0, "socket": 0, "mhz": 3300}]}}

        def save(path, value):
            original_save(path, value)
            if path.name != "live.json":
                return
            snapshots.append(value)
            clock[0] += 31  # Advance past the supplemental interval without sleeping.
            if len(snapshots) == 4:
                worker.STOP = True

        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            with patch.object(worker, "STOP", False), \
                 patch.object(worker.time, "monotonic", side_effect=lambda: clock[0]), \
                 patch.object(worker.data_api, "sample", side_effect=sample), \
                 patch.object(worker.collector, "os_context", return_value={"load1": 0}), \
                 patch.object(worker.common, "save", side_effect=save):
                worker.monitor(directory)
            self.assertEqual({"live.json", "info.json"}, {p.name for p in directory.iterdir()})
            self.assertEqual(snapshots[-1], json.loads((directory / "live.json").read_text()))
        self.assertEqual([1, 2, 3, 4], [v["sequence"] for v in snapshots])
        self.assertEqual([True, False, False, True], [v["available"] for v in snapshots])
        self.assertEqual([True] * 4, detail_reads)
        for denied in snapshots[1:3]:
            self.assertEqual({"sequence", "observed_at", "available"}, set(denied))
        self.assertEqual(108, snapshots[-1]["package_w"])

    def test_info_projection_uses_filtered_sections_and_invalidates_gate_failure(self):
        from sckocp_detail_fixture import INFO
        from sckocp_api.provider import parse_console
        data = parse_console(INFO.encode(), "info")
        data["cpus"][0]["license"] = "FORBIDDEN"
        data["dimms"][0]["fields"]["password"] = "FORBIDDEN"
        details = {"parts": {"info": {"status": "ok", "data": data}}}
        value = worker.hardware_info({"status": "ok"}, details)
        self.assertEqual("ok", value["status"])
        self.assertEqual(24, value["cpus"][0]["cores"])
        self.assertIn("Part Number", value["dimms"][0]["fields"])
        self.assertEqual(len(data["sections"]), len(value["sections"]))
        encoded = json.dumps(value)
        for prohibited in ("FORBIDDEN", "Refresh", "Secondary", "tRFC", "raw_info"):
            self.assertNotIn(prohibited, encoded)
        self.assertNotIn("title", value["sections"][0])
        denied = worker.hardware_info({"status": "license_required"}, details)
        self.assertEqual({"schema", "observed_at", "status"}, set(denied))
        self.assertEqual("unavailable", denied["status"])
        self.assertEqual("unavailable", worker.hardware_info({"status": "ok"}, None)["status"])

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
