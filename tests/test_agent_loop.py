import unittest
from spray_schema.agent_loop import run_loop
from spray_schema.bedrock_planner import BedrockPlanner
from tests.bedrock_test_support import make_test_budget


class FakeBrowser:
    def __init__(self, fail_once=False):
        self.calls = 0
        self.fail_once = fail_once
        self.done = False

    def observe(self):
        return {"controls": [{"kind": "button", "label": "Fix"}], "visible_text": "Not done"}

    def click(self, index):
        self.calls += 1
        if self.fail_once and self.calls == 1:
            raise TimeoutError("test failure")
        self.done = True

    def wait(self, milliseconds):
        pass

    def verify(self, target):
        return self.done and target == "Completed"


class ScriptedPlanner:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)
        self.histories = []

    def choose(self, **kwargs):
        self.histories.append(kwargs["history"])
        return next(self.decisions)


class AgentLoopTests(unittest.TestCase):
    def test_success_after_real_action(self):
        result = run_loop(FakeBrowser(), ScriptedPlanner({"action": "click", "index": 0}),
                          target_text="Completed")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(result.trace[0]["outcome"], "executed")

    def test_failed_action_replans(self):
        browser = FakeBrowser(fail_once=True)
        planner = ScriptedPlanner({"action": "click", "index": 0},
                                  {"action": "click", "index": 0})
        result = run_loop(browser, planner, target_text="Completed")
        self.assertEqual(result.status, "PASS")
        self.assertEqual(browser.calls, 2)
        self.assertEqual(planner.histories[1][0]["outcome"], "action_failed")

    def test_wait_then_click_tool_selection(self):
        planner = ScriptedPlanner({"action": "wait", "milliseconds": 50},
                                  {"action": "click", "index": 0})
        result = run_loop(FakeBrowser(), planner, target_text="Completed")
        self.assertEqual(result.status, "PASS")
        self.assertEqual([t["action"]["action"] for t in result.trace], ["wait", "click"])

    def test_invalid_tool_rejected(self):
        result = run_loop(FakeBrowser(), ScriptedPlanner({"action": "shell", "command": "rm"}),
                          target_text="Completed")
        self.assertEqual((result.status, result.reason), ("FAIL", "planner_rejected"))

    def test_invalid_index_rejected(self):
        result = run_loop(FakeBrowser(), ScriptedPlanner({"action": "click", "index": 99}),
                          target_text="Completed")
        self.assertEqual(result.reason, "planner_rejected")

    def test_finish_not_a_verification(self):
        result = run_loop(FakeBrowser(), ScriptedPlanner({"action": "finish"}),
                          target_text="Completed")
        self.assertEqual(result.reason, "finish_without_verification")

    def test_owner_pause(self):
        result = run_loop(FakeBrowser(), ScriptedPlanner({"action": "click", "index": 0}),
                          target_text="Completed", stopped=lambda: True)
        self.assertEqual(result.reason, "owner_pause")

    def test_owner_pause_during_initial_dom_verification(self):
        # DOM may be complete already, but a PAUSE raised while verifying
        # must take precedence. The model must never be consulted.
        state = {"paused": False}

        class PausingVerifiedBrowser(FakeBrowser):
            def verify(self, target):
                state["paused"] = True
                return True

        planner = ScriptedPlanner({"action": "click", "index": 0})
        result = run_loop(
            PausingVerifiedBrowser(), planner, target_text="Completed",
            stopped=lambda: state["paused"],
        )
        self.assertEqual((result.status, result.reason), ("FAIL", "owner_pause"))
        self.assertEqual(result.trace, ())
        self.assertEqual(planner.histories, [])

    def test_owner_pause_during_incomplete_dom_verification(self):
        # A failed DOM check may set PAUSE. Do not spend another model
        # request or execute a click just because the target is absent.
        state = {"paused": False}

        class PausingIncompleteBrowser(FakeBrowser):
            def verify(self, target):
                state["paused"] = True
                return False

        planner = ScriptedPlanner({"action": "click", "index": 0})
        browser = PausingIncompleteBrowser()
        result = run_loop(
            browser, planner, target_text="Completed",
            stopped=lambda: state["paused"],
        )
        self.assertEqual((result.status, result.reason), ("FAIL", "owner_pause"))
        self.assertEqual(planner.histories, [])
        self.assertEqual(browser.calls, 0)
        self.assertEqual(result.trace, ())

    def test_owner_pause_during_final_dom_verification(self):
        # PAUSE can be set after the executed click, while the browser is
        # performing the independent DOM proof. Keep the real action trace,
        # but never return PASS for a revoked run.
        state = {"paused": False}

        class PausingAfterClickBrowser(FakeBrowser):
            def verify(self, target):
                complete = super().verify(target)
                if complete:
                    state["paused"] = True
                return complete

        browser = PausingAfterClickBrowser()
        result = run_loop(
            browser, ScriptedPlanner({"action": "click", "index": 0}),
            target_text="Completed", stopped=lambda: state["paused"],
        )
        self.assertEqual((result.status, result.reason), ("FAIL", "owner_pause"))
        self.assertEqual(browser.calls, 1)
        self.assertEqual(len(result.trace), 1)
        self.assertEqual(result.trace[0]["outcome"], "executed")

    def test_step_cap(self):
        browser = FakeBrowser(fail_once=True)
        result = run_loop(browser, ScriptedPlanner({"action": "click", "index": 0}),
                          target_text="Completed", max_steps=1)
        self.assertEqual(result.reason, "step_limit")

    def test_bedrock_disabled_by_default(self):
        planner = BedrockPlanner(model_id="example", region="us-east-1")
        with self.assertRaises(PermissionError):
            planner.choose(observation={}, target_text="Completed", history=())

    def test_bedrock_response_parsed_without_live_call(self):
        class FakeClient:
            __spray_offline_only__ = True
            def converse(self, **kwargs):
                self.request = kwargs
                return {"output": {"message": {"content": [{"text": '{"action":"click","index":0}'}]}}}
        client = FakeClient()
        budget = make_test_budget(self, "example", "us-east-1")
        planner = BedrockPlanner(model_id="example", region="us-east-1",
                                 authorized=True, client=client, budget=budget,
                                 test_mode=True)
        decision = planner.choose(observation={"controls": []}, target_text="Completed", history=())
        self.assertEqual(decision["index"], 0)
        self.assertEqual(client.request["modelId"], "example")


if __name__ == "__main__":
    unittest.main()
