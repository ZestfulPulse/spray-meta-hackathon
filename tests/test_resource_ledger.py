"""Stdlib unittest suite for the SPRAY resource-ledger schema and fixtures.

Pure Python 3.11 standard library: no pytest, no third-party dependencies,
no network, no AWS. Run with:

    python3 -m unittest discover -s tests -p 'test_*.py' -v
"""

from __future__ import annotations

import copy
import decimal
import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

# Make the repo root importable so spray_schema / ci resolve regardless of the
# current working directory when the suite is discovered.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ci.policy_checks import CREDENTIAL_PATTERNS  # noqa: E402
from spray_schema.resource_ledger import (  # noqa: E402
    BUCKET_KINDS,
    DEFAULT_BALANCE_MAX_AGE_SECONDS,
    FUNDING_CLASSES,
    PROVENANCE_SOURCES,
    VERIFICATION_STATUSES,
    active_lease_headroom_usd,
    allocate_lease_reservation,
    balance_is_fresh,
    cash_runway_days,
    is_no_spend_lease,
    is_settled_cash_claim,
    lease_is_active,
    lease_remaining_headroom_usd,
    single_lease_authorization_usd,
    spend_is_permitted,
    usable_authorized_budget_usd,
    validate_bucket,
    validate_ledger,
    validate_lease,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


def _bucket(ledger: dict, kind: str) -> dict:
    for bucket in ledger["buckets"]:
        if bucket["bucket_kind"] == kind:
            return bucket
    raise AssertionError(f"fixture has no bucket of kind {kind!r}")





class RestrictedCreditBucketIsolationTests(unittest.TestCase):
    def test_credit_class_cannot_pool_distinct_restricted_buckets(self) -> None:
        ledger = _load_fixture("valid_ledger.json")
        self.assertIsNone(
            allocate_lease_reservation(
                ledger["leases"], 1, "2026-10-14T01:00:00Z",
                funding_class="credit",
            )
        )



class ReservationFloatPrecisionTests(unittest.TestCase):
    def test_tiny_reservation_must_not_round_to_zero(self) -> None:
        self.assertIsNone(allocate_lease_reservation(
            [], Decimal("1e-400"), "2026-10-14T01:00:00Z"
        ))

    def test_nonrepresentable_reservation_must_not_round_down(self) -> None:
        self.assertIsNone(allocate_lease_reservation(
            [], Decimal("1.000000000000000000000000000001"),
            "2026-10-14T01:00:00Z"
        ))


class ExactLeaseConsumptionRegressionTests(unittest.TestCase):
    def test_tiny_verified_consumption_is_not_lost(self) -> None:
        lease = {
            "max_spend_usd": Decimal("1"),
            "consumed_usd": Decimal("0.000000000000000000000000000001"),
            "consumed_usd_provenance": "owner",
        }
        remaining = lease_remaining_headroom_usd(lease)
        self.assertIsNotNone(remaining)
        self.assertLess(Decimal.from_float(remaining), Decimal("1"))

    def test_override_cannot_erase_verified_consumption(self) -> None:
        lease = {
            "max_spend_usd": Decimal("10"),
            "consumed_usd": Decimal("9.99"),
            "consumed_usd_provenance": "owner",
        }
        self.assertIsNone(
            lease_remaining_headroom_usd(lease, consumed_usd=Decimal("0"))
        )
        self.assertEqual(
            lease_remaining_headroom_usd(lease, consumed_usd=Decimal("10")),
            0.0,
        )

    def test_tiny_overconsumption_does_not_create_headroom(self) -> None:
        lease = {
            "max_spend_usd": Decimal("1"),
            "consumed_usd": Decimal("1.000000000000000000000000000001"),
            "consumed_usd_provenance": "owner",
        }
        self.assertEqual(lease_remaining_headroom_usd(lease), 0.0)


class ExactSpendAuthorizationTests(unittest.TestCase):
    def test_subcent_request_cannot_round_to_zero(self) -> None:
        self.assertFalse(spend_is_permitted(
            Decimal("0.000000000000000000000000000001"),
            Decimal("1"), Decimal("0"), Decimal("0"), Decimal("0")
        )[0])

    def test_subcent_liability_must_not_disappear(self) -> None:
        self.assertFalse(spend_is_permitted(
            Decimal("1"), Decimal("1"), Decimal("0"),
            Decimal("0.000000000000000000000000000001"), Decimal("1")
        )[0])

    def test_exact_decimal_boundary_is_allowed(self) -> None:
        self.assertTrue(spend_is_permitted(
            Decimal("0.1"), Decimal("0.3"), Decimal("0.1"),
            Decimal("0.1"), Decimal("0.1")
        )[0])


class DuplicateLeaseIdFailClosedTests(unittest.TestCase):
    """A single lease identifier must never authorize twice."""

    def test_duplicate_lease_id_denies_allocation(self) -> None:
        ledger = _load_fixture("valid_ledger.json")
        lease = copy.deepcopy(ledger["leases"][0])
        lease["lease_id"] = "duplicated-authorization"
        lease["bucket_kind"] = "settled_cash"
        lease["max_spend_usd"] = 100
        lease["consumed_usd"] = 0
        lease["consumed_usd_provenance"] = "owner"
        lease["granted_at"] = "2026-10-14T00:00:00Z"
        lease["expires_at"] = "2026-10-14T03:00:00Z"
        lease["revoked"] = False
        leases = [lease, copy.deepcopy(lease)]
        self.assertIsNone(
            allocate_lease_reservation(
                leases, 150, "2026-10-14T01:00:00Z"
            )
        )

    def test_duplicate_lease_id_is_reported_by_ledger_validation(self) -> None:
        ledger = _load_fixture("valid_ledger.json")
        ledger["leases"].append(copy.deepcopy(ledger["leases"][0]))
        errors = validate_ledger(ledger)
        self.assertTrue(
            any("duplicate lease_id" in error for error in errors),
            errors,
        )

    def test_invalid_lease_id_denies_allocation(self) -> None:
        ledger = _load_fixture("valid_ledger.json")
        lease = copy.deepcopy(ledger["leases"][0])
        lease["lease_id"] = ""
        self.assertIsNone(
            allocate_lease_reservation([lease], 1, "2026-10-14T01:00:00Z")
        )


class ValidLedgerTests(unittest.TestCase):
    def test_valid_fixture_has_no_errors(self) -> None:
        ledger = _load_fixture("valid_ledger.json")
        self.assertEqual(validate_ledger(ledger), [])

    def test_valid_fixture_models_all_four_buckets(self) -> None:
        ledger = _load_fixture("valid_ledger.json")
        kinds = {b["bucket_kind"] for b in ledger["buckets"]}
        self.assertEqual(kinds, BUCKET_KINDS)

    def test_constants_match_expected_values(self) -> None:
        self.assertEqual(
            BUCKET_KINDS,
            {
                "aws_activate_credit",
                "aws_free_tier_credit",
                "kiro_subscription_credit",
                "settled_cash",
            },
        )
        self.assertEqual(FUNDING_CLASSES, {"credit", "cash"})
        self.assertEqual(PROVENANCE_SOURCES, {"spray", "provider", "topia", "owner"})
        self.assertEqual(
            VERIFICATION_STATUSES, {"unverified", "verified", "rejected"}
        )


class BucketValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")

    def test_each_bucket_validates(self) -> None:
        for bucket in self.ledger["buckets"]:
            with self.subTest(bucket=bucket["bucket_kind"]):
                self.assertEqual(validate_bucket(bucket), [])

    def test_unknown_bucket_kind_errors(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "aws_activate_credit"))
        bucket["bucket_kind"] = "mystery_credit"
        errors = validate_bucket(bucket)
        self.assertTrue(any("bucket_kind must be one of" in e for e in errors))

    def test_negative_balance_rejected(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["balance_usd"] = -1
        self.assertIn("balance_usd must be >= 0", validate_bucket(bucket))

    def test_bool_balance_rejected_as_non_number(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["balance_usd"] = True
        errors = validate_bucket(bucket)
        self.assertTrue(any("balance_usd must be a number" in e for e in errors))

    def test_nan_balance_rejected(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["balance_usd"] = float("nan")
        self.assertIn(
            "balance_usd must be a finite number, not NaN", validate_bucket(bucket)
        )

    def test_infinity_balance_rejected(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["balance_usd"] = float("inf")
        self.assertIn(
            "balance_usd must be a finite number, not Infinity",
            validate_bucket(bucket),
        )

    def test_missing_required_bucket_fields_reported(self) -> None:
        base = _bucket(self.ledger, "settled_cash")
        for field in (
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
        ):
            bucket = copy.deepcopy(base)
            del bucket[field]
            with self.subTest(field=field):
                errors = validate_bucket(bucket)
                self.assertIn(f"missing required bucket field: {field}", errors)

    def test_funding_class_must_match_bucket_kind(self) -> None:
        # A credit bucket cannot claim to be cash (credits are not spendable).
        bucket = copy.deepcopy(_bucket(self.ledger, "aws_activate_credit"))
        bucket["funding_class"] = "cash"
        errors = validate_bucket(bucket)
        self.assertTrue(
            any("funding_class for 'aws_activate_credit' must be" in e for e in errors)
        )

    def test_credit_bucket_requires_expiry(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "aws_activate_credit"))
        bucket["expiry"] = None
        errors = validate_bucket(bucket)
        self.assertTrue(any("credits expire" in e for e in errors))

    def test_settled_cash_may_have_null_expiry(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        self.assertIsNone(bucket["expiry"])
        self.assertEqual(validate_bucket(bucket), [])

    def test_non_dict_bucket_reported(self) -> None:
        errors = validate_bucket(["not", "a", "mapping"])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("bucket must be a JSON object"))

    def test_non_z_provider_update_time_rejected(self) -> None:
        # validate_bucket must apply the same normalized-UTC-'Z' strictness as
        # balance_is_fresh so a bucket cannot validate yet always be UNKNOWN to
        # the freshness guard.
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["provider_update_time"] = "2026-10-13T18:00:00+00:00"
        errors = validate_bucket(bucket)
        self.assertTrue(
            any(
                "provider_update_time must be a normalized UTC ISO-8601 string" in e
                for e in errors
            ),
            msg=f"expected normalized-timestamp error, got: {errors}",
        )

    def test_empty_provider_update_time_still_reports_non_empty_error(self) -> None:
        # The original non-empty-string check is preserved for the empty case.
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["provider_update_time"] = ""
        errors = validate_bucket(bucket)
        self.assertIn(
            "provider_update_time must be a non-empty ISO-8601 string", errors
        )

    def test_normalized_z_provider_update_time_accepted(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["provider_update_time"] = "2026-10-13T18:00:00Z"
        self.assertEqual(validate_bucket(bucket), [])


class UnhashableEnumValueTests(unittest.TestCase):
    """Membership tests must not raise on unhashable values; they must report
    an error instead."""

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")

    def test_provenance_as_list_reports_error_without_raising(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["provenance"] = ["owner"]
        errors = validate_bucket(bucket)
        self.assertTrue(any("provenance must be one of" in e for e in errors))

    def test_bucket_kind_as_dict_reports_error_without_raising(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["bucket_kind"] = {"kind": "settled_cash"}
        errors = validate_bucket(bucket)
        self.assertTrue(any("bucket_kind must be one of" in e for e in errors))

    def test_verification_as_list_reports_error_without_raising(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["verification"] = ["verified"]
        errors = validate_bucket(bucket)
        self.assertTrue(any("verification must be one of" in e for e in errors))


class ProjectedRevenueTests(unittest.TestCase):
    """Evidence-backed case (a): 'A projected sale is not money.'"""

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")
        self.cash = copy.deepcopy(_bucket(self.ledger, "settled_cash"))

    def test_projected_claim_marked_verified_is_rejected(self) -> None:
        bucket = copy.deepcopy(self.cash)
        bucket["settlement_state"] = "projected"
        bucket["verification"] = "verified"
        errors = validate_bucket(bucket)
        self.assertIn("projected revenue cannot be verified settled cash", errors)

    def test_forecast_and_simulated_and_pending_rejected(self) -> None:
        for state in ("forecast", "simulated", "pending"):
            bucket = copy.deepcopy(self.cash)
            bucket["settlement_state"] = state
            bucket["verification"] = "verified"
            with self.subTest(state=state):
                errors = validate_bucket(bucket)
                self.assertIn(
                    "projected revenue cannot be verified settled cash", errors
                )

    def test_projected_claim_in_settled_cash_is_rejected(self) -> None:
        bucket = copy.deepcopy(self.cash)
        bucket["settlement_state"] = "forecast"
        bucket["verification"] = "unverified"
        errors = validate_bucket(bucket)
        self.assertIn("projected revenue cannot be verified settled cash", errors)

    def test_settled_cash_requires_independent_provenance(self) -> None:
        bucket = copy.deepcopy(self.cash)
        bucket["settlement_state"] = "settled"
        bucket["verification"] = "verified"
        bucket["provenance"] = "spray"
        errors = validate_bucket(bucket)
        self.assertTrue(
            any("independent provenance" in e for e in errors),
            msg=f"expected independence error, got: {errors}",
        )
        self.assertFalse(is_settled_cash_claim(bucket))

    def test_same_claim_once_settled_and_verified_is_accepted(self) -> None:
        # The SAME claim, once settled + independently verified, is accepted.
        bucket = copy.deepcopy(self.cash)
        bucket["settlement_state"] = "settled"
        bucket["verification"] = "verified"
        bucket["provenance"] = "provider"
        self.assertEqual(validate_bucket(bucket), [])
        self.assertTrue(is_settled_cash_claim(bucket))

    def test_invalid_fixture_reports_projected_revenue_error(self) -> None:
        ledger = _load_fixture("invalid_ledger.json")
        errors = validate_ledger(ledger)
        self.assertTrue(
            any(
                "projected revenue cannot be verified settled cash" in e
                for e in errors
            ),
            msg=f"expected projected-revenue error, got: {errors}",
        )

    def test_invalid_fixture_reports_unknown_bucket_kind(self) -> None:
        ledger = _load_fixture("invalid_ledger.json")
        errors = validate_ledger(ledger)
        self.assertTrue(any("bucket_kind must be one of" in e for e in errors))


class NoSpendLeaseTests(unittest.TestCase):
    """Evidence-backed case (b): no-spend leases, owner revocation, and
    rejection of SPRAY self-granted/self-un-revoked leases."""

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")
        self.lease = copy.deepcopy(self.ledger["leases"][1])  # the no-spend lease

    def test_no_spend_lease_is_detected(self) -> None:
        self.assertTrue(is_no_spend_lease(self.lease))
        self.assertEqual(self.lease["max_spend_usd"], 0)

    def test_no_spend_lease_active_while_not_revoked_and_not_expired(self) -> None:
        # Before expiry (expires_at is 03:00:00Z) and not revoked -> active.
        self.assertTrue(lease_is_active(self.lease, "2026-10-14T01:00:00Z"))

    def test_lease_inactive_after_owner_revocation(self) -> None:
        revoked = copy.deepcopy(self.lease)
        revoked["revoked"] = True
        revoked["revoked_by"] = "owner"
        self.assertEqual(validate_lease(revoked), [])
        self.assertFalse(lease_is_active(revoked, "2026-10-14T01:00:00Z"))

    def test_lease_inactive_after_expiry(self) -> None:
        self.assertFalse(lease_is_active(self.lease, "2026-10-14T05:00:00Z"))

    def test_lease_inactive_on_unknown_now(self) -> None:
        self.assertFalse(lease_is_active(self.lease, ""))
        self.assertFalse(lease_is_active(self.lease, None))

    def test_spray_self_granted_lease_is_rejected(self) -> None:
        lease = copy.deepcopy(self.lease)
        lease["provenance"] = "spray"
        errors = validate_lease(lease)
        self.assertTrue(
            any("SPRAY cannot self-grant or self-un-revoke" in e for e in errors)
        )
        # A rejected lease must never be considered active.
        self.assertFalse(lease_is_active(lease, "2026-10-14T01:00:00Z"))

    def test_spray_authority_lease_is_rejected(self) -> None:
        lease = copy.deepcopy(self.lease)
        lease["authority"] = "spray"
        errors = validate_lease(lease)
        self.assertTrue(any("lease authority must be one of" in e for e in errors))

    def test_revoked_lease_without_independent_revoker_is_rejected(self) -> None:
        lease = copy.deepcopy(self.lease)
        lease["revoked"] = True
        lease["revoked_by"] = "spray"
        errors = validate_lease(lease)
        self.assertTrue(any("revoked_by" in e for e in errors))

    def test_invalid_fixture_lease_rejected(self) -> None:
        ledger = _load_fixture("invalid_ledger.json")
        errors = validate_ledger(ledger)
        self.assertTrue(
            any("self-grant or self-un-revoke" in e for e in errors),
            msg=f"expected self-grant lease error, got: {errors}",
        )


class SpendDecisionTests(unittest.TestCase):
    """Fail-closed spend decisions and runway helpers."""

    def test_spend_permitted_within_usable_budget(self) -> None:
        # Permitting a spend now requires BOTH sufficient usable funds AND an
        # active lease whose authorized limit covers the amount. The lease cap
        # is an authorization, not a funding source, so it must be supplied
        # explicitly; the default 0 would deny any positive spend.
        permitted, reason = spend_is_permitted(
            amount_usd=10,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=50,
        )
        self.assertTrue(permitted, msg=reason)

    def test_spend_denied_when_insufficient_budget(self) -> None:
        # Even with an ample lease authorization, insufficient verified funds
        # must deny: lease headroom is never added to cash.
        permitted, reason = spend_is_permitted(
            amount_usd=200,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=500,
        )
        self.assertFalse(permitted)
        self.assertIn("insufficient", reason)

    def test_spend_denied_when_input_unknown(self) -> None:
        for kwargs in (
            {"settled_cash_usd": "unknown"},
            {"protective_buffer_usd": None},
            {"contingent_liabilities_usd": "unknown"},
            {"amount_usd": None},
            {"active_lease_headroom_usd": "unknown"},
        ):
            base = {
                "amount_usd": 10,
                "settled_cash_usd": 150,
                "protective_buffer_usd": 25,
                "contingent_liabilities_usd": 12,
                "active_lease_headroom_usd": 50,
            }
            base.update(kwargs)
            with self.subTest(kwargs=kwargs):
                permitted, _ = spend_is_permitted(**base)
                self.assertFalse(permitted)

    def test_spend_denied_when_relying_on_provider_billing_only(self) -> None:
        permitted, reason = spend_is_permitted(
            amount_usd=1,
            settled_cash_usd=10000,
            protective_buffer_usd=0,
            contingent_liabilities_usd=0,
            rely_on_provider_billing_only=True,
        )
        self.assertFalse(permitted)
        self.assertIn("billing", reason)

    def test_regression_spend_denied_when_no_active_lease_even_if_cash(
        self,
    ) -> None:
        # REGRESSION (defect 1): a spend must be DENIED when there is no valid
        # active lease authorizing it, regardless of available cash. With the
        # default active_lease_headroom_usd of 0 (no active lease), any
        # positive spend fails closed even though usable funds (113) easily
        # cover the request. The old model permitted this purely on cash.
        permitted, reason = spend_is_permitted(
            amount_usd=10,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            # active_lease_headroom_usd defaults to 0: no active lease.
        )
        self.assertFalse(permitted, msg=reason)
        self.assertIn("lease", reason)

    def test_regression_lease_cap_not_treated_as_cash_to_top_up(self) -> None:
        # REGRESSION (defect 2): the lease authorization limit must never be
        # treated as money that tops up insufficient cash. usable = 113; a
        # request of 150 is NOT covered by funds. A lease cap of 100 (which the
        # old additive model would have combined to 213 >= 150 and permitted)
        # must NOT manufacture spendable budget -> DENY for insufficient funds.
        permitted, reason = spend_is_permitted(
            amount_usd=150,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=100,
        )
        self.assertFalse(permitted, msg=reason)
        self.assertIn("insufficient", reason)

    def test_lease_headroom_is_a_cap_not_additive_funds(self) -> None:
        # Corrected model: lease headroom is an authorization CAP, never added
        # to cash. usable = 150 - 25 - 12 = 113. A request of 120 exceeds the
        # 113 usable funds, so even with 20 of lease headroom it is DENIED
        # (the old model added 113 + 20 = 133 and wrongly permitted it).
        permitted, reason = spend_is_permitted(
            amount_usd=120,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=20,
        )
        self.assertFalse(permitted, msg=reason)

    def test_both_conditions_required_permits_only_when_both_hold(self) -> None:
        # usable = 113. A 100 request is covered by funds (113) and by a lease
        # cap of 100, so BOTH conditions hold -> permit.
        permitted, reason = spend_is_permitted(
            amount_usd=100,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=100,
        )
        self.assertTrue(permitted, msg=reason)

    def test_credits_are_never_counted_as_cash(self) -> None:
        # Only settled cash feeds usable budget; credits are excluded entirely.
        usable = usable_authorized_budget_usd(150, 25, 12)
        self.assertEqual(usable, 113)

    def test_usable_budget_unknown_on_unknown_input(self) -> None:
        self.assertIsNone(usable_authorized_budget_usd("unknown", 25, 12))

    def test_cash_runway_days(self) -> None:
        # usable = 150 - 25 - 12 = 113; burn 3.5/day -> ~32.28 days.
        runway = cash_runway_days(150, 3.5, 25, 12)
        self.assertIsNotNone(runway)
        self.assertAlmostEqual(runway, 113 / 3.5)

    def test_cash_runway_unknown_on_unknown_burn(self) -> None:
        self.assertIsNone(cash_runway_days(150, "unknown", 25, 12))
        self.assertIsNone(cash_runway_days(150, 0, 25, 12))

    def test_cash_runway_zero_when_no_usable_budget(self) -> None:
        self.assertEqual(cash_runway_days(10, 3.5, 25, 12), 0.0)


class LedgerValidationTests(unittest.TestCase):
    def test_non_dict_ledger_reported(self) -> None:
        errors = validate_ledger(["not", "a", "mapping"])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("ledger must be a JSON object"))

    def test_missing_buckets_reported(self) -> None:
        self.assertIn("missing required field: buckets", validate_ledger({}))

    def test_buckets_not_a_list_reported(self) -> None:
        errors = validate_ledger({"buckets": "nope"})
        self.assertTrue(any("buckets must be a list" in e for e in errors))

    def test_bucket_errors_are_indexed(self) -> None:
        ledger = _load_fixture("valid_ledger.json")
        ledger["buckets"][0]["balance_usd"] = -5
        errors = validate_ledger(ledger)
        self.assertTrue(any(e.startswith("buckets[0]:") for e in errors))

    def test_negative_forecast_inputs_rejected(self) -> None:
        ledger = _load_fixture("valid_ledger.json")
        ledger["protective_buffer_usd"] = -1
        self.assertTrue(
            any("protective_buffer_usd must be >= 0" in e for e in validate_ledger(ledger))
        )


class CreditProjectionWordingTests(unittest.TestCase):
    """Finding 4: the 'projected cannot be verified' error is bucket-aware.

    The settled_cash path keeps the exact legacy string; a credit bucket gets
    a message that does not misname its balance as 'settled cash'. Behavior is
    fail-closed in both cases.
    """

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")

    def test_settled_cash_keeps_exact_legacy_message(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["settlement_state"] = "projected"
        bucket["verification"] = "verified"
        errors = validate_bucket(bucket)
        self.assertIn("projected revenue cannot be verified settled cash", errors)

    def test_unsettled_verified_cash_emits_message_exactly_once(self) -> None:
        # An unsettled + verified settled_cash bucket once triggered three
        # overlapping branches that appended the identical string three times.
        # The collapsed logic emits it at most once.
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["settlement_state"] = "projected"
        bucket["verification"] = "verified"
        errors = validate_bucket(bucket)
        self.assertEqual(
            errors.count("projected revenue cannot be verified settled cash"),
            1,
            msg=f"message should appear exactly once, got: {errors}",
        )

    def test_unsettled_unverified_cash_emits_message_exactly_once(self) -> None:
        # An unsettled claim parked in settled_cash but not yet verified must
        # also emit the settled-cash phrase exactly once.
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["settlement_state"] = "forecast"
        bucket["verification"] = "unverified"
        errors = validate_bucket(bucket)
        self.assertEqual(
            errors.count("projected revenue cannot be verified settled cash"),
            1,
            msg=f"message should appear exactly once, got: {errors}",
        )

    def test_settled_cash_without_independent_provenance_still_flagged(self) -> None:
        # The independence bar for a settled + verified settled_cash bucket
        # must still fire after the branch collapse.
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["settlement_state"] = "settled"
        bucket["verification"] = "verified"
        bucket["provenance"] = "spray"
        errors = validate_bucket(bucket)
        self.assertTrue(
            any("independent provenance" in e for e in errors),
            msg=f"expected independence error, got: {errors}",
        )

    def test_credit_bucket_uses_non_cash_wording(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "aws_activate_credit"))
        bucket["settlement_state"] = "forecast"
        bucket["verification"] = "verified"
        errors = validate_bucket(bucket)
        # The misleading legacy phrase (which asserts the credit IS verified
        # settled cash) must NOT appear for a credit bucket.
        self.assertFalse(
            any("cannot be verified settled cash" in e for e in errors),
            msg=f"credit bucket should not use the settled-cash phrase: {errors}",
        )
        self.assertTrue(
            any(
                "projected revenue cannot be verified" in e
                and "is not settled cash" in e
                for e in errors
            ),
            msg=f"expected bucket-aware wording, got: {errors}",
        )


class BalanceFreshnessTests(unittest.TestCase):
    """Finding 1: an in-module freshness guard treats a stale/unknown
    provider_update_time as UNKNOWN and fails closed."""

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")
        self.bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        # Fixture provider_update_time is 2026-10-13T18:00:00Z.

    def test_fresh_balance_within_max_age(self) -> None:
        # 6 hours later, default max age is 24 hours -> fresh.
        self.assertTrue(balance_is_fresh(self.bucket, "2026-10-14T00:00:00Z"))

    def test_stale_balance_beyond_max_age_fails_closed(self) -> None:
        # 48 hours later -> stale -> UNKNOWN -> False.
        self.assertFalse(balance_is_fresh(self.bucket, "2026-10-15T18:00:01Z"))

    def test_custom_max_age_boundary(self) -> None:
        # Exactly one hour later with a one-hour budget is still fresh.
        self.assertTrue(
            balance_is_fresh(
                self.bucket, "2026-10-13T19:00:00Z", max_age_seconds=3600
            )
        )
        # One second past the one-hour budget is stale.
        self.assertFalse(
            balance_is_fresh(
                self.bucket, "2026-10-13T19:00:01Z", max_age_seconds=3600
            )
        )

    def test_future_provider_time_fails_closed(self) -> None:
        # A timestamp in the future (relative to now) is not trustworthy.
        self.assertFalse(balance_is_fresh(self.bucket, "2026-10-13T12:00:00Z"))

    def test_unknown_or_unparseable_now_fails_closed(self) -> None:
        self.assertFalse(balance_is_fresh(self.bucket, "unknown"))
        self.assertFalse(balance_is_fresh(self.bucket, None))
        self.assertFalse(balance_is_fresh(self.bucket, ""))

    def test_unparseable_provider_time_fails_closed(self) -> None:
        bucket = copy.deepcopy(self.bucket)
        bucket["provider_update_time"] = "not-a-timestamp"
        self.assertFalse(balance_is_fresh(bucket, "2026-10-14T00:00:00Z"))

    def test_non_z_offset_fails_closed(self) -> None:
        # A non-normalized offset must not be trusted even if recent.
        bucket = copy.deepcopy(self.bucket)
        bucket["provider_update_time"] = "2026-10-13T18:00:00+00:00"
        self.assertFalse(balance_is_fresh(bucket, "2026-10-14T00:00:00Z"))

    def test_unknown_max_age_fails_closed(self) -> None:
        self.assertFalse(
            balance_is_fresh(
                self.bucket, "2026-10-14T00:00:00Z", max_age_seconds="unknown"
            )
        )

    def test_default_max_age_constant(self) -> None:
        self.assertEqual(DEFAULT_BALANCE_MAX_AGE_SECONDS, 24 * 60 * 60)


class IsoTimestampGuardTests(unittest.TestCase):
    """Finding 3: lease expiry comparison is guarded against non-normalized
    timestamps instead of a raw lexicographic string compare."""

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")
        self.lease = copy.deepcopy(self.ledger["leases"][0])  # active lease

    def test_non_z_expires_at_rejected_by_validator(self) -> None:
        lease = copy.deepcopy(self.lease)
        lease["expires_at"] = "2026-10-14T06:00:00+00:00"
        errors = validate_lease(lease)
        self.assertTrue(
            any("normalized UTC ISO-8601 string" in e for e in errors),
            msg=f"expected normalized-timestamp error, got: {errors}",
        )

    def test_non_z_now_fails_closed(self) -> None:
        # A caller clock that is not normalized UTC 'Z' cannot be trusted.
        self.assertFalse(lease_is_active(self.lease, "2026-10-14T01:00:00+00:00"))

    def test_instant_comparison_not_lexicographic(self) -> None:
        # Both normalized 'Z'; compared as instants. 01:00 < 06:00 -> active.
        self.assertTrue(lease_is_active(self.lease, "2026-10-14T01:00:00Z"))
        # Differing precision must still order correctly as instants.
        self.assertTrue(lease_is_active(self.lease, "2026-10-14T01:00:00.500Z"))
        self.assertFalse(lease_is_active(self.lease, "2026-10-14T06:00:00.000Z"))

    def test_unparseable_expiry_fails_closed(self) -> None:
        lease = copy.deepcopy(self.lease)
        lease["expires_at"] = "garbage"
        self.assertFalse(lease_is_active(lease, "2026-10-14T01:00:00Z"))


class ActiveLeaseHeadroomTests(unittest.TestCase):
    """Finding 2: headroom fed to spend_is_permitted is derived only from
    leases that pass lease_is_active, closing the lease/spend loop.

    Item 2 update: the DEFAULT authorization figure is the MAX single-lease
    remaining headroom (single-covering-lease), NOT the sum across leases, so
    independent lease headrooms are not silently combined to clear a spend no
    single lease covers (see SingleLeaseAuthorizationTests). Summing is an
    explicit opt-in (combine_across_leases=True) only under the atomic
    allocation/reservation contract (see AtomicLeaseAllocationTests).
    """

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")
        self.leases = copy.deepcopy(self.ledger["leases"])
        # leases[0]: active, max_spend 20; leases[1]: no-spend (0), expires 03Z.

    def test_default_is_max_single_active_lease(self) -> None:
        # Before 03:00Z both leases are active; the DEFAULT returns the max of
        # a single lease (20 vs 0 = 20), not the sum. Here the no-spend lease
        # contributes 0, so max and sum coincide at 20.
        self.assertEqual(
            active_lease_headroom_usd(self.leases, "2026-10-14T01:00:00Z"), 20.0
        )
        # single_lease_authorization_usd is the same default figure.
        self.assertEqual(
            single_lease_authorization_usd(
                self.leases, "2026-10-14T01:00:00Z"
            ),
            20.0,
        )

    def test_revoked_lease_contributes_zero(self) -> None:
        leases = copy.deepcopy(self.leases)
        leases[0]["revoked"] = True
        leases[0]["revoked_by"] = "owner"
        # Only the no-spend lease remains active -> headroom 0.
        self.assertEqual(
            active_lease_headroom_usd(leases, "2026-10-14T01:00:00Z"), 0.0
        )

    def test_expired_lease_excluded(self) -> None:
        # After 06:00Z both leases are expired -> headroom 0.
        self.assertEqual(
            active_lease_headroom_usd(self.leases, "2026-10-14T07:00:00Z"), 0.0
        )

    def test_unknown_now_fails_closed(self) -> None:
        self.assertIsNone(active_lease_headroom_usd(self.leases, "unknown"))
        self.assertIsNone(active_lease_headroom_usd(self.leases, None))

    def test_non_list_leases_fails_closed(self) -> None:
        self.assertIsNone(
            active_lease_headroom_usd("not-a-list", "2026-10-14T01:00:00Z")
        )

    def test_headroom_from_revoked_lease_does_not_authorize_spend(self) -> None:
        # End-to-end: a revoked lease must NOT let a spend through.
        leases = copy.deepcopy(self.leases)
        # Give the first lease real headroom, then revoke it.
        leases[0]["max_spend_usd"] = 500.0
        leases[0]["revoked"] = True
        leases[0]["revoked_by"] = "owner"
        now = "2026-10-14T01:00:00Z"
        headroom = active_lease_headroom_usd(leases, now)
        # Revoked lease contributes nothing (only the no-spend lease is active).
        self.assertEqual(headroom, 0.0)
        permitted, _ = spend_is_permitted(
            amount_usd=200,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        # usable = 113, headroom 0, request 200 -> deny.
        self.assertFalse(permitted)

    def test_active_lease_headroom_gates_spend_but_is_not_cash(self) -> None:
        leases = copy.deepcopy(self.leases)
        leases[0]["max_spend_usd"] = 100.0
        now = "2026-10-14T01:00:00Z"
        headroom = active_lease_headroom_usd(leases, now)
        self.assertEqual(headroom, 100.0)
        # Corrected model: the lease cap (100) is NOT added to cash. A 200
        # request exceeds both the 113 usable funds and the 100 lease cap, so
        # it is DENIED (the old additive model wrongly allowed 113 + 100).
        permitted, reason = spend_is_permitted(
            amount_usd=200,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        self.assertFalse(permitted, msg=reason)

    def test_active_lease_headroom_permits_spend_within_both_limits(
        self,
    ) -> None:
        leases = copy.deepcopy(self.leases)
        leases[0]["max_spend_usd"] = 100.0
        now = "2026-10-14T01:00:00Z"
        headroom = active_lease_headroom_usd(leases, now)
        self.assertEqual(headroom, 100.0)
        # A 90 request is covered by both the 113 usable funds and the 100
        # active-lease authorization -> permit.
        permitted, reason = spend_is_permitted(
            amount_usd=90,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        self.assertTrue(permitted, msg=reason)


class LeaseIntervalTests(unittest.TestCase):
    """Gap 1: future-dated leases and invalid grant/expiry intervals.

    Structural ordering (expires_at > granted_at) is a clock-free
    validate_lease concern; "not yet started" (granted_at > now) is a
    clock-dependent lease_is_active concern. Both compare INSTANTS via
    _parse_utc_iso, never lexicographically.
    """

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")
        self.lease = copy.deepcopy(self.ledger["leases"][0])  # active cash lease

    def test_inverted_interval_rejected_by_validator(self) -> None:
        # expires_at strictly before granted_at: a lease that could never be
        # live. validate_lease must reject it with no clock involved.
        lease = copy.deepcopy(self.lease)
        lease["granted_at"] = "2026-10-14T06:00:00Z"
        lease["expires_at"] = "2026-10-14T00:00:00Z"
        errors = validate_lease(lease)
        self.assertTrue(
            any("strictly after granted_at" in e for e in errors),
            msg=f"expected inverted-interval error, got: {errors}",
        )

    def test_zero_length_interval_rejected(self) -> None:
        # expires_at == granted_at is a zero-length validity window -> reject.
        lease = copy.deepcopy(self.lease)
        lease["granted_at"] = "2026-10-14T00:00:00Z"
        lease["expires_at"] = "2026-10-14T00:00:00Z"
        errors = validate_lease(lease)
        self.assertTrue(
            any("zero-length lease validity interval" in e for e in errors),
            msg=f"expected zero-length-interval error, got: {errors}",
        )

    def test_valid_interval_still_validates(self) -> None:
        # A normal granted_at < expires_at lease must still validate cleanly.
        self.assertEqual(validate_lease(self.lease), [])

    def test_interval_compared_as_instants_not_lexicographically(self) -> None:
        # Differing precision: 05:00:00.000Z is lexicographically GREATER than
        # 05:00:00Z but the two are the same instant, so a lease granted at
        # 05:00:00.000Z and expiring at 06:00:00Z is a valid forward interval.
        lease = copy.deepcopy(self.lease)
        lease["granted_at"] = "2026-10-14T05:00:00.000Z"
        lease["expires_at"] = "2026-10-14T06:00:00Z"
        self.assertEqual(validate_lease(lease), [])

    def test_interval_error_not_piled_on_bad_timestamp(self) -> None:
        # When a timestamp is non-normalized, only the normalized-'Z' error is
        # emitted; the interval error must NOT be added on top (both must parse
        # before the interval check fires).
        lease = copy.deepcopy(self.lease)
        lease["expires_at"] = "2026-10-14T06:00:00+00:00"
        errors = validate_lease(lease)
        self.assertFalse(
            any("strictly after granted_at" in e for e in errors),
            msg=f"interval error should not fire on an unparseable ts: {errors}",
        )

    def test_future_dated_lease_is_inactive(self) -> None:
        # granted_at is in the future relative to now -> not yet started ->
        # inactive, the same fail-closed treatment as expiry.
        lease = copy.deepcopy(self.lease)
        lease["granted_at"] = "2026-10-14T05:00:00Z"
        lease["expires_at"] = "2026-10-14T09:00:00Z"
        # now (01:00Z) is before the grant instant.
        self.assertFalse(lease_is_active(lease, "2026-10-14T01:00:00Z"))

    def test_lease_active_only_at_or_after_granted_at(self) -> None:
        # The same lease is active once now reaches/passes granted_at and is
        # still before expiry.
        lease = copy.deepcopy(self.lease)
        lease["granted_at"] = "2026-10-14T05:00:00Z"
        lease["expires_at"] = "2026-10-14T09:00:00Z"
        # Exactly at the grant instant -> active.
        self.assertTrue(lease_is_active(lease, "2026-10-14T05:00:00Z"))
        # Comfortably inside the window -> active.
        self.assertTrue(lease_is_active(lease, "2026-10-14T07:00:00Z"))
        # At/after expiry -> inactive.
        self.assertFalse(lease_is_active(lease, "2026-10-14T09:00:00Z"))

    def test_future_dated_lease_contributes_zero_headroom(self) -> None:
        # End-to-end: a not-yet-started lease adds nothing to the authorization
        # headroom even though it is structurally valid.
        leases = copy.deepcopy(self.ledger["leases"])
        leases[0]["granted_at"] = "2026-10-14T05:00:00Z"
        leases[0]["expires_at"] = "2026-10-14T09:00:00Z"
        leases[0]["max_spend_usd"] = 500.0
        # leases[1] is the no-spend lease (expires 03Z): also active at 01Z but
        # contributes 0. The future-dated lease contributes 0 -> total 0.
        self.assertEqual(
            active_lease_headroom_usd(leases, "2026-10-14T01:00:00Z"), 0.0
        )


class CreditLeaseAuthorizationTests(unittest.TestCase):
    """Gap 2: a credit-bucket lease must not authorize cash spending.

    active_lease_headroom_usd counts only leases whose bucket_kind funding
    class is cash (settled_cash). A lease on a credit bucket contributes zero
    to the cash authorization limit; non-fungibility and fail-closed behavior
    are preserved.
    """

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")
        self.leases = copy.deepcopy(self.ledger["leases"])

    def test_credit_bucket_lease_contributes_zero_cash_headroom(self) -> None:
        leases = copy.deepcopy(self.leases)
        # Re-point the active lease at a credit bucket with real headroom.
        leases[0]["bucket_kind"] = "aws_activate_credit"
        leases[0]["max_spend_usd"] = 500.0
        self.assertEqual(validate_lease(leases[0]), [])
        # Only cash-funding-class leases count: the credit lease contributes 0
        # and the remaining cash lease is the no-spend lease (0) -> total 0.
        self.assertEqual(
            active_lease_headroom_usd(leases, "2026-10-14T01:00:00Z"), 0.0
        )

    def test_cash_bucket_lease_still_counts(self) -> None:
        # Control: the unchanged settled_cash lease still contributes its cap.
        self.assertEqual(
            active_lease_headroom_usd(self.leases, "2026-10-14T01:00:00Z"), 20.0
        )

    def test_credit_lease_does_not_authorize_cash_spend(self) -> None:
        # End-to-end: a credit-bucket lease, even with an enormous cap and
        # ample cash on hand, cannot authorize a settled-cash spend because it
        # contributes zero cash headroom.
        leases = copy.deepcopy(self.leases)
        leases[0]["bucket_kind"] = "aws_activate_credit"
        leases[0]["max_spend_usd"] = 10000.0
        # Drop the no-spend cash lease so no cash lease remains.
        leases = [leases[0]]
        headroom = active_lease_headroom_usd(leases, "2026-10-14T01:00:00Z")
        self.assertEqual(headroom, 0.0)
        permitted, reason = spend_is_permitted(
            amount_usd=10,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        # Ample cash (usable 113) but no CASH lease authorization -> deny.
        self.assertFalse(permitted, msg=reason)
        self.assertIn("lease", reason)

    def test_explicit_credit_funding_class_filter(self) -> None:
        # The optional funding_class filter can tally credit-lease headroom
        # separately; this proves non-fungibility is a split, not a loss.
        leases = copy.deepcopy(self.leases)
        leases[0]["bucket_kind"] = "aws_activate_credit"
        leases[0]["max_spend_usd"] = 500.0
        self.assertEqual(
            active_lease_headroom_usd(
                leases, "2026-10-14T01:00:00Z", funding_class="credit"
            ),
            500.0,
        )
        # And it is excluded from the default (cash) tally.
        self.assertEqual(
            active_lease_headroom_usd(leases, "2026-10-14T01:00:00Z"), 0.0
        )


class SingleLeaseAuthorizationTests(unittest.TestCase):
    """Item 2: the DEFAULT authorization requires a SINGLE covering lease.

    Independent lease headrooms are NOT silently summed to authorize one spend.
    single_lease_authorization_usd (and active_lease_headroom_usd by default,
    combine_across_leases=False) returns the MAX single-lease remaining
    headroom, so a spend is authorized only when ONE lease by itself covers it.
    Credit/cash non-fungibility and the both-conditions spend model are
    preserved; SPRAY does not enforce or self-debit. Summing is an explicit
    opt-in under the atomic allocation/reservation contract only (see
    AtomicLeaseAllocationTests).
    """

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")
        self.now = "2026-10-14T01:00:00Z"
        # Two independent active cash leases. Each has an attested consumed_usd
        # 0.0 with independent (owner) provenance, so each is fully usable.
        base = copy.deepcopy(self.ledger["leases"][0])
        lease_a = copy.deepcopy(base)
        lease_a["lease_id"] = "lease-split-a"
        lease_a["max_spend_usd"] = 6.0
        lease_b = copy.deepcopy(base)
        lease_b["lease_id"] = "lease-split-b"
        lease_b["max_spend_usd"] = 7.0
        self.split_leases = [lease_a, lease_b]

    def test_default_returns_max_single_lease_not_sum(self) -> None:
        # Two leases of 6 and 7: the DEFAULT is 7 (the single largest), NOT the
        # sum 13.
        self.assertEqual(
            single_lease_authorization_usd(self.split_leases, self.now), 7.0
        )
        self.assertEqual(
            active_lease_headroom_usd(self.split_leases, self.now), 7.0
        )

    def test_joint_only_spend_is_denied_by_default(self) -> None:
        # NEGATIVE regression: several independent active cash leases that
        # individually do NOT cover a 10 spend, but whose SUM (13) would, must
        # NOT authorize it under the default single-covering-lease path.
        headroom = active_lease_headroom_usd(self.split_leases, self.now)
        self.assertEqual(headroom, 7.0)
        permitted, reason = spend_is_permitted(
            amount_usd=10,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        # usable = 113 (ample cash), but no single lease covers 10 -> DENY.
        self.assertFalse(permitted, msg=reason)
        self.assertIn("lease", reason)

    def test_single_covering_lease_authorizes_spend(self) -> None:
        # POSITIVE regression: when ONE lease by itself covers the spend, the
        # default path authorizes it.
        leases = copy.deepcopy(self.split_leases)
        leases[0]["max_spend_usd"] = 10.0  # a single lease now covers 10
        headroom = active_lease_headroom_usd(leases, self.now)
        self.assertEqual(headroom, 10.0)
        permitted, reason = spend_is_permitted(
            amount_usd=10,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        self.assertTrue(permitted, msg=reason)

    def test_default_still_fails_closed_on_unknown_clock(self) -> None:
        self.assertIsNone(
            single_lease_authorization_usd(self.split_leases, "unknown")
        )
        self.assertIsNone(active_lease_headroom_usd(self.split_leases, None))

    def test_default_fails_closed_on_unverified_consumption(self) -> None:
        # A counted active lease with unknown/unverified consumed-to-date makes
        # the default figure fail closed (None), exactly like the sum path did.
        leases = copy.deepcopy(self.split_leases)
        del leases[0]["consumed_usd"]
        del leases[0]["consumed_usd_provenance"]
        self.assertIsNone(active_lease_headroom_usd(leases, self.now))
        self.assertIsNone(
            single_lease_authorization_usd(leases, self.now)
        )

    def test_credit_lease_contributes_zero_to_single_authorization(self) -> None:
        # Non-fungibility preserved: a credit-bucket lease, however large,
        # contributes zero to the cash single-lease authorization.
        leases = copy.deepcopy(self.split_leases)
        leases[0]["bucket_kind"] = "aws_activate_credit"
        leases[0]["max_spend_usd"] = 1000.0
        self.assertEqual(validate_lease(leases[0]), [])
        # Only the 7 cash lease counts toward the cash authorization.
        self.assertEqual(
            single_lease_authorization_usd(leases, self.now), 7.0
        )

    def test_no_active_cash_lease_yields_zero(self) -> None:
        # With no active cash lease the default is 0.0 (no authorization), so a
        # positive spend is denied.
        self.assertEqual(
            single_lease_authorization_usd([], self.now), 0.0
        )


class AtomicLeaseAllocationTests(unittest.TestCase):
    """Item 2: the EXPLICIT atomic allocation/reservation contract.

    allocate_lease_reservation is the ONLY sanctioned way multiple leases may
    JOINTLY authorize one spend. When the active, correct-funding-class leases
    can jointly cover the amount it returns a covered reservation NAMING the
    contributing lease_ids and the per-lease reserved/debited amounts; it fails
    closed otherwise. SPRAY does NOT enforce or self-debit: the reservation is a
    representation of what the external, owner-controlled control plane would
    atomically reserve/debit under a per-request idempotency key.
    """

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")
        self.now = "2026-10-14T01:00:00Z"
        base = copy.deepcopy(self.ledger["leases"][0])
        lease_a = copy.deepcopy(base)
        lease_a["lease_id"] = "lease-split-a"
        lease_a["max_spend_usd"] = 6.0
        lease_b = copy.deepcopy(base)
        lease_b["lease_id"] = "lease-split-b"
        lease_b["max_spend_usd"] = 7.0
        self.split_leases = [lease_a, lease_b]

    def test_jointly_covering_reservation_names_contributing_leases(self) -> None:
        # POSITIVE: 6 + 7 jointly cover a 10 spend. The reservation names the
        # contributing lease_ids and the per-lease reserved amounts, totalling
        # exactly the requested amount.
        reservation = allocate_lease_reservation(self.split_leases, 10, self.now)
        self.assertIsNotNone(reservation)
        self.assertTrue(reservation["covered"])
        self.assertEqual(reservation["amount_usd"], 10.0)
        self.assertEqual(reservation["reserved_usd"], 10.0)
        allocations = dict(reservation["allocations"])
        # Both leases are named as contributors.
        self.assertEqual(set(allocations), {"lease-split-a", "lease-split-b"})
        # Per-lease reserved amounts never exceed each lease's remaining
        # headroom and sum to the requested amount.
        self.assertLessEqual(allocations["lease-split-a"], 6.0)
        self.assertLessEqual(allocations["lease-split-b"], 7.0)
        self.assertEqual(
            sum(amt for _lid, amt in reservation["allocations"]), 10.0
        )

    def test_single_lease_sufficient_reservation_names_one_lease(self) -> None:
        # When one lease covers the amount, only that lease is named.
        reservation = allocate_lease_reservation(self.split_leases, 5, self.now)
        self.assertIsNotNone(reservation)
        self.assertTrue(reservation["covered"])
        self.assertEqual(len(reservation["allocations"]), 1)
        # The largest lease (7) is drawn from first.
        self.assertEqual(reservation["allocations"][0][0], "lease-split-b")
        self.assertEqual(reservation["allocations"][0][1], 5.0)

    def test_not_jointly_covered_fails_closed(self) -> None:
        # NEGATIVE: 6 + 7 = 13 cannot cover a 20 spend -> fail closed (None),
        # no partial reservation.
        self.assertIsNone(
            allocate_lease_reservation(self.split_leases, 20, self.now)
        )

    def test_unknown_clock_fails_closed(self) -> None:
        self.assertIsNone(
            allocate_lease_reservation(self.split_leases, 5, "unknown")
        )

    def test_malformed_lease_list_fails_closed(self) -> None:
        self.assertIsNone(
            allocate_lease_reservation("not-a-list", 5, self.now)
        )

    def test_invalid_amount_fails_closed(self) -> None:
        for bad in ("unknown", -1, True, float("nan"), float("inf"), None):
            with self.subTest(amount=bad):
                self.assertIsNone(
                    allocate_lease_reservation(self.split_leases, bad, self.now)
                )

    def test_unverified_consumption_candidate_fails_closed(self) -> None:
        # If any candidate active lease has unknown/unverified consumed-to-date,
        # the whole reservation fails closed (we cannot read a contributor).
        leases = copy.deepcopy(self.split_leases)
        del leases[0]["consumed_usd"]
        del leases[0]["consumed_usd_provenance"]
        self.assertIsNone(allocate_lease_reservation(leases, 5, self.now))

    def test_credit_lease_excluded_from_cash_reservation(self) -> None:
        # Non-fungibility: a credit-bucket lease cannot contribute to a cash
        # reservation, so if only a 7 cash lease remains, a 10 spend fails
        # closed even with a huge credit lease present.
        leases = copy.deepcopy(self.split_leases)
        leases[0]["bucket_kind"] = "aws_activate_credit"
        leases[0]["max_spend_usd"] = 1000.0
        self.assertEqual(validate_lease(leases[0]), [])
        self.assertIsNone(allocate_lease_reservation(leases, 10, self.now))
        # But a 5 spend is covered by the single cash lease alone.
        reservation = allocate_lease_reservation(leases, 5, self.now)
        self.assertIsNotNone(reservation)
        self.assertEqual(
            [lid for lid, _amt in reservation["allocations"]], ["lease-split-b"]
        )

    def test_explicit_combine_matches_reservation_capacity(self) -> None:
        # The explicit opt-in sum (combine_across_leases=True) represents the
        # same joint capacity the atomic reservation draws on: 6 + 7 = 13.
        self.assertEqual(
            active_lease_headroom_usd(
                self.split_leases, self.now, combine_across_leases=True
            ),
            13.0,
        )


class LeaseConsumptionTests(unittest.TestCase):
    """Gap 3 + Item 1: repeated spend against an unchanged cap is bounded by
    cumulative consumption accounting, and UNKNOWN consumption FAILS CLOSED.

    lease_remaining_headroom_usd computes cap minus consumed-to-date (never
    negative), and active_lease_headroom_usd threads REMAINING headroom (not
    the raw cap) into the authorization path so a fully consumed lease
    authorizes nothing further. consumed_usd now counts ONLY when it is
    independently verified: a finite, non-negative figure carrying a
    consumed_usd_provenance in INDEPENDENT_PROVENANCE ({provider, topia,
    owner}). An absent/unknown/self-reported consumed figure is NOT treated as
    0.0 consumed; it fails closed (lease_remaining_headroom_usd returns None,
    the lease contributes a denying None to active_lease_headroom_usd, and the
    end-to-end spend is DENIED). An attested 0.0 with independent provenance is
    the explicit "zero consumed so far" figure. These SPRAY-side helpers are a
    representation plus fail-closed check of the externally maintained figures;
    SPRAY does not enforce or self-debit (see docs Section 4).
    """

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")
        # cap 20, cash, attested consumed_usd 0.0 with independent (owner)
        # consumed_usd_provenance.
        self.lease = copy.deepcopy(self.ledger["leases"][0])

    def test_remaining_headroom_is_cap_minus_consumed(self) -> None:
        lease = copy.deepcopy(self.lease)
        lease["max_spend_usd"] = 100.0
        lease["consumed_usd"] = 30.0  # provenance owner retained from fixture
        self.assertEqual(lease_remaining_headroom_usd(lease), 70.0)

    def test_absent_consumed_fails_closed(self) -> None:
        # Item 1 NEGATIVE regression (replaces the old back-compat
        # test_absent_consumed_is_zero_consumed): a lease whose consumed-to-date
        # is UNKNOWN (no consumed_usd at all) must NOT be treated as fully
        # unconsumed. lease_remaining_headroom_usd fails closed (None), the
        # lease contributes a denying None to active_lease_headroom_usd, and the
        # end-to-end positive spend is DENIED even with ample cash.
        lease = copy.deepcopy(self.lease)
        del lease["consumed_usd"]
        del lease["consumed_usd_provenance"]
        self.assertNotIn("consumed_usd", lease)
        self.assertIsNone(lease_remaining_headroom_usd(lease))
        # The unknown-consumption lease is still structurally valid and active,
        # so it is COUNTED, and its None headroom makes the tally fail closed.
        self.assertEqual(validate_lease(lease), [])
        self.assertTrue(lease_is_active(lease, "2026-10-14T01:00:00Z"))
        headroom = active_lease_headroom_usd([lease], "2026-10-14T01:00:00Z")
        self.assertIsNone(headroom)
        permitted, reason = spend_is_permitted(
            amount_usd=1,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        self.assertFalse(permitted, msg=reason)
        self.assertIn("lease headroom is unknown", reason)

    def test_attested_zero_consumed_authorizes_full_cap(self) -> None:
        # Item 1 POSITIVE regression: an explicitly attested 0.0 consumed with
        # independent provenance is a valid "zero consumed so far" figure and
        # authorizes exactly the full cap, and a spend within it is permitted.
        lease = copy.deepcopy(self.lease)
        lease["max_spend_usd"] = 20.0
        lease["consumed_usd"] = 0.0
        lease["consumed_usd_provenance"] = "owner"
        self.assertEqual(lease_remaining_headroom_usd(lease), 20.0)
        headroom = active_lease_headroom_usd([lease], "2026-10-14T01:00:00Z")
        self.assertEqual(headroom, 20.0)
        permitted, reason = spend_is_permitted(
            amount_usd=15,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        self.assertTrue(permitted, msg=reason)

    def test_verified_consumed_authorizes_only_the_remainder(self) -> None:
        # Item 1 POSITIVE regression: an independently-verified consumed figure
        # yields cap-minus-consumed remaining headroom.
        lease = copy.deepcopy(self.lease)
        lease["max_spend_usd"] = 50.0
        lease["consumed_usd"] = 45.0
        lease["consumed_usd_provenance"] = "provider"
        self.assertEqual(lease_remaining_headroom_usd(lease), 5.0)

    def test_self_reported_consumed_is_not_verified(self) -> None:
        # Item 1 NEGATIVE regression: a consumed figure self-reported by SPRAY
        # ('spray' provenance) is NOT independently verified. It is a hard
        # validate_lease error, so the lease is inactive and contributes
        # nothing; even standalone, lease_remaining_headroom_usd fails closed.
        lease = copy.deepcopy(self.lease)
        lease["max_spend_usd"] = 100.0
        lease["consumed_usd"] = 10.0
        lease["consumed_usd_provenance"] = "spray"
        self.assertIsNone(lease_remaining_headroom_usd(lease))
        errors = validate_lease(lease)
        self.assertTrue(
            any("consumed_usd_provenance must be independently" in e for e in errors),
            msg=f"expected self-reported consumption error, got: {errors}",
        )

    def test_consumed_without_provenance_fails_closed(self) -> None:
        # Item 1 NEGATIVE regression: a consumed figure with NO provenance
        # sibling is not independently verified. It is NOT a hard validate_lease
        # error (the lease stays active), but lease_remaining_headroom_usd fails
        # closed (None), so the counted lease denies the spend via a None tally
        # even with ample cash.
        lease = copy.deepcopy(self.lease)
        lease["max_spend_usd"] = 100.0
        lease["consumed_usd"] = 10.0
        del lease["consumed_usd_provenance"]
        self.assertEqual(validate_lease(lease), [])
        self.assertTrue(lease_is_active(lease, "2026-10-14T01:00:00Z"))
        self.assertIsNone(lease_remaining_headroom_usd(lease))
        headroom = active_lease_headroom_usd([lease], "2026-10-14T01:00:00Z")
        self.assertIsNone(headroom)
        permitted, reason = spend_is_permitted(
            amount_usd=5,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        self.assertFalse(permitted, msg=reason)

    def test_unverified_consumed_provenance_rejected_by_validator(self) -> None:
        # A consumed_usd_provenance that is not a recognized source is a hard
        # validate_lease error.
        lease = copy.deepcopy(self.lease)
        lease["consumed_usd"] = 1.0
        lease["consumed_usd_provenance"] = "galactic"
        errors = validate_lease(lease)
        self.assertTrue(
            any("consumed_usd_provenance must be one of" in e for e in errors),
            msg=f"expected provenance-enum error, got: {errors}",
        )

    def test_invalid_consumed_fixture_lease_rejected(self) -> None:
        # The invalid fixture models a lease whose consumed_usd is self-reported
        # ('spray'); validate_ledger must reject it.
        ledger = _load_fixture("invalid_ledger.json")
        errors = validate_ledger(ledger)
        self.assertTrue(
            any("consumed_usd_provenance must be independently" in e for e in errors),
            msg=f"expected self-reported consumption error, got: {errors}",
        )

    def test_fully_consumed_lease_contributes_zero_headroom(self) -> None:
        lease = copy.deepcopy(self.lease)
        lease["max_spend_usd"] = 20.0
        lease["consumed_usd"] = 20.0
        self.assertEqual(lease_remaining_headroom_usd(lease), 0.0)

    def test_consumed_exceeding_cap_is_zero_not_negative(self) -> None:
        # Over-consumption clamps to 0.0; it never yields negative headroom
        # (which would wrongly reduce another lease's contribution in a sum).
        lease = copy.deepcopy(self.lease)
        lease["max_spend_usd"] = 20.0
        lease["consumed_usd"] = 35.0
        self.assertEqual(lease_remaining_headroom_usd(lease), 0.0)

    def test_unknown_consumed_fails_closed(self) -> None:
        # An explicitly present but unknown/invalid consumed figure => None.
        for bad in ("unknown", -1, True, float("nan"), float("inf")):
            lease = copy.deepcopy(self.lease)
            lease["consumed_usd"] = bad
            with self.subTest(consumed=bad):
                self.assertIsNone(lease_remaining_headroom_usd(lease))

    def test_unusable_cap_fails_closed(self) -> None:
        lease = copy.deepcopy(self.lease)
        lease["max_spend_usd"] = "unknown"
        self.assertIsNone(lease_remaining_headroom_usd(lease))

    def test_invalid_consumed_rejected_by_validator(self) -> None:
        # validate_lease rejects an explicitly present invalid consumed_usd.
        lease = copy.deepcopy(self.lease)
        lease["consumed_usd"] = -5
        errors = validate_lease(lease)
        self.assertIn("consumed_usd must be >= 0", errors)

    def test_valid_consumed_accepted_by_validator(self) -> None:
        lease = copy.deepcopy(self.lease)
        lease["consumed_usd"] = 5.0
        self.assertEqual(validate_lease(lease), [])

    def test_headroom_reflects_remaining_not_raw_cap(self) -> None:
        # active_lease_headroom_usd reports REMAINING headroom (cap minus
        # consumed-to-date), not the raw cap. The DEFAULT single-covering-lease
        # path returns the max single lease's remaining headroom.
        leases = copy.deepcopy(self.ledger["leases"])
        leases[0]["max_spend_usd"] = 100.0
        leases[0]["consumed_usd"] = 75.0  # 25 remaining
        # leases[1] is the no-spend lease (0). Max single remaining = 25.
        self.assertEqual(
            active_lease_headroom_usd(leases, "2026-10-14T01:00:00Z"), 25.0
        )

    def test_fully_consumed_cap_cannot_reauthorize_further_spend(self) -> None:
        # End-to-end (the core Gap 3 defect): a lease whose consumed_usd has
        # reached its cap must no longer authorize ANY further positive spend,
        # even though the cap value itself is unchanged and cash is ample.
        leases = copy.deepcopy(self.ledger["leases"])
        leases[0]["max_spend_usd"] = 50.0
        leases[0]["consumed_usd"] = 50.0  # fully consumed
        leases = [leases[0]]  # drop the no-spend lease
        headroom = active_lease_headroom_usd(leases, "2026-10-14T01:00:00Z")
        self.assertEqual(headroom, 0.0)
        permitted, reason = spend_is_permitted(
            amount_usd=1,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        self.assertFalse(permitted, msg=reason)
        self.assertIn("lease", reason)

    def test_partially_consumed_cap_authorizes_only_the_remainder(self) -> None:
        # A lease with cap 50 and 45 consumed authorizes a 5 spend but denies a
        # 10 spend: the unchanged cap cannot re-authorize beyond the remainder.
        leases = copy.deepcopy(self.ledger["leases"])
        leases[0]["max_spend_usd"] = 50.0
        leases[0]["consumed_usd"] = 45.0  # 5 remaining
        leases = [leases[0]]
        headroom = active_lease_headroom_usd(leases, "2026-10-14T01:00:00Z")
        self.assertEqual(headroom, 5.0)
        ok, _ = spend_is_permitted(
            amount_usd=5,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        self.assertTrue(ok)
        denied, _ = spend_is_permitted(
            amount_usd=10,
            settled_cash_usd=150,
            protective_buffer_usd=25,
            contingent_liabilities_usd=12,
            active_lease_headroom_usd=headroom,
        )
        self.assertFalse(denied)

    def test_invalid_consumed_lease_fails_closed_and_contributes_nothing(
        self,
    ) -> None:
        # A lease carrying an invalid consumed_usd does not validate, so
        # lease_is_active rejects it up front and it contributes NOTHING to the
        # headroom (it is excluded, exactly like a revoked/expired lease). The
        # invalid consumed value can never silently authorize spend. Here only
        # the no-spend lease remains active -> total 0.0, not the lease's cap.
        leases = copy.deepcopy(self.ledger["leases"])
        leases[0]["max_spend_usd"] = 500.0
        leases[0]["consumed_usd"] = "unknown"
        self.assertNotEqual(validate_lease(leases[0]), [])
        self.assertFalse(lease_is_active(leases[0], "2026-10-14T01:00:00Z"))
        self.assertEqual(
            active_lease_headroom_usd(leases, "2026-10-14T01:00:00Z"), 0.0
        )


class FixtureSecretSafetyTests(unittest.TestCase):
    def test_no_fixture_matches_credential_patterns(self) -> None:
        fixture_files = sorted(FIXTURES_DIR.glob("*"))
        self.assertTrue(fixture_files, "expected at least one fixture file")
        for fixture in fixture_files:
            if not fixture.is_file():
                continue
            text = fixture.read_text(encoding="utf-8", errors="replace")
            for name, pattern in CREDENTIAL_PATTERNS:
                with self.subTest(fixture=fixture.name, pattern=name):
                    self.assertIsNone(
                        pattern.search(text),
                        msg=(
                            f"fixture {fixture.name} matched credential "
                            f"pattern '{name}'"
                        ),
                    )


class NonJsonNumericEdgeCaseTests(unittest.TestCase):
    """Hardening for non-JSON Python numeric edge cases (PR #6 parity).

    JSON can only express int/float, but the validators and decision helpers
    accept arbitrary Python objects. Three real-number-ish values historically
    slipped through the ``isinstance(value, float)``-scoped NaN/Infinity guards
    and the bare ``numbers.Number`` real-check:

      1. ``complex(1, 2)`` is a ``numbers.Number`` but NOT a ``numbers.Real``;
         it is not a ``float`` so NaN/Inf checks were skipped, and ``value < 0``
         raised ``TypeError`` because complex numbers are unordered.
      2. ``Decimal('NaN')`` is a ``numbers.Real`` but not a ``float``, so
         ``math.isnan`` was never applied and a later ``value < 0`` raised
         ``decimal.InvalidOperation``.
      3. ``Decimal('Infinity')`` is likewise not a ``float``, so the Inf guard
         was skipped and it was wrongly ACCEPTED as a finite number.

    Every case must be rejected (validator paths) / fail closed (decision
    paths) WITHOUT raising. Valid finite inputs - including a finite Decimal -
    must still validate.
    """

    def setUp(self) -> None:
        self.ledger = _load_fixture("valid_ledger.json")

    # --- validator (error-returning) paths ------------------------------

    def test_complex_balance_returns_error_without_raising(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["balance_usd"] = complex(1, 2)
        errors = validate_bucket(bucket)  # must not raise TypeError
        self.assertTrue(
            any("balance_usd must be a number" in e for e in errors),
            msg=f"expected non-number error, got: {errors}",
        )

    def test_decimal_nan_balance_returns_error_without_raising(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["balance_usd"] = Decimal("NaN")
        errors = validate_bucket(bucket)  # must not raise InvalidOperation
        self.assertIn(
            "balance_usd must be a finite number, not NaN", errors
        )

    def test_decimal_infinity_balance_rejected(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["balance_usd"] = Decimal("Infinity")
        errors = validate_bucket(bucket)
        self.assertIn(
            "balance_usd must be a finite number, not Infinity", errors
        )

    def test_lease_max_spend_edge_cases_rejected_without_raising(self) -> None:
        base = copy.deepcopy(self.ledger["leases"][0])
        for bad in (complex(1, 2), Decimal("NaN"), Decimal("Infinity")):
            lease = copy.deepcopy(base)
            lease["max_spend_usd"] = bad
            with self.subTest(bad=repr(bad)):
                errors = validate_lease(lease)  # must not raise
                self.assertTrue(
                    any(
                        e.startswith("max_spend_usd must be a number")
                        or e.startswith("max_spend_usd must be a finite number")
                        for e in errors
                    ),
                    msg=f"expected max_spend_usd rejection, got: {errors}",
                )

    def test_validate_ledger_rejects_decimal_infinity_balance(self) -> None:
        ledger = copy.deepcopy(self.ledger)
        _bucket(ledger, "settled_cash")["balance_usd"] = Decimal("Infinity")
        errors = validate_ledger(ledger)  # must not raise
        self.assertTrue(
            any(
                "balance_usd must be a finite number, not Infinity" in e
                for e in errors
            ),
            msg=f"expected Decimal Infinity rejection, got: {errors}",
        )

    def test_finite_decimal_balance_still_validates(self) -> None:
        # A finite Decimal is a legitimate real number and must be accepted,
        # exactly like an int/float of the same value.
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["balance_usd"] = Decimal("1234.56")
        self.assertEqual(validate_bucket(bucket), [])

    def test_finite_decimal_zero_balance_still_validates(self) -> None:
        bucket = copy.deepcopy(_bucket(self.ledger, "settled_cash"))
        bucket["balance_usd"] = Decimal("0")
        self.assertEqual(validate_bucket(bucket), [])

    # --- decision (fail-closed) paths -----------------------------------

    def test_spend_fails_closed_on_edge_case_inputs_without_raising(self) -> None:
        for bad in (complex(1, 2), Decimal("NaN"), Decimal("Infinity")):
            for field in (
                "settled_cash_usd",
                "protective_buffer_usd",
                "contingent_liabilities_usd",
                "amount_usd",
                "active_lease_headroom_usd",
            ):
                base = {
                    "amount_usd": 10,
                    "settled_cash_usd": 150,
                    "protective_buffer_usd": 25,
                    "contingent_liabilities_usd": 12,
                    "active_lease_headroom_usd": 50,
                }
                base[field] = bad
                with self.subTest(bad=repr(bad), field=field):
                    permitted, _ = spend_is_permitted(**base)  # must not raise
                    self.assertFalse(permitted)

    def test_usable_authorized_budget_fails_closed_on_edge_cases(self) -> None:
        for bad in (complex(1, 2), Decimal("NaN"), Decimal("Infinity")):
            with self.subTest(bad=repr(bad)):
                self.assertIsNone(
                    usable_authorized_budget_usd(bad, 0, 0)  # must not raise
                )
                self.assertIsNone(usable_authorized_budget_usd(150, bad, 0))
                self.assertIsNone(usable_authorized_budget_usd(150, 0, bad))

    def test_usable_authorized_budget_accepts_finite_decimal(self) -> None:
        # Finite Decimal inputs flow through the same fail-closed helper and
        # must yield the correct spendable budget (150 - 25 - 12 = 113).
        self.assertEqual(
            usable_authorized_budget_usd(
                Decimal("150"), Decimal("25"), Decimal("12")
            ),
            113.0,
        )

    def test_is_no_spend_lease_fails_closed_on_edge_cases(self) -> None:
        base = copy.deepcopy(self.ledger["leases"][1])  # the no-spend lease
        for bad in (complex(1, 2), Decimal("NaN"), Decimal("Infinity")):
            lease = copy.deepcopy(base)
            lease["max_spend_usd"] = bad
            with self.subTest(bad=repr(bad)):
                self.assertFalse(is_no_spend_lease(lease))  # must not raise

    def test_is_no_spend_lease_accepts_finite_decimal_zero(self) -> None:
        lease = copy.deepcopy(self.ledger["leases"][1])
        lease["max_spend_usd"] = Decimal("0")
        self.assertTrue(is_no_spend_lease(lease))

    def test_no_edge_case_raises_decimal_invalid_operation(self) -> None:
        # Explicit guard that the Decimal NaN path never leaks the arithmetic
        # exception the reviewer flagged.
        try:
            validate_bucket(
                {**_bucket(self.ledger, "settled_cash"), "balance_usd": Decimal("NaN")}
            )
            usable_authorized_budget_usd(Decimal("NaN"), 0, 0)
            is_no_spend_lease({"max_spend_usd": Decimal("NaN")})
        except decimal.InvalidOperation as exc:  # pragma: no cover - defensive
            self.fail(f"Decimal('NaN') leaked InvalidOperation: {exc}")
        except TypeError as exc:  # pragma: no cover - defensive
            self.fail(f"edge-case numeric guard raised TypeError: {exc}")


if __name__ == "__main__":
    unittest.main()
