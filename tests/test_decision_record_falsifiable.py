"""Offline tests for the R2-S8 falsifiable-prediction record machinery.

These prove, additively and backward-compatibly (pure stdlib, no network),
that:

  * the create-time quantitative-claim rule rejects a FALSIFIABLE record that
    lacks a well-formed quantitative_claim with the distinct reason
    'prediction_missing_quantitative_claim', while a record WITHOUT the S8
    marker (including the pre-S8 D-0001..D-0012 shape) is unaffected;
  * the shape validators reject malformed quantitative_claim / numeric_error /
    followup_decision / findings with their own distinct reasons;
  * the follow-up-on-finding rule rejects a cycle that does not address a prior
    over-range error / finding ('followup_finding_not_addressed') and ACCEPTS a
    conforming followup_decision citing the prior id + outcome; a prior without
    the S8 fields never triggers it;
  * the real finalized tail decisions/D-0010..D-0012 still validate with the
    decisions dir as the artifact resolution root.

Every decisions dir is an isolated tempfile.TemporaryDirectory so the repo's
real decisions/ dir is NEVER touched (the only exception is the read-only
regression test that loads the committed D-0010..D-0012 records).
"""
import json
import os
import tempfile
import unittest
from pathlib import Path

from spray_schema.decision_record import (
    OPTIONAL_FIELD_NAMES,
    STATUS_FINALIZED,
    STATUS_PREDICTED,
    create_record,
    load_records,
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


def _good_claim(**overrides):
    claim = {
        "metric_key": "total_files",
        "kind": "range",
        "predicted_low": 1,
        "predicted_high": 10,
        "confidence": 0.8,
    }
    claim.update(overrides)
    return claim


class S8FieldsPresenceTests(unittest.TestCase):
    def test_new_fields_in_optional_fields(self):
        for key in ("falsifiable", "quantitative_claim", "numeric_error",
                    "followup_decision", "findings"):
            self.assertIn(key, OPTIONAL_FIELD_NAMES)


class CreateTimeQuantitativeClaimRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = os.path.join(self.temp.name, "decisions")

    def test_falsifiable_without_claim_rejected(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["falsifiable"] = True
        # No quantitative_claim present.
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "prediction_missing_quantitative_claim")

    def test_falsifiable_with_wellformed_claim_validates(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["falsifiable"] = True
        record["quantitative_claim"] = _good_claim()
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_falsifiable_with_single_value_claim_validates(self):
        record = dict(create_record(self.dir + "v", **_base_kwargs()))
        record["falsifiable"] = True
        record["quantitative_claim"] = {
            "metric_key": "edges", "kind": "point",
            "predicted_value": 42, "confidence": 0.5}
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_non_falsifiable_record_unaffected(self):
        # No falsifiable marker -> the create-time rule never fires.
        record = create_record(self.dir, **_base_kwargs())
        self.assertNotIn("falsifiable", record)
        self.assertNotIn("quantitative_claim", record)
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_falsifiable_false_is_not_subject_to_rule(self):
        record = dict(create_record(self.dir + "f", **_base_kwargs()))
        record["falsifiable"] = False
        # Marker present but False -> the rule is NOT enforced.
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_create_record_passes_marker_and_claim(self):
        record = create_record(
            self.dir + "c", **_base_kwargs(),
            falsifiable=True, quantitative_claim=_good_claim())
        self.assertIs(record["falsifiable"], True)
        self.assertEqual(record["quantitative_claim"]["metric_key"],
                         "total_files")
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_create_record_falsifiable_without_claim_refused(self):
        with self.assertRaises(ValueError) as ctx:
            create_record(self.dir + "x", **_base_kwargs(), falsifiable=True)
        self.assertIn("prediction_missing_quantitative_claim",
                      str(ctx.exception))


class ShapeValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = os.path.join(self.temp.name, "decisions")

    def _record(self, **extra):
        record = dict(create_record(self.dir + str(len(extra)) + repr(extra),
                                    **_base_kwargs()))
        record.update(extra)
        return record

    def test_bad_quantitative_claim_metric_key(self):
        record = self._record(falsifiable=True,
                               quantitative_claim=_good_claim(metric_key="bogus"))
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_quantitative_claim")

    def test_bad_quantitative_claim_confidence_out_of_range(self):
        record = self._record(falsifiable=True,
                               quantitative_claim=_good_claim(confidence=1.5))
        self.assertEqual(
            validate_record(record, prior_records=[]).reason,
            "invalid_quantitative_claim")

    def test_bad_quantitative_claim_confidence_zero(self):
        record = self._record(falsifiable=True,
                               quantitative_claim=_good_claim(confidence=0.0))
        self.assertEqual(
            validate_record(record, prior_records=[]).reason,
            "invalid_quantitative_claim")

    def test_bad_quantitative_claim_both_value_and_range(self):
        claim = _good_claim()
        claim["predicted_value"] = 3  # now carries both -> invalid
        record = self._record(falsifiable=True, quantitative_claim=claim)
        self.assertEqual(
            validate_record(record, prior_records=[]).reason,
            "invalid_quantitative_claim")

    def test_bad_quantitative_claim_low_gt_high(self):
        record = self._record(
            falsifiable=True,
            quantitative_claim=_good_claim(predicted_low=10, predicted_high=1))
        self.assertEqual(
            validate_record(record, prior_records=[]).reason,
            "invalid_quantitative_claim")

    def test_bad_quantitative_claim_bool_value_rejected(self):
        # bool subclasses int; a bool predicted_value must be rejected.
        record = self._record(
            falsifiable=True,
            quantitative_claim={"metric_key": "edges", "kind": "point",
                                "predicted_value": True, "confidence": 0.5})
        self.assertEqual(
            validate_record(record, prior_records=[]).reason,
            "invalid_quantitative_claim")

    def _finalized_with(self, **extra):
        record = dict(create_record(self.dir + "fin" + repr(extra),
                                    **_base_kwargs()))
        record["status"] = STATUS_FINALIZED
        record["actual_outcome"] = {"note": "done"}
        record["prediction_vs_actual"] = "graded"
        record["lesson"] = "a lesson"
        record.update(extra)
        return record

    def test_bad_numeric_error_shape(self):
        record = self._finalized_with(numeric_error={"metric_key": "total_files"})
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_numeric_error")

    def test_wellformed_numeric_error_validates(self):
        record = self._finalized_with(numeric_error={
            "metric_key": "total_files", "predicted": [1, 10], "measured": 5,
            "absolute_error": 0, "relative_error": 0.0,
            "within_range": True, "confidence": 0.8})
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_numeric_error_relative_null_when_basis_zero(self):
        record = self._finalized_with(numeric_error={
            "metric_key": "total_markers", "predicted": 0, "measured": 0,
            "absolute_error": 0, "relative_error": None,
            "within_range": True, "confidence": 0.5})
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_bad_followup_decision_shape(self):
        record = self._finalized_with(
            followup_decision={"prior_decision_id": "not-an-id",
                               "prior_outcome": "x", "chose": True, "why": "y"})
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_followup_decision")

    def test_bad_followup_decision_missing_why(self):
        record = self._finalized_with(
            followup_decision={"prior_decision_id": "D-0001",
                               "prior_outcome": "x", "chose": True, "why": ""})
        self.assertEqual(
            validate_record(record, prior_records=[]).reason,
            "invalid_followup_decision")

    def test_bad_findings_shape(self):
        record = self._finalized_with(findings=["ok", 7])
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_findings")

    def test_wellformed_findings_validate(self):
        record = self._finalized_with(findings=["undocumented=3"])
        self.assertTrue(validate_record(record, prior_records=[]).valid)


class FollowupFindingRuleTests(unittest.TestCase):
    """The additive follow-up-on-finding rule, gated on S8 prior fields."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = os.path.join(self.temp.name, "decisions")

    LESSON = "the prior cycle taught a measured lesson"

    def _finalized_prior(self, *, numeric_error=None, findings=None):
        create_record(self.dir, **_base_kwargs())
        from spray_schema.decision_record import finalize_record
        finalize_record(
            self.dir, "D-0001",
            actual_outcome={"note": "done"},
            prediction_vs_actual="graded",
            lesson=self.LESSON,
            outcome_verdict="HIT",
            numeric_error=numeric_error,
            findings=findings)
        return load_records(self.dir)

    def _second(self, prior, *, followup_decision=None):
        record = dict(prior[0])
        record["id"] = "D-0002"
        record["prior_decision_ids_read"] = ["D-0001"]
        record["lessons_applied"] = [
            {"decision_id": "D-0001", "lesson": self.LESSON}]
        record["status"] = STATUS_PREDICTED
        record["actual_outcome"] = None
        record["prediction_vs_actual"] = None
        record["lesson"] = None
        for k in ("outcome_verdict", "outcome_at", "numeric_error",
                  "findings", "followup_decision", "artifact_path",
                  "artifact_sha256"):
            record.pop(k, None)
        if followup_decision is not None:
            record["followup_decision"] = followup_decision
        return record

    def test_over_range_prior_not_addressed_rejected(self):
        prior = self._finalized_prior(numeric_error={
            "metric_key": "total_files", "predicted": [1, 2], "measured": 99,
            "absolute_error": 97, "relative_error": 0.98,
            "within_range": False, "confidence": 0.9})
        bad = self._second(prior)  # no followup_decision
        result = validate_record(bad, prior_records=prior)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "followup_finding_not_addressed")

    def test_over_range_prior_addressed_accepted(self):
        prior = self._finalized_prior(numeric_error={
            "metric_key": "total_files", "predicted": [1, 2], "measured": 99,
            "absolute_error": 97, "relative_error": 0.98,
            "within_range": False, "confidence": 0.9})
        good = self._second(prior, followup_decision={
            "prior_decision_id": "D-0001",
            "prior_outcome": "prior numeric error on total_files exceeded range",
            "chose": True,
            "why": "re-run with a corrected range on the same metric"})
        self.assertTrue(validate_record(good, prior_records=prior).valid)

    def test_finding_prior_not_addressed_rejected(self):
        prior = self._finalized_prior(findings=["undocumented=12"])
        bad = self._second(prior)
        result = validate_record(bad, prior_records=prior)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "followup_finding_not_addressed")

    def test_finding_prior_addressed_accepted(self):
        prior = self._finalized_prior(findings=["undocumented=12"])
        good = self._second(prior, followup_decision={
            "prior_decision_id": "D-0001",
            "prior_outcome": "prior artifact surfaced undocumented=12",
            "chose": False,
            "why": "deferring docs backfill; chose a cheaper structural scan"})
        self.assertTrue(validate_record(good, prior_records=prior).valid)

    def test_followup_citing_wrong_prior_rejected(self):
        prior = self._finalized_prior(findings=["undocumented=12"])
        bad = self._second(prior, followup_decision={
            "prior_decision_id": "D-0099",  # does not cite the trigger prior
            "prior_outcome": "x", "chose": True, "why": "mismatched"})
        # prior_decision_id is a valid 'D-NNNN' shape but not the latest prior.
        result = validate_record(bad, prior_records=prior)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "followup_finding_not_addressed")

    def test_within_range_prior_does_not_trigger(self):
        prior = self._finalized_prior(numeric_error={
            "metric_key": "total_files", "predicted": [1, 100], "measured": 5,
            "absolute_error": 0, "relative_error": 0.0,
            "within_range": True, "confidence": 0.9})
        rec = self._second(prior)  # no followup needed
        self.assertTrue(validate_record(rec, prior_records=prior).valid)

    def test_prior_without_s8_fields_never_triggers(self):
        prior = self._finalized_prior()  # no numeric_error, no findings
        self.assertNotIn("numeric_error", prior[0])
        self.assertNotIn("findings", prior[0])
        rec = self._second(prior)
        self.assertTrue(validate_record(rec, prior_records=prior).valid)


class FollowupDecisionImmutableAtFinalizeTests(unittest.TestCase):
    """``followup_decision`` is committed at CREATE time and is immutable at
    finalize: it is deliberately NOT in _FINALIZE_FIELDS, finalize_record has
    no parameter for it, and a record that carries it from create survives
    finalize byte-for-byte unchanged."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = os.path.join(self.temp.name, "decisions")

    def test_followup_decision_not_in_finalize_fields(self):
        from spray_schema.decision_record import _FINALIZE_FIELDS
        self.assertNotIn("followup_decision", _FINALIZE_FIELDS)
        # numeric_error and findings ARE finalize-written and remain in the set.
        self.assertIn("numeric_error", _FINALIZE_FIELDS)
        self.assertIn("findings", _FINALIZE_FIELDS)

    def test_finalize_record_has_no_followup_decision_param(self):
        import inspect
        from spray_schema.decision_record import finalize_record
        params = inspect.signature(finalize_record).parameters
        self.assertNotIn("followup_decision", params)

    def test_create_time_followup_decision_survives_finalize_unchanged(self):
        from spray_schema.decision_record import finalize_record
        followup = {
            "prior_decision_id": "D-0001",
            "prior_outcome": "prior numeric error on total_files exceeded range",
            "chose": True,
            "why": "re-run with a corrected range on the same metric",
        }
        create_record(
            self.dir, **_base_kwargs(),
            falsifiable=True, quantitative_claim=_good_claim(),
            followup_decision=followup)
        finalized = finalize_record(
            self.dir, "D-0001",
            actual_outcome={"note": "done"},
            prediction_vs_actual="graded",
            lesson="a measured lesson",
            outcome_verdict="HIT",
            numeric_error={
                "metric_key": "total_files", "predicted": [1, 10],
                "measured": 5, "absolute_error": 0, "relative_error": 0.0,
                "within_range": True, "confidence": 0.8},
            findings=["undocumented=3"])
        # The create-time followup_decision is unchanged by finalize.
        self.assertEqual(finalized["followup_decision"], followup)
        self.assertEqual(finalized["status"], STATUS_FINALIZED)
        # And the finalized record validates.
        self.assertTrue(validate_record(finalized, prior_records=[]).valid)


class PreS8RegressionTests(unittest.TestCase):
    """The committed finalized tail D-0010..D-0012 still validates unchanged."""

    def test_d0010_to_d0012_validate_with_resolution_root(self):
        repo_root = Path(__file__).resolve().parent.parent
        decisions = repo_root / "decisions"
        records = load_records(decisions)
        by_id = {r["id"]: r for r in records}
        for rid in ("D-0010", "D-0011", "D-0012"):
            self.assertIn(rid, by_id, rid)
            record = by_id[rid]
            # None of the pre-S8 records carry the S8 marker.
            self.assertNotIn("falsifiable", record)
            prior = [r for r in records if r["id"] < rid]
            result = validate_record(
                record, prior_records=prior,
                artifacts_resolution_root=decisions)
            self.assertTrue(result.valid, "%s: %s" % (rid, result.reason))


if __name__ == "__main__":
    unittest.main()
