"""R2-S1 regression: the documented G5Recovery stale-control race.

A control that was observed as ENABLED but becomes disabled before the click
lands is a recoverable mid-click race (see docs/G3_GEMINI_LOCAL_PROOF.md),
not a batch-halting policy denial. It must raise a plain PermissionError and
leave the _blocked_policy_actions counter untouched so run_loop can replan to
the safe alternate control. A control that was never observed as enabled must
still go through _deny_policy and bump the counter.

These tests fail on the first-bad-commit behavior (44add73, which routed every
disabled-at-click control through _deny_policy) and pass with the fix. They use
a small fake locator/page in the style of tests/test_preview_guard_h9.py; no
Playwright is required.
"""
import unittest

from spray_schema.playwright_preview import PreviewBrowser

URL = "http://127.0.0.1:8765/"


class DisabledAtClickControl:
    """A control that reports enabled text/label but is disabled on click."""

    def inner_text(self):
        return "Continue"

    def is_enabled(self):
        return False

    def evaluate(self, *_):
        raise AssertionError("disabled control must not be dispatched")


class FakePage:
    url = "about:blank"


def _browser():
    browser = PreviewBrowser(
        URL, allowed_origin=URL, fixture_html="<p>Pending</p>",
        allowed_click_labels=("Continue",),
    )
    browser.page = FakePage()
    return browser


class StaleControlRecoveryTests(unittest.TestCase):
    def test_observed_enabled_then_disabled_is_recoverable(self):
        # Observed enabled -> disabled before click: recoverable race.
        browser = _browser()
        browser._controls = [DisabledAtClickControl()]
        browser._observed_labels = ["Continue"]
        browser._observed_enabled = [True]
        with self.assertRaisesRegex(PermissionError, "disabled control"):
            browser.click(0)
        # The key property: no policy-denial escalation, so run_batch does
        # not halt and run_loop can replan to the alternate control.
        self.assertEqual(browser._blocked_policy_actions, 0)

    def test_control_never_observed_enabled_is_a_policy_denial(self):
        # Observed as disabled (or without a truthy observation record) is a
        # policy denial that still bumps the counter and halts the batch.
        browser = _browser()
        browser._controls = [DisabledAtClickControl()]
        browser._observed_labels = ["Continue"]
        browser._observed_enabled = [False]
        with self.assertRaisesRegex(PermissionError, "disabled control"):
            browser.click(0)
        self.assertEqual(browser._blocked_policy_actions, 1)

    def test_missing_observed_enabled_record_is_a_policy_denial(self):
        # No _observed_enabled entry for the control (index out of range):
        # fail closed as a policy denial, matching test_preview_guard_h9's
        # directly-injected disabled control.
        browser = _browser()
        browser._controls = [DisabledAtClickControl()]
        browser._observed_labels = ["Continue"]
        browser._observed_enabled = []
        with self.assertRaisesRegex(PermissionError, "disabled control"):
            browser.click(0)
        self.assertEqual(browser._blocked_policy_actions, 1)


if __name__ == "__main__":
    unittest.main()
