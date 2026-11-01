# SPRAY Resource Ledger and Externally Enforced Budget Contract

**Status: PROPOSED design, not enforced by deployed infrastructure.** This is a
design-only document for P0 gate G2. Nothing here provisions AWS, changes IAM,
stores secrets, makes purchases, posts to SNS, or deploys anything. It describes
how SPRAY's funding is modeled, how spend decisions must fail closed, and how an
external, owner-controlled control plane - not SPRAY - enforces the budget. It
matches the tone and boundaries of the proposed
[docs/SPRAY_CONSTITUTION.md](SPRAY_CONSTITUTION.md) and the proposed
[docs/ARCHITECTURE.md](ARCHITECTURE.md), both of which live only on the unmerged
foundation branch.

The data model and the rules described here are implemented offline, with no
third-party dependencies, in the schema validator
[spray_schema/resource_ledger.py](../spray_schema/resource_ledger.py) and are
exercised by the stdlib test suite in
[tests/test_resource_ledger.py](../tests/test_resource_ledger.py). The offline
CI quality gate that keeps this consistent is described in
[ci/README.md](../ci/README.md). This document is a specification; it is not an
executable policy enforcer and does not substitute for externally enforced
policy.

## 1. The four non-fungible buckets

SPRAY's funding lives in four separate buckets. They **cannot be freely netted
or treated as a single wallet.** Only one of them is spendable money. The bucket
identifiers are the `BUCKET_KINDS` constant and each bucket carries a
`funding_class` of either `credit` or `cash` (the `BUCKET_FUNDING_CLASS`
mapping). Only `settled_cash` has `funding_class == cash`; every credit bucket
has `funding_class == credit`. The validator rejects any bucket whose
`funding_class` does not match the canonical class for its `bucket_kind`, so a
credit can never masquerade as cash (see
`BucketValidationTests.test_funding_class_must_match_bucket_kind`).

| `bucket_kind` | `funding_class` | Eligibility (what it may fund) | Expiry behavior | Provider update / latency uncertainty |
| --- | --- | --- | --- | --- |
| `aws_activate_credit` | `credit` | AWS-eligible services only, within AWS Activate program terms. Not withdrawable, not transferable, not real money. | Expires. A non-empty ISO-8601 `expiry` is required; a credit bucket with a null expiry is rejected (`test_credit_bucket_requires_expiry`). | Balance is a provider self-report with billing-update latency; `provider_update_time` records when the provider last updated it, and `balance_is_fresh` treats a value older than the max age (or in the future / unparseable) as UNKNOWN (Section 5, case 2). |
| `aws_free_tier_credit` | `credit` | Only AWS Free Tier allowances, per-service and per-period. Easy to exceed silently. | Expires or resets per the Free Tier terms; a non-empty ISO-8601 `expiry` is required. | Free Tier consumption is reported with delay; usage can overrun before the provider reflects it. Never the hard cutoff. |
| `kiro_subscription_credit` | `credit` | Kiro development usage through the supported official integration only. | Tied to the subscription period; a non-empty ISO-8601 `expiry` is required. | Subscription accounting is a provider self-report with its own latency. |
| `settled_cash` | `cash` | The only spendable money. Funds authorized, metered activity when (and only when) an active budget lease permits it. | Does not expire: `expiry` may be null for `settled_cash` (`test_settled_cash_may_have_null_expiry`). | Even cash balances rely on provider or bank settlement; only an independently verified, settled claim counts (see Section 3). |

Because credits are restricted and expiring, they are **excluded from spendable
budget entirely.** The helper `usable_authorized_budget_usd` is computed from
`settled_cash` only and never adds credit balances; the test
`SpendDecisionTests.test_credits_are_never_counted_as_cash` pins this. Treating
credits as cash, or netting the buckets into one number, is a modeled threat
(Section 6).

## 2. Data provenance and verification

Every balance or revenue claim records who reported it and whether it has been
independently checked. This encodes the constitution's **"Identity and
truthfulness"** clause: separate observed events from inference, forecasts and
simulated data, and link every material cost or revenue claim to an independent
source of evidence.

- `PROVENANCE_SOURCES = {spray, provider, topia, owner}` records the origin of a
  claim. The non-SPRAY sources `INDEPENDENT_PROVENANCE = {provider, topia,
  owner}` are the only ones that can attest spendable cash.
- `VERIFICATION_STATUSES = {unverified, verified, rejected}` records the
  independent-check result.

Provenance separates asserted truth from verified truth over SOURCE; the
freshness guard `balance_is_fresh(bucket, now_iso, max_age_seconds)` does the
same over TIME. A provider balance is a self-report subject to billing-update
latency, so a `provider_update_time` that is older than the max age, in the
future, unparseable, or not a normalized UTC `Z` string is treated as UNKNOWN
and the balance must not be trusted as current (`BalanceFreshnessTests`).
`validate_bucket` applies the same strictness up front: a `provider_update_time`
that is not a normalized UTC `Z` instant is rejected at validation time, so a
bucket can never validate yet be permanently judged UNKNOWN by the freshness
guard.

