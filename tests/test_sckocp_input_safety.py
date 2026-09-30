"""Hostile provider data and safe failure envelopes, exercised in cloud Linux."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

from sckocp_api import collect, provider
from sckocp_api.security import UnsafeExecutable
from test_sckocp import payload
from test_sckocp_original_api import original_payload


class InputSafetyTests(unittest.TestCase):
    def response(self, raw, native_format="v1"):
        with mock.patch.object(provider, "_capture", return_value=(0, raw)):
            return collect(native_format=native_format)

    def test_original_120_contract_keeps_reported_values_and_nulls(self):
        for amd in (False, True):
            sample = original_payload(amd)
            sample["version"] = "1.2.0"
            result = self.response(json.dumps(sample).encode())
            self.assertEqual("ok", result["status"])
            self.assertEqual(sample, result["data"])

    def test_nested_json_is_rejected_before_recursive_decoder_runs(self):
        for value in (b"[" * 33 + b"0" + b"]" * 33,
                      b'{"extra":' * 33 + b"0" + b"}" * 33):
            with mock.patch.object(provider.json, "loads") as decoder:
                result = self.response(value)
            decoder.assert_not_called()
            self.assertEqual("invalid_data", result["status"])
            self.assertIsNone(result["data"])

    def test_quoted_nesting_and_escaped_quotes_are_not_counted_as_structure(self):
        sample = payload()
        sample["notes"] = ['text with braces ' + '{[' * 60 + '\\" } ]']
        self.assertEqual("ok", self.response(json.dumps(sample).encode(), "v2")["status"])

    def test_oversized_numbers_and_overflow_are_safe_failures(self):
        for value in (b"9" * 129, b"1e" + b"9" * 129, b"1e309", b"-Infinity"):
            result = self.response(b'{"private-secret":' + value + b'}')
            self.assertEqual("invalid_data", result["status"])
            self.assertIsNone(result["data"])
            self.assertNotIn("private-secret", json.dumps(result))

    def test_unicode_controls_and_surrogates_never_reach_public_output(self):
        for bad in ("\u202e", "\u0085", "\ud800", "\udfff", "\u200b"):
            for native_format, base in (("v1", original_payload()), ("v2", payload())):
                sample = copy.deepcopy(base)
                sample["version"] = "1.2.0" + bad
                result = self.response(json.dumps(sample).encode(), native_format)
                self.assertEqual("invalid_data", result["status"])
                self.assertIsNone(result["data"])
        sample = payload()
        sample["notes"] = ["正常的 Unicode 传感器说明"]
        self.assertEqual("ok", self.response(json.dumps(sample).encode(), "v2")["status"])

    def test_unknown_vendor_is_not_treated_as_amd(self):
        sample = original_payload(amd=True)
        sample["vendor"] = "private-unexpected-vendor"
        result = self.response(json.dumps(sample).encode())
        self.assertEqual("invalid_data", result["status"])
        self.assertNotIn("private-unexpected-vendor", json.dumps(result))

    def test_policy_and_permission_errors_are_fixed_non_leaking_failures(self):
        for error, status in ((UnsafeExecutable("private-secret-path"), "unsafe_executable"),
                              (PermissionError("private-secret-path"), "permission_denied")):
            with mock.patch.object(provider, "_capture", side_effect=error):
                result = collect()
            self.assertEqual(status, result["status"])
            self.assertIsNone(result["data"])
            self.assertNotIn("private-secret-path", json.dumps(result))

    def test_control_or_surrogate_executable_path_is_refused_before_launch(self):
        for binary in ("/usr/bin/sckocp\n", "/tmp/\u202ename", "/tmp/\ud800", "/" + "x" * 4096):
            with mock.patch.object(provider, "_capture") as capture:
                self.assertEqual("invalid_configuration", collect(binary=binary)["status"])
            capture.assert_not_called()

    def test_pinned_child_uses_fixed_directory_and_bounded_native_transport_setting(self):
        with tempfile.TemporaryDirectory() as directory:
            program = Path(directory) / "sckocp"
            sample = original_payload()
            program.write_text("#!" + sys.executable + "\n"
                "import os,sys\n"
                "assert os.getcwd() == '/'\n"
                "assert sys.argv[1:] == ['mon','--json']\n"
                "assert os.environ['SCKOCP_TIMEOUT'] == '2'\n"
                "assert 'PYTHONPATH' not in os.environ and 'LD_PRELOAD' not in os.environ\n"
                "print(" + repr(json.dumps(sample)) + ")\n")
            program.chmod(0o700)
            with mock.patch.dict(os.environ, {"SCKOCP_TIMEOUT": "120", "PYTHONPATH": directory}):
                result = collect(str(program), interval=.05, timeout=5.1)
            self.assertEqual("ok", result["status"])


if __name__ == "__main__":
    unittest.main()
