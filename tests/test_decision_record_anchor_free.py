"""Offline tests for the R3-H5 additive ``anchor_free`` record field (FEAT-001).

These prove, additively and backward-compatibly (pure stdlib, no network), that:

  * ``anchor_free`` is in OPTIONAL_FIELD_NAMES and NOT in _FINALIZE_FIELDS
    (create-time + immutable at finalize);
  * a record carrying ``anchor_free: true`` (or false) validates;
  * a non-bool ``anchor_free`` is rejected with the DISTINCT reason
    'invalid_anchor_free';
  * a record WITHOUT ``anchor_free`` validates EXACTLY as before;
  * create_record accepts + persists an optional ``anchor_free`` kwarg, and
    finalize_record never mutates it (it stays byte-for-byte create-time);
  * BACKWARD COMPAT: every committed record D-0001..D-0022 (none carry
    anchor_free) still validates against its priors with the decisions dir as
    the artifact resolution root.

Every decisions dir is an isolated tempfile.TemporaryDirectory so the repo's
real decisions/ dir is NEVER touched (the only exception is the read-only
backward-compat test that loads the committed records).
"""
import glob
import json
import os
import tempfile
import unittest
from pathlib import Path

from spray_schema.decision_record import (
    OPTIONAL_FIELD_NAMES,
    STATUS_FINALIZED,
    STATUS_PREDICTED,
    _FINALIZE_FIELDS,
    _validate_anchor_free_shape,
    create_record,
    finalize_record,
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
        chosen_action="produce a repo-local offline markers scan",
        chosen_rationale="highest impact at zero cash cost",
        rejected=[{"candidate": "b", "why": "lower value"}],
        prediction="the offline analysis will complete",
        lessons_applied=[],
    )
    kwargs.update(overrides)
    return kwargs


def _minimal_record(**overrides):
    """A minimal valid PREDICTED record dict for direct validate_record calls."""
    record = {
        "id": "D-0001",
        "observed_state": {"note": "observed"},
        "prior_decision_ids_read": [],
        "candidates": [
            {"name": "a", "description": "do A"},
            {"name": "b", "description": "do B"},
            {"name": "c", "description": "do C"},
        ],
        "scoring_criteria": ["impact"],
        "candidate_scores": [{"candidate": "a", "impact": 1}],
        "chosen_action": "produce a repo-local offline markers scan",
        "chosen_rationale": "best",
        "rejected": [{"candidate": "b", "why": "lower"}],
        "prediction": "will complete",
        "status": STATUS_PREDICTED,
        "actual_outcome": None,
        "prediction_vs_actual": None,
        "lesson": None,
        "lessons_applied": [],
    }
    record.update(overrides)
    return record


class AnchorFreeSchemaTests(unittest.TestCase):
    def test_in_optional_and_not_in_finalize_fields(self):
        self.assertIn("anchor_free", OPTIONAL_FIELD_NAMES)
        self.assertNotIn("anchor_free", _FINALIZE_FIELDS)

    def test_shape_validator_rejects_non_bool(self):
        self.assertIsNone(_validate_anchor_free_shape(True))
        self.assertIsNone(_validate_anchor_free_shape(False))
        for bad in (1, 0, "true", None, [], {}):
            self.assertEqual(_validate_anchor_free_shape(bad),
                             "invalid_anchor_free")

    def test_record_with_anchor_free_true_validates(self):
        rec = _minimal_record(anchor_free=True)
        self.assertTrue(validate_record(rec, prior_records=[]).valid)

    def test_record_with_anchor_free_false_validates(self):
        rec = _minimal_record(anchor_free=False)
        self.assertTrue(validate_record(rec, prior_records=[]).valid)

    def test_record_without_anchor_free_validates(self):
        rec = _minimal_record()
        self.assertNotIn("anchor_free", rec)
        self.assertTrue(validate_record(rec, prior_records=[]).valid)

    def test_non_bool_anchor_free_rejected_with_distinct_reason(self):
        rec = _minimal_record(anchor_free="yes")
        result = validate_record(rec, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_anchor_free")


class AnchorFreeCreateFinalizeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.decisions = os.path.join(self.tmp.name, "decisions")

    def test_create_persists_anchor_free_and_finalize_preserves_it(self):
        record = create_record(
            self.decisions, anchor_free=True, **_base_kwargs())
        self.assertIs(record["anchor_free"], True)

        # Reload from disk to confirm it was persisted.
        on_disk = json.loads(
            (Path(self.decisions) / (record["id"] + ".json")).read_text(
                encoding="utf-8"))
        self.assertIs(on_disk["anchor_free"], True)

        # Finalize must NOT mutate anchor_free (it is create-time / immutable).
        finalized = finalize_record(
            self.decisions, record["id"],
            actual_outcome={"measured": True},
            prediction_vs_actual="artifact produced as predicted",
            lesson="a non-empty lesson")
        self.assertIs(finalized["anchor_free"], True)
        self.assertEqual(finalized["status"], STATUS_FINALIZED)

    def test_create_without_anchor_free_omits_key(self):
        record = create_record(self.decisions, **_base_kwargs())
        self.assertNotIn("anchor_free", record)


class BackwardCompatTests(unittest.TestCase):
    def test_committed_records_still_validate(self):
        repo_decisions = Path(__file__).resolve().parent.parent / "decisions"
        paths = sorted(glob.glob(str(repo_decisions / "D-00*.json")))
        self.assertGreaterEqual(len(paths), 22)
        loaded = []
        saw_pre_h5 = 0
        for path in paths:
            with open(path, encoding="utf-8") as handle:
                record = json.load(handle)
            # Pre-R3-H5 records D-0001..D-0022 must NOT carry the additive
            # anchor_free field and must still validate; the R3-H5 anchor-free
            # cycles (D-0023+) legitimately carry anchor_free=True and are only
            # checked here for continued chain validity.
            if record.get("id", "") <= "D-0022":
                self.assertNotIn("anchor_free", record)
                saw_pre_h5 += 1
            result = validate_record(
                record, prior_records=list(loaded),
                artifacts_resolution_root=repo_decisions)
            self.assertTrue(
                result.valid,
                "%s failed validation: %s" % (record.get("id"), result.reason))
            loaded.append(record)
        self.assertGreaterEqual(saw_pre_h5, 22)


if __name__ == "__main__":
    unittest.main()
