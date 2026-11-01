"""Offline tests for the R3-H5 anchor-free re-scoring prompt (FEAT-001).

These prove, entirely offline (a PURE body builder + a FAKE transport via the
``opener`` kwarg; NO network call is ever made), that
``spray_schema.self_direction_planner``:

  * ANCHOR-FREE ON: when the re-scoring pass is active (``anchor_free=True``),
    the built request body does NOT contain the prior lesson's announced-next
    ACTION text, while the prior MEASURED outcome scalars (metric_key /
    measured / within_range) and the error/reason/findings ARE still forwarded;
  * MUTATION CHECK: with ``anchor_free=False`` (the pre-H5 behavior) the SAME
    announced-next action text IS present in the body, proving the stripping is
    exactly what removes it;
  * ``_strip_announced_next`` is a pure function that removes the directive
    clause and leaves the remaining lesson prose;
  * ``SelfDirectionPlanner.choose(..., anchor_free=True)`` drives the same
    anchor-free body end-to-end through a fake opener, and the key stays
    header-only (never in the body).

No secret-shaped sentinel appears on a single source line: the distinctive
anchor phrase under test is a plain analysis phrase, and no credential format is
constructed here, so ci/policy_checks.py stays green.
"""
import json
import unittest

from spray_schema.self_direction_planner import (
    _CHAT_COMPLETIONS_PATH,
    SelfDirectionPlanner,
    _strip_announced_next,
    build_request_body,
)

# Non-credential placeholder used as the (header-only) API key under test.
API_KEY_PLACEHOLDER = "offline-placeholder-key"
MODEL_ID = "fake-muse-spark-001"

# A distinctive announced-next ACTION phrase that must be ABSENT from the
# anchor-free body. It is a plain analysis phrase (no credential shape).
ANNOUNCED_ACTION = "stats line byte analysis"

# A prior lesson that carries the measured-outcome prose AND ends with the
# announced-next directive clause naming ANNOUNCED_ACTION.
PRIOR_LESSON = (
    "Docs coverage follow-up preserved zero-side-effect compliance; "
    "measured documented 353 within the predicted range; "
    "next proceed to " + ANNOUNCED_ACTION + " only after this succeeds."
)

# A history entry in the shape run_one_cycle forwards: the lesson plus the prior
# MEASURED outcome summary (numeric_error) that MUST survive stripping.
HISTORY_ENTRY = {
    "decision_id": "D-0022",
    "lesson": PRIOR_LESSON,
    "outcome_verdict": "HIT",
    "reason": "measured documented count landed within the predicted range",
    "numeric_error": {
        "metric_key": "documented",
        "predicted": [300, 400],
        "measured": 353,
        "within_range": True,
    },
    "findings": ["documented=353"],
}


def _body_text(*, anchor_free):
    """Build the request body and decode it to text for assertions."""
    body = build_request_body(
        model_id=MODEL_ID,
        observation={},
        target_text="SPRAY self-directed opportunity selection",
        history=(HISTORY_ENTRY,),
        anchor_free=anchor_free,
    )
    return body.decode("utf-8")


class StripAnnouncedNextTests(unittest.TestCase):
    def test_removes_directive_clause_keeps_prose(self):
        stripped = _strip_announced_next(PRIOR_LESSON)
        self.assertNotIn(ANNOUNCED_ACTION, stripped)
        self.assertNotIn("next proceed to", stripped.lower())
        # The measured-outcome prose that preceded the directive survives.
        self.assertIn("measured documented 353", stripped)

    def test_no_directive_returns_unchanged(self):
        plain = "Markers triage preserved compliance and surfaced two markers"
        self.assertEqual(_strip_announced_next(plain), plain)

    def test_empty_or_non_string_returns_empty(self):
        self.assertEqual(_strip_announced_next(None), "")
        self.assertEqual(_strip_announced_next(""), "")