**SPRAY self-reports (provenance `spray`) are never sufficient** to mark a
balance verified or to classify money as settled cash. A claim that is settled
and marked verified but sourced from `spray` is rejected with an
"independent provenance" error
(`ProjectedRevenueTests.test_settled_cash_requires_independent_provenance`).
Local agent memory and logs are not authoritative for money.

**"A projected sale is not money."** The `SETTLEMENT_STATES` are `{projected,
forecast, simulated, pending, settled}`, and `UNSETTLED_STATES = {projected,
forecast, simulated, pending}`. The validator rule enforced in
`validate_ledger` / `validate_bucket` (via `_check_settled_cash_integrity`)
rejects any unsettled claim that is marked `verified` or placed in the
`settled_cash` bucket, with the error
`projected revenue cannot be verified settled cash`. The single gate
`is_settled_cash_claim()` returns true **only** for a claim that is in the
`settled_cash` bucket, has `funding_class == cash`, is in settlement state
`settled`, is `verified`, and carries independent provenance. Everything else,
including anything SPRAY self-reports, is not money. Evidence:
`ProjectedRevenueTests` (projected / forecast / simulated / pending all
rejected; the same claim accepted only once settled and independently
verified).

## 3. Cash runway and credit runway are separate

Runway is reported in two independent figures. **Credits cannot extend cash
runway.**

- **Cash runway** is driven by `settled_cash` only. The helper
  `cash_runway_days(settled_cash_usd, burn_rate_usd_per_day,
  protective_buffer_usd, contingent_liabilities_usd)` divides the usable
  authorized budget by the burn rate.
- **Credit runway** is tracked per credit bucket from its own balance, its own
  eligibility, and its own expiry. It never flows into the cash figure.

The spendable figure is
`usable_authorized_budget_usd = settled_cash - protective_buffer -
contingent_liabilities`, with credits deliberately omitted. The ledger carries
the fail-closed inputs `burn_rate_usd_per_day`, `contingent_liabilities_usd`,
`protective_buffer_usd` and `forecast_confidence`; all are validated as finite,
non-negative numbers (and `forecast_confidence` is bounded to 0..1).

**Why a protective buffer is mandatory.** From the constitution: "Costs cannot
be guaranteed to stop at exactly zero by billing telemetry alone; maintain a
protective buffer." The buffer and contingent liabilities are always subtracted
before any spend is considered.

**Why the hard cutoff must not rely on delayed AWS billing (G2).** The G2 gate
states: "Never rely on delayed AWS billing for exact hard cutoff." Provider
billing telemetry arrives late and can lag real consumption. Therefore
`spend_is_permitted(...)` denies outright when `rely_on_provider_billing_only`
is true (`SpendDecisionTests.test_spend_denied_when_relying_on_provider_billing_only`),
and the independent off switch and rate caps in the external control plane are
the real cutoff, not billing numbers.

## 4. Externally enforced budget contract

The authority that enforces the budget is an **EXTERNAL CONTROL PLANE** owned by
the operator / owner, as described in the architecture and the constitution's
**"Oversight and shutdown"** clause. It holds owner-controlled IAM, independent
billing collection, quotas and rate caps, signing keys, short-lived
work-authorization leases, and an independent off switch. It lives in
accounts and roles that **SPRAY cannot edit, replace, impersonate or disable.**

### No-spend lease model

Every paid call consumes a **budget lease that expires.** Leases are modeled by
`validate_lease` and `lease_is_active` with these invariants:

- Leases are granted and revoked only by the external control plane or the
  owner: `LEASE_AUTHORITIES = {external_control_plane, owner}`. A lease whose
  `authority` is `spray` is rejected
  (`NoSpendLeaseTests.test_spray_authority_lease_is_rejected`).
- A lease whose `provenance` is `spray` is rejected: **SPRAY cannot self-grant
  or self-un-revoke a lease**
  (`NoSpendLeaseTests.test_spray_self_granted_lease_is_rejected`).
- `lease_is_active(lease, now_iso)` **fails closed.** Any malformed lease, an
  unknown or missing `now_iso`, a revoked lease, or an expired lease returns
  false (`test_lease_inactive_after_expiry`,
  `test_lease_inactive_after_owner_revocation`,
  `test_lease_inactive_on_unknown_now`).
- A **no-spend lease** has `max_spend_usd == 0`; `is_no_spend_lease` detects it.
  An independent owner can revoke a lease at any time, and a revoked lease must
  record an independent `revoked_by`; a revocation attributed to `spray` is
  rejected (`test_revoked_lease_without_independent_revoker_is_rejected`).

