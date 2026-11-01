import json
import os
import tempfile
import unittest

from spray_schema.decision_record import (
    DecisionValidation,
    STATUS_FINALIZED,
    STATUS_PREDICTED,
    available_prior_lessons_not_cited,
    create_record,
    finalize_record,
    load_prior_lessons,
    load_records,
    validate_finalized_edit,
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


class DecisionRecordTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.dir = os.path.join(self.temp.name, "decisions")

    # ----- happy path ----------------------------------------------------- #
    def test_create_then_finalize_happy_path(self):
        record = create_record(self.dir, **_base_kwargs())
        self.assertEqual(record["id"], "D-0001")
        self.assertEqual(record["status"], STATUS_PREDICTED)
        self.assertIsNone(record["actual_outcome"])
        self.assertIsNone(record["prediction_vs_actual"])
        self.assertIsNone(record["lesson"])

        # On-disk file matches and has a trailing newline.
        path = os.path.join(self.dir, "D-0001.json")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as handle:
            raw = handle.read()
        self.assertTrue(raw.endswith("\n"))
        self.assertEqual(json.loads(raw)["status"], STATUS_PREDICTED)

        # DECISIONS.md gained one Korean summary line.
        md = os.path.join(self.dir, "DECISIONS.md")
        self.assertTrue(os.path.isfile(md))
        with open(md, encoding="utf-8") as handle:
            md_text = handle.read()
        self.assertIn("D-0001", md_text)
        self.assertIn("선택", md_text)

        finalized = finalize_record(
            self.dir, "D-0001",
            actual_outcome="metric improved",
            prediction_vs_actual="matched prediction",
            lesson="front-load the highest impact candidate",
        )
        self.assertEqual(finalized["status"], STATUS_FINALIZED)
        self.assertEqual(finalized["actual_outcome"], "metric improved")
        self.assertEqual(finalized["lesson"],
                         "front-load the highest impact candidate")
        # Non-allowed fields are unchanged.
        self.assertEqual(finalized["chosen_action"], record["chosen_action"])
        self.assertEqual(finalized["prediction"], record["prediction"])

    def test_create_rejects_outcome_fields(self):
        # create_record has no outcome parameters: passing them is a TypeError.
        with self.assertRaises(TypeError):
            create_record(self.dir, actual_outcome="x", **_base_kwargs())

    def test_sequential_ids_increment(self):
        create_record(self.dir, **_base_kwargs())
        finalize_record(
            self.dir, "D-0001", actual_outcome="o",
            prediction_vs_actual="p", lesson="lesson one",
        )
        second = create_record(self.dir, **_base_kwargs(
            prior_decision_ids_read=["D-0001"],
            lessons_applied=[{"decision_id": "D-0001", "lesson": "lesson one"}],
        ))
        self.assertEqual(second["id"], "D-0002")

    # ----- validator rejection rules (table-driven) ----------------------- #
    def test_validator_rejection_rules(self):
        cases = [
            (
                "fewer_than_three_candidates",
                _record(candidates=[{"name": "a"}, {"name": "b"}]),
                [],
            ),
            (
                "missing_scoring_criteria",
                _record(scoring_criteria=[]),
                [],
            ),
            (
                "missing_candidate_scores",
                _record(candidate_scores=[]),
                [],
            ),
            (
                "missing_prediction",
                _record(prediction="   "),
                [],
            ),
            (
                "prior_decision_id_not_found",
                _record(prior_decision_ids_read=["D-0001"]),
                [],
            ),
            (
                "non_sequential_id",
                _record(id="D-0002"),
                [],
            ),
        ]
        for reason, record, priors in cases:
            with self.subTest(reason=reason):
                result = validate_record(record, prior_records=priors)
                self.assertFalse(result.valid)
                self.assertEqual(result.reason, reason)

    def test_lessons_applied_cites_nonexistent_decision(self):
        record = _record(
            id="D-0002",
            prior_decision_ids_read=["D-0001"],
            lessons_applied=[{"decision_id": "D-0009", "lesson": "x"}],
        )
        prior = _record(id="D-0001", status=STATUS_FINALIZED, lesson="real")
        result = validate_record(record, prior_records=[prior])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "lesson_decision_id_not_found")

    def test_lessons_applied_text_mismatch(self):
        prior = _record(id="D-0001", status=STATUS_FINALIZED, lesson="real lesson")
        record = _record(
            id="D-0002",
            prior_decision_ids_read=["D-0001"],
            lessons_applied=[{"decision_id": "D-0001", "lesson": "wrong text"}],
        )
        result = validate_record(record, prior_records=[prior])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "lesson_text_mismatch")

    def test_available_prior_lessons_not_cited(self):
        prior = _record(id="D-0001", status=STATUS_FINALIZED, lesson="real lesson")
        record = _record(
            id="D-0002",
            prior_decision_ids_read=["D-0001"],
            lessons_applied=[],
        )
        result = validate_record(record, prior_records=[prior])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "available_prior_lessons_not_cited")
        self.assertTrue(available_prior_lessons_not_cited(record, [prior]))

    def test_d0001_citing_none_is_accepted(self):
        result = validate_record(_record(), prior_records=[])
        self.assertTrue(result.valid)
        self.assertFalse(available_prior_lessons_not_cited(_record(), []))

    def test_record_citing_prior_lessons_is_accepted(self):
        prior = _record(id="D-0001", status=STATUS_FINALIZED, lesson="real lesson")
        record = _record(
            id="D-0002",
            prior_decision_ids_read=["D-0001"],
            lessons_applied=[{"decision_id": "D-0001", "lesson": "real lesson"}],
        )
        result = validate_record(record, prior_records=[prior])
        self.assertTrue(result.valid)

    # ----- finalized-record immutability ---------------------------------- #
    def test_finalize_twice_rejected(self):
        create_record(self.dir, **_base_kwargs())
        finalize_record(
            self.dir, "D-0001", actual_outcome="o",
            prediction_vs_actual="p", lesson="lesson one",
        )
        with self.assertRaises(ValueError):
            finalize_record(
                self.dir, "D-0001", actual_outcome="o2",
                prediction_vs_actual="p2", lesson="lesson two",
            )

    def test_validate_finalized_edit_rejects_change(self):
        finalized = _record(id="D-0001", status=STATUS_FINALIZED, lesson="done")
        edited = dict(finalized)
        edited["chosen_action"] = "tampered"
        result = validate_finalized_edit(finalized, edited)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "edit_to_finalized_record")
        self.assertTrue(validate_finalized_edit(finalized, dict(finalized)).valid)

    def test_create_refuses_overwrite(self):
        create_record(self.dir, **_base_kwargs())
        with self.assertRaises(ValueError):
            create_record(self.dir, id="D-0001", **_base_kwargs())

    # ----- helpers / loaders ---------------------------------------------- #
    def test_load_prior_lessons_only_finalized(self):
        create_record(self.dir, **_base_kwargs())
        # Still PREDICTED: no lessons available.
        self.assertEqual(load_prior_lessons(self.dir), [])
        finalize_record(
            self.dir, "D-0001", actual_outcome="o",
            prediction_vs_actual="p", lesson="learned something",
        )
        lessons = load_prior_lessons(self.dir)
        self.assertEqual(lessons, [{"decision_id": "D-0001",
                                    "lesson": "learned something"}])

    def test_load_records_sorted(self):
        create_record(self.dir, **_base_kwargs())
        finalize_record(
            self.dir, "D-0001", actual_outcome="o",
            prediction_vs_actual="p", lesson="lesson one",
        )
        create_record(self.dir, **_base_kwargs(
            prior_decision_ids_read=["D-0001"],
            lessons_applied=[{"decision_id": "D-0001", "lesson": "lesson one"}],
        ))
        ids = [r["id"] for r in load_records(self.dir)]
        self.assertEqual(ids, ["D-0001", "D-0002"])

    def test_validation_result_is_frozen(self):
        result = validate_record(_record(), prior_records=[])
        self.assertIsInstance(result, DecisionValidation)
        with self.assertRaises(Exception):
            result.valid = False  # frozen dataclass


def _record(**overrides):
    """A fully-populated valid D-0001 record dict for validator tests."""
    record = {
        "id": "D-0001",
        "observed_state": {"note": "state"},
        "prior_decision_ids_read": [],
        "candidates": [
            {"name": "a", "description": "A"},
            {"name": "b", "description": "B"},
            {"name": "c", "description": "C"},
        ],
        "scoring_criteria": ["impact", "cost"],
        "candidate_scores": [
            {"candidate": "a", "impact": 3, "cost": 1},
            {"candidate": "b", "impact": 2, "cost": 2},
            {"candidate": "c", "impact": 1, "cost": 3},
        ],
        "chosen_action": "pursue a",
        "chosen_rationale": "best",
        "rejected": [
            {"candidate": "b", "why": "worse"},
            {"candidate": "c", "why": "riskier"},
        ],
        "prediction": "a will work",
        "status": STATUS_PREDICTED,
        "actual_outcome": None,
        "prediction_vs_actual": None,
        "lesson": None,
        "lessons_applied": [],
    }
    record.update(overrides)
    return record


if __name__ == "__main__":
    unittest.main()
