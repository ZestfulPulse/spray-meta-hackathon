"""Offline G4 trust-boundary diagnostics.

A caller-controlled policy and caller-controlled evidence do NOT provide
independent authorization. These checks deliberately demonstrate the gap.
"""
from __future__ import annotations

from dataclasses import dataclass

from spray_schema.offline_g4_control_plane import OfflineControlPlane, Outcome, Permit
from spray_schema.offline_g4_scoped_gateway import OfflinePolicy


@dataclass(frozen=True)
class BoundaryReport:
    production_ready: bool
    reasons: tuple[str, ...]


def inspect_independence(*, signer_outside_process: bool,
                         policy_outside_process: bool,
                         stop_outside_process: bool,
                         billing_provider_authenticated: bool,
                         audit_anchored_externally: bool,
                         direct_database_access_blocked: bool) -> BoundaryReport:
    evidence = {
        "signer_not_independent": signer_outside_process,
        "policy_not_independent": policy_outside_process,
        "stop_not_independent": stop_outside_process,
        "billing_not_authenticated": billing_provider_authenticated,
        "audit_not_anchored": audit_anchored_externally,
        "database_access_not_isolated": direct_database_access_blocked,
    }
    missing = tuple(reason for reason, passed in evidence.items() if passed is not True)
    # Caller flags are merely claims; never certify production from them.
    return BoundaryReport(False, missing or ("self_reported_evidence_untrusted",))


def demonstrate_policy_forgery(control: OfflineControlPlane, permit: Permit,
                               *, keyring: dict[str, bytes], now: int) -> Outcome:
    """Adversarial offline proof: permissive caller policy is accepted.

    The test MUST show this weakness, not mistake acceptance for security.
    """
    forged = OfflinePolicy(
        frozenset({permit.account}), frozenset({permit.region}),
        frozenset({permit.action}), frozenset({permit.environment}),
        permit.ceiling_usd,
    )
    return control.claim(
        permit, policy=forged, keyring=keyring, now=now,
        revoked_nonces=frozenset(), stop_authority_available=True,
        stop_active=False, revocation_fresh=True, billing_fresh=True,
        provider_credit_verified=True,
    )