SPRAY cannot self-grant, extend, un-revoke, or roll back a lease timer. A
request for additional time or funds may be evaluated using verifiable facts,
but it **never changes policy by persuasion alone.**

**Lease state is wired into the spend decision, and a lease limit is a cap, not
money.** `spend_is_permitted` must not be fed headroom from a lease the ledger
has not confirmed active, and it must never treat a lease's authorized limit as
spendable funds. The authorization figure is derived only from leases that pass
`lease_is_active(now_iso)`, so a revoked, expired, or not-yet-started lease
contributes zero; its result is intended to be passed directly as
`spend_is_permitted(active_lease_headroom_usd=...)`.

**A single covering lease is required by default (independent lease headrooms
are NOT combined).** The DEFAULT, recommended lease-authorization figure is the
MAXIMUM remaining headroom of any ONE active, non-revoked, non-expired,
correct-funding-class lease - **not** the sum across leases. The helper
`single_lease_authorization_usd(leases, now_iso)` returns exactly this maximum,
and `active_lease_headroom_usd(leases, now_iso)` behaves identically by default
(its `combine_across_leases` parameter defaults to `False`). Consequently a
plain spend is authorized only when ONE lease by itself covers the amount:
several independent leases whose remaining headrooms only **sum** to cover an
amount do **not** authorize it, because no single lease covers it. This closes a
gap where several leases could jointly authorize a spend no one lease covered,
with no contract naming or debiting the specific contributing leases. Both
helpers fail closed (return `None`) on a malformed lease list, an unknown or
non-normalized `now_iso`, or an active lease with an unusable `max_spend_usd` or
an unknown/unverified consumed-to-date, and `spend_is_permitted` then denies;
with no active correct-funding-class lease they return `0.0` (no authorization).
Evidence:
`SingleLeaseAuthorizationTests.test_default_returns_max_single_lease_not_sum`,
`test_joint_only_spend_is_denied_by_default`,
`test_single_covering_lease_authorizes_spend`,
`test_default_fails_closed_on_unverified_consumption`,
`test_credit_lease_contributes_zero_to_single_authorization`;
`ActiveLeaseHeadroomTests.test_default_is_max_single_active_lease`.

`spend_is_permitted` permits a spend only when **both** independent conditions
hold: (a) **sufficient verified funds** - the requested amount is covered by
`usable_authorized_budget_usd` (settled cash minus the protective buffer and
contingent liabilities; credits never count); and (b) **a valid active lease** -
the requested amount is covered by the active-lease authorization limit
(`active_lease_headroom_usd`, the single-covering-lease figure by default). A
lease's `max_spend_usd` is an authorization **CAP**, not a funding source: lease
headroom is **never added to cash** to manufacture spendable budget. Consequently
a positive spend with no active lease (headroom of zero, the default) is DENIED
no matter how much cash is on hand, and a lease cap can never top up insufficient
cash. Evidence:
`ActiveLeaseHeadroomTests.test_headroom_from_revoked_lease_does_not_authorize_spend`,
`test_active_lease_headroom_gates_spend_but_is_not_cash`,
`test_active_lease_headroom_permits_spend_within_both_limits`,
`test_default_is_max_single_active_lease`, and
`SpendDecisionTests.test_regression_spend_denied_when_no_active_lease_even_if_cash`,
`test_regression_lease_cap_not_treated_as_cash_to_top_up`,
`test_lease_headroom_is_a_cap_not_additive_funds`,
`test_both_conditions_required_permits_only_when_both_hold`.

### Atomic lease allocation/reservation contract (jointly authorizing one spend)

Because the default requires a single covering lease, multiple leases may
**jointly** authorize one spend **only** under an EXPLICIT atomic
allocation/reservation contract that identifies and reserves/debits the specific
contributing leases. This is a **PROPOSAL LAYERED ON the existing external,
owner-controlled control plane** described above; it does **not** change the
control-plane architecture.

> **Design constraint (per the owner): SPRAY does NOT enforce or self-debit.**
> The reservation is only a *representation* of what the external control plane
> would atomically perform. The same owner-controlled control plane that grants,
> revokes, and times leases is the authority that actually identifies the
> contributing leases and reserves/debits each one under a per-request
> idempotency key. Adopting this contract requires owner approval; nothing here
> moves enforcement into SPRAY.

The contract the external control plane must honor to jointly authorize a spend:

- **Named, specific contributing leases.** A joint authorization must identify
  the exact leases it draws on and the amount reserved/debited against each.
  Combining anonymous or unidentified headroom is never permitted; the summed
  headroom of leases nobody named cannot authorize a spend.
- **Atomic reserve/debit under a per-request idempotency key.** Each spend
  request carries an idempotency key, and the control plane reserves/debits the
  named leases **atomically** (a single transaction) so concurrent or retried
  requests for the same key reserve **at most once** and two concurrent requests
  can never both pass against the same combined headroom.
