"""Small JSON CLI for configuring and inspecting the official service."""

import argparse
import asyncio
import getpass
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
import warnings

from .adapter import McDonaldAdapter, OPERATIONS
from .client import McDonaldClient
from .config import ENDPOINT, PROJECT_ROOT, dependency_status, load_settings, save_token
from .errors import HacdonaldsError, public_error
from .privacy import redact
from .transport import connect


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Hacdonald's — 团队预算配餐与麦当劳 MCP 客户端")
    result.add_argument("--timeout", type=float, default=30, help="单步超时秒数，范围 1–120")
    commands = result.add_subparsers(dest="command", required=True)
    commands.add_parser("setup", help="隐藏输入 Token 并保存在项目本地")
    commands.add_parser("doctor", help="检查配置和依赖，不连接网络")
    commands.add_parser("tools", help="连接官方 MCP，列出工具及开放状态")
    schema = commands.add_parser("schema", help="读取工具的实际参数 Schema")
    schema.add_argument("tool", help="官方工具名，或 stores/menu/detail/quote 等别名")
    call = commands.add_parser("call", help="校验参数后调用首版允许的工具")
    call.add_argument("tool")
    call.add_argument("--args-file", type=Path, help="参数 JSON 文件；省略时使用空对象")
    for operation in OPERATIONS:
        command = commands.add_parser(operation, help=f"调用 {OPERATIONS[operation]}")
        command.add_argument("--args-file", type=Path)
    commands.add_parser("session", help="保持同一 MCP 会话，交互查看 Schema 与调用查询工具")
    plan = commands.add_parser("plan", help="按成员需求生成预算内方案并进行官方核价")
    plan.add_argument("--request", type=Path, required=True, help="配餐需求 JSON 文件")
    plan.add_argument("--limit", type=int, default=24, choices=range(4, 33), metavar="4..32", help="候选商品采集上限，默认 24")
    plan.add_argument("--output", type=Path, help="将完整结果以私有文件保存到指定路径")
    return result


def print_json(value, *, token: str = "", stream=None):
    print(json.dumps(redact(value, token), ensure_ascii=False, indent=2, allow_nan=False), file=stream or sys.stdout)


def read_arguments(path: Path | None) -> dict:
    if path is None:
        return {}
    try:
        def invalid_constant(_):
            raise ValueError("non-finite JSON number")
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=invalid_constant)
        if not isinstance(data, dict):
            raise ValueError("expected object")
        return data
    except (OSError, ValueError):
        raise HacdonaldsError("INVALID_ARGUMENTS_FILE", "参数文件必须存在、采用 UTF-8 编码且包含合法 JSON 对象。") from None


