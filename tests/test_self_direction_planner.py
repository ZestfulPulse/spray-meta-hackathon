"""Offline tests for the live self-direction planner (FEAT-003).

These prove, entirely offline (a FAKE transport via the ``opener`` kwarg; NO
network call is ever made), that
``spray_schema.self_direction_planner.SelfDirectionPlanner``:

  (1) returns a plan dict that passes
      ``spray_schema.decision_cycle._require_plan_shape`` for a well-formed fake
      model response built from the REAL confirmed plan shape;
  (2) sends the API key header-only (``Authorization: Bearer``), never in the
      body or URL;
  (3) applies S6 redaction to the observation/target/history so no secret
      sentinel reaches the request body;
  (4) refuses fail-closed when a ``budget_authorizer`` denies the call (the
      opener is never invoked);
  (5) raises RuntimeError without leaking provider content on
      ``finish_reason != 'stop'`` and on malformed / under-3-candidate plans;
  (6) resolves the request URL to ``<base>/v1/chat/completions`` for the
      default base, a ``META_API_BASE`` env override, and an ``api_base`` kwarg;
  (7) wraps HTTPError/URLError without leaking a provider body or header.

Secret-shaped sentinels are assembled from concatenated string parts so no
AWS / JWT / PEM / generic-secret FORMAT ever appears on a single source line
(ci/policy_checks.py scans every line, including tests). The (header-only) API
key under test is a non-credential placeholder.
"""
import json
import os
import unittest
from urllib.error import HTTPError, URLError

from spray_schema.decision_cycle import _require_plan_shape
from spray_schema.self_direction_planner import (
    _CHAT_COMPLETIONS_PATH,
    SelfDirectionPlanner,
)

# Non-credential placeholder used as the (header-only) API key under test.
API_KEY_PLACEHOLDER = "offline-placeholder-key"

# Non-placeholder model id so no live-gate concerns leak into these tests.
MODEL_ID = "fake-muse-spark-001"

# AWS-access-key-id sentinel assembled from parts so the full AKIA+16 FORMAT
# never appears on a single source line (otherwise the strict AWS pattern in
# ci/policy_checks.py would flag this file). It still has the real shape the
# S6 redactor must mask.
AWS_KEY_SENTINEL = "AK" + "IA" + "ISADEADBEEFOWNER"  # AKIA + 16 chars

# JWT sentinel, likewise assembled from parts; still matches the JWT shape the
# redactor masks (eyJ..eyJ..sig).
JWT_SENTINEL = "ey" + "JhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9." \
               "ey" + "JzdWI6Ik9XTkVSIn0.sIgNaTuReXyZ"

# Raw sensitive values that must NEVER reach the request body. Each is a
# distinct sentinel so a leak points straight at the source field.
SECRET_SENTINELS = (
    "URLSECRET",                 # query token inside a credentialed URL
    "PAGESECRET",                # api_key=... embedded in visible page text
    "TARGETSECRET",              # password=... embedded in the target text
    "HISTORYSECRET",             # Bearer ... embedded in a prior lesson
    "OWNERIDENTITYUSER",         # owner-identifying URL userinfo (username)
    "OWNERIDENTITYTOKEN",        # owner-identifying session_token value
    AWS_KEY_SENTINEL,            # AWS access key id shape
    JWT_SENTINEL,                # JWT shape
)


def _real_shape_plan():
    """A plan dict in the REAL confirmed shape the live model returns.

    Keys: domain, candidates (>=3, each name+description), scoring_criteria,
    candidate_scores, chosen_action, chosen_rationale, rejected ([{candidate,
    why}]), prediction, actual_outcome, prediction_vs_actual, lesson, and the
    optional observed_note.
    """
    names = ["offline-coverage-report", "dead-link-audit", "schema-lint"]
    return {
        "domain": "repo quality analysis",
        "candidates": [
            {"name": n, "description": "produce an offline analysis artifact: " + n}
            for n in names
        ],
        "scoring_criteria": ["impact", "cost", "risk"],
        "candidate_scores": [
            {"candidate": n, "impact": i, "cost": 0, "risk": 1}
            for i, n in enumerate(names, start=1)
        ],
        "chosen_action": "pursue " + names[0],
        "chosen_rationale": "highest impact at zero cash cost and lowest risk",
        "rejected": [
            {"candidate": n, "why": "lower expected value than the chosen option"}
            for n in names[1:]
        ],
        "prediction": "the offline coverage report will surface >=1 gap",
        "actual_outcome": {"note": "offline coverage report generated"},
        "prediction_vs_actual": "matched: a coverage gap was surfaced",
        "lesson": "offline coverage reporting is a reliable zero-cash first move",
        "observed_note": "first cycle: none available",
    }


