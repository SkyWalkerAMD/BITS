"""Provider contract and disposable Linux subprocess tests; no hardware access."""
import copy
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest import mock

from ocrun import sckocp


def metric(unit, value=None, source="fixture", age=0.0):
    return {"value": value, "unit": unit,
            "source": source if value is not None else None,
            "status": "ok" if value is not None else "unavailable",
            "age_s": age if value is not None else None}


def payload():
    socket_metrics = {name: metric(unit) for name, unit in sckocp.SOCKET_METRICS.items()}
    socket_metrics["temperature_c"] = metric("C", 61.5, "msr:intel-core-dts", 0.2)
    socket_metrics["package_watts"] = metric("W", 154.3, "msr:intel-package-energy", 0.2)
    core_metrics = {name: metric(unit) for name, unit in sckocp.CORE_METRICS.items()}
    core_metrics["active_mhz"] = metric("MHz", 2400)
    core_metrics["c0_percent"] = metric("%", 75.5)
    return {"schema": "sckocp-mon-v2", "version": "1.1.0", "extension": "ocrun-api-1",
            "vendor": "GenuineIntel", "family": 6, "interval_s": 1.0,
            "sampled_at_unix_ms": 1800000000000, "duration_s": 1.001, "read_only": True,
            "sockets": [{"id": 0, "metrics": socket_metrics,
                         "flags": {"thermal_throttling": False}}],
            "cores": [{"cpu": 0, "socket": 0, "metrics": core_metrics}],
            "system": {"metrics": {"psu_input_watts": metric("W", 1100, "bmc:psu-input-sum", 3)},
                       "psus": [{"name": "PSU1", "metrics": {"input_watts": metric("W", 500)}},
                                {"name": "PSU2", "metrics": {"input_watts": metric("W", 600)}}],
                       "psu_present_count": 2, "psu_reporting_count": 2,
                       "redundancy": "Fully Redundant", "redundancy_age_s": None},
            "notes": ["C0 residency is not operating-system CPU utilization"]}


