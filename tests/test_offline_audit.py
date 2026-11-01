import concurrent.futures
import os
import sqlite3
import tempfile
import unittest

from spray_schema.offline_audit import OfflineAuditJournal, reconcile_offline


class OfflineAuditTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = os.path.join(self.temp.name, "audit.db")
        self.audit = OfflineAuditJournal(self.path)
        self.audit.initialize()

    def test_chain_survives_restart(self):
        self.audit.append("admitted", "req-1", {"amount": "0.25"})
        self.audit.append("stopped", "req-1", {"reason": "owner"})
        self.assertEqual(OfflineAuditJournal(self.path).verify().entries, 2)
        self.assertTrue(OfflineAuditJournal(self.path).verify().valid)

    def test_tampered_record_detected(self):
        self.audit.append("admitted", "req-1", {"amount": "0.25"})
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE audit_events SET details_json=? WHERE sequence=1",
                       ('{"amount":"99"}',))
        self.assertEqual(self.audit.verify().reason, "modified_event")

    def test_missing_middle_event_detected(self):
        for i in range(3):
            self.audit.append("event", str(i), {})
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM audit_events WHERE sequence=2")
        self.assertEqual(self.audit.verify().reason, "broken_chain")

    def test_concurrent_append_serialized(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda i: self.audit.append("event", str(i), {}), range(30)))
        result = self.audit.verify()
        self.assertTrue(result.valid)
        self.assertEqual(result.entries, 30)

    def test_billing_drift_and_bad_values(self):
        self.assertTrue(reconcile_offline("0.100", "0.1").matched)
        self.assertEqual(reconcile_offline("1.00", "1.01").reason,
                         "billing_drift_pause_required")
        for invalid in ("NaN", "Infinity", "-1", "", " 1"):
            self.assertEqual(reconcile_offline("1", invalid).reason, "invalid_money")


if __name__ == "__main__":
    unittest.main()
