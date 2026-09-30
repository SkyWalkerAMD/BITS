import ast
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import tarfile
import tempfile
import threading
import unittest
from unittest import mock

import fakeredis

from ocrun.common import identifier, os_release, read_json, write_json
from ocrun.rediswire import Redis, RedisError
from ocrun.queue import Queue
from ocrun.agent import Agent
from ocrun.provision import render_server, render_rsync
from ocrun.report import summarize
from ocrun.sensors import parse_ipmi, parse_turbostat, metrics, hwmon_cpu_temperatures
from ocrun.unpack import unpack
from ocrun.workloads import validate_task, command


class Backend:
    def __init__(self, client=None):
        self.client = client or fakeredis.FakeRedis(decode_responses=True)

    def execute(self, *arguments):
        return self.client.execute_command(*arguments)


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.queue = Queue(self.backend, "node1")

    def task(self, task_id="job1"):
        return {"id": task_id, "tool": "stress", "duration_seconds": 10}

    def test_last_task_remains_running_with_empty_pending_queue(self):
        self.queue.enqueue(self.task())
        task = self.queue.claim("owner")
        self.assertEqual(0, self.backend.execute("LLEN", self.queue.key("pending")))
        self.assertEqual("running", self.queue.running()["status"])
        self.assertIsNone(self.queue.claim("second-owner"))
        self.queue.finish(task, "completed", {"verdict": "not_evaluated"})
        self.assertIsNone(self.queue.running())

    def test_submission_is_idempotent(self):
        self.assertTrue(self.queue.enqueue(self.task()))
        self.assertFalse(self.queue.enqueue(self.task()))
        self.assertEqual(1, self.backend.execute("LLEN", self.queue.key("pending")))

    def test_concurrent_claim_has_one_winner(self):
        self.queue.enqueue(self.task())
        outcomes = []
        def claim(index):
            outcomes.append(self.queue.claim("owner" + str(index)))
        threads = [threading.Thread(target=claim, args=(i,)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(1, sum(task is not None for task in outcomes))

    def test_fifo_and_state_retention(self):
        self.queue.enqueue(self.task("first"))
        self.queue.enqueue(self.task("second"))
        first = self.queue.claim("a")
        self.assertEqual("first", first["id"])
        self.queue.finish(first, "failed", {"reason": "exit 1"})
        self.assertEqual("second", self.queue.claim("b")["id"])
        self.assertEqual("failed", self.queue.get("first")["status"])

    def test_wrong_owner_cannot_acknowledge(self):
        self.queue.enqueue(self.task())
        task = self.queue.claim("owner")
        with self.assertRaises(RuntimeError):
            self.queue.finish(dict(task, claim_token="other"), "completed", {})
        self.assertEqual("running", self.queue.running()["status"])

    def test_repeated_ack_is_safe(self):
        self.queue.enqueue(self.task())
        task = self.queue.claim("owner")
        self.queue.finish(task, "failed", {"reason": "exit"})
        self.queue.finish(task, "failed", {"reason": "exit"})
        self.assertEqual("failed", self.queue.get("job1")["status"])

    def test_hosts_are_isolated(self):
        other = Queue(self.backend, "node2")
        self.queue.enqueue(self.task())
        self.assertIsNone(other.claim("owner"))
        self.assertEqual([], other.tasks())

    def test_missing_payload_does_not_drop_queue_entry(self):
        self.backend.execute("RPUSH", self.queue.key("pending"), "missing")
        with self.assertRaises(Exception):
            self.queue.claim("owner")
        self.assertEqual(1, self.backend.execute("LLEN", self.queue.key("pending")))

    def test_heartbeat_has_expiry_without_deleting_running_task(self):
        self.queue.enqueue(self.task())
        self.queue.claim("owner")
        self.queue.heartbeat({"task_id": "job1"})
        self.assertGreater(self.backend.execute("TTL", self.queue.key("heartbeat")), 0)
        self.backend.execute("DEL", self.queue.key("heartbeat"))
        self.assertEqual("running", self.queue.running()["status"])

    def test_duplicate_device_cannot_acquire_active_session(self):
        self.assertTrue(self.queue.acquire_session("first-agent", 180))
        self.assertFalse(self.queue.acquire_session("cloned-agent", 180))
        self.queue.enqueue(self.task())
        with self.assertRaises(Exception):
            self.queue.claim("claim", "cloned-agent")
        self.assertEqual("job1", self.queue.claim("claim", "first-agent")["id"])

    def test_expired_owner_cannot_refresh_or_release_new_session(self):
        self.queue.acquire_session("old", 180)
        self.backend.execute("DEL", self.queue.key("session"))
        self.assertTrue(self.queue.acquire_session("new", 180))
        self.assertFalse(self.queue.refresh_session("old", 180))
        self.queue.release_session("old")
        self.assertEqual("new", self.backend.execute("GET", self.queue.key("session")))


class AgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.queue = Queue(Backend(), "node1")
        self.agent = Agent({"host_id": "node1", "state_dir": self.temp.name,
                            "log_dir": os.path.join(self.temp.name, "logs"), "required_metrics": []}, queue=self.queue)
        self.queue.enqueue({"id": "job1", "tool": "stress", "duration_seconds": 10})

    def test_restart_recovers_running_task_without_losing_record(self):
        task = self.queue.claim("previous")
        write_json(self.agent.journal, {"task": task})
        self.agent.recover()
        self.assertEqual("interrupted", self.queue.get("job1")["status"])
        self.assertFalse(os.path.exists(self.agent.journal))

    def test_lost_claim_response_is_recovered(self):
        self.queue.claim("lost-response")
        self.agent.recover()
        self.assertEqual("interrupted", self.queue.get("job1")["status"])

    def test_result_is_retained_on_controller_failure(self):
        task = self.queue.claim("owner")
        write_json(self.agent.journal, {"task": task, "status": "completed", "result": {"verdict": "not_evaluated"}})
        with mock.patch.object(self.queue, "finish", side_effect=ConnectionError("offline")):
            with self.assertRaises(ConnectionError):
                self.agent.flush_result()
        self.assertTrue(os.path.exists(self.agent.journal))
        self.agent.flush_result()
        self.assertEqual("completed", self.queue.get("job1")["status"])

    def test_missing_executable_fails_and_preserves_result(self):
        task = self.queue.claim("owner")
        with mock.patch("ocrun.agent.command", side_effect=ValueError("Missing tool")):
            self.agent.execute_task(task)
        self.assertEqual("failed", self.queue.get("job1")["status"])
        self.assertTrue(os.path.isfile(os.path.join(self.agent.log_dir, "job1", "result.json")))

    def test_missing_required_temperature_prevents_workload_launch(self):
        self.agent.config["required_metrics"] = ["cpu_temperature_c"]
        task = self.queue.claim("owner")
        with mock.patch("ocrun.agent.command", return_value=(["fake"], self.temp.name, False)), \
             mock.patch("ocrun.agent.Collector.sample", return_value={"cpu_temperature_c": None}), \
             mock.patch("ocrun.agent.subprocess.Popen") as popen:
            self.agent.execute_task(task)
        popen.assert_not_called()
        self.assertEqual("failed", self.queue.get("job1")["status"])

    def execute_mock_process(self, one_shot, exit_code):
        task = self.queue.claim("owner")
        process = mock.Mock(pid=123)
        process.poll.return_value = exit_code
        with mock.patch("ocrun.agent.command", return_value=(["fake"], self.temp.name, one_shot)), \
             mock.patch("ocrun.agent.Collector.sample", return_value={"cpu_temperature_c": 50}), \
             mock.patch("ocrun.agent.threading.Thread"), \
             mock.patch("ocrun.agent.subprocess.Popen", return_value=process), \
             mock.patch("ocrun.agent.stop_process") as stop:
            self.agent.execute_task(task)
        stop.assert_called_once_with(None if self.agent.stop.is_set() else process)
        return self.queue.get("job1")

    def test_normal_benchmark_exit_records_completed_not_hardware_pass(self):
        result = self.execute_mock_process(True, 0)
        self.assertEqual("completed", result["status"])
        self.assertEqual("insufficient_data", result["result"]["verdict"])

    def test_early_stress_exit_is_failure(self):
        self.assertEqual("failed", self.execute_mock_process(False, 0)["status"])

    def test_nonzero_exit_is_failure(self):
        self.assertEqual("failed", self.execute_mock_process(True, 2)["status"])

    def test_cancel_stops_own_process_and_preserves_logs(self):
        self.queue.redis.execute("SET", self.queue.key("cancel"), "job1")
        self.assertEqual("cancelled", self.execute_mock_process(False, None)["status"])

    def test_service_stop_marks_interrupted(self):
        self.agent.stop.set()
        self.assertEqual("interrupted", self.execute_mock_process(False, None)["status"])


class SensorTests(unittest.TestCase):
    def test_ipmi_missing_zero_and_reordered_sensors(self):
        text = "PSU2 Power In | E4h | ok | 10.0 | 0 Watts\nCPU Package Temp | 01h | ok | 3.0 | 62 degrees C\nPSU1 Power In | DFh | ns | 10.0 | No Reading\nCPU_FAN | 32h | ok | 29.0 | 1800 RPM"
        data = parse_ipmi(text)
        result = metrics(data, {})
        self.assertEqual(62, result["cpu_temperature_c"])
        self.assertEqual(0, data["PSU2 Power In"]["value"])
        self.assertIsNone(result["system_watts"])
        self.assertEqual(1800, result["fan_rpm"]["CPU_FAN"])

    def test_complete_psu_sum(self):
        data = parse_ipmi("PSU1 Power In | 01 | ok | x | 100 Watts\nPSU2 Power In | 02 | ok | x | 125 Watts")
        self.assertEqual(225, metrics(data, {})["system_watts"])

    def test_turbostat_header_order_not_fixed_column_numbers(self):
        data = parse_turbostat("CPU PkgWatt Bzy_MHz Avg_MHz PkgTmp Busy%\n- 125.5 4200 3100 70 75\n0 100 4000 3000 69 74")
        self.assertEqual(125.5, data["PkgWatt"])
        self.assertEqual(3100, data["Avg_MHz"])

    def test_no_summary_does_not_mislabel_single_core_as_package(self):
        text = "CPU Avg_MHz PkgWatt\n0 2000 10\n1 4000 20"
        self.assertEqual({}, parse_turbostat(text))

    def test_invalid_sensor_never_becomes_zero(self):
        data = parse_ipmi("CPU Temp | 01 | ns | x | No Reading")
        self.assertIsNone(metrics(data, {})["cpu_temperature_c"])

    def test_critical_temperature_is_preserved(self):
        data = parse_ipmi("CPU Temp | 01 | cr | x | 95 degrees C")
        self.assertEqual(95, metrics(data, {})["cpu_temperature_c"])
        self.assertEqual("cr", data["CPU Temp"]["status"])

    def test_hwmon_only_uses_identified_cpu_sensors(self):
        with tempfile.TemporaryDirectory() as directory:
            cpu = Path(directory, "hwmon0")
            cpu.mkdir()
            (cpu / "name").write_text("k10temp\n")
            (cpu / "temp1_label").write_text("Tctl\n")
            (cpu / "temp1_input").write_text("61500\n")
            board = Path(directory, "hwmon1")
            board.mkdir()
            (board / "name").write_text("nct6775\n")
            (board / "temp1_label").write_text("Tctl\n")
            (board / "temp1_input").write_text("120000\n")
            self.assertEqual({"hwmon0:Tctl": 61.5}, hwmon_cpu_temperatures(directory))

    def test_mapping_supports_board_specific_sensor_names(self):
        data = parse_ipmi("Custom Temp | 01 | ok | x | 42 degrees C")
        self.assertEqual(42, metrics(data, {}, {"cpu_temperature": ["Custom Temp"]})["cpu_temperature_c"])


class CompatibilityTests(unittest.TestCase):
    def test_os_matrix(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "os-release")
            for distro, version in [("centos", "7"), ("rocky", "8.10"), ("rocky", "9.6"), ("rocky", "10.0"),
                                    ("ubuntu", "20.04"), ("ubuntu", "22.04"), ("ubuntu", "24.04"), ("ubuntu", "26.04")]:
                with self.subTest(distro=distro, version=version):
                    Path(path).write_text('ID={}\nVERSION_ID="{}"\n'.format(distro, version))
                    self.assertTrue(os_release(path)["supported_family"])
            Path(path).write_text('ID=ubuntu\nVERSION_ID="21.10"\n')
            self.assertFalse(os_release(path)["supported_family"])

    def test_invalid_identifiers(self):
        for name in ("../host", "host\nuser admin", "host\n", "a:b", "*", "a/b", ""):
            with self.assertRaises(ValueError):
                identifier(name)

    def test_portable_load_requires_no_external_binary(self):
        args, cwd, one_shot = command({"tool": "cpu-burn", "duration_seconds": 60, "threads": 1}, "/tools")
        self.assertEqual(["-m", "ocrun.cpu_burn", "--workers", "1"], args[1:])
        self.assertFalse(one_shot)

    def test_task_allowlist_and_resource_parameters(self):
        for task in ({"tool": "rm -rf /", "duration_seconds": 60}, {"tool": "stress", "duration_seconds": -1},
                     {"tool": "stress", "duration_seconds": True}, {"tool": "stress", "duration_seconds": 30, "threads": 0}):
            with self.assertRaises(ValueError):
                validate_task(task)

    def test_configs_render_without_installing_services(self):
        with tempfile.TemporaryDirectory() as directory:
            config = render_server(directory, "192.168.10.5", "192.168.10.0/24")
            self.assertEqual("admin", config["redis"]["username"])
            acl = Path(directory, "var/lib/ocrun-redis/users.acl").read_text()
            self.assertNotIn(config["redis"]["password"], acl)
            render_rsync(directory, config, [{"host_id": "node1", "upload": {"password": "test-only"}}])
            rsync = Path(directory, "etc/ocrun/rsyncd.conf").read_text()
            self.assertIn("[logs-node1]", rsync)
            self.assertIn("write only = yes", rsync)
            self.assertNotIn("test-only", rsync)

    def test_server_network_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                render_server(directory, "192.168.1.5", "10.0.0.0/24")

    def test_redis_response_and_protocol(self):
        self.assertEqual(["OK", 1, None], Redis.decode(io.BytesIO(b"*3\r\n+OK\r\n:1\r\n$-1\r\n")))
        self.assertEqual("a\nb", Redis.decode(io.BytesIO(b"$3\r\na\nb\r\n")))
        with self.assertRaises(RedisError):
            Redis.decode(io.BytesIO(b"$10\r\nx\r\n"))
        with self.assertRaises(RedisError):
            Redis.decode(io.BytesIO(b"-NOAUTH Authentication required\r\n"))

    def test_report_missing_values_and_separate_fans(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory, "metrics.jsonl")
            path.write_text('\n'.join([json.dumps({"system_watts": None, "fan_rpm": {"FAN1": 1000}}),
                                       json.dumps({"system_watts": 200, "fan_rpm": {"FAN1": 2000}}), "bad line"]), encoding="utf8")
            data = summarize(str(path))
            watts = next(row for row in data["metrics"] if row["metric"] == "system_watts")
            self.assertEqual(200, watts["mean"])
            self.assertEqual(0.5, watts["valid_ratio"])
            self.assertEqual(1, data["malformed_lines"])

    def test_archive_traversal_is_rejected_before_extraction(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory, "tools.tar.gz")
            with tarfile.open(archive, "w:gz") as bundle:
                member = tarfile.TarInfo("../outside")
                member.size = 1
                bundle.addfile(member, io.BytesIO(b"x"))
            with self.assertRaises(ValueError):
                unpack(str(archive), str(Path(directory, "out")), hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertFalse(Path(directory, "outside").exists())

    def test_archive_checksum_and_regular_file(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory, "tools.tar.gz")
            with tarfile.open(archive, "w:gz") as bundle:
                member = tarfile.TarInfo("bin/tool")
                member.size, member.mode = 2, 0o755
                bundle.addfile(member, io.BytesIO(b"ok"))
            with self.assertRaises(ValueError):
                unpack(str(archive), str(Path(directory, "out")), "0" * 64)
            unpack(str(archive), str(Path(directory, "out")), hashlib.sha256(archive.read_bytes()).hexdigest())
            self.assertEqual(b"ok", Path(directory, "out/bin/tool").read_bytes())


if __name__ == "__main__":
    unittest.main(verbosity=2)
