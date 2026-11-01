import concurrent.futures
import unittest

from spray_schema.execution_safety import (
    ExecutionRequest, IndependentEvidence, evaluate_execution,
)
from spray_schema.execution_fault_simulator import SimulatedIndependentBudget


def admitted(request):
    evidence = IndependentEvidence(
        owner_approved=True, global_stop_active=False, budget_stop_active=False,
        identity_verified=True, iam_scope_verified=True, cash_balance_verified=True,
        lease_verified=True, idempotency_key_unused=True, snapshot_fresh=True,
    )
    return evaluate_execution(request, evidence, authorized_cash_ceiling_usd="100")


def req(identifier, amount="1"):
    return ExecutionRequest(identifier, "offline-only", "b" * 64, amount)


class FaultSimulatorTests(unittest.TestCase):
    def test_duplicate_replay_is_denied(self):
        budget = SimulatedIndependentBudget("2")
        request = req("r1")
        self.assertTrue(budget.reserve(request, admitted(request)).accepted)
        self.assertEqual(budget.reserve(request, admitted(request)).reason, "replay")
        self.assertEqual(budget.snapshot(), ("1", False, 1))

    def test_concurrent_same_id_has_single_winner(self):
        budget = SimulatedIndependentBudget("10")
        request = req("race")
        decision = admitted(request)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda _: budget.reserve(request, decision), range(40)))
        self.assertEqual(sum(r.accepted for r in results), 1)
        self.assertEqual(budget.snapshot(), ("9", False, 1))

    def test_concurrent_unique_ids_never_overdraw(self):
        budget = SimulatedIndependentBudget("3")
        requests = [req("r" + str(i)) for i in range(30)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            results = list(pool.map(lambda r: budget.reserve(r, admitted(r)), requests))
        self.assertEqual(sum(r.accepted for r in results), 3)
        self.assertEqual(budget.snapshot(), ("0", False, 3))

    def test_emergency_stop_blocks_future_requests(self):
        budget = SimulatedIndependentBudget("3")
        budget.stop()
        request = req("stopped")
        self.assertEqual(budget.reserve(request, admitted(request)).reason, "stopped")
        self.assertEqual(budget.snapshot(), ("3", True, 0))

    def test_verifier_outage_and_stale_evidence(self):
        budget = SimulatedIndependentBudget("3")
        request = req("r1")
        decision = admitted(request)
        self.assertEqual(budget.reserve(request, decision, verifier_available=False).reason,
                         "verifier_unavailable")
        self.assertEqual(budget.reserve(request, decision, evidence_fresh=False).reason,
                         "stale_evidence")
        self.assertEqual(budget.snapshot(), ("3", False, 0))

    def test_mutated_request_cannot_reuse_decision(self):
        budget = SimulatedIndependentBudget("3")
        original = req("r1")
        altered = req("r1", "2")
        self.assertEqual(budget.reserve(altered, admitted(original)).reason, "admission_mismatch")
        self.assertEqual(budget.snapshot(), ("3", False, 0))

    def test_subcent_budget_exact(self):
        budget = SimulatedIndependentBudget("0.00000000000000000001")
        request = req("tiny", "0.00000000000000000001")
        self.assertTrue(budget.reserve(request, admitted(request)).accepted)
        self.assertEqual(budget.snapshot(), ("0E-20", False, 1))


if __name__ == "__main__":
    unittest.main()
