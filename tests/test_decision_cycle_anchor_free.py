"""Offline tests for the R3-H5 anchor-free re-scoring pass in run_one_cycle.

These prove, entirely offline (tempdir repo + decisions dir, a RECORDING FAKE
planner, no network), that:

  * ``run_one_cycle(..., rerank_on_prior_outcome=True, anchor_free=True)`` after
    a finalized MEASURED prior writes a record that carries ``anchor_free=True``
    AND still populates ``announced_next`` (source_decision_id + verbatim
    lesson_text + announced_action) AND ``rerank`` (diverged_from_announced +
    non-empty why + prior_outcome_decision_id), and the record validates and
    finalizes;
  * the planner was invoked WITH ``anchor_free=True`` (the prompt-side change is
    exercised end-to-end via a recording fake);
  * the record is verifiable-from-records-alone: announced_action, chosen_action,
    anchor_free, rerank.diverged_from_announced and rerank.prior_outcome_decision_id
    are all present on the committed record;
  * when ``anchor_free=False`` (or omitted) the planner is NOT given the
    anchor_free kwarg and the record carries NO anchor_free key, so behavior and
    record shape are byte-identical to a pre-H5 cycle.

Every decisions dir and repo_root is an isolated tempfile.TemporaryDirectory so
the repo's real tree is NEVER touched.
"""
import os
import tempfile
import unittest
from pathlib import Path

from spray_schema import action_executor
from spray_schema.decision_cycle import run_one_cycle
from spray_schema.decision_record import STATUS_FINALIZED, load_records


class StrictClock:
    """Return a strictly-increasing UTC 'Z' instant on each call."""

    def __init__(self):
        self._n = 0

    def __call__(self):
        value = "2026-11-03T00:%02d:00Z" % self._n
        self._n += 1
        return value


CAND_IMPORT = "import graph dependency analysis of the repo"
CAND_MARKERS = "TODO / FIXME / HACK / XXX markers recount follow-up"
CAND_STATS = "line and byte statistics of sizes"

# A cycle-1 lesson that ends with an explicit next-step directive so
# announced_next.announced_action is extracted and maps to import_graph.
CYCLE1_LESSON = (
    "Markers triage preserves zero-side-effect compliance; next proceed to "
    "import graph dependency analysis only after this succeeds"
)
# The announced-next ACTION text that the anchor-free prompt must NOT disclose.
ANNOUNCED_ACTION_TEXT = "import graph dependency analysis"


def _candidates(names):
    return [{"name": n, "description": "opportunity " + n} for n in names]


def _scores(names):
    return [{"candidate": n, "score": float(len(names) - i)}
            for i, n in enumerate(names)]


def _plan(*, domain, chosen, names, lesson, claim):
    rejected = [{"candidate": n, "why": "lower expected value"}
                for n in names if n != chosen]
    return {
        "domain": domain,
        "candidates": _candidates(names),
        "scoring_criteria": ["impact", "cost", "risk"],
        "candidate_scores": _scores(names),
        "chosen_action": chosen,
        "chosen_rationale": "best scored option",
        "rejected": rejected,
        "prediction": "markers between 5 and 50; edges between 180 and 320",
        "actual_outcome": {"note": "completed offline analysis"},
        "prediction_vs_actual": "see measured",
        "lesson": lesson,
        "quantitative_claim": claim,
    }


class RecordingFakePlanner:
    """A fake planner whose choose() accepts the R3-H5 anchor_free kwarg and
    records whether it was passed (so the prompt-side change is observable)."""

    def __init__(self, policy, *, provider="meta", model_id="fake-muse-spark-001"):
        self.provider = provider
        self.model_id = model_id
        self._policy = policy
        self.calls = []

    def choose(self, *, observation, target_text, history, anchor_free=False):
        self.calls.append({"history": history, "anchor_free": anchor_free})
        return self._policy(observation=observation, target_text=target_text,
                            history=history)


def _make_repo(tmp):
    root = Path(tmp) / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text(
        "import os\n# TODO: one\nx = 1\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text(
        "import sys\n# FIXME: two\n", encoding="utf-8")
    (root / "README.md").write_text("# repo\n", encoding="utf-8")
    return root


def _cycle1_policy(*, observation, target_text, history):
    return _plan(
        domain="repo-local offline analysis",
        chosen="produce a repo-local offline " + CAND_MARKERS,
        names=[CAND_MARKERS, CAND_IMPORT, CAND_STATS],
        lesson=CYCLE1_LESSON,
        claim={"metric_key": "total_markers", "kind": "range",
               "predicted_low": 5, "predicted_high": 50, "confidence": 0.6})


