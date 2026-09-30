"""Public local API contract and compatibility checks, run on cloud Linux."""
import io
import json
import signal
import unittest
from unittest import mock

from ocrun import sckocp as legacy
from sckocp_api import SCHEMA, collect
from sckocp_api import cli, provider
from test_sckocp import payload


class PublicApiTests(unittest.TestCase):
    def test_public_contract_does_not_mutate_legacy_contract(self):
        old = provider._envelope("ok", payload())
        with mock.patch.object(provider, "collect", return_value=old):
            result = collect("/licensed/sckocp", .5, 10)
        self.assertEqual("sckocp-api-v1", SCHEMA)
        self.assertEqual(SCHEMA, result["schema"])
        self.assertEqual("ocrun-sckocp-v1", old["schema"])
        self.assertIs(result["data"], old["data"])
        self.assertIsNone(result["data"]["sockets"][0]["metrics"]["dram_watts"]["value"])
        self.assertIs(legacy, provider)

    def test_every_call_checks_provider_and_failure_cannot_reuse_previous_data(self):
        sequence = [provider._envelope("ok", payload()), provider._envelope("license_denied"),
                    provider._envelope("license_unavailable"), provider._envelope("ok", payload())]
        with mock.patch.object(provider, "collect", side_effect=sequence) as native:
            responses = [collect("/licensed/sckocp", .5, 10) for unused in sequence]
        self.assertEqual(4, native.call_count)
        self.assertTrue(all(response["schema"] == SCHEMA for response in responses))
        for response in responses[1:3]:
            self.assertIsNone(response["data"])
            self.assertTrue(response["error"])
        self.assertIsNotNone(responses[-1]["data"])

    def test_all_native_gate_statuses_keep_public_error_shape_and_private_text_out(self):
        for code, status in ((10, "license_denied"), (12, "license_unavailable"),
                             (13, "unkeyed_build"), (14, "platform_required")):
            with self.subTest(code=code), mock.patch.object(provider, "_capture",
                    return_value=(code, b"private-activation-data")):
                result = collect()
                self.assertEqual(status, result["status"])
                self.assertEqual(SCHEMA, result["schema"])
                self.assertIsNone(result["data"])
                self.assertNotIn("private-activation-data", json.dumps(result))

    def test_invalid_parameters_do_not_invoke_provider_and_still_return_public_json(self):
        with mock.patch.object(provider, "_capture") as execute:
            result = collect(binary="relative-sckocp", interval=1, timeout=.5)
        execute.assert_not_called()
        self.assertEqual(SCHEMA, result["schema"])
        self.assertEqual("invalid_configuration", result["status"])
        self.assertIsNone(result["data"])

    def test_cli_returns_one_json_and_failure_exit_code(self):
        for status, expected in (("ok", 0), ("license_denied", 1)):
            value = provider._envelope(status, payload() if status == "ok" else None)
            value["schema"] = SCHEMA
            output = io.StringIO()
            with mock.patch.object(cli, "collect", return_value=value), mock.patch("sys.stdout", output):
                code = cli.main(["--binary", "/licensed/sckocp", "--interval", ".5", "--timeout", "10"])
            self.assertEqual(expected, code)
            self.assertEqual(1, output.getvalue().count("\n"))
            self.assertEqual(value, json.loads(output.getvalue()))

    def test_cli_restores_signal_handlers_after_an_interrupted_call(self):
        previous = {sig: signal.getsignal(sig) for sig in (signal.SIGTERM, signal.SIGINT)}
        output = io.StringIO()
        with mock.patch.object(cli, "collect", side_effect=KeyboardInterrupt), mock.patch("sys.stdout", output):
            self.assertEqual(130, cli.main([]))
        self.assertEqual("", output.getvalue())
        self.assertEqual(previous, {sig: signal.getsignal(sig) for sig in previous})


if __name__ == "__main__":
    unittest.main()
