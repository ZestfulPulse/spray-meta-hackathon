import concurrent.futures
import os
import tempfile
import unittest

from spray_schema.execution_safety import ExecutionRequest, IndependentEvidence, evaluate_execution
from spray_schema.offline_integrated_control import OfflineIntegratedControl


def request(i):
    return ExecutionRequest(str(i), "offline-only", "f" * 64, "1")


def admitted(req):
    evidence = IndependentEvidence(True, False, False, True, True, True, True, True, True)
    return evaluate_execution(req, evidence, authorized_cash_ceiling_usd="10")


class IntegratedControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "control.db")
        self.control = OfflineIntegratedControl(self.path)
        self.control.initialize("3")

    def reserve(self, req, **kwargs):
        options = dict(verifier_available=True, evidence_fresh=True)
        options.update(kwargs)
        return self.control.reserve(req, admitted(req), **options)

    def test_reservation_and_audit_commit_together(self):
        self.assertTrue(self.reserve(request("a")).accepted)
        self.assertEqual(self.control.reservations.snapshot(), ("2", False, 1))
        audit = self.control.audit.verify()
        self.assertTrue(audit.valid)
        self.assertEqual(audit.entries, 1)

    def test_injected_failure_rolls_back_both(self):
        with self.assertRaisesRegex(RuntimeError, "injected failure"):
            self.reserve(request("a"), inject_failure_after_reservation=True)
        self.assertEqual(self.control.reservations.snapshot(), ("3", False, 0))
        self.assertEqual(self.control.audit.verify().entries, 0)

    def test_concurrent_unique_requests_have_exact_audit_count(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(lambda i: self.reserve(request(i)), range(12)))
        self.assertEqual(sum(result.accepted for result in results), 3)
        self.assertEqual(self.control.reservations.snapshot(), ("0", False, 3))
        audit = self.control.audit.verify()
        self.assertTrue(audit.valid)
        self.assertEqual(audit.entries, 3)

    def test_stop_and_audit_survive_restart(self):
        self.control.stop()
        reopened = OfflineIntegratedControl(self.path)
        self.assertEqual(reopened.reservations.snapshot(), ("3", True, 0))
        self.assertEqual(reopened.audit.verify().entries, 1)
        self.assertEqual(reopened.reserve(request("a"), admitted(request("a")),
                                          verifier_available=True, evidence_fresh=True).reason, "stopped")

    def test_failed_request_never_debits_or_appends(self):
        req = request("a")
        self.assertEqual(self.control.reserve(req, admitted(req),
                         verifier_available=False, evidence_fresh=True).reason, "verifier_unavailable")
        self.assertEqual(self.control.reservations.snapshot(), ("3", False, 0))
        self.assertEqual(self.control.audit.verify().entries, 0)


if __name__ == "__main__":
    unittest.main()
