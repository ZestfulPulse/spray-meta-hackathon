"""No-cost unit tests for the free-tier gated Gemini planner."""
import json
import unittest
from urllib.error import URLError

from spray_schema.gemini_planner import GeminiPlanner

NON_CREDENTIAL_VALUE = "offline-placeholder-value"


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, limit=-1):
        return self.payload[:limit]


class GeminiPlannerTests(unittest.TestCase):
    def test_requires_free_tier_attestation_before_any_request(self):
        called = []
        with self.assertRaises(PermissionError):
            GeminiPlanner(api_key=NON_CREDENTIAL_VALUE,
                          opener=lambda *a, **k: called.append(1))
        self.assertEqual(called, [])

    def test_existing_key_is_header_only_and_context_is_redacted(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["key_header"] = request.get_header("X-goog-api-key")
            seen["body"] = json.loads(request.data)
            seen["timeout"] = timeout
            return FakeResponse({
                "candidates": [{
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": '{"action":"click","index":0}'}]},
                }],
                "usageMetadata": {"promptTokenCount": 31, "candidatesTokenCount": 8},
            })

        planner = GeminiPlanner(free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
                                opener=opener)
        decision = planner.choose(
            observation={"url": "/?token=URLSECRET",
                         "visible_text": "api_key=PAGESECRET",
                         "controls": [{"kind": "button", "label": "Repair"}]},
            target_text="password=TARGETSECRET", history=({
                "action": {"action": "click", "index": 0},
                "outcome": "action_failed",
                "error_type": "PermissionError",
                "message": "Bearer HISTORYSECRET",
            },))

        self.assertEqual(decision, {"action": "click", "index": 0})
        self.assertEqual(seen["key_header"], NON_CREDENTIAL_VALUE)
        self.assertNotIn(NON_CREDENTIAL_VALUE, seen["url"])
        self.assertEqual(seen["timeout"], 10)
        prompt = seen["body"]["contents"][0]["parts"][0]["text"]
        self.assertNotIn("URLSECRET", prompt)
        self.assertNotIn("PAGESECRET", prompt)
        self.assertNotIn("TARGETSECRET", prompt)
        self.assertNotIn("HISTORYSECRET", prompt)
        safe_prompt = json.loads(prompt)
        self.assertEqual(safe_prompt["target"], "password=[REDACTED]")
        recent = safe_prompt["recent_outcomes"]
        self.assertEqual(recent, [{
            "action": {"action": "click", "index": 0},
            "outcome": "action_failed",
            "error_type": "PermissionError",
        }])
        self.assertEqual(planner.usage, {"input_tokens": 31, "output_tokens": 8})
        self.assertEqual(planner.requests, 1)
        self.assertEqual(planner.action_history[0]["decision"], decision)
        self.assertNotIn(NON_CREDENTIAL_VALUE, json.dumps(planner.action_history))

    def test_rejects_paid_model_and_unbounded_requests(self):
        with self.assertRaises(ValueError):
            GeminiPlanner(free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
                          model_id="gemini-3.8-flash")
        with self.assertRaises(ValueError):
            GeminiPlanner(free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
                          max_requests=5)

    def test_rejects_invalid_action_schema(self):
        planner = GeminiPlanner(
            free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
            opener=lambda *a, **k: FakeResponse({
                "candidates": [{"finishReason": "STOP", "content": {
                    "parts": [{"text": '{"action":"shell","command":"ls"}'}]}}]
            }),
        )
        with self.assertRaisesRegex(RuntimeError, "invalid Gemini planner decision"):
            planner.choose(observation={"controls": []}, target_text="Completed", history=())
        self.assertEqual(planner.requests, 1)
        self.assertEqual(planner.action_history, [])

    def test_model_click_indices_must_match_evidence_bounds(self):
        """Reject invalid model click choices before the browser action loop."""
        for index in (-1, 100, 1000, True):
            with self.subTest(index=index):
                with self.assertRaises(ValueError):
                    GeminiPlanner._validate_decision(
                        {"action": "click", "index": index})
        for index in (0, 1, 99):
            with self.subTest(valid_index=index):
                self.assertIsNone(GeminiPlanner._validate_decision(
                    {"action": "click", "index": index}))

    def test_out_of_range_history_never_reaches_gemini_prompt(self):
        from spray_schema.gemini_planner import _safe_history

        history = (
            {"action": {"action": "click", "index": 100},
             "outcome": "action_failed"},
            {"action": {"action": "click", "index": 99},
             "outcome": "action_failed"},
        )
        self.assertEqual(_safe_history(history), [
            {"action": {"action": "click", "index": 99},
             "outcome": "action_failed"},
        ])

    def test_non_mapping_candidate_fails_closed_as_planner_error(self):
        """Malformed provider candidate payload must not leak AttributeError."""
        for candidate in (None, "junk", 7, []):
            with self.subTest(candidate=candidate):
                planner = GeminiPlanner(
                    free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
                    opener=lambda *a, candidate=candidate, **k: FakeResponse({
                        "candidates": [candidate],
                    }),
                )
                with self.assertRaisesRegex(RuntimeError, "invalid Gemini planner decision"):
                    planner.choose(observation={"controls": []},
                                   target_text="Completed", history=())
                self.assertEqual(planner.requests, 1)
                self.assertEqual(planner.responses_received, 1)
                self.assertEqual(planner.action_history, [])

    def test_transport_failure_still_consumes_request_cap(self):
        calls = []

        def opener(*args, **kwargs):
            calls.append(True)
            raise URLError("transport failure")

        planner = GeminiPlanner(free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
                                max_requests=1, opener=opener)
        kwargs = {"observation": {"controls": []}, "target_text": "Completed",
                  "history": ()}
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            planner.choose(**kwargs)
        with self.assertRaisesRegex(RuntimeError, "request cap reached"):
            planner.choose(**kwargs)
        self.assertEqual(planner.requests, 1)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
