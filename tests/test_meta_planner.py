"""No-cost unit tests for the Meta Model API planner adapter (fake transport).

All tests inject a FAKE transport (``opener``) so no network call is ever made.
They prove the API key is header-only, the request body is S6-redacted, the
model id defaults to the marked placeholder constant and is overridable, the
budget-authorizer seam runs before any send, and transport errors raise
RuntimeError without leaking provider bodies or headers.
"""
import json
import os
import unittest
from unittest import mock
from urllib.error import HTTPError, URLError

from spray_schema.meta_planner import (
    _DEFAULT_API_BASE,
    META_MODEL_ID,
    MetaPlanner,
)

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


def _click_response():
    # OpenAI chat shape: choices[0].message.content is the JSON decision text.
    return {
        "choices": [{
            "finish_reason": "stop",
            "message": {"content": '{"action":"click","index":0}'},
        }],
        "usage": {"prompt_tokens": 31, "completion_tokens": 8},
    }


class MetaPlannerTests(unittest.TestCase):
    def test_key_is_header_only_and_body_is_redacted(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["auth_header"] = request.get_header("Authorization")
            seen["raw_body"] = request.data
            seen["body"] = json.loads(request.data)
            seen["timeout"] = timeout
            return FakeResponse(_click_response())

        planner = MetaPlanner(api_key=NON_CREDENTIAL_VALUE, opener=opener)
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
        # Key is sent only as the Authorization: Bearer header, never in
        # URL or body.
        self.assertEqual(seen["auth_header"], "Bearer " + NON_CREDENTIAL_VALUE)
        self.assertNotIn(NON_CREDENTIAL_VALUE, seen["url"])
        self.assertNotIn(NON_CREDENTIAL_VALUE.encode(), seen["raw_body"])
        self.assertEqual(seen["timeout"], 10)
        # S6 redaction: no secret-shaped values reach the body.
        body_text = seen["raw_body"].decode("utf-8")
        for secret in ("URLSECRET", "PAGESECRET", "TARGETSECRET", "HISTORYSECRET"):
            self.assertNotIn(secret, body_text)
        prompt = seen["body"]["messages"][1]["content"]
        safe_prompt = json.loads(prompt)
        self.assertEqual(safe_prompt["target"], "password=[REDACTED]")
        self.assertEqual(safe_prompt["recent_outcomes"], [{
            "action": {"action": "click", "index": 0},
            "outcome": "action_failed",
            "error_type": "PermissionError",
        }])
        # The free-text history message must never reach the body or evidence.
        self.assertNotIn("HISTORYSECRET", json.dumps(planner.action_history))
        self.assertEqual(planner.usage, {"input_tokens": 31, "output_tokens": 8})
        self.assertEqual(planner.requests, 1)

    def test_target_meta_endpoint_is_openai_chat_on_api_host(self):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            return FakeResponse(_click_response())

        MetaPlanner(api_key=NON_CREDENTIAL_VALUE, opener=opener).choose(
            observation={"controls": []}, target_text="Repair", history=())
        # Live API host + OpenAI chat path; not the docs host, not legacy,
        # not the retired Gemini :generate path.
        self.assertIn("api.meta.ai", seen["url"])
        self.assertIn("/v1/chat/completions", seen["url"])
        self.assertNotIn("dev.meta.ai", seen["url"])
        self.assertNotIn("api.llama.com", seen["url"])
        self.assertNotIn(":generate", seen["url"])

    def test_model_id_defaults_to_placeholder_constant(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_MODEL_ID", None)
            planner = MetaPlanner(api_key=NON_CREDENTIAL_VALUE,
                                  opener=lambda *a, **k: FakeResponse(_click_response()))
        self.assertEqual(planner.model_id, META_MODEL_ID)
        self.assertIn("PLACEHOLDER", META_MODEL_ID)

    def test_model_id_override_via_kwarg(self):
        planner = MetaPlanner(api_key=NON_CREDENTIAL_VALUE, model_id="muse-spark-exact",
                              opener=lambda *a, **k: FakeResponse(_click_response()))
        self.assertEqual(planner.model_id, "muse-spark-exact")

    def test_model_id_override_via_env(self):
        with mock.patch.dict(os.environ, {"META_MODEL_ID": "muse-spark-fromenv"}):
            planner = MetaPlanner(api_key=NON_CREDENTIAL_VALUE,
                                  opener=lambda *a, **k: FakeResponse(_click_response()))
        self.assertEqual(planner.model_id, "muse-spark-fromenv")

    def test_kwarg_model_id_wins_over_env(self):
        with mock.patch.dict(os.environ, {"META_MODEL_ID": "muse-spark-fromenv"}):
            planner = MetaPlanner(api_key=NON_CREDENTIAL_VALUE, model_id="muse-spark-kwarg",
                                  opener=lambda *a, **k: FakeResponse(_click_response()))
        self.assertEqual(planner.model_id, "muse-spark-kwarg")

    def test_key_read_from_env_var(self):
        seen = {}

        def opener(request, timeout):
            seen["auth_header"] = request.get_header("Authorization")
            return FakeResponse(_click_response())

        with mock.patch.dict(os.environ, {"META_API_KEY": NON_CREDENTIAL_VALUE}):
            planner = MetaPlanner(opener=opener)
            planner.choose(observation={"controls": []}, target_text="Repair",
                           history=())
        self.assertEqual(seen["auth_header"], "Bearer " + NON_CREDENTIAL_VALUE)

    def test_missing_key_fails_closed(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_API_KEY", None)
            with self.assertRaises(PermissionError):
                MetaPlanner(opener=lambda *a, **k: FakeResponse(_click_response()))

    def test_budget_authorizer_runs_before_send_and_can_refuse(self):
        sent = []

        def opener(request, timeout):
            sent.append(True)
            return FakeResponse(_click_response())

        planner = MetaPlanner(api_key=NON_CREDENTIAL_VALUE, opener=opener,
                              budget_authorizer=lambda ctx: False)
        with self.assertRaisesRegex(RuntimeError, "refused by budget authorizer"):
            planner.choose(observation={"controls": []}, target_text="Repair",
                           history=())
        # Refusal happens before any transport send.
        self.assertEqual(sent, [])

    def test_budget_authorizer_receives_cost_context(self):
        captured = {}

        planner = MetaPlanner(
            api_key=NON_CREDENTIAL_VALUE,
            opener=lambda *a, **k: FakeResponse(_click_response()),
            budget_authorizer=lambda ctx: captured.update(ctx) or True,
            estimated_credit_cost_usd=0.5)
        planner.choose(observation={"controls": []}, target_text="Repair",
                       history=(), estimated_credit_cost_usd=0.75)
        self.assertEqual(captured["provider"], "meta")
        self.assertEqual(captured["model_id"], planner.model_id)
        self.assertEqual(captured["estimated_credit_cost_usd"], 0.75)

    def test_authorizer_exception_fails_closed_without_leak(self):
        sent = []

        def boom(ctx):
            raise KeyError("SENSITIVE-INTERNAL-DETAIL")

        planner = MetaPlanner(
            api_key=NON_CREDENTIAL_VALUE,
            opener=lambda *a, **k: sent.append(True) or FakeResponse(_click_response()),
            budget_authorizer=boom)
        with self.assertRaises(RuntimeError) as ctx:
            planner.choose(observation={"controls": []}, target_text="Repair",
                           history=())
        self.assertEqual(sent, [])
        self.assertNotIn("SENSITIVE-INTERNAL-DETAIL", str(ctx.exception))

    def test_http_error_raises_runtime_without_leak(self):
        def opener(*args, **kwargs):
            raise HTTPError("https://api.meta.ai", 403,
                            "SECRET-PROVIDER-BODY", {"x-leak": "SECRET-HEADER"}, None)

        planner = MetaPlanner(api_key=NON_CREDENTIAL_VALUE, opener=opener)
        with self.assertRaises(RuntimeError) as ctx:
            planner.choose(observation={"controls": []}, target_text="Repair",
                           history=())
        message = str(ctx.exception)
        self.assertIn("HTTP 403", message)
        self.assertNotIn("SECRET-PROVIDER-BODY", message)
        self.assertNotIn("SECRET-HEADER", message)

    def test_transport_failure_consumes_request_cap(self):
        calls = []

        def opener(*args, **kwargs):
            calls.append(True)
            raise URLError("transport failure")

        planner = MetaPlanner(api_key=NON_CREDENTIAL_VALUE, max_requests=1,
                              opener=opener)
        kwargs = {"observation": {"controls": []}, "target_text": "Repair",
                  "history": ()}
        with self.assertRaisesRegex(RuntimeError, "transport failed"):
            planner.choose(**kwargs)
        with self.assertRaisesRegex(RuntimeError, "request cap reached"):
            planner.choose(**kwargs)
        self.assertEqual(planner.requests, 1)
        self.assertEqual(len(calls), 1)

    def test_rejects_invalid_decision_schema(self):
        planner = MetaPlanner(
            api_key=NON_CREDENTIAL_VALUE,
            opener=lambda *a, **k: FakeResponse({
                "choices": [{"finish_reason": "stop", "message": {
                    "content": '{"action":"shell","command":"ls"}'}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3},
            }))
        with self.assertRaisesRegex(RuntimeError, "invalid Meta planner decision"):
            planner.choose(observation={"controls": []}, target_text="Completed",
                           history=())
        self.assertEqual(planner.action_history, [])

    def test_rejects_unbounded_requests_and_bad_cost(self):
        with self.assertRaises(ValueError):
            MetaPlanner(api_key=NON_CREDENTIAL_VALUE, max_requests=5,
                        opener=lambda *a, **k: FakeResponse(_click_response()))
        with self.assertRaises(ValueError):
            MetaPlanner(api_key=NON_CREDENTIAL_VALUE, estimated_credit_cost_usd=-1,
                        opener=lambda *a, **k: FakeResponse(_click_response()))

    def test_rejects_finish_reason_length(self):
        # A reasoning model that exhausts its budget returns finish_reason
        # 'length' with null content; that is not a complete decision.
        planner = MetaPlanner(
            api_key=NON_CREDENTIAL_VALUE,
            opener=lambda *a, **k: FakeResponse({
                "choices": [{"finish_reason": "length",
                             "message": {"content": None}}],
                "usage": {"prompt_tokens": 31, "completion_tokens": 2048},
            }))
        with self.assertRaisesRegex(RuntimeError, "invalid Meta planner decision"):
            planner.choose(observation={"controls": []}, target_text="Repair",
                           history=())
        self.assertEqual(planner.action_history, [])


class MetaPlannerApiBaseTests(unittest.TestCase):
    """The resolvable API base: kwarg > META_API_BASE env > default constant."""

    @staticmethod
    def _capture_url(**kwargs):
        seen = {}

        def opener(request, timeout):
            seen["url"] = request.full_url
            seen["auth_header"] = request.get_header("Authorization")
            seen["body"] = json.loads(request.data)
            seen["raw_body"] = request.data
            return FakeResponse(_click_response())

        planner = MetaPlanner(api_key=NON_CREDENTIAL_VALUE, opener=opener,
                              **kwargs)
        planner.choose(observation={"controls": []}, target_text="Repair",
                       history=())
        return seen

    def test_default_base_is_api_meta_ai(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_API_BASE", None)
            seen = self._capture_url()
        self.assertEqual(_DEFAULT_API_BASE, "https://api.meta.ai")
        self.assertEqual(seen["url"],
                         "https://api.meta.ai/v1/chat/completions")

    def test_env_overrides_base(self):
        with mock.patch.dict(os.environ,
                             {"META_API_BASE": "https://env.example"}):
            seen = self._capture_url()
        self.assertEqual(seen["url"],
                         "https://env.example/v1/chat/completions")

    def test_kwarg_base_wins_over_env(self):
        with mock.patch.dict(os.environ,
                             {"META_API_BASE": "https://env.example"}):
            seen = self._capture_url(api_base="https://kwarg.example")
        self.assertEqual(seen["url"],
                         "https://kwarg.example/v1/chat/completions")

    def test_built_url_is_exactly_base_plus_chat_completions(self):
        # Trailing slash is normalized; the path is appended verbatim.
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_API_BASE", None)
            seen = self._capture_url(api_base="https://host.example/")
        self.assertEqual(seen["url"],
                         "https://host.example/v1/chat/completions")

    def test_key_is_header_only_regardless_of_base(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("META_API_BASE", None)
            seen = self._capture_url(api_base="https://host.example")
        self.assertEqual(seen["auth_header"],
                         "Bearer " + NON_CREDENTIAL_VALUE)
        self.assertNotIn(NON_CREDENTIAL_VALUE, seen["url"])
        self.assertNotIn(NON_CREDENTIAL_VALUE.encode(), seen["raw_body"])
        # The key is not a top-level body field either.
        self.assertNotIn("Authorization", seen["body"])

    def test_invalid_base_fails_closed(self):
        for bad in ("http://api.meta.ai", "https://", "ftp://x", "api.meta.ai"):
            with self.subTest(base=bad):
                with self.assertRaises(ValueError):
                    MetaPlanner(api_key=NON_CREDENTIAL_VALUE, api_base=bad,
                                opener=lambda *a, **k: FakeResponse(
                                    _click_response()))

    def test_denied_authorizer_blocks_send_with_custom_base(self):
        sent = []

        def opener(request, timeout):
            sent.append(True)
            return FakeResponse(_click_response())

        planner = MetaPlanner(api_key=NON_CREDENTIAL_VALUE, opener=opener,
                              api_base="https://host.example",
                              budget_authorizer=lambda ctx: False)
        with self.assertRaisesRegex(RuntimeError, "refused by budget authorizer"):
            planner.choose(observation={"controls": []}, target_text="Repair",
                           history=())
        self.assertEqual(sent, [])


if __name__ == "__main__":
    unittest.main()
