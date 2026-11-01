"""No-cost integration of persistent budget, Converse planning, and browser loop."""
import json
from pathlib import Path
import tempfile
import unittest

from spray_schema.agent_loop import run_loop
from spray_schema.bedrock_budget import BedrockBudget
from spray_schema.bedrock_planner import BedrockPlanner


class BudgetedAgentLoopTests(unittest.TestCase):
    def test_failed_action_replans_through_budget_gate(self):
        class Browser:
            def __init__(self):
                self.clicks = 0

            def observe(self):
                return {"controls": [
                    {"kind": "button", "label": "Unavailable"},
                    {"kind": "button", "label": "Complete task"},
                ], "visible_text": "Pending"}

            def click(self, index):
                self.clicks += 1
                if index == 0:
                    raise TimeoutError("synthetic click failure")
                self.done = True

            def wait(self, milliseconds):
                pass

            def verify(self, target):
                return getattr(self, "done", False) and target == "Completed"

        class Converse:
            def __init__(self):
                self.requests = []

            def converse(self, **kwargs):
                self.requests.append(kwargs)
                payload = json.loads(kwargs["messages"][0]["content"][0]["text"])
                index = 1 if payload["recent_outcomes"] else 0
                return {
                    "stopReason": "end_turn",
                    "output": {"message": {"content": [{"text": json.dumps(
                        {"action": "click", "index": index})}]}},
                    "usage": {"inputTokens": 12, "outputTokens": 8},
                }

        with tempfile.TemporaryDirectory() as directory:
            ledger = Path(directory) / "bedrock-budget.json"
            budget = BedrockBudget(
                model_id="offline-test-model", region="us-east-1",
                ledger_path=str(ledger), input_usd_per_million="100",
                output_usd_per_million="1",
                pricing_source="https://aws.amazon.com/bedrock/pricing/",
                ceiling_usd="1.00",
            )
            converse = Converse()
            planner = BedrockPlanner(
                "offline-test-model", "us-east-1", authorized=True,
                client=converse, budget=budget,
            )
            result = run_loop(Browser(), planner, target_text="Completed")

            state = json.loads(ledger.read_text(encoding="utf-8"))
            self.assertEqual(result.status, "PASS")
            self.assertEqual([entry["outcome"] for entry in result.trace],
                             ["action_failed", "executed"])
            self.assertEqual(len(converse.requests), 2)
            self.assertEqual(state["requests"], 2)
            self.assertLessEqual(float(state["reserved_usd"]), 1.0)
            self.assertNotIn("Completed", ledger.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
