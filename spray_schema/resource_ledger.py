"""Self-contained resource-ledger schema and validator for SPRAY.

This module intentionally has NO external dependencies and does NOT import
from any unmerged branch. It models SPRAY's funding reality as described in
the proposed docs/ARCHITECTURE.md "Resource ledger" section and the
docs/SPRAY_CONSTITUTION.md clauses "Limited seed resources; no fixed death
date", "Oversight and shutdown" and "Identity and truthfulness" (all of which
live only on the unmerged foundation branch). It is a standalone copy on
purpose: the offline CI gate must run before that branch merges.

Everything here is pure Python 3.11 standard library and fully offline.

Core principles encoded here:

* The four funding buckets are NON-FUNGIBLE and MUST NOT be netted together.
  Only ``settled_cash`` is spendable money; credits (AWS Activate, AWS Free
  Tier, Kiro subscription) are restricted and may expire.
* "A projected sale is not money." A revenue claim that is projected, forecast,
  simulated or merely pending MUST NOT be marked verified or counted as
  settled cash. Only an independently verified, settled claim qualifies.
* All paid work consumes a budget lease that EXPIRES and lives in an external,
  owner-controlled control plane. SPRAY cannot self-grant or self-un-revoke a
  lease, and an independent owner may revoke a lease at any time.
* Spend decisions FAIL CLOSED: unknown or missing inputs are never treated as
  permission, provider billing telemetry alone can never authorize spend, and
  a protective buffer plus contingent liabilities are always subtracted from
  usable budget. "Never rely on delayed AWS billing for exact hard cutoff."
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from decimal import Decimal
from numbers import Real
from typing import Any

# --- Bucket model ------------------------------------------------------------

# The four non-fungible funding buckets from docs/ARCHITECTURE.md.
BUCKET_KINDS = {
    "aws_activate_credit",
    "aws_free_tier_credit",
    "kiro_subscription_credit",
    "settled_cash",
}

# Funding class per bucket: credits are restricted and cannot be netted into
# spendable cash; only settled_cash is real, spendable money.
FUNDING_CLASS_CREDIT = "credit"
FUNDING_CLASS_CASH = "cash"

BUCKET_FUNDING_CLASS = {
    "aws_activate_credit": FUNDING_CLASS_CREDIT,
    "aws_free_tier_credit": FUNDING_CLASS_CREDIT,
    "kiro_subscription_credit": FUNDING_CLASS_CREDIT,
    "settled_cash": FUNDING_CLASS_CASH,
}

FUNDING_CLASSES = {FUNDING_CLASS_CREDIT, FUNDING_CLASS_CASH}

# --- Provenance / verification -----------------------------------------------

# Who reported a value: SPRAY self-report, the cloud provider, the independent
# TOPIA supervisor, or the owner. Kept consistent with event_envelope.py
# (which uses {"spray", "provider", "topia"}) and extended with "owner".
PROVENANCE_SOURCES = {"spray", "provider", "topia", "owner"}

# Independent (non-SPRAY) sources that may attest a settled cash claim.
INDEPENDENT_PROVENANCE = {"provider", "topia", "owner"}

VERIFICATION_STATUSES = {"unverified", "verified", "rejected"}

# --- Revenue / forecast separation -------------------------------------------

# Settlement states for a revenue/cash claim. Only "settled" is real money;
# the rest are projections, forecasts, simulations or in-flight and MUST NOT be
# verified or counted as settled cash. ("A projected sale is not money.")
SETTLEMENT_STATES = {"projected", "forecast", "simulated", "pending", "settled"}
UNSETTLED_STATES = {"projected", "forecast", "simulated", "pending"}

# --- Lease model -------------------------------------------------------------

# Authorities that may grant/revoke a budget lease. The external owner-
# controlled control plane and the owner are legitimate; SPRAY is never a valid
# lease authority (it cannot self-grant or self-un-revoke).
LEASE_AUTHORITIES = {"external_control_plane", "owner"}

# Required fields for each bucket entry.
BUCKET_REQUIRED_FIELDS = (
    "bucket_kind",
    "balance_usd",
    "currency",
    "funding_class",
    "provenance",
    "verification",
    "provider_update_time",
    "eligibility",
    "expiry",
    "confidence",
)

# Required fields for each lease entry.
LEASE_REQUIRED_FIELDS = (
    "lease_id",
    "bucket_kind",
    "max_spend_usd",
    "granted_at",
    "expires_at",
    "authority",
    "provenance",
    "revoked",
)


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


def _parse_utc_iso(value: Any) -> datetime | None:
    """Parse a normalized UTC ISO-8601 timestamp, or return None.

    The ledger normalizes every timestamp to a trailing ``Z`` UTC instant so
    that comparisons are unambiguous. This helper REQUIRES that shape: a value
    that is not a string, does not end in ``Z``, carries an explicit numeric
    offset, or does not parse is rejected (returns None). It never raises.

    Returning None lets every caller fail closed on an unparseable or
    non-normalized timestamp instead of trusting a value it cannot order.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or not text.endswith("Z"):
        return None
    # Reject anything carrying an additional explicit offset before the Z.
    body = text[:-1]
    if "+" in body or (body.count("-") > 2):
        return None
    try:
        # fromisoformat accepts the UTC designator form (Python 3.11+).
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_real_number(value: Any) -> bool:
    """True only for genuine, orderable real numbers (not bool, not complex).

    ``bool`` is a subclass of ``int`` and must never count as a number here.
    ``complex`` is a ``numbers.Number`` but NOT a ``numbers.Real`` and is
    unordered, so it must be excluded before any ``value < 0`` comparison
    (which would otherwise raise ``TypeError``).

    ``decimal.Decimal`` is a ``numbers.Number`` but, by design, is NOT
    registered as a ``numbers.Real`` - yet it IS a legitimate, orderable real
    value we must accept (int/float/Fraction/Decimal all validate). So we admit
    anything that is ``numbers.Real`` OR a ``Decimal``, which includes every
    orderable real type and still cleanly excludes ``complex``.
    """
    if isinstance(value, bool):
        return False
    return isinstance(value, (Real, Decimal))


