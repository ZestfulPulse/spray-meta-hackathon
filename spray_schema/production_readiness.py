"""P0-G3 production readiness gate: explicit fail-closed evidence matrix.

This is a pure offline evaluator. It cannot grant deployment permissions,
alter IAM, create AWS resources, or approve spending.
"""
from __future__ import annotations

from dataclasses import dataclass


REQUIRED_EXTERNAL_CONTROLS = (
    "independent_owner_auth",
    "independent_signer_and_key_custody",
    "authenticated_revocation_and_clock",
    "durable_single_use_approval",
    "independent_emergency_stop",
    "restricted_iam_and_secret_isolation",
    "atomic_dispatch_and_reconciliation",
    "provider_billing_and_credit_validation",
    "external_immutable_audit",
    "real_failure_drills",
    "explicit_owner_deployment_approval",
)


@dataclass(frozen=True)
class ReadinessEvidence:
    control_id: str
    status: str
    evidence_ref: str


@dataclass(frozen=True)
class ReadinessDecision:
    ready: bool
    missing_or_failed: tuple[str, ...]
    reason: str


def evaluate_readiness(evidence: tuple[ReadinessEvidence, ...]) -> ReadinessDecision:
    if not isinstance(evidence, tuple):
        return ReadinessDecision(False, REQUIRED_EXTERNAL_CONTROLS, "invalid_evidence_set")
    indexed: dict[str, ReadinessEvidence] = {}
    for entry in evidence:
        if not isinstance(entry, ReadinessEvidence) or not isinstance(entry.control_id, str):
            return ReadinessDecision(False, REQUIRED_EXTERNAL_CONTROLS, "invalid_evidence_entry")
        if entry.control_id not in REQUIRED_EXTERNAL_CONTROLS or entry.control_id in indexed:
            return ReadinessDecision(False, REQUIRED_EXTERNAL_CONTROLS, "unknown_or_duplicate_control")
        indexed[entry.control_id] = entry
    missing = tuple(
        control for control in REQUIRED_EXTERNAL_CONTROLS
        if control not in indexed or indexed[control].status != "PASS"
        or not isinstance(indexed[control].evidence_ref, str)
        or not indexed[control].evidence_ref.strip()
    )
    if missing:
        return ReadinessDecision(False, missing, "external_controls_not_verified")
    return ReadinessDecision(True, (), "evidence_matrix_complete_not_authorization")
