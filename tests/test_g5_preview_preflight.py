"""Offline G5 real-Preview CLI preflight tests; no API/browser calls."""
import contextlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from scripts.run_g5_preview_tasks import load_manifest, main, validate_execution, summarize_model_proof
from spray_schema.g5_batch import BatchOutcome, TaskOutcome


def manifest():
    return {"tasks": [
        {
            "task_id": f"Task{i}",
            "url": f"https://preview.zestfulpulse.com/read-only/task-{i}",
            "approved_origin": "https://preview.zestfulpulse.com",
            "target_text": "Completed",
            "requirements": [{"case_id": "MUST-01", "text": "Completed", "priority": "must"}],
            "allowed_click_labels": ["Open details"],
            "max_steps": 4,
        }
        for i in range(5)
    ]}


class G5PreviewPreflightTests(unittest.TestCase):
    def _write(self, directory, document):
        path = Path(directory) / "tasks.json"
        path.write_text(json.dumps(document), encoding="utf-8")
        return path

    def test_validation_only_never_requires_api_key_or_browser(self):
        with tempfile.TemporaryDirectory() as temp:
            path = self._write(temp, manifest())
            output = io.StringIO()
            with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(output):
                code = main(["--manifest", str(path)])
            self.assertEqual(code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["mode"], "validation_only")
            self.assertFalse(result["actual_model_call"])
            self.assertEqual(result["model_requests"], 0)
            self.assertFalse(result["evidence_goals_distinct"])
            self.assertEqual(len(result["tasks"]), 5)

    def test_rpd_reservation_blocks_insufficient_quota(self):
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            for i, item in enumerate(doc["tasks"]):
                item["target_text"] = f"Completed-{i}"
                item["requirements"][0]["text"] = item["target_text"]
            tasks = load_manifest(self._write(temp, doc))
        with patch.dict("os.environ", {"GEMINI_API_KEY": "offline-placeholder"}):
            with self.assertRaises(PermissionError):
                validate_execution(
                    tasks, allow_origin="https://preview.zestfulpulse.com",
                    owner_confirmed_origin=True, free_tier_confirmed=True,
                    rpd_limit=500, rpd_used=490, max_requests_per_task=4,
                )
            allowed = validate_execution(
                tasks, allow_origin="https://preview.zestfulpulse.com",
                owner_confirmed_origin=True, free_tier_confirmed=True,
                rpd_limit=500, rpd_used=480, max_requests_per_task=4,
            )
        self.assertEqual(allowed["reserved_requests"], 20)

    def test_live_g5_requires_five_distinct_observable_must_goals(self):
        # Different URLs can be distinct navigation workflows while
        # yielding the SAME proof fingerprint, making S7 inevitably FAIL.
        # Stop before real Gemini/Chromium can consume a free RPD budget.
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            tasks = load_manifest(self._write(temp, doc))
            with patch.dict("os.environ", {"GEMINI_API_KEY": "offline-placeholder"}):
                with self.assertRaisesRegex(ValueError, "distinct observable MUST goals"):
                    validate_execution(
                        tasks, allow_origin="https://preview.zestfulpulse.com",
                        owner_confirmed_origin=True, free_tier_confirmed=True,
                        rpd_limit=500, rpd_used=0, max_requests_per_task=4,
                    )
                for i, item in enumerate(doc["tasks"]):
                    item["target_text"] = f"Completed-{i}"
                    item["requirements"][0]["text"] = item["target_text"]
                unique = load_manifest(self._write(temp, doc))
                allowed = validate_execution(
                    unique, allow_origin="https://preview.zestfulpulse.com",
                    owner_confirmed_origin=True, free_tier_confirmed=True,
                    rpd_limit=500, rpd_used=0, max_requests_per_task=4,
                )
                self.assertEqual(allowed["reserved_requests"], 20)
            output = io.StringIO()
            with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(output):
                self.assertEqual(main(["--manifest", str(self._write(temp, doc))]), 0)
            self.assertTrue(json.loads(output.getvalue())["evidence_goals_distinct"])

    def test_requires_origin_owner_attestation_and_exact_match(self):
        with tempfile.TemporaryDirectory() as temp:
            tasks = load_manifest(self._write(temp, manifest()))
        with patch.dict("os.environ", {"GEMINI_API_KEY": "offline-placeholder"}):
            for origin, confirmed in (
                ("https://preview.zestfulpulse.com", False),
                ("https://other.zestfulpulse.com", True),
            ):
                with self.subTest(origin=origin, confirmed=confirmed):
                    with self.assertRaises(PermissionError):
                        validate_execution(
                            tasks, allow_origin=origin,
                            owner_confirmed_origin=confirmed, free_tier_confirmed=True,
                            rpd_limit=500, rpd_used=0, max_requests_per_task=4,
                        )

    def test_missing_api_key_blocks_execution(self):
        with tempfile.TemporaryDirectory() as temp:
            tasks = load_manifest(self._write(temp, manifest()))
        with patch.dict("os.environ", {}, clear=True):
            with self.assertRaises(PermissionError):
                validate_execution(
                    tasks, allow_origin="https://preview.zestfulpulse.com",
                    owner_confirmed_origin=True, free_tier_confirmed=True,
                    rpd_limit=500, rpd_used=0, max_requests_per_task=4,
                )

    def test_manifest_rejects_unicode_obfuscated_mutation_without_gemini(self):
        # Validation-only must deny visually mutating labels before browser
        # construction or Gemini requests, even with Unicode obfuscation.
        with tempfile.TemporaryDirectory() as temp:
            for label in ("ＤｅｌｅｔｅAccount", "Ｓａｖｅ changes",
                          "De\u200bleteAccount", "Sa\u2060ve changes"):
                with self.subTest(label=label):
                    document = manifest()
                    document["tasks"][0]["allowed_click_labels"] = [label]
                    with self.assertRaisesRegex(PermissionError, "unsafe click label"):
                        load_manifest(self._write(temp, document))

    def test_manifest_rejects_unsafe_owner_clicks_without_gemini(self):
        # Even an owner-supplied allowlist cannot authorize writes. Detect
        # impossible Preview tasks at validation-only entry, before browser
        # construction, Free Tier reservation, or a model API call.
        with tempfile.TemporaryDirectory() as temp:
            for label in ("DeleteAccount", "Publish-Draft", "Transfer Funds",
                          "Delete2FA", "DELETE2FA", "Transfer2Account",
                          "TRANSFER2ACCOUNT", "Publish2026",
                          "Save changes", "saveprofile", "CreateProject",
                          "Edit profile", "archive9logs", "publishdraft",
                          "transferfunds", "Confirm order", "Share file",
                          "삭제", "Upload"):
                with self.subTest(label=label):
                    document = manifest()
                    document["tasks"][0]["allowed_click_labels"] = [label]
                    with self.assertRaisesRegex(PermissionError, "unsafe click label"):
                        load_manifest(self._write(temp, document))
            safe = manifest()
            safe["tasks"][0]["allowed_click_labels"] = ["Open details"]
            self.assertEqual(len(load_manifest(self._write(temp, safe))), 5)

    def test_manifest_rejects_cross_origin_and_unapproved_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            doc["tasks"][1]["url"] = "https://other.example.test/"
            with self.assertRaises(PermissionError):
                load_manifest(self._write(temp, doc))
            doc = manifest()
            doc["tasks"][0]["dangerous_override"] = True
            with self.assertRaises(ValueError):
                load_manifest(self._write(temp, doc))
            doc = manifest()
            doc["tasks"][0]["allowed_click_labels"] = []
            with self.assertRaises(ValueError):
                load_manifest(self._write(temp, doc))

    def test_identical_workflows_with_five_unique_ids_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            # A manifest with five aliases of the same read-only workflow
            # must not be scored as five independent G5 evaluations.
            for item in doc["tasks"]:
                item["url"] = "https://preview.zestfulpulse.com/read-only"
            with self.assertRaisesRegex(ValueError, "distinct workflows"):
                load_manifest(self._write(temp, doc))

            # Case-ID renaming and allowlist ordering are not new workflows.
            for item in doc["tasks"]:
                item["requirements"][0]["case_id"] = f"REQ-{item['task_id']}"
            with self.assertRaisesRegex(ValueError, "distinct workflows"):
                load_manifest(self._write(temp, doc))

            # Five different target assertions on one page are independent.
            for item in doc["tasks"]:
                item["target_text"] = f"Completed-{item['task_id']}"
                item["requirements"][0]["text"] = item["target_text"]
            self.assertEqual(len(load_manifest(self._write(temp, doc))), 5)

    def test_policy_variants_cannot_count_same_goal_as_five_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            # All five tasks assert the same UI goal on the same page.
            # Only click permissions and recovery flags differ.
            for i, item in enumerate(doc["tasks"]):
                item["url"] = "https://preview.zestfulpulse.com/read-only"
                item["allowed_click_labels"] = [f"Open details {i}"]
                item["require_alternate_on_failure"] = bool(i % 2)
                item["max_steps"] = i + 1
            with self.assertRaisesRegex(ValueError, "five distinct workflows"):
                load_manifest(self._write(temp, doc))

            # Genuine different UI goals remain eligible even when sharing
            # the same page and using different allowed controls.
            for i, item in enumerate(doc["tasks"]):
                item["target_text"] = f"Completed-{i}"
                item["requirements"][0]["text"] = item["target_text"]
            self.assertEqual(len(load_manifest(self._write(temp, doc))), 5)

    def test_unicode_case_spacing_and_should_variants_are_not_distinct_goals(self):
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            aliases = ("Completed", " completed ", "Ｃｏｍｐｌｅｔｅｄ",
                       "COMPLETED", "\u200bCompleted")
            for i, item in enumerate(doc["tasks"]):
                item["url"] = "https://preview.zestfulpulse.com/read-only"
                item["target_text"] = aliases[i]
                item["requirements"][0]["text"] = aliases[i]
                if i:
                    item["requirements"].append({
                        "case_id": f"SHOULD-{i}", "text": f"optional-{i}",
                        "priority": "should",
                    })
            with self.assertRaisesRegex(ValueError, "five distinct workflows"):
                load_manifest(self._write(temp, doc))

    def test_percent_encoded_unreserved_paths_cannot_inflate_five_tasks(self):
        # RFC 3986 unreserved chars (%72 = r, %2D = -) are equivalent
        # to their literal representation. Distinct URL spellings cannot
        # manufacture five independent observable goals.
        variants = (
            "/read-only?tab=overview",
            "/r%65ad-only?tab=overview",
            "/read%2Donly?tab=overview",
            "/read-onl%79?tab=overview",
            "/%72ead-only?tab=overview",
        )
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            for item, suffix in zip(doc["tasks"], variants):
                item["url"] = "https://preview.zestfulpulse.com" + suffix
            with self.assertRaisesRegex(ValueError, "five distinct workflows"):
                load_manifest(self._write(temp, doc))
            # Actual different query states must remain distinguishable.
            for i, item in enumerate(doc["tasks"]):
                item["url"] = ("https://preview.zestfulpulse.com/read-only"
                               f"?tab={i}")
            self.assertEqual(len(load_manifest(self._write(temp, doc))), 5)

    def test_percent_encoded_unreserved_hash_routes_are_same_goal(self):
        variants = (
            "#/reports?tab=overview",
            "#/%72eports?tab=overview",
            "#/re%70orts?tab=overview",
            "#/report%73?tab=overview",
            "#/%72eport%73?tab=overview",
        )
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            for item, suffix in zip(doc["tasks"], variants):
                item["url"] = ("https://preview.zestfulpulse.com/read-only"
                               + suffix)
            with self.assertRaisesRegex(ValueError, "five distinct workflows"):
                load_manifest(self._write(temp, doc))
            for i, item in enumerate(doc["tasks"]):
                item["url"] = ("https://preview.zestfulpulse.com/read-only"
                               f"#/reports?tab={i}")
            self.assertEqual(len(load_manifest(self._write(temp, doc))), 5)

    def test_analytics_only_url_aliases_are_not_independent_tasks(self):
        # Distinct tracking tags do not imply five distinct read-only views.
        # The browser must still navigate the exact owner-approved URLs.
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            for i, item in enumerate(doc["tasks"]):
                item["url"] = (
                    "https://preview.zestfulpulse.com/read-only"
                    f"?utm_source=campaign-{i}&fbclid=click-{i}"
                )
            with self.assertRaisesRegex(ValueError, "five distinct workflows"):
                load_manifest(self._write(temp, doc))

            # Genuine route state still distinguishes workflows even when
            # each has marketing tags attached.
            for i, item in enumerate(doc["tasks"]):
                item["url"] = (
                    "https://preview.zestfulpulse.com/read-only"
                    f"?tab={i}&utm_source=campaign-{i}&gclid=click-{i}"
                )
            self.assertEqual(len(load_manifest(self._write(temp, doc))), 5)


    def test_unrelated_query_key_order_cannot_inflate_five_task_score(self):
        # All five URLs describe the same query state. Ordering parameters
        # or varying tracking tags cannot manufacture independent objectives.
        permutations = (
            "tab=overview&mode=compact&view=cards",
            "tab=overview&view=cards&mode=compact",
            "mode=compact&tab=overview&view=cards",
            "mode=compact&view=cards&tab=overview",
            "view=cards&tab=overview&mode=compact",
        )
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            for i, item in enumerate(doc["tasks"]):
                item["url"] = (
                    "https://preview.zestfulpulse.com/read-only?"
                    + permutations[i] + f"&utm_source=alias-{i}"
                )
            with self.assertRaisesRegex(ValueError, "five distinct workflows"):
                load_manifest(self._write(temp, doc))

            # Genuine per-task state differences remain five valid goals.
            for i, item in enumerate(doc["tasks"]):
                item["url"] = (
                    "https://preview.zestfulpulse.com/read-only?"
                    + permutations[i] + f"&tab_state={i}"
                )
            self.assertEqual(len(load_manifest(self._write(temp, doc))), 5)

    def test_spa_hash_query_aliases_do_not_inflate_five_task_evaluation(self):
        # Hash-router views can be selected without any HTTP navigation.
        # All five aliases below describe the same observable state.
        permutations = (
            "tab=overview&mode=compact&view=cards",
            "tab=overview&view=cards&mode=compact",
            "mode=compact&tab=overview&view=cards",
            "mode=compact&view=cards&tab=overview",
            "view=cards&tab=overview&mode=compact",
        )
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            for i, item in enumerate(doc["tasks"]):
                item["url"] = (
                    "https://preview.zestfulpulse.com/read-only#/view?"
                    + permutations[i] + f"&utm_source=campaign-{i}"
                )
            with self.assertRaisesRegex(ValueError, "five distinct workflows"):
                load_manifest(self._write(temp, doc))

            # Different SPA state keys remain valid separate QA objectives.
            for i, item in enumerate(doc["tasks"]):
                item["url"] = (
                    "https://preview.zestfulpulse.com/read-only#/view?"
                    + permutations[i] + f"&tab_state={i}&fbclid=click-{i}"
                )
            self.assertEqual(len(load_manifest(self._write(temp, doc))), 5)

    def test_spa_hash_query_repeated_values_keep_order(self):
        from scripts.run_g5_preview_tasks import _semantic_route

        base = "https://preview.zestfulpulse.com/read-only#/view?"
        first = base + "tab=overview&mode=compact&tab=details"
        reordered_keys = base + "mode=compact&tab=overview&tab=details"
        reordered_values = base + "mode=compact&tab=details&tab=overview"
        self.assertEqual(_semantic_route(first), _semantic_route(reordered_keys))
        self.assertNotEqual(_semantic_route(first), _semantic_route(reordered_values))

    def test_repeated_query_key_value_order_retains_identity(self):
        from scripts.run_g5_preview_tasks import _semantic_route

        base = "https://preview.zestfulpulse.com/read-only?"
        ordered = base + "tab=overview&view=cards&tab=details"
        unrelated_key_reordered = base + "view=cards&tab=overview&tab=details"
        repeated_value_reordered = base + "view=cards&tab=details&tab=overview"
        self.assertEqual(_semantic_route(ordered),
                         _semantic_route(unrelated_key_reordered))
        self.assertNotEqual(_semantic_route(ordered),
                            _semantic_route(repeated_value_reordered))

    def test_invisible_only_target_is_not_an_observable_goal(self):
        with tempfile.TemporaryDirectory() as temp:
            doc = manifest()
            doc["tasks"][0]["target_text"] = " \u200b "
            doc["tasks"][0]["requirements"][0]["text"] = " \u200b "
            with self.assertRaisesRegex(ValueError, "visible text"):
                load_manifest(self._write(temp, doc))

    def test_manifest_rejects_more_or_fewer_than_five_tasks(self):
        with tempfile.TemporaryDirectory() as temp:
            for n in (0, 4, 6):
                with self.subTest(n=n):
                    doc = manifest()
                    doc["tasks"] = doc["tasks"][:n] if n <= 5 else doc["tasks"] + [doc["tasks"][0]]
                    with self.assertRaises(ValueError):
                        load_manifest(self._write(temp, doc))


    def test_live_proof_requires_four_model_backed_successes_not_just_dom(self):
        outcomes = tuple(
            TaskOutcome(f"Task{i}", "PASS", "independent_browser_verification",
                        "PASS", 4, ({"outcome": "executed",
                                     "action": {"action": "click", "index": 0}},))
            for i in range(5)
        )
        batch = BatchOutcome("synthetic", "PASS", 5, 4, outcomes)
        planners = {
            f"Task{i}": SimpleNamespace(requests=1, responses_received=1,
                                        max_requests=4, action_history=[
                                            {"decision": {"action": "click", "index": 0}}])
            for i in range(3)
        }
        proof = summarize_model_proof(batch, planners, reserved_requests=20)
        self.assertEqual(proof["final_verdict"], "FAIL")
        self.assertEqual(proof["autonomous_successes"], 3)
        self.assertEqual(proof["model_requests"], 3)
        planners["Task3"] = SimpleNamespace(requests=1, responses_received=1,
                                            max_requests=4, action_history=[
                                                {"decision": {"action": "click", "index": 0}}])
        proof = summarize_model_proof(batch, planners, reserved_requests=20)
        self.assertEqual(proof["final_verdict"], "PASS")
        self.assertEqual(proof["autonomous_successes"], 4)
        self.assertEqual(proof["tasks"][0]["model_decision_trace"],
                         [{"action": "click", "index": 0}])
        self.assertNotIn("sanitized_observation", str(proof))
        self.assertNotIn("api_key", str(proof).lower())

    def test_live_proof_rejects_exceeded_budget_and_unanswered_requests(self):
        outcomes = tuple(
            TaskOutcome(f"Task{i}", "PASS", "independent_browser_verification",
                        "PASS", 4, ({"outcome": "executed",
                                     "action": {"action": "click", "index": 0}},))
            for i in range(5)
        )
        batch = BatchOutcome("synthetic", "PASS", 5, 4, outcomes)
        planners = {
            f"Task{i}": SimpleNamespace(requests=2, responses_received=1,
                                        max_requests=4, action_history=[
                                            {"decision": {"action": "click", "index": 0}}])
            for i in range(5)
        }
        self.assertEqual(
            summarize_model_proof(batch, planners, reserved_requests=9)["final_verdict"],
            "FAIL",
        )
        self.assertEqual(
            summarize_model_proof(batch, planners, reserved_requests=10)["final_verdict"],
            "PASS",
        )
        planners["Task4"] = SimpleNamespace(requests=5, responses_received=5,
                                            max_requests=4, action_history=[
                                                {"decision": {"action": "click", "index": 0}}])
        self.assertEqual(
            summarize_model_proof(batch, planners, reserved_requests=20)["final_verdict"],
            "FAIL",
        )


    def test_http_response_without_valid_decision_or_executed_click_is_not_proof(self):
        outcome = TaskOutcome(
            "Task0", "PASS", "independent_browser_verification", "PASS", 4,
            ({"outcome": "executed", "action": {"action": "wait", "milliseconds": 500}},),
        )
        batch = BatchOutcome("synthetic", "PASS", 1, 1, (outcome,))
        planner = SimpleNamespace(
            requests=1, responses_received=1, max_requests=4,
            action_history=[{"decision": {"action": "wait", "milliseconds": 500}}],
        )
        proof = summarize_model_proof(batch, {"Task0": planner}, reserved_requests=4)
        self.assertEqual(proof["final_verdict"], "FAIL")
        self.assertEqual(proof["autonomous_successes"], 0)
        self.assertFalse(proof["tasks"][0]["model_click_executed"])
        planner.action_history = []
        proof = summarize_model_proof(batch, {"Task0": planner}, reserved_requests=4)
        self.assertEqual(proof["final_verdict"], "FAIL")
        self.assertEqual(proof["tasks"][0]["model_decisions"], 0)

    def test_model_click_proof_requires_ordered_one_to_one_action_trace(self):
        # Two model choices with the same indices in the wrong order must not
        # be mistaken for a model-backed executed click.
        from scripts.run_g5_preview_tasks import _executed_model_click
        outcome = TaskOutcome(
            "Task0", "PASS", "independent_browser_verification", "PASS", 4,
            ({"step": 1, "outcome": "executed",
              "action": {"action": "wait", "milliseconds": 100}},
             {"step": 2, "outcome": "executed",
              "action": {"action": "click", "index": 0}}),
        )
        planner = SimpleNamespace(action_history=[
            {"decision": {"action": "wait", "milliseconds": 100}},
            {"decision": {"action": "click", "index": 0}},
        ])
        self.assertTrue(_executed_model_click(outcome, planner))
        planner.action_history.reverse()
        self.assertFalse(_executed_model_click(outcome, planner))
        planner.action_history = [{"decision": {"action": "click", "index": 0}}]
        self.assertFalse(_executed_model_click(outcome, planner))

        # Index-set matching previously accepted this reordered pair.
        swapped = TaskOutcome(
            "Task0", "PASS", "independent_browser_verification", "PASS", 4,
            ({"step": 1, "outcome": "executed",
              "action": {"action": "click", "index": 1}},
             {"step": 2, "outcome": "executed",
              "action": {"action": "click", "index": 0}}),
        )
        planner.action_history = [
            {"decision": {"action": "click", "index": 0}},
            {"decision": {"action": "click", "index": 1}},
        ]
        self.assertFalse(_executed_model_click(swapped, planner))

    def test_transport_attempt_without_response_is_not_a_real_model_proof(self):
        outcome = TaskOutcome("Task0", "PASS",
                              "independent_browser_verification", "PASS", 4, ())
        batch = BatchOutcome("synthetic", "PASS", 1, 1, (outcome,))
        planner = SimpleNamespace(requests=1, responses_received=0,
                                  max_requests=4)
        proof = summarize_model_proof(
            batch, {"Task0": planner}, reserved_requests=4,
        )
        self.assertTrue(proof["model_request_attempted"])
        self.assertFalse(proof["actual_model_call"])
        self.assertEqual(proof["final_verdict"], "FAIL")


if __name__ == "__main__":
    unittest.main()
