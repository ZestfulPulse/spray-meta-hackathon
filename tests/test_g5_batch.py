"""Offline tests for the bounded G5 batch runner; no browser or API calls."""
import json
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch

from spray_schema.g5_batch import TaskSpec, run_batch, _goal_fingerprint
from spray_schema.qa_flow import Requirement


def task(task_id, *, strict=False, url="https://preview.example.test/"):
    return TaskSpec(
        task_id=task_id, url=url, approved_origin="https://preview.example.test",
        target_text="Completed",
        requirements=(Requirement("MUST-01", "Completed", "must"),),
        allowed_click_labels=("Continue", "Repair instead"),
        max_steps=2, require_alternate_on_failure=strict,
    )


class FakeBrowser:
    def __init__(self, mode="success", pause=None):
        self.mode = mode
        self.pause = pause
        self.done = False
        self.clicks = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def observe(self):
        if self.mode == "pause" and self.pause is not None:
            self.pause["paused"] = True
        return {
            "url": "/qa", "title": "SECRET_CANARY",
            "visible_text": "SECRET_CANARY",
            "controls": [
                {"kind": "button", "label": "Continue", "enabled": True},
                {"kind": "button", "label": "Repair instead", "enabled": True},
            ],
        }

    def verify(self, text):
        return self.done and text == "Completed"

    def click(self, index):
        self.clicks.append(index)
        if self.mode == "recovery" and len(self.clicks) == 1:
            raise PermissionError("SECRET_CANARY")
        if self.mode != "never":
            self.done = True

    def wait(self, milliseconds):
        pass


class FakePlanner:
    def __init__(self, mode="success"):
        self.mode = mode

    def choose(self, *, observation, target_text, history):
        if self.mode == "recovery" and not history:
            return {"action": "click", "index": 0}
        return {"action": "click", "index": 1}


