import concurrent.futures
import os
import tempfile
import unittest

from spray_schema.execution_safety import ExecutionRequest, IndependentEvidence, evaluate_execution
from spray_schema.offline_reservation_store import OfflineReservationStore


def request(i, amount="1"):
    return ExecutionRequest(str(i), "offline", "a" * 64, amount)


def admit(req):
    evidence = IndependentEvidence(True, False, False, True, True, True, True, True, True)
    return evaluate_execution(req, evidence, authorized_cash_ceiling_usd="100")


class OfflineStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "ledger.db")
        self.store = OfflineReservationStore(self.path)
        self.store.initialize("3")

    def reserve(self, req, **overrides):
        options = dict(verifier_available=True, evidence_fresh=True)
        options.update(overrides)
        return self.store.reserve(req, admit(req), **options)

    def test_persistence_and_replay_after_reopen(self):
        req = request("one")
        self.assertTrue(self.reserve(req).accepted)
        reopened = OfflineReservationStore(self.path)
        self.assertEqual(reopened.reserve(req, admit(req), verifier_available=True,
                                          evidence_fresh=True).reason, "replay")
        self.assertEqual(reopened.snapshot(), ("2", False, 1))

    def test_concurrent_connections_no_double_charge(self):
        req = request("same")
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda _: self.reserve(req), range(24)))
        self.assertEqual(sum(result.accepted for result in results), 1)
        self.assertEqual(self.store.snapshot(), ("2", False, 1))

    def test_concurrent_distinct_requests_cannot_overdraw(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.reserve(request(i)), range(12)))
        self.assertEqual(sum(result.accepted for result in results), 3)
        self.assertEqual(self.store.snapshot(), ("0", False, 3))

    def test_stop_survives_reopen(self):
        self.store.stop()
        reopened = OfflineReservationStore(self.path)
        self.assertEqual(reopened.reserve(request("new"), admit(request("new")),
                                         verifier_available=True, evidence_fresh=True).reason, "stopped")
        self.assertEqual(reopened.snapshot(), ("3", True, 0))

    def test_outage_and_stale_evidence_do_not_mutate(self):
        self.assertEqual(self.reserve(request("x"), verifier_available=False).reason,
                         "verifier_unavailable")
        self.assertEqual(self.reserve(request("y"), evidence_fresh=False).reason,
                         "stale_evidence")
        self.assertEqual(self.store.snapshot(), ("3", False, 0))

    def test_modified_amount_cannot_reuse_approval(self):
        original = request("x", "1")
        altered = request("x", "2")
        result = self.store.reserve(altered, admit(original),
                                    verifier_available=True, evidence_fresh=True)
        self.assertEqual(result.reason, "admission_mismatch")
        self.assertEqual(self.store.snapshot(), ("3", False, 0))

    def test_initialization_does_not_reset_balance(self):
        self.assertTrue(self.reserve(request("x")).accepted)
        self.store.initialize("999")
        self.assertEqual(self.store.snapshot(), ("2", False, 1))


if __name__ == "__main__":
    unittest.main()
