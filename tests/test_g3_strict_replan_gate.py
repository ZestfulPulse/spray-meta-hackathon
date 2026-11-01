"""G3 strict replan regression. No browser process or model API calls."""
import unittest

from scripts.run_g3_gemini_chromium import _executed_alternate_after_failure
from spray_schema.agent_loop import run_loop
from spray_schema.qa_flow import Requirement, run_qa_flow


class StaleControlBrowser:
    def __init__(self):
        self.attempts = []
        self.done = False

    def observe(self):
        return {
            "url": "/",
            "title": "Synthetic G3",
            "visible_text": "Pending",
            "controls": [
                {"kind": "button", "label": "Continue", "enabled": True},
                {"kind": "button", "label": "Repair instead", "enabled": True},
            ],
        }

    def verify(self, target):
        return self.done and target == "Completed"

    def click(self, index):
        self.attempts.append(index)
        if len(self.attempts) == 1 and index == 0:
            raise PermissionError("synthetic stale control")
        self.done = True

    def wait(self, milliseconds):
        pass


class ReorderedControlBrowser(StaleControlBrowser):
    """The same two controls exchange indices after the first failed click."""

    def observe(self):
        observation = super().observe()
        if self.attempts:
            observation["controls"].reverse()
        return observation


class Decisions:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)
        self.histories = []

    def choose(self, *, observation, target_text, history):
        self.histories.append(history)
        return next(self.decisions)


class OneRequestOnly:
    def __init__(self):
        self.requests = 0

    def choose(self, *, observation, target_text, history):
        if self.requests >= 1:
            raise RuntimeError("model request cap reached")
        self.requests += 1
        return {"action": "click", "index": 0}


