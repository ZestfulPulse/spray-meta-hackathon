"""Offline unit tests for credit-only Meta budget enforcement.

These tests exercise spray_schema.credit_budget (a thin additive layer over
spray_schema.resource_ledger) and prove the Meta adapter seam short-circuits a
denied authorization BEFORE any transport send. Every test is pure stdlib and
offline; the Meta-adapter test injects a FAKE opener that must never be called
when authorization denies.

Boundary semantics under test (matching the module docstring):
* caps are inclusive ceilings compared with ``>`` (strictly over is denied);
* per-call cost of exactly USD 20 is allowed, USD 20.01 denied;
* lifetime spent + worst-case of exactly USD 140 is allowed, one cent over
  denied;
* credit expiry is at-or-after (``>=``): exactly 2026-11-05T02:00:00Z is denied.
"""
import unittest
from decimal import Decimal

from spray_schema import resource_ledger
from spray_schema.credit_budget import (
    CASH_BUCKET_USD,
    CREDIT_EXPIRY_ISO,
    LIFETIME_HARD_STOP_USD,
    R2_PER_CALL_CAP_USD,
    authorize_meta_spend,
    make_meta_budget_authorizer,
    remaining_credit_usd,
)
from spray_schema.meta_planner import MetaPlanner

NON_CREDENTIAL_VALUE = "offline-placeholder-value"
BEFORE_EXPIRY = "2026-11-05T01:59:59Z"


def _ok(**overrides):
    """A baseline ALLOW call; override individual fields per test."""
    kwargs = {
        "worst_case_cost_usd": Decimal("1"),
        "lifetime_credit_spent_usd": Decimal("0"),
        "now_iso": BEFORE_EXPIRY,
        "round_spent_usd": Decimal("0"),
    }
    kwargs.update(overrides)
    return authorize_meta_spend(**kwargs)


class ConstantsTests(unittest.TestCase):
    def test_constants_have_expected_values_and_types(self):
        self.assertEqual(R2_PER_CALL_CAP_USD, Decimal("20"))
        self.assertEqual(LIFETIME_HARD_STOP_USD, Decimal("140"))
        self.assertEqual(CREDIT_EXPIRY_ISO, "2026-11-05T02:00:00Z")
        self.assertEqual(CASH_BUCKET_USD, Decimal("0"))
        self.assertIsInstance(R2_PER_CALL_CAP_USD, Decimal)
        self.assertIsInstance(LIFETIME_HARD_STOP_USD, Decimal)
        self.assertIsInstance(CASH_BUCKET_USD, Decimal)


class ExpiryTests(unittest.TestCase):
    def test_before_expiry_allows(self):
        permitted, _ = _ok(now_iso=BEFORE_EXPIRY)
        self.assertTrue(permitted)

    def test_exactly_at_expiry_denies(self):
        permitted, reason = _ok(now_iso="2026-11-05T02:00:00Z")
        self.assertFalse(permitted)
        self.assertIn("credit expired", reason)

    def test_after_expiry_denies(self):
        permitted, reason = _ok(now_iso="2026-11-05T02:00:01Z")
        self.assertFalse(permitted)
        self.assertIn("credit expired", reason)

    def test_unparseable_now_iso_denies(self):
        permitted, reason = _ok(now_iso="not-a-timestamp")
        self.assertFalse(permitted)
        self.assertIn("unparseable", reason)

    def test_non_normalized_offset_now_iso_denies(self):
        # A non-'Z' explicit offset is rejected by the ledger parser.
        permitted, _ = _ok(now_iso="2026-11-05T00:00:00+02:00")
        self.assertFalse(permitted)

    def test_none_now_iso_denies(self):
        permitted, _ = _ok(now_iso=None)
        self.assertFalse(permitted)


