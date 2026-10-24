"""Bounded query/detail/quote smoke test using discovered tool schemas.

Menu response selection is deliberately conservative and may stop on an unknown
shape. Every attempted code comes from the menu; no identifier or price is made up.
"""

from .errors import HacdonaldsError


def business_data(response: dict):
    body = response.get("data")
    if not isinstance(body, dict) or body.get("success") is not True:
        raise HacdonaldsError("BUSINESS_RESULT_UNVERIFIED", "上游未返回已识别的业务成功状态，请检查本地验证记录。")
    if body.get("data") is None:
        raise HacdonaldsError("BUSINESS_DATA_MISSING", "上游业务数据为空，无法继续验证。")
    return body["data"]


def menu_candidates(menu) -> list[dict]:
    """Read category meal codes even when the official menu omits names."""
    found = []
    if isinstance(menu, dict) and isinstance(menu.get("categories"), list):
        for category in menu["categories"]:
            if not isinstance(category, dict) or not isinstance(category.get("meals"), list):
                continue
            for meal in category["meals"]:
                if not isinstance(meal, dict):
                    continue
                code = meal.get("code")
                if isinstance(code, str) and code.strip():
                    candidate = {"code": code}
                    name = meal.get("name") or meal.get("productName")
                    if isinstance(name, str) and name.strip():
                        candidate["name"] = name
                    tags = meal.get("tags")
                    candidate["tags"] = [tag for tag in tags if isinstance(tag, str)] if isinstance(tags, list) else []
                    found.append(candidate)
    # For the observed category shape, only meals are products. Do not scan
    # category identifiers or unrelated metadata as fallback candidates.
    pending = [] if isinstance(menu, dict) and isinstance(menu.get("categories"), list) else [(menu, False)]
    while pending:
        item, visited = pending.pop()
        if isinstance(item, list):
            pending.extend((value, False) for value in reversed(item))
        elif isinstance(item, dict):
            if not visited:
                pending.append((item, True))
                pending.extend((value, False) for value in reversed(list(item.values()))
                               if isinstance(value, (list, dict)))
            else:
                code = item.get("code") or item.get("productCode")
                name = item.get("name") or item.get("productName")
                if isinstance(code, str) and code.strip() and isinstance(name, str) and name.strip():
                    found.append({"code": code, "name": name})
    seen = set()
    unique = []
    for item in found:
        if item["code"] not in seen:
            seen.add(item["code"])
            unique.append(item)
    # Official 单品 tags take precedence; a category name is never a meal name.
    return sorted(unique, key=lambda item: (
        "单品" not in item.get("tags", []),
        any(word in item.get("name", "") for word in ("套餐", "组合", "分享", "任选", "双人", "全家")),
    ))


async def verify(client, store: dict, report: dict, checkpoint, *, max_candidates: int = 5):
    if not 1 <= max_candidates <= 5:
        raise HacdonaldsError("INVALID_LIMIT", "验证最多尝试 5 个菜单候选。")
    context = {"storeCode": store["storeCode"], "beType": 1, "orderType": 1}
    report.update(status="running", store={"storeCode": store["storeCode"], "storeName": store["storeName"]}, stages=[])

    async def call(name, arguments):
        stage = {"tool": name, "arguments": arguments}
        report["stages"].append(stage)
        checkpoint(report)
        response = await client.call(name, arguments)
        stage["response"] = response
        checkpoint(report)
        return business_data(response)

    candidates = menu_candidates(await call("query-meals", context))
    if not candidates:
        raise HacdonaldsError("MENU_SHAPE_UNKNOWN", "菜单已保存，但尚未识别可验证的商品编码，请检查实际菜单结构。")
    for candidate in candidates[:max_candidates]:
        try:
            await call("query-meal-detail", dict(context, code=candidate["code"]))
            # Default single-item quote. Custom combos requiring roundList will
            # be rejected upstream; this test never invents combo selections.
            await call("calculate-price", dict(context, items=[{"productCode": candidate["code"], "quantity": 1}]))
        except HacdonaldsError as error:
            if error.code not in {"BUSINESS_RESULT_UNVERIFIED", "BUSINESS_DATA_MISSING", "TOOL_FAILED"}:
                raise
            report["stages"][-1]["rejected"] = {"code": error.code, "message": str(error)}
            checkpoint(report)
            continue
        report.update(status="verified", product=candidate, price_interpretation="raw_official_payload")
        checkpoint(report)
        return report
    raise HacdonaldsError("NO_VERIFIED_QUOTE", "候选均未完成详情和核价，原始返回已保存；需要按实际套餐结构继续适配。")
