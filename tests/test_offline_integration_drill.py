import concurrent.futures
import os
import tempfile
import unittest

from spray_schema.execution_safety import ExecutionRequest, IndependentEvidence, evaluate_execution
from spray_schema.offline_approval_attestation import SignedApproval, sign_offline_for_test
from spray_schema.offline_integrated_control import OfflineIntegratedControl
from spray_schema.offline_integration_drill import run_offline_drill

NOW = 1_800_000_000
KEY = b"trusted-test-key" * 3


def evidence(**overrides):
    values = dict(owner_approved=True, global_stop_active=False,
                  budget_stop_active=False, identity_verified=True,
                  iam_scope_verified=True, cash_balance_verified=True,
                  lease_verified=True, idempotency_key_unused=True,
                  snapshot_fresh=True)
    values.update(overrides)
    return IndependentEvidence(**values)


def request(i):
    return ExecutionRequest(str(i), "offline", "a" * 64, "1")


def approval(req):
    digest = evaluate_execution(req, evidence(), authorized_cash_ceiling_usd="3").request_digest
    a = SignedApproval(digest, req.request_id, req.action, "owner", "test-key",
                       NOW - 1, NOW + 60, "approval-" + req.request_id, "")
    return sign_offline_for_test(a, KEY)


class IntegrationDrills(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.control = OfflineIntegratedControl(os.path.join(self.temp.name, "drill.db"))
        self.control.initialize("3")

    def run_drill(self, req, *, approval_override=None, evidence_override=None, **overrides):
        params = dict(trusted_now_epoch=NOW, trusted_keys={"test-key": KEY},
                      revoked_approval_ids=frozenset(), revoked_key_ids=frozenset(),
                      authorized_cash_ceiling_usd="3", verifier_available=True,
                      evidence_fresh=True)
        params.update(overrides)
        return run_offline_drill(self.control, req, evidence_override or evidence(),
                                 approval_override if approval_override is not None else approval(req),
                                 **params)

    def test_happy_path_and_replay(self):
        req = request("a")
        self.assertTrue(self.run_drill(req).accepted)
        self.assertEqual(self.run_drill(req).reason, "replay")
        self.assertEqual(self.control.reservations.snapshot(), ("2", False, 1))
        self.assertEqual(self.control.audit.verify().entries, 1)

    def test_revoked_expired_and_invalid_signer_denied(self):
        req = request("a")
        self.assertFalse(self.run_drill(req, revoked_approval_ids=frozenset({"approval-a"})).accepted)
        self.assertFalse(self.run_drill(req, trusted_now_epoch=NOW+100).accepted)
        self.assertFalse(self.run_drill(req, trusted_keys={"test-key": b"z" * 32}).accepted)
        self.assertEqual(self.control.reservations.snapshot(), ("3", False, 0))

    def test_outage_stop_and_stale_evidence_denied(self):
        req = request("a")
        self.assertFalse(self.run_drill(req, verifier_available=False).accepted)
        self.assertFalse(self.run_drill(req, evidence_fresh=False).accepted)
        self.assertFalse(self.run_drill(req, evidence_override=evidence(snapshot_fresh=False)).accepted)
        self.control.stop()
        self.assertFalse(self.run_drill(req).accepted)
        self.assertEqual(self.control.reservations.snapshot(), ("3", True, 0))

    def test_concurrent_signed_requests_do_not_overdraw(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.run_drill(request(i)), range(12)))
        self.assertEqual(sum(result.accepted for result in results), 3)
        self.assertEqual(self.control.reservations.snapshot(), ("0", False, 3))
        self.assertEqual(self.control.audit.verify().entries, 3)

    def test_injected_crash_rolls_back_reservation_and_audit(self):
        req = request("a")
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            self.run_drill(req, inject_failure_after_reservation=True)
        self.assertEqual(self.control.reservations.snapshot(), ("3", False, 0))
        self.assertEqual(self.control.audit.verify().entries, 0)


if __name__ == "__main__":
    unittest.main()
