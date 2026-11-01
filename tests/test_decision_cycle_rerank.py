"""Offline tests for the R3-H4 re-rank SECOND scoring pass in run_one_cycle.

These prove, entirely offline (tempdir repo + decisions dir, a FAKE planner, no
network), that:

  * when ``rerank_on_prior_outcome=True`` and a most-recent FINALIZED prior
    carries a MEASURED outcome, ``run_one_cycle`` writes a record carrying
    ``announced_next`` (citing that prior's verbatim lesson) and a ``rerank``
    block whose ``prior_outcome_decision_id`` cites that prior, and the record
    validates and finalizes;
  * the SECOND pass is demonstrably DERIVED FROM the prior MEASURED outcome, not
    model prose: flipping the fake prior ``within_range`` (surprising vs calm)
    changes the second-pass ranking (``new_rank`` order and the promoted
    candidate), and can flip ``diverged_from_announced``;
  * the ranking unit helper ``_rerank_candidates_on_prior_outcome`` reads the
    prior measured scalars directly;
  * with no finalized measured prior the cycle writes NO announced_next/rerank
    (it behaves as a plain cycle), so the feature is backward compatible.

Every decisions dir and repo_root is an isolated tempfile.TemporaryDirectory so
the repo's real tree is NEVER touched.
"""
import os
import tempfile
import unittest
from pathlib import Path

from spray_schema import action_executor
from spray_schema import decision_cycle
from spray_schema.decision_cycle import run_one_cycle
from spray_schema.decision_record import STATUS_FINALIZED, load_records


class StrictClock:
    """Return a strictly-increasing UTC 'Z' instant on each call."""

    def __init__(self):
        self._n = 0

    def __call__(self):
        value = "2026-11-02T00:%02d:00Z" % self._n
        self._n += 1
        return value


# Candidate names phrased so each maps (via action_executor) to a known
# analysis: "markers" -> markers, "import graph" -> import_graph.
CAND_IMPORT = "import graph dependency analysis of the repo"
CAND_MARKERS = "TODO / FIXME / HACK / XXX markers recount follow-up"
CAND_STATS = "line and byte statistics of sizes"

# A cycle-1 lesson that ends with an explicit next-step directive so
# announced_next.announced_action is extracted and maps to import_graph.
CYCLE1_LESSON = (
    "Markers triage preserves zero-side-effect compliance; next proceed to "
    "import graph dependency analysis only after this succeeds"
)


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


class FakePlanner:
    def __init__(self, policy, *, provider="meta", model_id="fake-muse-spark-001"):
        self.provider = provider
        self.model_id = model_id
        self._policy = policy
        self.calls = []

    def choose(self, *, observation, target_text, history):
        self.calls.append({"history": history})
        return self._policy(observation=observation, target_text=target_text,
                            history=history)


def _make_repo(tmp):
    """Create a tiny repo tree so the real analyses run fast + deterministic."""
    root = Path(tmp) / "repo"
    (root / "pkg").mkdir(parents=True)
    (root / "pkg" / "a.py").write_text(
        "import os\n# TODO: one\nx = 1\n", encoding="utf-8")
    (root / "pkg" / "b.py").write_text(
        "import sys\n# FIXME: two\n", encoding="utf-8")
    (root / "README.md").write_text("# repo\n", encoding="utf-8")
    return root


def _cycle1_policy(*, observation, target_text, history):
    # Cycle 1: a falsifiable markers scan that produces a measured outcome.
    return _plan(
        domain="repo-local offline analysis",
        chosen="produce a repo-local offline " + CAND_MARKERS,
        names=[CAND_MARKERS, CAND_IMPORT, CAND_STATS],
        lesson=CYCLE1_LESSON,
        claim={"metric_key": "total_markers", "kind": "range",
               "predicted_low": 5, "predicted_high": 50, "confidence": 0.6},
    )


class RerankCycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _make_repo(self.tmp.name)
        self.decisions = os.path.join(self.tmp.name, "decisions")

    def _run_cycle1(self):
        run_one_cycle(
            self.decisions, planner=FakePlanner(_cycle1_policy),
            now_iso_provider=StrictClock(), repo_root=self.repo,
            falsifiable=True, require_executed_matches_chosen=True)

    def test_cycle2_carries_announced_next_and_rerank(self):
        self._run_cycle1()
        # Cycle 2: choose the announced import_graph (no divergence expected
        # when the prior markers scan was calm, but the fixture repo has only 2
        # markers so total_markers=2 is within [5,50]? No: 2 < 5 -> outside).
        def _cycle2_policy(*, observation, target_text, history):
            return _plan(
                domain="repo-local offline structural analysis",
                chosen="generate a repo-local offline " + CAND_IMPORT,
                names=[CAND_IMPORT, CAND_MARKERS, CAND_STATS],
                lesson="applied prior lesson; proceeded to import graph",
                claim={"metric_key": "edges", "kind": "range",
                       "predicted_low": 0, "predicted_high": 50,
                       "confidence": 0.6})

        summary = run_one_cycle(
            self.decisions, planner=FakePlanner(_cycle2_policy),
            now_iso_provider=StrictClock(), repo_root=self.repo,
            falsifiable=True, require_executed_matches_chosen=True,
            rerank_on_prior_outcome=True)

        self.assertIsNotNone(summary["announced_next"])
        self.assertIsNotNone(summary["rerank"])
        self.assertEqual(summary["announced_next"]["source_decision_id"],
                         "D-0001")
        self.assertEqual(summary["rerank"]["prior_outcome_decision_id"],
                         "D-0001")

        # The committed D-0002 is FINALIZED and validates against its priors.
        records = load_records(self.decisions)
        by_id = {r["id"]: r for r in records}
        self.assertEqual(by_id["D-0002"]["status"], STATUS_FINALIZED)
        self.assertIn("announced_next", by_id["D-0002"])
        self.assertIn("rerank", by_id["D-0002"])
        # The rerank block echoes the prior MEASURED outcome scalars.
        echo = by_id["D-0002"]["rerank"]["prior_outcome"]
        self.assertEqual(echo["metric_key"], "total_markers")
        self.assertIn("within_range", echo)

    def test_no_measured_prior_omits_fields(self):
        # First cycle with rerank_on_prior_outcome=True: there is no prior, so
        # announced_next / rerank are omitted (backward compatible).
        summary = run_one_cycle(
            self.decisions, planner=FakePlanner(_cycle1_policy),
            now_iso_provider=StrictClock(), repo_root=self.repo,
            falsifiable=True, require_executed_matches_chosen=True,
            rerank_on_prior_outcome=True)
        self.assertIsNone(summary["announced_next"])
        self.assertIsNone(summary["rerank"])
        record = load_records(self.decisions)[0]
        self.assertNotIn("announced_next", record)
        self.assertNotIn("rerank", record)


def _cycle1_calm_policy(*, observation, target_text, history):
    # Cycle 1: a falsifiable markers scan whose predicted range CONTAINS the
    # fixture repo's real total_markers (2 markers: one TODO, one FIXME), so the
    # measured outcome is within_range=True -> a CALM prior (surprised=False).
    return _plan(
        domain="repo-local offline analysis",
        chosen="produce a repo-local offline " + CAND_MARKERS,
        names=[CAND_MARKERS, CAND_IMPORT, CAND_STATS],
        lesson=CYCLE1_LESSON,
        claim={"metric_key": "total_markers", "kind": "range",
               "predicted_low": 0, "predicted_high": 50, "confidence": 0.6},
    )


class RerankWhyAccuracyTests(unittest.TestCase):
    """R3-H6 generator fix: the rerank.why prose must be ACCURATE.

    When the cycle-2 CHOSEN analysis differs from the announced next action but
    the prior outcome is CALM (surprised=False, so the strict divergence flag
    stays False), the generated ``why`` must NOT claim the chosen action "stays
    consistent with the announced next step"; it must state the chosen analysis
    differs. When the chosen analysis EQUALS the announced one, the accurate
    "stays consistent" wording is still emitted.
    """

    STAYS_CONSISTENT = "stays consistent with the announced next step"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = _make_repo(self.tmp.name)
        self.decisions = os.path.join(self.tmp.name, "decisions")

    def _run_cycle1_calm(self):
        run_one_cycle(
            self.decisions, planner=FakePlanner(_cycle1_calm_policy),
            now_iso_provider=StrictClock(), repo_root=self.repo,
            falsifiable=True, require_executed_matches_chosen=True)

    def _cycle2(self, policy):
        return run_one_cycle(
            self.decisions, planner=FakePlanner(policy),
            now_iso_provider=StrictClock(), repo_root=self.repo,
            falsifiable=True, require_executed_matches_chosen=True,
            rerank_on_prior_outcome=True)

    def test_chosen_differs_from_announced_calm_prior_why_is_accurate(self):
        # Calm cycle-1 prior; announced_action maps to import_graph. Cycle 2
        # CHOOSES stats -> chosen_analysis (stats) != announced_analysis
        # (import_graph), but the calm prior keeps surprised=False so the strict
        # divergence flag stays False.
        self._run_cycle1_calm()

        def _cycle2_policy(*, observation, target_text, history):
            return _plan(
                domain="repo-local offline statistical analysis",
                chosen="generate a repo-local offline " + CAND_STATS,
                names=[CAND_STATS, CAND_IMPORT, CAND_MARKERS],
                lesson="applied prior lesson; proceeded to size statistics",
                claim={"metric_key": "total_bytes", "kind": "range",
                       "predicted_low": 0, "predicted_high": 100000,
                       "confidence": 0.6})

        summary = self._cycle2(_cycle2_policy)
        self.assertIsNotNone(summary["rerank"])

        records = load_records(self.decisions)
        by_id = {r["id"]: r for r in records}
        rerank = by_id["D-0002"]["rerank"]
        # (1) The authoritative machine field is False (no strict divergence).
        self.assertFalse(rerank["diverged_from_announced"])
        why = rerank["why"]
        # (2) The inaccurate wording must NOT appear.
        self.assertNotIn(self.STAYS_CONSISTENT, why)
        # (3) why is non-empty and states the chosen analysis differs.
        self.assertTrue(why)
        self.assertIn("differs from the announced next step", why)

    def test_chosen_equals_announced_calm_prior_why_stays_consistent(self):
        # Calm cycle-1 prior; cycle 2 CHOOSES the announced import_graph, so
        # chosen_analysis == announced_analysis and the accurate "stays
        # consistent" wording is still emitted.
        self._run_cycle1_calm()

        def _cycle2_policy(*, observation, target_text, history):
            return _plan(
                domain="repo-local offline structural analysis",
                chosen="generate a repo-local offline " + CAND_IMPORT,
                names=[CAND_IMPORT, CAND_MARKERS, CAND_STATS],
                lesson="applied prior lesson; proceeded to import graph",
                claim={"metric_key": "edges", "kind": "range",
                       "predicted_low": 0, "predicted_high": 50,
                       "confidence": 0.6})

        summary = self._cycle2(_cycle2_policy)
        self.assertIsNotNone(summary["rerank"])

        records = load_records(self.decisions)
        by_id = {r["id"]: r for r in records}
        rerank = by_id["D-0002"]["rerank"]
        self.assertFalse(rerank["diverged_from_announced"])
        self.assertIn(self.STAYS_CONSISTENT, rerank["why"])


