"""Provider freshness and activation failures through the actual collector worker."""
import copy
import json
import os
import tempfile
import threading
import time
import unittest
from unittest import mock

from ocrun.sensors import Collector, _SourceWorker


def metric(value, age=0):
    return {"value": value, "status": "ok" if value is not None else "unavailable",
            "unit": "C", "source": "fixture" if value is not None else None,
            "age_s": age if value is not None else None}


def envelope():
    return {"schema": "ocrun-sckocp-v1", "status": "ok", "error": None,
            "observed_at": "2026-09-22T00:00:00Z", "data": {
                "sockets": [{"id": 0, "metrics": {
                    "temperature_c": metric(72), "control_temperature_c": metric(92),
                    "package_watts": metric(110), "dram_watts": metric(None)}}],
                "cores": [{"cpu": 0, "socket": 0, "metrics": {
                    "active_mhz": metric(3200), "c0_percent": metric(60), "temperature_c": metric(70)}}],
                "system": {"metrics": {"psu_input_watts": metric(200)}}}}


class SckocpCollectorTests(unittest.TestCase):
    def test_provider_age_starts_at_receipt_without_double_counting_native_age(self):
        clock = [100.0]
        def read():
            clock[0] += 20
            return envelope(), None
        with mock.patch("ocrun.sensors.time.monotonic", side_effect=lambda: clock[0]):
            worker = _SourceWorker("receipt-age", read, age_from_completion=True)
            try:
                worker.request(0, 0)
                self.assertTrue(worker.finished.wait(1))
                data, meta = worker.snapshot(30)
                self.assertEqual(20, meta["collection_seconds"])
                self.assertEqual(0, meta["age_seconds"])
                self.assertTrue(data)
            finally:
                worker.stop()
                worker.thread.join(2)

    def test_actual_agent_collection_exposes_provider_data_for_heartbeat_and_logs(self):
        from ocrun.agent import Agent
        agent = Agent.__new__(Agent)
        sample = {"timestamp": "2026-09-22T00:00:00Z", "sckocp": envelope(),
                  "sckocp_available": 1, "cpu_core_active_mean_mhz": 3200,
                  "source_status": {"sckocp": {"stale": False}}}
        agent.config = {}
        agent.collector = mock.Mock()
        agent.collector.sample.return_value = sample
        agent.task_started = time.monotonic()
        agent.collect_failed = threading.Event()
        agent.process = None
        stop = threading.Event()
        agent.protection = mock.Mock()
        agent.protection.check.side_effect = lambda value: stop.set()
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "metrics.jsonl")
            agent.collect_loop(path, "native-task", stop)
            with open(path) as stream:
                saved = json.loads(stream.readline())
        self.assertFalse(agent.collect_failed.is_set())
        self.assertEqual(envelope(), saved["sckocp"])
        self.assertEqual(envelope(), agent.latest_metrics["sckocp"])
        self.assertEqual(3200, agent.latest_metrics["cpu_core_active_mean_mhz"])

    def collector(self, data=None, config=None):
        settings = {"enabled": True, "poll_interval_seconds": 300, "max_age_seconds": 30}
        settings.update(config or {})
        collector = Collector({"sckocp": settings, "initial_wait_seconds": 1, "sample_wait_seconds": 0.1})
        collector._read_sckocp = mock.Mock(return_value=(data or envelope(), None))
        collector._read_ipmi = lambda: ({}, None)
        collector._read_turbostat = lambda: ({}, None)
        self.addCleanup(collector.close)
        return collector

    @mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={})
    def test_native_metrics_and_tctl_are_kept_separate(self, hwmon):
        sample = self.collector().sample()
        self.assertEqual(72, sample["cpu_temperature_c"])
        self.assertEqual(92, sample["cpu_control_temperature_c"])
        self.assertEqual(110, sample["cpu_package_watts"])
        self.assertEqual(200, sample["system_watts"])
        self.assertEqual(3200, sample["cpu_core_active_mean_mhz"])
        self.assertEqual(60, sample["cpu_core_c0_mean_percent"])
        self.assertIsNone(sample["cpu_busy_percent"])
        self.assertIsNone(sample["cpu_avg_mhz"])
        self.assertEqual(1, sample["sckocp_available"])
        self.assertFalse(sample["metric_status"]["cpu_temperature_c"]["stale"])

    @mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={})
    def test_bmc_age_excludes_old_power_even_in_fresh_snapshot(self, hwmon):
        data = envelope()
        data["data"]["system"]["metrics"]["psu_input_watts"]["age_s"] = 40
        sample = self.collector(data).sample()
        self.assertIsNone(sample["system_watts"])
        self.assertEqual(72, sample["cpu_temperature_c"])
        self.assertTrue(sample["metric_status"]["system_watts"]["stale"])

    @mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={})
    def test_incomplete_socket_power_never_becomes_partial_total(self, hwmon):
        data = envelope()
        socket = copy.deepcopy(data["data"]["sockets"][0])
        socket["id"] = 1
        socket["metrics"]["package_watts"] = metric(None)
        data["data"]["sockets"].append(socket)
        sample = self.collector(data).sample()
        self.assertIsNone(sample["cpu_package_watts"])

    @mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={})
    def test_failed_activation_refresh_discards_previously_valid_data(self, hwmon):
        collector = self.collector()
        self.assertEqual(1, collector.sample()["sckocp_available"])
        collector._read_sckocp.return_value = ({}, "license_denied")
        worker = collector.workers["sckocp"]
        worker.request(0, 0)
        self.assertTrue(worker.finished.wait(1))
        sample = collector.sample()
        self.assertIsNone(sample["sckocp_available"])
        self.assertIsNone(sample["cpu_temperature_c"])
        self.assertIsNone(sample["sckocp"]["data"])
        self.assertEqual("license_denied", sample["sckocp"]["status"])
        self.assertIn("sckocp", sample["errors"])

    @mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={"hwmon0:Tdie": 65})
    def test_unavailable_provider_does_not_hide_independent_sources(self, hwmon):
        collector = self.collector()
        collector._read_sckocp.return_value = ({}, "platform_required")
        sample = collector.sample()
        self.assertEqual(65, sample["cpu_temperature_c"])
        self.assertEqual("hwmon:max-package-or-die-temperature", sample["sources"]["cpu_temperature_c"])
        self.assertIsNone(sample["sckocp_available"])

    @mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={})
    def test_stale_provider_never_reappears_in_metrics(self, hwmon):
        collector = self.collector()
        collector.sample()
        with collector.workers["sckocp"].lock:
            collector.workers["sckocp"].observed_mono -= 40
        sample = collector.sample()
        self.assertIsNone(sample["cpu_temperature_c"])
        self.assertIsNone(sample["sckocp"]["data"])
        self.assertEqual({}, sample["cpu_temperatures_c"])

    def test_disabled_by_default_and_invalid_configuration_rejected(self):
        self.assertFalse(Collector().sckocp_enabled)
        self.assertEqual(20.5, Collector({"sckocp": {"enabled": True}}).initial_wait)
        for settings in ({"enabled": "yes"}, {"enabled": True, "binary": "sckocp"},
                         {"enabled": True, "interval_seconds": 21, "timeout_seconds": 20}):
            with self.assertRaises(ValueError):
                Collector({"sckocp": settings})

    @mock.patch("ocrun.sensors.hwmon_cpu_temperatures", return_value={})
    def test_partial_core_or_socket_temperature_never_passes_as_full_coverage(self, hwmon):
        data = envelope()
        data["data"]["sockets"][0]["metrics"]["temperature_c"] = metric(None)
        sample = self.collector(data).sample()
        self.assertEqual(70, sample["sckocp"]["data"]["cores"][0]["metrics"]["temperature_c"]["value"])
        self.assertIsNone(sample["cpu_temperature_c"])
        self.assertEqual({}, sample["cpu_temperatures_c"])
        data = envelope()
        socket = copy.deepcopy(data["data"]["sockets"][0])
        socket["id"] = 1
        socket["metrics"]["temperature_c"] = metric(None)
        data["data"]["sockets"].append(socket)
        sample = self.collector(data).sample()
        self.assertIsNone(sample["cpu_temperature_c"])
        self.assertEqual({}, sample["cpu_temperatures_c"])