def _is_finite_real(value: Any) -> bool:
    """True only for finite real numbers (rejects NaN/Infinity for any type).

    The non-finite guard must cover ALL real types, not just ``float``.
    ``Decimal('NaN')`` / ``Decimal('Infinity')`` are ``numbers.Real`` but not
    ``float``, so they must be converted and checked explicitly. Converting via
    ``float(value)`` can raise for exotic/overflowing values, so every failure
    mode is caught and treated as "not finite" (fail closed).
    """
    if not _is_real_number(value):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, ArithmeticError):
        return False


def _check_non_negative_number(value: Any, field: str) -> list[str]:
    """Validate that ``value`` is a finite, non-negative, non-bool number.

    Returns a list of error strings (empty when valid). Mirrors the
    bool-as-Number / NaN / Infinity guards used in event_envelope.py.

    Hardened against non-JSON Python numeric edge cases: ``complex`` (a
    ``Number`` but not a ``Real``), ``Decimal('NaN')`` and ``Decimal('Infinity')``
    (``Real`` but not ``float``). None of these raise here; all are rejected.
    """
    errors: list[str] = []
    if not _is_real_number(value):
        errors.append(f"{field} must be a number, got {type(value).__name__}")
    elif not _is_finite_real(value):
        try:
            is_nan = math.isnan(float(value))
        except (TypeError, ValueError, ArithmeticError):
            is_nan = False
        if is_nan:
            errors.append(f"{field} must be a finite number, not NaN")
        else:
            errors.append(f"{field} must be a finite number, not Infinity")
    elif value < 0:
        errors.append(f"{field} must be >= 0")
    return errors


def _is_unsettled(settlement_state: Any) -> bool:
    """True if the claim is projected / forecast / simulated / pending."""
    return _is_allowed(settlement_state, UNSETTLED_STATES)


def validate_bucket(obj: Any) -> list[str]:
    """Validate a single funding-bucket entry.

    Returns a list of human-readable error strings. An empty list means the
    bucket is valid. Never raises for ordinary bad input.
    """
    errors: list[str] = []

    if not isinstance(obj, dict):
        return [f"bucket must be a JSON object (mapping), got {type(obj).__name__}"]

    for field in BUCKET_REQUIRED_FIELDS:
        if field not in obj:
            errors.append(f"missing required bucket field: {field}")

    bucket_kind = obj.get("bucket_kind")
    if "bucket_kind" in obj and not _is_allowed(bucket_kind, BUCKET_KINDS):
        errors.append(
            f"bucket_kind must be one of {sorted(BUCKET_KINDS)}, "
            f"got {bucket_kind!r}"
        )

    if "balance_usd" in obj:
        errors.extend(_check_non_negative_number(obj["balance_usd"], "balance_usd"))

    if "funding_class" in obj:
        funding_class = obj["funding_class"]
        if not _is_allowed(funding_class, FUNDING_CLASSES):
            errors.append(
                f"funding_class must be one of {sorted(FUNDING_CLASSES)}, "
                f"got {funding_class!r}"
            )
        # Funding class must match the canonical class for the bucket kind so
        # credits can never masquerade as cash (or vice versa).
        if _is_allowed(bucket_kind, BUCKET_KINDS):
            expected = BUCKET_FUNDING_CLASS[bucket_kind]
            if funding_class != expected:
                errors.append(
                    f"funding_class for {bucket_kind!r} must be {expected!r}, "
                    f"got {funding_class!r}"
                )

    if "provenance" in obj:
        provenance = obj["provenance"]
        if not _is_allowed(provenance, PROVENANCE_SOURCES):
            errors.append(
                f"provenance must be one of {sorted(PROVENANCE_SOURCES)}, "
                f"got {provenance!r}"
            )

    if "verification" in obj:
        verification = obj["verification"]
        if not _is_allowed(verification, VERIFICATION_STATUSES):
            errors.append(
                "verification must be one of "
                f"{sorted(VERIFICATION_STATUSES)}, got {verification!r}"
            )

    if "eligibility" in obj:
        eligibility = obj["eligibility"]
        if not isinstance(eligibility, (list, dict)):
            errors.append(
                "eligibility must be a list or object, got "
                f"{type(eligibility).__name__}"
            )

    # expiry: settled_cash may carry null (cash does not expire); credits must
    # carry a non-empty ISO-8601 string expiry.
    if "expiry" in obj:
        expiry = obj["expiry"]
        if bucket_kind == "settled_cash":
            if expiry is not None and not isinstance(expiry, str):
                errors.append(
                    "expiry for settled_cash must be null or an ISO-8601 "
                    f"string, got {type(expiry).__name__}"
                )
        elif _is_allowed(bucket_kind, BUCKET_KINDS):
            # A credit bucket.
            if not isinstance(expiry, str) or not expiry.strip():
                errors.append(
                    f"expiry for credit bucket {bucket_kind!r} must be a "
                    "non-empty ISO-8601 string (credits expire)"
                )

    if "provider_update_time" in obj:
        put = obj["provider_update_time"]
        if not isinstance(put, str) or not put.strip():
            errors.append(
                "provider_update_time must be a non-empty ISO-8601 string"
            )
        elif _parse_utc_iso(put) is None:
            # Align validation with balance_is_fresh/_parse_utc_iso: a stored
            # provider_update_time must be a normalized UTC 'Z' instant, so a
            # bucket cannot validate yet be permanently judged UNKNOWN by the
            # freshness guard. Same rule validate_lease applies to lease
            # timestamps.
            errors.append(
                "provider_update_time must be a normalized UTC ISO-8601 string "
                "ending in 'Z' (e.g. 2026-10-13T18:00:00Z)"
            )

    if "confidence" in obj:
        confidence = obj["confidence"]
        conf_errors = _check_non_negative_number(confidence, "confidence")
        if conf_errors:
            errors.extend(conf_errors)
        elif confidence > 1:
            errors.append("confidence must be between 0 and 1")

    # Revenue/forecast separation. A settlement_state field is optional, but
    # when present it governs whether this entry may be verified settled cash.
    settlement_state = obj.get("settlement_state")
    if settlement_state is not None and not _is_allowed(
        settlement_state, SETTLEMENT_STATES
    ):
        errors.append(
            f"settlement_state must be one of {sorted(SETTLEMENT_STATES)}, "
            f"got {settlement_state!r}"
        )

    errors.extend(_check_settled_cash_integrity(obj))

    return errors