def _plan_with_quantitative_claim():
    """A plan that additionally includes the S8 quantitative_claim object (and a
    followup_decision), proving extra keys parse through choose() unchanged."""
    plan = _real_shape_plan()
    plan["quantitative_claim"] = {
        "metric_key": "duplicate_basename_groups",
        "predicted_low": 1,
        "predicted_high": 8,
        "kind": "range",
        "confidence": 0.65,
    }
    plan["followup_decision"] = {
        "chose": False,
        "why": "no prior over-range numeric error or finding (D-0001)",
    }
    return plan


def _chat_response(payload_obj, *, finish_reason="stop",
                   prompt_tokens=120, completion_tokens=700):
    """Build an OpenAI-shaped chat completion whose content is JSON of payload."""
    content = (payload_obj if isinstance(payload_obj, str)
               else json.dumps(payload_obj))
    return {
        "choices": [{
            "finish_reason": finish_reason,
            "message": {"content": content},
        }],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, limit=-1):
        return self.payload[:limit]


def _capturing_opener(response_payload, captured):
    """Return an opener that records the request and returns response_payload."""

    def opener(request, timeout):
        captured["url"] = request.full_url
        captured["raw_bytes"] = request.data
        captured["body"] = json.loads(request.data)
        captured["auth_header"] = request.get_header("Authorization")
        captured["called"] = True
        return FakeResponse(response_payload)

    return opener


class PlanShapeTests(unittest.TestCase):
    def test_wellformed_response_returns_plan_passing_require_plan_shape(self):
        captured = {}
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(_real_shape_plan()), captured))
        plan = planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="SPRAY self-directed opportunity selection",
            history=())
        self.assertIsInstance(plan, dict)
        # The runner's own validator must accept it (import + call directly).
        _require_plan_shape(plan)  # raises ValueError on any shape failure
        self.assertEqual(plan["domain"], "repo quality analysis")
        self.assertEqual(len(plan["candidates"]), 3)
        # Usage is mapped OpenAI prompt/completion -> input/output tokens.
        self.assertEqual(planner.last_usage,
                         {"input_tokens": 120, "output_tokens": 700})
        self.assertEqual(planner.usage,
                         {"input_tokens": 120, "output_tokens": 700})


class HeaderOnlyKeyTests(unittest.TestCase):
    def test_key_is_header_only_never_in_body_or_url(self):
        captured = {}
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(_real_shape_plan()), captured))
        planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="pick one zero-cash opportunity",
            history=())
        self.assertEqual(captured["auth_header"],
                         "Bearer " + API_KEY_PLACEHOLDER)
        raw_text = captured["raw_bytes"].decode("utf-8")
        body_text = json.dumps(captured["body"])
        self.assertNotIn(API_KEY_PLACEHOLDER, raw_text)
        self.assertNotIn(API_KEY_PLACEHOLDER, body_text)
        self.assertNotIn(API_KEY_PLACEHOLDER, captured["url"])


class RedactionTests(unittest.TestCase):
    def test_s6_redaction_applied_to_observation_target_history(self):
        captured = {}
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(_real_shape_plan()), captured))
        planner.choose(
            observation={
                # Owner-identifying username + secret query token in a URL.
                "url": "https://OWNERIDENTITYUSER:URLSECRET@host.example"
                       "/tasks?token=URLSECRET",
                # api_key / session_token pairs, an AWS key, and a JWT, each in
                # a shape the S6 redactor masks.
                "visible_text": (
                    "api_key=PAGESECRET "
                    "session_token=OWNERIDENTITYTOKEN "
                    f"{AWS_KEY_SENTINEL} {JWT_SENTINEL}"),
                "controls": [{"kind": "button", "label": "Repair",
                              "enabled": True}],
            },
            target_text="password=TARGETSECRET",
            history=(
                {"decision_id": "D-0001",
                 "lesson": "Bearer HISTORYSECRET was in the prior lesson"},
            ))

        raw_text = captured["raw_bytes"].decode("utf-8")
        body_text = json.dumps(captured["body"])
        for sentinel in SECRET_SENTINELS:
            self.assertNotIn(sentinel, raw_text,
                             f"{sentinel} leaked into raw request bytes")
            self.assertNotIn(sentinel, body_text,
                             f"{sentinel} leaked into parsed request body")

        # The redacted structure is preserved: the user message is JSON with a
        # redacted target and a redacted prior-lessons list.
        prompt = json.loads(captured["body"]["messages"][1]["content"])
        self.assertEqual(prompt["target"], "password=[REDACTED]")
        self.assertEqual(len(prompt["prior_lessons"]), 1)
        self.assertNotIn("HISTORYSECRET",
                         json.dumps(prompt["prior_lessons"]))


