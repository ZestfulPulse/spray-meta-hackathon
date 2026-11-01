"""Offline control-plane adversarial preflight, not an authorization source.

No cloud calls or dispatch. Report structural weaknesses of test fixtures
before any production boundary is designed.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from spray_schema.offline_g4_control_plane import Permit


@dataclass(frozen=True)
class Preflight:
    acceptable: bool
    failures: tuple[str, ...]


def check_permit_shape(permit: Permit, *, allowed_accounts: frozenset[str],
                       allowed_regions: frozenset[str],
                       allowed_actions: frozenset[str],
                       allowed_environments: frozenset[str],
                       max_cash_usd: str) -> Preflight:
    failures: list[str] = []
    if not isinstance(permit, Permit):
        return Preflight(False, ("invalid_permit",))
    for name, allowed in (
        ("account", allowed_accounts), ("region", allowed_regions),
        ("action", allowed_actions), ("environment", allowed_environments),
    ):
        if not isinstance(allowed, frozenset) or not allowed or getattr(permit, name) not in allowed:
            failures.append("scope_" + name)
    if not isinstance(permit.request_id, str) or not permit.request_id.strip():
        failures.append("request_id")
    if not isinstance(permit.nonce, str) or len(permit.nonce) < 16:
        failures.append("nonce_entropy_not_established")
    try:
        amount = Decimal(permit.ceiling_usd)
        cap = Decimal(max_cash_usd)
        if (not amount.is_finite() or not cap.is_finite()
                or amount <= 0 or cap <= 0 or amount > cap
                or amount.as_tuple().exponent < -2):
            failures.append("cash_ceiling")
    except (InvalidOperation, TypeError, AttributeError):
        failures.append("cash_ceiling")
    return Preflight(not failures, tuple(failures))
