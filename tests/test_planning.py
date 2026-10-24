"""Synthetic prices test constraints and quote reconciliation, never real prices."""

import asyncio
from copy import deepcopy
from pathlib import Path
import random
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hacdonalds.errors import HacdonaldsError
from hacdonalds.planning import basket, classify, parse_request, propose, validate_quote
from hacdonalds.meal_workflow import collect_catalogue, menu_selection, plan_meals, price_candidates


PRODUCTS = [
    {"code": "A", "name": "测试鸡肉汉堡", "role": "main", "reference_price_fen": 1000},
    {"code": "B", "name": "测试牛肉汉堡", "role": "main", "reference_price_fen": 1200},
    {"code": "S", "name": "测试薯条", "role": "side", "reference_price_fen": 500},
    {"code": "D", "name": "测试可乐", "role": "drink", "reference_price_fen": 300},
]


def request(budget=5000):
    return parse_request({"store_code": "1234567", "budget_fen": budget, "crew": [
        {"id": "one", "hunger": 4, "drink": "none", "exclude_keywords": ["牛肉"]},
        {"id": "two", "drink": "required", "prefer": ["牛肉"]},
    ]})


class PlannerTests(unittest.TestCase):
    def test_constraints_and_no_float_money(self):
        result = propose(request(), PRODUCTS)
        self.assertTrue(result["candidates"])
        for plan in result["candidates"]:
            self.assertLessEqual(plan["reference_total_fen"], 5000)
            self.assertEqual(type(plan["reference_total_fen"]), int)
            one, two = plan["assignments"]
            self.assertNotIn("B", one["codes"])
            self.assertNotIn("D", one["codes"])
            self.assertIn("S", one["codes"])
            self.assertIn("D", two["codes"])
            self.assertEqual(basket(plan["assignments"]), plan["items"])

    def test_insufficient_budget_does_not_relax_constraints(self):
        result = propose(request(100), PRODUCTS)
        self.assertFalse(result["candidates"])
        self.assertEqual(result["status"], "no_feasible_reference_plan")

    def test_dietary_unknown_is_not_name_filter(self):
        data = request()
        data["crew"][0]["avoid"] = ["beef"]
        result = propose(data, PRODUCTS)
        self.assertEqual(result["status"], "needs_dietary_verification")
        self.assertFalse(result["candidates"])

    def test_missing_drink_and_side_never_substitutes_another_role(self):
        result = propose(request(), PRODUCTS[:2])
        self.assertFalse(result["candidates"])

    def test_20_fixed_requests_preserve_hard_constraints(self):
        rng = random.Random(19)
        for _ in range(20):
            data = request(rng.randint(30, 90) * 100)
            data["crew"][1]["hunger"] = rng.randint(1, 5)
            for plan in propose(data, PRODUCTS)["candidates"]:
                self.assertLessEqual(plan["reference_total_fen"], data["budget_fen"])
                for member, assignment in zip(data["crew"], plan["assignments"]):
                    foods = [p for p in PRODUCTS if p["code"] in assignment["codes"]]
                    self.assertEqual(sum(p["role"] == "main" for p in foods), 1)
                    self.assertTrue(all(not any(k in p["name"] for k in member["exclude_keywords"]) for p in foods))

    def test_invalid_requests_reject_before_network(self):
        for edit in ({"budget_fen": 1.5}, {"budget_fen": True}, {"store_code": "invalid"}, {"crew": []}, {"surprise": 1}):
            with self.subTest(edit=edit), self.assertRaises(HacdonaldsError):
                parse_request(dict(request(), **edit))
        for edit in ({"drink": []}, {"hunger": False}, {"prefer": "鸡"}, {"avoid": [""]}):
            data = request()
            data["crew"][0].update(edit)
            with self.subTest(edit=edit), self.assertRaises(HacdonaldsError):
                parse_request(data)

    def test_quote_requires_exact_product_quantities(self):
        wanted = [{"productCode": "A", "quantity": 2}]
        self.assertEqual(validate_quote({"price": 1999, "productList": wanted}, wanted), 1999)
        for body in ({"price": True, "productList": wanted}, {"price": 1999}, {"price": 1999, "productList": [{"productCode": "A", "quantity": 1}]}):
            with self.assertRaises(HacdonaldsError):
                validate_quote(body, wanted)

    def test_roles_do_not_treat_bundles_as_single_items(self):
        self.assertEqual(classify("测试汉堡套餐"), "unsupported")
        self.assertEqual(classify("测试鸡肉汉堡"), "main")
        self.assertEqual(classify("测试可乐"), "drink")
        self.assertEqual(classify("测试薯条"), "side")
        self.assertEqual(classify("测试吉士堡"), "main")
        self.assertEqual(classify("测试新口味", "炸鸡"), "side")
        self.assertEqual(classify("测试特调", "饮品"), "drink")
        self.assertEqual(classify("测试甜品", "小食甜品"), "unsupported")
        self.assertEqual(classify("测试牧场酱", "炸鸡"), "unsupported")
        self.assertEqual(classify("测试蘸酱", "炸鸡"), "unsupported")


