"""Observed menu/detail/price normalization and bounded live planning."""

from datetime import datetime, timezone

from .errors import HacdonaldsError
from .live_check import business_data
from .planning import classify, parse_request, propose, validate_quote


def menu_selection(menu, limit=24):
    if not isinstance(menu, dict) or not isinstance(menu.get("categories"), list):
        raise HacdonaldsError("MENU_SHAPE_UNKNOWN", "菜单缺少 categories，无法建立餐品集。")
    groups = {"main": [], "side": [], "drink": []}
    for category in menu["categories"]:
        if not isinstance(category, dict):
            continue
        name = category.get("name", "")
        if not isinstance(name, str):
            continue
        role = "main" if any(w in name for w in ("汉堡", "肉堡", "厚牛堡")) else "drink" if name == "饮品" else "side" if "小食甜品" in name or name == "炸鸡" else None
        if role is None or not isinstance(category.get("meals"), list):
            continue
        for meal in category["meals"]:
            if not isinstance(meal, dict) or not isinstance(meal.get("code"), str) or not meal["code"].strip():
                continue
            tags = meal.get("tags", [])
            if role == "main" and (not isinstance(tags, list) or "单品" not in tags):
                continue
            groups[role].append({"code": meal["code"], "menu_category": name})
    # Allocate half to mains, a quarter each to sides/drinks. Interleave side
    # and drink queries early; selection is bounded even on very large menus.
    quotas = {"main": limit // 2, "side": limit // 4, "drink": limit - limit // 2 - limit // 4}
    selected, seen = [], set()
    while len(selected) < limit:
        added = False
        for role in groups:
            if quotas[role] <= 0:
                continue
            while groups[role]:
                item = groups[role].pop(0)
                if item["code"] not in seen:
                    selected.append(item)
                    seen.add(item["code"])
                    quotas[role] -= 1
                    added = True
                    break
        if not added:
            break
    return selected


async def collect_catalogue(client, store_code, *, limit=24, progress=lambda _: None):
    if type(limit) is not int or not 4 <= limit <= 32:
        raise HacdonaldsError("INVALID_LIMIT", "每次采集 4–32 个候选商品。")
    context = {"storeCode": store_code, "orderType": 1, "beType": 1}
    response = await client.call("query-meals", context)
    selected = menu_selection(business_data(response), limit)
    catalogue = {"source": "live", "store_code": store_code, "context": context,
                 "retrieved_at": response["retrieved_at"], "products": [], "skipped": [],
                 "scope": "bounded_single_items", "attempted_codes": [p["code"] for p in selected]}
    for index, item in enumerate(selected, 1):
        code = item["code"]
        detail = None
        progress(f"餐品采集 {index}/{len(selected)}")
        try:
            detail_response = await client.call("query-meal-detail", dict(context, code=code))
            detail = business_data(detail_response)
            if not isinstance(detail, dict) or detail.get("code") != code or not isinstance(detail.get("name"), str) or not detail["name"].strip():
                raise HacdonaldsError("DETAIL_SHAPE_UNKNOWN", "餐品详情名称或编码不匹配。")
            rounds = detail.get("rounds")
            if not isinstance(rounds, list) or rounds:
                raise HacdonaldsError("COMBO_NOT_SUPPORTED", "套餐子项尚未适配，本次跳过。")
            role = classify(detail["name"], item["menu_category"])
            if role == "unsupported":
                raise HacdonaldsError("ROLE_UNSUPPORTED", "本次仅规划已识别的主食、小食和饮品单品。")
            items = [{"productCode": code, "quantity": 1}]
            priced = await client.call("calculate-price", dict(context, items=items))
            amount = validate_quote(business_data(priced), items)
            catalogue["products"].append(dict(item, name=detail["name"], role=role, reference_price_fen=amount,
                support_modify=detail.get("supportModify") is True, default_configuration=True,
                detail_retrieved_at=detail_response["retrieved_at"], price_retrieved_at=priced["retrieved_at"],
                dietary_verified=False))
        except HacdonaldsError as error:
            if error.code not in {"TOOL_FAILED", "BUSINESS_RESULT_UNVERIFIED", "BUSINESS_DATA_MISSING", "DETAIL_SHAPE_UNKNOWN", "COMBO_NOT_SUPPORTED", "ROLE_UNSUPPORTED", "PRICE_SHAPE_UNKNOWN", "QUOTE_ITEMS_MISMATCH"}:
                raise
            skipped = {"code": code, "reason": error.code}
            if isinstance(detail, dict) and isinstance(detail.get("name"), str):
                skipped["name"] = detail["name"]
            catalogue["skipped"].append(skipped)
    return catalogue


async def plan_meals(client, raw_request, *, limit=24, progress=lambda _: None):
    request = parse_request(raw_request)
    if any(member["avoid"] for member in request["crew"]):
        result = propose(request, [])
        return dict(result, request=request, plans=[])
    catalogue = await collect_catalogue(client, request["store_code"], limit=limit, progress=progress)
    return await price_candidates(client, request, catalogue, progress=progress)


async def price_candidates(client, request, catalogue, *, progress=lambda _: None):
    """Use collected reference costs for search, then freshly quote baskets."""
    request = parse_request(request)
    expected = {"storeCode": request["store_code"], "orderType": 1, "beType": 1}
    if catalogue.get("store_code") != request["store_code"] or catalogue.get("context") != expected:
        raise HacdonaldsError("CONTEXT_MISMATCH", "餐品集与配餐请求的门店或取餐方式不一致。")
    proposed = propose(request, catalogue["products"])
    result = {"status": proposed["status"], "request": request, "catalogue": catalogue, "plans": [],
              "rejected": [], "diagnostics": proposed.get("diagnostics", []), "search_note": proposed["reason"], "quoted_at": None}
    quote_cache = {}
    by_code = {p["code"]: p for p in catalogue["products"]}
    seen_strategies = set()
    seen_baskets = set()
    for candidate in proposed["candidates"]:
        strategy = candidate["strategy"]
        if strategy in seen_strategies:
            continue
        key = tuple((item["productCode"], item["quantity"]) for item in candidate["items"])
        if key not in quote_cache:
            progress(f"方案核价：{strategy}")
            try:
                response = await client.call("calculate-price", dict(catalogue["context"], items=candidate["items"]))
                body = business_data(response)
                quote_cache[key] = {"amount_fen": validate_quote(body, candidate["items"]), "retrieved_at": response["retrieved_at"], "data": body}
            except HacdonaldsError as error:
                if error.code not in {"TOOL_FAILED", "BUSINESS_RESULT_UNVERIFIED", "BUSINESS_DATA_MISSING", "PRICE_SHAPE_UNKNOWN", "QUOTE_ITEMS_MISMATCH"}:
                    raise
                quote_cache[key] = {"error": error.code}
        quote = quote_cache[key]
        if "error" in quote or quote["amount_fen"] > request["budget_fen"]:
            result["rejected"].append({"strategy": strategy, "reason": quote.get("error", "OFFICIAL_PRICE_OVER_BUDGET")})
            continue
        # Different strategies may converge to the same basket. Do not present
        # identical food as three separate alternatives.
        if key in seen_baskets:
            continue
        seen_baskets.add(key)
        seen_strategies.add(strategy)
        assignments = []
        for assignment in candidate["assignments"]:
            assignments.append({"member_id": assignment["member_id"], "products": [
                {"code": code, "name": by_code[code]["name"], "support_modify": by_code[code]["support_modify"]} for code in assignment["codes"]]})
        result["plans"].append(dict(candidate, assignments=assignments, official_quote=quote,
                                     remaining_budget_fen=request["budget_fen"] - quote["amount_fen"]))
    result["status"] = "verified" if result["plans"] else "no_verified_plan"
    result["quoted_at"] = datetime.now(timezone.utc).isoformat()
    return result
