import concurrent.futures
import dataclasses
import os
import tempfile
import unittest

from spray_schema.offline_g4_control_plane import OfflineControlPlane, Permit, sign_test_permit
from spray_schema.offline_g4_scoped_gateway import OfflinePolicy

KEY = b"local-test-only-key-material-32-bytes"
NOW = 1800000000


def permit(i="1", **changes):
    values = dict(request_id="req-" + i, principal="owner", action="offline",
                  account="test-account", region="test-region", environment="sandbox",
                  ceiling_usd="1.00", nonce="nonce-000000000000" + i, issued_at=NOW-10,
                  expires_at=NOW+60, key_id="test", signature="")
    values.update(changes)
    return sign_test_permit(Permit(**values), KEY)


class UnifiedG4Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = OfflineControlPlane(os.path.join(self.tmp.name, "control.db"))
        self.db.initialize("3.00")

    def claim(self, p, **changes):
        args = dict(keyring={"test": KEY}, now=NOW, revoked_nonces=frozenset(),
                    stop_authority_available=True, stop_active=False,
                    revocation_fresh=True, billing_fresh=True, provider_credit_verified=True,
                    policy=OfflinePolicy(frozenset({"test-account"}), frozenset({"test-region"}),
                                         frozenset({"offline"}), frozenset({"sandbox"}), "3.00"))
        args.update(changes)
        return self.db.claim(p, **args)

    def test_direct_claim_without_policy_denied(self):
        self.assertEqual(self.claim(permit(), policy=None).reason, "missing_policy")
        self.assertEqual(self.db.snapshot(), ("3.00", 0, 0))

    def test_claim_replay_and_scope_tamper(self):
        p = permit()
        self.assertTrue(self.claim(p).accepted)
        self.assertEqual(self.claim(p).reason, "replay")
        self.assertTrue(self.claim(dataclasses.replace(p, account="prod")).reason.startswith("preflight_"))
        self.assertEqual(self.db.snapshot(), ("2.00", 1, 1))

    def test_revocation_expiry_outage_and_credit_denial(self):
        p = permit()
        for overrides in (
            dict(revoked_nonces=frozenset({"nonce-0000000000001"})),
            dict(now=NOW+100),
            dict(stop_authority_available=False),
            dict(revocation_fresh=False),
            dict(billing_fresh=False),
            dict(provider_credit_verified=False),
        ):
            with self.subTest(overrides=overrides):
                self.assertFalse(self.claim(p, **overrides).accepted)
        self.assertEqual(self.db.snapshot(), ("3.00", 0, 0))

    def test_concurrency_and_restart(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda i: self.claim(permit(str(i))), range(12)))
        self.assertEqual(sum(x.accepted for x in outcomes), 3)
        self.assertEqual(self.db.snapshot(), ("0.00", 3, 3))
        self.assertTrue(OfflineControlPlane(self.db.path).verify_audit())

    def test_atomic_failure_rollback(self):
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            self.claim(permit(), inject_failure=True)
        self.assertEqual(self.db.snapshot(), ("3.00", 0, 0))

    def test_settlement_refund_and_duplicate_provider_ref(self):
        self.assertTrue(self.claim(permit("1")).accepted)
        self.assertTrue(self.claim(permit("2")).accepted)
        self.assertTrue(self.db.settle("nonce-0000000000001", "invoice-1", "0.40").accepted)
        self.assertEqual(self.db.snapshot(), ("1.60", 2, 3))
        self.assertEqual(self.db.settle("nonce-0000000000001", "invoice-1", "0.40").reason, "missing_or_settled")
        self.assertEqual(self.db.settle("nonce-0000000000002", "invoice-1", "0.50").reason, "duplicate_provider_reference")
        self.assertEqual(self.db.settle("nonce-0000000000002", "invoice-2", "2.00").reason, "settlement_exceeds_reservation")
        self.assertTrue(self.db.verify_audit())

    def test_durable_stop_and_audit_tamper(self):
        self.db.stop()
        self.assertEqual(self.claim(permit()).reason, "durable_stop")
        self.assertTrue(self.db.verify_audit())
        import sqlite3
        with sqlite3.connect(self.db.path) as db:
            db.execute("UPDATE journal SET data='tampered' WHERE seq=1")
        self.assertFalse(self.db.verify_audit())

    def test_bad_money_denied(self):
        self.assertIn("cash_ceiling", self.claim(permit(ceiling_usd="NaN")).reason)
        self.assertIn("cash_ceiling", self.claim(permit(ceiling_usd="1.001")).reason)


if __name__ == "__main__":
    unittest.main()