- **All-or-nothing / fail closed.** If the named leases cannot jointly cover the
  amount, or any candidate lease has unknown/unverified remaining headroom, the
  reservation fails and no partial debit is applied.

The SPRAY-side representation and fail-closed check:

- `allocate_lease_reservation(leases, amount_usd, now_iso, funding_class=cash)`
  is the ONLY sanctioned way multiple leases may jointly authorize one spend. On
  success it returns a reservation dict with `covered == True`, the requested
  `amount_usd`, the total `reserved_usd`, and an `allocations` list of
  `(lease_id, reserved_usd)` pairs that **names the contributing leases** and the
  per-lease reserved/debited amount. It fails closed (returns `None`) when
  `now_iso` is unknown/non-normalized, the lease list is malformed, `amount_usd`
  is unknown/invalid, any candidate active lease has `None` remaining headroom
  (unusable cap or unknown/unverified consumed-to-date), or the leases cannot
  jointly cover the amount; a not-covered request never returns a partial
  reservation. Non-fungibility is preserved: a credit-bucket lease never
  contributes to a cash reservation.
- `active_lease_headroom_usd(..., combine_across_leases=True)` is the explicit
  opt-in that sums remaining headroom across the named leases; it represents the
  joint capacity the atomic reservation draws on and is correct ONLY under this
  contract. It is never the default.
- SPRAY only reads the externally maintained figures and proposes a reservation;
  it never performs the debit. Evidence:
  `AtomicLeaseAllocationTests.test_jointly_covering_reservation_names_contributing_leases`,
  `test_single_lease_sufficient_reservation_names_one_lease`,
  `test_not_jointly_covered_fails_closed`, `test_unknown_clock_fails_closed`,
  `test_malformed_lease_list_fails_closed`, `test_invalid_amount_fails_closed`,
  `test_unverified_consumption_candidate_fails_closed`,
  `test_credit_lease_excluded_from_cash_reservation`,
  `test_explicit_combine_matches_reservation_capacity`.

**Timestamps are compared as instants, not strings.** Both `now_iso` and a
lease's `expires_at` must be normalized UTC ISO-8601 strings ending in `Z`.
`validate_lease` rejects any `granted_at` / `expires_at` that is not in that
form, and `lease_is_active` parses both values into timezone-aware instants
before comparing, so a non-`Z` offset or differing precision cannot flip an
expiry decision (`IsoTimestampGuardTests`).

**Future-dated leases and invalid validity intervals are rejected.** A lease's
validity window must be well-formed and must have actually started:

- **Structural ordering (clock-free).** `validate_lease` requires
  `expires_at` to be strictly after `granted_at`, compared as INSTANTS via
  `_parse_utc_iso` (never lexicographically). An inverted interval
  (`expires_at < granted_at`) or a zero-length one (`expires_at == granted_at`)
  is rejected with `expires_at must be strictly after granted_at (invalid or
  zero-length lease validity interval)`. This check needs no clock, so it lives
  in `validate_lease`; it is only emitted when both timestamps already parse as
  normalized `Z` instants, so it never piles onto a non-`Z`/unparseable
  timestamp error (`LeaseIntervalTests.test_inverted_interval_rejected_by_validator`,
  `test_zero_length_interval_rejected`,
  `test_interval_compared_as_instants_not_lexicographically`,
  `test_interval_error_not_piled_on_bad_timestamp`).
- **Not-yet-started (clock-dependent).** `lease_is_active(lease, now_iso)` treats
  a lease whose `granted_at` is in the future relative to `now` as INACTIVE,
  the same fail-closed treatment as expiry: a lease is usable only when
  `granted_at <= now < expires_at`. An unknown/unparseable `granted_at` also
  fails closed. This needs the clock, so it lives in `lease_is_active`
  (`LeaseIntervalTests.test_future_dated_lease_is_inactive`,
  `test_lease_active_only_at_or_after_granted_at`,
  `test_future_dated_lease_contributes_zero_headroom`).

**A credit-bucket lease cannot authorize cash spend (non-fungibility of
authorization).** The authorization headroom is bound to a funding class, not
just to activity. `active_lease_headroom_usd(leases, now_iso,
funding_class=cash)` counts a lease toward the cash-spend authorization limit
**only** when its `bucket_kind` maps (via `BUCKET_FUNDING_CLASS`) to the
requested funding class, which defaults to `cash` (and `settled_cash` is the
only cash bucket). A lease on a credit bucket (`aws_activate_credit`,
`aws_free_tier_credit`, `kiro_subscription_credit`) contributes **zero** to the
cash authorization limit, so an active credit lease with an enormous cap and
ample cash on hand still cannot authorize a settled-cash spend. An
unexpected/unknown `bucket_kind` maps to no funding class and likewise
contributes zero (fail closed). This mirrors, at the authorization layer, the
bucket non-fungibility already enforced for balances in Section 1 (credits are
never netted into spendable cash). Evidence:
`CreditLeaseAuthorizationTests.test_credit_bucket_lease_contributes_zero_cash_headroom`,
`test_credit_lease_does_not_authorize_cash_spend`,
`test_cash_bucket_lease_still_counts`,
`test_explicit_credit_funding_class_filter`.

