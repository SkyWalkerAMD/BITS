"""Cloud-only fault checks; no production services or hardware access."""
import hashlib
import json
import os
from pathlib import Path
import pwd
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from bits_core.results.control import inspect, listing, sample, check
from bits_core.results.data import Reader

MACHINE = "K6C-165_260168795800086"
BASE = MACHINE + "_PLUGIN-CLOUD_20260928-01"
REL = MACHINE + "/" + BASE


class ReaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="control-data-")
        self.root = Path(self.temporary.name)
        self.folder = self.root / MACHINE
        self.folder.mkdir()
        self.mon = self.folder / (BASE + ".mon")
        self.mon.write_bytes(b"# host\n# columns\nsckocp-v1,20260928_010001,Stress,100,24,4900,62,,456.5,,\n")
        frame = {"schema": "mon-sensors-sckocp-v1", "os": {"time": "20260928_010001", "task": "Stress"},
                 "reading_quality": "reported-validity-and-age-unknown",
                 "provider": {"schema": "sckocp-api-v1", "data": {"sockets": [{"temp_max_c": 62}]}}}
        self.frame = frame
        (self.folder / (BASE + ".mon.sckocp.jsonl")).write_text(json.dumps(frame) + "\n")
        (self.folder / (BASE + ".xlsx")).write_bytes(b"not-a-real-workbook-hash-only-test")
        self.value = {"schema": "mon-sensors-finish-receipt-v1", "case": "a" * 32,
                      "task_id": "PLUGIN-CLOUD", "task_time": "20260928-01", "version": "0.2.1",
                      "execution_result": "completed", "data_quality": "readings_reported_validity_unknown",
                      "report_result": "generated", "delivery_result": "data_verified", "artifacts": {}}
        for path in self.folder.iterdir():
            data = path.read_bytes()
            self.value["artifacts"][path.name] = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        self.receipt = self.folder / (BASE + ".finish.json")
        self.save()

    def save(self):
        self.receipt.write_text(json.dumps(self.value))

    def tearDown(self):
        self.temporary.cleanup()

    def test_current_verify_is_independent_of_hardware_and_execution(self):
        self.value["execution_result"] = "early_exit"
        self.save()
        with Reader(self.root) as reader:
            result = inspect(reader, REL + ".finish.json", True)
        self.assertTrue(result["verified_here"])
        self.assertEqual(result["execution"], "early_exit")
        self.assertEqual(result["hardware_result"], "not_assessed")
        self.assertEqual(result["receipt_trust"], "unsigned_unpinned")

    def test_show_does_not_claim_local_verification(self):
        with Reader(self.root) as reader:
            self.assertFalse(inspect(reader, REL + ".finish.json")["verified_here"])

    def test_legacy_receipt_keeps_quality_and_execution_unknown(self):
        for key in ("execution_result", "data_quality", "report_result", "delivery_result"):
            del self.value[key]
        self.save()
        with Reader(self.root) as reader:
            result = inspect(reader, REL + ".finish.json", True)
        self.assertEqual(result["execution"], "legacy_launch_status_only")
        self.assertEqual(result["data_quality"], "legacy_quality_not_recorded")

    def test_supplied_receipt_hash_and_wrong_hash(self):
        expected = hashlib.sha256(self.receipt.read_bytes()).hexdigest()
        with Reader(self.root) as reader:
            self.assertEqual(inspect(reader, REL + ".finish.json", True, expected)["receipt_trust"], "matches_supplied_sha256")
            with self.assertRaises(ValueError):
                inspect(reader, REL + ".finish.json", True, "0" * 64)

    def test_corrupt_data_and_missing_data_are_failures(self):
        self.mon.write_bytes(b"modified")
        with Reader(self.root) as reader:
            with self.assertRaises(ValueError):
                inspect(reader, REL + ".finish.json", True)
            self.mon.unlink()
            with self.assertRaises(FileNotFoundError):
                inspect(reader, REL + ".finish.json", True)

    def test_manifest_traversal_and_wrong_batch_and_empty_files_rejected(self):
        with Reader(self.root) as reader:
            for bad in ({"../secret": {"bytes": 1, "sha256": "0" * 64}}, {}, {"another.mon": {}}):
                self.value["artifacts"] = bad
                self.save()
                with self.assertRaises(ValueError):
                    inspect(reader, REL + ".finish.json", True)

    def test_symlink_files_directories_and_fifo_never_read(self):
        self.mon.unlink()
        self.mon.symlink_to("/etc/passwd")
        with Reader(self.root) as reader:
            with self.assertRaises(ValueError):
                reader.digest(REL + ".mon")
            self.mon.unlink()
            os.mkfifo(str(self.mon))
            with self.assertRaises(ValueError):
                reader.digest(REL + ".mon")
            (self.root / "link").symlink_to(str(self.folder))
            with self.assertRaises((ValueError, OSError)):
                reader.read("link/" + BASE + ".finish.json")
            with self.assertRaises(ValueError):
                reader.read("../etc/passwd")

    def test_live_sample_keeps_missing_null_and_original_detail(self):
        with Reader(self.root) as reader:
            result = sample(reader, REL + ".mon")
        self.assertIsNone(result["sample"]["vrm_temp_c"])
        self.assertIsNone(result["sample"]["system_power_w"])
        self.assertIsNone(result["sample"]["fan"])
        self.assertEqual(result["detail"], self.frame)
        self.assertEqual(result["quality"], "reported-validity-and-age-unknown")

    def test_partial_append_and_mismatched_sidecar(self):
        with self.mon.open("ab") as stream:
            stream.write(b"partial")
        self.frame["os"]["time"] = "20260928_020000"
        (self.folder / (BASE + ".mon.sckocp.jsonl")).write_text(json.dumps(self.frame) + "\n")
        with Reader(self.root) as reader:
            result = sample(reader, REL + ".mon")
        self.assertIsNone(result["detail"])
        self.assertEqual(result["sidecar_alignment"], "no_matching_sample_in_tail")

    def test_large_sparse_log_reads_only_tail(self):
        with self.mon.open("wb") as stream:
            stream.seek(1024 ** 3)
            stream.write(b"\nsckocp-v1,20260928_010001,Stress,100,24,4900,62,,456.5,,\n")
        with Reader(self.root) as reader:
            self.assertEqual(sample(reader, REL + ".mon")["sample"]["cpu_temp_c"], "62")

    def test_modified_during_hash_rejected(self):
        original = os.read
        changed = []
        def mutation(fd, count):
            data = original(fd, count)
            if not changed:
                changed.append(True)
                with self.mon.open("ab") as stream:
                    stream.write(b"changed\n")
            return data
        with Reader(self.root) as reader, mock.patch("bits_core.results.data.os.read", side_effect=mutation):
            with self.assertRaises(ValueError):
                reader.digest(REL + ".mon")

    def test_no_receipt_does_not_mean_running_or_success(self):
        self.receipt.unlink()
        with Reader(self.root) as reader:
            result = listing(reader, MACHINE)
        self.assertEqual(result["batches"][0]["record"], "no_receipt")

    def test_duplicate_keys_bad_schema_and_wrong_identity(self):
        with Reader(self.root) as reader:
            self.receipt.write_text('{"schema": "a", "schema": "b"}')
            with self.assertRaises(ValueError):
                inspect(reader, REL + ".finish.json")
            self.value["task_id"] = "OTHER"
            self.save()
            with self.assertRaises(ValueError):
                inspect(reader, REL + ".finish.json")
            self.value["schema"] = "v999"
            self.save()
            with self.assertRaises(ValueError):
                inspect(reader, REL + ".finish.json")

    def test_original_application_is_never_sourced(self):
        app = self.root / "old-app"
        app.mkdir()
        (app / "oc.env").write_text("touch /should-never-exist\n")
        result = check(str(app), str(self.root))
        self.assertFalse(result["original_files_match"])
        self.assertFalse(Path("/should-never-exist").exists())

    def test_v2_report_manifest_verified_and_tampering_rejected(self):
        self.value['schema'] = 'mon-sensors-finish-receipt-v2'
        self.value['detailed_report_schema'] = 'ocrun-acceptance-report-v1'
        for suffix, content in (('.report.html', b'<html>synthetic</html>'), ('.report.json', b'{}')):
            target = self.mon.with_suffix(suffix)
            target.write_bytes(content)
            self.value['artifacts'][target.name] = {'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}
        self.save()
        with Reader(self.root) as reader:
            result = inspect(reader, REL + '.finish.json', True)
            self.assertTrue(result['verified_here'])
            self.assertTrue(result['html_report_path'].endswith('.report.html'))
            self.mon.with_suffix('.report.html').write_text('changed')
            with self.assertRaises(ValueError):
                inspect(reader, REL + '.finish.json', True)
            self.value['detailed_report_schema'] = 'unknown'
            self.save()
            with self.assertRaises(ValueError):
                inspect(reader, REL + '.finish.json')

    def test_ordinary_user_can_read_0600_data_without_chmod(self):
        account = pwd.getpwnam("ocuser")
        for base, dirs, files in os.walk(str(self.root)):
            os.chown(base, account.pw_uid, account.pw_gid)
            os.chmod(base, 0o700)
            for name in files:
                path = os.path.join(base, name)
                os.chown(path, account.pw_uid, account.pw_gid)
                os.chmod(path, 0o600)
        completed = subprocess.run(["/usr/local/bin/mon-sensors-control", "verify", "--results-root", str(self.root),
                                    "--receipt", REL + ".finish.json", "--json"], stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, timeout=10,
                                   preexec_fn=lambda: (os.setgroups([]), os.setgid(account.pw_gid), os.setuid(account.pw_uid)))
        self.assertEqual(completed.returncode, 0, completed.stderr.decode())
        self.assertTrue(json.loads(completed.stdout.decode())["verified_here"])
        self.assertEqual(self.mon.stat().st_mode & 0o777, 0o600)


def main():
    if os.environ.get("GITHUB_ACTIONS") != "true" or sys.platform != "linux":
        raise SystemExit("Only run tests in authorized GitHub Actions Linux")
    subprocess.check_call(["useradd", "-m", "ocuser"])
    installer = str(ROOT / "control-dist/mon-sensors-control-0.3.0.run")
    original = Path("/home/ocuser/ocrun")
    original.mkdir()
    (original / "oc.env").write_text("# synthetic original; MUST remain unchanged\n")
    before = (original / "oc.env").read_bytes()
    def install(action, success=True):
        completed = subprocess.run(["bash", installer, action], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20)
        if (completed.returncode == 0) != success:
            raise RuntimeError(completed.stdout.decode() + completed.stderr.decode())
        return completed
    baseline = ROOT / '.control-baseline/control-dist/mon-sensors-control-0.1.0.run'
    subprocess.check_call(['bash', str(baseline), '--apply'])
    old_command = Path('/usr/local/bin/mon-sensors-control').read_bytes()
    install('--check')
    assert Path('/usr/local/bin/mon-sensors-control').read_bytes() == old_command
    install('--apply')
    install('--rollback')
    assert Path('/usr/local/bin/mon-sensors-control').read_bytes() == old_command
    install('--apply')
    install('--remove')
    install("--check")
    assert not Path("/usr/local/bin/mon-sensors-control").exists()
    install("--apply")
    install("--apply")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(ReaderTests)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    command = Path("/usr/local/bin/mon-sensors-control")
    content = command.read_bytes()
    command.write_bytes(content + b"# manual edit\n")
    install("--apply", False)
    install("--remove", False)
    command.write_bytes(content)
    extra = Path("/opt/mon-sensors-control/0.3.0/unknown.txt")
    extra.write_text("keep me")
    install("--remove", False)
    assert extra.read_text() == "keep me"
    extra.unlink()
    program = Path("/opt/mon-sensors-control/0.3.0/bits_core/results/control.py")
    program.chmod(0o666)
    install("--check", False)
    program.chmod(0o644)
    install("--remove")
    install("--remove")
    assert not command.exists() and (original / "oc.env").read_bytes() == before
    command.symlink_to("/etc/passwd")
    install("--apply", False)
    assert command.is_symlink()
    command.unlink()
    install("--apply")
    # No service, database, old shell or workload command has been invoked.
    record = {"status": "passed" if result.wasSuccessful() else "failed", "reader_tests": result.testsRun,
              "failures": len(result.failures), "errors": len(result.errors),
              "installation_scenarios": ["010_to_020_upgrade", "020_to_010_rollback_exact_launcher", "upgrade_read_only_preflight", "read_only_preflight", "install", "repeat_install", "modified_launcher_rejected",
                                         "unknown_payload_preserved", "writable_program_rejected", "remove", "repeat_remove",
                                         "symlink_command_rejected", "reinstall", "original_file_unchanged"],
              "python": sys.version, "os_release": Path("/etc/os-release").read_text(),
              "source_commit": os.environ["OCRUN_SOURCE_COMMIT"], "production_connections": False,
              "hardware_test": False}
    Path("/results/control.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    if not result.wasSuccessful():
        sys.exit(1)


if __name__ == "__main__":
    main()
