"""Offline application tests; test fields below are not official MCP schemas."""

import asyncio
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hacdonalds.adapter import McDonaldAdapter
from hacdonalds.cli import main, read_arguments
from hacdonalds.client import McDonaldClient, validate_arguments
from hacdonalds.config import Settings, credential_path, load_settings, save_token
from hacdonalds.errors import HacdonaldsError, public_error
from hacdonalds.privacy import redact
from hacdonalds.transport import SDKTransport

TEST_SCHEMA = {"type": "object", "properties": {"test_store_id": {"type": "string"}},
               "required": ["test_store_id"], "additionalProperties": False}


class FakeTransport:
    def __init__(self, *, result=None, pages=None):
        self.result = result if result is not None else {
            "is_error": False, "structured_content": {"test_total_fen": 2345}, "content": []}
        self.pages = pages or [{"tools": [
            {"name": name, "description": "TEST ONLY", "input_schema": TEST_SCHEMA}
            for name in ["query-meals", "calculate-price", "create-order"]], "next_cursor": None}]
        self.calls = []
        self.list_calls = []

    async def list_tools_page(self, cursor=None):
        self.list_calls.append(cursor)
        return self.pages[0 if cursor is None else int(cursor)]

    async def call_tool(self, name, arguments):
        self.calls.append((name, arguments))
        return self.result


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)

    def test_missing_token_is_actionable(self):
        with self.assertRaises(HacdonaldsError) as caught:
            load_settings(self.root)
        self.assertEqual(caught.exception.code, "TOKEN_MISSING")

    def test_save_and_load_private_file(self):
        path = save_token("test-only-token", self.root)
        self.assertEqual(load_settings(self.root).token, "test-only-token")
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
        self.assertNotIn("test-only-token", repr(load_settings(self.root)))

    def test_environment_has_precedence(self):
        save_token("file-test-token", self.root)
        os.environ["MCD_MCP_TOKEN"] = "env-test-token"
        self.assertEqual(load_settings(self.root).token, "env-test-token")

    def test_empty_environment_does_not_silently_use_file(self):
        save_token("file-test-token", self.root)
        os.environ["MCD_MCP_TOKEN"] = ""
        with self.assertRaises(HacdonaldsError):
            load_settings(self.root)

    def test_header_injection_rejected(self):
        with self.assertRaises(HacdonaldsError):
            save_token("bad\r\nX-Header: injected", self.root)

    def test_bearer_prefix_rejected(self):
        with self.assertRaises(HacdonaldsError):
            save_token("Bearer test-token", self.root)

    def test_insecure_permissions_rejected(self):
        path = save_token("test-only-token", self.root)
        path.chmod(0o644)
        with self.assertRaises(HacdonaldsError) as caught:
            load_settings(self.root)
        self.assertEqual(caught.exception.code, "UNSAFE_CONFIG")

    def test_symlink_file_rejected(self):
        target = self.root / "unrelated"
        target.write_text('{}')
        path = credential_path(self.root)
        path.parent.mkdir()
        path.symlink_to(target)
        with self.assertRaises(HacdonaldsError):
            load_settings(self.root)

    def test_save_does_not_follow_file_symlink(self):
        target = self.root / "unrelated"
        target.write_text('unmodified')
        path = credential_path(self.root)
        path.parent.mkdir()
        path.symlink_to(target)
        save_token("test-only-token", self.root)
        self.assertEqual(target.read_text(), "unmodified")
        self.assertFalse(path.is_symlink())

    def test_invalid_timeout(self):
        for value in [0, 121, float("nan"), float("inf")]:
            with self.subTest(value=value), self.assertRaises(HacdonaldsError):
                Settings("test-only-token", value)


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_discovery_schema_and_quote_path_with_fake_transport(self):
        transport = FakeTransport()
        adapter = McDonaldAdapter(McDonaldClient(transport))
        result = await adapter.execute("quote", {"test_store_id": "test-store"})
        self.assertEqual(result["tool"], "calculate-price")
        self.assertEqual(result["data"], {"test_total_fen": 2345})
        self.assertEqual(transport.calls, [("calculate-price", {"test_store_id": "test-store"})])

    async def test_missing_required_input_never_calls_tool(self):
        transport = FakeTransport()
        with self.assertRaises(HacdonaldsError) as caught:
            await McDonaldClient(transport).call("calculate-price", {})
        self.assertEqual(caught.exception.code, "INVALID_ARGUMENTS")
        self.assertEqual(transport.calls, [])

    async def test_wrong_type_and_extra_field_rejected(self):
        for args in [{"test_store_id": 1}, {"test_store_id": "x", "extra": 1}]:
            with self.subTest(args=args), self.assertRaises(HacdonaldsError):
                validate_arguments(TEST_SCHEMA, args)

    async def test_disabled_write_tool_never_reaches_transport(self):
        transport = FakeTransport()
        with self.assertRaises(HacdonaldsError) as caught:
            await McDonaldClient(transport).call("create-order", {})
        self.assertEqual(caught.exception.code, "TOOL_BLOCKED")
        self.assertEqual(transport.calls, [])
        self.assertEqual(transport.list_calls, [])

    async def test_unavailable_tool(self):
        with self.assertRaises(HacdonaldsError) as caught:
            await McDonaldClient(FakeTransport()).call("query-nearby-stores", {})
        self.assertEqual(caught.exception.code, "TOOL_UNAVAILABLE")

    async def test_all_tool_pages_are_discovered(self):
        transport = FakeTransport(pages=[{"tools": [], "next_cursor": "1"},
                                         {"tools": [], "next_cursor": None}])
        self.assertEqual(await McDonaldClient(transport).discover(), [])
        self.assertEqual(transport.list_calls, [None, "1"])

    async def test_repeated_cursor_is_bounded(self):
        transport = FakeTransport(pages=[{"tools": [], "next_cursor": "0"}])
        with self.assertRaises(HacdonaldsError) as caught:
            await McDonaldClient(transport).discover()
        self.assertEqual(caught.exception.code, "PAGINATION_ERROR")

    async def test_tool_errors_are_not_success_results(self):
        client = McDonaldClient(FakeTransport(result={"is_error": True, "content": []}))
        with self.assertRaises(HacdonaldsError) as caught:
            await client.call("query-meals", {"test_store_id": "x"})
        self.assertEqual(caught.exception.code, "TOOL_FAILED")

    async def test_json_text_fallback(self):
        client = McDonaldClient(FakeTransport(result={"is_error": False, "content": [
            {"type": "text", "text": '{"test_items": []}'}]}))
        result = await client.call("query-meals", {"test_store_id": "x"})
        self.assertEqual(result["encoding"], "json_text")
        self.assertEqual(result["data"], {"test_items": []})

    async def test_non_json_text_is_preserved(self):
        client = McDonaldClient(FakeTransport(result={"is_error": False, "content": [
            {"type": "text", "text": 'TEST: no menu'}]}))
        result = await client.call("query-meals", {"test_store_id": "x"})
        self.assertEqual(result["encoding"], "text")
        self.assertEqual(result["data"], "TEST: no menu")

    async def test_external_schema_reference_is_blocked(self):
        with self.assertRaises(HacdonaldsError) as caught:
            validate_arguments({"$ref": "https://example.test/schema"}, {})
        self.assertEqual(caught.exception.code, "UNSUPPORTED_SCHEMA")

    async def test_local_schema_reference_works(self):
        validate_arguments({"$defs": {"input": {"type": "object"}}, "$ref": "#/$defs/input"}, {})

    async def test_timeout_does_not_retry(self):
        transport = FakeTransport()
        async def slow_call(name, arguments):
            transport.calls.append((name, arguments))
            await asyncio.sleep(0.05)
        transport.call_tool = slow_call
        with self.assertRaises(TimeoutError):
            await McDonaldClient(transport, timeout_seconds=0.01).call("query-meals", {"test_store_id": "x"})
        self.assertEqual(len(transport.calls), 1)

    async def test_sdk_mapping_uses_v2_fields(self):
        class FakeSDK:
            async def list_tools(self, *, cursor=None):
                return SimpleNamespace(tools=[SimpleNamespace(name="test", description=None, input_schema={})], next_cursor=None)
            async def call_tool(self, name, arguments):
                return SimpleNamespace(is_error=False, structured_content={"test": 1}, content=[])
        transport = SDKTransport(FakeSDK())
        self.assertEqual((await transport.list_tools_page())["tools"][0]["input_schema"], {})
        self.assertEqual((await transport.call_tool("test", {}))["structured_content"], {"test": 1})


