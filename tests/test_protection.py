"""Collector-to-protection contracts; run by cloud Linux validation only."""
import json
import os
import tempfile
import unittest
from pathlib import Path

from ocrun.evaluation import ErrorScanner, evaluate
from ocrun.safety import SafetyMonitor, protection_policy, reading


def status(observed, age=0, stale=False):
    return {"observed_at": observed, "age_seconds": age, "stale": stale}


def temperatures(hwmon=60, bmc=90, hwmon_observed="hwmon-1", bmc_observed="bmc-1", bmc_age=0):
    return {
        "cpu_temperature_c": hwmon,
        "cpu_temperatures_c": {"hwmon0:Tdie": hwmon, "ipmi:CPU0_TEMP": bmc},
        "metric_status": {
            "cpu_temperature_c": status(hwmon_observed),
            "cpu_temperatures_c": {
                "stale": False,
                "readings": {"hwmon0:Tdie": status(hwmon_observed),
                             "ipmi:CPU0_TEMP": status(bmc_observed, bmc_age)}}}}


class ProtectionMetadataTests(unittest.TestCase):
    def test_named_fan_uses_fan_map_age(self):
        sample = {"fan_rpm": {"FAN1": 1200},
                  "metric_status": {"fan_rpm": status("bmc-1", age=15)}}
        self.assertIsNone(reading(sample, "fan:FAN1", maximum_age=5))
        self.assertEqual(1200, reading(sample, "fan:FAN1", maximum_age=20))
        sample["metric_status"]["fan_rpm"]["stale"] = True
        self.assertIsNone(reading(sample, "fan:FAN1", maximum_age=20))

    def test_hottest_socket_must_meet_individual_freshness_policy(self):
        sample = temperatures(bmc_age=15)
        self.assertEqual(60, reading(sample, "cpu_temperature_c", maximum_age=5))
        self.assertEqual(90, reading(sample, "cpu_temperature_c", maximum_age=20))
        sample["metric_status"]["cpu_temperatures_c"]["readings"]["ipmi:CPU0_TEMP"]["stale"] = True
        self.assertEqual(60, reading(sample, "cpu_temperature_c", maximum_age=20))

    def test_fresh_physical_socket_remains_usable_if_scalar_source_expired(self):
        sample = temperatures()
        sample["cpu_temperature_c"] = 90
        sample["metric_status"]["cpu_temperature_c"] = status("old-bmc", age=40, stale=True)
        sample["metric_status"]["cpu_temperatures_c"]["readings"]["ipmi:CPU0_TEMP"] = status(
            "old-bmc", age=40, stale=True)
        self.assertEqual(60, reading(sample, "cpu_temperature_c", maximum_age=30))

    def test_repeated_cached_hottest_reading_is_one_exceedance(self):
        monitor = SafetyMonitor(protection_policy({
            "limits": {"cpu_temperature_c": {"max": 80, "consecutive": 2}}}), ["cpu_temperature_c"])
        self.assertIsNone(monitor.check(temperatures(hwmon_observed="hwmon-1", bmc_observed="bmc-1")))
        self.assertIsNone(monitor.check(temperatures(hwmon_observed="hwmon-2", bmc_observed="bmc-1", bmc_age=5)))
        failure = monitor.check(temperatures(hwmon_observed="hwmon-3", bmc_observed="bmc-2"))
        self.assertIsNotNone(failure)
        self.assertEqual("limit_exceeded", failure["code"])

    def test_task_cannot_loosen_host_implicit_consecutive_count(self):
        policy = protection_policy({"limits": {"cpu_temperature_c": {"max": 80}}},
                                   {"limits": {"cpu_temperature_c": {"max": 85, "consecutive": 10}}})
        self.assertLessEqual(policy["limits"]["cpu_temperature_c"].get("consecutive", 3), 3)
        self.assertEqual(80, policy["limits"]["cpu_temperature_c"]["max"])


class AcceptanceTimelineTests(unittest.TestCase):
    def result(self, elapsed_samples, duration):
        profile = {"name": "CPU utilization after warmup", "warmup_seconds": 10,
                   "require_tool_verification": False, "minimum_samples": 3,
                   "metrics": {"cpu_busy_percent": {"min": 90}}, "max_sample_gap_seconds": 5}
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "metrics.jsonl"), "w") as stream:
                for elapsed in elapsed_samples:
                    stream.write(json.dumps({"elapsed_seconds": elapsed, "cpu_busy_percent": 99}) + "\n")
            return evaluate(directory, {"tool": "cpu-burn"}, "completed",
                            {"elapsed_seconds": duration}, profile)

    def test_backward_warmup_records_prevent_pass(self):
        result = self.result([9, 1, 10, 11, 12], 12)
        self.assertEqual("insufficient_data", result["verdict"])
        self.assertGreater(result["acceptance"]["malformed_lines"], 0)

    def test_duplicate_warmup_records_prevent_pass(self):
        result = self.result([5, 5, 10, 11, 12], 12)
        self.assertEqual("insufficient_data", result["verdict"])

    def test_warmup_is_excluded_from_acceptance_gap_measurement(self):
        result = self.result([1, 9, 10, 12, 15], 15)
        self.assertEqual("passed", result["verdict"])
        self.assertEqual(3, result["acceptance"]["samples"])
        self.assertEqual(3, result["acceptance"]["max_sample_gap_seconds"])


class StreamingLogTests(unittest.TestCase):
    def test_truncation_does_not_hide_new_workload_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "workload.log")
            path.write_bytes(b"Clean output\n" * 1000)
            scanner = ErrorScanner(directory, "p95-avx_m1")
            self.assertEqual([], scanner.scan())
            path.write_bytes(b"FATAL ERROR: Rounding was 0.5, expected 0.4\n")
            self.assertTrue(scanner.scan())

    def test_equal_size_file_replacement_does_not_hide_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "workload.log")
            path.write_bytes(b"x" * 4096)
            scanner = ErrorScanner(directory, "p95-avx_m1")
            self.assertEqual([], scanner.scan())
            replacement = Path(directory, "replacement.log")
            replacement.write_bytes(b"FATAL ERROR: new worker failure\n".ljust(4096, b"x"))
            os.replace(str(replacement), str(path))
            self.assertTrue(scanner.scan())