### Atomic external accounting contract (consumption vs. an unchanged cap)

A lease's `max_spend_usd` is a **cap**, not a one-shot token. Without
cumulative accounting, the same unchanged cap could be presented over and over
to authorize repeated spends that together far exceed it (a replayed-debit /
double-spend against one cap). The following **ATOMIC EXTERNAL ACCOUNTING
CONTRACT** closes that gap. It is a **PROPOSAL LAYERED ON the existing external,
owner-controlled control plane** described above:

> **Design constraint (per the owner): the control-plane architecture is NOT
> being changed, and SPRAY does not enforce this.** SPRAY cannot self-debit or
> self-credit a lease. The contract below is maintained and enforced by the
> same external, owner-controlled control plane that already grants, revokes,
> and times leases. The SPRAY-side helpers are only a *representation plus a
> fail-closed check* of the externally maintained figures, never the enforcer.
> Adopting this contract requires owner approval; nothing here moves
> enforcement into SPRAY.

The contract the external control plane must honor:

- **Cumulative consumed-to-date vs. cap.** Each lease carries, in the control
  plane, a monotonically non-decreasing `consumed_usd` (total authorized spend
  debited against the lease so far) alongside its fixed `max_spend_usd` cap.
  This figure is **independently attested**: the control plane records it with a
  `consumed_usd_provenance` naming the independent source (`provider`, `topia`,
  or `owner`). SPRAY cannot attest its own consumption.
- **Unknown consumption is UNKNOWN, never zero.** A lease whose consumed-to-date
  cannot be independently established (absent, or self-reported) is treated as
  UNKNOWN and authorizes **no** further spend. It is never assumed to be fully
  unconsumed. Only an independently-attested figure (including an attested
  `0.0`) authorizes headroom.
- **Remaining headroom = cap minus consumed.** The slice still authorizable is
  `max_spend_usd - consumed_usd`, clamped to zero (over-consumption is never
  negative headroom). When `consumed_usd` reaches the cap, remaining headroom
  is zero and the lease authorizes no further spend even though the cap value
  is unchanged.
- **Idempotent, atomic debit.** Each spend request carries an **idempotency
  key**. The control plane debits `consumed_usd` **atomically** (a single
  compare-and-increment / transaction) so that concurrent or retried requests
  for the same key debit **at most once**, and two concurrent requests can
  never both pass against the same remaining headroom. This is what prevents a
  double-spend / replayed debit against one cap.

The SPRAY-side representation and fail-closed check:

- An **optional** `consumed_usd` field on a lease, plus a sibling
  `consumed_usd_provenance` naming the independent source that attests it.
  `validate_lease` requires `consumed_usd` to be a finite, non-negative number
  when present, requires `consumed_usd_provenance` (when present) to be one of
  `PROVENANCE_SOURCES`, and rejects a self-reported `spray` attestation
  outright: **SPRAY cannot attest its own consumption**
  (`LeaseConsumptionTests.test_self_reported_consumed_is_not_verified`,
  `test_unverified_consumed_provenance_rejected_by_validator`,
  `test_invalid_consumed_rejected_by_validator`,
  `test_invalid_consumed_fixture_lease_rejected`).
- **Unknown consumption fails closed; it is NOT treated as `0.0`.** This
  reverses the former back-compatible "absent `consumed_usd` => 0.0 consumed"
  convenience. A lease's `consumed_usd` counts toward headroom **only** when it
  is independently verified: a finite, non-negative figure carrying a
  `consumed_usd_provenance` in `INDEPENDENT_PROVENANCE` (`{provider, topia,
  owner}`), the same independence bar used for settled cash and `revoked_by`. A
  consumed figure that is absent, self-reported (`spray`), or missing/with an
  unrecognized provenance is **not** independently verified and does not count.
  An attested `0.0` with independent provenance is the explicit "zero consumed
  so far" figure; zero only counts when attested, never as an absent default
  (`test_absent_consumed_fails_closed`,
  `test_attested_zero_consumed_authorizes_full_cap`,
  `test_verified_consumed_authorizes_only_the_remainder`,
  `test_self_reported_consumed_is_not_verified`,
  `test_consumed_without_provenance_fails_closed`).
