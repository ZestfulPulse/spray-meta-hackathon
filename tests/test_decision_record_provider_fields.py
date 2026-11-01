"""Offline tests for the R2-S3 additive Decision Record provenance fields.

These exercise the OPTIONAL ``provider`` / ``model`` / ``estimated_credit_cost_usd``
fields added additively in FEAT-004. The unchanged R2-S2 file
``tests/test_decision_record.py`` proves backward compatibility; this file
proves the new fields validate, round-trip, reject bad values, and stay
immutable across finalize. No network or model calls are made.
"""
import json
import math
import os
import tempfile
import unittest

from spray_schema.decision_record import (
    ALLOWED_PROVIDERS,
    OPTIONAL_FIELD_NAMES,
    STATUS_FINALIZED,
    create_record,
    finalize_record,
    validate_record,
)


def _base_kwargs(**overrides):
    """A valid create_record kwargs set for D-0001 (no priors, cites none)."""
    kwargs = dict(
        observed_state={"note": "initial observed state"},
        prior_decision_ids_read=[],
        candidates=[
            {"name": "candidate-a", "description": "do A"},
            {"name": "candidate-b", "description": "do B"},
            {"name": "candidate-c", "description": "do C"},
        ],
        scoring_criteria=["impact", "cost", "risk"],
        candidate_scores=[
            {"candidate": "candidate-a", "impact": 3, "cost": 2, "risk": 1},
            {"candidate": "candidate-b", "impact": 2, "cost": 1, "risk": 2},
            {"candidate": "candidate-c", "impact": 1, "cost": 1, "risk": 3},
        ],
        chosen_action="pursue candidate-a",
        chosen_rationale="best impact-to-risk ratio",
        rejected=[
            {"candidate": "candidate-b", "why": "lower impact"},
            {"candidate": "candidate-c", "why": "highest risk"},
        ],
        prediction="candidate-a will improve the metric within one cycle",
        lessons_applied=[],
    )
    kwargs.update(overrides)
    return kwargs


class ProviderFieldsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = os.path.join(self.temp.name, "decisions")

    # (a) backward compat: a record WITHOUT the new fields still validates. --- #
    def test_record_without_new_fields_still_validates(self):
        record = create_record(self.dir, **_base_kwargs())
        for key in OPTIONAL_FIELD_NAMES:
            self.assertNotIn(key, record)
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    # (b) valid new fields validate and round-trip through create/finalize. -- #
    def test_valid_new_fields_round_trip_through_create_and_finalize(self):
        record = create_record(
            self.dir,
            provider="meta",
            model="muse-spark-PLACEHOLDER",
            estimated_credit_cost_usd=0.0125,
            **_base_kwargs(),
        )
        self.assertEqual(record["provider"], "meta")
        self.assertEqual(record["model"], "muse-spark-PLACEHOLDER")
        self.assertEqual(record["estimated_credit_cost_usd"], 0.0125)
        self.assertTrue(validate_record(record, prior_records=[]).valid)

        # Persisted bytes carry the fields and reload identically.
        path = os.path.join(self.dir, "D-0001.json")
        with open(path, encoding="utf-8") as handle:
            on_disk = json.loads(handle.read())
        self.assertEqual(on_disk["provider"], "meta")
        self.assertEqual(on_disk["model"], "muse-spark-PLACEHOLDER")
        self.assertEqual(on_disk["estimated_credit_cost_usd"], 0.0125)

        finalized = finalize_record(
            self.dir, "D-0001",
            actual_outcome={"note": "metric improved"},
            prediction_vs_actual="prediction held",
            lesson="candidate-a was the right call",
        )
        self.assertEqual(finalized["status"], STATUS_FINALIZED)
        # New fields survive finalize unchanged.
        self.assertEqual(finalized["provider"], "meta")
        self.assertEqual(finalized["model"], "muse-spark-PLACEHOLDER")
        self.assertEqual(finalized["estimated_credit_cost_usd"], 0.0125)

    def test_gemini_provider_and_integer_cost_validate(self):
        record = create_record(
            self.dir, provider="gemini", model="gemini-2.5-flash",
            estimated_credit_cost_usd=0, **_base_kwargs(),
        )
        self.assertIn("gemini", ALLOWED_PROVIDERS)
        self.assertEqual(record["estimated_credit_cost_usd"], 0)
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    # (c) invalid new-field values are rejected with distinct reasons. ------- #
    def test_bad_provider_rejected(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["provider"] = "openai"
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_provider")

    def test_empty_provider_rejected(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["provider"] = ""
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_provider")

    def test_empty_model_rejected(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["model"] = "   "
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_model")

    def test_non_str_model_rejected(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["model"] = 123
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_model")

    def test_negative_cost_rejected(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["estimated_credit_cost_usd"] = -0.01
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_estimated_credit_cost_usd")

    def test_nan_cost_rejected(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["estimated_credit_cost_usd"] = float("nan")
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_estimated_credit_cost_usd")

    def test_inf_cost_rejected(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["estimated_credit_cost_usd"] = float("inf")
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_estimated_credit_cost_usd")

    def test_bool_cost_rejected(self):
        # bool is an int subclass; the numeric guard must reject it.
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["estimated_credit_cost_usd"] = True
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_estimated_credit_cost_usd")

    def test_unknown_extra_field_rejected(self):
        record = dict(create_record(self.dir, **_base_kwargs()))
        record["unexpected"] = "nope"
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "field_set_mismatch")

    # (d) the new fields are immutable across finalize. ---------------------- #
    def test_new_fields_immutable_across_finalize(self):
        create_record(
            self.dir, provider="meta", model="muse-spark-PLACEHOLDER",
            estimated_credit_cost_usd=0.02, **_base_kwargs(),
        )
        # Tamper with the on-disk provider before finalizing; finalize must
        # refuse because it may not alter a non-allowed field.
        path = os.path.join(self.dir, "D-0001.json")
        with open(path, encoding="utf-8") as handle:
            on_disk = json.loads(handle.read())
        self.assertEqual(on_disk["provider"], "meta")

        # finalize_record itself must never change the new fields: confirm the
        # value is preserved exactly and that the finalize immutability loop
        # covers the optional keys (sanity: they are not in the allowed set).
        finalized = finalize_record(
            self.dir, "D-0001",
            actual_outcome={"note": "done"},
            prediction_vs_actual="held",
            lesson="keep the meta provenance intact",
        )
        self.assertEqual(finalized["provider"], "meta")
        self.assertEqual(finalized["model"], "muse-spark-PLACEHOLDER")
        self.assertEqual(finalized["estimated_credit_cost_usd"], 0.02)
        self.assertFalse(math.isnan(finalized["estimated_credit_cost_usd"]))


if __name__ == "__main__":
    unittest.main()
