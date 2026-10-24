#!/usr/bin/env python3
"""Checkpoint current store/menu/team pricing and one member revision."""

import argparse
import asyncio
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from hacdonalds.cli import read_arguments
from hacdonalds.client import McDonaldClient
from hacdonalds.config import load_settings
from hacdonalds.errors import HacdonaldsError, public_error
from hacdonalds.live_check import business_data
from hacdonalds.meal_workflow import plan_meals, price_candidates
from hacdonalds.planning import parse_request
from hacdonalds.privacy import redact
from hacdonalds.transport import connect


class RecordingClient:
    def __init__(self, client, report, checkpoint):
        self.client, self.report, self.checkpoint = client, report, checkpoint

    async def call(self, name, arguments):
        stage = {"tool": name, "arguments": arguments, "status": "pending"}
        self.report["stages"].append(stage)
        self.checkpoint()
        try:
            response = await self.client.call(name, arguments)
        except Exception as exc:
            error = public_error(exc)
            stage.update(status="failed", error={"code": error.code, "message": str(error)})
            self.checkpoint()
            raise
        stage.update(status="returned", response=response)
        self.checkpoint()
        return response


def main(argv=None):
    parser = argparse.ArgumentParser(description="验证当前门店、团队配餐及连续修改，逐步保存结果")
    parser.add_argument("--request", type=Path, default=ROOT / "examples/team-request.json")
    parser.add_argument("--store-search", type=Path, default=ROOT / "examples/store-search.json")
    parser.add_argument("--limit", type=int, choices=range(4, 33), default=24, metavar="4..32")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    destination = ROOT / ".local/team-check.json"
    report = {"status": "starting", "purpose": "technical_team_planning_check", "stages": []}
    token = ""

    def checkpoint():
        destination.parent.mkdir(exist_ok=True, mode=0o700)
        fd, filename = tempfile.mkstemp(prefix="team-check-", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(redact(report, token), handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
            os.replace(filename, destination)
        finally:
            Path(filename).unlink(missing_ok=True)

    async def run():
        nonlocal token
        request = parse_request(read_arguments(args.request))
        report["request"] = request
        settings = load_settings(ROOT, timeout=15)
        token = settings.token
        report["status"] = "connecting"
        checkpoint()
        print("正在连接官方 MCP；完整阶段记录将保存在项目本地。", flush=True)
        async with connect(settings) as transport:
            client = RecordingClient(McDonaldClient(transport, timeout_seconds=15), report, checkpoint)
            stores = business_data(await client.call("query-nearby-stores", read_arguments(args.store_search)))
            store = next((s for s in stores if isinstance(s, dict) and s.get("storeCode") == request["store_code"]), None) if isinstance(stores, list) else None
            if store is None or store.get("businessStatus") is not True:
                raise HacdonaldsError("STORE_NOT_OPEN", "本次查询未确认选定餐厅营业，请先重新选择门店；未自动替换。")
            report["store"] = {"code": store["storeCode"], "name": store["storeName"]}
            print(f"测试门店：{store['storeName']}（{store['storeCode']}）", flush=True)
            report["status"] = "baseline"
            report["baseline"] = await plan_meals(client, request, limit=args.limit, progress=lambda s: print(s, flush=True))
            checkpoint()
            if report["baseline"]["status"] != "verified":
                raise HacdonaldsError("NO_VERIFIED_BASELINE", "原始需求未取得已核价方案，请检查记录中的状态和诊断。")
            revised = deepcopy(request)
            revised["crew"][0]["drink"] = "none"
            revised["budget_fen"] = max(1, request["budget_fen"] - 500)
            report.update(status="revision", revised_request=revised)
            checkpoint()
            report["revision"] = await price_candidates(client, revised, report["baseline"]["catalogue"], progress=lambda s: print(s, flush=True))
            checkpoint()
            if report["revision"]["status"] != "verified":
                raise HacdonaldsError("NO_VERIFIED_REVISION", "原始方案已核价，但修改后尚未取得已核价方案。")
        report["status"] = "verified"
        checkpoint()

    try:
        asyncio.run(run())
    except (KeyboardInterrupt, EOFError):
        report["status"] = "cancelled"
        checkpoint()
        print(f"已取消。阶段记录：{destination}")
        return 130
    except Exception as exc:
        error = public_error(exc)
        report.update(status="incomplete", error={"code": error.code, "message": str(error)})
        checkpoint()
        print(f"验证未完成：{error.code}：{error}\n阶段记录：{destination}")
        return 2
    for label, key in (("原始需求", "baseline"), ("连续修改", "revision")):
        for plan in report[key]["plans"]:
            print(f"{label} / {plan['strategy']}：{plan['official_quote']['amount_fen'] / 100:.2f} 元")
    print(f"两轮配餐与官方核价均已完成。完整记录：{destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
