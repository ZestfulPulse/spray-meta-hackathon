import dataclasses
import unittest

from spray_schema.offline_g4_control_plane import Permit
from spray_schema.offline_g4_preflight import check_permit_shape


def permit():
    return Permit("req-1", "owner", "offline", "sandbox-account",
                  "test-region", "sandbox", "1.00", "nonce-0000000001",
                  1800000000, 1800000060, "test-key", "signature")


def preflight(p, **changes):
    params = dict(allowed_accounts=frozenset({"sandbox-account"}),
                  allowed_regions=frozenset({"test-region"}),
                  allowed_actions=frozenset({"offline"}),
                  allowed_environments=frozenset({"sandbox"}),
                  max_cash_usd="2.00")
    params.update(changes)
    return check_permit_shape(p, **params)


class G4PreflightTests(unittest.TestCase):
    def test_scoped_permit(self):
        self.assertTrue(preflight(permit()).acceptable)

    def test_cross_account_region_action_and_environment(self):
        for field in ("account", "region", "action", "environment"):
            with self.subTest(field=field):
                changed = dataclasses.replace(permit(), **{field: "unexpected"})
                self.assertFalse(preflight(changed).acceptable)

    def test_budget_over_ceiling_and_nonfinite(self):
        self.assertFalse(preflight(dataclasses.replace(permit(), ceiling_usd="2.01")).acceptable)
        self.assertFalse(preflight(dataclasses.replace(permit(), ceiling_usd="NaN")).acceptable)

    def test_missing_nonce_and_allowlist(self):
        self.assertFalse(preflight(dataclasses.replace(permit(), nonce="short")).acceptable)
        self.assertFalse(preflight(permit(), allowed_accounts=frozenset()).acceptable)


if __name__ == "__main__":
    unittest.main()
