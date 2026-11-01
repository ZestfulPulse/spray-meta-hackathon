"""Offline tests for the R3-H4 re-rank record machinery.

These prove, additively and backward-compatibly (pure stdlib, no network),
that the new create-time fields and GATED rules behave as designed:

  * a valid ``announced_next`` + ``rerank`` record shape validates;
  * ``rerank_missing_prior_outcome_citation`` FIRES when the rerank block omits
    or carries an invalid ``prior_outcome_decision_id`` (absent, malformed, not
    found among priors, or pointing at a non-finalized / non-measured prior),
    and PASSES when the cited prior is a finalized measured record;
  * ``announced_next_lesson_mismatch`` fires when ``announced_next.lesson_text``
    does not match the cited prior record's ``lesson`` verbatim;
  * a ``rerank`` claiming ``diverged_from_announced: true`` whose
    ``chosen_action`` maps to a DIFFERENT analysis than ``announced_action``
    validates, and ``divergence_not_reflected_in_choice`` fires when the claimed
    divergence is NOT reflected in the choice (chosen maps to the SAME analysis
    as announced);
  * the two fields are in OPTIONAL_FIELD_NAMES and NOT in _FINALIZE_FIELDS;
  * BACKWARD COMPAT: every committed record D-0001..D-0018 (none carry the new
    fields) still validates against its priors with the decisions dir as the
    artifact resolution root.

Every decisions dir is an isolated tempfile.TemporaryDirectory so the repo's
real decisions/ dir is NEVER touched (the only exception is the read-only
backward-compat test that loads the committed records).
"""
import os
import tempfile
import unittest
from pathlib import Path

from spray_schema import action_executor
from spray_schema.decision_record import (
    OPTIONAL_FIELD_NAMES,
    STATUS_FINALIZED,
    _FINALIZE_FIELDS,
    create_record,
    load_records,
    validate_record,
)


# A prior lesson whose text ends with an explicit "next proceed to ..." step so
# announced_next.announced_action can be extracted from / matched against it.
PRIOR_LESSON = (
    "Markers triage preserves zero-side-effect compliance adding distinct value"
    " beyond inventory baseline; next proceed to import graph dependency "
    "analysis only after this succeeds continuing incremental complexity"
)
# The announced next step phrased so it maps to the import_graph analysis.
ANNOUNCED_IMPORT_GRAPH = "import graph dependency analysis"
# A chosen action phrased to map to a DIFFERENT analysis (markers) than the
# announced import_graph, used to demonstrate a REAL divergence.
CHOSEN_MARKERS = "run a TODO / FIXME / HACK / XXX markers recount follow-up"


def _base_kwargs(**overrides):
    kwargs = dict(
        observed_state={"note": "observed"},
        prior_decision_ids_read=[],
        candidates=[
            {"name": "a", "description": "do A"},
            {"name": "b", "description": "do B"},
            {"name": "c", "description": "do C"},
        ],
        scoring_criteria=["impact", "cost", "risk"],
        candidate_scores=[
            {"candidate": "a", "impact": 3},
            {"candidate": "b", "impact": 2},
            {"candidate": "c", "impact": 1},
        ],
        chosen_action="generate an import graph dependency analysis artifact",
        chosen_rationale="best option",
        rejected=[{"candidate": "b", "why": "lower"},
                  {"candidate": "c", "why": "risk"}],
        prediction="will produce an artifact",
        lessons_applied=[],
    )
    kwargs.update(overrides)
    return kwargs