def _check_settled_cash_integrity(obj: dict) -> list[str]:
    """Enforce 'a projected sale is not money'.

    An entry that is projected / forecast / simulated / pending MUST NOT be
    verified and MUST NOT sit in the settled_cash bucket. Only a settled claim
    with independent provenance and verified status may be settled cash.
    """
    errors: list[str] = []
    settlement_state = obj.get("settlement_state")
    if settlement_state is None:
        return errors

    verification = obj.get("verification")
    bucket_kind = obj.get("bucket_kind")
    is_cash_bucket = bucket_kind == "settled_cash"

    # The wording is bucket-aware: "settled cash" is only accurate for the
    # cash bucket. For a credit bucket (where "settled cash" would be a
    # misnomer) we say the projected claim cannot be verified instead. The
    # fail-closed behavior is identical in both cases.
    def _unverified_projection_error() -> str:
        if is_cash_bucket:
            return "projected revenue cannot be verified settled cash"
        return (
            "projected revenue cannot be verified "
            f"(bucket {bucket_kind!r} is not settled cash)"
        )

    unsettled = _is_unsettled(settlement_state)

    # A projection cannot be verified settled cash. This single branch covers
    # every unsettled case (verified claim, or anything sitting in the
    # settled_cash bucket) so the message is emitted AT MOST ONCE per entry
    # rather than once per overlapping condition.
    if unsettled and (verification == "verified" or is_cash_bucket):
        errors.append(_unverified_projection_error())
    elif is_cash_bucket and verification == "verified":
        # The entry claims to be settled cash and is already settled; it must
        # still clear the independence bar.
        if not _is_allowed(obj.get("provenance"), INDEPENDENT_PROVENANCE):
            errors.append(
                "settled_cash must have independent provenance "
                f"(one of {sorted(INDEPENDENT_PROVENANCE)}), not self-reported"
            )

    return errors


def is_settled_cash_claim(obj: Any) -> bool:
    """True only for an independently verified, settled cash claim.

    This is the single gate that distinguishes real spendable money from a
    projection. Everything else ('a projected sale is not money') returns
    False, including anything SPRAY self-reports.
    """
    if not isinstance(obj, dict):
        return False
    return (
        obj.get("bucket_kind") == "settled_cash"
        and obj.get("funding_class") == FUNDING_CLASS_CASH
        and obj.get("settlement_state") == "settled"
        and obj.get("verification") == "verified"
        and _is_allowed(obj.get("provenance"), INDEPENDENT_PROVENANCE)
    )


# --- Balance freshness -------------------------------------------------------

# Default maximum age, in seconds, before a provider-reported balance is
# considered stale and therefore UNKNOWN. Billing telemetry lags reality, so a
# balance that has not been refreshed recently cannot be trusted as current.
DEFAULT_BALANCE_MAX_AGE_SECONDS = 24 * 60 * 60  # 24 hours


def balance_is_fresh(
    obj: Any,
    now_iso: Any,
    max_age_seconds: Any = DEFAULT_BALANCE_MAX_AGE_SECONDS,
) -> bool:
    """Return True only if a bucket's ``provider_update_time`` is recent.

    Separates asserted truth from verified truth over TIME: a provider balance
    is a self-report with billing-update latency, so a ``provider_update_time``
    that is older than ``max_age_seconds`` (or in the future, unparseable, or
    not a normalized UTC 'Z' string) is treated as UNKNOWN and this returns
    False. ``now_iso`` must also be a normalized UTC 'Z' string. Any malformed
    input fails closed (returns False). This never raises.

    This is the in-module freshness guard the design doc references: a stale or
    unknown balance timestamp must not be trusted as current.
    """
    if not isinstance(obj, dict):
        return False

    now = _parse_utc_iso(now_iso)
    put = _parse_utc_iso(obj.get("provider_update_time"))
    if now is None or put is None:
        return False

    max_age = _usable_number(max_age_seconds)
    if max_age is None:
        return False

    age_seconds = (now - put).total_seconds()
    # A future timestamp (negative age) is as untrustworthy as a stale one.
    if age_seconds < 0:
        return False
    return age_seconds <= max_age


# --- Lease validation --------------------------------------------------------


