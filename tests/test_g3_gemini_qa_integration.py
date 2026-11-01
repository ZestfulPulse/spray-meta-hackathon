"""G3 integration: Gemini decision transport -> recon -> browser action -> priority gate.

All default tests use a fake HTTP transport, never a Gemini API request.
The opt-in test exercises real Chromium on an in-memory loopback fixture.
"""
import json
import os
import unittest

from spray_schema.gemini_planner import GeminiPlanner
from spray_schema.qa_flow import Requirement, run_qa_flow

KEY = "offline-noncredential-placeholder"
REQUIREMENTS = (
    Requirement("MUST-01", "Completed", "must"),
    Requirement("SHOULD-02", "Repair instead", "should"),
    Requirement("COULD-03", "Optional guidance", "could"),
)


class FakeResponse:
    def __init__(self, decision):
        self.payload = json.dumps({
            "candidates": [{
                "finishReason": "STOP",
                "content": {"parts": [{"text": json.dumps(decision)}]},
            }],
            "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 8},
        }).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def read(self, limit=-1):
        return self.payload[:limit]


class FakeGeminiTransport:
    """Choose from observed outcomes, not a pre-scripted sequence."""

    def __init__(self, *, unsafe=False):
        self.prompts = []
        self.unsafe = unsafe

    def __call__(self, request, timeout):
        assert timeout <= 10
        assert request.full_url.endswith("/gemini-3.5-flash-lite:generateContent")
        payload = json.loads(request.data)
        prompt = json.loads(payload["contents"][0]["parts"][0]["text"])
        self.prompts.append(prompt)
        assert prompt["observation"]["qa"]["missing_in_priority_order"][0] == "MUST-01"
        assert "raw_dom" not in json.dumps(prompt)
        if self.unsafe:
            return FakeResponse({"action": "shell", "command": "forbidden"})
        index = 1 if any(
            item["outcome"] == "action_failed"
            for item in prompt["recent_outcomes"]
        ) else 0
        return FakeResponse({"action": "click", "index": index})


class FakeBrowser:
    def __init__(self):
        self.done = False
        self.attempted = []

    def observe(self):
        return {
            "url": "/?token=SHOULD_NOT_LEAK",
            "title": "Local QA",
            "visible_text": "Pending",
            "controls": [
                {"kind": "button", "label": "Continue", "enabled": True},
                {"kind": "button", "label": "Repair instead", "enabled": True},
            ],
        }

    def verify(self, text):
        return ((text == "Completed" and self.done)
                or text == "Repair instead")

    def click(self, index):
        self.attempted.append(index)
        if index == 0:
            raise PermissionError("synthetic stale control")
        if index == 1:
            self.done = True

    def wait(self, milliseconds):
        pass


class G3GeminiQAIntegration(unittest.TestCase):
    def test_model_replans_and_priority_gate_passes_without_api(self):
        transport = FakeGeminiTransport()
        planner = GeminiPlanner(free_tier_confirmed=True, api_key=KEY,
                                opener=transport, max_requests=3)
        browser = FakeBrowser()
        result = run_qa_flow(
            browser, planner, flow_id="G3Local",
            requirements=REQUIREMENTS, target_text="Completed", max_steps=3,
        )
        self.assertEqual((result.status, result.quality_gate), ("PASS", "PASS"))
        self.assertEqual([step["outcome"] for step in result.trace],
                         ["action_failed", "executed"])
        self.assertEqual(browser.attempted, [0, 1])
        self.assertEqual(planner.responses_received, 2)
        self.assertEqual(len(transport.prompts), 2)
        self.assertEqual(result.final_qa["weighted_coverage_percent"], 83.3)
        self.assertEqual(result.final_qa["missing_in_priority_order"], ["COULD-03"])
        self.assertNotIn("SHOULD_NOT_LEAK", json.dumps(transport.prompts))

    def test_unapproved_model_tool_fails_closed_before_browser_action(self):
        transport = FakeGeminiTransport(unsafe=True)
        planner = GeminiPlanner(free_tier_confirmed=True, api_key=KEY,
                                opener=transport)
        browser = FakeBrowser()
        result = run_qa_flow(
            browser, planner, flow_id="G3Local",
            requirements=REQUIREMENTS, target_text="Completed",
        )
        self.assertEqual((result.status, result.quality_gate), ("FAIL", "FAIL"))
        self.assertEqual(result.reason, "planner_rejected")
        self.assertEqual(browser.attempted, [])
        self.assertEqual(planner.requests, 1)


@unittest.skipUnless(os.getenv("RUN_BROWSER_E2E") == "1",
                     "opt-in real Chromium; no network or Gemini calls")
class G3GeminiChromiumIntegration(unittest.TestCase):
    def test_real_chromium_stale_click_then_model_replan(self):
        from scripts.run_g3_gemini_chromium import (
            TransitionPreview, FIXTURE, URL, TARGET, REQUIREMENTS as LIVE_REQS,
        )
        transport = FakeGeminiTransport()
        planner = GeminiPlanner(free_tier_confirmed=True, api_key=KEY,
                                opener=transport, max_requests=3)
        with TransitionPreview(
            URL, allowed_origin=URL, fixture_html=FIXTURE,
            chromium_path=os.getenv("SPRAY_CHROMIUM_PATH"),
        ) as browser:
            result = run_qa_flow(
                browser, planner, flow_id="G3Local",
                requirements=LIVE_REQS, target_text=TARGET, max_steps=3,
            )
            self.assertTrue(browser.verify(TARGET))
        self.assertEqual((result.status, result.quality_gate), ("PASS", "PASS"))
        self.assertEqual([step["outcome"] for step in result.trace],
                         ["action_failed", "executed"])
        self.assertEqual(planner.responses_received, 2)


if __name__ == "__main__":
    unittest.main()