def _finalized_measured_prior(record_id="D-0001", lesson=PRIOR_LESSON):
    """A minimal FINALIZED prior record carrying a measured outcome.

    Carries a numeric_error (within_range False) and outcome_verdict HIT so the
    rerank citation rule recognizes it as a finalized measured record and the
    verbatim lesson check can match ``lesson`` against announced_next.
    """
    return {
        "id": record_id,
        "observed_state": {"note": "prior"},
        "prior_decision_ids_read": [],
        "candidates": [
            {"name": "a", "description": "do A"},
            {"name": "b", "description": "do B"},
            {"name": "c", "description": "do C"},
        ],
        "scoring_criteria": ["impact"],
        "candidate_scores": [{"candidate": "a", "impact": 1}],
        "chosen_action": "produce a markers scan",
        "chosen_rationale": "prior rationale",
        "rejected": [{"candidate": "b", "why": "lower"}],
        "prediction": "markers between 5 and 50",
        "status": STATUS_FINALIZED,
        "actual_outcome": {"measured": True},
        "prediction_vs_actual": "outside the predicted range",
        "lesson": lesson,
        "lessons_applied": [],
        "outcome_verdict": "HIT",
        "numeric_error": {
            "metric_key": "total_markers",
            "predicted": [5, 50],
            "measured": 25,
            "absolute_error": 0,
            "relative_error": 0.0,
            # within_range True so this prior does NOT trigger the S8
            # follow-up-on-finding rule; these tests exercise only the R3-H4
            # rerank machinery. (A separate cycle test exercises the surprising
            # within_range=False case end to end.)
            "within_range": True,
            "confidence": 0.65,
        },
    }


def _announced_next(source_id="D-0001", lesson=PRIOR_LESSON,
                    announced_action=ANNOUNCED_IMPORT_GRAPH):
    return {
        "source_decision_id": source_id,
        "lesson_text": lesson,
        "announced_action": announced_action,
    }


def _rerank(prior_id="D-0001", diverged=False, why="second pass reads prior",
            prior_outcome=None, candidates=None):
    if prior_outcome is None:
        prior_outcome = {
            "metric_key": "total_markers",
            "measured": 25,
            "within_range": True,
            "outcome_verdict": "HIT",
        }
    if candidates is None:
        candidates = [
            {"candidate": "import graph dependency analysis",
             "old_rank": 1, "new_rank": 2},
            {"candidate": "markers recount follow-up",
             "old_rank": 2, "new_rank": 1},
        ]
    return {
        "prior_outcome_decision_id": prior_id,
        "prior_outcome": prior_outcome,
        "candidates": candidates,
        "diverged_from_announced": diverged,
        "why": why,
    }


class FieldPresenceTests(unittest.TestCase):
    def test_fields_in_optional_names(self):
        self.assertIn("announced_next", OPTIONAL_FIELD_NAMES)
        self.assertIn("rerank", OPTIONAL_FIELD_NAMES)

    def test_fields_not_in_finalize_fields(self):
        # Both are create-time and immutable at finalize.
        self.assertNotIn("announced_next", _FINALIZE_FIELDS)
        self.assertNotIn("rerank", _FINALIZE_FIELDS)


class ValidShapeTests(unittest.TestCase):
    def setUp(self):
        self.priors = [_finalized_measured_prior("D-0001")]

    def _candidate_record(self, **overrides):
        record = {
            "id": "D-0002",
            "observed_state": {"note": "observed"},
            "prior_decision_ids_read": ["D-0001"],
            "candidates": [
                {"name": "a", "description": "do A"},
                {"name": "b", "description": "do B"},
                {"name": "c", "description": "do C"},
            ],
            "scoring_criteria": ["impact"],
            "candidate_scores": [{"candidate": "a", "impact": 1}],
            "chosen_action": "generate an import graph dependency analysis",
            "chosen_rationale": "best option",
            "rejected": [{"candidate": "b", "why": "lower"}],
            "prediction": "edges between 180 and 320",
            "status": STATUS_FINALIZED,
            "actual_outcome": {"measured": True},
            "prediction_vs_actual": "within",
            "lesson": "applied prior lesson; proceeded to import graph",
            "lessons_applied": [
                {"decision_id": "D-0001", "lesson": PRIOR_LESSON}],
            "announced_next": _announced_next(),
            "rerank": _rerank(),
        }
        record.update(overrides)
        return record

    def test_valid_announced_next_and_rerank_validates(self):
        record = self._candidate_record()
        result = validate_record(record, prior_records=self.priors)
        self.assertTrue(result.valid, result.reason)

    def test_rerank_passes_key_alias_validates(self):
        rerank = _rerank()
        rerank["passes"] = rerank.pop("candidates")
        record = self._candidate_record(rerank=rerank)
        self.assertTrue(
            validate_record(record, prior_records=self.priors).valid)