def validate_lease(obj: Any) -> list[str]:
    """Validate a single budget-lease entry.

    Returns a list of human-readable error strings. Never raises for ordinary
    bad input.
    """
    errors: list[str] = []

    if not isinstance(obj, dict):
        return [f"lease must be a JSON object (mapping), got {type(obj).__name__}"]

    for field in LEASE_REQUIRED_FIELDS:
        if field not in obj:
            errors.append(f"missing required lease field: {field}")

    if "lease_id" in obj and (
        not isinstance(obj["lease_id"], str) or not obj["lease_id"].strip()
    ):
        errors.append("lease_id must be a non-empty string")

    bucket_kind = obj.get("bucket_kind")
    if "bucket_kind" in obj and not _is_allowed(bucket_kind, BUCKET_KINDS):
        errors.append(
            f"bucket_kind must be one of {sorted(BUCKET_KINDS)}, "
            f"got {bucket_kind!r}"
        )

    if "max_spend_usd" in obj:
        errors.extend(
            _check_non_negative_number(obj["max_spend_usd"], "max_spend_usd")
        )

    for ts_field in ("granted_at", "expires_at"):
        if ts_field in obj:
            value = obj[ts_field]
            if not isinstance(value, str) or not value.strip():
                errors.append(
                    f"{ts_field} must be a non-empty ISO-8601 string"
                )
            elif _parse_utc_iso(value) is None:
                # Timestamps must be normalized UTC 'Z' instants so expiry
                # comparisons in lease_is_active cannot be fooled by a mixed
                # offset or precision.
                errors.append(
                    f"{ts_field} must be a normalized UTC ISO-8601 string "
                    "ending in 'Z' (e.g. 2026-10-14T06:00:00Z)"
                )

    # Structural validity interval: a lease's expiry must be strictly after
    # its grant. This is a clock-free check (no now_iso needed), so it belongs
    # in validate_lease rather than lease_is_active. Compared as INSTANTS via
    # _parse_utc_iso, never lexicographically. Only emitted when BOTH
    # timestamps are present and parse as normalized UTC 'Z' instants, so we do
    # not pile a confusing interval error on top of an already-reported
    # non-'Z'/unparseable timestamp error. An inverted (expires_at < granted_at)
    # or zero-length (expires_at == granted_at) interval is rejected: such a
    # lease could never be active and a future-dated/garbled interval must not
    # slip through.
    granted = _parse_utc_iso(obj.get("granted_at"))
    expires = _parse_utc_iso(obj.get("expires_at"))
    if granted is not None and expires is not None and expires <= granted:
        errors.append(
            "expires_at must be strictly after granted_at "
            "(invalid or zero-length lease validity interval)"
        )

    # Optional cumulative consumption: when a lease carries a consumed_usd
    # figure (maintained by the EXTERNAL control plane, see Section 4 of
    # docs/RESOURCE_LEDGER.md), it must be a finite, non-negative number.
    # Absent consumed_usd is NO LONGER treated as 0.0 downstream: the headroom
    # helpers now FAIL CLOSED on it (unknown consumption never authorizes
    # spend). An explicitly present but invalid value is still rejected here.
    if "consumed_usd" in obj:
        errors.extend(
            _check_non_negative_number(obj["consumed_usd"], "consumed_usd")
        )

    # Independent-verification gate for consumed-to-date. A consumed_usd figure
    # only counts toward authorization when it carries independent provenance
    # (one of INDEPENDENT_PROVENANCE), consistent with the settled-cash and
    # revoked_by models. The attestation field is OPTIONAL at the structural
    # level: a bare consumed_usd without it is NOT a hard validate_lease error,
    # it is simply not-independently-verified and so fails closed in the
    # headroom path (lease_remaining_headroom_usd returns None). But when the
    # field IS present it must be a recognized provenance source and must not
    # be the self-report 'spray' (SPRAY cannot attest its own consumption).
    if "consumed_usd_provenance" in obj:
        cons_prov = obj["consumed_usd_provenance"]
        if not _is_allowed(cons_prov, PROVENANCE_SOURCES):
            errors.append(
                "consumed_usd_provenance must be one of "
                f"{sorted(PROVENANCE_SOURCES)}, got {cons_prov!r}"
            )
        elif cons_prov == "spray":
            errors.append(
                "consumed_usd_provenance must be independently verified "
                f"(one of {sorted(INDEPENDENT_PROVENANCE)}), not self-reported "
                "'spray'"
            )

    # Authority and provenance must be external/owner-controlled. SPRAY can
    # never grant or revoke its own leases.
    authority = obj.get("authority")
    if "authority" in obj and not _is_allowed(authority, LEASE_AUTHORITIES):
        errors.append(
            f"lease authority must be one of {sorted(LEASE_AUTHORITIES)}, "
            f"got {authority!r} (SPRAY cannot self-grant a lease)"
        )

    provenance = obj.get("provenance")
    if "provenance" in obj:
        if not _is_allowed(provenance, PROVENANCE_SOURCES):
            errors.append(
                f"provenance must be one of {sorted(PROVENANCE_SOURCES)}, "
                f"got {provenance!r}"
            )
        elif provenance == "spray":
            errors.append(
                "lease provenance must be owner-controlled "
                "(not 'spray'): SPRAY cannot self-grant or self-un-revoke a lease"
            )

    if "revoked" in obj and not isinstance(obj["revoked"], bool):
        errors.append(
            f"revoked must be a boolean, got {type(obj['revoked']).__name__}"
        )

    # A revoked lease must record who revoked it, and that authority must not
    # be SPRAY (SPRAY cannot un-revoke by claiming the revocation).
    if obj.get("revoked") is True:
        revoked_by = obj.get("revoked_by")
        if not _is_allowed(revoked_by, INDEPENDENT_PROVENANCE):
            errors.append(
                "revoked lease must record revoked_by as an independent "
                f"authority (one of {sorted(INDEPENDENT_PROVENANCE)})"
            )

    return errors


def lease_is_active(lease: Any, now_iso: Any) -> bool:
    """Return True only if the lease is currently usable.

    A lease is usable only when it is a well-formed, owner-controlled lease
    that is NOT revoked and NOT expired at ``now_iso``. Any malformed input,
    unknown timestamp, or SPRAY-authored lease fails closed (returns False).

    Both ``now_iso`` and the lease's ``expires_at`` must be normalized UTC
    'Z' ISO-8601 strings. They are parsed into timezone-aware datetimes and
    compared as instants, so a non-'Z' offset or differing precision cannot
    silently flip an expiry decision; an unparseable or non-normalized
    timestamp fails closed.
    """
    if not isinstance(lease, dict):
        return False

    now = _parse_utc_iso(now_iso)
    if now is None:
        return False

    # Fail closed on any structural problem.
    if validate_lease(lease):
        return False

    if lease.get("revoked") is True:
        return False

    # A lease that has not yet started is INACTIVE, with the same fail-closed
    # treatment as an expired lease. "Not yet started" needs the clock, so it
    # lives here (not in validate_lease). validate_lease already requires
    # granted_at to be a normalized 'Z' string and is called above, so this
    # should parse for any lease that reaches this point; still fail closed if
    # it does not. Compared as INSTANTS, never lexicographically.
    granted = _parse_utc_iso(lease.get("granted_at"))
    if granted is None:
        return False
    if now < granted:
        return False

    expires = _parse_utc_iso(lease.get("expires_at"))
    if expires is None:
        return False

    # Expired if now is at or past the expiry instant.
    if now >= expires:
        return False

    return True


