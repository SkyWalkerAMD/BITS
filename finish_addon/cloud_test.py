"""Cloud-only integration tests using real rsync, report wheels and collector supervision."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

SRC = Path("/src")
REPORT = "/usr/local/bin/mon-sensors-report"


def command(argv, good=True, timeout=90, **kwargs):
    result = subprocess.run([str(a) for a in argv], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, **kwargs)
    if good and result.returncode:
        raise AssertionError(result.stdout.decode("utf-8", "replace") + result.stderr.decode("utf-8", "replace"))
    return result


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def script(path, text):
    path.write_text(text, encoding="utf-8")
    path.chmod(0o700)


def sample(mon, count=4, unavailable=False):
    mon.write_text("#-主机名: cloud provider=sckocp-v1\n"
                   "#=类型,时间,当前系统任务,运行时长,负载,主频,CPU温度(C),VRM温度(C),CPU功耗(W),整机功耗(W),风扇转速\n",
                   encoding="utf-8")
    with mon.open("a", encoding="utf-8") as left, Path(str(mon) + ".sckocp.jsonl").open("w", encoding="utf-8") as right:
        for n in range(count):
            stamp = "20260927_1200{:02d}".format(n)
            left.write("sckocp-v1,{},Stress,{},1,{}\n".format(stamp, 100 + n,
                       ",,,,," if unavailable and n == 0 else "4900,55,,120,,"))
            payload = {"schema": "sckocp-mon-v1", "version": "1.2.0", "vendor": "GenuineIntel", "family": 6,
                       "interval_s": 1, "sockets": [{"id": 0, "tjmax_c": 100, "temp_max_c": 55,
                       "vid_v": 1.1, "core_mhz": 4900, "base_mhz": 2500, "pkg_w": 120}],
                       "cores": [{"cpu": 0, "socket": 0, "mhz": 4900, "temp_c": 55, "vid_v": 1.1,
                                  "c0_pct": 100, "c6_pct": 0}]}
            detail = {"schema": "mon-sensors-sckocp-v1", "os": {"time": stamp, "task": "Stress", "uptime_seconds": 100+n, "load1": 1},
                      "reading_quality": "reported-validity-and-age-unknown",
                      "provider": {"schema": "sckocp-api-v1", "status": "collection_failed" if unavailable and n == 0 else "ok",
                                   "data": None if unavailable and n == 0 else payload,
                                   "error": "Monitoring unavailable." if unavailable and n == 0 else None}}
            right.write(json.dumps(detail) + "\n")


class FinishChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(tempfile.mkdtemp(prefix="finish-ci-", dir="/root"))
        command(["/bin/bash", SRC / "report-dist/mon-sensors-report-py36-0.2.0.run"])
        cls.remote = cls.root / "remote"
        cls.remote.mkdir()
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            cls.port = sock.getsockname()[1]
        config = cls.root / "rsync.conf"
        config.write_text("pid file = {0}/rsync.pid\nlock file = {0}/rsync.lock\nlog file = {0}/rsync.log\n"
                          "use chroot = no\n[logs]\npath = {1}\nread only = no\nuid = root\ngid = root\n".
                          format(cls.root, cls.remote))
        cls.daemon = subprocess.Popen(["/usr/bin/rsync", "--daemon", "--no-detach", "--address=127.0.0.1",
                                       "--port=" + str(cls.port), "--config=" + str(config)])
        for unused in range(50):
            try:
                socket.create_connection(("127.0.0.1", cls.port), .1).close()
                break
            except OSError:
                time.sleep(.1)
        else:
            raise AssertionError("Loopback rsync daemon did not start")
        cls.native = cls.root / "synthetic-sckocp"
        payload = {"schema": "sckocp-mon-v1", "version": "1.2.0", "vendor": "GenuineIntel", "family": 6,
                   "interval_s": .05, "sockets": [{"id": 0, "tjmax_c": 100, "temp_max_c": 55,
                   "vid_v": 1.1, "core_mhz": 4900, "base_mhz": 2500, "pkg_w": 120}],
                   "cores": [{"cpu": 0, "socket": 0, "mhz": 4900, "temp_c": 55, "vid_v": 1.1,
                              "c0_pct": 100, "c6_pct": 0}]}
        script(cls.native, "#!" + sys.executable + "\nimport json,time\ntime.sleep(.05)\nprint(" + repr(json.dumps(payload)) + ")\n")
        shutil.copyfile(str(cls.native), '/usr/local/bin/sckocp')
        os.chmod('/usr/local/bin/sckocp', 0o700)

    @classmethod
    def tearDownClass(cls):
        cls.daemon.terminate()
        cls.daemon.wait(timeout=5)

    def setUp(self):
        self.work = Path(tempfile.mkdtemp(prefix="case-", dir=str(self.root)))
        self.app = self.work / "ocrun"
        (self.app / "py").mkdir(parents=True)
        source = SRC / "integrations/mon-sensors/upstream-0.9.24a"
        for name in ("mon-sensors", "oct", "ocb"):
            shutil.copyfile(str(source / name), str(self.app / name))
            (self.app / name).chmod(0o755)
        shutil.copyfile(str(source / "mon-analyse-log.py"), str(self.app / "py/mon-analyse-log.py"))
        artifact = json.loads((SRC / 'dist/manifest.json').read_text())['mon_sensors_installer']
        assert Path(artifact['file']).name == artifact['file']
        plugin = SRC / 'dist' / artifact['file']
        assert sha(plugin) == artifact['sha256']
        command(["/bin/bash", plugin, "--app", self.app, "--backend", "sckocp"])
        self.original = (self.app / "ocb").read_bytes()
        self.installer = SRC / "finish-dist/mon-sensors-finish-0.2.6.run"
        self.logs = self.work / "log"
        self.logs.mkdir()
        self.remote_node = "node-" + self.work.name
        self.target = "127.0.0.1:{}::logs/{}".format(self.port, self.remote_node)
        command(["/bin/bash", self.installer, "--app", self.app])
        self.entry = self.app / "mon-sensors-finish"
        module_path = self.app / "mon-sensors-finish.d"
        # Each fixture uses exactly the installed source. Reset the shared module
        # name so its own dependency is imported when a new app is constructed.
        sys.modules.pop("common", None)
        sys.modules.pop("security", None)
        module_spec = importlib.util.spec_from_file_location("finish_under_test", str(module_path / "finish.py"))
        self.module = importlib.util.module_from_spec(module_spec)
        module_spec.loader.exec_module(self.module)

    def tearDown(self):
        command([self.app / "mon-sensors-plugin", "--stop-app", self.app], good=False)

    def cli(self, *args, **kwargs):
        return command([self.entry] + list(args), **kwargs)

    def start(self, tag="TEST", count=4, ended=True, unavailable=False):
        mon = self.logs / ("cloud_serial_{}_batch.mon".format(tag))
        case = self.cli("begin", "--mon", mon, "--remote", self.target,
                        "--task-id", tag, "--task-time", "batch", "--print-id").stdout.decode().strip()
        self.cli("step", "--case", case, "--name", "stress", "--event", "start", "--runtime", "0")
        if count:
            sample(mon, count, unavailable)
        if ended:
            self.cli("step", "--case", case, "--name", "stress", "--event", "end")
        return case, mon

    def state(self, case):
        return self.module.load_case(self.app, case)

    def monitor(self, mon):
        child = subprocess.Popen([str(self.app / "mon-sensors-plugin"), ".15", str(mon),
                                  "--sample-window", ".05", "--timeout", "2", "--binary", str(self.native)],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for unused in range(100):
            if mon.exists() and len(mon.read_bytes().splitlines()) >= 4:
                return child
            if child.poll() is not None:
                raise AssertionError("Synthetic collector failed to start")
            time.sleep(.05)
        raise AssertionError("Synthetic collector did not write samples")

    def test_complete_real_report_rsync_receipt_and_idempotence(self):
        case, mon = self.start()
        self.cli("finish", "--case", case)
        state = self.state(case)
        self.assertEqual("complete", state["stage"])
        self.assertEqual(4, state["samples"]["rows"])
        outputs = self.module.paths(self.app, state) + [mon.with_suffix('.finish.json')]
        self.assertEqual(6, len(outputs))
        receipt = json.loads(mon.with_suffix('.finish.json').read_text())
        self.assertEqual('mon-sensors-finish-receipt-v2', receipt['schema'])
        before = [sha(p) for p in outputs]
        for path in outputs:
            self.assertEqual(sha(path), sha(self.remote / self.remote_node / path.name))
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
        self.cli("retry", "--case", case)
        self.assertEqual(before, [sha(p) for p in outputs])
        self.assertFalse((self.logs / ".mon-sensors-report-backups").exists())
        with self.assertRaises(ValueError):
            self.module.begin(self.app, str(mon), self.target, "TEST", "batch")

    def test_stop_exact_collector_and_no_further_writes(self):
        case, mon = self.start(count=0)
        child = self.monitor(mon)
        try:
            self.cli("finish", "--case", case)
            child.wait(timeout=5)
            before = sha(mon)
            time.sleep(.4)
            self.assertEqual(before, sha(mon))
            self.assertEqual("complete", self.state(case)["stage"])
        finally:
            if child.poll() is None:
                command([self.app / "mon-sensors-plugin", "--stop-app", self.app], good=False)
                child.wait(timeout=10)

    def test_other_log_collector_is_not_stopped(self):
        case, mon = self.start()
        other = self.logs / "other.mon"
        child = self.monitor(other)
        try:
            result = self.cli("finish", "--case", case, good=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIsNone(child.poll())
            self.assertIn("different", self.state(case)["error"])
        finally:
            command([self.app / "mon-sensors-plugin", "--stop-app", self.app])
            child.wait(timeout=10)

    def test_mismatched_rows_refused_and_no_report(self):
        case, mon = self.start()
        with Path(str(mon) + ".sckocp.jsonl").open("a") as stream:
            stream.write('{}\n')
        self.assertNotEqual(0, self.cli("finish", "--case", case, good=False).returncode)
        self.assertFalse(mon.with_suffix(".xlsx").exists())
        self.assertEqual("validating", self.state(case)["stage"])

    def test_invalid_provider_payload_refused(self):
        case, mon = self.start()
        sidecar = Path(str(mon) + ".sckocp.jsonl")
        records = sidecar.read_text().splitlines()
        detail = json.loads(records[0])
        detail["provider"]["data"]["cores"][0]["c0_pct"] = 1000
        records[0] = json.dumps(detail)
        sidecar.write_text("\n".join(records) + "\n")
        self.assertNotEqual(0, self.cli("finish", "--case", case, good=False).returncode)
        self.assertFalse(mon.with_suffix(".xlsx").exists())

    def test_unavailable_samples_preserved_as_gaps(self):
        case, mon = self.start(unavailable=True)
        self.cli("finish", "--case", case)
        self.assertEqual(1, self.state(case)["samples"]["unavailable_samples"])

    def test_paired_sensor_summary_mismatch_refused(self):
        case, mon = self.start()
        mon.write_bytes(mon.read_bytes().replace(b",4900,", b",4901,"))
        self.assertNotEqual(0, self.cli("finish", "--case", case, good=False).returncode)
        self.assertIn("summary differs", self.state(case)["error"])
        self.assertFalse(mon.with_suffix(".xlsx").exists())

    def test_malformed_csv_keeps_a_diagnostic_state(self):
        case, mon = self.start()
        header = mon.read_bytes().splitlines()[:2]
        mon.write_bytes(b"\n".join(header) + b'\n"unterminated\n')
        result = self.cli("finish", "--case", case, good=False)
        self.assertNotEqual(0, result.returncode)
        self.assertNotIn(b"Traceback", result.stderr)
        self.assertTrue(self.state(case)["error"])

    def test_report_failure_stops_collector_and_can_retry(self):
        case, mon = self.start(count=0)
        child = self.monitor(mon)
        original = self.module.run
        def failed(argv, *args):
            if str(argv[0]) == REPORT and "--check" not in argv:
                raise ValueError("Injected report failure")
            return original(argv, *args)
        try:
            with mock.patch.object(self.module, "run", side_effect=failed):
                with self.assertRaisesRegex(ValueError, "report failure"):
                    self.module.finish(self.app, self.state(case))
            child.wait(timeout=5)
            self.assertEqual("reporting", self.state(case)["stage"])
            before = sha(mon)
            self.cli("retry", "--case", case)
            self.assertEqual(before, sha(mon))
        finally:
            if child.poll() is None:
                command([self.app / "mon-sensors-plugin", "--stop-app", self.app], good=False)
                child.wait(timeout=10)

    def test_upload_failure_keeps_workbook_and_retry_does_not_regenerate(self):
        case, mon = self.start()
        original = self.module.transfer
        with mock.patch.object(self.module, "transfer", side_effect=ValueError("Injected offline receiver")), \
                mock.patch.object(self.module.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "offline receiver"):
                self.module.finish(self.app, self.state(case))
        workbook = mon.with_suffix(".xlsx")
        before = (sha(mon), sha(workbook), workbook.stat().st_ino)
        self.assertEqual("uploading_and_verifying", self.state(case)["stage"])
        self.assertNotEqual(0, self.cli("check", "--scheduler", good=False).returncode)
        self.cli("retry", "--case", case)
        self.assertEqual(before, (sha(mon), sha(workbook), workbook.stat().st_ino))
        self.assertEqual("complete", self.state(case)["stage"])

    def test_remote_readback_mismatch_is_failure(self):
        case, mon = self.start()
        original = self.module.run
        def corrupt(argv, *args):
            result = original(argv, *args)
            if "--checksum" in argv:
                (self.remote / self.remote_node / mon.name).write_bytes(b"corrupted remote content\n")
            return result
        with mock.patch.object(self.module, "run", side_effect=corrupt), mock.patch.object(self.module.time, "sleep"):
            with self.assertRaisesRegex(ValueError, "read-back mismatch"):
                self.module.finish(self.app, self.state(case))
        self.assertNotEqual("complete", self.state(case)["stage"])
        self.assertFalse(mon.with_suffix(".finish.json").exists())
        self.cli("retry", "--case", case)
        self.assertEqual("complete", self.state(case)["stage"])

    def test_source_change_after_sealing_refused(self):
        case, mon = self.start()
        with mock.patch.object(self.module, "transfer", side_effect=ValueError("offline")), mock.patch.object(self.module.time, "sleep"):
            with self.assertRaises(ValueError):
                self.module.finish(self.app, self.state(case))
        mon.write_bytes(mon.read_bytes().replace(b",4900,", b",4901,"))
        sidecar = Path(str(mon) + ".sckocp.jsonl")
        sidecar.write_text(sidecar.read_text().replace('"mhz": 4900', '"mhz": 4901'))
        self.assertNotEqual(0, self.cli("retry", "--case", case, good=False).returncode)
        self.assertIn("changed", self.state(case)["error"])

    def test_receipt_failure_remains_pending_and_retry_reuses_workbook(self):
        case, mon = self.start()
        original = self.module.transfer
        def failed(rsync, state, files, expected, stage):
            if Path(files[0]).name.endswith(".finish.json"):
                raise ValueError("Injected receipt failure")
            return original(rsync, state, files, expected, stage)
        with mock.patch.object(self.module, "transfer", side_effect=failed):
            with self.assertRaisesRegex(ValueError, "receipt failure"):
                self.module.finish(self.app, self.state(case))
        self.assertEqual("publishing_receipt", self.state(case)["stage"])
        before = sha(mon.with_suffix(".xlsx"))
        self.cli("retry", "--case", case)
        self.assertEqual(before, sha(mon.with_suffix(".xlsx")))
        self.assertEqual("complete", self.state(case)["stage"])

    def test_concurrent_finalizer_refused_without_changing_state(self):
        case, mon = self.start()
        record = self.app / ".mon-sensors-finish" / (case + ".json")
        before = sha(record)
        with self.module.lock(record.parent / "operation.lock"):
            self.assertNotEqual(0, self.cli("finish", "--case", case, good=False).returncode)
        self.assertEqual(before, sha(record))
        self.assertFalse(mon.with_suffix(".xlsx").exists())

    def test_explicit_interrupted_recovery(self):
        case, mon = self.start(ended=False)
        self.assertNotEqual(0, self.cli("retry", "--case", case, good=False).returncode)
        self.cli("recover", "--case", case, "--interrupted")
        self.assertEqual("interrupted", self.state(case)["outcome"])
        self.assertEqual("complete", self.state(case)["stage"])

    def test_unrecoverable_record_closes_without_deleting_or_claiming_success(self):
        case, mon = self.start()
        sidecar = Path(str(mon) + ".sckocp.jsonl")
        sidecar.write_bytes(b"invalid json\n")
        self.assertNotEqual(0, self.cli("finish", "--case", case, good=False).returncode)
        before = (sha(mon), sha(sidecar))
        self.cli("close-incomplete", "--case", case, "--reason", "Synthetic damaged log retained for review")
        self.assertEqual("closed_incomplete", self.state(case)["stage"])
        self.assertEqual(before, (sha(mon), sha(sidecar)))
        self.assertFalse(mon.with_suffix(".finish.json").exists())
        self.cli("check", "--scheduler")
        self.assertNotEqual(0, self.cli("retry", "--case", case, good=False).returncode)
        other, unused = self.start(tag="NEXT")
        self.assertNotEqual(case, other)

    def test_close_incomplete_refuses_an_active_load(self):
        case, mon = self.start()
        load = self.work / "stress"
        shutil.copyfile("/usr/bin/sleep", str(load))
        load.chmod(0o700)
        child = subprocess.Popen([str(load), "30"])
        try:
            result = self.cli("close-incomplete", "--case", case, "--reason", "not idle", good=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIsNone(child.poll())
            self.assertNotEqual("closed_incomplete", self.state(case)["stage"])
        finally:
            child.terminate()
            child.wait(timeout=5)

    def test_active_load_prevents_finalization_without_killing_it(self):
        case, mon = self.start()
        load = self.work / "stress"
        shutil.copyfile("/usr/bin/sleep", str(load))
        load.chmod(0o700)
        child = subprocess.Popen([str(load), "30"])
        try:
            result = self.cli("finish", "--case", case, good=False)
            self.assertNotEqual(0, result.returncode)
            self.assertIsNone(child.poll())
            self.assertIn("Load processes", self.state(case)["error"])
        finally:
            child.terminate()
            child.wait(timeout=5)

    def test_hardlink_and_symlink_data_refused(self):
        case, mon = self.start()
        os.link(str(mon), str(self.work / "duplicate"))
        self.assertNotEqual(0, self.cli("finish", "--case", case, good=False).returncode)
        (self.work / "duplicate").unlink()
        mon.with_suffix(".xlsx").symlink_to(self.work / "victim")
        self.assertNotEqual(0, self.cli("retry", "--case", case, good=False).returncode)
        self.assertFalse((self.work / "victim").exists())

    def test_path_and_destination_injection_refused(self):
        for remote in ("--bad", "host::logs/../other", "host::other/node", "host;touch::logs/node"):
            with self.assertRaises(ValueError):
                self.module.remote_parts(remote)
        for name in ("../file", "-option", "file\nname", "a/b"):
            with self.assertRaises(ValueError):
                self.module.safe_name(name)

    def test_install_check_idempotence_detach_and_restore(self):
        attached = (self.app / "ocb").read_bytes()
        result = command(["/bin/bash", self.installer, "--app", self.app, "--check"])
        self.assertTrue(json.loads(result.stdout.decode())["check"])
        self.assertEqual(attached, (self.app / "ocb").read_bytes())
        command(["/bin/bash", self.installer, "--app", self.app])
        command(["/bin/bash", self.installer, "--app", self.app, "--detach"])
        self.assertEqual(self.original, (self.app / "ocb").read_bytes())
        command(["/bin/bash", self.installer, "--app", self.app])
        self.assertEqual(attached, (self.app / "ocb").read_bytes())

    def test_modified_scheduler_refuses_install(self):
        with (self.app / "ocb").open("a") as stream:
            stream.write("# local edit\n")
        result = command(["/bin/bash", self.installer, "--app", self.app, "--check"], good=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b"modified", result.stderr)

    def test_corrupt_archive_refused(self):
        corrupt = self.work / "corrupt.run"
        corrupt.write_bytes(self.installer.read_bytes() + b"bad")
        result = command(["/bin/bash", corrupt, "--app", self.app, "--check"], good=False)
        self.assertNotEqual(0, result.returncode)
        self.assertIn(b"payload length mismatch", result.stderr)

if __name__ == "__main__":
    if os.environ.get("GITHUB_ACTIONS") != "true" or os.geteuid() != 0 or sys.platform != "linux":
        raise SystemExit("Run only in the disposable cloud Linux container")
    os.umask(0o077)
    sys.path.insert(0, str(SRC / 'finish_addon'))
    import reliability_test
    import report_test
    reliability_test.FinishChecks = FinishChecks
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(FinishChecks)
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(reliability_test.make_checks(FinishChecks)))
    suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(report_test.make_checks(FinishChecks)))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    report = {"status": "passed" if result.wasSuccessful() else "failed", "tests_run": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors), "python": sys.version,
              "os_release": Path("/etc/os-release").read_text(), "network": "none except loopback rsync",
              "hardware": "synthetic provider, no real activation or stress workload"}
    path = Path("/results/finish-addon.json")
    path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    path.chmod(0o644)
    print(json.dumps(report))
    sys.exit(not result.wasSuccessful())
