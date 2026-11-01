import concurrent.futures
import dataclasses
import os
import tempfile
import unittest

from spray_schema.offline_g5_boundary import OfflineAuthority, OfflineWorker, Scope


KEY = b"offline-test-only-32-byte-signing-key"
NOW = 1800000000
SCOPE = Scope("sandbox-account", "local", "sandbox", "offline")


class G5BoundaryBundleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.auth = OfflineAuthority(KEY, frozenset({SCOPE}), "2.00")
        self.worker = OfflineWorker(os.path.join(self.tmp.name, "worker.db"), self.auth, "3.00")

    def cap(self, i="1", **changes):
        values = dict(request_id="request-" + i, nonce="nonce-000000000000" + i,
                      scope=SCOPE, cash_ceiling="1.00", expires_at=NOW+60, now=NOW)
        values.update(changes)
        return self.auth.issue(**values)

    def allow(self, cap, **changes):
        kwargs = dict(now=NOW, evidence_fresh=True, stop_reachable=True)
        kwargs.update(changes)
        return self.worker.authorize_offline(cap, **kwargs)

    def test_issue_and_replay(self):
        p = self.cap()
        self.assertTrue(self.allow(p))
        self.assertFalse(self.allow(p))
        self.assertEqual(self.worker.snapshot(), (1, 1))

    def test_tampering_denied(self):
        p = self.cap()
        self.assertFalse(self.allow(dataclasses.replace(p, cash_ceiling="2.00")))
        self.assertFalse(self.allow(dataclasses.replace(p, scope=Scope("prod", "local", "sandbox", "offline"))))
        self.assertEqual(self.worker.snapshot(), (0, 0))

    def test_policy_scope_and_amount_denied(self):
        with self.assertRaises(ValueError):
            self.cap(scope=Scope("prod", "local", "sandbox", "offline"))
        with self.assertRaises(ValueError):
            self.cap(cash_ceiling="3.00")

    def test_epoch_stop_and_resume_revokes_old_capability(self):
        p = self.cap()
        self.auth.stop()
        self.assertFalse(self.allow(p))
        with self.assertRaises(ValueError):
            self.cap("2")
        self.auth.resume_test_only()
        self.assertFalse(self.allow(p))
        self.assertTrue(self.allow(self.cap("3")))

    def test_stale_and_unreachable_fail_closed(self):
        p = self.cap()
        self.assertFalse(self.allow(p, evidence_fresh=False))
        self.assertFalse(self.allow(p, stop_reachable=False))
        self.assertFalse(self.allow(p, now=NOW+61))
        self.assertEqual(self.worker.snapshot(), (0, 0))

    def test_concurrent_budget_cap(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(lambda i: self.allow(self.cap(str(i))), range(12)))
        self.assertEqual(sum(outcomes), 3)
        self.assertEqual(self.worker.snapshot(), (3, 3))

    def test_policy_revision_revokes_old(self):
        p = self.cap()
        self.auth.policy_revision += 1
        self.assertFalse(self.allow(p))
        self.assertTrue(self.allow(self.cap("2")))


if __name__ == "__main__":
    unittest.main()
