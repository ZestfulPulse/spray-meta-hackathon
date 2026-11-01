"""Offline tests for the R2-S9 executed-matches-chosen record machinery.

These prove, additively and backward-compatibly (pure stdlib, no network),
that the new create-time rule guarantees a chosen action that WOULD fall back to
the executor's inventory default is refused for a record that opts in via the
explicit marker, while every record WITHOUT the marker (including the committed
fallback=true records D-0013/D-0015) validates exactly as before:

  * a record marked ``executed_matches_chosen: true`` whose ``chosen_action``
    maps to the inventory fallback is rejected with the DISTINCT reason
    'chosen_action_would_fall_back';
  * a record marked true whose ``chosen_action`` maps to a SUPPORTED analysis
    (markers, docs_coverage, ...) validates;
  * a record WITHOUT the marker whose ``chosen_action`` maps to the fallback
    STILL validates (backward compatible);
  * a malformed (non-bool) marker is rejected with 'invalid_executed_matches_chosen';
  * ``executed_matches_chosen`` is in OPTIONAL_FIELD_NAMES and NOT in
    _FINALIZE_FIELDS;
  * the committed fallback=true records D-0013/D-0015 still validate against
    their priors with the decisions dir as the artifact resolution root.

Every decisions dir is an isolated tempfile.TemporaryDirectory so the repo's
real decisions/ dir is NEVER touched (the only exception is the read-only
regression test that loads the committed records).
"""
import os
import tempfile
import unittest
from pathlib import Path

from spray_schema import action_executor
from spray_schema.decision_record import (
    OPTIONAL_FIELD_NAMES,
    _FINALIZE_FIELDS,
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
        chosen_action="run a file inventory of the repo tree",
        chosen_rationale="best option",
        rejected=[{"candidate": "b", "why": "lower"},
                  {"candidate": "c", "why": "risk"}],
        prediction="will produce an artifact",
        lessons_applied=[],
    )
    kwargs.update(overrides)
    return kwargs


class ExecutedMatchFieldPresenceTests(unittest.TestCase):
    def test_marker_in_optional_fields(self):
        self.assertIn("executed_matches_chosen", OPTIONAL_FIELD_NAMES)

    def test_marker_not_in_finalize_fields(self):
        # The marker is create-time and immutable at finalize.
        self.assertNotIn("executed_matches_chosen", _FINALIZE_FIELDS)


class CreateTimeFallbackRejectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = os.path.join(self.temp.name, "decisions")

    def test_marked_true_fallback_chosen_rejected(self):
        # 'dead-export audit' maps to no supported analysis -> fallback True.
        self.assertEqual(
            action_executor.map_action_to_analysis("dead-export audit"),
            (action_executor.DEFAULT_ANALYSIS, True))
        record = dict(create_record(
            self.dir, **_base_kwargs(chosen_action="dead-export audit")))
        record["executed_matches_chosen"] = True
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "chosen_action_would_fall_back")

    def test_marked_true_supported_markers_validates(self):
        record = dict(create_record(
            self.dir + "m",
            **_base_kwargs(chosen_action="produce a TODO/FIXME marker scan")))
        record["executed_matches_chosen"] = True
        # Sanity: the action maps to a real analysis (no fallback).
        self.assertEqual(
            action_executor.map_action_to_analysis(record["chosen_action"]),
            ("markers", False))
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_marked_true_supported_docs_coverage_validates(self):
        record = dict(create_record(
            self.dir + "d",
            **_base_kwargs(chosen_action="run a docstring / docs coverage audit")))
        record["executed_matches_chosen"] = True
        self.assertEqual(
            action_executor.map_action_to_analysis(record["chosen_action"])[0],
            "docs_coverage")
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_unmarked_fallback_choice_still_validates(self):
        # Backward compat: NO marker -> the rule never fires even for a choice
        # that would fall back.
        record = create_record(
            self.dir + "u", **_base_kwargs(chosen_action="dead-export audit"))
        self.assertNotIn("executed_matches_chosen", record)
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_marker_false_is_not_subject_to_rule(self):
        record = dict(create_record(
            self.dir + "f", **_base_kwargs(chosen_action="dead-export audit")))
        record["executed_matches_chosen"] = False
        # Marker present but False -> the rule is NOT enforced.
        self.assertTrue(validate_record(record, prior_records=[]).valid)

    def test_invalid_marker_shape_rejected(self):
        record = dict(create_record(self.dir + "s", **_base_kwargs()))
        record["executed_matches_chosen"] = "yes"  # not a bool
        result = validate_record(record, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_executed_matches_chosen")

    def test_create_record_refuses_fallback_choice_when_marked(self):
        with self.assertRaises(ValueError) as ctx:
            create_record(
                self.dir + "x",
                **_base_kwargs(chosen_action="dead-export audit"),
                executed_matches_chosen=True)
        self.assertIn("chosen_action_would_fall_back", str(ctx.exception))

    def test_create_record_passes_marker_for_supported_choice(self):
        record = create_record(
            self.dir + "c",
            **_base_kwargs(chosen_action="produce a TODO/FIXME marker scan"),
            executed_matches_chosen=True)
        self.assertIs(record["executed_matches_chosen"], True)
        self.assertTrue(validate_record(record, prior_records=[]).valid)


class CommittedFallbackRecordsRegressionTests(unittest.TestCase):
    """The committed fallback=true records D-0013/D-0015 carry NO S9 marker and
    MUST still validate against their priors (no finalized record edited)."""

    def test_d0013_d0015_still_validate(self):
        repo_root = Path(__file__).resolve().parent.parent
        decisions = repo_root / "decisions"
        records = load_records(decisions)
        by_id = {r["id"]: r for r in records}
        for rid in ("D-0013", "D-0015"):
            self.assertIn(rid, by_id, rid)
            record = by_id[rid]
            # Neither committed record carries the S9 marker.
            self.assertNotIn("executed_matches_chosen", record)
            prior = [r for r in records if r["id"] < rid]
            result = validate_record(
                record, prior_records=prior,
                artifacts_resolution_root=decisions)
            self.assertTrue(result.valid, "%s: %s" % (rid, result.reason))


if __name__ == "__main__":
    unittest.main()
