"""Test-only menu and business shapes; not real McDonald's responses."""

from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from hacdonalds.errors import HacdonaldsError, public_error
from hacdonalds.live_check import business_data, menu_candidates, verify


class FakeClient:
    def __init__(self, *, detail_success=True, quote_success=True, menu=None):
        self.calls = []
        self.detail_success = detail_success
        self.quote_success = quote_success
        self.menu = menu if menu is not None else [{"code": "TEST-P1", "name": "测试单品"}]

    async def call(self, name, arguments):
        self.calls.append((name, arguments))
        if name == "query-meals":
            return {"data": {"success": True, "data": self.menu}}
        success = self.detail_success if name == "query-meal-detail" else self.quote_success
        return {"data": {"success": success, "data": {"test_amount": 1234} if success else None}}


class LiveCheckTests(unittest.IsolatedAsyncioTestCase):
    store = {"storeCode": "TEST-STORE", "storeName": "测试餐厅"}

    async def test_success_requires_all_three_business_results(self):
        client = FakeClient()
        report = {}
        await verify(client, self.store, report, lambda _: None)
        self.assertEqual(report["status"], "verified")
        self.assertEqual([name for name, _ in client.calls], ["query-meals", "query-meal-detail", "calculate-price"])
        self.assertEqual(client.calls[-1][1]["items"], [{"productCode": "TEST-P1", "quantity": 1}])

    async def test_failed_detail_prevents_quote(self):
        client = FakeClient(detail_success=False)
        with self.assertRaises(HacdonaldsError):
            await verify(client, self.store, {}, lambda _: None)
        self.assertNotIn("calculate-price", [name for name, _ in client.calls])

    async def test_failed_quote_is_not_verified(self):
        report = {}
        with self.assertRaises(HacdonaldsError):
            await verify(FakeClient(quote_success=False), self.store, report, lambda _: None)
        self.assertNotEqual(report["status"], "verified")
        self.assertIn("rejected", report["stages"][-1])

    async def test_unknown_menu_stops_without_inventing_codes(self):
        client = FakeClient(menu=[{"unknown": "TEST"}])
        with self.assertRaises(HacdonaldsError) as caught:
            await verify(client, self.store, {}, lambda _: None)
        self.assertEqual(caught.exception.code, "MENU_SHAPE_UNKNOWN")
        self.assertEqual(len(client.calls), 1)

    async def test_candidate_attempts_are_bounded(self):
        client = FakeClient(detail_success=False, menu=[{"code": str(i), "name": f"测试{i}"} for i in range(10)])
        with self.assertRaises(HacdonaldsError):
            await verify(client, self.store, {}, lambda _: None, max_candidates=2)
        self.assertEqual(len(client.calls), 3)

    async def test_candidates_deduplicate_and_prefer_single_items(self):
        result = menu_candidates([{"name": "测试套餐", "code": "COMBO"},
                                  {"name": "测试分类", "items": [{"name": "测试单品", "code": "P"},
                                                                      {"name": "测试单品", "code": "P"}]}])
        self.assertEqual([item["code"] for item in result], ["P", "COMBO"])

    async def test_transport_success_is_not_business_success(self):
        with self.assertRaises(HacdonaldsError):
            business_data({"source": "live", "data": {"success": False, "data": []}})

    async def test_official_category_shape_without_meal_names(self):
        menu = {"categories": [{"name": "测试分类", "code": "NOT-A-MEAL", "meals": [
            {"code": "COMBO", "tags": ["热门"]},
            {"code": "SINGLE", "tags": ["单品", "热门"]},
            {"code": "SINGLE", "tags": ["单品"]},
        ]}]}
        candidates = menu_candidates(menu)
        self.assertEqual([item["code"] for item in candidates], ["SINGLE", "COMBO"])
        self.assertNotIn("name", candidates[0])
        client = FakeClient(menu=menu)
        report = {}
        await verify(client, self.store, report, lambda _: None)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(client.calls[1][1]["code"], "SINGLE")
        self.assertEqual(client.calls[2][1]["items"], [{"productCode": "SINGLE", "quantity": 1}])

    async def test_nested_sdk_group_preserves_domain_error(self):
        error = HacdonaldsError("MENU_SHAPE_UNKNOWN", "菜单格式未识别")
        wrapped = ExceptionGroup("SDK transport", [ExceptionGroup("task group", [error])])
        self.assertIs(public_error(wrapped), error)

    async def test_error_causes_and_cycles_are_safe(self):
        error = HacdonaldsError("TOOL_FAILED", "工具失败")
        wrapper = RuntimeError("private upstream text")
        wrapper.__cause__ = error
        error.__context__ = wrapper
        self.assertIs(public_error(wrapper), error)
        cycle = RuntimeError("Bearer secret")
        cycle.__cause__ = cycle
        result = public_error(cycle)
        self.assertEqual(result.code, "MCP_FAILED")
        self.assertNotIn("secret", str(result))


if __name__ == "__main__":
    unittest.main()
