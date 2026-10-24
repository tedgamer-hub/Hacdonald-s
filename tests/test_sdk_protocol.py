"""Runs against the real SDK in memory once dependencies are installed.

This is a protocol integration test with test-only data, never a McDonald's E2E.
"""

import importlib.metadata
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

try:
    SDK_AVAILABLE = importlib.metadata.version("mcp").split(".")[0] == "2"
except importlib.metadata.PackageNotFoundError:
    SDK_AVAILABLE = False


@unittest.skipUnless(SDK_AVAILABLE, "MCP SDK 2.x 未安装：协议集成测试尚未执行")
class SDKProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_discover_validate_and_call_in_memory(self):
        from mcp import Client
        from mcp.server import MCPServer
        from hacdonalds.client import McDonaldClient
        from hacdonalds.transport import SDKTransport

        server = MCPServer("Hacdonalds TEST ONLY")

        @server.tool(name="query-meals")
        def query_meals(test_store_id: str) -> dict:
            return {"test_items": [], "test_store_id": test_store_id}

        async with Client(server) as sdk:
            client = McDonaldClient(SDKTransport(sdk))
            schema = await client.schema("query-meals")
            self.assertIn("test_store_id", schema["input_schema"]["required"])
            result = await client.call("query-meals", {"test_store_id": "TEST-STORE"})
            self.assertEqual(result["tool"], "query-meals")
            self.assertIn("TEST-STORE", str(result["data"]))


if __name__ == "__main__":
    unittest.main()
