import dataclasses
import os
import tempfile
import unittest

from spray_schema.offline_g4_control_plane import OfflineControlPlane, Permit, sign_test_permit
from spray_schema.offline_g4_scoped_gateway import OfflinePolicy, ScopedOfflineGateway


KEY = b"offline-test-key-material-32-bytes"
NOW = 1800000000


class ScopedGatewayTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.control = OfflineControlPlane(os.path.join(self.tmp.name, "control.db"))
        self.control.initialize("5.00")
        policy = OfflinePolicy(frozenset({"test"}), frozenset({"local"}),
                               frozenset({"offline"}), frozenset({"sandbox"}), "2.00")
        self.gateway = ScopedOfflineGateway(self.control, policy)

    def permit(self, **changes):
        fields = dict(request_id="request-1", principal="owner", action="offline",
                      account="test", region="local", environment="sandbox",
                      ceiling_usd="1.00", nonce="nonce-12345678901", issued_at=NOW-1,
                      expires_at=NOW+30, key_id="test", signature="")
        fields.update(changes)
        return sign_test_permit(Permit(**fields), KEY)

    def claim(self, p):
        return self.gateway.claim(
            p, keyring={"test": KEY}, now=NOW, revoked_nonces=frozenset(),
            stop_authority_available=True, stop_active=False, revocation_fresh=True,
            billing_fresh=True, provider_credit_verified=True)

    def test_valid_claim(self):
        self.assertTrue(self.claim(self.permit()).accepted)

    def test_wrong_account_denied_before_reservation(self):
        self.assertFalse(self.claim(self.permit(account="other")).accepted)
        self.assertEqual(self.control.snapshot(), ("5.00", 0, 0))

    def test_over_policy_ceiling_denied_before_reservation(self):
        self.assertFalse(self.claim(self.permit(ceiling_usd="2.01")).accepted)
        self.assertEqual(self.control.snapshot(), ("5.00", 0, 0))

    def test_missing_policy_denied(self):
        self.gateway.policy = None
        self.assertEqual(self.claim(self.permit()).reason, "missing_policy")


if __name__ == "__main__":
    unittest.main()
