"""Pure, fail-closed P0-G3 execution admission policy.

This module is deliberately offline: it does not call AWS, dispatch work,
hold credentials, or authorize any real payment. All evidence must be supplied
by a separately controlled, trusted verifier. A permit is one-time and scoped
to a specific immutable action and request identifier.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from typing import Any


@dataclass(frozen=True)
class ExecutionRequest:
    request_id: str
    action: str
    payload_sha256: str
    estimated_cash_usd: str


@dataclass(frozen=True)
class IndependentEvidence:
    """Snapshot supplied by an independent owner-controlled verifier."""

    owner_approved: bool
    global_stop_active: bool
    budget_stop_active: bool
    identity_verified: bool
    iam_scope_verified: bool
    cash_balance_verified: bool
    lease_verified: bool
    idempotency_key_unused: bool
    snapshot_fresh: bool


@dataclass(frozen=True)
class AdmissionDecision:
    permitted: bool
    reason: str
    request_digest: str | None = None


def _cash_amount(raw: str) -> Decimal | None:
    if not isinstance(raw, str) or not raw or raw.strip() != raw:
        return None
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    if not amount.is_finite() or amount < 0:
        return None
    return amount


def evaluate_execution(
    request: ExecutionRequest,
    evidence: IndependentEvidence,
    *,
    authorized_cash_ceiling_usd: str,
) -> AdmissionDecision:
    """Decide only; caller must still perform atomic external enforcement."""
    if not isinstance(request, ExecutionRequest) or not isinstance(evidence, IndependentEvidence):
        return AdmissionDecision(False, "invalid_input")
    if not isinstance(request.request_id, str) or not request.request_id or len(request.request_id) > 128:
        return AdmissionDecision(False, "invalid_request_id")
    if not isinstance(request.action, str) or not request.action or len(request.action) > 128:
        return AdmissionDecision(False, "invalid_action")
    digest = request.payload_sha256
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        return AdmissionDecision(False, "invalid_payload_digest")
    amount = _cash_amount(request.estimated_cash_usd)
    ceiling = _cash_amount(authorized_cash_ceiling_usd)
    if amount is None or ceiling is None:
        return AdmissionDecision(False, "invalid_money")
    if amount > ceiling:
        return AdmissionDecision(False, "cash_ceiling_exceeded")
    checks = (
        ("owner_approved", evidence.owner_approved),
        ("global_stop_active", not evidence.global_stop_active),
        ("budget_stop_active", not evidence.budget_stop_active),
        ("identity_verified", evidence.identity_verified),
        ("iam_scope_verified", evidence.iam_scope_verified),
        ("cash_balance_verified", evidence.cash_balance_verified),
        ("lease_verified", evidence.lease_verified),
        ("idempotency_key_unused", evidence.idempotency_key_unused),
        ("snapshot_fresh", evidence.snapshot_fresh),
    )
    for field, value in checks:
        if type(value) is not bool:
            return AdmissionDecision(False, "invalid_evidence_" + field)
        if not value:
            return AdmissionDecision(False, "denied_" + field)
    canonical = json.dumps(
        {"request_id": request.request_id, "action": request.action,
         "payload_sha256": digest, "estimated_cash_usd": str(amount)},
        sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    )
    return AdmissionDecision(True, "admission_only_not_dispatch", hashlib.sha256(canonical.encode()).hexdigest())
