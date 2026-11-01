"""Offline end-to-end integration harness for signed approvals and reservations.

No actual external verifier or dispatcher. This explicitly fails closed
on absent or invalid approval, unavailable verification, or invalid admission.
"""
from __future__ import annotations

from dataclasses import dataclass

from spray_schema.execution_safety import (
    AdmissionDecision, ExecutionRequest, IndependentEvidence, evaluate_execution,
)
from spray_schema.offline_approval_attestation import (
    SignedApproval, verify_approval,
)
from spray_schema.offline_integrated_control import OfflineIntegratedControl
from spray_schema.offline_reservation_store import DurableResult


@dataclass(frozen=True)
class DrillResult:
    accepted: bool
    reason: str


def run_offline_drill(
    control: OfflineIntegratedControl,
    request: ExecutionRequest,
    evidence: IndependentEvidence,
    approval: SignedApproval,
    *,
    trusted_now_epoch: int,
    trusted_keys: dict[str, bytes],
    revoked_approval_ids: frozenset[str],
    revoked_key_ids: frozenset[str],
    authorized_cash_ceiling_usd: str,
    verifier_available: bool,
    evidence_fresh: bool,
    inject_failure_after_reservation: bool = False,
) -> DrillResult:
    decision = evaluate_execution(
        request, evidence, authorized_cash_ceiling_usd=authorized_cash_ceiling_usd)
    if not decision.permitted or not decision.request_digest:
        return DrillResult(False, "admission_" + decision.reason)
    verified = verify_approval(
        approval, expected_request_digest=decision.request_digest,
        expected_request_id=request.request_id, expected_action=request.action,
        trusted_now_epoch=trusted_now_epoch, trusted_keys=trusted_keys,
        revoked_approval_ids=revoked_approval_ids, revoked_key_ids=revoked_key_ids)
    if not verified.verified:
        return DrillResult(False, "approval_" + verified.reason)
    reserved = control.reserve(
        request, decision, verifier_available=verifier_available,
        evidence_fresh=evidence_fresh,
        inject_failure_after_reservation=inject_failure_after_reservation)
    return DrillResult(reserved.accepted, reserved.reason)
