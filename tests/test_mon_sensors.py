"""Compatibility contract for the original command; run only in cloud Linux."""
import copy
import io
import json
import os
from pathlib import Path
import signal
import stat
import tempfile
import unittest
from unittest import mock

import sckocp_api
from mon_sensors_plugin import collector as mon_sensors
from sckocp_api import provider as sckocp
from test_sckocp import payload, metric
from test_sckocp_original_api import original_payload


CONTEXT = {"time": "20260922_123456", "task": "P95-AVX-M1",
           "uptime_seconds": 124.2, "load1": 6.25}


def envelope(data=None):
    return sckocp._envelope("ok", payload() if data is None else data)


class MonSensorsRowsTests(unittest.TestCase):
    def test_original_parser_layout_and_separate_frequency_semantics(self):
        sample = envelope()
        row = mon_sensors.make_row(sample, CONTEXT)
        self.assertEqual(["sckocp-v2", "20260922_123456", "P95-AVX-M1", "124.2", "6.25",
                          "2400", "61.5", "", "154.3", "1100", ""], row)
        # The original parser splits on commas and appends one E-core column.
        old_parser_fields = ",".join(row).strip().split(",")
        old_parser_fields.append("")
        self.assertEqual(12, len(old_parser_fields))
        self.assertNotIn("Noa", old_parser_fields)
        self.assertEqual("6.25", old_parser_fields[4], "C0 cannot replace OS load")

    def test_partial_or_stale_sockets_do_not_make_plausible_totals(self):
        for field, column in (("temperature_c", 6), ("package_watts", 8)):
            for unavailable in (True, False):
                with self.subTest(field=field, unavailable=unavailable):
                    sample = payload()
                    second = copy.deepcopy(sample["sockets"][0])
                    second["id"] = 1
                    descriptor = second["metrics"][field]
                    second["metrics"][field] = (metric(descriptor["unit"]) if unavailable
                                                 else metric(descriptor["unit"], 999, age=31))
                    sample["sockets"].append(second)
                    row = mon_sensors.make_row(envelope(sample), CONTEXT)
                    self.assertEqual("", row[column])

    def test_tctl_is_not_a_physical_temperature_fallback(self):
        sample = payload()
        sample["sockets"][0]["metrics"]["temperature_c"] = metric("C")
        sample["sockets"][0]["metrics"]["control_temperature_c"] = metric("C", 95)
        self.assertEqual("", mon_sensors.make_row(envelope(sample), CONTEXT)[6])

    def test_all_cores_required_for_mean_and_stale_psu_is_blank(self):
        sample = payload()
        second = copy.deepcopy(sample["cores"][0])
        second["cpu"] = 2
        second["metrics"]["active_mhz"] = metric("MHz", 3600)
        sample["cores"].append(second)
        self.assertEqual("3000", mon_sensors.make_row(envelope(sample), CONTEXT)[5])
        second["metrics"]["active_mhz"] = metric("MHz")
        sample["system"]["metrics"]["psu_input_watts"]["age_s"] = 29.9
        row = mon_sensors.make_row(envelope(sample), CONTEXT, elapsed=.2)
        self.assertEqual("", row[5])
        self.assertEqual("", row[9])

    def test_true_zero_is_retained_and_failure_does_not_reuse_data(self):
        sample = payload()
        sample["sockets"][0]["metrics"]["package_watts"] = metric("W", 0)
        self.assertEqual("0", mon_sensors.make_row(envelope(sample), CONTEXT)[8])
        failed = envelope(sample)
        failed["status"] = "license_denied"
        self.assertEqual(["", "", "", "", "", ""], mon_sensors.make_row(failed, CONTEXT)[5:])

    def test_aggregate_overflow_does_not_escape_as_infinity(self):
        sample = payload()
        sample["sockets"][0]["metrics"]["package_watts"] = metric("W", 1e308)
        sample["sockets"].append(copy.deepcopy(sample["sockets"][0]))
        self.assertEqual("", mon_sensors.make_row(envelope(sample), CONTEXT)[8])

    def test_original_task_names_are_detected_without_executing_oc_env(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.assertEqual("IDIE", mon_sensors.detect_task(directory))
            (root / "100001").mkdir()
            (root / "100001" / "cmdline").write_bytes(b"/root/ocrun/bin/p95-fma3_m2/mprime\x00-t")
            self.assertEqual("P95-FMA3-M2", mon_sensors.detect_task(directory))
            (root / "100002").mkdir()
            (root / "100002" / "cmdline").write_bytes(b"/root/ocrun/bin/stress/stress\x00--cpu\x004")
            self.assertEqual("Multi-Load", mon_sensors.detect_task(directory))

    def test_versioned_imports_and_shared_p95_keep_task_labels(self):
        paths = [(b'/var/lib/ocrun-workloads/mlc-3.13/mlc\0-e\0-r', 'MLC'),
                 (b'/var/lib/ocrun-workloads/cpu2017-1.0.5/bin/runcpu\0all', 'SPEC2017'),
                 (b'/opt/ocrun-workloads/0.1.0/mprime/mprime\0-t\0-W/root/case/step-2-p95-no_m4/', 'P95-M4'),
                 (b'/opt/ocrun-workloads/0.1.0/cyclictest/cyclictest\0--duration=60', 'Cyclictest'),
                 (b'/usr/bin/perl\0/opt/ocrun-workloads/0.1.0/unixbench/Run', 'UnixBench')]
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory) / '100001'
            proc.mkdir()
            for args, label in paths:
                (proc / 'cmdline').write_bytes(args)
                self.assertEqual(label, mon_sensors.detect_task(directory))


class MonSensorsCommandTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.log = Path(self.directory.name) / "task.mon"

    def invoke(self, args, result=None):
        output, error = io.StringIO(), io.StringIO()
        with mock.patch.object(sckocp_api, "collect", return_value=result or envelope()) as collect, \
                mock.patch.object(mon_sensors, "os_context", return_value=CONTEXT), \
                mock.patch("sys.stdout", output), mock.patch("sys.stderr", error):
            code = mon_sensors.main(args + ["--format", "v2", "--details-interval", "0"])
        return code, output.getvalue(), error.getvalue(), collect

    def invoke_sequence(self, args, results, sleep_effect=None):
        output, error = io.StringIO(), io.StringIO()
        with mock.patch.object(sckocp_api, "collect", side_effect=results) as collect, \
                mock.patch.object(mon_sensors, "os_context", return_value=CONTEXT), \
                mock.patch.object(mon_sensors.time, "sleep", side_effect=sleep_effect) as sleep, \
                mock.patch("sys.stdout", output), mock.patch("sys.stderr", error):
            code = mon_sensors.main(args + ["--format", "v2", "--details-interval", "0"])
        return code, output.getvalue(), error.getvalue(), collect, sleep

    def test_log_has_exactly_two_headers_append_and_complete_sidecar(self):
        args = ["2", str(self.log), "--once", "--binary", "/safe/sckocp"]
        for index in range(2):
            code, output, error, collect = self.invoke(args)
            self.assertEqual(0, code)
            self.assertEqual("", output)
            collect.assert_called_once_with("/safe/sckocp", 1, 20, native_format="v2")
        lines = self.log.read_text(encoding="utf-8").splitlines()
        self.assertEqual(4, len(lines))
        self.assertIn("provider=sckocp-v2", lines[0])
        self.assertIn("frequency=physical-core-active-mean-MHz", lines[0])
        self.assertEqual(mon_sensors.HEADERS, lines[1].split(","))
        self.assertTrue(all(len(line.split(",")) == 11 for line in lines[2:]))
        sidecar = Path(str(self.log) + ".sckocp.jsonl")
        frames = [json.loads(line) for line in sidecar.read_text(encoding="utf-8").splitlines()]
        self.assertEqual(2, len(frames))
        self.assertEqual(payload(), frames[0]["provider"]["data"])
        self.assertEqual(CONTEXT, frames[0]["os"])
        self.assertEqual(0o600, stat.S_IMODE(self.log.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(sidecar.stat().st_mode))

    def test_provider_failure_is_logged_once_then_returns_failure(self):
        failed = sckocp._envelope("license_denied")
        failed["error"] = "UNTRUSTED DIAGNOSTIC SECRET"
        code, output, error, collect = self.invoke([".05", str(self.log)], failed)
        self.assertEqual(1, code)
        collect.assert_called_once()
        row = self.log.read_text(encoding="utf-8").splitlines()[2].split(",")
        self.assertEqual(["", "", "", "", "", ""], row[5:])
        self.assertNotIn("SECRET", error)
        self.assertIn("did not authorize", error)
        frame = json.loads(Path(str(self.log) + ".sckocp.jsonl").read_text(encoding="utf-8"))
        self.assertEqual("license_denied", frame["provider"]["status"])
        self.assertNotIn("SECRET", json.dumps(frame))

    def test_transient_failure_recovers_and_resets_consecutive_retry_budget(self):
        timeout = sckocp._envelope("timeout", payload())
        timeout["error"] = "UNTRUSTED DIAGNOSTIC SECRET"
        failures = [timeout, sckocp._envelope("collection_failed", payload())]
        code, _, error, collect, sleep = self.invoke_sequence(
            [".05", str(self.log), "--retries", "1", "--retry-delay", ".1"],
            [failures[0], envelope(), failures[1], envelope(), KeyboardInterrupt])
        self.assertEqual(0, code)
        self.assertEqual(5, collect.call_count)
        self.assertEqual(2, sleep.call_args_list.count(mock.call(.1)))
        rows = [line.split(",") for line in self.log.read_text(encoding="utf-8").splitlines()[2:]]
        self.assertEqual(4, len(rows))
        self.assertTrue(all(len(row) == 11 for row in rows))
        for index in (0, 2):
            self.assertEqual([""] * 6, rows[index][5:])
        for index in (1, 3):
            self.assertEqual("2400", rows[index][5])
        frames = [json.loads(line) for line in Path(str(self.log) + ".sckocp.jsonl").read_text(
            encoding="utf-8").splitlines()]
        self.assertEqual(["timeout", "ok", "collection_failed", "ok"],
                         [frame["provider"]["status"] for frame in frames])
        self.assertIsNone(frames[0]["provider"]["data"])
        self.assertIsNone(frames[2]["provider"]["data"])
        self.assertNotIn("SECRET", json.dumps(frames) + error)

    def test_transient_retry_exhaustion_is_bounded_and_zero_disables_it(self):
        for status in ("timeout", "collection_failed"):
            for retries in (0, 2):
                with self.subTest(status=status, retries=retries):
                    path = self.log.with_name("{}-{}.mon".format(status, retries))
                    code, _, _, collect, sleep = self.invoke_sequence(
                        [".05", str(path), "--retries", str(retries), "--retry-delay", ".2"],
                        [sckocp._envelope(status)] * (retries + 1))
                    self.assertEqual(1, code)
                    self.assertEqual(retries + 1, collect.call_count)
                    self.assertEqual([mock.call(.2)] * retries, sleep.call_args_list)
                    self.assertEqual(retries + 1, len(path.read_text(encoding="utf-8").splitlines()[2:]))

    def test_authorization_and_other_nontransient_errors_are_never_retried(self):
        statuses = ("license_denied", "license_unavailable", "unkeyed_build", "platform_required",
                    "unsafe_executable", "permission_denied", "unavailable", "invalid_data",
                    "output_limit", "unsupported_schema", "invalid_configuration", "unknown_error")
        for status in statuses:
            with self.subTest(status=status):
                code, _, _, collect, sleep = self.invoke_sequence(
                    ["--retries", "5"], [sckocp._envelope(status)])
                self.assertEqual(1, code)
                collect.assert_called_once()
                sleep.assert_not_called()

    def test_once_never_retries_even_a_transient_failure(self):
        for status in ("timeout", "collection_failed"):
            with self.subTest(status=status):
                code, _, _, collect, sleep = self.invoke_sequence(
                    ["--once", "--retries", "5"], [sckocp._envelope(status)])
                self.assertEqual(1, code)
                collect.assert_called_once()
                sleep.assert_not_called()

    def test_stop_during_retry_delay_exits_without_another_collection(self):
        code, _, _, collect, sleep = self.invoke_sequence(
            [".05", str(self.log), "--retry-delay", "30"],
            [sckocp._envelope("timeout")], sleep_effect=KeyboardInterrupt)
        self.assertEqual(0, code)
        collect.assert_called_once()
        sleep.assert_called_once_with(30)
        with mon_sensors._locked_file(str(self.log)):
            pass

    def test_view_keeps_table_on_stdout_and_status_on_stderr(self):
        code, output, error, collect = self.invoke(["--once"])
        self.assertEqual(0, code)
        self.assertIn("CPU温度(C)", output)
        self.assertIn("sckocp-v2 |", output)
        self.assertNotIn('"schema"', output)
        self.assertIn("Monitoring available", error)

    def test_duplicate_writer_does_not_collect_or_append(self):
        with mon_sensors._locked_file(str(self.log)) as stream:
            mon_sensors._prepare_log(stream)
            before = self.log.read_bytes()
            code, output, error, collect = self.invoke(["2", str(self.log), "--once"])
            self.assertEqual(1, code)
            collect.assert_not_called()
            self.assertEqual(before, self.log.read_bytes())

    def test_unknown_partial_symlink_and_hardlink_logs_are_rejected(self):
        for content in ("foreign\nheader\n", "partial"):
            self.log.write_text(content, encoding="utf-8")
            code, _, _, collect = self.invoke(["2", str(self.log), "--once"])
            self.assertEqual(1, code)
            collect.assert_not_called()
            self.assertEqual(content, self.log.read_text(encoding="utf-8"))
        target = self.log.with_name("target")
        target.write_text("protected", encoding="utf-8")
        self.log.unlink()
        self.log.symlink_to(target)
        code, _, _, collect = self.invoke(["2", str(self.log), "--once"])
        self.assertEqual(1, code)
        collect.assert_not_called()
        self.log.unlink()
        os.link(str(target), str(self.log))
        code, _, _, collect = self.invoke(["2", str(self.log), "--once"])
        self.assertEqual(1, code)
        collect.assert_not_called()
        self.assertEqual("protected", target.read_text(encoding="utf-8"))

    def test_trusted_existing_log_permissions_become_private_without_duplicate_headers(self):
        self.invoke(["2", str(self.log), "--once"])
        self.log.chmod(0o640)
        sidecar = Path(str(self.log) + ".sckocp.jsonl")
        sidecar.chmod(0o644)
        self.invoke(["2", str(self.log), "--once"])
        self.assertEqual(0o600, stat.S_IMODE(self.log.stat().st_mode))
        self.assertEqual(0o600, stat.S_IMODE(sidecar.stat().st_mode))
        self.assertEqual(1, self.log.read_text(encoding="utf-8").count("#=类型"))

    def test_foreign_log_keeps_content_and_permissions(self):
        self.log.write_text("foreign\nheader\n", encoding="utf-8")
        self.log.chmod(0o644)
        before = self.log.read_bytes()
        code, _, _, collect = self.invoke(["2", str(self.log), "--once"])
        self.assertEqual(1, code)
        collect.assert_not_called()
        self.assertEqual(before, self.log.read_bytes())
        self.assertEqual(0o644, stat.S_IMODE(self.log.stat().st_mode))

    def test_foreign_or_hardlinked_sidecar_is_not_modified(self):
        sidecar = Path(str(self.log) + ".sckocp.jsonl")
        foreign = ("foreign\n", '{"schema":"unrelated","provider":{}}\n',
                   '{"schema":"mon-sensors-sckocp-v1","provider":null}\n')
        for content in foreign:
            with self.subTest(content=content):
                sidecar.write_text(content, encoding="utf-8")
                sidecar.chmod(0o644)
                code, _, _, collect = self.invoke(["2", str(self.log), "--once"])
                self.assertEqual(1, code)
                collect.assert_not_called()
                self.assertEqual(content, sidecar.read_text(encoding="utf-8"))
                self.assertEqual(0o644, stat.S_IMODE(sidecar.stat().st_mode))
                self.assertEqual(b"", self.log.read_bytes())
        target = self.log.with_name("protected.jsonl")
        target.write_text('{"schema":"mon-sensors-sckocp-v1","provider":{}}\n', encoding="utf-8")
        sidecar.unlink()
        os.link(str(target), str(sidecar))
        before = target.read_bytes()
        mode = stat.S_IMODE(target.stat().st_mode)
        code, _, _, collect = self.invoke(["2", str(self.log), "--once"])
        self.assertEqual(1, code)
        collect.assert_not_called()
        self.assertEqual(before, target.read_bytes())
        self.assertEqual(mode, stat.S_IMODE(target.stat().st_mode))

    def test_other_owner_or_writable_logs_fail_before_chmod_or_collection(self):
        self.invoke(["2", str(self.log), "--once"])
        before = self.log.read_bytes()
        for mode in (0o620, 0o602, 0o4600):
            with self.subTest(mode=mode):
                self.log.chmod(mode)
                with mock.patch.object(mon_sensors.os, "fchmod") as chmod:
                    code, _, _, collect = self.invoke(["2", str(self.log), "--once"])
                    self.assertEqual(1, code)
                    collect.assert_not_called()
                    chmod.assert_not_called()
                self.assertEqual(before, self.log.read_bytes())
                self.assertEqual(mode, stat.S_IMODE(self.log.stat().st_mode))
        self.log.chmod(0o600)
        details = list(self.log.stat())
        details[4] = os.geteuid() + 1
        with mock.patch.object(mon_sensors.os, "fstat", return_value=os.stat_result(details)), \
                mock.patch.object(mon_sensors.os, "fchmod") as chmod:
            code, _, _, collect = self.invoke(["2", str(self.log), "--once"])
            self.assertEqual(1, code)
            collect.assert_not_called()
            chmod.assert_not_called()
        self.assertEqual(before, self.log.read_bytes())

    def test_invalid_options_fail_before_collection(self):
        for args in (["nan"], ["0"], ["--max-age", "inf"], ["--timeout", ".5"],
                     ["--binary", "relative"], ["--task", "bad,label"], ["--task", "bad\nlabel"],
                     ["--task", "bad\u2028label"], ["--retries", "-1"], ["--retries", "6"],
                     ["--retries", "1.5"], ["--retry-delay", "nan"], ["--retry-delay", "0"],
                     ["--retry-delay", "31"]):
            with self.subTest(args=args), mock.patch.object(sckocp_api, "collect") as collect, \
                    mock.patch("sys.stderr", io.StringIO()):
                with self.assertRaises(SystemExit) as caught:
                    mon_sensors.main(args)
                self.assertEqual(2, caught.exception.code)
                collect.assert_not_called()

    def test_environment_binary_and_keyboard_interrupt_release_log_lock(self):
        with mock.patch.dict(os.environ, {"SCKOCP_BINARY": "/configured/sckocp"}):
            _, _, _, collect = self.invoke(["--once"])
            collect.assert_called_once_with("/configured/sckocp", 1, 20, native_format="v2")
        with mock.patch.object(sckocp_api, "collect", side_effect=KeyboardInterrupt):
            self.assertEqual(0, mon_sensors.main(["2", str(self.log)]))
        with mon_sensors._locked_file(str(self.log)):
            pass


class MonSensorsOriginalTests(unittest.TestCase):
    def test_original_values_missing_amd_temperature_and_uncertain_zero(self):
        for amd in (False, True):
            sample = original_payload(amd)
            row = mon_sensors.make_row(envelope(sample), CONTEXT)
            self.assertEqual("sckocp-v1", row[0])
            self.assertEqual(["3000", "", "", "", "", ""] if amd else
                             ["2500", "65", "", "0", "", ""], row[5:])
            self.assertEqual(11, len(row))
            self.assertNotIn("metrics", sample["sockets"][0])

    def test_original_aggregates_need_all_reported_sockets_and_do_not_use_old_data(self):
        sample = original_payload()
        second = copy.deepcopy(sample["sockets"][0])
        second.update(id=1, temp_max_c=75, pkg_w=None)
        sample["sockets"].append(second)
        self.assertEqual("75", mon_sensors.make_row(envelope(sample), CONTEXT)[6])
        self.assertEqual("", mon_sensors.make_row(envelope(sample), CONTEXT)[8])
        for result, elapsed in ((envelope(sample), 31), (sckocp._envelope("license_denied"), 0)):
            row = mon_sensors.make_row(result, CONTEXT, elapsed=elapsed, native_format="v1")
            self.assertEqual("sckocp-v1", row[0])
            self.assertEqual([""] * 6, row[5:])

    def test_default_calls_public_api_and_marks_original_quality_in_both_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "original.mon"
            sample = envelope(original_payload())
            with mock.patch.object(sckocp_api, "collect", return_value=sample) as collect, \
                    mock.patch.dict(os.environ, {"SCKOCP_FORMAT": ""}), \
                    mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(0, mon_sensors.main(["1", str(path), "--once", "--binary", "/safe/sckocp"]))
            collect.assert_called_once_with("/safe/sckocp", 1, 20, native_format="v1")
            lines = path.read_text(encoding="utf-8").splitlines()
            self.assertIn("provider=sckocp-v1", lines[0])
            self.assertIn("quality=reported-validity-and-age-unknown", lines[0])
            self.assertEqual(["2500", "65", "", "0", "", ""], lines[2].split(",")[5:])
            record = json.loads(Path(str(path) + ".sckocp.jsonl").read_text(encoding="utf-8"))
            self.assertEqual("reported-validity-and-age-unknown", record["reading_quality"])
            self.assertEqual(sample, record["provider"])

    def test_format_changes_cannot_mix_with_existing_log_or_call_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            for first, second in (("v1", "v2"), ("v2", "v1")):
                path = Path(directory) / (first + ".mon")
                with mon_sensors._locked_file(str(path)) as stream:
                    mon_sensors._prepare_log(stream, first)
                original = path.read_bytes()
                with mock.patch.object(sckocp_api, "collect") as collect, \
                        mock.patch("sys.stderr", io.StringIO()):
                    self.assertEqual(1, mon_sensors.main(["1", str(path), "--once", "--format", second]))
                    collect.assert_not_called()
                self.assertEqual(original, path.read_bytes())

    def test_format_environment_is_explicit_validated_and_cli_can_override(self):
        for selected in ("v1", "v2"):
            with mock.patch.dict(os.environ, {"SCKOCP_FORMAT": selected}):
                self.assertEqual(selected, mon_sensors._arguments([]).format)
                self.assertEqual("v1", mon_sensors._arguments(["--format", "v1"]).format)
        with mock.patch.dict(os.environ, {"SCKOCP_FORMAT": "auto"}), \
                mock.patch.object(sckocp_api, "collect") as collect, \
                mock.patch("sys.stderr", io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                mon_sensors.main(["--once"])
            self.assertEqual(2, caught.exception.code)
            collect.assert_not_called()


class MonSensorsStopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.app = self.root / "ocrun"
        self.app.mkdir()
        self.proc = self.root / "proc"
        self.proc.mkdir()

    def process(self, pid, arguments, start="1000"):
        folder = self.proc / str(pid)
        folder.mkdir()
        (folder / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in arguments) + b"\0")
        fields = ["S"] + ["0"] * 18 + [start]
        (folder / "stat").write_text("{} (fixture) {}\n".format(pid, " ".join(fields)), encoding="utf-8")
        return folder

    def test_only_exact_application_entrypoints_are_stopped(self):
        script = str(self.app / "mon-sensors")
        bridge = str(self.app / "sckocp-collector" / "mon-sensors")
        plugin = str(self.app / "mon-sensors-plugin")
        commands = [
            ["/bin/bash", script, "2", "/tmp/task.mon"],
            ["/usr/bin/python3.11", bridge, "2", "/tmp/task.mon"],
            [str(self.app / "mon_sensors"), "2"],
            ["tee", "/tmp/mon-sensors-legacy.txt"],
            ["tee", script],
            ["/bin/bash", str(self.root / "other-app" / "mon-sensors")],
            ["/usr/bin/python3", "--unbuffered", script],
            ["/bin/bash", "-c", script],
            ["/usr/bin/python3", bridge, "--stop-app", str(self.app)],
            ["/usr/bin/python3", plugin, "2", "/tmp/plugin.mon"],
            [plugin, "2", "/tmp/direct.mon"],
            ["/usr/bin/python3", str(self.app / "mon-sensors-plugin.d" / "mon-sensors-plugin"), "2"],
            ["tee", plugin],
            ["/usr/bin/python3", plugin, "--stop-app", str(self.app)],
            ["/usr/bin/python3", str(self.root / "other-app" / "mon-sensors-plugin")],
        ]
        for index, command in enumerate(commands):
            self.process(900001 + index, command)
        with mock.patch.object(os, "kill") as kill:
            self.assertEqual(0, mon_sensors.stop_monitors(str(self.app), str(self.proc)))
        self.assertEqual({(900001, signal.SIGTERM), (900002, signal.SIGTERM),
                          (900003, signal.SIGTERM), (900010, signal.SIGTERM),
                          (900011, signal.SIGTERM), (900012, signal.SIGTERM)},
                         {call[0] for call in kill.call_args_list})

    def test_own_pid_and_changed_process_identity_are_not_signalled(self):
        self.process(os.getpid(), [str(self.app / "mon-sensors")])
        with mock.patch.object(os, "kill") as kill:
            mon_sensors.stop_monitors(str(self.app), str(self.proc))
            kill.assert_not_called()
        self.process(900001, [str(self.app / "mon-sensors")])
        original = (os.fsencode(str(self.app / "mon-sensors")) + b"\0", "1000")
        for changed in ((original[0], "1001"), (b"tee\0/tmp/mon-sensors\0", "1000")):
            with mock.patch.object(mon_sensors, "_process_snapshot", side_effect=[original, changed]), \
                    mock.patch.object(os, "kill") as kill:
                mon_sensors.stop_monitors(str(self.app), str(self.proc))
                kill.assert_not_called()

    def test_directory_aliases_work_without_collapsing_other_application_symlinks(self):
        alias = self.root / "alias"
        alias.symlink_to(self.app, target_is_directory=True)
        other = self.root / "other"
        other.mkdir()
        common = self.root / "shared-code"
        common.write_text("fixture", encoding="utf-8")
        (self.app / "mon-sensors").symlink_to(common)
        (other / "mon-sensors").symlink_to(common)
        self.process(900001, ["python3", str(alias / "mon-sensors")])
        self.process(900002, ["python3", str(other / "mon-sensors")])
        with mock.patch.object(os, "kill") as kill:
            mon_sensors.stop_monitors(str(alias), str(self.proc))
            kill.assert_called_once_with(900001, signal.SIGTERM)

    def test_gone_process_is_ignored_but_signal_permission_failure_is_reported(self):
        self.process(900001, ["sh", str(self.app / "mon-sensors")])
        with mock.patch.object(os, "kill", side_effect=ProcessLookupError):
            self.assertEqual(0, mon_sensors.stop_monitors(str(self.app), str(self.proc)))
        with mock.patch.object(os, "kill", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                mon_sensors.stop_monitors(str(self.app), str(self.proc))

    def test_cli_stop_never_collects_and_bad_scope_returns_failure(self):
        with mock.patch.object(mon_sensors, "stop_monitors", return_value=0) as stop, \
                mock.patch.object(sckocp_api, "collect") as collect, \
                mock.patch.dict(os.environ, {"SCKOCP_BINARY": "unused-relative-path"}):
            self.assertEqual(0, mon_sensors.main(["--stop-app", str(self.app)]))
            stop.assert_called_once_with(str(self.app))
            collect.assert_not_called()
        for app in ("relative", "/", str(self.root / "missing")):
            with self.subTest(app=app), mock.patch.object(os, "kill") as kill, \
                    mock.patch("sys.stderr", io.StringIO()):
                self.assertEqual(1, mon_sensors.main(["--stop-app", app]))
                kill.assert_not_called()
        self.assertEqual(0, mon_sensors.stop_monitors(str(self.app), str(self.proc)))


if __name__ == "__main__":
    unittest.main()
