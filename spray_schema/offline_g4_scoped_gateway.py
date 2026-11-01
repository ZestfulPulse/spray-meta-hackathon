"""Mandatory preflight facade for the OFFLINE G4 reference control plane.

Callers using this facade cannot reach the reservation method without a
trusted local policy. Direct access to OfflineControlPlane.claim still exists:
this is NOT a security boundary against a compromised process.
"""
from __future__ import annotations

from dataclasses import dataclass

from spray_schema.offline_g4_control_plane import OfflineControlPlane, Outcome, Permit
from spray_schema.offline_g4_preflight import check_permit_shape


@dataclass(frozen=True)
class OfflinePolicy:
    allowed_accounts: frozenset[str]
    allowed_regions: frozenset[str]
    allowed_actions: frozenset[str]
    allowed_environments: frozenset[str]
    max_cash_usd: str


class ScopedOfflineGateway:
    def __init__(self, control: OfflineControlPlane, policy: OfflinePolicy):
        self.control = control
        self.policy = policy

    def claim(self, permit: Permit, **verification_evidence: object) -> Outcome:
        if not isinstance(self.policy, OfflinePolicy):
            return Outcome(False, "missing_policy")
        preflight = check_permit_shape(
            permit, allowed_accounts=self.policy.allowed_accounts,
            allowed_regions=self.policy.allowed_regions,
            allowed_actions=self.policy.allowed_actions,
            allowed_environments=self.policy.allowed_environments,
            max_cash_usd=self.policy.max_cash_usd,
        )
        if not preflight.acceptable:
            return Outcome(False, "preflight_" + ",".join(preflight.failures))
        return self.control.claim(permit, policy=self.policy, **verification_evidence)
