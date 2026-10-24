"""Deterministic, bounded team planning. All costs are integer fen.

Prices here are search references. A plan becomes presentable as a live quote
only after the workflow checks the official whole-basket calculation.
"""

from dataclasses import dataclass
from itertools import product

from .errors import HacdonaldsError


def integer(value, low, high):
    return type(value) is int and low <= value <= high


def parse_request(data):
    if not isinstance(data, dict) or set(data) - {"store_code", "budget_fen", "crew"}:
        raise HacdonaldsError("INVALID_PLAN_REQUEST", "配餐参数只支持 store_code、budget_fen 和 crew。")
    store = data.get("store_code")
    budget = data.get("budget_fen")
    crew = data.get("crew")
    if not isinstance(store, str) or not store.isascii() or not store.isdigit():
        raise HacdonaldsError("INVALID_PLAN_REQUEST", "store_code 必须来自门店查询结果。")
    if not integer(budget, 1, 100000) or not isinstance(crew, list) or not 1 <= len(crew) <= 10:
        raise HacdonaldsError("INVALID_PLAN_REQUEST", "预算使用 1–100000 整数分，团队人数为 1–10。")
    normalized = []
    for member in crew:
        if not isinstance(member, dict) or set(member) - {"id", "hunger", "drink", "prefer", "exclude_keywords", "avoid"}:
            raise HacdonaldsError("INVALID_PLAN_REQUEST", "成员参数包含不支持的字段。")
        identity = member.get("id")
        hunger = member.get("hunger", 3)
        drink = member.get("drink", "optional")
        if not isinstance(identity, str) or not identity.strip() or len(identity) > 50 or not integer(hunger, 1, 5) or not isinstance(drink, str) or drink not in {"optional", "required", "none"}:
            raise HacdonaldsError("INVALID_PLAN_REQUEST", "成员需要唯一 id、1–5 食量和 optional/required/none 饮品选项。")
        for key in ("prefer", "exclude_keywords", "avoid"):
            values = member.get(key, [])
            if not isinstance(values, list) or len(values) > 10 or any(not isinstance(v, str) or not v.strip() or len(v) > 40 for v in values):
                raise HacdonaldsError("INVALID_PLAN_REQUEST", "偏好、名称排除和禁忌必须是短字符串数组。")
        normalized.append(dict(member, hunger=hunger, drink=drink, prefer=member.get("prefer", []),
                               exclude_keywords=member.get("exclude_keywords", []), avoid=member.get("avoid", [])))
    if len({m["id"] for m in normalized}) != len(normalized):
        raise HacdonaldsError("INVALID_PLAN_REQUEST", "成员 id 不可重复。")
    return {"store_code": store, "budget_fen": budget, "crew": normalized}


def classify(name, category=""):
    """Roles describe meal structure, not ingredients, allergens or nutrition."""
    if name.endswith("酱") or any(word in name for word in ("蘸酱", "调味料", "餐具", "打包袋")):
        return "unsupported"
    if any(word in name for word in ("套餐", "组合", "拼盘", "分享", "随心配", "开心乐园餐", "双人餐")):
        return "unsupported"
    if any(word in name for word in ("堡", "巨无霸", "板烧", "麦香鸡", "麦香鱼", "鸡肉卷")):
        return "main"
    if any(word in name for word in ("可乐", "雪碧", "茶", "咖啡", "美式", "拿铁", "牛奶", "豆浆", "果汁", "柠檬水", "饮料", "朱古力", "特饮")):
        return "drink"
    if any(word in name for word in ("薯条", "薯饼", "鸡块", "鸡翅", "脆汁鸡", "玉米杯", "玉米粒")):
        return "side"
    if category == "饮品":
        return "drink"
    if category == "炸鸡":
        return "side"
    return "unsupported"


def quote_amount(data):
    """The observed official calculate-price data.price is integer fen."""
    amount = data.get("price") if isinstance(data, dict) else None
    if not integer(amount, 0, 10000000):
        raise HacdonaldsError("PRICE_SHAPE_UNKNOWN", "官方核价缺少可识别的整数分 price，不能显示最终金额。")
    return amount


def validate_quote(data, items):
    amount = quote_amount(data)
    lines = data.get("productList")
    actual = {}
    if isinstance(lines, list):
        for line in lines:
            if not isinstance(line, dict) or not isinstance(line.get("productCode"), str) or not integer(line.get("quantity"), 1, 100):
                raise HacdonaldsError("QUOTE_ITEMS_MISMATCH", "官方核价明细的商品或数量无法识别。")
            code = line["productCode"]
            actual[code] = actual.get(code, 0) + line["quantity"]
    expected = {item["productCode"]: item["quantity"] for item in items}
    if not actual or actual != expected:
        raise HacdonaldsError("QUOTE_ITEMS_MISMATCH", "官方核价明细与提交的商品、数量不一致。")
    return amount