class CitationRuleTests(unittest.TestCase):
    """rerank_missing_prior_outcome_citation is the REQUIRED-CITATION rule.

    A mutation-style assertion: these tests FAIL if the rule stops firing, so
    removing the rule breaks the suite.
    """

    def setUp(self):
        self.priors = [_finalized_measured_prior("D-0001")]

    def _record_with_rerank(self, rerank):
        return {
            "id": "D-0002",
            "observed_state": {"note": "observed"},
            "prior_decision_ids_read": ["D-0001"],
            "candidates": [
                {"name": "a", "description": "do A"},
                {"name": "b", "description": "do B"},
                {"name": "c", "description": "do C"},
            ],
            "scoring_criteria": ["impact"],
            "candidate_scores": [{"candidate": "a", "impact": 1}],
            "chosen_action": "generate an import graph dependency analysis",
            "chosen_rationale": "best option",
            "rejected": [{"candidate": "b", "why": "lower"}],
            "prediction": "edges between 180 and 320",
            "status": STATUS_FINALIZED,
            "actual_outcome": {"measured": True},
            "prediction_vs_actual": "within",
            "lesson": "applied prior lesson",
            "lessons_applied": [
                {"decision_id": "D-0001", "lesson": PRIOR_LESSON}],
            "rerank": rerank,
        }

    def test_valid_citation_passes(self):
        record = self._record_with_rerank(_rerank(prior_id="D-0001"))
        self.assertTrue(
            validate_record(record, prior_records=self.priors).valid)

    def test_malformed_citation_rejected(self):
        rerank = _rerank()
        rerank["prior_outcome_decision_id"] = "not-an-id"
        result = validate_record(
            self._record_with_rerank(rerank), prior_records=self.priors)
        self.assertFalse(result.valid)
        # Malformed id fails the SHAPE validator first (distinct reason).
        self.assertEqual(result.reason, "invalid_rerank")

    def test_citation_not_found_among_priors_rejected(self):
        # Well-formed id, but D-0009 is not among the single prior D-0001.
        rerank = _rerank(prior_id="D-0009")
        result = validate_record(
            self._record_with_rerank(rerank), prior_records=self.priors)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "rerank_missing_prior_outcome_citation")

    def test_citation_points_at_non_measured_prior_rejected(self):
        # A finalized prior that carries NO measured outcome must not satisfy
        # the citation rule.
        plain_prior = _finalized_measured_prior("D-0001")
        del plain_prior["numeric_error"]
        del plain_prior["outcome_verdict"]
        result = validate_record(
            self._record_with_rerank(_rerank(prior_id="D-0001")),
            prior_records=[plain_prior])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "rerank_missing_prior_outcome_citation")

    def test_citation_points_at_predicted_prior_rejected(self):
        # A non-FINALIZED (PREDICTED) prior is not a measured record. A PREDICTED
        # prior exposes no lesson, so the record cites none (empty
        # lessons_applied) and the rerank citation rule is reached.
        predicted_prior = _finalized_measured_prior("D-0001")
        predicted_prior["status"] = "PREDICTED"
        record = self._record_with_rerank(_rerank(prior_id="D-0001"))
        record["lessons_applied"] = []
        result = validate_record(record, prior_records=[predicted_prior])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "rerank_missing_prior_outcome_citation")


class AnnouncedNextLessonMismatchTests(unittest.TestCase):
    def setUp(self):
        self.priors = [_finalized_measured_prior("D-0001")]

    def _record(self, announced_next):
        return {
            "id": "D-0002",
            "observed_state": {"note": "observed"},
            "prior_decision_ids_read": ["D-0001"],
            "candidates": [
                {"name": "a", "description": "do A"},
                {"name": "b", "description": "do B"},
                {"name": "c", "description": "do C"},
            ],
            "scoring_criteria": ["impact"],
            "candidate_scores": [{"candidate": "a", "impact": 1}],
            "chosen_action": "generate an import graph dependency analysis",
            "chosen_rationale": "best option",
            "rejected": [{"candidate": "b", "why": "lower"}],
            "prediction": "edges between 180 and 320",
            "status": STATUS_FINALIZED,
            "actual_outcome": {"measured": True},
            "prediction_vs_actual": "within",
            "lesson": "applied prior lesson",
            "lessons_applied": [
                {"decision_id": "D-0001", "lesson": PRIOR_LESSON}],
            "announced_next": announced_next,
        }

    def test_verbatim_lesson_passes(self):
        self.assertTrue(
            validate_record(self._record(_announced_next()),
                            prior_records=self.priors).valid)

    def test_mismatched_lesson_rejected(self):
        bad = _announced_next(lesson="a lesson that does not match verbatim")
        result = validate_record(self._record(bad), prior_records=self.priors)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "announced_next_lesson_mismatch")

    def test_source_not_found_rejected(self):
        bad = _announced_next(source_id="D-0007")
        result = validate_record(self._record(bad), prior_records=self.priors)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "announced_next_lesson_mismatch")