async def interactive(client: McDonaldClient, token: str):
    if not sys.stdin.isatty():
        raise HacdonaldsError("TERMINAL_REQUIRED", "session 需要交互终端。自动化调用请使用 call 和参数文件。")
    print("保持同一 MCP 会话。输入 tools、schema <工具名>、call <工具名> <JSON> 或 exit。", file=sys.stderr)
    while True:
        try:
            line = await asyncio.to_thread(input, "hacdonalds> ")
        except EOFError:
            return
        parts = line.strip().split(maxsplit=2)
        if not parts:
            continue
        if parts[0] in {"exit", "quit"}:
            return
        try:
            if parts == ["tools"]:
                result = await client.discover()
            elif len(parts) == 2 and parts[0] == "schema":
                result = await client.schema(OPERATIONS.get(parts[1], parts[1]))
            elif len(parts) == 3 and parts[0] == "call":
                try:
                    arguments = json.loads(parts[2], parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
                except ValueError:
                    raise HacdonaldsError("INVALID_ARGUMENTS", "call 后请提供合法 JSON 参数对象。") from None
                result = await client.call(OPERATIONS.get(parts[1], parts[1]), arguments)
            else:
                raise HacdonaldsError("INVALID_COMMAND", "请输入 tools、schema <工具名>、call <工具名> <JSON> 或 exit。")
            print_json(result, token=token)
        except HacdonaldsError as error:
            print_json({"ok": False, "code": error.code, "message": str(error)}, token=token, stream=sys.stderr)


async def run_live(args, settings):
    # Input errors and disabled tools fail before opening a network connection.
    from .client import ALLOWED_TOOLS
    arguments = None
    name = None
    request = None
    if args.command == "plan":
        from .planning import parse_request
        request = parse_request(read_arguments(args.request))
        if any(member["avoid"] for member in request["crew"]):
            from .planning import propose
            return dict(propose(request, []), request=request, plans=[])
    if args.command == "call" or args.command in OPERATIONS:
        name = OPERATIONS.get(args.tool, args.tool) if args.command == "call" else OPERATIONS[args.command]
        if name not in ALLOWED_TOOLS:
            raise HacdonaldsError("TOOL_BLOCKED", "首版不开放该工具。")
        arguments = read_arguments(args.args_file)
    async with connect(settings) as transport:
        client = McDonaldClient(transport, timeout_seconds=settings.timeout_seconds)
        if args.command == "plan":
            from .meal_workflow import plan_meals
            return await plan_meals(client, request, limit=args.limit, progress=lambda message: print(message, file=sys.stderr, flush=True))
        if args.command == "tools":
            return {"endpoint": ENDPOINT, "source": "live", "tools": await client.discover()}
        if args.command == "schema":
            return await client.schema(OPERATIONS.get(args.tool, args.tool))
        if args.command == "session":
            await interactive(client, settings.token)
            return {"ok": True, "message": "会话已关闭。"}
        if args.command in OPERATIONS:
            return await McDonaldAdapter(client).execute(args.command, arguments)
        return await client.call(name, arguments)


def main(argv=None, *, root: Path = PROJECT_ROOT) -> int:
    args = parser().parse_args(argv)
    token = ""
    # SDK diagnostics are not forwarded: upstream errors could include private input.
    for name in ("mcp", "httpx", "httpcore", "httpx2"):
        logger = logging.getLogger(name)
        logger.addHandler(logging.NullHandler())
        logger.propagate = False
        logger.setLevel(logging.CRITICAL + 1)
    try:
        if args.command == "setup":
            if not sys.stdin.isatty():
                raise HacdonaldsError("TERMINAL_REQUIRED", "请在你自己的交互终端运行 setup，Token 不应通过聊天或命令参数传入。")
            with warnings.catch_warnings():
                warnings.simplefilter("error", getpass.GetPassWarning)
                try:
                    value = getpass.getpass("粘贴麦当劳 MCP Token（输入不可见）：")
                except getpass.GetPassWarning:
                    raise HacdonaldsError("HIDDEN_INPUT_UNAVAILABLE", "当前终端不支持隐藏输入，已停止配置。") from None
            path = save_token(value, root)
            print_json({"ok": True, "message": "Token 已保存在项目本地。", "file": str(path)})
            return 0
        if args.command == "doctor":
            try:
                load_settings(root, timeout=args.timeout)
                config = {"configured": True}
            except HacdonaldsError as error:
                config = {"configured": False, "code": error.code, "message": str(error)}
            dependencies = dependency_status()
            ready = config["configured"] and all(item["compatible"] for item in dependencies.values())
            print_json({"ready": ready, "network_checked": False, "endpoint": ENDPOINT,
                        "credentials": config, "dependencies": dependencies})
            return 0 if ready else 2
        settings = load_settings(root, timeout=args.timeout)
        token = settings.token
        result = asyncio.run(run_live(args, settings))
        if args.command == "plan" and args.output is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            fd, filename = tempfile.mkstemp(prefix="plan-", dir=args.output.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(redact(result, token), handle, ensure_ascii=False, indent=2, allow_nan=False)
                    handle.write("\n")
                os.replace(filename, args.output)
            finally:
                Path(filename).unlink(missing_ok=True)
        print_json(result, token=token)
        return 2 if args.command == "plan" and result["status"] != "verified" else 0
    except (KeyboardInterrupt, EOFError):
        print_json({"ok": False, "code": "CANCELLED", "message": "操作已取消。"}, stream=sys.stderr)
        return 130
    except Exception as exc:
        error = public_error(exc)
        print_json({"ok": False, "code": error.code, "message": str(error)}, token=token, stream=sys.stderr)
        return 2
