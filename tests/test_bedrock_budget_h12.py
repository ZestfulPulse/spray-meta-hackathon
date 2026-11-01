"""No-cost tests for durable Bedrock spend reservation and planner integration."""
import json
from pathlib import Path
import tempfile
import unittest

from spray_schema.bedrock_budget import BedrockBudget
from spray_schema.bedrock_planner import BedrockPlanner


class FakeConverse:
    def __init__(self, *, fail=False):
        self.calls = 0
        self.fail = fail

    def converse(self, **kwargs):
        self.calls += 1
        if self.fail:
            raise TimeoutError("ambiguous provider failure")
        return {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": '{"action":"wait","milliseconds":5}'}]}},
            "usage": {"inputTokens": 10, "outputTokens": 8},
        }


class BedrockBudgetTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.ledger = Path(self.folder.name) / "bedrock-budget.json"

    def budget(self, **overrides):
        options = {
            "model_id": "approved-test-model", "region": "us-east-1",
            "ledger_path": str(self.ledger),
            "input_usd_per_million": "100",
            "output_usd_per_million": "1",
            "pricing_source": "https://aws.amazon.com/bedrock/pricing/",
            "ceiling_usd": "1.00",
        }
        options.update(overrides)
        return BedrockBudget(**options)

    def planner(self, client, budget):
        return BedrockPlanner(
            "approved-test-model", "us-east-1", authorized=True,
            client=client, budget=budget, max_requests=10)

    def invoke(self, planner):
        return planner.choose(observation={"controls": []},
                              target_text="Completed", history=())

    def test_no_live_client_without_budget_fails_before_boto3(self):
        planner = BedrockPlanner("approved-test-model", "us-east-1",
                                 authorized=True)
        with self.assertRaises(PermissionError):
            self.invoke(planner)
        self.assertEqual(planner.requests, 0)

    def test_persistent_ceiling_blocks_before_third_provider_call(self):
        client = FakeConverse()
        self.invoke(self.planner(client, self.budget()))
        self.invoke(self.planner(client, self.budget()))
        with self.assertRaises(PermissionError):
            self.invoke(self.planner(client, self.budget()))
        self.assertEqual(client.calls, 2)
        state = json.loads(self.ledger.read_text())
        self.assertEqual(state["requests"], 2)
        self.assertLessEqual(float(state["reserved_usd"]), 1.0)
        self.assertNotIn("Completed", self.ledger.read_text())

    def test_ambiguous_failure_keeps_reservation(self):
        client = FakeConverse(fail=True)
        with self.assertRaises(TimeoutError):
            self.invoke(self.planner(client, self.budget()))
        self.assertEqual(json.loads(self.ledger.read_text())["requests"], 1)

    def test_model_region_and_pricing_identity_are_bound(self):
        budget = self.budget()
        with self.assertRaises(PermissionError):
            budget.reserve(model_id="different", region="us-east-1",
                           request_text="hi", system_text="hi", max_output_tokens=120)
        self.invoke(self.planner(FakeConverse(), budget))
        with self.assertRaises(PermissionError):
            self.budget(input_usd_per_million="101").reserve(
                model_id="approved-test-model", region="us-east-1",
                request_text="hi", system_text="hi", max_output_tokens=120)

    def test_corrupt_ledger_fails_closed(self):
        self.ledger.write_text("not json")
        with self.assertRaises(PermissionError):
            self.invoke(self.planner(FakeConverse(), self.budget()))

    def test_symlinked_ledger_refused(self):
        target = Path(self.folder.name) / "target"
        target.write_text("{}")
        self.ledger.symlink_to(target)
        with self.assertRaises(PermissionError):
            self.invoke(self.planner(FakeConverse(), self.budget()))

    def test_unverified_pricing_source_and_over_ceiling_rejected(self):
        with self.assertRaises(ValueError):
            self.budget(pricing_source="https://example.org/")
        with self.assertRaises(ValueError):
            self.budget(ceiling_usd="1.01")
        with self.assertRaises(ValueError):
            self.budget(input_usd_per_million="NaN")

    def test_reservation_happens_before_provider_call(self):
        client = FakeConverse()
        planner = self.planner(client, self.budget(input_usd_per_million="1000"))
        with self.assertRaises(PermissionError):
            self.invoke(planner)
        self.assertEqual(client.calls, 0)
        self.assertEqual(planner.requests, 0)
        self.assertFalse(self.ledger.exists())

    def test_budget_free_test_mode_rejects_unmarked_client(self):
        client = FakeConverse()
        with self.assertRaises(PermissionError):
            BedrockPlanner("approved-test-model", "us-east-1",
                           authorized=True, client=client, test_mode=True)
        self.assertEqual(client.calls, 0)

    def test_test_mode_cannot_bypass_budget_for_marked_offline_client(self):
        class Offline:
            __spray_offline_only__ = True

            def __init__(self):
                self.calls = 0

            def converse(self, **kwargs):
                self.calls += 1
                raise AssertionError("budget check must precede Converse")

        client = Offline()
        planner = BedrockPlanner("approved-test-model", "us-east-1",
                                 authorized=True, client=client, test_mode=True)
        with self.assertRaises(PermissionError):
            self.invoke(planner)
        self.assertEqual(client.calls, 0)
        self.assertEqual(planner.requests, 0)

    def test_non_bedrockbudget_object_cannot_bypass_reservation(self):
        class NoOpBudget:
            def reserve(self, **kwargs):
                return {"request_reserved_usd": "0"}

        with self.assertRaises(TypeError):
            BedrockPlanner("approved-test-model", "us-east-1",
                           budget=NoOpBudget())

    def test_budget_free_test_mode_rejects_sdk_shaped_client(self):
        class SDKShaped:
            __spray_offline_only__ = True

            def __init__(self):
                from types import SimpleNamespace
                self.meta = SimpleNamespace(
                    service_model=SimpleNamespace(service_name="bedrock-runtime"))
                self.calls = 0

            def converse(self, **kwargs):
                self.calls += 1
                raise AssertionError("SDK-shaped client must not be called")

        client = SDKShaped()
        with self.assertRaises(PermissionError):
            BedrockPlanner("approved-test-model", "us-east-1",
                           authorized=True, client=client, test_mode=True)
        self.assertEqual(client.calls, 0)

    def test_test_mode_rechecks_swapped_client_before_call(self):
        class Offline:
            __spray_offline_only__ = True

            def converse(self, **kwargs):
                return {"output": {"message": {"content": [
                    {"text": '{"action":"finish"}'}]}}}

        planner = BedrockPlanner("approved-test-model", "us-east-1",
                                 authorized=True, client=Offline(), test_mode=True)
        live_candidate = FakeConverse()
        planner.client = live_candidate
        with self.assertRaises(PermissionError):
            self.invoke(planner)
        self.assertEqual(live_candidate.calls, 0)
        self.assertEqual(planner.requests, 0)


if __name__ == "__main__":
    unittest.main()
