"""Exercise the two-round entry point with synthetic business responses."""

from contextlib import asynccontextmanager, redirect_stdout
import importlib.util
import io
import json
import logging
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from test_planning import FakeClient, request

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("team_check_script", ROOT / "scripts/verify_team.py")
script = importlib.util.module_from_spec(spec)
spec.loader.exec_module(script)


@asynccontextmanager
async def fake_connect(settings):
    yield object()


class StoreClient(FakeClient):
    async def call(self, name, arguments):
        if name == "query-nearby-stores":
            return {"data": {"success": True, "data": [
                {"storeCode": "1234567", "storeName": "测试餐厅", "businessStatus": True}]}}
        return await super().call(name, arguments)


class TeamCheckTests(unittest.TestCase):
    def run_script(self, client):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = request()
            original["crew"][0]["drink"] = "required"
            inputs = root / "request.json"
            inputs.write_text(json.dumps(original))
            search = root / "search.json"
            search.write_text('{"beType":1,"searchType":1}')
            output = io.StringIO()
            original_level = logging.root.manager.disable
            try:
                with patch.object(script, "ROOT", root), patch.object(script, "load_settings", return_value=SimpleNamespace(token="PRIVATE-TEST-TOKEN")), patch.object(script, "connect", fake_connect), patch.object(script, "McDonaldClient", return_value=client), redirect_stdout(output):
                    code = script.main(["--request", str(inputs), "--store-search", str(search), "--limit", "8"])
            finally:
                logging.disable(original_level)
            saved = root / ".local/team-check.json"
            raw = saved.read_text()
            self.assertEqual(saved.stat().st_mode & 0o777, 0o600)
            self.assertNotIn("PRIVATE-TEST-TOKEN", raw + output.getvalue())
            return code, json.loads(raw), original

    def test_two_rounds_preserve_others_and_require_new_quotes(self):
        code, report, original = self.run_script(StoreClient())
        self.assertEqual(code, 0)
        self.assertEqual(report["status"], "verified")
        self.assertEqual(report["request"], original)
        self.assertEqual(report["revised_request"]["budget_fen"], 4500)
        self.assertEqual(report["revised_request"]["crew"][0]["drink"], "none")
        self.assertEqual(report["revised_request"]["crew"][1], original["crew"][1])
        self.assertTrue(report["baseline"]["plans"] and report["revision"]["plans"])
        for plan in report["revision"]["plans"]:
            self.assertNotIn("D", [p["code"] for p in plan["assignments"][0]["products"]])
            self.assertLessEqual(plan["official_quote"]["amount_fen"], 4500)
        self.assertEqual(report["stages"][0]["tool"], "query-nearby-stores")
        self.assertTrue(all(stage["status"] == "returned" for stage in report["stages"]))

    def test_tool_timeout_saves_failed_stage_without_private_exception(self):
        class BrokenClient:
            async def call(self, name, arguments):
                raise TimeoutError("Bearer PRIVATE-TEST-TOKEN")
        code, report, _ = self.run_script(BrokenClient())
        self.assertEqual(code, 2)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["error"]["code"], "TIMEOUT")
        self.assertEqual(report["stages"][0]["status"], "failed")
        self.assertNotIn("baseline", report)


if __name__ == "__main__":
    unittest.main()