class BudgetAuthorizerTests(unittest.TestCase):
    def test_denied_authorizer_blocks_send_and_raises(self):
        captured = {}
        opener = _capturing_opener(_chat_response(_real_shape_plan()), captured)
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID, opener=opener,
            budget_authorizer=lambda ctx: False)
        with self.assertRaises(RuntimeError) as ctx:
            planner.choose(
                observation={"url": "https://host.example/tasks",
                             "controls": []},
                target_text="pick one zero-cash opportunity",
                history=())
        self.assertIn("refused by budget authorizer", str(ctx.exception))
        # The transport was NEVER invoked (no send on refuse).
        self.assertNotIn("called", captured)

    def test_raising_authorizer_blocks_send_and_raises(self):
        captured = {}
        opener = _capturing_opener(_chat_response(_real_shape_plan()), captured)

        def boom(ctx):
            raise RuntimeError("nope")

        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID, opener=opener,
            budget_authorizer=boom)
        with self.assertRaises(RuntimeError) as ctx:
            planner.choose(
                observation={"url": "https://host.example/tasks",
                             "controls": []},
                target_text="pick one zero-cash opportunity",
                history=())
        self.assertIn("refused by budget authorizer", str(ctx.exception))
        self.assertNotIn("called", captured)


class MalformedResponseTests(unittest.TestCase):
    def _planner(self, response_payload):
        captured = {}
        return SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(response_payload, captured))

    def _choose(self, planner):
        return planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="pick one zero-cash opportunity",
            history=())

    def test_finish_reason_not_stop_raises_without_leak(self):
        payload = _chat_response(_real_shape_plan(), finish_reason="length")
        planner = self._planner(payload)
        with self.assertRaises(RuntimeError) as ctx:
            self._choose(planner)
        self.assertEqual(str(ctx.exception), "invalid self-direction plan")

    def test_under_three_candidates_raises_without_leak(self):
        plan = _real_shape_plan()
        plan["candidates"] = plan["candidates"][:2]  # only 2 candidates
        planner = self._planner(_chat_response(plan))
        with self.assertRaises(RuntimeError) as ctx:
            self._choose(planner)
        self.assertEqual(str(ctx.exception), "invalid self-direction plan")

    def test_empty_lesson_raises_without_leak(self):
        plan = _real_shape_plan()
        plan["lesson"] = "   "  # non-empty-string requirement fails
        planner = self._planner(_chat_response(plan))
        with self.assertRaises(RuntimeError) as ctx:
            self._choose(planner)
        self.assertEqual(str(ctx.exception), "invalid self-direction plan")

    def test_missing_required_field_raises_without_leak(self):
        plan = _real_shape_plan()
        del plan["prediction"]
        planner = self._planner(_chat_response(plan))
        with self.assertRaises(RuntimeError) as ctx:
            self._choose(planner)
        self.assertEqual(str(ctx.exception), "invalid self-direction plan")

    def test_non_json_content_raises_without_leak(self):
        payload = _chat_response("this is not json", )
        planner = self._planner(payload)
        with self.assertRaises(RuntimeError) as ctx:
            self._choose(planner)
        self.assertEqual(str(ctx.exception), "invalid self-direction plan")

    def test_candidate_without_name_or_description_raises(self):
        plan = _real_shape_plan()
        plan["candidates"] = [{"foo": "bar"}, {"foo": "baz"}, {"foo": "qux"}]
        planner = self._planner(_chat_response(plan))
        with self.assertRaises(RuntimeError) as ctx:
            self._choose(planner)
        self.assertEqual(str(ctx.exception), "invalid self-direction plan")


