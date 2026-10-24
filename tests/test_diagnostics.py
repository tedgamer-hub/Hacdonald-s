"""Verify SDK error codes and HTTP statuses survive public error mapping."""

import asyncio
import importlib.metadata
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hacdonalds.config import Settings
from hacdonalds.errors import error_metadata, public_error
from hacdonalds.transport import connect
from test_team_check import script

try:
    SDK_AVAILABLE = importlib.metadata.version("mcp").split(".")[0] == "2"
except importlib.metadata.PackageNotFoundError:
    SDK_AVAILABLE = False


@unittest.skipUnless(SDK_AVAILABLE, "MCP SDK 2.x 未安装")
class DiagnosticTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_timeout_without_python_timeout_is_recognized(self):
        from mcp.shared.exceptions import MCPError
        from mcp_types import REQUEST_TIMEOUT
        error = MCPError(REQUEST_TIMEOUT, "Bearer PRIVATE-DIAGNOSTIC-TOKEN")
        error.__suppress_context__ = True
        wrapped = ExceptionGroup("SDK", [error])
        self.assertEqual(public_error(wrapped).code, "TIMEOUT")
        self.assertTrue(any(item.get("rpc_code") == REQUEST_TIMEOUT for item in error_metadata(wrapped)))
        self.assertNotIn("PRIVATE-DIAGNOSTIC-TOKEN", str(public_error(wrapped)) + json.dumps(error_metadata(wrapped)))

    async def test_rpc_disconnect_and_other_errors_remain_distinct(self):
        from mcp.shared.exceptions import MCPError
        self.assertEqual(public_error(MCPError(-32000, "private")).code, "CONNECTION_CLOSED")
        result = public_error(MCPError(-32603, "private"))
        self.assertEqual(result.code, "MCP_RPC_ERROR")
        self.assertIn("-32603", str(result))
        self.assertNotIn("private", str(result))
        details = error_metadata(MCPError(-32603, "Echo PRIVATE-DIAGNOSTIC-TOKEN"), token="PRIVATE-DIAGNOSTIC-TOKEN")
        self.assertNotIn("PRIVATE-DIAGNOSTIC-TOKEN", json.dumps(details))
        self.assertIn("rpc_message", details[0])

    async def test_http_auth_rate_limit_and_server_status_survive_real_sdk(self):
        import httpx2
        real_client = httpx2.AsyncClient
        for status, expected in ((401, "AUTH_FAILED"), (403, "AUTH_FAILED"), (429, "RATE_LIMITED"), (503, "UPSTREAM_HTTP_ERROR")):
            def handler(request):
                return httpx2.Response(status, request=request, text="Bearer PRIVATE-DIAGNOSTIC-TOKEN")
            class LocalHTTP(real_client):
                def __init__(self, **kwargs):
                    super().__init__(**kwargs, transport=httpx2.MockTransport(handler))
            with self.subTest(status=status), patch.object(httpx2, "AsyncClient", LocalHTTP):
                caught = None
                try:
                    async with connect(Settings("PRIVATE-DIAGNOSTIC-TOKEN", 1)):
                        self.fail("HTTP failure unexpectedly opened a session")
                except Exception as error:
                    caught = error
                self.assertIsNotNone(caught)
                public = public_error(caught)
                self.assertEqual(public.code, expected)
                metadata = error_metadata(caught)
                self.assertTrue(any(item.get("http_status") == status for item in metadata))
                self.assertNotIn("PRIVATE-DIAGNOSTIC-TOKEN", str(public) + json.dumps(metadata))

    async def test_cancelled_call_is_checkpointed_instead_of_pending(self):
        class CancelledClient:
            async def call(self, name, arguments):
                raise asyncio.CancelledError("PRIVATE-DIAGNOSTIC-TOKEN")
        report = {"stages": []}
        statuses = []
        client = script.RecordingClient(CancelledClient(), report, lambda: statuses.append(report["stages"][-1]["status"]))
        with self.assertRaises(asyncio.CancelledError):
            await client.call("calculate-price", {})
        stage = report["stages"][0]
        self.assertEqual(statuses, ["pending", "interrupted"])
        self.assertEqual(stage["status"], "interrupted")
        self.assertIn("elapsed_seconds", stage)
        self.assertNotIn("PRIVATE-DIAGNOSTIC-TOKEN", json.dumps(report))


if __name__ == "__main__":
    unittest.main()