def is_no_spend_lease(lease: Any) -> bool:
    """True if the lease authorizes zero spend (``max_spend_usd == 0``)."""
    if not isinstance(lease, dict):
        return False
    max_spend = lease.get("max_spend_usd")
    # Reject bool, non-real (e.g. complex), and NaN/Inf (e.g. Decimal('NaN')/
    # Decimal('Infinity')) before the equality check so a malformed value fails
    # closed (not a no-spend lease) rather than raising.
    if not _is_finite_real(max_spend):
        return False
    return max_spend == 0


def _independently_verified_consumed(lease: dict) -> Decimal | None:
    """Return a lease's independently-verified consumed-to-date, or None.

    A lease's own ``consumed_usd`` counts ONLY when it is a finite, non-negative
    number AND it carries a sibling ``consumed_usd_provenance`` that is one of
    INDEPENDENT_PROVENANCE ({provider, topia, owner}) - the same independence
    bar used for settled cash and for ``revoked_by``. A figure that is absent,
    invalid, self-reported ('spray'), or missing/with-unrecognized provenance
    is NOT independently verified and yields None (fail closed). An attested
    ``0.0`` with independent provenance is a valid "zero consumed so far"
    figure; zero only counts when explicitly attested, never as an absent
    default.
    """
    if "consumed_usd" not in lease:
        return None
    consumed = _exact_nonnegative_decimal(lease.get("consumed_usd"))
    if consumed is None:
        return None
    if not _is_allowed(lease.get("consumed_usd_provenance"), INDEPENDENT_PROVENANCE):
        return None
    return consumed


def lease_remaining_headroom_usd(lease: Any, consumed_usd: Any = None) -> float | None:
    """Return a lease's REMAINING authorization headroom, or None.

    Remaining headroom is ``max_spend_usd - consumed_to_date``: the slice of a
    lease's authorization cap that has not yet been spent. This represents the
    cumulative consumed-vs-cap figures the EXTERNAL control plane maintains (see
    the atomic external accounting contract in Section 4 of
    docs/RESOURCE_LEDGER.md). SPRAY does NOT enforce or self-debit this; the
    helper is only a representation plus a fail-closed check of the externally
    maintained numbers.

    ``consumed_usd`` is resolved as follows: an explicit argument wins; else the
    lease's own ``consumed_usd`` field is used ONLY WHEN it is independently
    verified (a finite, non-negative figure carrying a
    ``consumed_usd_provenance`` in INDEPENDENT_PROVENANCE); else the consumed
    figure is UNKNOWN and this FAILS CLOSED (returns ``None``). Absent/unknown
    consumption is never treated as 0.0 consumed: a lease whose consumed-to-date
    cannot be independently established authorizes no further spend.

    Fails closed (returns ``None``) when ``max_spend_usd`` is unusable, when an
    explicitly present ``consumed_usd`` argument is unknown/invalid (non-numeric,
    bool, NaN, Inf, negative), or when no independently-verified consumed figure
    can be established. Over-consumption (consumed >= cap) is NEVER negative
    headroom: it clamps to ``0.0`` (a fully consumed cap authorizes no further
    spend). This matches the UNKNOWN-is-never-PASS style everywhere else in this
    module.
    """
    if not isinstance(lease, dict):
        return None

    cap = _exact_nonnegative_decimal(lease.get("max_spend_usd"))
    if cap is None:
        return None

    if consumed_usd is None:
        # Require independent provenance for the externally reported debit.
        consumed = _exact_nonnegative_decimal(
            _independently_verified_consumed(lease)
        )
    else:
        consumed = _exact_nonnegative_decimal(consumed_usd)
        # An override may increase accounted consumption, never erase a
        # higher independently verified debit recorded by the owner/provider.
        verified = _independently_verified_consumed(lease)
        if verified is not None and consumed is not None and consumed < verified:
            return None

    if consumed is None:
        return None

    # A sufficiently wide local decimal context prevents sub-cent debits
    # disappearing under the caller's default Decimal precision.
    from decimal import localcontext
    with localcontext() as context:
        context.prec = max(
            80,
            len(cap.as_tuple().digits) + abs(cap.as_tuple().exponent) + 8,
            len(consumed.as_tuple().digits) + abs(consumed.as_tuple().exponent) + 8,
        )
        remaining = cap - consumed
    if remaining <= 0:
        return 0.0
    # Keep the public float return type, but never round tiny positive
    # headroom UP to an authorization. Values that cannot be represented
    # exactly by float fail closed.
    try:
        approximate = float(remaining)
        if not math.isfinite(approximate):
            return None
        if Decimal.from_float(approximate) > remaining:
            approximate = math.nextafter(approximate, 0.0)
        return approximate
    except (OverflowError, ValueError, ArithmeticError):
        return None