class PriorOutcomeHistoryTests(unittest.TestCase):
    """R2-S7: prior HIT/MISS outcomes (not only lesson text) reach the prompt,
    are S6-redacted, and the system instruction tells the model to react to a
    MISS. History lacking the new fields still works unchanged."""

    def test_miss_outcome_is_redacted_into_body(self):
        from spray_schema.self_direction_planner import _SYSTEM_INSTRUCTION
        captured = {}
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(_real_shape_plan()),
                                     captured))
        planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="pick one zero-cash opportunity",
            history=(
                {"decision_id": "D-0001",
                 "lesson": "Bearer HISTORYSECRET must not leak",
                 "outcome_verdict": "MISS",
                 "reason": "MISS: executor fell back; session_token=URLSECRET"},
            ))
        prompt = json.loads(captured["body"]["messages"][1]["content"])
        prior = prompt["prior_lessons"]
        self.assertEqual(len(prior), 1)
        self.assertEqual(prior[0]["outcome_verdict"], "MISS")
        self.assertIn("reason", prior[0])
        # Secrets in both the lesson and the reason are redacted.
        blob = json.dumps(prior)
        self.assertNotIn("HISTORYSECRET", blob)
        self.assertNotIn("URLSECRET", blob)
        # The system instruction tells the model to react to a prior MISS.
        self.assertIn("MISS", _SYSTEM_INSTRUCTION)
        self.assertIn("react", _SYSTEM_INSTRUCTION)

    def test_bad_verdict_is_dropped(self):
        captured = {}
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(_real_shape_plan()),
                                     captured))
        planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="pick one zero-cash opportunity",
            history=(
                {"decision_id": "D-0001", "lesson": "a lesson",
                 "outcome_verdict": "MAYBE"},
            ))
        prompt = json.loads(captured["body"]["messages"][1]["content"])
        self.assertNotIn("outcome_verdict", prompt["prior_lessons"][0])

    def test_lesson_only_history_still_works(self):
        """Backward compat: an entry with no verdict/reason keeps its shape."""
        captured = {}
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(_real_shape_plan()),
                                     captured))
        planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="pick one zero-cash opportunity",
            history=({"decision_id": "D-0001", "lesson": "a prior lesson"},))
        prompt = json.loads(captured["body"]["messages"][1]["content"])
        entry = prompt["prior_lessons"][0]
        self.assertEqual(entry["lesson"], "a prior lesson")
        self.assertNotIn("outcome_verdict", entry)
        self.assertNotIn("reason", entry)


class S8EnrichedPromptTests(unittest.TestCase):
    """R2-S8: prior numeric errors and artifact findings reach the request body
    (S6-redacted) and the system instruction requires a quantitative claim and a
    follow-up on a prior over-range error / finding."""

    def test_numeric_error_and_findings_reach_body_redacted(self):
        captured = {}
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(_real_shape_plan()),
                                     captured))
        planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="pick one zero-cash opportunity",
            history=(
                {"decision_id": "D-0001",
                 "lesson": "Bearer HISTORYSECRET must not leak",
                 "outcome_verdict": "MISS",
                 "numeric_error": {"metric_key": "total_files",
                                   "predicted": [1, 2], "measured": 50,
                                   "within_range": False},
                 "findings": ["undocumented=12", "Bearer HISTORYSECRET"]},
            ))
        prompt = json.loads(captured["body"]["messages"][1]["content"])
        prior = prompt["prior_lessons"][0]
        self.assertEqual(prior["numeric_error"]["metric_key"], "total_files")
        self.assertEqual(prior["numeric_error"]["measured"], 50)
        self.assertFalse(prior["numeric_error"]["within_range"])
        self.assertIn("findings", prior)
        self.assertEqual(len(prior["findings"]), 2)
        # The benign measured finding survives; the secret-shaped one is masked.
        self.assertEqual(prior["findings"][0], "undocumented=12")
        # No secret sentinel survives into the body (findings ARE S6-redacted).
        blob = json.dumps(prompt)
        self.assertNotIn("HISTORYSECRET", blob)

    def test_system_instruction_requires_quantitative_claim_and_followup(self):
        from spray_schema.self_direction_planner import _SYSTEM_INSTRUCTION
        self.assertIn("quantitative_claim", _SYSTEM_INSTRUCTION)
        self.assertIn("confidence", _SYSTEM_INSTRUCTION)
        self.assertIn("follow-up", _SYSTEM_INSTRUCTION)
        self.assertIn("followup_decision", _SYSTEM_INSTRUCTION)

    def test_quantitative_claim_is_in_explicit_key_enumeration(self):
        """R2-S8 FEAT-004: quantitative_claim must appear WITHIN the explicit
        'EXACTLY these keys:' enumeration (not only trailing prose), so the live
        model treats it as a required key and emits it on a complete plan."""
        from spray_schema.self_direction_planner import _SYSTEM_INSTRUCTION
        marker = "EXACTLY these keys:"
        self.assertIn(marker, _SYSTEM_INSTRUCTION)
        enumeration = _SYSTEM_INSTRUCTION.split(marker, 1)[1]
        # The enumeration ends at "No prose outside the JSON object."
        enumeration = enumeration.split("No prose outside", 1)[0]
        self.assertIn("quantitative_claim", enumeration)
        # followup_decision is listed as optional within the same enumeration.
        self.assertIn("followup_decision", enumeration)
        self.assertIn("optionally", enumeration)

    def test_system_instruction_has_brevity_hint(self):
        from spray_schema.self_direction_planner import _SYSTEM_INSTRUCTION
        self.assertIn("Keep reasoning brief", _SYSTEM_INSTRUCTION)
        self.assertIn("emit the JSON object promptly", _SYSTEM_INSTRUCTION)

    def test_history_without_s8_fields_still_works(self):
        captured = {}
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(_real_shape_plan()),
                                     captured))
        planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="pick one zero-cash opportunity",
            history=({"decision_id": "D-0001", "lesson": "a prior lesson"},))
        entry = json.loads(
            captured["body"]["messages"][1]["content"])["prior_lessons"][0]
        self.assertNotIn("numeric_error", entry)
        self.assertNotIn("findings", entry)


