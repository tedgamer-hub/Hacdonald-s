"""Discovery, runtime schema validation and a limited set of business tools."""

import asyncio
from datetime import datetime, timezone
import json
from typing import Protocol

from .errors import HacdonaldsError

ALLOWED_TOOLS = frozenset({
    "query-nearby-stores", "query-meals", "query-meal-detail", "calculate-price",
    "query-store-coupons", "list-nutrition-foods",
})


class Transport(Protocol):
    async def list_tools_page(self, cursor=None) -> dict: ...
    async def call_tool(self, name: str, arguments: dict) -> dict: ...


def validate_arguments(schema: dict, arguments: dict):
    if not isinstance(arguments, dict):
        raise HacdonaldsError("INVALID_ARGUMENTS", "工具参数必须是 JSON 对象。")
    # No remote schema resolution: an upstream schema must not trigger another request.
    stack = [schema]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key, value in item.items():
                if key in {"$ref", "$dynamicRef"} and (not isinstance(value, str) or not value.startswith("#")):
                    raise HacdonaldsError("UNSUPPORTED_SCHEMA", "工具 Schema 使用了外部引用，请先检查官方定义。")
                stack.append(value)
        elif isinstance(item, list):
            stack.extend(item)
    try:
        from jsonschema.validators import validator_for
        validator = validator_for(schema)
        validator.check_schema(schema)
        error = next(validator(schema).iter_errors(arguments), None)
        if error is not None:
            path = ".".join(str(part) for part in error.absolute_path) or "<root>"
            raise HacdonaldsError("INVALID_ARGUMENTS", f"参数未通过官方 Schema 校验：{path}（{error.validator}）。请先查看 schema。")
    except ImportError:
        raise HacdonaldsError("DEPENDENCY_MISSING", "缺少 jsonschema，请先安装项目依赖。") from None
    except HacdonaldsError:
        raise
    except Exception:
        raise HacdonaldsError("INVALID_SCHEMA", "无法校验官方工具 Schema，本次没有调用工具。") from None


class McDonaldClient:
    def __init__(self, transport: Transport, *, timeout_seconds: float = 30):
        self.transport = transport
        self.timeout_seconds = timeout_seconds
        self._tools: dict[str, dict] | None = None

    async def discover(self) -> list[dict]:
        tools = {}
        cursor = None
        seen = set()
        async with asyncio.timeout(self.timeout_seconds):
            for _ in range(100):
                page = await self.transport.list_tools_page(cursor)
                for tool in page["tools"]:
                    tools[tool["name"]] = tool
                cursor = page.get("next_cursor")
                if cursor is None:
                    self._tools = tools
                    return [dict(tool, enabled=name in ALLOWED_TOOLS) for name, tool in tools.items()]
                if cursor in seen:
                    raise HacdonaldsError("PAGINATION_ERROR", "工具列表出现重复分页标记。")
                seen.add(cursor)
        raise HacdonaldsError("PAGINATION_ERROR", "工具分页超过安全上限。")

    async def schema(self, name: str) -> dict:
        if self._tools is None:
            await self.discover()
        if name not in self._tools:
            raise HacdonaldsError("TOOL_UNAVAILABLE", "当前连接没有提供该工具，请先查看 tools 列表。")
        return self._tools[name]

    async def call(self, name: str, arguments: dict) -> dict:
        if name not in ALLOWED_TOOLS:
            raise HacdonaldsError("TOOL_BLOCKED", "首版仅开放门店、菜单、详情、核价、券和营养查询。")
        tool = await self.schema(name)
        validate_arguments(tool["input_schema"], arguments)
        async with asyncio.timeout(self.timeout_seconds):
            result = await self.transport.call_tool(name, arguments)
        if result.get("is_error") is not False:
            raise HacdonaldsError("TOOL_FAILED", "官方工具报告执行失败，请核对参数、门店和商品状态。")
        content = result.get("content", [])
        data = result.get("structured_content")
        encoding = "structured"
        if data is None:
            texts = [block["text"] for block in content if block.get("type") == "text"]
            encoding = "text"
            if len(texts) == 1:
                try:
                    data = json.loads(texts[0])
                    encoding = "json_text"
                except (ValueError, TypeError):
                    data = texts[0]
            else:
                data = texts
        return {"source": "live", "tool": name, "retrieved_at": datetime.now(timezone.utc).isoformat(),
                "encoding": encoding, "data": data, "content": content}