- The helper `lease_remaining_headroom_usd(lease, consumed_usd=None)` computes
  `max_spend_usd - consumed_to_date`, returns `0.0` on over-consumption (never
  negative), and fails closed (`None`) when the cap is unusable, when an
  explicitly present `consumed_usd` argument is unknown/invalid, or when no
  independently-verified consumed figure can be established
  (`test_remaining_headroom_is_cap_minus_consumed`,
  `test_fully_consumed_lease_contributes_zero_headroom`,
  `test_consumed_exceeding_cap_is_zero_not_negative`,
  `test_unknown_consumed_fails_closed`, `test_unusable_cap_fails_closed`,
  `test_absent_consumed_fails_closed`).
- `active_lease_headroom_usd` threads **remaining** headroom (not the raw cap)
  into the authorization path: it evaluates `lease_remaining_headroom_usd` over
  the active cash leases (returning the max single-lease remaining headroom by
  default, see the single-covering-lease rule above), so a fully consumed lease
  contributes zero and a lease with unknown/unverified consumption makes the
  tally fail closed (`None`), so the same unchanged cap cannot re-authorize
  spends beyond it and an unverifiable consumption figure can never silently
  authorize spend
  (`test_headroom_reflects_remaining_not_raw_cap`,
  `test_fully_consumed_cap_cannot_reauthorize_further_spend`,
  `test_partially_consumed_cap_authorizes_only_the_remainder`,
  `test_absent_consumed_fails_closed`,
  `test_consumed_without_provenance_fails_closed`). SPRAY only reads the
  externally maintained consumed figure here; it never increments it.

## 5. Zero-budget and fail-closed decision cases

Every case below states a fail-closed outcome, and **UNKNOWN is always treated
as not-PASS.** The sentinel `UNKNOWN` and the `_usable_number` guard ensure any
unknown, missing, non-numeric, NaN, Infinity or negative input yields a denial
rather than a guess.

| # | Scenario | Required decision (fail closed) | FEAT-001 helper / test |
| --- | --- | --- | --- |
| 1 | Usable authorized budget is below the protective buffer (or otherwise insufficient for the request). | Deny the new metered action. New metered actions must stop and the external owner-controlled enforcer terminates runtime execution safely. | `spend_is_permitted` returns `(False, "insufficient...")`; `SpendDecisionTests.test_spend_denied_when_insufficient_budget`. |
| 2 | Provider billing / balance update is stale or unknown; any budget input is UNKNOWN. | Treat as UNKNOWN and fail closed; do not PASS. Never rely on delayed billing telemetry as the hard cutoff. | `balance_is_fresh(bucket, now_iso, max_age_seconds)` returns `False` for a `provider_update_time` older than the max age, in the future, unparseable, or non-normalized (`BalanceFreshnessTests`); `spend_is_permitted` with `rely_on_provider_billing_only=True` or any unknown input denies (`test_spend_denied_when_input_unknown`, `test_spend_denied_when_relying_on_provider_billing_only`); `usable_authorized_budget_usd` returns `None` on unknown (`test_usable_budget_unknown_on_unknown_input`). |
| 3 | A budget lease is expired, revoked, **not yet started** (future-dated `granted_at`), or there is no active lease at all (even if cash is available). | Deny; without a valid active lease authorizing the amount there is no permission, regardless of cash on hand. A lease is usable only when `granted_at <= now < expires_at`; a future-dated or inverted/zero-length interval never authorizes spend. A lease limit is an authorization cap, never spendable funds, so it can never top up insufficient cash. `spend_is_permitted` requires BOTH sufficient usable funds AND an active-lease authorization that each independently cover the amount. | `lease_is_active` returns false; `NoSpendLeaseTests.test_lease_inactive_after_expiry`, `test_lease_inactive_after_owner_revocation`; `LeaseIntervalTests.test_future_dated_lease_is_inactive`, `test_inverted_interval_rejected_by_validator`; `ActiveLeaseHeadroomTests.test_headroom_from_revoked_lease_does_not_authorize_spend`; `SpendDecisionTests.test_regression_spend_denied_when_no_active_lease_even_if_cash`, `test_regression_lease_cap_not_treated_as_cash_to_top_up`. |
| 4 | A credit-bucket lease is presented to authorize settled-cash spend. | Deny; a credit-funding-class lease contributes zero to the cash authorization limit (non-fungibility at the authorization layer), so it can never authorize cash spend no matter how large its cap or how much cash is on hand. | `active_lease_headroom_usd(..., funding_class=cash)` counts only cash-funding-class leases; `CreditLeaseAuthorizationTests.test_credit_bucket_lease_contributes_zero_cash_headroom`, `test_credit_lease_does_not_authorize_cash_spend`. |
| 5 | A lease cap is already fully consumed (`consumed_usd == max_spend_usd`), or consumed-to-date is unknown/unverified (absent, self-reported `spray`, missing/invalid `consumed_usd_provenance`, or an invalid `consumed_usd`). | Deny further spend: remaining headroom is zero for a fully consumed cap, so an unchanged cap cannot re-authorize repeated spends. Unknown/unverified consumption **fails closed and is never treated as `0.0` consumed**: `consumed_usd` counts only when independently verified (a finite, non-negative figure with a `consumed_usd_provenance` in `INDEPENDENT_PROVENANCE`), including an attested `0.0`. Otherwise `lease_remaining_headroom_usd` returns `None`, the counted lease makes `active_lease_headroom_usd` return `None`, and the spend is DENIED even with ample cash. | `lease_remaining_headroom_usd` clamps to `0.0` and returns `None` on unknown/unverified consumed; `active_lease_headroom_usd` sums remaining headroom and fails closed on a `None`; `LeaseConsumptionTests.test_fully_consumed_cap_cannot_reauthorize_further_spend`, `test_absent_consumed_fails_closed`, `test_self_reported_consumed_is_not_verified`, `test_consumed_without_provenance_fails_closed`, `test_attested_zero_consumed_authorizes_full_cap`, `test_unknown_consumed_fails_closed`, `test_invalid_consumed_lease_fails_closed_and_contributes_nothing`. |
| 6 | Only credits remain: no settled cash and no authorized funded continuation. | Stop safely. There is no fixed death date, but there is no unfunded continuation either; credits are excluded from spendable budget. | `usable_authorized_budget_usd` ignores credits (`test_credits_are_never_counted_as_cash`); `cash_runway_days` yields `0.0` when usable budget is non-positive (`test_cash_runway_zero_when_no_usable_budget`). |
| 7 | Projected / forecast / simulated / pending revenue is present. | Exclude it from spendable budget; it is not money and cannot be verified settled cash. | `_check_settled_cash_integrity` rejects it (`projected revenue cannot be verified settled cash`); `is_settled_cash_claim` returns false; `ProjectedRevenueTests`. |
| 8 | Several independent active cash leases individually do NOT cover a spend, but their remaining headrooms would SUM to cover it, and there is no explicit atomic reservation naming the contributing leases. | Deny. The DEFAULT authorization requires a SINGLE covering lease; independent lease headrooms are never silently combined. Multiple leases may jointly authorize one spend ONLY under the explicit atomic allocation/reservation contract (`allocate_lease_reservation`), which names and atomically reserves/debits the specific contributing leases under a per-request idempotency key. SPRAY does not enforce or self-debit; the reservation is a representation only. | `single_lease_authorization_usd` / `active_lease_headroom_usd` (default `combine_across_leases=False`) return the max single-lease headroom; `allocate_lease_reservation` names contributors or fails closed; `SingleLeaseAuthorizationTests.test_joint_only_spend_is_denied_by_default`, `test_single_covering_lease_authorizes_spend`; `AtomicLeaseAllocationTests.test_jointly_covering_reservation_names_contributing_leases`, `test_not_jointly_covered_fails_closed`. |