class S9SupportedAnalysisPromptTests(unittest.TestCase):
    """R2-S9: the system instruction constrains chosen_action to the
    EXECUTOR-SUPPORTED analyses and names each one with its natural keywords, so
    a regression that drops the constraint is caught. No network call is made."""

    def test_system_instruction_names_supported_analyses(self):
        from spray_schema.self_direction_planner import _SYSTEM_INSTRUCTION
        text = _SYSTEM_INSTRUCTION.lower()
        # The six executor-supported analyses must be named with their keywords.
        self.assertIn("markers", text)
        self.assertIn("duplicate", text)
        self.assertIn("docs coverage", text)
        self.assertIn("docstring", text)
        self.assertIn("import graph", text)
        self.assertIn("dependency graph", text)
        self.assertIn("inventory", text)
        # The instruction must tell the model the choice must be executor
        # supported and that an unsupported choice would fall back / be refused.
        self.assertIn("executor-supported", text)
        self.assertIn("fall back", text)

    def test_system_instruction_warns_off_unsupported_analyses(self):
        from spray_schema.self_direction_planner import _SYSTEM_INSTRUCTION
        text = _SYSTEM_INSTRUCTION.lower()
        # The three analyses SPRAY kept wrongly choosing are called out as NOT
        # supported, so the planner does not re-propose them.
        self.assertIn("test-gap", text)
        self.assertIn("license-header", text)
        self.assertIn("dead-export", text)


class WidenedCapsTests(unittest.TestCase):
    """R2-S8 FEAT-004: the committed caps were widened so a complete live plan
    fits (more reasoning-token headroom and a higher transport-timeout ceiling),
    while the request cap and fail-closed validation stay intact."""

    def test_max_output_tokens_widened_to_8000_and_sent_in_body(self):
        from spray_schema.self_direction_planner import _MAX_OUTPUT_TOKENS
        self.assertGreaterEqual(_MAX_OUTPUT_TOKENS, 8000)
        captured = {}
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(_real_shape_plan()),
                                     captured))
        planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="pick one zero-cash opportunity",
            history=())
        self.assertEqual(captured["body"]["max_tokens"], _MAX_OUTPUT_TOKENS)

    def test_timeout_up_to_40_is_accepted(self):
        # A complete live plan takes ~15-25s (variance up to ~33s observed), so
        # the harness must be able to pass a timeout above the old 20s cap.
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID, timeout=40,
            opener=_capturing_opener(_chat_response(_real_shape_plan()), {}))
        self.assertEqual(planner.timeout, 40)

    def test_timeout_above_ceiling_still_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            SelfDirectionPlanner(api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
                                 timeout=41)
        self.assertIn("invalid Meta timeout", str(ctx.exception))

    def test_timeout_below_floor_still_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            SelfDirectionPlanner(api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
                                 timeout=0)
        self.assertIn("invalid Meta timeout", str(ctx.exception))

    def test_request_cap_unchanged(self):
        with self.assertRaises(ValueError):
            SelfDirectionPlanner(api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
                                 max_requests=5)