class PerCallCapTests(unittest.TestCase):
    def test_exactly_cap_allowed(self):
        # Boundary: exactly USD 20 is ALLOWED (inclusive ceiling). Needs enough
        # remaining credit, so lifetime spent is 0.
        permitted, reason = _ok(worst_case_cost_usd=Decimal("20"),
                                lifetime_credit_spent_usd=Decimal("0"))
        self.assertTrue(permitted, reason)

    def test_one_cent_over_cap_denies(self):
        permitted, reason = _ok(worst_case_cost_usd=Decimal("20.01"))
        self.assertFalse(permitted)
        self.assertIn("per-call cap", reason)

    def test_round_spent_plus_worst_case_over_cap_denies(self):
        permitted, reason = _ok(worst_case_cost_usd=Decimal("5"),
                                round_spent_usd=Decimal("16"))
        self.assertFalse(permitted)
        self.assertIn("per-round cap", reason)

    def test_round_spent_plus_worst_case_exactly_cap_allowed(self):
        permitted, reason = _ok(worst_case_cost_usd=Decimal("5"),
                                round_spent_usd=Decimal("15"))
        self.assertTrue(permitted, reason)


class LifetimeHardStopTests(unittest.TestCase):
    def test_under_hard_stop_allows(self):
        # 130 spent + 10 worst-case = 140 exactly -> allowed (inclusive).
        permitted, reason = _ok(worst_case_cost_usd=Decimal("10"),
                                lifetime_credit_spent_usd=Decimal("130"))
        self.assertTrue(permitted, reason)

    def test_crossing_hard_stop_denies(self):
        # 131 spent + 10 worst-case = 141 -> over the hard stop.
        permitted, reason = _ok(worst_case_cost_usd=Decimal("10"),
                                lifetime_credit_spent_usd=Decimal("131"))
        self.assertFalse(permitted)
        self.assertIn("lifetime hard stop", reason)

    def test_one_cent_over_hard_stop_denies(self):
        permitted, reason = _ok(worst_case_cost_usd=Decimal("10"),
                                lifetime_credit_spent_usd=Decimal("130.01"))
        self.assertFalse(permitted)
        self.assertIn("lifetime hard stop", reason)

    def test_remaining_credit_exhausted_denies(self):
        # Already at the hard stop: no remaining credit for any positive spend.
        permitted, reason = _ok(worst_case_cost_usd=Decimal("0.01"),
                                lifetime_credit_spent_usd=Decimal("140"))
        self.assertFalse(permitted)

    def test_worst_case_over_remaining_credit_denies(self):
        # Remaining credit = 140 - 125 = 15 but worst-case 18 (still under the
        # per-call cap of 20) -> denied. The lifetime-hard-stop rule (check 4)
        # catches this first because 125 + 18 = 143 > 140; the remaining-credit
        # rule (check 5) is the equivalent framing. Either deny reason is a
        # correct refusal of a worst-case that exceeds remaining credit.
        permitted, reason = _ok(worst_case_cost_usd=Decimal("18"),
                                lifetime_credit_spent_usd=Decimal("125"))
        self.assertFalse(permitted)
        self.assertTrue(
            "remaining credit" in reason or "lifetime hard stop" in reason,
            reason)


class RemainingCreditHelperTests(unittest.TestCase):
    def test_remaining_credit_normal(self):
        self.assertEqual(remaining_credit_usd(Decimal("40")), Decimal("100"))

    def test_remaining_credit_at_hard_stop_is_none(self):
        self.assertIsNone(remaining_credit_usd(Decimal("140")))

    def test_remaining_credit_over_hard_stop_is_none(self):
        self.assertIsNone(remaining_credit_usd(Decimal("150")))

    def test_remaining_credit_unknown_is_none(self):
        self.assertIsNone(remaining_credit_usd(None))
        self.assertIsNone(remaining_credit_usd(Decimal("-1")))