def signature(assignments):
    return tuple((a["member_id"], tuple(a["codes"])) for a in assignments)


def basket(assignments):
    counts = {}
    for assignment in assignments:
        for code in assignment["codes"]:
            counts[code] = counts.get(code, 0) + 1
    return [{"productCode": code, "quantity": count} for code, count in sorted(counts.items())]


@dataclass(frozen=True)
class State:
    cost: int
    score: int
    minimum: int
    assignments: tuple


def member_options(member, products, strategy):
    allowed = [p for p in products if not any(word.casefold() in p["name"].casefold() for word in member["exclude_keywords"])]
    mains = [p for p in allowed if p["role"] == "main"]
    sides = [p for p in allowed if p["role"] == "side"]
    drinks = [p for p in allowed if p["role"] == "drink"]
    side_options = sides if member["hunger"] >= 4 else [None] + sides
    drink_options = [None] if member["drink"] == "none" else drinks if member["drink"] == "required" else [None] + drinks
    result = []
    for main, side, drink in product(mains, side_options, drink_options):
        items = [item for item in (main, side, drink) if item]
        codes = tuple(item["code"] for item in items)
        cost = sum(item["reference_price_fen"] for item in items)
        preferred = sum(any(word.casefold() in item["name"].casefold() for item in items) for word in member["prefer"])
        # Portion and variety scores are transparent heuristics, never calories.
        satisfaction = 10 + 8 * preferred + (8 if side else 0) + (4 if drink else 0)
        if strategy == "saver":
            score = -cost
        elif strategy == "balanced":
            score = satisfaction * 100 - cost // 5
        else:
            score = satisfaction * 100 + (10 if side else 0) * 100 + (6 if drink else 0) * 100
        result.append((cost, score, satisfaction, {"member_id": member["id"], "codes": codes}))
    cheapest = sorted(result, key=lambda o: (o[0], -o[1], o[3]["codes"]))[:32]
    preferred = sorted(result, key=lambda o: (-o[1], o[0], o[3]["codes"]))[:32]
    unique = {option[3]["codes"]: option for option in cheapest + preferred}
    return list(unique.values())


def propose(request, products, *, per_strategy=3, max_states=512):
    """Bounded beam search, not a claim of global optimality over the menu."""
    if any(member["avoid"] for member in request["crew"]):
        return {"status": "needs_dietary_verification", "candidates": [],
                "reason": "当前接口记录无法确认配料或过敏原，不能验证 avoid 禁忌；需先取得官方配料确认。不要将其改写成名称排除。"}
    candidates = []
    for strategy in ("saver", "balanced", "full_power"):
        states = [State(0, 0, 100000, ())]
        for member in request["crew"]:
            options = member_options(member, products, strategy)
            if not options:
                states = []
                break
            pool = {}
            for state in states:
                for cost, score, satisfaction, assignment in options:
                    total = state.cost + cost
                    if total > request["budget_fen"]:
                        continue
                    new = State(total, state.score + score, min(state.minimum, satisfaction), state.assignments + (assignment,))
                    key = (total, signature(new.assignments))
                    pool[key] = new
            def rank(state):
                fairness = state.minimum * 100 if strategy == "balanced" else 0
                return (-state.score - fairness, state.cost, signature(state.assignments))
            ranked = sorted(pool.values(), key=rank)
            # Retain inexpensive prefixes as well as high-scoring prefixes so
            # later members do not lose all available budget.
            cheap = sorted(pool.values(), key=lambda s: (s.cost, rank(s)))[:max_states // 2]
            selected = {signature(s.assignments): s for s in ranked[:max_states // 2] + cheap}
            states = list(selected.values())
        seen_baskets = set()
        for state in sorted(states, key=rank) if states else []:
            items = basket(state.assignments)
            key = tuple((item["productCode"], item["quantity"]) for item in items)
            if key in seen_baskets:
                continue
            seen_baskets.add(key)
            candidates.append({"strategy": strategy, "reference_total_fen": state.cost,
                               "assignments": list(state.assignments), "items": items})
            if len(seen_baskets) >= per_strategy:
                break
    diagnostics = []
    minimum = 0
    for member in request["crew"]:
        options = member_options(member, products, "saver")
        if not options:
            diagnostics.append({"member_id": member["id"], "reason": "NO_COMPATIBLE_MAIN_SIDE_OR_DRINK"})
        else:
            minimum += min(option[0] for option in options)
    if not diagnostics and minimum > request["budget_fen"]:
        diagnostics.append({"reason": "REFERENCE_BUDGET_TOO_LOW", "minimum_reference_fen": minimum})
    return {"status": "candidates" if candidates else "no_feasible_reference_plan", "candidates": candidates,
            "diagnostics": diagnostics,
            "reason": "仅在本次采集的单品范围内进行有界搜索，不代表全部菜单的全局最优。"}