class G3StrictReplanGateTests(unittest.TestCase):
    def test_final_recon_failure_preserves_successful_loop_trace_but_fails_gate(self):
        class BrokenFinalRecon(StaleControlBrowser):
            observations = 0

            def observe(self):
                self.observations += 1
                if self.observations == 3:
                    raise RuntimeError("synthetic final recon failure")
                return super().observe()

        browser = BrokenFinalRecon()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "click", "index": 1})
        result = run_qa_flow(
            browser, planner, flow_id="G3FinalRecon",
            requirements=(Requirement("MUST-01", "Completed", "must"),),
            target_text="Completed", require_alternate_on_failure=True,
        )
        self.assertEqual((result.status, result.reason, result.quality_gate),
                         ("FAIL", "final_recon_error_RuntimeError", "FAIL"))
        self.assertEqual(browser.attempts, [0, 1])
        self.assertEqual([step["outcome"] for step in result.trace],
                         ["action_failed", "executed"])

    def test_final_recon_failure_does_not_erase_original_failure_trace(self):
        class BrokenFinalRecon(StaleControlBrowser):
            observations = 0

            def observe(self):
                self.observations += 1
                if self.observations == 3:
                    raise RuntimeError("synthetic final recon failure")
                return super().observe()

        browser = BrokenFinalRecon()
        result = run_qa_flow(
            browser, OneRequestOnly(), flow_id="G3FinalReconFail",
            requirements=(Requirement("MUST-01", "Completed", "must"),),
            target_text="Completed", require_alternate_on_failure=True,
        )
        self.assertEqual((result.status, result.reason, result.quality_gate),
                         ("FAIL", "final_recon_error_RuntimeError", "FAIL"))
        self.assertEqual(browser.attempts, [0])
        self.assertEqual([step["outcome"] for step in result.trace],
                         ["action_failed", "planner_rejected"])

    def test_known_disabled_click_cannot_fake_a_recovery_failure(self):
        class DisabledObservedControl(StaleControlBrowser):
            def observe(self):
                observation = super().observe()
                observation["controls"][0]["enabled"] = False
                return observation

        browser = DisabledObservedControl()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "click", "index": 1})
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason),
                         ("FAIL", "planner_rejected"))
        self.assertEqual(browser.attempts, [])
        self.assertEqual(len(planner.histories), 1)
        self.assertEqual(result.trace[0]["outcome"], "planner_rejected")

    def test_legacy_mode_preserves_disabled_click_handling(self):
        class DisabledObservedControl(StaleControlBrowser):
            def observe(self):
                observation = super().observe()
                observation["controls"][0]["enabled"] = False
                return observation

        browser = DisabledObservedControl()
        result = run_loop(browser, Decisions({"action": "click", "index": 0}),
                          target_text="Completed")
        self.assertEqual(result.status, "FAIL")
        self.assertEqual(result.reason, "planner_rejected")
        self.assertEqual(browser.attempts, [0])

    def test_repeating_failed_click_is_denied_before_second_browser_action(self):
        browser = StaleControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "click", "index": 0})
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason),
                         ("FAIL", "repeated_failed_action"))
        self.assertEqual(browser.attempts, [0])
        self.assertEqual([step["outcome"] for step in result.trace],
                         ["action_failed", "repeated_failed_action"])
        self.assertEqual(planner.histories[1][0]["outcome"], "action_failed")

    def test_different_model_decision_executes_and_verifies(self):
        browser = StaleControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "click", "index": 1})
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason),
                         ("PASS", "independent_browser_verification"))
        self.assertEqual(browser.attempts, [0, 1])
        self.assertEqual([step["outcome"] for step in result.trace],
                         ["action_failed", "executed"])

    def test_reindexed_same_failed_control_is_denied(self):
        browser = ReorderedControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "click", "index": 1})
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason),
                         ("FAIL", "repeated_failed_action"))
        self.assertEqual(browser.attempts, [0])

    def test_same_index_new_control_is_a_genuine_alternative(self):
        browser = ReorderedControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "click", "index": 0})
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason),
                         ("PASS", "independent_browser_verification"))
        self.assertEqual(browser.attempts, [0, 0])

    def test_wait_does_not_erase_failed_click_identity(self):
        browser = StaleControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "wait", "milliseconds": 100},
                            {"action": "click", "index": 0})
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason),
                         ("FAIL", "repeated_failed_action"))
        self.assertEqual(browser.attempts, [0])
        self.assertEqual([step["outcome"] for step in result.trace],
                         ["action_failed", "executed", "repeated_failed_action"])
        self.assertEqual(len(planner.histories), 3)

    def test_wait_then_genuinely_different_click_can_recover(self):
        browser = StaleControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "wait", "milliseconds": 100},
                            {"action": "click", "index": 1})
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason),
                         ("PASS", "independent_browser_verification"))
        self.assertEqual(browser.attempts, [0, 1])

    def test_wait_does_not_erase_failed_click_after_reindex(self):
        browser = ReorderedControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "wait", "milliseconds": 100},
                            {"action": "click", "index": 1})
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason),
                         ("FAIL", "repeated_failed_action"))
        self.assertEqual(browser.attempts, [0])

    def test_live_proof_wait_is_not_alternate_strategy(self):
        trace = [
            {"action": {"action": "click", "index": 0},
             "control_fingerprint": "a" * 64, "outcome": "action_failed"},
            {"action": {"action": "wait", "milliseconds": 100},
             "outcome": "executed"},
        ]
        self.assertFalse(_executed_alternate_after_failure(trace))

    def test_live_proof_repeated_click_is_not_alternate_strategy(self):
        trace = [
            {"action": {"action": "click", "index": 0},
             "control_fingerprint": "a" * 64, "outcome": "action_failed"},
            {"action": {"action": "wait", "milliseconds": 100},
             "outcome": "executed"},
            {"action": {"action": "click", "index": 1},
             "control_fingerprint": "a" * 64, "outcome": "executed"},
        ]
        # The failed button moved to a different index. No new strategy.
        self.assertFalse(_executed_alternate_after_failure(trace))

    def test_live_proof_different_executed_click_counts_as_alternate(self):
        browser = StaleControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "click", "index": 1})
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual(result.status, "PASS")
        self.assertTrue(_executed_alternate_after_failure(result.trace))
        self.assertNotEqual(result.trace[0]["control_fingerprint"],
                            result.trace[1]["control_fingerprint"])
        # Evidence stores only digests, not potentially sensitive UI labels.
        self.assertNotIn("Continue", str(result.trace))
        self.assertNotIn("Repair instead", str(result.trace))

    def test_live_proof_same_index_new_control_counts_as_alternate(self):
        browser = ReorderedControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "click", "index": 0})
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual(result.status, "PASS")
        self.assertTrue(_executed_alternate_after_failure(result.trace))

    def test_live_proof_missing_identity_fails_closed(self):
        trace = [
            {"action": {"action": "click", "index": 0},
             "outcome": "action_failed"},
            {"action": {"action": "click", "index": 1},
             "outcome": "executed"},
        ]
        self.assertFalse(_executed_alternate_after_failure(trace))
        trace[0]["control_fingerprint"] = "a" * 64
        self.assertFalse(_executed_alternate_after_failure(trace))
        trace[1]["control_fingerprint"] = "b" * 64
        self.assertTrue(_executed_alternate_after_failure(trace))

    def test_default_mode_preserves_legacy_transient_retry(self):
        browser = StaleControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "click", "index": 0})
        result = run_loop(browser, planner, target_text="Completed")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(browser.attempts, [0, 0])

    def test_qa_flow_propagates_strict_replan_failure(self):
        browser = StaleControlBrowser()
        planner = Decisions({"action": "click", "index": 0},
                            {"action": "click", "index": 0})
        result = run_qa_flow(
            browser, planner, flow_id="G3Strict",
            requirements=(Requirement("MUST-01", "Completed", "must"),),
            target_text="Completed", require_alternate_on_failure=True,
        )
        self.assertEqual((result.status, result.reason, result.quality_gate),
                         ("FAIL", "repeated_failed_action", "FAIL"))
        self.assertEqual(browser.attempts, [0])

    def test_single_model_request_cannot_claim_replan(self):
        browser = StaleControlBrowser()
        planner = OneRequestOnly()
        result = run_loop(browser, planner, target_text="Completed",
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason),
                         ("FAIL", "planner_rejected"))
        self.assertEqual(planner.requests, 1)
        self.assertEqual(browser.attempts, [0])
        self.assertEqual([step["outcome"] for step in result.trace],
                         ["action_failed", "planner_rejected"])

    def test_owner_pause_during_observation_prevents_planning(self):
        paused = {"value": False}

        class PausingBrowser(StaleControlBrowser):
            def observe(self):
                paused["value"] = True
                return super().observe()

        browser = PausingBrowser()
        planner = Decisions({"action": "click", "index": 0})
        result = run_loop(browser, planner, target_text="Completed",
                          stopped=lambda: paused["value"],
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason), ("FAIL", "owner_pause"))
        self.assertEqual(browser.attempts, [])
        self.assertEqual(planner.histories, [])

    def test_owner_pause_during_planning_prevents_stale_click(self):
        paused = {"value": False}

        class PausingPlanner:
            def choose(self, *, observation, target_text, history):
                paused["value"] = True
                return {"action": "click", "index": 0}

        browser = StaleControlBrowser()
        result = run_loop(browser, PausingPlanner(), target_text="Completed",
                          stopped=lambda: paused["value"],
                          require_alternate_on_failure=True)
        self.assertEqual((result.status, result.reason), ("FAIL", "owner_pause"))
        self.assertEqual(browser.attempts, [])
        self.assertEqual(result.trace, ())

    def test_invalid_strict_gate_fails_closed(self):
        with self.assertRaises(ValueError):
            run_loop(StaleControlBrowser(), Decisions(),
                     target_text="Completed", require_alternate_on_failure="yes")


if __name__ == "__main__":
    unittest.main()