class CashZeroTests(unittest.TestCase):
    def test_ledger_refuses_any_positive_spend_against_zero_cash(self):
        # The ledger primitive proves no positive spend can be funded from a
        # zero-cash / zero-lease position. This is the cash-zero invariant the
        # authorizer relies on.
        permitted, reason = resource_ledger.spend_is_permitted(
            amount_usd=Decimal("0.01"),
            settled_cash_usd=CASH_BUCKET_USD,
            protective_buffer_usd=0,
            contingent_liabilities_usd=0,
            active_lease_headroom_usd=0,
        )
        self.assertFalse(permitted)
        self.assertIn("lease", reason)

    def test_authorizer_never_permits_a_cash_funded_spend(self):
        # A normal ALLOW still proves (internally) that the cash path refuses;
        # the allow is funded ONLY by non-fungible credit. We assert the allow
        # happens AND that the ledger independently refuses the same amount
        # from cash.
        permitted, _ = _ok(worst_case_cost_usd=Decimal("5"))
        self.assertTrue(permitted)
        cash_ok, _ = resource_ledger.spend_is_permitted(
            amount_usd=Decimal("5"),
            settled_cash_usd=CASH_BUCKET_USD,
            protective_buffer_usd=0,
            contingent_liabilities_usd=0,
            active_lease_headroom_usd=0,
        )
        self.assertFalse(cash_ok)

    def test_zero_worst_case_is_allowed_noop(self):
        # A zero-cost call is a no-op spend that does not touch cash.
        permitted, _ = _ok(worst_case_cost_usd=Decimal("0"))
        self.assertTrue(permitted)


class FailClosedInputTests(unittest.TestCase):
    def test_none_worst_case_denies(self):
        permitted, reason = _ok(worst_case_cost_usd=None)
        self.assertFalse(permitted)
        self.assertIn("worst-case", reason)

    def test_negative_worst_case_denies(self):
        permitted, _ = _ok(worst_case_cost_usd=Decimal("-1"))
        self.assertFalse(permitted)

    def test_nan_worst_case_denies(self):
        permitted, _ = _ok(worst_case_cost_usd=float("nan"))
        self.assertFalse(permitted)

    def test_inf_worst_case_denies(self):
        permitted, _ = _ok(worst_case_cost_usd=float("inf"))
        self.assertFalse(permitted)

    def test_decimal_nan_worst_case_denies(self):
        permitted, _ = _ok(worst_case_cost_usd=Decimal("NaN"))
        self.assertFalse(permitted)

    def test_bool_worst_case_denies(self):
        # bool is not a valid money value (ledger rejects it).
        permitted, _ = _ok(worst_case_cost_usd=True)
        self.assertFalse(permitted)

    def test_none_lifetime_spent_denies(self):
        permitted, reason = _ok(lifetime_credit_spent_usd=None)
        self.assertFalse(permitted)
        self.assertIn("lifetime-spent", reason)

    def test_negative_lifetime_spent_denies(self):
        permitted, _ = _ok(lifetime_credit_spent_usd=Decimal("-5"))
        self.assertFalse(permitted)

    def test_none_round_spent_denies(self):
        permitted, reason = _ok(round_spent_usd=None)
        self.assertFalse(permitted)
        self.assertIn("round-spent", reason)

    def test_negative_round_spent_denies(self):
        permitted, _ = _ok(round_spent_usd=Decimal("-1"))
        self.assertFalse(permitted)