class QuantitativeClaimRoundTripTests(unittest.TestCase):
    """R2-S8 FEAT-004: a plan that additionally carries a quantitative_claim
    (and followup_decision) parses through choose() unchanged; _validate_plan
    does not reject the extra keys."""

    def test_quantitative_claim_bearing_plan_returned_unchanged(self):
        captured = {}
        source_plan = _plan_with_quantitative_claim()
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(source_plan), captured))
        plan = planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="pick one zero-cash opportunity",
            history=())
        # The parsed plan is returned unchanged, including the extra keys.
        self.assertEqual(plan, source_plan)
        self.assertIn("quantitative_claim", plan)
        self.assertEqual(plan["quantitative_claim"]["metric_key"],
                         "duplicate_basename_groups")
        self.assertEqual(plan["quantitative_claim"]["predicted_low"], 1)
        self.assertEqual(plan["quantitative_claim"]["predicted_high"], 8)
        self.assertEqual(plan["quantitative_claim"]["confidence"], 0.65)
        self.assertIn("followup_decision", plan)
        # The base validator still accepts it.
        _require_plan_shape(plan)


class ApiBaseResolutionTests(unittest.TestCase):
    def _resolved_url(self, **kwargs):
        captured = {}
        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
            opener=_capturing_opener(_chat_response(_real_shape_plan()), captured),
            **kwargs)
        planner.choose(
            observation={"url": "https://host.example/tasks", "controls": []},
            target_text="pick one zero-cash opportunity",
            history=())
        return captured["url"]

    def test_default_base_resolves_to_api_meta_ai(self):
        saved = os.environ.pop("META_API_BASE", None)
        try:
            url = self._resolved_url()
        finally:
            if saved is not None:
                os.environ["META_API_BASE"] = saved
        self.assertEqual(url, "https://api.meta.ai" + _CHAT_COMPLETIONS_PATH)

    def test_env_override_resolves_url(self):
        saved = os.environ.get("META_API_BASE")
        os.environ["META_API_BASE"] = "https://env.meta.example"
        try:
            url = self._resolved_url()
        finally:
            if saved is None:
                os.environ.pop("META_API_BASE", None)
            else:
                os.environ["META_API_BASE"] = saved
        self.assertEqual(url,
                         "https://env.meta.example" + _CHAT_COMPLETIONS_PATH)

    def test_kwarg_override_beats_env(self):
        saved = os.environ.get("META_API_BASE")
        os.environ["META_API_BASE"] = "https://env.meta.example"
        try:
            url = self._resolved_url(api_base="https://kwarg.meta.example")
        finally:
            if saved is None:
                os.environ.pop("META_API_BASE", None)
            else:
                os.environ["META_API_BASE"] = saved
        self.assertEqual(url,
                         "https://kwarg.meta.example" + _CHAT_COMPLETIONS_PATH)

    def test_non_https_base_fails_closed(self):
        with self.assertRaises(ValueError):
            SelfDirectionPlanner(api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID,
                                 api_base="http://insecure.example")


class TransportErrorTests(unittest.TestCase):
    def test_http_error_wrapped_without_leaking_body(self):
        def opener(request, timeout):
            raise HTTPError(request.full_url, 503, "Service Unavailable",
                            {"x-provider": "leak"}, None)

        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID, opener=opener)
        with self.assertRaises(RuntimeError) as ctx:
            planner.choose(
                observation={"url": "https://host.example/tasks",
                             "controls": []},
                target_text="pick one zero-cash opportunity",
                history=())
        message = str(ctx.exception)
        self.assertEqual(message, "self-direction planner returned HTTP 503")
        self.assertNotIn("leak", message)
        self.assertNotIn("Service Unavailable", message)

    def test_url_error_wrapped_without_leaking(self):
        def opener(request, timeout):
            raise URLError("Tunnel connection failed: 403 Forbidden")

        planner = SelfDirectionPlanner(
            api_key=API_KEY_PLACEHOLDER, model_id=MODEL_ID, opener=opener)
        with self.assertRaises(RuntimeError) as ctx:
            planner.choose(
                observation={"url": "https://host.example/tasks",
                             "controls": []},
                target_text="pick one zero-cash opportunity",
                history=())
        message = str(ctx.exception)
        self.assertEqual(
            message, "self-direction planner transport failed (URLError)")
        self.assertNotIn("403", message)
        self.assertNotIn("Forbidden", message)


if __name__ == "__main__":
    unittest.main()
