import dataclasses
import unittest

from spray_schema.offline_approval_attestation import (
    SignedApproval, sign_offline_for_test, verify_approval,
)


KEY = b"k" * 32
NOW = 1_800_000_000


def approval(**overrides):
    fields = dict(request_digest="a" * 64, request_id="r-1", action="offline",
                  approver_id="owner", key_id="key-1", issued_at_epoch=NOW-10,
                  expires_at_epoch=NOW+60, approval_id="approval-1", signature_hex="")
    fields.update(overrides)
    return sign_offline_for_test(SignedApproval(**fields), KEY)


def verify(a, **overrides):
    params = dict(expected_request_digest="a" * 64, expected_request_id="r-1",
                  expected_action="offline", trusted_now_epoch=NOW,
                  trusted_keys={"key-1": KEY}, revoked_approval_ids=frozenset(),
                  revoked_key_ids=frozenset())
    params.update(overrides)
    return verify_approval(a, **params)


class AttestationTests(unittest.TestCase):
    def test_valid_scope_and_signature(self):
        self.assertTrue(verify(approval()).verified)

    def test_tamper_detection(self):
        signed = approval()
        for field, changed in (("action", "paid"), ("request_id", "r-2"),
                               ("request_digest", "b" * 64), ("expires_at_epoch", NOW+70)):
            with self.subTest(field=field):
                self.assertFalse(verify(dataclasses.replace(signed, **{field: changed})).verified)

    def test_expired_future_and_long_lifetime(self):
        self.assertFalse(verify(approval(expires_at_epoch=NOW)).verified)
        self.assertFalse(verify(approval(issued_at_epoch=NOW+1)).verified)
        self.assertFalse(verify(approval(expires_at_epoch=NOW+1000)).verified)

    def test_approval_and_key_revocation(self):
        self.assertEqual(verify(approval(), revoked_approval_ids=frozenset({"approval-1"})).reason, "revoked")
        self.assertEqual(verify(approval(), revoked_key_ids=frozenset({"key-1"})).reason, "revoked")

    def test_missing_key_and_wrong_key(self):
        self.assertFalse(verify(approval(), trusted_keys={}).verified)
        self.assertFalse(verify(approval(), trusted_keys={"key-1": b"z" * 32}).verified)

    def test_missing_revocation_snapshot_fails_closed(self):
        self.assertFalse(verify(approval(), revoked_approval_ids=None).verified)

    def test_scope_rebinding_denied(self):
        self.assertFalse(verify(approval(), expected_request_id="different").verified)
        self.assertFalse(verify(approval(), expected_action="aws-deploy").verified)

    def test_boolean_clock_denied(self):
        self.assertFalse(verify(approval(), trusted_now_epoch=True).verified)


if __name__ == "__main__":
    unittest.main()