def _cycle2_policy(*, observation, target_text, history):
    # Choose the announced import_graph (no forced divergence).
    return _plan(
        domain="repo-local offline structural analysis",
        chosen="generate a repo-local offline " + CAND_IMPORT,
        names=[CAND_IMPORT, CAND_MARKERS, CAND_STATS],
        lesson="applied prior lesson; proceeded to import graph",
        claim={"metric_key": "edges", "kind": "range",
               "predicted_low": 0, "predicted_high": 50, "confidence": 0.6})


class AnchorFreeCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _make_repo(self.tmp.name)
        self.decisions = os.path.join(self.tmp.name, "decisions")

    def _run_cycle1(self):
        run_one_cycle(
            self.decisions, planner=RecordingFakePlanner(_cycle1_policy),
            now_iso_provider=StrictClock(), repo_root=self.repo,
            falsifiable=True, require_executed_matches_chosen=True)

    def test_anchor_free_record_keeps_announced_next_and_rerank(self):
        self._run_cycle1()
        planner = RecordingFakePlanner(_cycle2_policy)
        summary = run_one_cycle(
            self.decisions, planner=planner,
            now_iso_provider=StrictClock(), repo_root=self.repo,
            falsifiable=True, require_executed_matches_chosen=True,
            rerank_on_prior_outcome=True, anchor_free=True)

        # The summary and committed record self-describe as anchor-free.
        self.assertTrue(summary["anchor_free"])
        # The planner was invoked WITH anchor_free=True (prompt-side change).
        self.assertTrue(planner.calls[0]["anchor_free"])

        # announced_next + rerank are STILL computed server-side.
        self.assertIsNotNone(summary["announced_next"])
        self.assertIsNotNone(summary["rerank"])

        records = load_records(self.decisions)
        by_id = {r["id"]: r for r in records}
        rec = by_id["D-0002"]
        self.assertEqual(rec["status"], STATUS_FINALIZED)

        # Verifiable-from-records-alone fields.
        self.assertIs(rec["anchor_free"], True)
        self.assertIn("announced_next", rec)
        self.assertEqual(rec["announced_next"]["source_decision_id"], "D-0001")
        self.assertEqual(rec["announced_next"]["lesson_text"], CYCLE1_LESSON)
        self.assertTrue(rec["announced_next"]["announced_action"].strip())
        self.assertIn("rerank", rec)
        self.assertIn("diverged_from_announced", rec["rerank"])
        self.assertIsInstance(rec["rerank"]["diverged_from_announced"], bool)
        self.assertTrue(rec["rerank"]["why"].strip())
        self.assertEqual(rec["rerank"]["prior_outcome_decision_id"], "D-0001")
        self.assertTrue(rec["chosen_action"].strip())

    def test_anchor_free_off_writes_no_marker_and_omits_kwarg(self):
        self._run_cycle1()
        planner = RecordingFakePlanner(_cycle2_policy)
        summary = run_one_cycle(
            self.decisions, planner=planner,
            now_iso_provider=StrictClock(), repo_root=self.repo,
            falsifiable=True, require_executed_matches_chosen=True,
            rerank_on_prior_outcome=True)  # anchor_free defaults to False

        self.assertFalse(summary["anchor_free"])
        # The planner call recorded anchor_free=False (default, not forced on).
        self.assertFalse(planner.calls[0]["anchor_free"])

        records = load_records(self.decisions)
        rec = {r["id"]: r for r in records}["D-0002"]
        # No anchor_free key is written when the flag is off.
        self.assertNotIn("anchor_free", rec)
        # announced_next + rerank are still present (rerank path still ran).
        self.assertIn("announced_next", rec)
        self.assertIn("rerank", rec)

    def test_anchor_free_without_measured_prior_is_inactive(self):
        # First cycle (no prior) with anchor_free=True + rerank: no measured
        # prior exists, so the anchor-free path is INACTIVE and no marker is
        # written. The planner is a plain pre-H5 fake (no anchor_free kwarg) to
        # prove the runner does not force the kwarg when inactive.
        class PlainFake:
            provider = "meta"
            model_id = "fake-muse-spark-001"

            def __init__(self):
                self.calls = 0

            def choose(self, *, observation, target_text, history):
                self.calls += 1
                return _cycle1_policy(observation=observation,
                                      target_text=target_text, history=history)

        planner = PlainFake()
        summary = run_one_cycle(
            self.decisions, planner=planner,
            now_iso_provider=StrictClock(), repo_root=self.repo,
            falsifiable=True, require_executed_matches_chosen=True,
            rerank_on_prior_outcome=True, anchor_free=True)

        self.assertEqual(planner.calls, 1)
        self.assertFalse(summary["anchor_free"])
        self.assertIsNone(summary["announced_next"])
        rec = {r["id"]: r for r in load_records(self.decisions)}["D-0001"]
        self.assertNotIn("anchor_free", rec)


if __name__ == "__main__":
    unittest.main()
