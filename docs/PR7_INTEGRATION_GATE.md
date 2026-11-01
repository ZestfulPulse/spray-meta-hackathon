# PR #7 integration acceptance gate (design-only)

Status: **NOT_VERIFIED** until the head commit and its actual checks are inspected. This document does not grant spend authority or claim enforcement.

## Branch dependency

- PR #6 head at review: `5f2d6a2` (event envelope and tests).
- PR #7 head at review: `a4b97dc`.
- GitHub compare `5f2d6a2...a4b97dc`: diverged, ahead 10, behind 1. Re-check after Kiro's update. Do not interpret a green check on an earlier head as a check on the new head.
- Ensure PR #6's latest event-envelope fix is included in the eventual integration history before merge. Stacked PRs may legitimately diverge before integration.

## Required regression scenarios

1. **Duplicate lease IDs:** Two entries with the same `lease_id` must not count as two independent authorizations, even if their summed headroom would cover the request. Invalid IDs must fail closed.
2. **Exact money:** A positive sub-cent Decimal must not become zero by float conversion; large integer/Decimal values must not round upward into permission. Prefer exact decimal or integer minor-unit arithmetic and explicit precision policy.
3. **Nonfungibility:** `aws_activate_credit`, `aws_free_tier_credit`, and `kiro_subscription_credit` must never authorize a `settled_cash` spend, nor should two incompatible credit kinds be pooled without explicit provider-specific rules.
4. **Verified consumption:** Unknown or unverified consumed-to-date must deny. An explicit override must not reduce independently verified consumption without authoritative evidence.
5. **Atomicity:** Multi-lease allocation is only a proposed reservation; it is not real external enforcement. Duplicate dispatch, crash/retry, lease revocation, and stale cash evidence must fail closed in any future executor.
6. **Numeric validation:** bool, complex, NaN, Infinity, negative amounts, and malformed timestamps must be rejected without unexpected exceptions.

## Evidence required for completion

- Link exact GitHub commit SHA(s) containing fixes and regression tests.
- Run the offline test suite against that same head SHA; link the relevant GitHub Actions run and inspect its conclusion. Historical CI runs are not proof of a new commit.
- Check PR #1/#3/#6/#7 dependency ordering and conflicts. No merges without owner approval.
- Distinguish pure schema/decision simulation from real owner-controlled spend enforcement. The latter remains **NOT_VERIFIED**.

## Stabilization batch: 2026-10-16

- Last verified green baseline: `6808a3e`, Actions run #23 (success).
- Follow-up code and regression commits: `d0dfaa5`, `0d18d23`, `4854be7`, `1e210f1`.
- These later commits are **NOT_VERIFIED** until CI on their exact latest head completes.
- Verified consumption is retained as Decimal before subtraction; an explicit lower override is rejected when a larger independently attested consumed amount exists.
- Restricted-credit reporting is distinct from reservation authorization; generic credit-class reservations fail closed.
- PR #6 dependency remains **UNRESOLVED** (previous compare showed one commit behind `5f2d6a2`). Do not merge PR #7 on the strength of this document.
- Remaining acceptance work: latest-head CI, compare PR #6/#7, review numeric representation in multi-lease allocation, and verify owner-controlled external enforcement separately.

## P0-G2 milestone execution checkpoint

- Owner-approved milestone scope: ledger precision, regression tests, PR #6/#7 integration review, and offline CI verification.
- Latest independently verified green head: `4304009c09c6f9160ed5d457fddbebee8c609ef3`, Actions run [#31](https://github.com/ZestfulPulse/SPRAY/actions/runs/38973296904) (success).
- PR #6 comparison at this checkpoint: diverged, PR #7 head 30 commits ahead and 1 commit behind `5f2d6a2`. **Dependency unresolved**. This is not merge approval.
- Next integration gate: inspect the one missing event-envelope commit and incorporate it without overwriting existing ledger changes; then run CI on the resulting head. Do not mark the milestone complete from an older green commit.
- Authorization and reservation are schema-level simulations; external debit enforcement, retries, and crash recovery remain **NOT_VERIFIED**.

## P0-G2 post-merge evidence (2026-10-16)

- PR #6 merge commit: `e6ae48d5fd195a3cc05dc4eeac5e666559f6899e`.
- PR #7 merge API returned `baa7da3a1ae1645c18e4c15b1321057170c36ec2`, but `main` did not advance to it; a non-fast-forward update was correctly rejected.
- Recovery PR #8 was merged at `2058a73f119335616beff580930969f90cd2418b` without force pushing. Compare that SHA against `main` returned **identical** and `spray_schema/resource_ledger.py` was readable on `main`.
- Pre-merge integrated PR head `a82a536e49e572510872dcdce66b1c8dcd94f9bb`: [CI #33](https://github.com/ZestfulPulse/SPRAY/actions/runs/38973828398) **PASS**.
- Post-merge `main` Actions result for `2058a73`: **NOT_VERIFIED** (the available PR-run query returned no matching run; no status checks were exposed by combined commit status). Do not mislabel this as a CI failure or PASS.
- P0-G2 code integration: **PASS**. P0-G2 milestone closure: **PENDING final main CI evidence**. External spend enforcement: **NOT_VERIFIED**.

## Safety boundaries

No AWS resources, IAM or secret changes, paid execution, production deployment, external social posting, or automatic merge. Existing PR #7 only; avoid duplicate PRs and repeated `/kiro fix` requests.
