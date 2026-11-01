"""Credit-only budget enforcement for Meta Model API calls (offline, stdlib).

This is a THIN ADDITIVE layer on top of ``spray_schema.resource_ledger``: it
COMPOSES that module's fail-closed primitives and NEVER modifies its behavior.
It implements the R2-S3 credit-bucket rules for Meta (Muse Spark) planner
calls:

* Credits only. The cash bucket is zero (``CASH_BUCKET_USD``). Any positive
  spend is proven un-fundable from cash via
  ``resource_ledger.spend_is_permitted`` (zero cash, zero lease headroom), so a
  positive worst-case cost can only ever be funded by remaining credit, never
  by cash. Credits are NON-FUNGIBLE and are tracked HERE (the ledger
  deliberately excludes credits from spendable cash).
* R2 per-call / per-round cap: ``R2_PER_CALL_CAP_USD`` (USD 20).
* Lifetime hard stop: ``LIFETIME_HARD_STOP_USD`` (USD 140).
* Credit expiry: ``CREDIT_EXPIRY_ISO`` (2026-11-05T02:00:00Z). Any call at or
  after that instant is refused; an unparseable ``now_iso`` is also refused.
* Refuse any call whose WORST-CASE cost could exceed remaining credit.

Everything fails closed: any unknown / missing / non-finite / negative input,
or an unparseable timestamp, DENIES. Money is handled with ``Decimal``.

Boundary semantics (documented and tested consistently)
-------------------------------------------------------
All caps are INCLUSIVE ceilings, compared with ``>`` (strictly greater than is
denied). A worst-case cost EQUAL to the per-call cap (exactly USD 20) is
ALLOWED; USD 20.01 is denied. A lifetime spent-plus-worst-case EQUAL to the
hard stop (exactly USD 140) is ALLOWED; one cent over is denied. Likewise a
worst-case cost EQUAL to remaining credit is ALLOWED.

The credit-expiry comparison is at-or-after (``>=``): a ``now_iso`` EQUAL to
2026-11-05T02:00:00Z is DENIED (the credits are expired at that instant), and
any later instant is denied; strictly before is allowed.
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable

from . import resource_ledger
from .resource_ledger import (
    _exact_nonnegative_decimal,
    _parse_utc_iso,
    spend_is_permitted,
)

# --- Credit-bucket constants -------------------------------------------------

# R2 per-call / per-round cap: no single Meta call (and no round's cumulative
# worst-case) may exceed this.
R2_PER_CALL_CAP_USD = Decimal("20")

# Lifetime hard stop across all Meta credit spend.
LIFETIME_HARD_STOP_USD = Decimal("140")

# Credits expire at this instant (normalized UTC 'Z'). Calls at or after this
# instant are refused.
CREDIT_EXPIRY_ISO = "2026-11-05T02:00:00Z"

# The cash bucket for Meta spend is zero: credits only, no cash path.
CASH_BUCKET_USD = Decimal("0")

# Parsed once; the ledger's own parser is the single source of truth for how a
# normalized UTC 'Z' instant is interpreted.
_CREDIT_EXPIRY_INSTANT = _parse_utc_iso(CREDIT_EXPIRY_ISO)


def remaining_credit_usd(lifetime_credit_spent_usd: Any) -> Decimal | None:
    """Return remaining credit headroom, or None if the input is unusable.

    Remaining credit = ``LIFETIME_HARD_STOP_USD - lifetime_credit_spent_usd``.
    Fails closed (returns None) when lifetime spent is unknown / missing /
    non-finite / negative, or already at/over the hard stop (no credit left).
    """
    spent = _exact_nonnegative_decimal(lifetime_credit_spent_usd)
    if spent is None:
        return None
    remaining = LIFETIME_HARD_STOP_USD - spent
    if remaining <= 0:
        return None
    return remaining


def authorize_meta_spend(
    *,
    worst_case_cost_usd: Any,
    lifetime_credit_spent_usd: Any,
    now_iso: Any,
    round_spent_usd: Any = 0,
) -> tuple[bool, str]:
    """Fail-closed authorizer for a single Meta credit spend.

    Returns ``(permitted, reason)``. A call is ALLOWED only when every check
    below passes; otherwise it is DENIED with a distinct reason. Any unknown /
    missing / non-finite / negative input, or an unparseable / non-normalized
    ``now_iso``, fails closed.

    Order of checks:

    1. Credit expiry: parse ``now_iso`` via ``resource_ledger._parse_utc_iso``;
       if unparseable OR at/after ``CREDIT_EXPIRY_ISO`` -> DENY "credit expired".
    2. Numeric validity: ``worst_case_cost_usd`` and ``round_spent_usd`` must be
       known, finite, non-negative numbers -> else DENY.
    3. Per-call / per-round R2 cap: worst-case over ``R2_PER_CALL_CAP_USD``, or
       round-spent + worst-case over the cap -> DENY "R2 cap exceeded".
    4. Lifetime hard stop: lifetime spent + worst-case over
       ``LIFETIME_HARD_STOP_USD`` -> DENY "lifetime hard stop".
    5. Remaining credit: worst-case over remaining credit -> DENY.
    6. Cash-zero proof: ``spend_is_permitted`` against zero cash / zero lease
       MUST refuse any positive spend; if it would ever permit, DENY. This
       proves the spend can never touch cash.
    """
    # (1) Credit expiry (also fails closed on unparseable now_iso).
    now = _parse_utc_iso(now_iso)
    if now is None:
        return (False, "deny: now_iso is unparseable or not a normalized UTC 'Z' instant")
    if _CREDIT_EXPIRY_INSTANT is None:
        # Defensive: the module constant must parse. Fail closed if it does not.
        return (False, "deny: credit expiry instant is misconfigured (fail closed)")
    if now >= _CREDIT_EXPIRY_INSTANT:
        return (False, f"deny: credit expired (now at/after {CREDIT_EXPIRY_ISO})")

    # (2) Numeric validity (fail closed on unknown/NaN/Inf/negative).
    worst_case = _exact_nonnegative_decimal(worst_case_cost_usd)
    if worst_case is None:
        return (False, "deny: worst-case cost is unknown or invalid (fail closed)")
    round_spent = _exact_nonnegative_decimal(round_spent_usd)
    if round_spent is None:
        return (False, "deny: round-spent-to-date is unknown or invalid (fail closed)")
    lifetime_spent = _exact_nonnegative_decimal(lifetime_credit_spent_usd)
    if lifetime_spent is None:
        return (False, "deny: lifetime-spent-to-date is unknown or invalid (fail closed)")

    # (3) Per-call / per-round R2 cap (inclusive ceiling, strictly-over denied).
    if worst_case > R2_PER_CALL_CAP_USD:
        return (
            False,
            f"deny: R2 per-call cap exceeded (worst-case {worst_case} > cap "
            f"{R2_PER_CALL_CAP_USD})",
        )
    if round_spent + worst_case > R2_PER_CALL_CAP_USD:
        return (
            False,
            f"deny: R2 per-round cap exceeded (round-spent {round_spent} + "
            f"worst-case {worst_case} > cap {R2_PER_CALL_CAP_USD})",
        )

    # (4) Lifetime hard stop (inclusive ceiling).
    if lifetime_spent + worst_case > LIFETIME_HARD_STOP_USD:
        return (
            False,
            f"deny: lifetime hard stop exceeded (spent {lifetime_spent} + "
            f"worst-case {worst_case} > hard stop {LIFETIME_HARD_STOP_USD})",
        )

    # (5) Remaining credit must cover the worst-case cost.
    remaining = remaining_credit_usd(lifetime_credit_spent_usd)
    if remaining is None:
        return (False, "deny: no remaining credit (lifetime hard stop reached)")
    if worst_case > remaining:
        return (
            False,
            f"deny: worst-case cost {worst_case} exceeds remaining credit "
            f"{remaining}",
        )

    # (6) Cash-zero proof: a positive spend must be refused by the cash path
    # (zero cash, zero buffer, zero liabilities, zero lease headroom). This
    # proves the spend can never be funded from cash; only credit (tracked
    # here, non-fungible) may fund it. A worst-case of exactly zero is a no-op
    # spend and is allowed (the ledger permits a zero spend covered by a zero
    # cap), which does not touch cash.
    permitted_from_cash, _reason = spend_is_permitted(
        amount_usd=worst_case,
        settled_cash_usd=CASH_BUCKET_USD,
        protective_buffer_usd=0,
        contingent_liabilities_usd=0,
        active_lease_headroom_usd=0,
    )
    if worst_case > 0 and permitted_from_cash:
        # Would be fundable from cash: this must never happen with zero cash.
        # Fail closed if the invariant is somehow violated.
        return (False, "deny: spend must never be fundable from cash (fail closed)")

    return (
        True,
        "allow: covered by remaining non-fungible credit; cash path refuses "
        "any positive spend (credits only)",
    )


def make_meta_budget_authorizer(
    *,
    now_iso_provider: Callable[[], str],
    lifetime_credit_spent_usd: Any,
    round_spent_usd: Any = 0,
) -> Callable[[dict], bool]:
    """Build a MetaPlanner ``budget_authorizer`` closure for the FEAT-002 seam.

    The returned callable matches the seam contract: it receives the small,
    non-sensitive ctx dict the adapter passes
    (``{"provider", "model_id", "estimated_credit_cost_usd"}``) and returns a
    bool. It reads the worst-case cost from ``estimated_credit_cost_usd`` and
    combines it with the lifetime/round spent-to-date captured here (the seam
    ctx intentionally carries no spend history, so the closure supplies it) and
    a freshly sampled ``now_iso`` from ``now_iso_provider`` to call
    ``authorize_meta_spend``.

    A truthy return authorizes the send; a falsy return refuses it. The
    MetaPlanner seam treats a raised exception as a fail-closed refusal too, so
    this never needs to swallow one; but it is constructed to be total and
    delegate every decision to the fail-closed ``authorize_meta_spend``.
    """
    if not callable(now_iso_provider):
        raise ValueError("now_iso_provider must be callable")

    def _authorizer(ctx: dict) -> bool:
        if not isinstance(ctx, dict):
            return False
        permitted, _reason = authorize_meta_spend(
            worst_case_cost_usd=ctx.get("estimated_credit_cost_usd"),
            lifetime_credit_spent_usd=lifetime_credit_spent_usd,
            now_iso=now_iso_provider(),
            round_spent_usd=round_spent_usd,
        )
        return permitted

    return _authorizer
