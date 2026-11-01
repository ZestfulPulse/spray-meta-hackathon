"""Offline tests for the R2-S7 additive outcome_verdict field and the new
'miss_not_cited' validator rule (pure stdlib, no network).

These prove, additively and backward-compatibly, that:

  * ``outcome_verdict`` validates to exactly 'HIT'/'MISS'; any other value is
    rejected with the distinct reason 'invalid_outcome_verdict';
  * a record WITHOUT ``outcome_verdict`` still validates (backward compat);
  * the 'miss_not_cited' rule rejects a cycle that follows a prior MISS without
    citing that missed record's lesson, and ACCEPTS one that cites the missed
    record's verbatim lesson;
  * priors with no MISS verdict leave later records entirely unaffected.

The unchanged R2-S2/R2-S3 files prove the rest of the record contract stays
green; this file only exercises the new behavior.
"""
import os
import tempfile
import unittest

from spray_schema.decision_record import (
    OPTIONAL_FIELD_NAMES,
    STATUS_FINALIZED,
    create_record,
    finalize_record,
    validate_record,
)


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
        chosen_action="pursue a",
        chosen_rationale="best option",
        rejected=[{"candidate": "b", "why": "lower"},
                  {"candidate": "c", "why": "risk"}],
        prediction="will produce an artifact",
        lessons_applied=[],
    )
    kwargs.update(overrides)
    return kwargs


class OutcomeVerdictShapeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = os.path.join(self.temp.name, "decisions")

    def test_outcome_verdict_in_optional_fields(self):
        self.assertIn("outcome_verdict", OPTIONAL_FIELD_NAMES)

    def test_hit_and_miss_validate(self):
        for verdict in ("HIT", "MISS"):
            record = dict(create_record(self.dir + verdict, **_base_kwargs()))
            record["status"] = STATUS_FINALIZED
            record["actual_outcome"] = {"note": "done"}
            record["prediction_vs_actual"] = "graded"
            record["lesson"] = "a lesson"
            record["outcome_verdict"] = verdict
            self.assertTrue(
                validate_record(record, prior_records=[]).valid, verdict)

    def test_bad_verdict_rejected_with_distinct_reason(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["status"] = STATUS_FINALIZED
        record["actual_outcome"] = {"note": "done"}
        record["prediction_vs_actual"] = "graded"
        record["lesson"] = "a lesson"
        record["outcome_verdict"] = "MAYBE"
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_outcome_verdict")

    def test_record_without_verdict_still_validates(self):
        record = create_record(self.dir, **_base_kwargs())
        self.assertNotIn("outcome_verdict", record)
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_finalize_threads_outcome_verdict(self):
        create_record(self.dir, **_base_kwargs())
        finalized = finalize_record(
            self.dir, "D-0001",
            actual_outcome={"note": "done"},
            prediction_vs_actual="MISS: executor fell back to inventory",
            lesson="repair the executor next",
            outcome_verdict="MISS")
        self.assertEqual(finalized["outcome_verdict"], "MISS")
        self.assertTrue(validate_record(finalized, prior_records=[]).valid)

    def test_finalize_without_verdict_adds_no_key(self):
        create_record(self.dir, **_base_kwargs())
        finalized = finalize_record(
            self.dir, "D-0001",
            actual_outcome={"note": "done"},
            prediction_vs_actual="graded",
            lesson="a lesson")
        self.assertNotIn("outcome_verdict", finalized)


class MissNotCitedRuleTests(unittest.TestCase):
    """The new additive rule: a cycle following a prior MISS must cite it."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = os.path.join(self.temp.name, "decisions")

    MISS_LESSON = "executor fell back; next choose an executor-supported action"
    HIT_LESSON = "the chosen analysis ran faithfully; proceed"

    def _finalized_prior(self, *, verdict, lesson):
        create_record(self.dir, **_base_kwargs())
        finalize_record(
            self.dir, "D-0001",
            actual_outcome={"note": "done"},
            prediction_vs_actual="graded",
            lesson=lesson,
            outcome_verdict=verdict)
        from spray_schema.decision_record import load_records
        return load_records(self.dir)

    def _second_record(self, prior, lessons_applied):
        record = dict(prior[0])
        record["id"] = "D-0002"
        record["prior_decision_ids_read"] = ["D-0001"]
        record["lessons_applied"] = lessons_applied
        # The second record is a fresh PREDICTED; strip finalize-only fields.
        record["status"] = "PREDICTED"
        record["actual_outcome"] = None
        record["prediction_vs_actual"] = None
        record["lesson"] = None
        record.pop("outcome_verdict", None)
        record.pop("outcome_at", None)
        return record

    def test_miss_without_citation_rejected(self):
        prior = self._finalized_prior(verdict="MISS", lesson=self.MISS_LESSON)
        # Cite nothing at all -> caught first by available_prior_lessons_not_cited.
        bad = self._second_record(prior, [])
        result = validate_record(bad, prior_records=prior)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "available_prior_lessons_not_cited")

    def test_miss_citing_a_different_record_rejected_with_miss_not_cited(self):
        # Build a chain where D-0001 is a HIT and D-0002 is a MISS; D-0003 then
        # cites D-0001 (not the most-recent MISS D-0002) -> miss_not_cited.
        create_record(self.dir, **_base_kwargs())
        finalize_record(self.dir, "D-0001",
                        actual_outcome={"note": "ok"},
                        prediction_vs_actual="graded",
                        lesson=self.HIT_LESSON, outcome_verdict="HIT")
        create_record(
            self.dir,
            **_base_kwargs(
                id="D-0002",
                prior_decision_ids_read=["D-0001"],
                lessons_applied=[{"decision_id": "D-0001",
                                  "lesson": self.HIT_LESSON}]))
        finalize_record(self.dir, "D-0002",
                        actual_outcome={"note": "fell back"},
                        prediction_vs_actual="MISS: executor fell back",
                        lesson=self.MISS_LESSON, outcome_verdict="MISS")
        from spray_schema.decision_record import load_records
        prior = load_records(self.dir)
        # D-0003 cites the OLD HIT lesson (D-0001), ignoring the latest MISS.
        third = dict(prior[0])
        third["id"] = "D-0003"
        third["prior_decision_ids_read"] = ["D-0001", "D-0002"]
        third["lessons_applied"] = [{"decision_id": "D-0001",
                                     "lesson": self.HIT_LESSON}]
        third["status"] = "PREDICTED"
        third["actual_outcome"] = None
        third["prediction_vs_actual"] = None
        third["lesson"] = None
        third.pop("outcome_verdict", None)
        third.pop("outcome_at", None)
        result = validate_record(third, prior_records=prior)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "miss_not_cited")

    def test_miss_citing_the_missed_lesson_verbatim_is_accepted(self):
        prior = self._finalized_prior(verdict="MISS", lesson=self.MISS_LESSON)
        good = self._second_record(
            prior,
            [{"decision_id": "D-0001", "lesson": self.MISS_LESSON}])
        result = validate_record(good, prior_records=prior)
        self.assertTrue(result.valid, result.reason)

    def test_prior_hit_does_not_trigger_miss_rule(self):
        prior = self._finalized_prior(verdict="HIT", lesson=self.HIT_LESSON)
        # A later record still must cite the available lesson (existing rule),
        # but the miss_not_cited rule itself does not fire for a HIT prior.
        rec = self._second_record(
            prior, [{"decision_id": "D-0001", "lesson": self.HIT_LESSON}])
        self.assertTrue(validate_record(rec, prior_records=prior).valid)

    def test_prior_without_verdict_unaffected(self):
        # A prior finalized WITHOUT outcome_verdict (the pre-R2-S7 shape) never
        # triggers miss_not_cited.
        create_record(self.dir, **_base_kwargs())
        finalize_record(self.dir, "D-0001",
                        actual_outcome={"note": "done"},
                        prediction_vs_actual="graded",
                        lesson=self.HIT_LESSON)
        from spray_schema.decision_record import load_records
        prior = load_records(self.dir)
        self.assertNotIn("outcome_verdict", prior[0])
        rec = self._second_record(
            prior, [{"decision_id": "D-0001", "lesson": self.HIT_LESSON}])
        self.assertTrue(validate_record(rec, prior_records=prior).valid)


if __name__ == "__main__":
    unittest.main()
