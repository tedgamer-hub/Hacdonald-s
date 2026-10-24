#!/usr/bin/env python3
"""Run a one-shot menu/detail/quote check; save every stage for inspection."""

import asyncio
import json
import logging
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from hacdonalds.client import McDonaldClient
from hacdonalds.config import load_settings
from hacdonalds.errors import HacdonaldsError, public_error
from hacdonalds.live_check import business_data, verify
from hacdonalds.privacy import redact
from hacdonalds.transport import connect


def main():
    logging.disable(logging.CRITICAL)
    report = {"status": "starting", "purpose": "technical_query_and_quote_test"}
    token = ""
    destination = ROOT / ".local" / "live-check.json"

    def checkpoint(state):
        destination.parent.mkdir(exist_ok=True, mode=0o700)
        fd, name = tempfile.mkstemp(prefix="live-check-", dir=destination.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(redact(state, token), handle, ensure_ascii=False, indent=2, allow_nan=False)
                handle.write("\n")
            os.replace(name, destination)
        finally:
            Path(name).unlink(missing_ok=True)

    async def run():
        settings = load_settings(ROOT, timeout=30)
        nonlocal token
        token = settings.token
        saved = json.loads((ROOT / ".local" / "stores-live.json").read_text(encoding="utf-8"))
        stores = business_data(saved)
        if not isinstance(stores, list):
            raise HacdonaldsError("STORES_SHAPE_UNKNOWN", "门店数据格式尚未识别。")
        store = next((item for item in stores if item.get("businessStatus") is True), None)
        if store is None:
            raise HacdonaldsError("NO_OPEN_STORE", "保存的门店结果中没有营业餐厅，请重新查询。")
        report["store_lookup"] = {"source": "saved_live_response", "retrieved_at": saved.get("retrieved_at")}
        print(f"测试门店：{store['storeName']}（{store['storeCode']}）", flush=True)
        print("正在查询菜单、餐品详情和核价，完整记录将保存到本地。", flush=True)
        async with connect(settings) as transport:
            await verify(McDonaldClient(transport), store, report, checkpoint)

    try:
        asyncio.run(run())
    except (KeyboardInterrupt, EOFError):
        report.update(status="cancelled")
        checkpoint(report)
        print("验证已取消，已保存阶段记录。")
        return 130
    except Exception as exc:
        error = public_error(exc)
        report.update(status="incomplete", error={"code": error.code, "message": str(error)})
        checkpoint(report)
        print(f"验证未完成：{error.code}：{error}。阶段记录：{destination}")
        return 2
    print(f"菜单、详情、核价调用通过。完整核价结果：{destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