class AuthorizerFactoryTests(unittest.TestCase):
    def test_factory_requires_callable_provider(self):
        with self.assertRaises(ValueError):
            make_meta_budget_authorizer(
                now_iso_provider="not-callable",
                lifetime_credit_spent_usd=Decimal("0"))

    def test_factory_allows_within_budget(self):
        authz = make_meta_budget_authorizer(
            now_iso_provider=lambda: BEFORE_EXPIRY,
            lifetime_credit_spent_usd=Decimal("0"))
        self.assertTrue(authz({"provider": "meta", "model_id": "m",
                               "estimated_credit_cost_usd": 5}))

    def test_factory_denies_over_cap(self):
        authz = make_meta_budget_authorizer(
            now_iso_provider=lambda: BEFORE_EXPIRY,
            lifetime_credit_spent_usd=Decimal("0"))
        self.assertFalse(authz({"provider": "meta", "model_id": "m",
                                "estimated_credit_cost_usd": 20.01}))

    def test_factory_denies_after_expiry(self):
        authz = make_meta_budget_authorizer(
            now_iso_provider=lambda: "2026-11-05T02:00:00Z",
            lifetime_credit_spent_usd=Decimal("0"))
        self.assertFalse(authz({"provider": "meta", "model_id": "m",
                                "estimated_credit_cost_usd": 1}))

    def test_factory_denies_non_dict_ctx(self):
        authz = make_meta_budget_authorizer(
            now_iso_provider=lambda: BEFORE_EXPIRY,
            lifetime_credit_spent_usd=Decimal("0"))
        self.assertFalse(authz(None))


class FakeResponse:
    def __init__(self, payload):
        import json
        self.payload = json.dumps(payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return None

    def read(self, limit=-1):
        return self.payload[:limit]


class MetaAdapterShortCircuitTests(unittest.TestCase):
    """Prove a denied authorization never reaches the (fake) transport."""

    def _click_response(self):
        # OpenAI-compatible chat shape (see spray_schema.meta_planner).
        return {
            "choices": [{
                "finish_reason": "stop",
                "message": {"content": '{"action":"click","index":0}'},
            }],
            "usage": {"prompt_tokens": 10, "completion_tokens": 2},
        }

    def test_denied_authorization_prevents_any_transport_call(self):
        sent = []

        def opener(request, timeout):
            sent.append(True)
            return FakeResponse(self._click_response())

        # Over the per-call cap -> authorizer denies.
        authz = make_meta_budget_authorizer(
            now_iso_provider=lambda: BEFORE_EXPIRY,
            lifetime_credit_spent_usd=Decimal("0"))
        planner = MetaPlanner(
            api_key=NON_CREDENTIAL_VALUE, opener=opener,
            budget_authorizer=authz, estimated_credit_cost_usd=25.0)
        with self.assertRaisesRegex(RuntimeError, "refused by budget authorizer"):
            planner.choose(observation={"controls": []}, target_text="Repair",
                           history=())
        self.assertEqual(sent, [])
        self.assertEqual(planner.requests, 0)

    def test_expired_credit_prevents_any_transport_call(self):
        sent = []

        def opener(request, timeout):
            sent.append(True)
            return FakeResponse(self._click_response())

        authz = make_meta_budget_authorizer(
            now_iso_provider=lambda: "2026-11-05T02:00:00Z",
            lifetime_credit_spent_usd=Decimal("0"))
        planner = MetaPlanner(
            api_key=NON_CREDENTIAL_VALUE, opener=opener,
            budget_authorizer=authz, estimated_credit_cost_usd=1.0)
        with self.assertRaisesRegex(RuntimeError, "refused by budget authorizer"):
            planner.choose(observation={"controls": []}, target_text="Repair",
                           history=())
        self.assertEqual(sent, [])

    def test_authorized_within_budget_allows_transport_call(self):
        sent = []

        def opener(request, timeout):
            sent.append(True)
            return FakeResponse(self._click_response())

        authz = make_meta_budget_authorizer(
            now_iso_provider=lambda: BEFORE_EXPIRY,
            lifetime_credit_spent_usd=Decimal("0"))
        planner = MetaPlanner(
            api_key=NON_CREDENTIAL_VALUE, opener=opener,
            budget_authorizer=authz, estimated_credit_cost_usd=5.0)
        decision = planner.choose(observation={"controls": []},
                                  target_text="Repair", history=())
        self.assertEqual(decision, {"action": "click", "index": 0})
        self.assertEqual(sent, [True])


if __name__ == "__main__":
    unittest.main()
