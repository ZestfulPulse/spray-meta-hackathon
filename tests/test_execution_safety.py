import unittest

from spray_schema.execution_safety import (
    AdmissionDecision, ExecutionRequest, IndependentEvidence, evaluate_execution,
)


def good_evidence(**overrides):
    values = dict(
        owner_approved=True, global_stop_active=False, budget_stop_active=False,
        identity_verified=True, iam_scope_verified=True,
        cash_balance_verified=True, lease_verified=True,
        idempotency_key_unused=True, snapshot_fresh=True,
    )
    values.update(overrides)
    return IndependentEvidence(**values)


def good_request(amount="0.25"):
    return ExecutionRequest("req-001", "offline-evaluation", "a" * 64, amount)


class ExecutionSafetyTests(unittest.TestCase):
    def decide(self, request=None, evidence=None, ceiling="1.00"):
        return evaluate_execution(request or good_request(), evidence or good_evidence(),
                                  authorized_cash_ceiling_usd=ceiling)

    def test_approved_request_is_admission_only(self):
        result = self.decide()
        self.assertTrue(result.permitted)
        self.assertEqual(result.reason, "admission_only_not_dispatch")
        self.assertEqual(len(result.request_digest), 64)
        self.assertEqual(result, self.decide())

    def test_every_positive_evidence_gate_fails_closed(self):
        for field in ("owner_approved", "identity_verified", "iam_scope_verified",
                      "cash_balance_verified", "lease_verified",
                      "idempotency_key_unused", "snapshot_fresh"):
            with self.subTest(field=field):
                self.assertFalse(self.decide(evidence=good_evidence(**{field: False})).permitted)

    def test_stop_switches_deny(self):
        for field in ("global_stop_active", "budget_stop_active"):
            with self.subTest(field=field):
                self.assertFalse(self.decide(evidence=good_evidence(**{field: True})).permitted)

    def test_malformed_evidence_denies(self):
        self.assertFalse(self.decide(evidence=good_evidence(owner_approved=1)).permitted)
        self.assertFalse(self.decide(evidence=good_evidence(snapshot_fresh=None)).permitted)

    def test_exact_money_and_no_float_authority(self):
        self.assertFalse(self.decide(request=good_request("0.30000000000000000001"),
                                     ceiling="0.30").permitted)
        self.assertTrue(self.decide(request=good_request("0.30"), ceiling="0.30").permitted)
        for amount in ("NaN", "Infinity", "-0.01", "1e999999999", " 0.2", ""):
            with self.subTest(amount=amount):
                self.assertFalse(self.decide(request=good_request(amount)).permitted)

    def test_invalid_identity_or_payload_denies(self):
        self.assertFalse(self.decide(request=ExecutionRequest("", "x", "a" * 64, "0")).permitted)
        self.assertFalse(self.decide(request=ExecutionRequest("r", "x", "A" * 64, "0")).permitted)
        self.assertFalse(self.decide(request=ExecutionRequest("r", "", "a" * 64, "0")).permitted)

    def test_replay_denied_by_external_evidence(self):
        self.assertFalse(self.decide(evidence=good_evidence(idempotency_key_unused=False)).permitted)


if __name__ == "__main__":
    unittest.main()
