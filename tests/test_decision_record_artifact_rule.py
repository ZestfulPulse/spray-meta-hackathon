"""Offline tests for the R2-S6 additive artifact-existence validator rule.

These prove ``validate_record`` rejects a FINALIZED record whose referenced
artifact is missing (``artifact_missing``) or whose recomputed sha256 does not
match (``artifact_sha256_mismatch``), while remaining strictly additive: a
record WITHOUT the artifact fields validates unchanged, a PREDICTED record is
unaffected, and when no resolution root is supplied the on-disk check is
skipped (so every existing caller / fixture keeps passing). No network.
"""
import hashlib
import tempfile
import unittest
from pathlib import Path

from spray_schema.decision_record import (
    STATUS_FINALIZED,
    STATUS_PREDICTED,
    create_record,
    validate_record,
)


def _base_kwargs(**overrides):
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
            {"candidate": "candidate-a", "impact": 3},
            {"candidate": "candidate-b", "impact": 2},
            {"candidate": "candidate-c", "impact": 1},
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


class ArtifactRuleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.decisions_dir = Path(self.temp.name) / "decisions"

    def _predicted(self):
        return create_record(self.decisions_dir, **_base_kwargs())

    def _write_artifact(self, rel_path: str, content: bytes) -> str:
        target = self.decisions_dir / rel_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return hashlib.sha256(content).hexdigest()

    def _finalize_dict(self, record, *, artifact_path=None, artifact_sha256=None):
        final = dict(record)
        final["status"] = STATUS_FINALIZED
        final["actual_outcome"] = {"measured": True}
        final["prediction_vs_actual"] = "artifact produced as predicted"
        final["lesson"] = "a lesson learned"
        if artifact_path is not None:
            final["artifact_path"] = artifact_path
            final["artifact_sha256"] = artifact_sha256
        return final

    # (i) artifact present with matching sha256 -> valid ------------------- #
    def test_finalized_with_matching_artifact_is_valid(self):
        rec = self._predicted()
        rel = "artifacts/D-0001/inventory.json"
        digest = self._write_artifact(rel, b'{"analysis":"inventory"}\n')
        final = self._finalize_dict(rec, artifact_path=rel, artifact_sha256=digest)
        result = validate_record(
            final, prior_records=[], artifacts_resolution_root=self.decisions_dir)
        self.assertTrue(result.valid, result.reason)

    # (ii) artifact missing -> artifact_missing ---------------------------- #
    def test_finalized_with_missing_artifact_is_rejected(self):
        rec = self._predicted()
        rel = "artifacts/D-0001/inventory.json"
        digest = hashlib.sha256(b"never written").hexdigest()
        final = self._finalize_dict(rec, artifact_path=rel, artifact_sha256=digest)
        result = validate_record(
            final, prior_records=[], artifacts_resolution_root=self.decisions_dir)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "artifact_missing")

    # (iii) content changed so sha256 mismatches --------------------------- #
    def test_finalized_with_sha256_mismatch_is_rejected(self):
        rec = self._predicted()
        rel = "artifacts/D-0001/inventory.json"
        recorded = self._write_artifact(rel, b'{"analysis":"inventory"}\n')
        # Overwrite the artifact AFTER recording so the sha256 no longer matches.
        (self.decisions_dir / rel).write_bytes(b'{"analysis":"tampered"}\n')
        final = self._finalize_dict(
            rec, artifact_path=rel, artifact_sha256=recorded)
        result = validate_record(
            final, prior_records=[], artifacts_resolution_root=self.decisions_dir)
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "artifact_sha256_mismatch")

    # (iv) no artifact reference -> still valid (backward compat) ---------- #
    def test_finalized_without_artifact_reference_is_valid(self):
        rec = self._predicted()
        final = self._finalize_dict(rec)
        self.assertNotIn("artifact_path", final)
        # Valid both with and without a resolution root.
        self.assertTrue(validate_record(final, prior_records=[]).valid)
        self.assertTrue(
            validate_record(
                final, prior_records=[],
                artifacts_resolution_root=self.decisions_dir).valid)

    # (v) a PREDICTED record is unaffected by the rule --------------------- #
    def test_predicted_record_with_artifact_fields_not_file_checked(self):
        rec = self._predicted()
        # Hand-attach artifact fields to a PREDICTED record pointing at a
        # NONEXISTENT file. The rule must not fire for PREDICTED records.
        pred = dict(rec)
        self.assertEqual(pred["status"], STATUS_PREDICTED)
        pred["artifact_path"] = "artifacts/D-0001/missing.json"
        pred["artifact_sha256"] = hashlib.sha256(b"x").hexdigest()
        result = validate_record(
            pred, prior_records=[], artifacts_resolution_root=self.decisions_dir)
        self.assertTrue(result.valid, result.reason)

    # No resolution root -> on-disk check skipped (backward compatible). --- #
    def test_no_resolution_root_skips_file_check(self):
        rec = self._predicted()
        rel = "artifacts/D-0001/missing.json"
        digest = hashlib.sha256(b"x").hexdigest()
        final = self._finalize_dict(rec, artifact_path=rel, artifact_sha256=digest)
        # Default root (None) -> the file is NOT resolved; shape is still OK.
        self.assertTrue(validate_record(final, prior_records=[]).valid)

    # Shape validation of the fields when present ------------------------- #
    def test_invalid_sha256_shape_is_rejected(self):
        rec = self._predicted()
        final = self._finalize_dict(
            rec, artifact_path="artifacts/D-0001/x.json",
            artifact_sha256="not-a-valid-digest")
        result = validate_record(final, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_artifact_sha256")

    def test_invalid_artifact_path_shape_is_rejected(self):
        rec = self._predicted()
        final = self._finalize_dict(
            rec, artifact_path="", artifact_sha256=hashlib.sha256(b"x").hexdigest())
        # Empty path -> invalid shape.
        final["artifact_path"] = ""
        result = validate_record(final, prior_records=[])
        self.assertFalse(result.valid)
        self.assertEqual(result.reason, "invalid_artifact_path")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