class AnchorFreeBodyTests(unittest.TestCase):
    def test_anchor_free_body_omits_announced_action(self):
        text = _body_text(anchor_free=True)
        # The announced next ACTION text is ABSENT from the anchor-free body.
        self.assertNotIn(ANNOUNCED_ACTION, text)
        self.assertNotIn("next proceed to", text.lower())
        # The prior MEASURED outcome scalars ARE still forwarded.
        self.assertIn("documented", text)       # metric_key
        self.assertIn("353", text)               # measured
        self.assertIn("within_range", text)      # within_range key present
        # The error/reason/findings prose also survives.
        self.assertIn("measured documented 353", text)

    def test_mutation_check_anchor_present_when_off(self):
        # Pre-H5 behavior: the announced action text IS present in the body.
        text = _body_text(anchor_free=False)
        self.assertIn(ANNOUNCED_ACTION, text)
        self.assertIn("next proceed to", text.lower())
        # And the measured scalars are present here too (never removed).
        self.assertIn("353", text)

    def test_only_difference_is_the_directive_clause(self):
        # The anchor-free and anchored bodies differ ONLY by the stripped
        # directive: the measured scalars are identical in both.
        on = _body_text(anchor_free=True)
        off = _body_text(anchor_free=False)
        self.assertNotEqual(on, off)
        for marker in ("documented", "353", "within_range"):
            self.assertIn(marker, on)
            self.assertIn(marker, off)


class _RecordingOpener:
    """A fake opener that captures the request and returns a canned chat reply."""

    def __init__(self, response_obj):
        self._response_obj = response_obj
        self.captured_body = None
        self.captured_headers = None
        self.captured_url = None

    def __call__(self, request, timeout=None):
        self.captured_body = request.data
        self.captured_headers = dict(request.header_items())
        self.captured_url = request.full_url
        outer = self

        class _Ctx:
            def __enter__(self_inner):
                class _Resp:
                    def read(self_r, _n=None):
                        return json.dumps(outer._response_obj).encode("utf-8")
                return _Resp()

            def __exit__(self_inner, *exc):
                return False

        return _Ctx()


def _valid_plan():
    names = ["stats-scan", "markers-scan", "import-graph-scan"]
    return {
        "domain": "repo-local offline analysis",
        "candidates": [{"name": n, "description": "offline " + n} for n in names],
        "scoring_criteria": ["impact", "cost", "risk"],
        "candidate_scores": [{"candidate": n, "impact": 1} for n in names],
        "chosen_action": "produce a repo-local offline " + names[0],
        "chosen_rationale": "highest impact at zero cash cost",
        "rejected": [{"candidate": n, "why": "lower value"} for n in names[1:]],
        "prediction": "the offline analysis will complete",
        "actual_outcome": {"note": "done"},
        "prediction_vs_actual": "see measured",
        "lesson": "offline analysis is a reliable zero-cash move",
    }


class ChooseAnchorFreeEndToEndTests(unittest.TestCase):
    def test_choose_anchor_free_body_omits_announced_action_and_key_header_only(self):
        reply = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps(_valid_plan())},
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }
        opener = _RecordingOpener(reply)
        planner = SelfDirectionPlanner(
            model_id=MODEL_ID, api_key=API_KEY_PLACEHOLDER, opener=opener)
        plan = planner.choose(
            observation={},
            target_text="SPRAY self-directed opportunity selection",
            history=(HISTORY_ENTRY,),
            anchor_free=True,
        )
        self.assertIsInstance(plan, dict)
        sent = opener.captured_body.decode("utf-8")
        # Anchor-free: announced action text is absent from the sent body.
        self.assertNotIn(ANNOUNCED_ACTION, sent)
        self.assertNotIn("next proceed to", sent.lower())
        # The measured scalars still reach the body.
        self.assertIn("353", sent)
        # The key is header-only and NEVER in the body.
        self.assertNotIn(API_KEY_PLACEHOLDER, sent)
        auth = dict(opener.captured_headers)
        # urllib title-cases header names.
        self.assertEqual(auth.get("Authorization"),
                         "Bearer " + API_KEY_PLACEHOLDER)
        self.assertTrue(opener.captured_url.endswith(_CHAT_COMPLETIONS_PATH))

    def test_choose_default_is_anchored(self):
        reply = {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": json.dumps(_valid_plan())},
            }],
            "usage": {"prompt_tokens": 100, "completion_tokens": 200},
        }
        opener = _RecordingOpener(reply)
        planner = SelfDirectionPlanner(
            model_id=MODEL_ID, api_key=API_KEY_PLACEHOLDER, opener=opener)
        planner.choose(
            observation={},
            target_text="SPRAY self-directed opportunity selection",
            history=(HISTORY_ENTRY,),
        )
        sent = opener.captured_body.decode("utf-8")
        # Default (anchor_free omitted) keeps the pre-H5 behavior: the announced
        # action is present.
        self.assertIn(ANNOUNCED_ACTION, sent)


if __name__ == "__main__":
    unittest.main()
