import unittest

from spray_schema.production_readiness import (
    REQUIRED_EXTERNAL_CONTROLS, ReadinessEvidence, evaluate_readiness,
)


class ProductionReadinessTests(unittest.TestCase):
    def test_empty_evidence_denies(self):
        result = evaluate_readiness(())
        self.assertFalse(result.ready)
        self.assertEqual(result.missing_or_failed, REQUIRED_EXTERNAL_CONTROLS)

    def test_all_unverified_denies(self):
        evidence = tuple(ReadinessEvidence(c, "NOT_VERIFIED", "") for c in REQUIRED_EXTERNAL_CONTROLS)
        self.assertFalse(evaluate_readiness(evidence).ready)

    def test_one_missing_control_denies(self):
        evidence = tuple(ReadinessEvidence(c, "PASS", "external:evidence/" + c)
                         for c in REQUIRED_EXTERNAL_CONTROLS[:-1])
        self.assertEqual(evaluate_readiness(evidence).missing_or_failed,
                         (REQUIRED_EXTERNAL_CONTROLS[-1],))

    def test_missing_evidence_reference_denies(self):
        evidence = tuple(ReadinessEvidence(c, "PASS", "" if i == 0 else "external:evidence")
                         for i, c in enumerate(REQUIRED_EXTERNAL_CONTROLS))
        self.assertFalse(evaluate_readiness(evidence).ready)

    def test_duplicate_and_unknown_control_denied(self):
        item = ReadinessEvidence(REQUIRED_EXTERNAL_CONTROLS[0], "PASS", "ref")
        self.assertEqual(evaluate_readiness((item, item)).reason, "unknown_or_duplicate_control")
        self.assertFalse(evaluate_readiness((ReadinessEvidence("invented", "PASS", "ref"),)).ready)

    def test_complete_matrix_only_reports_evidence_not_authorization(self):
        evidence = tuple(ReadinessEvidence(c, "PASS", "external:evidence/" + c)
                         for c in REQUIRED_EXTERNAL_CONTROLS)
        result = evaluate_readiness(evidence)
        self.assertTrue(result.ready)
        self.assertEqual(result.reason, "evidence_matrix_complete_not_authorization")


if __name__ == "__main__":
    unittest.main()