class ProviderValidationTests(unittest.TestCase):
    def test_valid_payload_retains_missing_readings_and_per_metric_ages(self):
        sample = payload()
        self.assertIs(sample, sckocp.validate_payload(sample))
        self.assertIsNone(sample["sockets"][0]["metrics"]["dram_watts"]["value"])
        self.assertEqual(3, sample["system"]["metrics"]["psu_input_watts"]["age_s"])
        self.assertEqual(75.5, sample["cores"][0]["metrics"]["c0_percent"]["value"])
        del sample["extension"]
        sckocp.validate_payload(sample)

    def test_nonfinite_boolean_negative_or_missing_metric_data_is_rejected(self):
        mutations = [("value", True), ("value", float("nan")), ("value", float("inf")),
                     ("value", -300), ("value", None), ("value", "61.5"),
                     ("unit", "F"), ("age_s", -1), ("age_s", None),
                     ("age_s", float("inf")), ("source", None),
                     ("source", "unsafe\nsource"), ("status", "unknown")]
        for key, value in mutations:
            with self.subTest(key=key, value=value):
                sample = payload()
                sample["sockets"][0]["metrics"]["temperature_c"][key] = value
                with self.assertRaises(ValueError):
                    sckocp.validate_payload(sample)
        sample = payload()
        del sample["sockets"][0]["metrics"]["dram_watts"]
        with self.assertRaises(ValueError):
            sckocp.validate_payload(sample)

    def test_unavailable_reading_cannot_masquerade_as_zero(self):
        for key, value in (("value", 0), ("source", "msr"), ("age_s", 0)):
            with self.subTest(key=key):
                sample = payload()
                sample["sockets"][0]["metrics"]["dram_watts"][key] = value
                with self.assertRaises(ValueError):
                    sckocp.validate_payload(sample)

    def test_physical_core_topology_is_checked(self):
        for mutate in (lambda p: p["cores"][0].update(socket=8),
                       lambda p: p["cores"].append(copy.deepcopy(p["cores"][0])),
                       lambda p: p["sockets"].append(copy.deepcopy(p["sockets"][0])),
                       lambda p: p["cores"][0].update(cpu=True),
                       lambda p: p["sockets"][0].update(id=-1)):
            sample = payload()
            mutate(sample)
            with self.assertRaises(ValueError):
                sckocp.validate_payload(sample)

    def test_partial_psu_power_cannot_be_reported_as_total(self):
        sample = payload()
        sample["system"]["psus"].pop()
        sample["system"]["psu_reporting_count"] = 1
        with self.assertRaises(ValueError):
            sckocp.validate_payload(sample)
        sample["system"]["metrics"]["psu_input_watts"] = metric("W")
        sckocp.validate_payload(sample)
        sample["system"]["psu_reporting_count"] = 2
        with self.assertRaises(ValueError):
            sckocp.validate_payload(sample)

    def test_unknown_fields_and_invalid_metadata_are_rejected(self):
        mutations = [lambda p: p.update(lease="never upload this"),
                     lambda p: p.update(read_only=1), lambda p: p.update(read_only=False),
                     lambda p: p.update(sampled_at_unix_ms=True),
                     lambda p: p.update(duration_s=0), lambda p: p.update(family=True),
                     lambda p: p.update(notes=["bad\x1btext"]),
                     lambda p: p["sockets"][0]["flags"].update(thermal_throttling=1),
                     lambda p: p["cores"][0]["metrics"]["c0_percent"].update(value=101),
                     lambda p: p.update(cores=p["cores"] * 4097),
                     lambda p: p["system"].update(redundancy_age_s=-1)]
        for index, mutate in enumerate(mutations):
            with self.subTest(index=index):
                sample = payload()
                mutate(sample)
                with self.assertRaises(ValueError):
                    sckocp.validate_payload(sample)

    def test_v1_is_not_silently_used_as_v2(self):
        sample = payload()
        sample["schema"] = "sckocp-mon-v1"
        with self.assertRaises(sckocp.UnsupportedSchema):
            sckocp.validate_payload(sample)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.binary = Path(self.directory.name) / "fake-sckocp"

    def program(self, body):
        self.binary.write_text("#!" + sys.executable + "\n" + body + "\n", encoding="utf-8")
        self.binary.chmod(0o700)
        return str(self.binary)

    def test_real_child_receives_one_shot_arguments_and_read_only_environment(self):
        expected = payload()
        body = ("import json, os, sys\n"
                "assert sys.argv[1:] == ['mon', '--json=v2']\n"
                "assert os.environ['SCKOCP_MODE'] == 'ro'\n"
                "assert os.environ['LC_ALL'] == 'C'\n"
                "assert os.environ['SCKOCP_MODPROBE'] == '0'\n"
                "assert os.environ['BMCPROBET'] == '2'\n"
                "assert os.environ['BMCTTL'] == os.environ['BMCSDRTTL'] == '0'\n"
                "assert float(os.environ['INT']) == 0.25\n"
                "assert 'SCKOCP_HOST' not in os.environ\n"
                "print(" + repr(json.dumps(expected)) + ")")
        with mock.patch.dict(os.environ, {"SCKOCP_MODE": "rw", "SCKOCP_HOST": "invalid"}):
            result = sckocp.collect(self.program(body), interval=0.25, timeout=5)
        self.assertEqual("ok", result["status"])
        self.assertEqual(expected, result["data"])
        self.assertIsNone(result["error"])
        self.assertTrue(result["observed_at"].endswith("Z"))

    def test_all_activation_failures_are_distinct_and_do_not_leak_output(self):
        secret = "license-code-and-machine-fingerprint-SECRET"
        for code, status in ((10, "license_denied"), (12, "license_unavailable"),
                             (13, "unkeyed_build"), (14, "platform_required"),
                             (1, "collection_failed")):
            with self.subTest(code=code):
                body = ("import sys\nprint(" + repr(secret) + ")\n"
                        "sys.stderr.write(" + repr(secret) + ")\nsys.exit(" + str(code) + ")")
                result = sckocp.collect(self.program(body), timeout=5)
                self.assertEqual(status, result["status"])
                self.assertIsNone(result["data"])
                self.assertNotIn(secret, json.dumps(result))

    def test_plaintext_duplicate_keys_nonfinite_or_wrong_schema_are_safe_failures(self):
        for output, status in (("secret activation diagnostic", "invalid_data"),
                               ('{"schema":"sckocp-mon-v1"}', "unsupported_schema"),
                               ('{"schema":"sckocp-mon-v2","schema":"duplicate"}', "invalid_data"),
                               ('{"temperature":NaN}', "invalid_data"),
                               ('{"temperature":Infinity}', "invalid_data"),
                               ('[[[', "invalid_data")):
            with self.subTest(output=output):
                with mock.patch.object(sckocp, "_capture", return_value=(0, output.encode("utf-8"))):
                    result = sckocp.collect(str(self.binary))
                self.assertEqual(status, result["status"])
                self.assertIsNone(result["data"])
                self.assertNotIn("secret activation", json.dumps(result))

    def test_invalid_utf8_is_a_safe_failure(self):
        with mock.patch.object(sckocp, "_capture", return_value=(0, b"\xff")):
            self.assertEqual("invalid_data", sckocp.collect(str(self.binary))["status"])

    def test_both_output_streams_are_bounded(self):
        for fd in (1, 2):
            with self.subTest(fd=fd):
                body = "import os\nos.write(" + str(fd) + ", b'x' * 8192)"
                with mock.patch.object(sckocp, "STDOUT_LIMIT", 1024), mock.patch.object(
                        sckocp, "STDERR_LIMIT", 1024):
                    result = sckocp.collect(self.program(body), timeout=5)
                self.assertEqual("output_limit", result["status"])
                self.assertIsNone(result["data"])

    def test_timeout_kills_descendant_even_after_parent_exits(self):
        marker = str(Path(self.directory.name) / "child-survived")
        body = ("import os, time\n"
                "if os.fork() == 0:\n"
                "    time.sleep(0.8)\n"
                "    with open(" + repr(marker) + ", 'w') as stream: stream.write('survived')\n"
                "    os._exit(0)\n"
                "os._exit(0)")
        started = time.monotonic()
        result = sckocp.collect(self.program(body), interval=0.05, timeout=0.2)
        self.assertEqual("timeout", result["status"])
        self.assertLess(time.monotonic() - started, 2)
        time.sleep(0.85)
        self.assertFalse(os.path.exists(marker), "Timed-out provider child was left running")

    def test_deadline_still_applies_when_child_closes_its_output(self):
        body = "import os, time\nos.close(1)\nos.close(2)\ntime.sleep(5)"
        result = sckocp.collect(self.program(body), interval=0.05, timeout=0.2)
        self.assertEqual("timeout", result["status"])

    def test_success_also_cleans_helper_that_closed_its_output(self):
        marker = str(Path(self.directory.name) / 'success-helper-survived')
        body = ('import os, time\n'
                'ready_r, ready_w = os.pipe()\n'
                'if os.fork() == 0:\n'
                '    os.close(ready_r)\n'
                '    os.setpgid(0, 0)\n'
                '    os.close(1); os.close(2)\n'
                '    os.write(ready_w, b"1"); os.close(ready_w)\n'
                '    time.sleep(0.6)\n'
                '    open(' + repr(marker) + ', "w").close()\n'
                '    os._exit(0)\n'
                'os.close(ready_w); os.read(ready_r, 1); os.close(ready_r)\n'
                'print(' + repr(json.dumps(payload())) + ')\n')
        result = sckocp.collect(self.program(body), interval=0.05, timeout=5)
        self.assertEqual('ok', result['status'])
        time.sleep(.7)
        self.assertFalse(os.path.exists(marker), 'Successful provider left a helper running')

    def test_timeout_kills_native_style_helper_in_its_own_process_group(self):
        marker = str(Path(self.directory.name) / "detached-group-survived")
        body = ("import os, time\n"
                "if os.fork() == 0:\n"
                "    os.setpgid(0, 0)\n"
                "    time.sleep(0.8)\n"
                "    with open(" + repr(marker) + ", 'w') as stream: stream.write('survived')\n"
                "    os._exit(0)\n"
                "os._exit(0)")
        result = sckocp.collect(self.program(body), interval=0.05, timeout=0.2)
        self.assertEqual("timeout", result["status"])
        time.sleep(0.85)
        self.assertFalse(os.path.exists(marker), "Native-style helper survived the timeout")

    def test_missing_binary_and_invalid_configuration_are_distinct(self):
        self.assertEqual("unavailable", sckocp.collect(str(self.binary))["status"])
        configurations = [{"binary": "sckocp"}, {"binary": "/bad\0path"},
                          {"interval": True}, {"interval": float("nan")},
                          {"timeout": 0.5, "interval": 1}, {"timeout": 999}]
        for configuration in configurations:
            with self.subTest(configuration=configuration):
                with mock.patch.object(sckocp, "_capture") as capture:
                    self.assertEqual("invalid_configuration", sckocp.collect(**configuration)["status"])
                    capture.assert_not_called()

    def test_cli_has_one_json_envelope_and_meaningful_exit_code(self):
        for status, expected_exit in (("ok", 0), ("license_denied", 1)):
            with self.subTest(status=status):
                result = sckocp._envelope(status, payload() if status == "ok" else None)
                output = io.StringIO()
                with mock.patch.object(sckocp, "collect", return_value=result), mock.patch(
                        "sys.stdout", output):
                    exit_code = sckocp.main(["--binary", "/usr/bin/sckocp"])
                self.assertEqual(expected_exit, exit_code)
                self.assertEqual(result, json.loads(output.getvalue()))
                self.assertEqual(1, output.getvalue().count("\n"))


if __name__ == "__main__":
    unittest.main()