class ErrorAndCLITests(unittest.TestCase):
    def test_nested_auth_error_is_recognized_without_leaking_text(self):
        error = RuntimeError("test-secret must not appear")
        error.response = SimpleNamespace(status_code=401)
        result = public_error(ExceptionGroup("group", [error]))
        self.assertEqual(result.code, "AUTH_FAILED")
        self.assertNotIn("test-secret", str(result))

    def test_rate_limit_and_timeout(self):
        error = RuntimeError()
        error.response = SimpleNamespace(status_code=429)
        self.assertEqual(public_error(error).code, "RATE_LIMITED")
        self.assertEqual(public_error(TimeoutError()).code, "TIMEOUT")

    def test_transport_import_error_is_actionable_and_redacted(self):
        error = ImportError("test-secret must not appear")
        result = public_error(error)
        self.assertEqual(result.code, "DEPENDENCY_MISSING")
        self.assertIn("uv sync", str(result))
        self.assertNotIn("test-secret", str(result))

    def test_secret_redaction_covers_nested_output(self):
        value = {"Authorization": "test-secret", "body": ["echo test-secret", "Bearer other-secret"]}
        encoded = json.dumps(redact(value, "test-secret"))
        self.assertNotIn("test-secret", encoded)
        self.assertNotIn("other-secret", encoded)

    def test_setup_refuses_noninteractive_input(self):
        with patch("sys.stdin.isatty", return_value=False), redirect_stderr(StringIO()) as output:
            self.assertEqual(main(["setup"]), 2)
        self.assertIn("TERMINAL_REQUIRED", output.getvalue())

    def test_help_requires_no_sdk_or_token(self):
        with redirect_stdout(StringIO()) as output, self.assertRaises(SystemExit) as caught:
            main(["--help"])
        self.assertEqual(caught.exception.code, 0)
        self.assertIn("quote", output.getvalue())

    def test_doctor_does_not_claim_network_verified(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {}, clear=True), redirect_stdout(StringIO()) as output:
            self.assertEqual(main(["doctor"], root=Path(directory)), 2)
        self.assertFalse(json.loads(output.getvalue())["network_checked"])

    def test_argument_file_rejects_array_nan_and_broken_json(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "args.json"
            for text in ["[]", '{"x": NaN}', "{"]:
                path.write_text(text)
                with self.subTest(text=text), self.assertRaises(HacdonaldsError):
                    read_arguments(path)

    def test_cli_blocks_write_before_connecting(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {"MCD_MCP_TOKEN": "test-only-token"}), patch("hacdonalds.cli.connect") as connect, redirect_stderr(StringIO()) as output:
            self.assertEqual(main(["call", "create-order"], root=Path(directory)), 2)
        connect.assert_not_called()
        self.assertIn("TOOL_BLOCKED", output.getvalue())


if __name__ == "__main__":
    unittest.main()
