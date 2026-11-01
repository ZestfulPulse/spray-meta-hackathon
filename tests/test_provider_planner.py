"""Tests for the provider-agnostic planner interface.

These prove the Gemini path behaves IDENTICALLY when driven through
GeminiProviderAdapter as when calling GeminiPlanner directly: same decision for
the same fake-opener response, free-tier attestation still required, and the
API key still header-only (never in URL or body). They also confirm both
adapters satisfy the Planner protocol.
"""
import json
import unittest

from spray_schema.gemini_planner import GeminiPlanner
from spray_schema.meta_planner import MetaPlanner
from spray_schema.provider_planner import (GeminiProviderAdapter, Planner)

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


def _gemini_response():
    return {
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": '{"action":"click","index":0}'}]},
        }],
        "usageMetadata": {"promptTokenCount": 31, "candidatesTokenCount": 8},
    }


class ProviderPlannerInterfaceTests(unittest.TestCase):
    def test_both_adapters_conform_to_planner_protocol(self):
        gemini = GeminiProviderAdapter(
            free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
            opener=lambda *a, **k: FakeResponse(_gemini_response()))
        meta = MetaPlanner(api_key=NON_CREDENTIAL_VALUE,
                           opener=lambda *a, **k: FakeResponse(_gemini_response()))
        self.assertIsInstance(gemini, Planner)
        self.assertIsInstance(meta, Planner)
        self.assertEqual(gemini.provider, "gemini")
        self.assertEqual(meta.provider, "meta")

    def test_gemini_adapter_requires_free_tier_attestation(self):
        called = []
        with self.assertRaises(PermissionError):
            GeminiProviderAdapter(api_key=NON_CREDENTIAL_VALUE,
                                  opener=lambda *a, **k: called.append(1))
        self.assertEqual(called, [])

    def test_gemini_path_identical_through_adapter(self):
        """Same inputs/response produce identical decision and transport use."""
        observation = {"url": "/?token=URLSECRET",
                       "visible_text": "api_key=PAGESECRET",
                       "controls": [{"kind": "button", "label": "Repair"}]}
        target = "password=TARGETSECRET"
        history = ({"action": {"action": "click", "index": 0},
                    "outcome": "action_failed", "error_type": "PermissionError",
                    "message": "Bearer HISTORYSECRET"},)

        direct_seen = {}
        adapter_seen = {}

        def make_opener(store):
            def opener(request, timeout):
                store["url"] = request.full_url
                store["key_header"] = request.get_header("X-goog-api-key")
                store["body"] = request.data
                store["timeout"] = timeout
                return FakeResponse(_gemini_response())
            return opener

        direct = GeminiPlanner(free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
                               opener=make_opener(direct_seen))
        adapter = GeminiProviderAdapter(
            free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
            opener=make_opener(adapter_seen))

        direct_decision = direct.choose(observation=observation,
                                        target_text=target, history=history)
        adapter_decision = adapter.choose(observation=observation,
                                          target_text=target, history=history)

        # Identical decision, identical outbound body, identical key handling.
        self.assertEqual(direct_decision, adapter_decision)
        self.assertEqual(direct_seen["body"], adapter_seen["body"])
        self.assertEqual(direct_seen["url"], adapter_seen["url"])
        self.assertEqual(direct_seen["timeout"], adapter_seen["timeout"])
        # Key header-only, never in URL or body.
        self.assertEqual(adapter_seen["key_header"], NON_CREDENTIAL_VALUE)
        self.assertNotIn(NON_CREDENTIAL_VALUE, adapter_seen["url"])
        self.assertNotIn(NON_CREDENTIAL_VALUE.encode(), adapter_seen["body"])

    def test_adapter_exposes_model_id_from_wrapped_planner(self):
        planner = GeminiPlanner(free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
                                opener=lambda *a, **k: FakeResponse(_gemini_response()))
        adapter = GeminiProviderAdapter(planner=planner)
        self.assertEqual(adapter.model_id, planner.model_id)
        self.assertIs(adapter.planner, planner)

    def test_adapter_rejects_both_planner_and_kwargs(self):
        planner = GeminiPlanner(free_tier_confirmed=True, api_key=NON_CREDENTIAL_VALUE,
                                opener=lambda *a, **k: FakeResponse(_gemini_response()))
        with self.assertRaises(ValueError):
            GeminiProviderAdapter(planner=planner, api_key=NON_CREDENTIAL_VALUE)


if __name__ == "__main__":
    unittest.main()
