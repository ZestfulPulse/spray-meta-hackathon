"""Opt-in real Chromium E2E; CI stdlib-only gate skips without RUN_BROWSER_E2E=1."""
import os
import unittest
from spray_schema.agent_loop import run_loop
from spray_schema.playwright_preview import PreviewBrowser
from spray_schema.bedrock_planner import BedrockPlanner
from tests.bedrock_test_support import make_test_budget


HTML = ('<html><title>SPRAY Preview</title><body><h1>Demo</h1>'
        '<button onclick="document.getElementById(\'status\').textContent=\'Completed\'">Repair</button>'
        '<p id="status">Pending</p></body></html>')

class Planner:
    def choose(self, *, observation, target_text, history):
        return {"action": "click", "index": 0}


@unittest.skipUnless(os.getenv("RUN_BROWSER_E2E") == "1", "opt-in real Chromium E2E")
class BrowserE2ETests(unittest.TestCase):
    def test_local_preview_real_chromium(self):
        url = "http://127.0.0.1:8765/"
        with PreviewBrowser(url, allowed_origin=url, fixture_html=HTML) as browser:
            result = run_loop(browser, Planner(), target_text="Completed")
            self.assertEqual(result.status, "PASS", result)
            self.assertEqual(result.trace[0]["action"], {"action": "click", "index": 0})
            self.assertTrue(browser.page.get_by_text("Completed", exact=True).is_visible())
            if os.getenv("SPRAY_EVIDENCE_DIR"):
                browser.page.screenshot(path=os.path.join(os.environ["SPRAY_EVIDENCE_DIR"], "browser_pass.png"))
                import json
                with open(os.path.join(os.environ["SPRAY_EVIDENCE_DIR"], "browser_trace.json"), "w") as f:
                    json.dump({"status": result.status, "reason": result.reason,
                               "trace": result.trace, "fixture": True}, f, indent=2)

    def test_mock_bedrock_replans_after_failed_chromium_click(self):
        import json
        html = ('<html><body><button disabled>Broken</button>'
                '<button onclick="document.getElementById(\'status\').textContent=\'Completed\'">Repair</button>'
                '<p id="status">Pending</p></body></html>')
        class FakeConverse:
            __spray_offline_only__ = True
            calls = 0
            def converse(self, **kwargs):
                self.calls += 1
                data = json.loads(kwargs["messages"][0]["content"][0]["text"])
                index = 1 if data["recent_outcomes"] else 0
                return {"output": {"message": {"content": [
                    {"text": json.dumps({"action": "click", "index": index})}]}}}
        fake = FakeConverse()
        budget = make_test_budget(self, "test-only", "us-east-1")
        planner = BedrockPlanner(model_id="test-only", region="us-east-1",
                                 authorized=True, client=fake, budget=budget,
                                 test_mode=True)
        url = "http://127.0.0.1:8765/"
        with PreviewBrowser(url, allowed_origin=url, fixture_html=html) as browser:
            result = run_loop(browser, planner, target_text="Completed", max_steps=3)
            self.assertEqual(result.status, "PASS", result)
            self.assertEqual([t["outcome"] for t in result.trace],
                             ["action_failed", "executed"])
            self.assertEqual(fake.calls, 2)
            if os.getenv("SPRAY_EVIDENCE_DIR"):
                with open(os.path.join(os.environ["SPRAY_EVIDENCE_DIR"], "replan_trace.json"), "w") as f:
                    json.dump({"status": result.status, "trace": result.trace,
                               "mock_model": True, "fixture": True}, f, indent=2)

    def test_disallowed_origin(self):
        with self.assertRaises(PermissionError):
            PreviewBrowser("https://example.com/", allowed_origin="https://example.org/")


if __name__ == "__main__":
    unittest.main()
