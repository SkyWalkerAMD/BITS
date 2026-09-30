import json
import io
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest import mock

import fakeredis

from ocrun.admin import main as admin_main, status_records
from ocrun.common import exclusive_lock, read_json, write_json
from ocrun.dashboard import metric_tail, write_dashboard, write_fleet_csv
from ocrun.fleet import FleetStore, batch_summary, batch_tick, run_batch
from ocrun.queue import Queue


class Backend:
    def __init__(self):
        self.client = fakeredis.FakeRedis(decode_responses=True)
        self.commands = []

    def execute(self, *args):
        self.commands.append(args)
        return self.client.execute_command(*args)


class QueueManagementTests(unittest.TestCase):
    def setUp(self):
        self.backend = Backend()
        self.queue = Queue(self.backend, "node1")

    def enqueue(self, task_id):
        self.queue.enqueue({"id": task_id, "tool": "cpu-burn", "duration_seconds": 10})

    def test_cancel_pending_preserves_record_and_fifo(self):
        for task_id in ("first", "cancel-me", "third"):
            self.enqueue(task_id)
        self.assertEqual("cancelled", self.queue.cancel("cancel-me"))
        self.assertEqual("cancelled", self.queue.get("cancel-me")["status"])
        first = self.queue.claim("owner")
        self.assertEqual("first", first["id"])
        self.queue.finish(first, "completed", {})
        self.assertEqual("third", self.queue.claim("owner2")["id"])

    def test_cancel_claim_race_is_always_cancelled_or_requested(self):
        for index in range(12):
            queue = Queue(self.backend, "race" + str(index))
            queue.enqueue({"id": "task", "tool": "cpu-burn", "duration_seconds": 10})
            barrier = threading.Barrier(2)
            errors = []
            def run(operation):
                try:
                    barrier.wait()
                    operation()
                except Exception as error:
                    errors.append(error)
            threads = [threading.Thread(target=run, args=(lambda: queue.claim("owner"),)),
                       threading.Thread(target=run, args=(lambda: queue.cancel("task"),))]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual([], errors)
            task = queue.get("task")
            self.assertTrue(task["status"] == "cancelled" or
                            (task["status"] == "running" and queue.cancelled("task")))

    def test_terminal_cancel_does_not_cancel_next_task(self):
        self.enqueue("first")
        self.enqueue("next")
        first = self.queue.claim("owner")
        self.queue.finish(first, "completed", {})
        self.queue.claim("owner2")
        self.assertEqual("already_terminal", self.queue.cancel("first"))
        self.assertFalse(self.queue.cancelled("next"))

    def test_pause_prevents_claim_without_stopping_running_task(self):
        self.enqueue("first")
        self.enqueue("second")
        task = self.queue.claim("owner")
        self.queue.pause()
        self.assertTrue(self.queue.is_paused())
        self.assertEqual("first", self.queue.running()["id"])
        self.queue.finish(task, "completed", {})
        self.assertIsNone(self.queue.claim("blocked"))
        self.queue.resume()
        self.assertEqual("second", self.queue.claim("allowed")["id"])

    def test_history_pages_bound_reads_and_expose_legacy_migration(self):
        for index in range(70):
            self.enqueue("job{:03d}".format(index))
        self.backend.commands = []
        page = self.queue.task_page(limit=7)
        self.assertEqual(7, len(page["tasks"]))
        self.assertEqual(7, page["next_offset"])
        self.assertFalse(page["history_incomplete"])
        self.assertEqual(7, sum(args[0] == "HGET" for args in self.backend.commands))
        self.assertFalse(any(args[0] in ("HGETALL", "HSCAN") for args in self.backend.commands))
        self.backend.execute("HSET", self.queue.key("jobs"), "legacy", json.dumps(
            {"id": "legacy", "created_at": 1, "status": "completed"}))
        self.assertTrue(self.queue.task_page()["history_incomplete"])
        self.assertEqual(71, self.queue.reindex_history())
        self.assertFalse(self.queue.task_page()["history_incomplete"])
        self.assertEqual("legacy", self.queue.task_page(offset=70)["tasks"][0]["id"])

    def test_filter_cannot_trigger_unbounded_scans(self):
        for index in range(30):
            self.enqueue("job" + str(index))
        page = self.queue.task_page(limit=5, status="failed")
        self.assertEqual([], page["tasks"])
        self.assertEqual(5, page["next_offset"])
        self.assertTrue(page["filter_applies_to_page"])
        for options in ({"limit": 0}, {"limit": 201}, {"offset": -1}):
            with self.assertRaises(ValueError):
                self.queue.task_page(**options)


class FleetTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.config = {"data_root": self.temporary.name,
                       "enrollments": os.path.join(self.temporary.name, "enrollments")}
        self.backend = Backend()
        self.store = FleetStore(self.config)
        self.hosts = ["node1", "node2", "node3"]
        for host in self.hosts:
            write_json(os.path.join(self.config["enrollments"], host + ".json"), {"host_id": host})
            Queue(self.backend, host).heartbeat({"task_id": None})
        self.template = {"tool": "cpu-burn", "duration_seconds": 10, "threads": 1}

    def create(self, parallel=1, stagger=0):
        return self.store.create_batch("batch1", self.hosts, self.template, parallel, stagger)

    def complete(self, host):
        queue = Queue(self.backend, host)
        task = queue.claim("owner")
        queue.finish(task, "completed", {"verdict": "insufficient_data"})

    def test_group_and_template_snapshots_are_validated(self):
        self.store.save_group("rack1", ["node1", "node1", "node2"])
        self.assertEqual(["node1", "node2"], self.store.group("rack1"))
        self.store.save_template("burn", self.template)
        record = self.store.create_batch("batch1", self.store.group("rack1"), self.store.template("burn"))
        self.store.save_template("burn", dict(self.template, duration_seconds=20))
        self.assertEqual(10, record["task"]["duration_seconds"])
        with self.assertRaises(ValueError):
            self.store.save_group("bad", ["not-enrolled"])
        with self.assertRaises(ValueError):
            self.store.save_template("bad", dict(self.template, command="anything"))
        with self.assertRaises(ValueError):
            self.create()

    def test_cap_counts_pending_and_running_and_releases_only_terminal(self):
        self.create(parallel=1)
        first = batch_tick(self.store, self.backend, "batch1", now=100)
        self.assertEqual({"pending": 1, "waiting": 2}, batch_summary(first)["counts"])
        queue = Queue(self.backend, "node1")
        task = queue.claim("owner")
        second = batch_tick(self.store, self.backend, "batch1", now=101)
        self.assertEqual({"running": 1, "waiting": 2}, batch_summary(second)["counts"])
        self.backend.execute("DEL", queue.key("heartbeat"))
        self.assertEqual("waiting", batch_tick(self.store, self.backend, "batch1", now=10000)["members"][1]["state"])
        queue.finish(task, "interrupted", {"reason": "Confirmed stopped"})
        third = batch_tick(self.store, self.backend, "batch1", now=10001)
        self.assertEqual("pending", third["members"][1]["state"])
        self.assertEqual("interrupted", third["members"][0]["state"])

    def test_stagger_delays_additional_dispatch(self):
        self.create(parallel=3, stagger=10)
        first = batch_tick(self.store, self.backend, "batch1", now=100)
        self.assertEqual(1, batch_summary(first)["counts"]["pending"])
        second = batch_tick(self.store, self.backend, "batch1", now=109)
        self.assertEqual(1, batch_summary(second)["counts"]["pending"])
        third = batch_tick(self.store, self.backend, "batch1", now=110)
        self.assertEqual(2, batch_summary(third)["counts"]["pending"])

    def test_offline_paused_and_busy_hosts_are_not_dispatched(self):
        self.create(parallel=3)
        self.backend.execute("DEL", Queue(self.backend, "node1").key("heartbeat"))
        Queue(self.backend, "node2").pause()
        Queue(self.backend, "node3").enqueue(dict(self.template, id="manual"))
        record = batch_tick(self.store, self.backend, "batch1")
        self.assertTrue(all(member["state"] == "waiting" for member in record["members"]))

    def test_running_pointer_without_payload_still_reserves_device(self):
        self.store.create_batch("batch1", ["node1"], self.template)
        queue = Queue(self.backend, "node1")
        self.backend.execute("SET", queue.key("running"), "missing-record")
        record = batch_tick(self.store, self.backend, "batch1")
        self.assertEqual("waiting", record["members"][0]["state"])
        self.assertEqual(0, self.backend.execute("LLEN", queue.key("pending")))

    def test_lost_enqueue_response_resumes_same_id_without_duplicate(self):
        initial = self.create(parallel=1)
        original = Queue.enqueue
        def lost_response(queue, task):
            original(queue, task)
            raise ConnectionError("response lost")
        with mock.patch.object(Queue, "enqueue", new=lost_response):
            with self.assertRaises(ConnectionError):
                batch_tick(self.store, self.backend, "batch1", now=100)
        self.assertEqual("submitting", self.store.batch("batch1")["members"][0]["state"])
        resumed = batch_tick(self.store, self.backend, "batch1", now=101)
        queue = Queue(self.backend, "node1")
        self.assertEqual("pending", resumed["members"][0]["state"])
        self.assertEqual(1, self.backend.execute("LLEN", queue.key("pending")))
        self.assertEqual(initial["members"][0]["task_id"], queue.tasks()[0]["id"])

    def test_failed_send_without_acceptance_retries_reserved_slot(self):
        self.create()
        with mock.patch.object(Queue, "enqueue", side_effect=ConnectionError("offline")):
            with self.assertRaises(ConnectionError):
                batch_tick(self.store, self.backend, "batch1")
        resumed = batch_tick(self.store, self.backend, "batch1")
        self.assertEqual({"pending": 1, "waiting": 2}, batch_summary(resumed)["counts"])

    def test_missing_previously_running_task_never_restarts_it(self):
        self.create()
        record = batch_tick(self.store, self.backend, "batch1")
        queue = Queue(self.backend, "node1")
        self.backend.execute("DEL", queue.key("jobs"), queue.key("pending"))
        with self.assertRaises(RuntimeError):
            batch_tick(self.store, self.backend, "batch1")
        self.assertIsNone(queue.get(record["members"][0]["task_id"]))

    def test_cancel_batch_handles_waiting_pending_and_running(self):
        self.create(parallel=2)
        initial = batch_tick(self.store, self.backend, "batch1")
        first_queue = Queue(self.backend, "node1")
        task = first_queue.claim("owner")
        self.store.request_cancel("batch1")
        cancelled = batch_tick(self.store, self.backend, "batch1")
        self.assertEqual("cancelling", cancelled["status"])
        self.assertTrue(first_queue.cancelled(task["id"]))
        self.assertEqual("cancelled", cancelled["members"][1]["state"])
        self.assertIsNone(Queue(self.backend, "node3").get(initial["members"][2]["task_id"]))
        first_queue.finish(task, "cancelled", {})
        self.assertEqual("cancelled", batch_tick(self.store, self.backend, "batch1")["status"])

    def test_cancel_request_is_durable_even_while_controller_holds_plan_lock(self):
        self.create()
        with self.store.locked_batch("batch1"):
            self.assertTrue(self.store.request_cancel("batch1")["cancel_requested"])
        record = batch_tick(self.store, self.backend, "batch1")
        self.assertEqual("cancelled", record["status"])
        self.assertTrue(all(member["state"] == "cancelled" for member in record["members"]))

    def test_cancellation_during_enqueue_cancels_in_flight_task(self):
        self.create(parallel=3)
        original = Queue.enqueue
        def cancel_while_sending(queue, task):
            accepted = original(queue, task)
            self.store.request_cancel("batch1")
            return accepted
        with mock.patch.object(Queue, "enqueue", new=cancel_while_sending):
            record = batch_tick(self.store, self.backend, "batch1")
        self.assertEqual("cancelled", record["members"][0]["state"])
        self.assertTrue(record["cancel_requested"])
        self.assertEqual(0, self.backend.execute("LLEN", Queue(self.backend, "node1").key("pending")))
        self.assertEqual(0, self.backend.execute("LLEN", Queue(self.backend, "node2").key("pending")))
        self.assertEqual("cancelled", batch_tick(self.store, self.backend, "batch1")["status"])

    def test_controller_exclusivity_and_completed_batches_are_not_repeated(self):
        self.create(parallel=3)
        with exclusive_lock(os.path.join(self.store.root, "controller.lock")):
            with self.assertRaises(RuntimeError):
                next(run_batch(self.store, self.backend, "batch1", once=True))
        next(run_batch(self.store, self.backend, "batch1", once=True))
        for host in self.hosts:
            self.complete(host)
        self.assertEqual("finished", batch_tick(self.store, self.backend, "batch1")["status"])
        self.assertEqual("finished", batch_tick(self.store, self.backend, "batch1")["status"])
        self.assertTrue(all(Queue(self.backend, host).running() is None for host in self.hosts))

    def test_admin_resolve_and_verify_commands_dispatch_without_fleet_arguments(self):
        queue = Queue(self.backend, "node1")
        queue.enqueue(dict(self.template, id="manual"))
        queue.claim("owner")
        config = dict(self.config, redis={})
        with mock.patch("ocrun.admin.read_json", return_value=config), \
             mock.patch("ocrun.admin.Redis", return_value=self.backend), \
             mock.patch("sys.stdout", new_callable=io.StringIO), \
             mock.patch("sys.argv", ["ocrun-admin", "resolve-interrupted", "node1", "manual", "--confirmed-stopped"]):
            admin_main()
        self.assertEqual("interrupted", queue.get("manual")["status"])
        self.assertEqual("insufficient_data", queue.get("manual")["result"]["verdict"])
        with mock.patch("ocrun.admin.read_json", return_value=config), \
             mock.patch("ocrun.admin.Redis", return_value=self.backend), \
             mock.patch("ocrun.admin.verify_server", return_value={"verified": 1, "pending": 0}) as verify, \
             mock.patch("sys.stdout", new_callable=io.StringIO) as output, \
             mock.patch("sys.argv", ["ocrun-admin", "verify-logs"]):
            admin_main()
        verify.assert_called_once_with(config, self.backend)
        self.assertEqual(1, json.loads(output.getvalue())["verified"])


