"""Original sckocp support must preserve licensing, schema and read limitations."""
import copy
import json
import os
import sys
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from sckocp_api import collect
from sckocp_api import provider


def original_payload(amd=False):
    result = {"schema": "sckocp-mon-v1", "version": "1.1.0", "vendor": "GenuineIntel",
              "family": 6, "interval_s": 1, "sockets": [{"id": 0, "tjmax_c": 100,
              "temp_max_c": 65, "vid_v": 1.05, "core_mhz": 2600, "base_mhz": 2500, "pkg_w": 0}],
              "cores": [{"cpu": 0, "socket": 0, "mhz": 2500, "temp_c": 65,
                         "vid_v": 1.05, "c0_pct": 100, "c6_pct": 0}]}
    if amd:
        result.update(vendor="AuthenticAMD", family=25, sockets=[{"id": 0, "pkg_w": None}],
                      cores=[{"cpu": 0, "socket": 0, "mhz": 3000, "c0_pct": 25}])
    return result


class OriginalApiTests(unittest.TestCase):
    def test_original_schema_is_preserved_without_inventing_sensor_validity(self):
        for amd in (False, True):
            value = original_payload(amd)
            with mock.patch.object(provider, "_capture", return_value=(0, json.dumps(value).encode())) as native:
                result = collect()
            native.assert_called_once_with("/usr/bin/sckocp", 1.0, 20.0, "v1")
            self.assertEqual("ok", result["status"])
            self.assertEqual(value, result["data"])
            self.assertNotIn("read_only", result["data"])
            self.assertNotIn("system", result["data"])
            self.assertNotIn("metrics", result["data"]["sockets"][0])

    def test_unknown_fields_invalid_numbers_duplicates_and_topology_are_rejected(self):
        mutations = [lambda x: x.update(lease="secret"),
                     lambda x: x["sockets"][0].update(activation_code="secret"),
                     lambda x: x["sockets"][0].update(pkg_w=True),
                     lambda x: x["sockets"][0].update(pkg_w=float("inf")),
                     lambda x: x["sockets"].append(copy.deepcopy(x["sockets"][0])),
                     lambda x: x["cores"][0].update(socket=9),
                     lambda x: x["cores"].append(copy.deepcopy(x["cores"][0])),
                     lambda x: x["cores"][0].update(c0_pct=101),
                     lambda x: x["cores"][0].update(temp_c=-300),
                     lambda x: x.update(interval_s=0)]
        for mutate in mutations:
            value = original_payload()
            mutate(value)
            with mock.patch.object(provider, "_capture", return_value=(0, json.dumps(value).encode())):
                result = collect()
            self.assertEqual("invalid_data", result["status"])
            self.assertIsNone(result["data"])
            self.assertNotIn("secret", json.dumps(result))

    def test_format_mismatch_and_bad_option_never_silently_change_format(self):
        with mock.patch.object(provider, "_capture", return_value=(0, json.dumps(original_payload()).encode())):
            self.assertEqual("unsupported_schema", collect(native_format="v2")["status"])
        with mock.patch.object(provider, "_capture") as native:
            self.assertEqual("invalid_configuration", collect(native_format="auto")["status"])
        native.assert_not_called()

    def test_real_original_command_uses_read_only_environment_and_only_mon_json(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "sckocp"
            executable.write_text("#!" + sys.executable + "\nimport os,sys,json\n"
                "assert sys.argv[1:] == ['mon','--json']\n"
                "assert os.environ['SCKOCP_MODE'] == 'ro'\n"
                "assert os.environ['SCKOCP_MODPROBE'] == '0'\n"
                "assert 'SCKOCP_HOST' not in os.environ\n"
                "assert 'LD_PRELOAD' not in os.environ\n"
                "print(" + repr(json.dumps(original_payload())) + ")\n")
            executable.chmod(0o700)
            before = executable.read_bytes()
            with mock.patch.dict(os.environ, {"SCKOCP_HOST": "https://untrusted.invalid", "SCKOCP_MODE": "rw"}):
                result = collect(str(executable))
            self.assertEqual("ok", result["status"])
            self.assertEqual(before, executable.read_bytes())
            self.assertEqual(["sckocp"], sorted(path.name for path in Path(directory).iterdir()))


if __name__ == "__main__":
    unittest.main()