def _active_class_lease_headrooms(
    leases: Any,
    now_iso: Any,
    funding_class: Any,
) -> list[tuple[str | None, float]] | None:
    """Return (lease_id, remaining_headroom) for each counted active lease.

    Shared, fail-closed core used by both the single-covering-lease
    authorization path and the explicit atomic-allocation path. A lease is
    counted only when it is active at ``now_iso`` (via ``lease_is_active``) and
    its ``bucket_kind`` maps (via ``BUCKET_FUNDING_CLASS``) to the requested
    ``funding_class`` - preserving non-fungibility, so a credit-bucket lease
    contributes nothing to a cash authorization and an unknown bucket_kind maps
    to no class and is skipped.

    Each counted lease contributes its REMAINING headroom (cap minus
    independently-verified consumed-to-date via ``lease_remaining_headroom_usd``),
    never its raw cap. Fails closed (returns ``None``) when the lease list is
    malformed, ``now_iso`` is unknown/non-normalized, or any counted active
    lease has ``None`` remaining headroom (unusable cap, or unknown/unverified
    consumed-to-date). Never raises.
    """
    if not isinstance(leases, list):
        return None
    # An unknown/non-normalized clock cannot be used to judge activity.
    if _parse_utc_iso(now_iso) is None:
        return None

    # Reject duplicate identifiers across the entire lease collection before
    # selecting active leases. Otherwise one authorization can be counted twice.
    seen_lease_ids: set[str] = set()
    for lease in leases:
        if not isinstance(lease, dict):
            return None
        lease_id = lease.get("lease_id")
        if not isinstance(lease_id, str) or not lease_id.strip():
            return None
        if lease_id in seen_lease_ids:
            return None
        seen_lease_ids.add(lease_id)

    contributions: list[tuple[str | None, float]] = []
    for lease in leases:
        if not lease_is_active(lease, now_iso):
            continue
        # Non-fungibility: only count leases of the requested funding class
        # toward this authorization limit. A credit-bucket lease must not
        # authorize cash spend. An unknown kind maps to None and is skipped.
        if not isinstance(lease, dict):
            continue
        lease_class = BUCKET_FUNDING_CLASS.get(lease.get("bucket_kind"))
        if lease_class != funding_class:
            continue
        # Count REMAINING headroom (cap minus consumed-to-date), not raw cap,
        # so a fully consumed lease contributes zero.
        remaining = lease_remaining_headroom_usd(lease)
        if remaining is None:
            # Fail closed on any counted active lease we cannot read.
            return None
        contributions.append((lease.get("lease_id"), remaining))
    return contributions


def single_lease_authorization_usd(
    leases: Any,
    now_iso: Any,
    funding_class: Any = FUNDING_CLASS_CASH,
) -> float | None:
    """Return the MAX single-lease remaining headroom active at ``now_iso``.

    This is the DEFAULT, recommended lease-authorization figure to pass into
    ``spend_is_permitted(active_lease_headroom_usd=...)``. It returns the
    greatest REMAINING headroom of any single active, non-revoked,
    non-expired, correct-funding-class lease (via
    ``lease_remaining_headroom_usd``), NOT the sum across leases. A plain spend
    is therefore authorized only when ONE lease by itself covers the amount:
    independent lease headrooms are NOT silently combined to clear a spend that
    no single lease covers.

    Combining multiple leases to authorize one spend is permitted ONLY under
    the explicit atomic allocation/reservation contract (see
    ``allocate_lease_reservation`` and Section 4 of
    docs/RESOURCE_LEDGER.md), which names and reserves/debits the specific
    contributing leases. SPRAY does not enforce or self-debit either way.

    Fails closed (returns ``None``) when the lease list is malformed,
    ``now_iso`` is unknown/non-normalized, or any counted active lease has an
    unusable ``max_spend_usd`` or an unknown/unverified consumed-to-date. When
    no active correct-funding-class lease exists, returns ``0.0`` (no
    authorization), so a positive spend is denied.
    """
    contributions = _active_class_lease_headrooms(leases, now_iso, funding_class)
    if contributions is None:
        return None
    if not contributions:
        return 0.0
    return max(headroom for _lease_id, headroom in contributions)


def active_lease_headroom_usd(
    leases: Any,
    now_iso: Any,
    funding_class: Any = FUNDING_CLASS_CASH,
    combine_across_leases: bool = False,
) -> float | None:
    """Return the active cash-lease authorization figure, or None.

    This closes the loop between "is this lease usable?" (`lease_is_active`)
    and "is this spend permitted?" (`spend_is_permitted`): headroom is derived
    only from leases that pass `lease_is_active(now_iso)`, so a revoked,
    expired, or not-yet-started lease contributes nothing.

    **DEFAULT (single-covering-lease).** By default (``combine_across_leases``
    is ``False``) this returns the MAXIMUM single-lease remaining headroom (same
    as ``single_lease_authorization_usd``), NOT the sum across leases. A plain
    spend is therefore authorized only when ONE lease by itself covers the
    amount; independent lease headrooms are NOT silently combined to clear a
    spend that no single lease covers.

    **EXPLICIT opt-in (atomic allocation).** When ``combine_across_leases`` is
    ``True`` this sums REMAINING headroom across the counted active leases. This
    is ONLY correct under the explicit atomic allocation/reservation contract
    (see ``allocate_lease_reservation`` and Section 4 of
    docs/RESOURCE_LEDGER.md), where the external, owner-controlled control plane
    identifies and atomically reserves/debits the specific contributing leases
    under a per-request idempotency key. SPRAY does not enforce or self-debit;
    the sum is a representation of what that external reservation would cover.

    Two correctness properties beyond plain activity apply in both modes:

    * **Funding-class binding (non-fungibility).** Only leases whose
      ``bucket_kind`` maps to the requested ``funding_class`` are counted. The
      default is ``cash`` (``settled_cash`` is the only cash bucket), so a lease
      on a credit bucket (aws_activate_credit, aws_free_tier_credit,
      kiro_subscription_credit) contributes ZERO to the cash-spend
      authorization limit. A credit lease can never authorize settled-cash
      spend. An unexpected/unknown bucket_kind maps to no funding class and so
      also contributes zero (fail closed), never raises.
    * **Cumulative consumption.** Each counted lease contributes its REMAINING
      headroom (``max_spend_usd`` minus consumed-to-date via
      ``lease_remaining_headroom_usd``), not its raw cap. A lease whose
      consumed_usd has reached its cap contributes zero, so the same unchanged
      cap cannot re-authorize repeated spends beyond it.

    Fails closed (returns ``None``) when the lease list is malformed,
    ``now_iso`` is unknown/non-normalized, or any counted active lease has an
    unusable ``max_spend_usd`` or an unknown/unverified consumed-to-date. Pass
    the result straight into ``spend_is_permitted(active_lease_headroom_usd=...)``.
    """
    contributions = _active_class_lease_headrooms(leases, now_iso, funding_class)
    if contributions is None:
        return None
    if not combine_across_leases:
        # DEFAULT: a single lease must cover the spend; do NOT sum independent
        # lease headrooms together.
        if not contributions:
            return 0.0
        return max(headroom for _lease_id, headroom in contributions)
    # EXPLICIT opt-in only: sum under the atomic allocation/reservation
    # contract, which identifies and reserves/debits the named leases.
    return sum((headroom for _lease_id, headroom in contributions), 0.0)


