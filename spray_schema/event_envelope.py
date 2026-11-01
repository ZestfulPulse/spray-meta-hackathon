"""Self-contained minimal event envelope schema for SPRAY.

This module intentionally has NO external dependencies and does NOT import
from any unmerged branch. It mirrors the field names and allowed values of
the proposed ``spray.event.v1`` schema (documented in docs/ARCHITECTURE.md on
the unmerged foundation branch) so the two stay conceptually consistent, but
it is a standalone copy on purpose: CI must run before that branch merges.

Everything here is pure Python 3.11 standard library.
"""

from __future__ import annotations

import math
from numbers import Number
from typing import Any

# Fields that must be present on every event envelope.
REQUIRED_FIELDS = (
    "schema_version",
    "event_id",
    "occurred_at",
    "ingested_at",
    "run_id",
    "kind",
    "claim_origin",
    "verification",
    "evidence_refs",
    "cost_estimate_usd",
    "policy_version",
    "summary",
)

# Allowed enumerated values, matching the proposed architecture schema.
ALLOWED_VERIFICATION_STATUSES = {"unverified", "verified", "rejected"}
ALLOWED_KINDS = {
    "decision",
    "action",
    "expense",
    "revenue",
    "skill",
    "publication",
    "incident",
}
ALLOWED_CLAIM_ORIGINS = {"spray", "provider", "topia"}


def _is_nan(value: Any) -> bool:
    """Return True if ``value`` is a NaN float or Decimal.

    Covers both ``float('nan')`` (via ``math.isnan``) and
    ``decimal.Decimal('NaN')`` (which is a ``numbers.Number`` but not a float,
    so the plain ``isinstance(value, float)`` guard misses it). ``Decimal``
    exposes ``.is_nan()``; any object that neither looks like a NaN nor
    supports the check is treated as not-NaN.
    """
    is_nan = getattr(value, "is_nan", None)
    if callable(is_nan):
        try:
            return bool(is_nan())
        except Exception:
            return False
    try:
        return math.isnan(value)
    except (TypeError, ValueError):
        return False


def _is_infinite(value: Any) -> bool:
    """Return True if ``value`` is +/-Infinity as a float or Decimal.

    Covers ``float('inf')`` (via ``math.isinf``) and
    ``decimal.Decimal('Infinity')`` (which exposes ``.is_infinite()`` and is a
    ``numbers.Number`` but not a float). Anything that cannot be classified is
    treated as not-infinite.
    """
    is_infinite = getattr(value, "is_infinite", None)
    if callable(is_infinite):
        try:
            return bool(is_infinite())
        except Exception:
            return False
    try:
        return math.isinf(value)
    except (TypeError, ValueError):
        return False


def _is_allowed(value: Any, allowed: set[str]) -> bool:
    """Return True if ``value`` is a member of ``allowed``.

    A plain ``value in allowed`` test raises ``TypeError`` when ``value`` is
    unhashable (e.g. a list or dict). This helper treats any unhashable or
    otherwise non-matching value as simply not allowed, so callers can report
    a validation error instead of raising.
    """
    try:
        return value in allowed
    except TypeError:
        return False


def validate_envelope(obj: Any) -> list[str]:
    """Validate an event envelope object.

    Returns a list of human-readable error strings. An empty list means the
    envelope is valid. The function never raises for ordinary bad input; it
    reports problems as strings instead.
    """
    errors: list[str] = []

    if not isinstance(obj, dict):
        return [f"envelope must be a JSON object (mapping), got {type(obj).__name__}"]

    # Required fields.
    for field in REQUIRED_FIELDS:
        if field not in obj:
            errors.append(f"missing required field: {field}")

    # verification status.
    if "verification" in obj:
        verification = obj["verification"]
        if not _is_allowed(verification, ALLOWED_VERIFICATION_STATUSES):
            errors.append(
                "verification must be one of "
                f"{sorted(ALLOWED_VERIFICATION_STATUSES)}, got {verification!r}"
            )

    # kind.
    if "kind" in obj:
        kind = obj["kind"]
        if not _is_allowed(kind, ALLOWED_KINDS):
            errors.append(
                f"kind must be one of {sorted(ALLOWED_KINDS)}, got {kind!r}"
            )

    # claim_origin.
    if "claim_origin" in obj:
        claim_origin = obj["claim_origin"]
        if not _is_allowed(claim_origin, ALLOWED_CLAIM_ORIGINS):
            errors.append(
                "claim_origin must be one of "
                f"{sorted(ALLOWED_CLAIM_ORIGINS)}, got {claim_origin!r}"
            )

    # evidence_refs must be a list.
    if "evidence_refs" in obj:
        if not isinstance(obj["evidence_refs"], list):
            errors.append(
                "evidence_refs must be a list, got "
                f"{type(obj['evidence_refs']).__name__}"
            )

    # cost_estimate_usd must be a non-negative, finite real number
    # (and not a bool). Beyond ordinary JSON numbers, Python objects such as
    # ``complex`` and ``decimal.Decimal`` are ``numbers.Number`` instances but
    # cannot be compared with ``<`` safely (complex raises TypeError;
    # Decimal('NaN') raises decimal.InvalidOperation), so they are handled
    # explicitly here to honor the "never raises for ordinary bad input"
    # contract.
    if "cost_estimate_usd" in obj:
        cost = obj["cost_estimate_usd"]
        if isinstance(cost, bool) or not isinstance(cost, Number):
            errors.append(
                "cost_estimate_usd must be a number, got "
                f"{type(cost).__name__}"
            )
        elif isinstance(cost, complex):
            # complex is a Number but is not an ordered/real value; ``< 0``
            # would raise TypeError. Report it rather than letting it blow up.
            errors.append(
                "cost_estimate_usd must be a finite number, got "
                f"{type(cost).__name__}"
            )
        elif _is_nan(cost):
            errors.append("cost_estimate_usd must be a finite number, not NaN")
        elif _is_infinite(cost):
            errors.append(
                "cost_estimate_usd must be a finite number, not Infinity"
            )
        elif cost < 0:
            errors.append("cost_estimate_usd must be >= 0")

    return errors
