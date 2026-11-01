import os
import tempfile
import unittest

from spray_schema.offline_g4_boundary_diagnostics import (
    demonstrate_policy_forgery, inspect_independence,
)
from spray_schema.offline_g4_control_plane import (
    OfflineControlPlane, Permit, sign_test_permit,
)

KEY = b"test-only-hmac-key-material-32bytes"
NOW = 1800000000


class G4TrustBoundaryTests(unittest.TestCase):
    def test_all_self_reported_evidence_never_certifies_production(self):
        report = inspect_independence(
            signer_outside_process=True, policy_outside_process=True,
            stop_outside_process=True, billing_provider_authenticated=True,
            audit_anchored_externally=True, direct_database_access_blocked=True)
        self.assertFalse(report.production_ready)
        self.assertEqual(report.reasons, ("self_reported_evidence_untrusted",))

    def test_missing_independent_controls_reported(self):
        report = inspect_independence(
            signer_outside_process=False, policy_outside_process=False,
            stop_outside_process=False, billing_provider_authenticated=False,
            audit_anchored_externally=False, direct_database_access_blocked=False)
        self.assertFalse(report.production_ready)
        self.assertEqual(len(report.reasons), 6)

    def test_caller_policy_can_still_be_forged_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = OfflineControlPlane(os.path.join(tmp, "test.db"))
            db.initialize("10.00")
            p = sign_test_permit(Permit(
                "req-adversary", "owner", "offline", "unexpected-account",
                "unexpected-region", "sandbox", "1.00", "nonce-123456789012345",
                NOW - 1, NOW + 60, "test", ""), KEY)
            result = demonstrate_policy_forgery(
                db, p, keyring={"test": KEY}, now=NOW)
            self.assertTrue(result.accepted, "Expected to expose caller-policy forgery")
            self.assertEqual(db.snapshot(), ("9.00", 1, 1))


if __name__ == "__main__":
    unittest.main()