def allocate_lease_reservation(
    leases: Any,
    amount_usd: Any,
    now_iso: Any,
    funding_class: Any = FUNDING_CLASS_CASH,
) -> dict | None:
    """Represent an EXPLICIT atomic reservation across named leases, or None.

    This is the ONLY sanctioned way multiple leases may JOINTLY authorize a
    single spend that no one lease covers by itself. It is an explicit opt-in,
    layered on the existing external, owner-controlled control plane: when the
    combined remaining headroom of the active, correct-funding-class leases can
    cover ``amount_usd``, it selects specific contributing leases and returns a
    reservation that NAMES them and the per-lease reserved/debited amount.

    **SPRAY does NOT enforce or self-debit.** The returned reservation is only a
    REPRESENTATION of what the external control plane would atomically perform:
    identify the contributing leases and reserve/debit each one under a
    per-request idempotency key (see Section 4 of docs/RESOURCE_LEDGER.md). It
    is a proposal, not an enforced debit by SPRAY. The caller/control plane is
    responsible for carrying the idempotency key and performing the atomic debit.

    On success returns a dict::

        {
            "covered": True,
            "amount_usd": <float requested>,
            "reserved_usd": <float total reserved, == amount_usd>,
            "allocations": [(lease_id, reserved_usd), ...],  # named leases
        }

    Fails CLOSED by returning ``None`` when the clock (``now_iso``) is
    unknown/non-normalized, the lease list is malformed, any candidate active
    lease has ``None`` remaining headroom (unusable cap or unknown/unverified
    consumed-to-date), ``amount_usd`` is unknown/invalid, or the leases cannot
    JOINTLY cover ``amount_usd``. A not-covered request never returns a partial
    reservation.
    """
    amount_exact = _exact_nonnegative_decimal(amount_usd)
    if amount_exact is None:
        return None
    amount = _usable_number(amount_usd)
    if amount is None or Decimal.from_float(amount) < amount_exact:
        # Float would understate the requested reservation.
        return None

    # A generic credit class cannot safely authorize a reservation across
    # non-fungible restricted credit buckets. Read-only class headroom remains
    # available for reporting; authorization fails closed.
    if funding_class == FUNDING_CLASS_CREDIT:
        return None

    contributions = _active_class_lease_headrooms(leases, now_iso, funding_class)
    if contributions is None:
        # Fail closed on an unknown clock, malformed lease list, or any
        # candidate active lease we cannot read.
        return None

    # Deterministic selection: greedily draw from the leases with the most
    # remaining headroom first, naming each contributing lease and the amount
    # reserved/debited against it, until the requested amount is covered.
    ordered = sorted(contributions, key=lambda item: item[1], reverse=True)

    allocations: list[tuple[str | None, float]] = []
    remaining_needed = amount
    for lease_id, headroom in ordered:
        if remaining_needed <= 0:
            break
        if headroom <= 0:
            continue
        take = headroom if headroom < remaining_needed else remaining_needed
        allocations.append((lease_id, take))
        remaining_needed -= take

    if remaining_needed > 0:
        # The leases cannot jointly cover the amount: fail closed, no partial
        # reservation is returned.
        return None

    reserved_total = sum((take for _lease_id, take in allocations), 0.0)
    return {
        "covered": True,
        "amount_usd": amount,
        "reserved_usd": reserved_total,
        "allocations": allocations,
    }


# --- Ledger validation -------------------------------------------------------


def validate_ledger(obj: Any) -> list[str]:
    """Validate a full resource ledger object.

    Returns a list of human-readable error strings. An empty list means the
    ledger is valid. The function never raises for ordinary bad input; it
    reports problems as strings instead.
    """
    errors: list[str] = []

    if not isinstance(obj, dict):
        return [f"ledger must be a JSON object (mapping), got {type(obj).__name__}"]

    buckets = obj.get("buckets")
    if "buckets" not in obj:
        errors.append("missing required field: buckets")
    elif not isinstance(buckets, list):
        errors.append(f"buckets must be a list, got {type(buckets).__name__}")
    else:
        for index, bucket in enumerate(buckets):
            for err in validate_bucket(bucket):
                errors.append(f"buckets[{index}]: {err}")

    leases = obj.get("leases")
    if leases is not None:
        if not isinstance(leases, list):
            errors.append(f"leases must be a list, got {type(leases).__name__}")
        else:
            seen_lease_ids: set[str] = set()
            for index, lease in enumerate(leases):
                for err in validate_lease(lease):
                    errors.append(f"leases[{index}]: {err}")
                if isinstance(lease, dict):
                    lease_id = lease.get("lease_id")
                    if isinstance(lease_id, str) and lease_id.strip():
                        if lease_id in seen_lease_ids:
                            errors.append(
                                f"leases[{index}]: duplicate lease_id: {lease_id}"
                            )
                        seen_lease_ids.add(lease_id)

    # Optional top-level fail-closed inputs; validate when present.
    for num_field in (
        "burn_rate_usd_per_day",
        "contingent_liabilities_usd",
        "protective_buffer_usd",
    ):
        if num_field in obj:
            errors.extend(_check_non_negative_number(obj[num_field], num_field))

    if "forecast_confidence" in obj:
        fc = obj["forecast_confidence"]
        fc_errors = _check_non_negative_number(fc, "forecast_confidence")
        if fc_errors:
            errors.extend(fc_errors)
        elif fc > 1:
            errors.append("forecast_confidence must be between 0 and 1")

    return errors


# --- Fail-closed decision helpers --------------------------------------------

# Sentinel marking an input SPRAY does not actually know. Treated as a hard
# "deny" by spend_is_permitted: unknown is never PASS.
UNKNOWN = "unknown"


