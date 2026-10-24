"""The single boundary that imports the official MCP Python SDK (2.x)."""

from contextlib import asynccontextmanager
import importlib.metadata

from .config import ENDPOINT, Settings
from .errors import HacdonaldsError


class SDKTransport:
    def __init__(self, client):
        self.client = client

    async def list_tools_page(self, cursor=None) -> dict:
        page = await self.client.list_tools(cursor=cursor)
        return {
            "tools": [{"name": tool.name, "description": tool.description or "",
                       "input_schema": tool.input_schema} for tool in page.tools],
            "next_cursor": page.next_cursor,
        }

    async def call_tool(self, name: str, arguments: dict) -> dict:
        result = await self.client.call_tool(name, arguments)
        return {
            "is_error": result.is_error,
            "structured_content": result.structured_content,
            "content": [block.model_dump(mode="json", by_alias=True) for block in result.content],
        }


@asynccontextmanager
async def connect(settings: Settings):
    try:
        version = importlib.metadata.version("mcp")
        if int(version.split(".")[0]) != 2:
            raise HacdonaldsError("SDK_VERSION", "本项目使用 MCP Python SDK 2.x，请按 README 安装依赖。")
        import httpx2
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
    except (ImportError, importlib.metadata.PackageNotFoundError):
        raise HacdonaldsError("DEPENDENCY_MISSING", "缺少 MCP 依赖，请先运行 uv sync。") from None
    async with httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {settings.token}"},
        timeout=httpx2.Timeout(settings.timeout_seconds),
        follow_redirects=False,
    ) as http_client:
        transport = streamable_http_client(ENDPOINT, http_client=http_client)
        async with Client(transport, read_timeout_seconds=settings.timeout_seconds) as client:
            yield SDKTransport(client)