class FakeClient:
    def __init__(self, *, surcharge=0, mismatched=False, failure=False):
        self.calls = []
        self.surcharge = surcharge
        self.mismatched = mismatched
        self.failure = failure

    async def call(self, name, arguments):
        self.calls.append((name, deepcopy(arguments)))
        if name == "query-meals":
            data = {"categories": [
                {"name": "鸡肉汉堡", "meals": [{"code": p["code"], "tags": ["单品"]} for p in PRODUCTS if p["role"] == "main"]},
                {"name": "小食甜品", "meals": [{"code": "S"}]},
                {"name": "饮品", "meals": [{"code": "D"}]},
            ]}
        elif name == "query-meal-detail":
            p = next(p for p in PRODUCTS if p["code"] == arguments["code"])
            data = {"code": p["code"], "name": p["name"], "rounds": [], "supportModify": False}
        else:
            items = arguments["items"]
            amount = sum(next(p["reference_price_fen"] for p in PRODUCTS if p["code"] == i["productCode"]) * i["quantity"] for i in items)
            team = sum(i["quantity"] for i in items) > 1
            if team and self.failure:
                return {"data": {"success": False, "data": None}}
            data = {"price": amount + (self.surcharge if team else 0), "productList": [] if team and self.mismatched else items}
        return {"source": "live", "retrieved_at": "2026-01-01T00:00:00+00:00", "data": {"success": True, "data": data}}


class WorkflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_end_to_end_official_amount_overrides_reference(self):
        client = FakeClient(surcharge=-123)
        result = await plan_meals(client, request(), limit=8)
        self.assertEqual(result["status"], "verified")
        self.assertTrue(result["plans"])
        keys = []
        for plan in result["plans"]:
            self.assertEqual(plan["official_quote"]["amount_fen"], plan["reference_total_fen"] - 123)
            keys.append(tuple((i["productCode"], i["quantity"]) for i in plan["items"]))
        self.assertEqual(len(keys), len(set(keys)))
        self.assertTrue(all(c[1]["storeCode"] == "1234567" and "beCode" not in c[1] for c in client.calls))
        self.assertTrue(all(c[0] in {"query-meals", "query-meal-detail", "calculate-price"} for c in client.calls))

    async def test_over_budget_quotes_never_presented(self):
        result = await plan_meals(FakeClient(surcharge=999999), request(), limit=8)
        self.assertEqual(result["status"], "no_verified_plan")
        self.assertFalse(result["plans"])
        self.assertTrue(result["rejected"])

    async def test_failed_and_mismatched_quotes_never_presented(self):
        for client in (FakeClient(failure=True), FakeClient(mismatched=True)):
            result = await plan_meals(client, request(), limit=8)
            self.assertEqual(result["status"], "no_verified_plan")
            self.assertFalse(result["plans"])

    async def test_dietary_request_makes_no_calls(self):
        client = FakeClient()
        data = request()
        data["crew"][0]["avoid"] = ["allergy"]
        result = await plan_meals(client, data)
        self.assertEqual(result["status"], "needs_dietary_verification")
        self.assertFalse(client.calls)

    async def test_catalogue_query_count_is_bounded(self):
        client = FakeClient()
        result = await collect_catalogue(client, "1234567", limit=4)
        self.assertLessEqual(len(client.calls), 9)
        self.assertEqual(len(result["products"]), 4)
        self.assertTrue(all(not p["dietary_verified"] for p in result["products"]))

    async def test_reprice_rejects_another_store(self):
        client = FakeClient()
        catalogue = await collect_catalogue(client, "9999999", limit=4)
        count = len(client.calls)
        with self.assertRaises(HacdonaldsError) as caught:
            await price_candidates(client, request(), catalogue)
        self.assertEqual(caught.exception.code, "CONTEXT_MISMATCH")
        self.assertEqual(len(client.calls), count)

    async def test_basket_quote_calls_are_unique_and_bounded(self):
        client = FakeClient()
        await plan_meals(client, request(), limit=8)
        baskets = [tuple((i["productCode"], i["quantity"]) for i in a["items"]) for n,a in client.calls if n == "calculate-price" and sum(i["quantity"] for i in a["items"]) > 1]
        self.assertLessEqual(len(baskets), 9)
        self.assertEqual(len(baskets), len(set(baskets)))


if __name__ == "__main__":
    unittest.main()