class DashboardTests(unittest.TestCase):
    def test_snapshot_escapes_untrusted_logs_and_omits_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            logs = Path(directory, "logs")
            job = logs / "node1" / "job1"
            job.mkdir(parents=True)
            (job / "metrics.jsonl").write_text(json.dumps({"cpu_temperature_c": 50, "timestamp": "now"}) + "\n")
            records = [{"host": "node1", "online": True, "paused": False,
                        "heartbeat": {"password": "not-for-dashboard", "metrics_summary": {
                            "timestamp": "heartbeat-sample-time", "cpu_temperature_c": 52}}, "tasks": [
                            {"id": "job1", "tool": "cpu-burn", "status": "failed", "result": {
                                "reason": '<script>alert("x")</script>', "verdict": "failed"}}]}]
            output = Path(directory, "dashboard.html")
            write_dashboard(str(output), records, str(logs))
            document = output.read_text(encoding="utf-8")
            self.assertNotIn("<script>", document)
            self.assertIn("&lt;script&gt;", document)
            self.assertNotIn("not-for-dashboard", document)
            self.assertIn("Content-Security-Policy", document)
            self.assertIn("50.0", document)
            self.assertIn("heartbeat-sample-time", document)
            self.assertIn("心跳 CPU 温度", document)
            with self.assertRaises(FileExistsError):
                write_dashboard(str(output), records, str(logs))

    def test_tail_is_bounded_and_csv_formula_is_escaped(self):
        with tempfile.TemporaryDirectory() as directory:
            task_dir = Path(directory, "node1", "job1")
            task_dir.mkdir(parents=True)
            with (task_dir / "metrics.jsonl").open("w") as stream:
                for value in range(100):
                    stream.write(json.dumps({"cpu_temperature_c": value}) + "\n")
            tail = metric_tail(directory, "node1", "job1", max_bytes=200, max_samples=3)
            self.assertEqual([97, 98, 99], [sample["cpu_temperature_c"] for sample in tail])
            output = Path(directory, "report.csv")
            write_fleet_csv(str(output), [{"host": "node1", "online": False, "tasks": [
                {"id": "job1", "result": {"reason": "=1+1"}}]}])
            self.assertIn("'=1+1", output.read_text(encoding="utf-8-sig"))

    def test_status_contains_active_task_even_before_history_migration(self):
        backend = Backend()
        queue = Queue(backend, "node1")
        queue.enqueue({"id": "job1", "tool": "cpu-burn", "duration_seconds": 10})
        queue.claim("owner")
        backend.execute("DEL", queue.key("history"))
        record = status_records(backend, ["node1"])[0]
        self.assertEqual("job1", record["running"]["id"])
        self.assertEqual([], record["tasks"])
        self.assertTrue(record["history_incomplete"])
