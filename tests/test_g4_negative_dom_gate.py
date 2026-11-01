"""G4 negative regression: an alternate click is not enough without DOM completion.

Offline only. Does not call Gemini or open a browser.
"""
import unittest

from scripts.run_g3_gemini_chromium import _executed_alternate_after_failure
from spray_schema.agent_loop import run_loop


class NoCompletionBrowser:
    def __init__(self):
        self.attempts = []

    def observe(self):
        return {
            "url": "/", "title": "Negative recovery",
            "visible_text": "Pending",
            "controls": [
                {"kind": "button", "label": "Continue", "enabled": True},
                {"kind": "button", "label": "Repair instead", "enabled": True},
            ],
        }

    def verify(self, target):
        return False

    def click(self, index):
        self.attempts.append(index)
        if len(self.attempts) == 1:
            raise PermissionError("synthetic stale control")

    def wait(self, milliseconds):
        pass


class Decisions:
    def __init__(self):
        self.actions = iter((
            {"action": "click", "index": 0},
            {"action": "click", "index": 1},
        ))

    def choose(self, *, observation, target_text, history):
        return next(self.actions)


class G4NegativeDomGateTests(unittest.TestCase):
    def test_dom_completion_after_failed_click_without_alternative_is_not_recovery(self):
        class SideEffectBrowser(NoCompletionBrowser):
            def __init__(self):
                super().__init__()
                self.completed = False

            def verify(self, target):
                return self.completed

            def click(self, index):
                self.attempts.append(index)
                self.completed = True
                raise PermissionError("synthetic click reported failure")

        browser = SideEffectBrowser()
        result = run_loop(
            browser, Decisions(), target_text="Completed",
            max_steps=2, require_alternate_on_failure=True,
        )
        self.assertEqual(browser.attempts, [0])
        self.assertEqual((result.status, result.reason),
                         ("FAIL", "completion_without_alternate"))


    def test_alternate_action_without_dom_completion_must_not_pass(self):
        browser = NoCompletionBrowser()
        result = run_loop(
            browser, Decisions(), target_text="Completed",
            max_steps=2, require_alternate_on_failure=True,
        )
        self.assertEqual(browser.attempts, [0, 1])
        self.assertTrue(_executed_alternate_after_failure(result.trace))
        self.assertNotEqual(result.status, "PASS")
        self.assertFalse(browser.verify("Completed"))


    def test_repeat_denial_trace_proves_same_observed_control(self):
        class RepeatSameControl:
            def choose(self, *, observation, target_text, history):
                return {"action": "click", "index": 0}

        browser = NoCompletionBrowser()
        result = run_loop(
            browser, RepeatSameControl(), target_text="Completed",
            max_steps=2, require_alternate_on_failure=True,
        )
        self.assertEqual(browser.attempts, [0])
        self.assertEqual((result.status, result.reason),
                         ("FAIL", "repeated_failed_action"))
        self.assertEqual(result.trace[0]["control_fingerprint"],
                         result.trace[1]["control_fingerprint"])

    def test_missing_or_blank_control_identity_cannot_prove_recovery(self):
        # The planner chooses a different index, but without both observed
        # identities the engine cannot establish a genuinely new control.
        # G5 must fail closed even if a second click would complete the DOM.
        class UnverifiableBrowser(NoCompletionBrowser):
            def __init__(self, mode):
                super().__init__()
                self.mode = mode
                self.completed = False

            def observe(self):
                observation = super().observe()
                if not self.attempts and self.mode == "missing_failed":
                    observation["controls"][0].pop("label")
                if self.attempts and self.mode == "missing_alternate":
                    observation["controls"][1].pop("label")
                if self.attempts and self.mode == "blank_alternate":
                    observation["controls"][1]["label"] = "   "
                return observation

            def verify(self, target):
                return self.completed

            def click(self, index):
                self.attempts.append(index)
                if len(self.attempts) == 1:
                    raise PermissionError("synthetic stale control")
                self.completed = True

        for mode in ("missing_failed", "missing_alternate", "blank_alternate"):
            with self.subTest(mode=mode):
                browser = UnverifiableBrowser(mode)
                result = run_loop(
                    browser, Decisions(), target_text="Completed",
                    max_steps=2, require_alternate_on_failure=True,
                )
                self.assertEqual((result.status, result.reason),
                                 ("FAIL", "unverifiable_alternative"))
                self.assertEqual(browser.attempts, [0])
                self.assertFalse(_executed_alternate_after_failure(result.trace))
                self.assertEqual(result.trace[-1]["outcome"], "planner_rejected")


if __name__ == "__main__":
    unittest.main()
