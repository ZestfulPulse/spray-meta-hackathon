"""Offline authenticated approval attestation verifier for P0-G3.

HMAC prototype only. The independent verifier must own keys, revocations,
trusted clock and approval issuance; never place signing keys in SPRAY.
No cloud access, credentials, dispatch or spending authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Mapping


@dataclass(frozen=True)
class SignedApproval:
    request_digest: str
    request_id: str
    action: str
    approver_id: str
    key_id: str
    issued_at_epoch: int
    expires_at_epoch: int
    approval_id: str
    signature_hex: str


@dataclass(frozen=True)
class ApprovalVerification:
    verified: bool
    reason: str


def _payload(a: SignedApproval) -> bytes:
    fields = {name: getattr(a, name) for name in (
        "request_digest", "request_id", "action", "approver_id", "key_id",
        "issued_at_epoch", "expires_at_epoch", "approval_id")}
    return json.dumps(fields, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def sign_offline_for_test(a: SignedApproval, key: bytes) -> SignedApproval:
    """Test fixture helper; do not import into any runtime dispatcher."""
    from dataclasses import replace
    return replace(a, signature_hex=hmac.new(key, _payload(a), hashlib.sha256).hexdigest())


def verify_approval(
    approval: SignedApproval,
    *,
    expected_request_digest: str,
    expected_request_id: str,
    expected_action: str,
    trusted_now_epoch: int,
    trusted_keys: Mapping[str, bytes],
    revoked_approval_ids: frozenset[str],
    revoked_key_ids: frozenset[str],
    max_lifetime_seconds: int = 300,
) -> ApprovalVerification:
    if not isinstance(approval, SignedApproval):
        return ApprovalVerification(False, "invalid_approval")
    if type(trusted_now_epoch) is not int or trusted_now_epoch < 0:
        return ApprovalVerification(False, "invalid_clock")
    if type(max_lifetime_seconds) is not int or not 0 < max_lifetime_seconds <= 3600:
        return ApprovalVerification(False, "invalid_lifetime_policy")
    for field in ("request_digest", "request_id", "action", "approver_id", "key_id", "approval_id"):
        value = getattr(approval, field)
        if not isinstance(value, str) or not value or len(value) > 128:
            return ApprovalVerification(False, "invalid_" + field)
    if not re.fullmatch(r"[0-9a-f]{64}", approval.request_digest):
        return ApprovalVerification(False, "invalid_digest")
    if not isinstance(approval.signature_hex, str) or not re.fullmatch(r"[0-9a-f]{64}", approval.signature_hex):
        return ApprovalVerification(False, "invalid_signature")
    if type(approval.issued_at_epoch) is not int or type(approval.expires_at_epoch) is not int:
        return ApprovalVerification(False, "invalid_time")
    if approval.issued_at_epoch > trusted_now_epoch or approval.expires_at_epoch <= trusted_now_epoch:
        return ApprovalVerification(False, "expired_or_future")
    if not 0 < approval.expires_at_epoch - approval.issued_at_epoch <= max_lifetime_seconds:
        return ApprovalVerification(False, "lifetime_exceeded")
    if approval.request_digest != expected_request_digest or approval.request_id != expected_request_id or approval.action != expected_action:
        return ApprovalVerification(False, "scope_mismatch")
    if not isinstance(revoked_approval_ids, frozenset) or not isinstance(revoked_key_ids, frozenset):
        return ApprovalVerification(False, "invalid_revocation_snapshot")
    if approval.approval_id in revoked_approval_ids or approval.key_id in revoked_key_ids:
        return ApprovalVerification(False, "revoked")
    if not isinstance(trusted_keys, Mapping):
        return ApprovalVerification(False, "invalid_keyring")
    key = trusted_keys.get(approval.key_id)
    if not isinstance(key, bytes) or len(key) < 32:
        return ApprovalVerification(False, "unknown_or_weak_key")
    if not hmac.compare_digest(
        hmac.new(key, _payload(approval), hashlib.sha256).hexdigest(),
        approval.signature_hex,
    ):
        return ApprovalVerification(False, "signature_mismatch")
    return ApprovalVerification(True, "verified_offline_only")