## 6. Threat model

This mirrors the Threats list in [docs/ARCHITECTURE.md](ARCHITECTURE.md)
(prompt injection, malicious PR, supply-chain modifications, false tool output,
forged self-report, cost denial of service, secret exfiltration, timer/lease
rollback, unauthorized social automation) and ties the budget-relevant threats
to a validator rule or test from FEAT-001.

| Threat | Attack | Mitigation (validator rule / test) |
| --- | --- | --- |
| Forging TOPIA / provider self-reports | Runtime fabricates a provider or TOPIA report, or marks its own report verified, to classify a projection as money. | Settled cash requires independent provenance plus `verification == verified` plus `settlement_state == settled`; self-reports (`spray`) can never verify a balance. `is_settled_cash_claim`; `ProjectedRevenueTests.test_settled_cash_requires_independent_provenance`. |
| Circumventing expiration | Runtime hides or omits a credit expiry, presents a stale `provider_update_time` as current, or feeds a non-normalized timestamp to dodge an expiry comparison. | Credit buckets require a non-empty ISO-8601 `expiry` (`test_credit_bucket_requires_expiry`). `validate_bucket` requires `provider_update_time` to be a normalized UTC ISO-8601 `Z` string (the same rule `validate_lease` applies to lease timestamps), and `balance_is_fresh` treats a stale, future, unparseable, or non-'Z' value as UNKNOWN and fails closed (`BalanceFreshnessTests`; Section 5, case 2). Lease timestamps must be normalized UTC 'Z' strings and are compared as instants, not lexicographically, so a mixed offset or precision cannot defeat expiry (`IsoTimestampGuardTests`). |
| Lease / timer rollback | Runtime tries to extend, un-revoke, roll back a lease timer, or forge a future-dated / inverted validity window to keep spending. | Leases are owned by the external authority: `authority` and `provenance` of `spray` are rejected, a revoked lease needs an independent `revoked_by`, and `lease_is_active` fails closed on expiry / revocation / unknown time. A lease validity interval must be well-formed (`expires_at` strictly after `granted_at`, compared as instants) and a not-yet-started lease (`granted_at` in the future) is inactive. `NoSpendLeaseTests` (`test_spray_authority_lease_is_rejected`, `test_spray_self_granted_lease_is_rejected`, `test_revoked_lease_without_independent_revoker_is_rejected`, `test_lease_inactive_after_expiry`); `LeaseIntervalTests` (`test_inverted_interval_rejected_by_validator`, `test_zero_length_interval_rejected`, `test_future_dated_lease_is_inactive`). |
| Repeated spend against an unchanged cap / replayed debit | Runtime presents the same unchanged lease cap repeatedly, or replays a spend request, to authorize cumulative spend far beyond the cap (double-spend against one authorization). | Cumulative consumption accounting: remaining headroom is `max_spend_usd` minus consumed-to-date, so a fully consumed lease contributes zero and the same cap cannot re-authorize further spend; the external control plane debits atomically with a per-request idempotency key (atomic external accounting contract, Section 4). SPRAY-side this is a representation plus fail-closed check (`consumed_usd` unknown/invalid fails closed), not enforcement. `LeaseConsumptionTests.test_fully_consumed_cap_cannot_reauthorize_further_spend`, `test_partially_consumed_cap_authorizes_only_the_remainder`, `test_consumed_exceeding_cap_is_zero_not_negative`, `test_unknown_consumed_fails_closed`. |
| Unknown / self-reported consumption treated as unconsumed | Runtime omits consumed-to-date, or self-reports it, so a lease looks fully unconsumed and re-authorizes spend against a cap it has already drawn down. | Unknown consumption **fails closed, never defaults to `0.0` consumed**: a lease's `consumed_usd` counts only when independently verified (finite, non-negative, with a `consumed_usd_provenance` in `INDEPENDENT_PROVENANCE`; a `spray` attestation is rejected). An absent/self-reported/unattested figure makes `lease_remaining_headroom_usd` return `None` and `active_lease_headroom_usd` fail closed, so the spend is DENIED. SPRAY-side this is a representation plus fail-closed check, not enforcement; the external control plane maintains and attests the figure. `LeaseConsumptionTests.test_absent_consumed_fails_closed`, `test_self_reported_consumed_is_not_verified`, `test_consumed_without_provenance_fails_closed`, `test_attested_zero_consumed_authorizes_full_cap`, `test_unverified_consumed_provenance_rejected_by_validator`, `test_invalid_consumed_fixture_lease_rejected`. |
| Jointly authorizing a spend no single lease covers | Runtime presents several independent active leases whose remaining headrooms only SUM to cover an amount, so the combined figure clears a spend no one lease authorizes, with no contract naming or debiting the contributing leases. | DEFAULT single-covering-lease authorization: `single_lease_authorization_usd` / `active_lease_headroom_usd` (default `combine_across_leases=False`) return the MAX single-lease remaining headroom, not the sum, so a spend is authorized only when ONE lease covers it. Multiple leases may jointly authorize one spend ONLY via the explicit atomic allocation/reservation contract (`allocate_lease_reservation`), which names and atomically reserves/debits the contributing leases under a per-request idempotency key (Section 4). SPRAY-side this is a representation plus fail-closed check, not enforcement. `SingleLeaseAuthorizationTests.test_joint_only_spend_is_denied_by_default`, `test_single_covering_lease_authorizes_spend`; `AtomicLeaseAllocationTests.test_jointly_covering_reservation_names_contributing_leases`, `test_not_jointly_covered_fails_closed`, `test_credit_lease_excluded_from_cash_reservation`. |
| Cost denial-of-service | Runtime drives metered usage up faster than billing telemetry reports, aiming to overspend before any cutoff. | Hard budget caps independent of billing telemetry plus a mandatory protective buffer and contingent liabilities; `spend_is_permitted` denies on `rely_on_provider_billing_only`, on insufficient budget, and on any unknown input. `SpendDecisionTests.test_spend_denied_when_relying_on_provider_billing_only`, `test_spend_denied_when_insufficient_budget`, `test_spend_denied_when_input_unknown`. |
| Treating credits as cash / netting buckets | Runtime nets credit balances into the spendable figure or relabels a credit bucket as cash. | Non-fungibility is enforced: `funding_class` must match the `bucket_kind`, and `usable_authorized_budget_usd` counts `settled_cash` only. `BucketValidationTests.test_funding_class_must_match_bucket_kind`; `SpendDecisionTests.test_credits_are_never_counted_as_cash`. |

## 7. Boundaries

Consistent with the top-level [README.md](../README.md) hard boundaries and the
constitution: no secrets in this repo, no credential exposure, no AWS
provisioning, no IAM change, no purchases, no SNS posting, and no deployment.
The authoritative enforcement is external and owner-controlled; this document
and the validator are a specification and an offline check, not the enforcer.