def _exact_nonnegative_decimal(value: Any) -> Decimal | None:
    """Convert supported real numbers without a float round-trip."""
    if not _is_real_number(value):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (ValueError, ArithmeticError):
        return None


def _usable_number(value: Any) -> float | None:
    """Return a finite, non-negative float, or None if the value is unusable.

    Any unknown/missing/non-numeric/bool/NaN/Inf/negative value yields None so
    callers can fail closed rather than guess.
    """
    if value is None or value == UNKNOWN:
        return None
    # Reject bool, non-real (e.g. complex), NaN/Inf for any real type (e.g.
    # Decimal('NaN')/Decimal('Infinity')) BEFORE any ordering comparison so a
    # malformed value fails closed to None rather than raising.
    if not _is_finite_real(value):
        return None
    if value < 0:
        return None
    return float(value)


def usable_authorized_budget_usd(
    settled_cash_usd: Any,
    protective_buffer_usd: Any,
    contingent_liabilities_usd: Any,
) -> float | None:
    """Compute spendable budget, or None if any input is unknown.

    Usable budget = settled cash - protective buffer - contingent liabilities.
    Credits are deliberately excluded: they are non-fungible and are never
    netted into spendable cash. Returns None (not a number) whenever any input
    is unknown/missing so the caller fails closed.
    """
    cash = _usable_number(settled_cash_usd)
    buffer_ = _usable_number(protective_buffer_usd)
    liabilities = _usable_number(contingent_liabilities_usd)
    if cash is None or buffer_ is None or liabilities is None:
        return None
    return cash - buffer_ - liabilities


def cash_runway_days(
    settled_cash_usd: Any,
    burn_rate_usd_per_day: Any,
    protective_buffer_usd: Any = 0,
    contingent_liabilities_usd: Any = 0,
) -> float | None:
    """Estimate days of runway from usable cash at the given burn rate.

    Returns None if any input is unknown or if the burn rate is unknown/zero
    (runway is undefined). A non-positive usable budget yields 0.0 days.
    """
    usable = usable_authorized_budget_usd(
        settled_cash_usd, protective_buffer_usd, contingent_liabilities_usd
    )
    burn = _usable_number(burn_rate_usd_per_day)
    if usable is None or burn is None:
        return None
    if burn == 0:
        return None
    if usable <= 0:
        return 0.0
    return usable / burn


def spend_is_permitted(
    amount_usd: Any,
    settled_cash_usd: Any,
    protective_buffer_usd: Any,
    contingent_liabilities_usd: Any,
    active_lease_headroom_usd: Any = 0,
    rely_on_provider_billing_only: bool = False,
) -> tuple[bool, str]:
    """Fail-closed spend decision requiring BOTH funds and a valid lease.

    Returns ``(permitted, reason)``. A spend is permitted only when every one
    of the following independent conditions holds; otherwise it is DENIED:

    * ``rely_on_provider_billing_only`` is False (delayed billing can never be
      the hard cutoff);
    * the requested amount is a known, usable number;
    * every budget input is known (unknown is never a PASS);
    * **sufficient verified funds**: the requested amount is covered by the
      usable authorized budget (settled cash minus the protective buffer and
      contingent liabilities; credits are never counted); AND
    * **a valid active lease**: the requested amount is covered by the active
      lease authorization limit passed as ``active_lease_headroom_usd``. By
      DEFAULT this figure is the single-covering-lease authorization
      (``single_lease_authorization_usd`` / ``active_lease_headroom_usd`` with
      the default ``combine_across_leases=False``): the MAX remaining headroom
      of any ONE active, non-revoked, non-expired, cash-funding-class lease, NOT
      the sum across leases. Independent lease headrooms are NOT combined to
      clear a spend that no single lease covers; multiple leases may jointly
      authorize one spend ONLY under the explicit atomic allocation/reservation
      contract (``allocate_lease_reservation``). The lease cap remains an
      authorization cap, not a funding source.

    These two conditions are required INDEPENDENTLY. A lease limit is an
    authorization CAP, not a funding source: lease headroom is NEVER added to
    cash to manufacture spendable budget. Any positive spend with no active
    lease authorizing it (headroom below the amount, e.g. the default 0) is
    denied regardless of how much cash is on hand. Any unknown/invalid input
    fails closed.
    """
    if rely_on_provider_billing_only:
        return (
            False,
            "deny: cannot rely solely on delayed provider billing telemetry "
            "for a hard spend cutoff",
        )

    amount = _exact_nonnegative_decimal(amount_usd)
    if amount is None:
        return (False, "deny: requested spend amount is unknown or invalid")

    cash = _exact_nonnegative_decimal(settled_cash_usd)
    buffer_ = _exact_nonnegative_decimal(protective_buffer_usd)
    liabilities = _exact_nonnegative_decimal(contingent_liabilities_usd)
    if cash is None or buffer_ is None or liabilities is None:
        return (False, "deny: budget inputs are unknown or missing (fail closed)")

    headroom = _exact_nonnegative_decimal(active_lease_headroom_usd)
    if headroom is None:
        return (False, "deny: lease headroom is unknown or invalid (fail closed)")

    if amount > headroom:
        return (
            False,
            "deny: no active lease authorizes this spend; lease headroom "
            f"({headroom}) is insufficient for requested spend ({amount}) "
            "(lease limits are an authorization cap, not spendable funds)",
        )

    # Compare with an increased precision context to prevent rounding of
    # sub-cent liabilities or huge decimal amounts into an approval.
    from decimal import localcontext
    with localcontext() as context:
        context.prec = max(80, *(len(v.as_tuple().digits) + abs(v.as_tuple().exponent) + 8
                                 for v in (amount, cash, buffer_, liabilities)))
        required_cash = amount + buffer_ + liabilities
        if cash < required_cash:
            return (
                False,
                "deny: usable authorized budget is insufficient for requested "
                f"spend ({amount}); required cash ({required_cash}), verified cash ({cash})",
            )

    return (
        True,
        "allow: covered by both usable authorized budget and an active "
        "lease authorization",
    )