class G5BatchTests(unittest.TestCase):
    def test_four_of_five_approved_tasks_pass_with_negative_evidence(self):
        modes = {"A": "success", "B": "recovery", "C": "success",
                 "D": "never", "E": "success"}
        tasks = tuple(task(x, strict=x == "B") for x in modes)
        summary = run_batch(
            tasks,
            browser_factory=lambda t: FakeBrowser(modes[t.task_id]),
            planner_factory=lambda t: FakePlanner(modes[t.task_id]),
            min_successes=4,
        )
        self.assertEqual((summary.status, summary.successes), ("PASS", 4))
        self.assertEqual([o.status for o in summary.task_outcomes],
                         ["PASS", "PASS", "PASS", "FAIL", "PASS"])
        self.assertEqual(summary.task_outcomes[1].reason,
                         "independent_browser_verification")
        self.assertEqual([e["outcome"] for e in summary.task_outcomes[1].trace],
                         ["action_failed", "executed"])
        self.assertEqual(summary.task_outcomes[3].reason, "step_limit")

    def test_precompleted_dom_does_not_inflate_five_task_success(self):
        # Previously, run_loop returned PASS on initial DOM verification
        # before any planner choice. The batch must not count 5/5 for that.
        class AlreadyDoneBrowser(FakeBrowser):
            def __init__(self):
                super().__init__()
                self.done = True

        class UnexpectedPlanner(FakePlanner):
            def choose(self, **kwargs):
                raise AssertionError("no model action should run")

        summary = run_batch(
            tuple(task(x) for x in "ABCDE"),
            browser_factory=lambda t: AlreadyDoneBrowser(),
            planner_factory=lambda t: UnexpectedPlanner(),
            min_successes=4,
        )
        self.assertEqual((summary.status, summary.successes), ("FAIL", 0))
        self.assertEqual([item.status for item in summary.task_outcomes], ["FAIL"] * 5)
        self.assertTrue(all(item.reason == "no_executed_click"
                            and item.quality_gate == "NOT_VERIFIED"
                            and not item.trace for item in summary.task_outcomes))

    def test_wait_only_dom_completion_is_not_click_execution(self):
        class WaitCompletionBrowser(FakeBrowser):
            def wait(self, milliseconds):
                self.done = True

        class WaitPlanner:
            def choose(self, *, observation, target_text, history):
                return {"action": "wait", "milliseconds": 25}

        summary = run_batch(
            (task("WaitOnly"),),
            browser_factory=lambda t: WaitCompletionBrowser(),
            planner_factory=lambda t: WaitPlanner(),
            min_successes=1,
        )
        outcome = summary.task_outcomes[0]
        self.assertEqual((summary.status, summary.successes), ("FAIL", 0))
        self.assertEqual(outcome.reason, "no_executed_click")
        self.assertEqual(outcome.quality_gate, "NOT_VERIFIED")
        self.assertEqual(len(outcome.trace), 1)
        self.assertEqual(outcome.trace[0]["action"]["action"], "wait")
        self.assertEqual(outcome.trace[0]["outcome"], "executed")

    def test_impossible_click_index_cannot_certify_unattended_success(self):
        # The S7 evidence gate already refuses negative indices, while the
        # G5 batch runner previously counted any int as an executed click.
        # Keep admission and final proof aligned; malformed browser traces
        # must not inflate success or consume a false 4/5 pass.
        from spray_schema.qa_flow import QAResult

        for index, expected_status in (
            (-1, "FAIL"), (100, "FAIL"), (True, "FAIL"),
            (0, "PASS"), (1, "PASS"), (99, "PASS"),
        ):
            with self.subTest(index=index):
                qa = QAResult(
                    "PASS", "independent_browser_verification", "PASS", {},
                    ({"step": 1, "action": {"action": "click", "index": index},
                      "outcome": "executed"},),
                )
                with patch("spray_schema.g5_batch.run_qa_flow", return_value=qa):
                    summary = run_batch(
                        (task("A"),), browser_factory=lambda t: FakeBrowser(),
                        planner_factory=lambda t: FakePlanner(), min_successes=1,
                    )
                self.assertEqual(summary.status, expected_status)
                self.assertEqual(summary.successes, int(expected_status == "PASS"))
                self.assertEqual(summary.task_outcomes[0].status, expected_status)
                if expected_status == "FAIL":
                    self.assertEqual(summary.task_outcomes[0].reason,
                                     "no_executed_click")
                    self.assertEqual(summary.task_outcomes[0].quality_gate,
                                     "NOT_VERIFIED")

    def test_three_of_five_is_not_a_pass(self):
        modes = {"A": "never", "B": "never", "C": "success",
                 "D": "success", "E": "success"}
        summary = run_batch(
            tuple(task(x) for x in modes),
            browser_factory=lambda t: FakeBrowser(modes[t.task_id]),
            planner_factory=lambda t: FakePlanner(),
            min_successes=4,
        )
        self.assertEqual((summary.status, summary.successes), ("FAIL", 3))

    def test_live_quota_mode_stops_when_four_of_five_becomes_impossible(self):
        # Two normal task failures already make a four-of-five batch fail.
        # The remaining three tasks must not open Chromium or call a planner.
        opened, planners = [], []
        modes = {"A": "never", "B": "never", "C": "success",
                 "D": "success", "E": "success"}

        def browser_factory(item):
            opened.append(item.task_id)
            return FakeBrowser(modes[item.task_id])

        def planner_factory(item):
            planners.append(item.task_id)
            return FakePlanner()

        summary = run_batch(
            tuple(task(x) for x in modes),
            browser_factory=browser_factory, planner_factory=planner_factory,
            min_successes=4, stop_when_threshold_unreachable=True,
        )
        self.assertEqual((summary.status, summary.successes), ("FAIL", 0))
        self.assertEqual(opened, ["A", "B"])
        self.assertEqual(planners, ["A", "B"])
        self.assertEqual([x.status for x in summary.task_outcomes],
                         ["FAIL", "FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN"])
        self.assertTrue(all(
            x.reason == "success_threshold_unreachable"
            and x.quality_gate == "NOT_VERIFIED"
            for x in summary.task_outcomes[2:]
        ))

    def test_live_quota_mode_keeps_running_if_threshold_still_possible(self):
        opened = []
        modes = {"A": "never", "B": "success", "C": "success",
                 "D": "success", "E": "success"}

        def browser_factory(item):
            opened.append(item.task_id)
            return FakeBrowser(modes[item.task_id])

        summary = run_batch(
            tuple(task(x) for x in modes),
            browser_factory=browser_factory, planner_factory=lambda _: FakePlanner(),
            min_successes=4, stop_when_threshold_unreachable=True,
        )
        self.assertEqual((summary.status, summary.successes), ("PASS", 4))
        self.assertEqual(opened, list(modes))
        self.assertEqual([x.status for x in summary.task_outcomes],
                         ["FAIL", "PASS", "PASS", "PASS", "PASS"])

    def test_pause_prevents_all_subsequent_tasks(self):
        pause = {"paused": False}
        summary = run_batch(
            (task("A"), task("B"), task("C")),
            browser_factory=lambda t: FakeBrowser("pause", pause),
            planner_factory=lambda t: FakePlanner(),
            min_successes=2,
            stopped=lambda: pause["paused"],
        )
        self.assertEqual(summary.status, "FAIL")
        self.assertEqual([o.status for o in summary.task_outcomes],
                         ["FAIL", "NOT_RUN", "NOT_RUN"])
        self.assertEqual(summary.task_outcomes[0].reason, "owner_pause")

    def test_permission_error_halts_batch_and_redacts_message(self):
        def deny(_task):
            raise PermissionError("SECRET_CANARY")
        summary = run_batch(
            (task("A"), task("B")),
            browser_factory=deny,
            planner_factory=lambda t: FakePlanner(),
            min_successes=1,
        )
        self.assertEqual([o.status for o in summary.task_outcomes],
                         ["FAIL", "NOT_RUN"])
        self.assertEqual(summary.task_outcomes[0].reason,
                         "runner_error_PermissionError")
        self.assertNotIn("SECRET_CANARY", str(summary))

    def test_rejects_unapproved_origin_and_remote_http(self):
        with self.assertRaises(PermissionError):
            task("A", url="https://other.example.test/")
        with self.assertRaises(PermissionError):
            TaskSpec("A", "http://preview.example.test/", "http://preview.example.test",
                     "Completed", (Requirement("MUST-01", "Completed"),),
                     ("Continue",))
        with self.assertRaises(PermissionError):
            task("A", url="https://preview.example.test/delete")

    def test_unsafe_owner_allowlist_fails_before_browser_or_model(self):
        # Runtime would deny these labels, but the Preview manifest must not
        # waste Gemini quota on a task that can never safely execute.
        for label in ("DeleteAccount", "Publish-Draft", "Transfer Funds",
                      "삭제", "Upload"):
            with self.subTest(label=label):
                with self.assertRaisesRegex(PermissionError, "unsafe click label"):
                    TaskSpec(
                        task_id="A", url="https://preview.example.test/",
                        approved_origin="https://preview.example.test",
                        target_text="Completed",
                        requirements=(Requirement("MUST-01", "Completed", "must"),),
                        allowed_click_labels=(label,),
                    )
        # A harmless descriptive word containing an unsafe substring is
        # not equivalent to a destructive action.
        self.assertIsInstance(TaskSpec(
            task_id="Safe", url="https://preview.example.test/",
            approved_origin="https://preview.example.test",
            target_text="Completed",
            requirements=(Requirement("MUST-01", "Completed", "must"),),
            allowed_click_labels=("Publisher profile", "Open details"),
        ), TaskSpec)

    def test_rejects_duplicate_ids_and_invalid_caps(self):
        with self.assertRaises(ValueError):
            run_batch((task("A"), task("A")),
                      browser_factory=FakeBrowser,
                      planner_factory=FakePlanner, min_successes=1)
        with self.assertRaises(ValueError):
            run_batch((task("A"),), browser_factory=FakeBrowser,
                      planner_factory=FakePlanner, min_successes=2)
        with self.assertRaises(ValueError):
            TaskSpec("bad/id", "https://preview.example.test/",
                     "https://preview.example.test", "Completed",
                     (Requirement("MUST-01", "Completed"),), ("Continue",))
        with self.assertRaises(ValueError):
            TaskSpec("A", "https://preview.example.test/",
                     "https://preview.example.test", "Completed",
                     (Requirement("MUST-01", "Completed"),), ())

    def test_invalid_evidence_path_stops_before_any_browser_action(self):
        calls = []
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            with patch("spray_schema.g5_batch.Path.home", return_value=home):
                with self.assertRaises(PermissionError):
                    run_batch(
                        (task("A"),),
                        browser_factory=lambda t: calls.append(t) or FakeBrowser(),
                        planner_factory=lambda t: FakePlanner(),
                        min_successes=1, evidence_dir=home / "outside",
                    )
        self.assertEqual(calls, [])

    def test_late_owner_pause_cannot_leave_a_false_pass(self):
        state = {"paused": False}
        class LatePauseBrowser(FakeBrowser):
            def __exit__(self, *args):
                state["paused"] = True
                return False
        summary = run_batch(
            (task("A"), task("B")),
            browser_factory=lambda t: LatePauseBrowser(),
            planner_factory=lambda t: FakePlanner(),
            min_successes=1, stopped=lambda: state["paused"],
        )
        self.assertEqual(summary.status, "FAIL")
        self.assertEqual([o.status for o in summary.task_outcomes],
                         ["FAIL", "NOT_RUN"])
        self.assertEqual(summary.task_outcomes[0].reason, "owner_pause")

    def test_policy_denial_overrides_dom_pass_and_halts_batch(self):
        class DeniedBrowser(FakeBrowser):
            _blocked_policy_actions = 1
            _blocked_requests = 0

        summary = run_batch(
            (task("A"), task("B")),
            browser_factory=lambda t: DeniedBrowser(),
            planner_factory=lambda t: FakePlanner(),
            min_successes=1,
        )
        self.assertEqual(summary.status, "FAIL")
        self.assertEqual([o.status for o in summary.task_outcomes],
                         ["FAIL", "NOT_RUN"])
        self.assertEqual(summary.task_outcomes[0].reason, "browser_policy_denied")

    def test_policy_denial_stops_current_task_before_second_click(self):
        browsers = []

        class DeniedFirstClick(FakeBrowser):
            def __init__(self):
                super().__init__()
                self._blocked_policy_actions = 0

            def click(self, index):
                self.clicks.append(index)
                if len(self.clicks) == 1:
                    self._blocked_policy_actions += 1
                    raise PermissionError("SECRET_CANARY")
                self.done = True

        def make_browser(_task):
            browser = DeniedFirstClick()
            browsers.append(browser)
            return browser

        summary = run_batch(
            (task("A"), task("B")),
            browser_factory=make_browser,
            planner_factory=lambda t: FakePlanner(),
            min_successes=2,
        )
        # Before this regression fix, the second click was executed even
        # though the first had already tripped the policy-denial counter.
        self.assertEqual(len(browsers), 1)
        self.assertEqual(browsers[0].clicks, [1])
        self.assertEqual([x.status for x in summary.task_outcomes],
                         ["FAIL", "NOT_RUN"])
        self.assertEqual(summary.task_outcomes[0].reason, "browser_policy_denied")
        self.assertNotIn("SECRET_CANARY", str(summary))

    def test_blocked_network_during_observe_stops_before_model_decision(self):
        browsers = []
        chosen = []

        class CountingPlanner(FakePlanner):
            def choose(self, **kwargs):
                chosen.append(True)
                return super().choose(**kwargs)

        class DeniedObserve(FakeBrowser):
            def __init__(self):
                super().__init__()
                self._blocked_requests = 0

            def observe(self):
                self._blocked_requests += 1
                return super().observe()

        def make_browser(_task):
            browser = DeniedObserve()
            browsers.append(browser)
            return browser

        def make_planner(_task):
            # Factory construction is local and side-effect free. The gated
            # property is that GeminiPlanner.choose must never be invoked.
            return CountingPlanner()

        summary = run_batch(
            (task("A"), task("B")),
            browser_factory=make_browser,
            planner_factory=make_planner,
            min_successes=2,
        )
        self.assertEqual(chosen, [])
        self.assertEqual(len(browsers), 1)
        self.assertEqual(browsers[0].clicks, [])
        self.assertEqual([x.status for x in summary.task_outcomes],
                         ["FAIL", "NOT_RUN"])
        self.assertEqual(summary.task_outcomes[0].reason, "browser_policy_denied")

    def test_blocked_network_request_overrides_dom_pass(self):
        class BlockedBrowser(FakeBrowser):
            _blocked_policy_actions = 0
            _blocked_requests = 1

        summary = run_batch(
            (task("A"),),
            browser_factory=lambda t: BlockedBrowser(),
            planner_factory=lambda t: FakePlanner(),
            min_successes=1,
        )
        self.assertEqual((summary.status, summary.task_outcomes[0].reason),
                         ("FAIL", "browser_policy_denied"))

    def test_blocked_navigation_during_context_entry_halts_entire_batch(self):
        # Playwright can raise a TimeoutError in __enter__ after the request
        # guard aborts navigation. The adapter's counters must remain visible
        # to run_batch, even though "with ... as browser" never completed.
        entered = []
        class BlockedEntryBrowser(FakeBrowser):
            def __enter__(self):
                entered.append("entered")
                self._blocked_requests = 1
                raise TimeoutError("SECRET_CANARY")

        summary = run_batch(
            (task("A"), task("B"), task("C"), task("D"), task("E")),
            browser_factory=lambda t: BlockedEntryBrowser(),
            planner_factory=lambda t: FakePlanner(),
            min_successes=4,
        )
        self.assertEqual(summary.status, "FAIL")
        self.assertEqual(entered, ["entered"])
        self.assertEqual(summary.task_outcomes[0].reason, "browser_policy_denied")
        self.assertEqual([x.status for x in summary.task_outcomes],
                         ["FAIL", "NOT_RUN", "NOT_RUN", "NOT_RUN", "NOT_RUN"])
        self.assertNotIn("SECRET_CANARY", str(summary))

    def test_non_policy_context_failure_isolated_without_false_success(self):
        entered = []
        class TransientEntryBrowser(FakeBrowser):
            def __enter__(self):
                entered.append("entered")
                raise TimeoutError("SECRET_CANARY")

        summary = run_batch(
            (task("A"), task("B")),
            browser_factory=lambda t: (
                TransientEntryBrowser() if t.task_id == "A" else FakeBrowser()),
            planner_factory=lambda t: FakePlanner(),
            min_successes=2,
        )
        self.assertEqual(entered, ["entered"])
        self.assertEqual([x.status for x in summary.task_outcomes], ["FAIL", "PASS"])
        self.assertEqual(summary.status, "FAIL")
        self.assertEqual(summary.task_outcomes[0].reason, "runner_error_TimeoutError")
        self.assertNotIn("SECRET_CANARY", str(summary))

    def test_untrusted_exception_names_cannot_enter_runner_reason_or_evidence(self):
        fake_name = "PRIVATE_FAKE_APIKEYError"
        fake_error = type(fake_name, (Exception,), {})

        def poisoned_factory(task_spec):
            raise fake_error("PRIVATE_FAKE_SECRET_MESSAGE")

        summary = run_batch(
            (task("A"),), browser_factory=poisoned_factory,
            planner_factory=lambda t: FakePlanner(), min_successes=1,
        )
        self.assertEqual(summary.status, "FAIL")
        self.assertEqual(summary.task_outcomes[0].reason, "runner_error_RunnerError")
        self.assertNotIn(fake_name, repr(summary))
        self.assertNotIn("PRIVATE_FAKE_SECRET_MESSAGE", repr(summary))

        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root = home / "data/SPRAY/output/g5" / "run-safe"
            with patch("spray_schema.g5_batch.Path.home", return_value=home):
                run_batch(
                    (task("A"),), browser_factory=poisoned_factory,
                    planner_factory=lambda t: FakePlanner(), min_successes=1,
                    evidence_dir=root,
                )
                payload = (root / "run.json").read_text()
                self.assertNotIn(fake_name, payload)
                self.assertNotIn("PRIVATE_FAKE_SECRET_MESSAGE", payload)
                self.assertIn("runner_error_RunnerError", payload)

    def test_untrusted_trace_exception_name_is_not_preserved(self):
        from spray_schema.g5_batch import _safe_trace
        malicious = _safe_trace((
            {"step": 1, "outcome": "planner_rejected",
             "error_type": "PRIVATE_FAKE_APIKEYError"},
        ))
        self.assertEqual(len(malicious), 1)
        self.assertNotIn("error_type", malicious[0])
        valid = _safe_trace((
            {"step": 1, "outcome": "action_failed", "error_type": "TimeoutError"},
            {"step": 2, "outcome": "planner_rejected",
             "error_type": "PlannerDecisionError"},
        ))
        self.assertEqual([item["error_type"] for item in valid],
                         ["TimeoutError", "PlannerDecisionError"])

    def test_goal_fingerprint_conservatively_rejects_display_aliases(self):
        original = task("A")
        duplicate = TaskSpec(
            task_id="B", url="https://preview.example.test/?state=other",
            approved_origin="https://preview.example.test",
            target_text="  Ｃｏｍｐｌｅｔｅｄ  ",
            requirements=(Requirement("OTHER-ID", "  Ｃｏｍｐｌｅｔｅｄ  ", "must"),),
            allowed_click_labels=("Read details",),
            max_steps=3,
        )
        genuinely_distinct = TaskSpec(
            task_id="C", url=original.url,
            approved_origin=original.approved_origin,
            target_text="Details displayed",
            requirements=(Requirement("MUST-2", "Details displayed", "must"),),
            allowed_click_labels=("Continue",),
        )
        self.assertEqual(_goal_fingerprint(original), _goal_fingerprint(duplicate))
        self.assertNotEqual(_goal_fingerprint(original), _goal_fingerprint(genuinely_distinct))
        self.assertEqual(len(_goal_fingerprint(original)), 64)

    def test_evidence_is_private_redacted_and_exclusive(self):
        with tempfile.TemporaryDirectory() as temp:
            home = Path(temp)
            root = home / "data/SPRAY/output/g5" / "run-A"
            with patch("spray_schema.g5_batch.Path.home", return_value=home):
                summary = run_batch(
                    (task("A"),),
                    browser_factory=lambda t: FakeBrowser(),
                    planner_factory=lambda t: FakePlanner(),
                    min_successes=1, evidence_dir=root,
                )
                self.assertEqual(summary.status, "PASS")
                payload = (root / "run.json").read_text()
                self.assertNotIn("SECRET_CANARY", payload)
                self.assertNotIn("preview.example.test", payload)
                self.assertEqual(json.loads(payload)["schema"], "spray.g5.batch.v1")
                self.assertEqual(json.loads(payload)["tasks"][0]["goal_fingerprint"],
                                 _goal_fingerprint(task("A")))
                self.assertEqual(stat.S_IMODE((root / "run.json").stat().st_mode), 0o600)
                with self.assertRaises(FileExistsError):
                    run_batch(
                        (task("A"),),
                        browser_factory=lambda t: FakeBrowser(),
                        planner_factory=lambda t: FakePlanner(),
                        min_successes=1, evidence_dir=root,
                    )
                with self.assertRaises(PermissionError):
                    run_batch(
                        (task("A"),),
                        browser_factory=lambda t: FakeBrowser(),
                        planner_factory=lambda t: FakePlanner(),
                        min_successes=1, evidence_dir=home / "outside",
                    )


if __name__ == "__main__":
    unittest.main()
