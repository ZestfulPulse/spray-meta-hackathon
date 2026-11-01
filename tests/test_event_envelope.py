"""Stdlib unittest suite for the SPRAY event envelope schema and fixtures.

Pure Python 3.11 standard library: no pytest, no third-party dependencies,
no network, no AWS. Run with:

    python3 -m unittest discover -s tests -p 'test_*.py' -v
"""

from __future__ import annotations

import copy
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
from spray_schema.event_envelope import (  # noqa: E402
    ALLOWED_VERIFICATION_STATUSES,
    REQUIRED_FIELDS,
    validate_envelope,
)

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _load_fixture(name: str) -> dict:
    with (FIXTURES_DIR / name).open(encoding="utf-8") as handle:
        return json.load(handle)


class ValidEnvelopeTests(unittest.TestCase):
    def test_valid_fixture_has_no_errors(self) -> None:
        envelope = _load_fixture("valid_event.json")
        self.assertEqual(validate_envelope(envelope), [])

    def test_valid_fixture_contains_all_required_fields(self) -> None:
        envelope = _load_fixture("valid_event.json")
        for field in REQUIRED_FIELDS:
            self.assertIn(field, envelope)


class VerificationStatusTests(unittest.TestCase):
    def test_each_allowed_status_validates(self) -> None:
        base = _load_fixture("valid_event.json")
        self.assertEqual(
            ALLOWED_VERIFICATION_STATUSES, {"unverified", "verified", "rejected"}
        )
        for status in ALLOWED_VERIFICATION_STATUSES:
            envelope = copy.deepcopy(base)
            envelope["verification"] = status
            with self.subTest(status=status):
                self.assertEqual(validate_envelope(envelope), [])

    def test_unknown_status_produces_error(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["verification"] = "maybe"
        errors = validate_envelope(envelope)
        self.assertTrue(errors)
        self.assertTrue(any("verification" in e for e in errors))


class MissingFieldTests(unittest.TestCase):
    def test_invalid_fixture_reports_missing_required_field(self) -> None:
        envelope = _load_fixture("invalid_event.json")
        errors = validate_envelope(envelope)
        self.assertTrue(errors)
        # invalid_event.json omits 'summary'.
        self.assertTrue(
            any("missing required field: summary" in e for e in errors),
            msg=f"expected a missing-summary error, got: {errors}",
        )

    def test_dropping_any_required_field_is_reported(self) -> None:
        base = _load_fixture("valid_event.json")
        for field in REQUIRED_FIELDS:
            envelope = copy.deepcopy(base)
            del envelope[field]
            with self.subTest(field=field):
                errors = validate_envelope(envelope)
                self.assertIn(f"missing required field: {field}", errors)


class FieldTypeTests(unittest.TestCase):
    def test_evidence_refs_not_a_list_is_reported(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["evidence_refs"] = "not-a-list"
        errors = validate_envelope(envelope)
        self.assertTrue(any("evidence_refs must be a list" in e for e in errors))

    def test_negative_cost_is_reported(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["cost_estimate_usd"] = -1
        errors = validate_envelope(envelope)
        self.assertIn("cost_estimate_usd must be >= 0", errors)

    def test_bool_cost_is_rejected_as_non_number(self) -> None:
        # bool is a Number subclass, so it must be explicitly rejected.
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["cost_estimate_usd"] = True
        errors = validate_envelope(envelope)
        self.assertTrue(
            any("cost_estimate_usd must be a number" in e for e in errors),
            msg=f"expected a cost-type error, got: {errors}",
        )

    def test_non_dict_input_is_reported(self) -> None:
        errors = validate_envelope(["not", "a", "mapping"])
        self.assertEqual(len(errors), 1)
        self.assertTrue(errors[0].startswith("envelope must be a JSON object"))

    def test_nan_cost_is_rejected(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["cost_estimate_usd"] = float("nan")
        errors = validate_envelope(envelope)
        self.assertIn("cost_estimate_usd must be a finite number, not NaN", errors)

    def test_positive_infinity_cost_is_rejected(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["cost_estimate_usd"] = float("inf")
        errors = validate_envelope(envelope)
        self.assertIn(
            "cost_estimate_usd must be a finite number, not Infinity", errors
        )

    def test_negative_infinity_cost_is_rejected(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["cost_estimate_usd"] = float("-inf")
        errors = validate_envelope(envelope)
        self.assertIn(
            "cost_estimate_usd must be a finite number, not Infinity", errors
        )

    def test_complex_cost_is_reported_without_raising(self) -> None:
        # complex is a numbers.Number but not orderable; `complex < 0` would
        # raise TypeError. It must be reported, not blow up.
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["cost_estimate_usd"] = complex(1, 2)
        errors = validate_envelope(envelope)
        self.assertTrue(
            any("cost_estimate_usd must be a finite number" in e for e in errors),
            msg=f"expected a finite-number error for complex, got: {errors}",
        )

    def test_decimal_nan_cost_is_reported_without_raising(self) -> None:
        # Decimal('NaN') is a Number but not a float; `Decimal('NaN') < 0`
        # would raise decimal.InvalidOperation. Must be a reported NaN error.
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["cost_estimate_usd"] = Decimal("NaN")
        errors = validate_envelope(envelope)
        self.assertIn("cost_estimate_usd must be a finite number, not NaN", errors)

    def test_decimal_infinity_cost_is_reported_without_raising(self) -> None:
        # Decimal('Infinity') is a Number but not a float; previously `< 0`
        # was False so it was silently accepted. Must be a reported error.
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["cost_estimate_usd"] = Decimal("Infinity")
        errors = validate_envelope(envelope)
        self.assertIn(
            "cost_estimate_usd must be a finite number, not Infinity", errors
        )


class UnhashableEnumValueTests(unittest.TestCase):
    """Regression: membership tests must not raise TypeError on unhashable
    values (lists/dicts); they must report a validation error instead."""

    def test_verification_as_list_reports_error_without_raising(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["verification"] = ["unverified"]
        errors = validate_envelope(envelope)
        self.assertTrue(any("verification must be one of" in e for e in errors))

    def test_verification_as_dict_reports_error_without_raising(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["verification"] = {"status": "verified"}
        errors = validate_envelope(envelope)
        self.assertTrue(any("verification must be one of" in e for e in errors))

    def test_kind_as_list_reports_error_without_raising(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["kind"] = ["decision"]
        errors = validate_envelope(envelope)
        self.assertTrue(any("kind must be one of" in e for e in errors))

    def test_kind_as_dict_reports_error_without_raising(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["kind"] = {"type": "decision"}
        errors = validate_envelope(envelope)
        self.assertTrue(any("kind must be one of" in e for e in errors))

    def test_claim_origin_as_list_reports_error_without_raising(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["claim_origin"] = ["spray"]
        errors = validate_envelope(envelope)
        self.assertTrue(any("claim_origin must be one of" in e for e in errors))

    def test_claim_origin_as_dict_reports_error_without_raising(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["claim_origin"] = {"origin": "spray"}
        errors = validate_envelope(envelope)
        self.assertTrue(any("claim_origin must be one of" in e for e in errors))

    def test_all_three_unhashable_at_once_does_not_raise(self) -> None:
        envelope = copy.deepcopy(_load_fixture("valid_event.json"))
        envelope["verification"] = ["x"]
        envelope["kind"] = {"k": "v"}
        envelope["claim_origin"] = ["spray", "provider"]
        # The key assertion is that this call returns rather than raising.
        errors = validate_envelope(envelope)
        self.assertTrue(any("verification must be one of" in e for e in errors))
        self.assertTrue(any("kind must be one of" in e for e in errors))
        self.assertTrue(any("claim_origin must be one of" in e for e in errors))


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


if __name__ == "__main__":
    unittest.main()