class DivergenceHonestyTests(unittest.TestCase):
    def setUp(self):
        self.priors = [_finalized_measured_prior("D-0001")]

    def _record(self, *, chosen_action, diverged):
        return {
            "id": "D-0002",
            "observed_state": {"note": "observed"},
            "prior_decision_ids_read": ["D-0001"],
            "candidates": [
                {"name": "a", "description": "do A"},
                {"name": "b", "description": "do B"},
                {"name": "c", "description": "do C"},
            ],
            "scoring_criteria": ["impact"],
            "candidate_scores": [{"candidate": "a", "impact": 1}],
            "chosen_action": chosen_action,
            "chosen_rationale": "best option",
            "rejected": [{"candidate": "b", "why": "lower"}],
            "prediction": "a prediction",
            "status": STATUS_FINALIZED,
            "actual_outcome": {"measured": True},
            "prediction_vs_actual": "within",
            "lesson": "applied prior lesson",
            "lessons_applied": [
                {"decision_id": "D-0001", "lesson": PRIOR_LESSON}],
            "announced_next": _announced_next(
                announced_action=ANNOUNCED_IMPORT_GRAPH),
            "rerank": _rerank(diverged=diverged),
        }

    def test_real_divergence_validates(self):
        # announced -> import_graph; chosen -> markers (a DIFFERENT analysis).
        self.assertEqual(
            action_executor.map_action_to_analysis(ANNOUNCED_IMPORT_GRAPH)[0],
            "import_graph")
        self.assertEqual(
            action_executor.map_action_to_analysis(CHOSEN_MARKERS)[0],
            "markers")
        record = self._record(chosen_action=CHOSEN_MARKERS, diverged=True)
        result = validate_record(record, prior_records=self.priors)
        self.assertTrue(result.valid, result.reason)

    def test_claimed_divergence_not_reflected_rejected(self):
        # diverged=True but chosen maps to the SAME analysis as announced.
        record = self._record(
            chosen_action="generate an import graph dependency analysis",
            diverged=True)
        result = validate_record(record, prior_records=self.priors)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "divergence_not_reflected_in_choice")

    def test_no_divergence_claim_same_analysis_ok(self):
        # diverged=False: chosen may map to the SAME analysis as announced.
        record = self._record(
            chosen_action="generate an import graph dependency analysis",
            diverged=False)
        self.assertTrue(
            validate_record(record, prior_records=self.priors).valid)


class BackwardCompatTests(unittest.TestCase):
    """The pre-R3-H4 committed records D-0001..D-0018 (none carry the new
    fields) still validate against their priors; removing the GATE would break
    this. Records from D-0019 onward are the live R3-H4 re-rank cycles and DO
    carry announced_next + rerank, so the "no new fields" check is scoped to the
    pre-R3-H4 ids while the validate-against-priors check covers EVERY record."""

    def test_committed_records_still_validate(self):
        repo_root = Path(__file__).resolve().parent.parent
        decisions = repo_root / "decisions"
        records = load_records(decisions)
        self.assertGreaterEqual(len(records), 18)
        for i, record in enumerate(records):
            # Pre-R3-H4 records must remain additive-field-free, proving the
            # GATE left them exactly as committed. The R3-H4 live cycles
            # (D-0019+) legitimately carry the new fields.
            if record["id"] <= "D-0018":
                self.assertNotIn("announced_next", record, record["id"])
                self.assertNotIn("rerank", record, record["id"])
            result = validate_record(
                record, prior_records=records[:i],
                artifacts_resolution_root=decisions)
            self.assertTrue(result.valid, "%s: %s" % (record["id"], result.reason))


if __name__ == "__main__":
    unittest.main()
