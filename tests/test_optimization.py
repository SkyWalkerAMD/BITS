import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ocrun.common import write_json
from ocrun.evaluation import ErrorScanner, evaluate, validate_acceptance
from ocrun.logs import LogStore, digest, seal, verify_directory, verify_server
from ocrun.queue import Queue
from ocrun.safety import SafetyMonitor, protection_policy
from ocrun.workloads import validate_task
from test_runtime import Backend


class AcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = self.temp.name
        self.profile = {"name": "CPU test profile", "require_tool_verification": False,
                        "metrics": {"cpu_busy_percent": {"min": 90}}, "minimum_samples": 3}
        self.samples([95, 96, 98])

    def samples(self, values):
        with open(os.path.join(self.directory, "metrics.jsonl"), "w") as stream:
            for index, value in enumerate(values):
                stream.write(json.dumps({"elapsed_seconds": index + 1, "cpu_busy_percent": value}) + "\n")

    def result(self, **kwargs):
        return evaluate(self.directory, {"tool": "cpu-burn"}, kwargs.get("status", "completed"),
                        {"elapsed_seconds": kwargs.get("seconds", 3)}, kwargs.get("profile", self.profile))

    def test_explicit_profile_is_required_to_pass(self):
        self.assertEqual("insufficient_data", self.result(profile={})["verdict"])
        self.assertEqual("passed", self.result()["verdict"])
        self.assertEqual("configured_profile_only", self.result()["acceptance"]["scope"])

    def test_incomplete_run_cannot_pass(self):
        self.assertEqual("insufficient_data", self.result(status="interrupted")["verdict"])
        self.assertEqual("failed", self.result(status="timed_out")["verdict"])

    def test_low_actual_load_fails(self):
        self.samples([95, 30, 98])
        self.assertEqual("failed", self.result()["verdict"])

    def test_missing_data_is_not_zero_or_a_pass(self):
        self.samples([95, None, 98])
        self.assertEqual("insufficient_data", self.result()["verdict"])

    def test_long_gap_and_short_duration_prevent_pass(self):
        self.assertEqual("insufficient_data", self.result(seconds=100)["verdict"])
        self.profile["minimum_duration_seconds"] = 10
        self.assertEqual("insufficient_data", self.result()["verdict"])

    def test_full_tool_verification_is_not_invented(self):
        del self.profile["require_tool_verification"]
        self.assertEqual("insufficient_data", self.result()["verdict"])

    def test_error_split_between_reads_is_detected(self):
        path = Path(self.directory, "workload.log")
        path.write_bytes(b"Worker 1: FATAL ER")
        scanner = ErrorScanner(self.directory, "p95-avx_m1")
        self.assertEqual([], scanner.scan())
        with path.open("ab") as stream:
            stream.write(b"ROR: Rounding was 0.5, expected 0.4\n")
        self.assertTrue(scanner.scan())
        self.assertEqual("failed", evaluate(self.directory, {}, "completed", {}, self.profile, scanner)["verdict"])

    def test_stress_ng_failures_and_finite_profile_bounds(self):
        Path(self.directory, "workload.log").write_text("stress-ng: fail: [123] computation incorrect\n")
        self.assertTrue(ErrorScanner(self.directory, "stress-ng").scan())
        with self.assertRaises(ValueError):
            validate_acceptance({"metrics": {"cpu_busy_percent": {"min": float("nan")}}})

    def test_parameters_which_do_not_take_effect_are_rejected(self):
        with self.assertRaises(ValueError):
            validate_task({"tool": "p95-avx_m1", "threads": 2, "duration_seconds": 60})
        with self.assertRaises(ValueError):
            validate_task({"tool": "stress", "memory_mb": 200, "duration_seconds": 60})


class VerifiedLogsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.directory = self.root / "logs" / "node1" / "job1"
        self.directory.mkdir(parents=True)
        write_json(str(self.directory / "task.json"), {"id": "job1"})
        write_json(str(self.directory / "result.json"), {"status": "completed"})
        (self.directory / "workload.log").write_text("done\n")
        seal(str(self.directory))
        self.backend = Backend()
        self.queue = Queue(self.backend, "node1")
        self.config = {"host_id": "node1", "log_dir": str(self.root / "logs" / "node1"),
                       "state_dir": str(self.root / "state"), "local_retention_days": 1,
                       "upload": {"host": "127.0.0.1", "password": "test-only"}}

    def test_manifest_detects_changed_bytes_and_path_escape(self):
        self.assertEqual(digest(str(self.directory / "manifest.json")), verify_directory(str(self.directory)))
        (self.directory / "workload.log").write_text("evil\n")
        with self.assertRaises(ValueError):
            verify_directory(str(self.directory))
        manifest = {"schema_version": 1, "task_id": "job1", "files": [{"path": "../outside", "bytes": 0, "sha256": "x"}]}
        write_json(str(self.directory / "manifest.json"), manifest)
        with self.assertRaises(ValueError):
            verify_directory(str(self.directory))

    def make_old(self):
        old = time.time() - 3 * 86400
        os.utime(str(self.directory / "manifest.json"), (old, old))

    def ack(self):
        self.backend.execute("SET", self.queue.key("log_ack:job1"), json.dumps({
            "manifest_sha256": digest(str(self.directory / "manifest.json")), "verified_at": "2026-09-22T00:00:00Z"}))

    def test_transfer_success_without_receiver_ack_never_deletes_logs(self):
        self.make_old()
        with mock.patch("ocrun.logs.capture", return_value=(0, "", "")) as transfer:
            result = LogStore(self.config, self.queue).upload()
        self.assertTrue(self.directory.exists())
        self.assertEqual(1, result["pending"])
        self.assertIn("--checksum", transfer.call_args[0][0])

    def test_receiver_verified_old_logs_can_be_pruned(self):
        self.make_old()
        self.ack()
        with mock.patch("ocrun.logs.capture") as transfer:
            result = LogStore(self.config, self.queue).upload()
        self.assertFalse(self.directory.exists())
        self.assertEqual(1, result["verified"])
        transfer.assert_not_called()

    def test_post_seal_change_and_active_task_are_retained(self):
        self.make_old()
        self.ack()
        (self.directory / "workload.log").write_text("new evidence\n")
        with self.assertRaises(ValueError):
            LogStore(self.config, self.queue).upload()
        self.assertTrue(self.directory.exists())

    def test_unsealed_new_file_prevents_retention_cleanup(self):
        self.make_old()
        self.ack()
        (self.directory / "late-error.log").write_text("new evidence after sealing\n")
        with self.assertRaises(ValueError):
            LogStore(self.config, self.queue).upload()
        self.assertTrue((self.directory / "late-error.log").exists())
        with mock.patch("ocrun.logs.capture", return_value=(0, "", "")):
            LogStore(self.config, self.queue).upload(active_id="job1")
        self.assertTrue(self.directory.exists())

    def test_verifier_waits_for_terminal_task_and_writes_matching_ack(self):
        enrollments = self.root / "enrollments"
        enrollments.mkdir()
        write_json(str(enrollments / "node1.json"), {})
        config = {"data_root": str(self.root), "enrollments": str(enrollments)}
        self.queue.enqueue({"id": "job1", "tool": "cpu-burn", "duration_seconds": 1})
        task = self.queue.claim("test")
        self.assertEqual(0, verify_server(config, self.backend)["verified"])
        self.queue.finish(task, "completed", {"verdict": "insufficient_data"})
        self.assertEqual(1, verify_server(config, self.backend)["verified"])
        ack = json.loads(self.backend.execute("GET", self.queue.key("log_ack:job1")))
        self.assertEqual(digest(str(self.directory / "manifest.json")), ack["manifest_sha256"])

    def test_symlink_member_is_never_followed(self):
        (self.root / "private").write_text("private data")
        (self.directory / "linked").symlink_to(self.root / "private")
        with self.assertRaises(ValueError):
            seal(str(self.directory))
