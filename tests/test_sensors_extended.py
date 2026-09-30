"""Acquisition contracts, exercised only by the cloud Linux test workflow."""
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from ocrun.sensors import Collector, _SourceWorker, hwmon_cpu_temperatures, metrics, parse_ipmi


class SensorReadingsTests(unittest.TestCase):
    def test_all_ipmi_sockets_are_preserved_and_hottest_selected(self):
        sensors = parse_ipmi("CPU0_TEMP | 01 | ok | x | 40 degrees C\n"
                             "CPU1 Temp | 02 | cr | x | 90 degrees C")
        result = metrics(sensors, {})
        self.assertEqual(90, result["cpu_temperature_c"])
        self.assertEqual("ipmi:CPU1 Temp", result["sources"]["cpu_temperature_c"])

    def test_missing_hwmon_label_does_not_hide_another_sensor(self):
        with tempfile.TemporaryDirectory() as directory:
            device = Path(directory, "hwmon0")
            device.mkdir()
            (device / "name").write_text("coretemp\n")
            (device / "temp1_input").write_text("123000\n")
            (device / "temp2_input").write_text("61000\n")
            (device / "temp2_label").write_text("Package id 1\n")
            self.assertEqual({"hwmon0:Package id 1": 61}, hwmon_cpu_temperatures(directory))

    def make_collector(self, settings=None, ipmi=None, turbo=None):
        config = {"initial_wait_seconds": 1, "sample_wait_seconds": 0.01}
        config.update(settings or {})
        collector = Collector(config)
        collector._read_turbostat = lambda: (turbo or {}, None)
        collector._read_ipmi = lambda: (ipmi or {}, None)
        self.addCleanup(collector.close)
        return collector

    def test_control_temperature_is_not_mixed_with_physical_temperature(self):
        collector = self.make_collector()
        with mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={
                "hwmon0:Tctl": 95, "hwmon0:Tdie": 70, "hwmon1:Tdie": 75}):
            sample = collector.sample("task1")
        self.assertEqual(75, sample["cpu_temperature_c"])
        self.assertEqual(95, sample["cpu_control_temperature_c"])
        self.assertEqual({"hwmon0:Tdie": 70, "hwmon1:Tdie": 75}, sample["cpu_temperatures_c"])
        self.assertEqual({"hwmon0:Tctl": 95}, sample["cpu_control_temperatures_c"])
        self.assertFalse(sample["metric_status"]["cpu_temperature_c"]["stale"])
        self.assertIsNotNone(sample["metric_status"]["cpu_temperature_c"]["observed_at"])

    def test_control_only_device_requires_explicit_control_metric_policy(self):
        collector = self.make_collector()
        with mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={"hwmon0:Tctl": 75}):
            sample = collector.sample()
        self.assertIsNone(sample["cpu_temperature_c"])
        self.assertEqual(75, sample["cpu_control_temperature_c"])
        self.assertIn("cpu_temperature_c", sample["missing"])
        self.assertTrue(sample["metric_status"]["cpu_temperature_c"]["stale"])

    def test_explicit_bmc_mapping_keeps_precedence_and_raw_socket_readings(self):
        ipmi = parse_ipmi("Custom | 01 | ok | x | 50 degrees C")
        collector = self.make_collector({"mapping": {"cpu_temperature": ["Custom"]}}, ipmi=ipmi)
        with mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={"hwmon0:Tdie": 70}):
            sample = collector.sample()
        self.assertEqual(50, sample["cpu_temperature_c"])
        self.assertEqual({"ipmi:Custom": 50, "hwmon0:Tdie": 70}, sample["cpu_temperatures_c"])
        self.assertIn("ipmi:Custom", sample["metric_status"]["cpu_temperatures_c"]["readings"])

    def test_stale_ipmi_cache_does_not_reappear_in_metrics_or_alerts(self):
        ipmi = parse_ipmi("CPU Temp | 01 | cr | x | 90 degrees C")
        collector = self.make_collector({"ipmi_interval_seconds": 300}, ipmi=ipmi)
        with mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={}):
            first = collector.sample()
            with collector.workers["ipmi"].lock:
                collector.workers["ipmi"].observed_mono -= 30
            second = collector.sample()
        self.assertEqual(["CPU Temp"], first["sensor_alerts"])
        self.assertIsNone(second["cpu_temperature_c"])
        self.assertEqual({}, second["ipmi_sensors"])
        self.assertEqual([], second["sensor_alerts"])
        self.assertTrue(second["source_status"]["ipmi"]["stale"])
        self.assertGreaterEqual(second["source_status"]["ipmi"]["age_seconds"], 30)

    def test_slow_bmc_does_not_block_current_hwmon_or_spawn_more_workers(self):
        entered, release = threading.Event(), threading.Event()
        calls = []

        def blocked_ipmi():
            calls.append(1)
            entered.set()
            release.wait(3)
            return {}, "BMC unavailable"

        collector = self.make_collector({"initial_wait_seconds": 0.01, "sample_wait_seconds": 0})
        collector._read_ipmi = blocked_ipmi
        try:
            with mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={"hwmon0:Tdie": 65}):
                first = collector.sample()
                self.assertTrue(entered.wait(1))
                workers = [worker.thread for worker in collector.workers.values()]
                second = collector.sample()
                third = collector.sample()
            self.assertEqual(1, len(calls))
            self.assertEqual(workers, [worker.thread for worker in collector.workers.values()])
            for sample in (first, second, third):
                self.assertEqual(65, sample["cpu_temperature_c"])
                self.assertTrue(sample["source_status"]["ipmi"]["in_flight"])
                self.assertTrue(sample["source_status"]["ipmi"]["stale"])
        finally:
            release.set()
            collector.close()
        self.assertTrue(all(not worker.thread.is_alive() for worker in collector.workers.values()))

    def test_independent_sources_are_started_before_either_finishes(self):
        turbo_entered, ipmi_entered = threading.Event(), threading.Event()

        def turbo():
            turbo_entered.set()
            if not ipmi_entered.wait(1):
                return {}, "IPMI was serialized behind turbostat"
            return {"Avg_MHz": 3000}, None

        def ipmi():
            ipmi_entered.set()
            if not turbo_entered.wait(1):
                return {}, "Turbostat was serialized behind IPMI"
            return parse_ipmi("System Power | 01 | ok | x | 200 Watts"), None

        collector = self.make_collector({"initial_wait_seconds": 2})
        collector._read_turbostat, collector._read_ipmi = turbo, ipmi
        with mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={}):
            sample = collector.sample()
        self.assertEqual(3000, sample["cpu_avg_mhz"])
        self.assertEqual(200, sample["system_watts"])
        self.assertNotIn("turbostat", sample["errors"])
        self.assertNotIn("ipmi", sample["errors"])

    def test_failed_source_refresh_preserves_original_observation_age(self):
        results = [({"value": 42}, None), ({}, "Disconnected")]
        worker = _SourceWorker("test", lambda: results.pop(0))
        try:
            worker.request(0, 0)
            self.assertTrue(worker.finished.wait(1))
            with worker.lock:
                worker.observed_mono -= 10
                observed_mono = worker.observed_mono
            worker.request(0, 0)
            self.assertTrue(worker.finished.wait(1))
            data, meta = worker.snapshot(20)
            self.assertEqual({"value": 42}, data)
            self.assertEqual("cached", meta["status"])
            self.assertEqual("Disconnected", meta["error"])
            self.assertEqual(observed_mono, worker.observed_mono)
            self.assertGreaterEqual(meta["age_seconds"], 10)
            data, meta = worker.snapshot(5)
            self.assertEqual({}, data)
            self.assertTrue(meta["stale"])
        finally:
            worker.stop()
            worker.thread.join(2)

    def test_unavailable_source_uses_retry_cooldown(self):
        read = mock.Mock(return_value=({}, "Tool unavailable"))
        worker = _SourceWorker("test", read)
        try:
            worker.request(0, 30)
            self.assertTrue(worker.finished.wait(1))
            worker.request(0, 30)
            self.assertEqual(1, read.call_count)
        finally:
            worker.stop()
            worker.thread.join(2)

    def test_source_exception_is_reported_without_killing_worker(self):
        worker = _SourceWorker("test", mock.Mock(side_effect=ValueError("bad output")))
        try:
            worker.request(0, 0)
            self.assertTrue(worker.finished.wait(1))
            data, meta = worker.snapshot(10)
            self.assertEqual({}, data)
            self.assertEqual("bad output", meta["error"])
            self.assertTrue(worker.thread.is_alive())
        finally:
            worker.stop()
            worker.thread.join(2)

    def test_collector_close_prevents_restart(self):
        collector = self.make_collector()
        collector.close()
        with self.assertRaises(RuntimeError):
            collector.sample()

    def test_invalid_age_limit_is_rejected(self):
        for value in (0, -1, "nan", "invalid"):
            with self.assertRaises(ValueError):
                Collector({"ipmi_max_age_seconds": value})