class RerankDerivedFromPriorOutcomeTests(unittest.TestCase):
    """Prove the SECOND pass is derived from the PRIOR MEASURED OUTCOME: the
    same plan re-ranked against a SURPRISING (within_range False) prior differs
    from the ranking against a CALM (within_range True) prior."""

    def _plan_with_candidates(self):
        # Order: announced import graph first, then a markers recount that
        # re-examines the prior total_markers metric.
        return {
            "candidates": _candidates([CAND_IMPORT, CAND_MARKERS, CAND_STATS]),
        }

    def test_surprising_prior_promotes_reexam_candidate(self):
        plan = self._plan_with_candidates()
        surprising = {"metric_key": "total_markers", "measured": 154,
                      "within_range": False, "outcome_verdict": "MISS"}
        result = decision_cycle._rerank_candidates_on_prior_outcome(
            plan, surprising)
        self.assertIsNotNone(result)
        entries, top_candidate, surprised = result
        self.assertTrue(surprised)
        # The markers recount (re-examines the surprising prior metric) is
        # promoted to new_rank 1.
        self.assertIn("markers", top_candidate.lower())
        new_rank = {e["candidate"]: e["new_rank"] for e in entries}
        markers_entry = next(c for c in new_rank if "markers" in c.lower())
        self.assertEqual(new_rank[markers_entry], 1)

    def test_calm_prior_keeps_first_pass_order(self):
        plan = self._plan_with_candidates()
        calm = {"metric_key": "total_markers", "measured": 25,
                "within_range": True, "outcome_verdict": "HIT"}
        result = decision_cycle._rerank_candidates_on_prior_outcome(plan, calm)
        self.assertIsNotNone(result)
        entries, top_candidate, surprised = result
        self.assertFalse(surprised)
        # Calm prior: new_rank == old_rank for every candidate (no reorder).
        for e in entries:
            self.assertEqual(e["old_rank"], e["new_rank"])
        # The top stays the planner's first candidate (import graph).
        self.assertIn("import", top_candidate.lower())

    def test_flipping_prior_outcome_changes_the_rerank(self):
        plan = self._plan_with_candidates()
        surprising = {"metric_key": "total_markers", "measured": 154,
                      "within_range": False, "outcome_verdict": "MISS"}
        calm = {"metric_key": "total_markers", "measured": 25,
                "within_range": True, "outcome_verdict": "HIT"}
        s_entries, s_top, _ = decision_cycle._rerank_candidates_on_prior_outcome(
            plan, surprising)
        c_entries, c_top, _ = decision_cycle._rerank_candidates_on_prior_outcome(
            plan, calm)
        # The ONLY difference between the two calls is the prior measured
        # outcome, and it changes the re-rank result (top candidate differs).
        self.assertNotEqual(s_top, c_top)
        self.assertNotEqual(
            [e["new_rank"] for e in s_entries],
            [e["new_rank"] for e in c_entries])


if __name__ == "__main__":
    unittest.main()
