"""Offline regression: G5 synthetic Chromium tasks must test five distinct goals.

This module imports fixtures only. It never starts Playwright, Gemini, or an
external Preview, and it does not assert that a browser smoke has passed.
"""
import unittest
from unittest.mock import patch

from scripts.run_g5_chromium_tasks import BUTTONS, TARGETS, build_tasks, fixture


class G5SyntheticGoalIndependenceTests(unittest.TestCase):
    def test_five_independent_observable_goals_match_fixture_actions(self):
        tasks = build_tasks()
        self.assertEqual(len(tasks), 5)
        self.assertEqual({task.task_id for task in tasks}, set(BUTTONS))
        goals = {
            (task.url, task.target_text,
             tuple(sorted((req.text, req.priority) for req in task.requirements)))
            for task in tasks
        }
        self.assertEqual(len(goals), 5)
        self.assertEqual({task.target_text for task in tasks}, set(TARGETS.values()))
        for task in tasks:
            with self.subTest(task=task.task_id):
                html = fixture(task.task_id)
                self.assertIn('<p id="status">Pending</p>', html)
                self.assertIn("textContent='" + task.target_text + "'", html)
                self.assertTrue(any(req.text == task.target_text
                                    and req.priority == "must"
                                    for req in task.requirements))
                self.assertNotIn('<p id="status">' + task.target_text + '</p>', html)

    def test_identical_goals_are_rejected_even_with_different_labels(self):
        # Before distinct goals, five otherwise different clicks all asserted
        # the same Completed text on the same synthetic URL.
        with patch.dict(TARGETS, {task_id: "Completed" for task_id in BUTTONS}):
            with self.assertRaisesRegex(ValueError, "distinct observable goals"):
                build_tasks()

    def test_recovery_task_retains_alternate_click_transition(self):
        recovery = next(task for task in build_tasks()
                        if task.task_id == "G5Recovery")
        html = fixture("G5Recovery")
        self.assertTrue(recovery.require_alternate_on_failure)
        self.assertIn('id="continue"', html)
        self.assertIn('id="repair"', html)
        self.assertIn("window.__sprayArmTransition", html)
        self.assertIn("textContent='" + recovery.target_text + "'", html)


if __name__ == "__main__":
    unittest.main()
