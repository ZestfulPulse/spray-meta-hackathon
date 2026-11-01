"""Offline G6 handoff evidence validation, with adversarial tampering cases."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

from scripts.verify_g45_evidence import evaluate, main, verify_g4, verify_g5_batch, verify_g5_model


def g4_matrix():
    expectations = {
        "recovery": ("PASS", "independent_browser_verification", True, True),
        "no_dom": ("FAIL", "step_limit", False, True),
        "repeat_denied": ("FAIL", "repeated_failed_action", False, False),
    }
    records = []
    for scenario, (status, reason, dom, alternate) in expectations.items():
        for repetition in (1, 2, 3):
            records.append({
                "scenario": scenario, "repetition": repetition,
                "status": status, "reason": reason,
                "dom_verified": dom, "alternate_click_proven": alternate,
                "quality_gate": "PASS" if status == "PASS" else "FAIL",
                "expected_match": True,
                "trace": [
                    {"step": 1, "outcome": "action_failed",
                     "action": {"action": "click", "index": 0},
                     "control_fingerprint": "a" * 64},
                    {"step": 2,
                     "outcome": ("repeated_failed_action" if scenario == "repeat_denied"
                                 else "executed"),
                     "action": {"action": "click",
                                "index": 0 if scenario == "repeat_denied" else 1},
                     "control_fingerprint": ("a" if scenario == "repeat_denied"
                                             else "b") * 64},
                ],
            })
    return {
        "schema": "spray.g4.chromium_matrix.v1",
        "mode": "real_chromium_synthetic_loopback_deterministic_planner",
        "actual_model_call": False, "external_requests": "blocked_by_preview_adapter",
        "browser_version": "Chromium 1.2.3",
        "final_verdict": "PASS", "repetitions_per_scenario": 3,
        "records": records,
    }


def g5_batch():
    return {
        "schema": "spray.g5.batch.v1", "run_id": "synthetic-run-1",
        "status": "PASS", "successes": 4, "min_successes": 4,
        "tasks": [
            {"task_id": f"Task{i}", "goal_fingerprint": f"{i + 1:064x}",
             "max_steps": 4,
             "status": "PASS" if i < 4 else "FAIL",
             "quality_gate": "PASS" if i < 4 else "FAIL",
             "trace": [{"outcome": "executed",
                        "action": {"action": "click", "index": 0}}] if i < 4 else []}
            for i in range(5)
        ],
    }


def model_proof():
    return {
        "schema": "spray.g5.model_proof.v1",
        "run_id": "synthetic-run-1",
        "mode": "owner_attested_free_tier_real_gemini_preview",
        "final_verdict": "PASS", "actual_model_call": True,
        "billing_tier": "owner_attested_free_not_independently_verified",
        "model_request_attempted": True,
        "model_requests": 4, "model_responses": 4,
        "reserved_requests": 20, "autonomous_successes": 4,
        "required_successes": 4,
        "tasks": [
            {"task_id": f"Task{i}", "qa_status": "PASS" if i < 4 else "FAIL",
             "model_requests": 1 if i < 4 else 0,
             "model_responses": 1 if i < 4 else 0,
             "model_decisions": 1 if i < 4 else 0,
             "model_decision_trace": ([{"action": "click", "index": 0}]
                                      if i < 4 else []),
             "model_click_executed": i < 4,
             "autonomous_success_proven": i < 4}
            for i in range(5)
        ],
    }


class G45EvidenceGateTests(unittest.TestCase):
    def test_synthetic_evidence_only_is_partial_not_a_live_pass(self):
        result = evaluate(g4=g4_matrix(), g5=g5_batch())
        self.assertEqual(result["readiness"], "PARTIAL")
        self.assertEqual(result["g5_real_model_proof"], "NOT_VERIFIED")

    def test_complete_consistent_evidence_passes_schema_gate(self):
        result = evaluate(g4=g4_matrix(), g5=g5_batch(), model=model_proof())
        self.assertEqual(result["readiness"], "PASS")
        self.assertEqual(result["g5_real_model_proof"], "PASS")

    def test_g4_rejects_raw_data_hidden_in_synthetic_matrix(self):
        # No raw secrets, URLs or arbitrary fields may enter signed-off G4
        # evidence, even when all nine scenario results still look correct.
        self.assertTrue(verify_g4(g4_matrix()))
        for mutation in ("root_raw", "external_allowed", "missing_policy",
                         "record_raw", "trace_raw", "action_url",
                         "boolean_dom", "boolean_step", "observation_raw",
                         "error_payload", "browser_version_url"):
            with self.subTest(mutation=mutation):
                matrix = g4_matrix()
                if mutation == "root_raw":
                    matrix["raw_password"] = "FAKE_SECRET"
                elif mutation == "external_allowed":
                    matrix["external_requests"] = "external_allowed"
                elif mutation == "missing_policy":
                    del matrix["external_requests"]
                elif mutation == "record_raw":
                    matrix["records"][0]["raw_page_text"] = "FAKE_PAGE"
                elif mutation == "trace_raw":
                    matrix["records"][0]["trace"][0]["raw_password"] = "FAKE_SECRET"
                elif mutation == "action_url":
                    matrix["records"][0]["trace"][0]["action"]["url"] = "https://example.invalid/private"
                elif mutation == "boolean_dom":
                    matrix["records"][0]["dom_verified"] = 1
                elif mutation == "boolean_step":
                    matrix["records"][0]["trace"][0]["step"] = True
                elif mutation == "observation_raw":
                    matrix["records"][0]["trace"][0]["observation_sha256"] = "FAKE_SECRET"
                elif mutation == "error_payload":
                    matrix["records"][0]["trace"][0]["error_type"] = "FAKE SECRET"
                else:
                    matrix["browser_version"] = "https://example.invalid/private"
                self.assertFalse(verify_g4(matrix))

    def test_g5_rejects_unexpected_raw_evidence_fields(self):
        self.assertTrue(verify_g5_batch(g5_batch()))
        for mutation in ("batch_raw", "task_raw", "task_url", "invalid_gate",
                         "reason_url", "run_id_url"):
            with self.subTest(mutation=mutation):
                batch = g5_batch()
                if mutation == "batch_raw":
                    batch["raw_page_text"] = "FAKE_PAGE"
                elif mutation == "task_raw":
                    batch["tasks"][-1]["raw_password"] = "FAKE_SECRET"
                elif mutation == "task_url":
                    batch["tasks"][0]["url"] = "https://example.invalid/private"
                elif mutation == "invalid_gate":
                    batch["tasks"][-1]["quality_gate"] = "FAKE_SECRET"
                elif mutation == "reason_url":
                    batch["tasks"][-1]["reason"] = "https://example.invalid/private"
                else:
                    batch["run_id"] = "https://example.invalid/private"
                self.assertFalse(verify_g5_batch(batch))

    def test_g4_g5_exception_provenance_rejects_plausible_raw_names(self):
        # A short ASCII name ending with Error is still untrusted because
        # a dependency may create an exception class from page content.
        for fake in ("PRIVATE_FAKE_APIKEYError", "가짜비밀Error",
                     "PRIVATE_FAKE_CLASS_" + "x" * 120):
            with self.subTest(error_name=fake):
                matrix = g4_matrix()
                matrix["records"][0]["trace"][0]["error_type"] = fake
                self.assertFalse(verify_g4(matrix))
                batch = g5_batch()
                batch["tasks"][0]["trace"][0]["error_type"] = fake
                self.assertFalse(verify_g5_batch(batch))
        matrix = g4_matrix()
        matrix["records"][0]["trace"][0]["error_type"] = "TimeoutError"
        self.assertTrue(verify_g4(matrix))
        batch = g5_batch()
        batch["tasks"][0]["trace"][0]["error_type"] = "TimeoutError"
        self.assertTrue(verify_g5_batch(batch))

    def test_g4_missing_repeat_and_false_dom_evidence_fail(self):
        matrix = g4_matrix()
        matrix["records"][0]["dom_verified"] = False
        self.assertFalse(verify_g4(matrix))
        matrix = g4_matrix()
        matrix["records"][-1]["repetition"] = 2
        self.assertFalse(verify_g4(matrix))
        matrix = g4_matrix()
        matrix["actual_model_call"] = True
        self.assertFalse(verify_g4(matrix))

    def test_g4_trace_is_required_not_just_self_reported_match(self):
        for mutation in ("missing", "no_failed_click", "same_control", "no_execution",
                         "wrong_repeat"):
            with self.subTest(mutation=mutation):
                matrix = g4_matrix()
                record = matrix["records"][0 if mutation != "wrong_repeat" else 6]
                if mutation == "missing":
                    record["trace"] = []
                elif mutation == "no_failed_click":
                    record["trace"][0]["outcome"] = "executed"
                elif mutation == "same_control":
                    record["trace"][1]["control_fingerprint"] = "a" * 64
                elif mutation == "no_execution":
                    record["trace"][1]["outcome"] = "unverified"
                else:
                    record["trace"][1]["outcome"] = "executed"
                self.assertFalse(verify_g4(matrix))
                self.assertEqual(evaluate(g4=matrix, g5=g5_batch())["readiness"],
                                 "FAIL")

    def test_g4_repeat_denial_requires_same_control_identity(self):
        for mutation in ("missing", "different", "invalid"):
            with self.subTest(mutation=mutation):
                matrix = g4_matrix()
                denied = matrix["records"][6]["trace"][1]
                if mutation == "missing":
                    denied.pop("control_fingerprint")
                elif mutation == "different":
                    denied["control_fingerprint"] = "b" * 64
                else:
                    denied["control_fingerprint"] = "invalid"
                self.assertFalse(verify_g4(matrix))

        # DOM reordering can change an index without changing the control.
        matrix = g4_matrix()
        matrix["records"][6]["trace"][1]["action"]["index"] = 1
        self.assertTrue(verify_g4(matrix))

    def test_duplicate_missing_or_invalid_goal_fingerprints_fail_closed(self):
        for mutation in ("duplicate", "missing", "invalid"):
            with self.subTest(mutation=mutation):
                batch = g5_batch()
                if mutation == "duplicate":
                    batch["tasks"][4]["goal_fingerprint"] = batch["tasks"][0]["goal_fingerprint"]
                elif mutation == "missing":
                    batch["tasks"][4].pop("goal_fingerprint")
                else:
                    batch["tasks"][4]["goal_fingerprint"] = "not-a-sha256"
                self.assertEqual(
                    evaluate(g4=g4_matrix(), g5=batch, model=model_proof())["readiness"],
                    "FAIL",
                )

    def test_g5_safety_halt_cannot_be_hidden_by_four_prior_successes(self):
        # A passing batch may tolerate one ordinary task error, but never a
        # revoked permission, unsafe browser action, or owner pause.
        for reason in ("owner_pause", "owner_pause_or_halt",
                       "browser_policy_denied", "runner_error_PermissionError"):
            with self.subTest(reason=reason):
                batch = g5_batch()
                batch["tasks"][4]["reason"] = reason
                self.assertFalse(verify_g5_batch(batch))
                self.assertEqual(evaluate(g4=g4_matrix(), g5=batch)["readiness"],
                                 "FAIL")

        batch = g5_batch()
        batch["tasks"][4]["reason"] = "runner_error_TimeoutError"
        self.assertTrue(verify_g5_batch(batch))

    def test_g5_pass_cannot_claim_unrun_or_inconsistent_quality_gate(self):
        batch = g5_batch()
        batch["tasks"][4].update(status="NOT_RUN",
                                  reason="success_threshold_unreachable",
                                  quality_gate="NOT_VERIFIED")
        self.assertFalse(verify_g5_batch(batch))

        batch = g5_batch()
        batch["tasks"][4]["quality_gate"] = "PASS"
        self.assertFalse(verify_g5_batch(batch))

        batch = g5_batch()
        batch["tasks"][4].update(
            status="PASS", quality_gate="FAIL",
            trace=[{"outcome": "executed",
                    "action": {"action": "click", "index": 0}}],
        )
        self.assertFalse(verify_g5_batch(batch))

        batch = g5_batch()
        batch["tasks"][4]["reason"] = ["malformed", "json"]
        self.assertFalse(verify_g5_batch(batch))

    def test_g5_dom_only_pass_without_executed_click_is_rejected(self):
        batch = g5_batch()
        batch["tasks"][0]["trace"] = []
        self.assertEqual(evaluate(g4=g4_matrix(), g5=batch)["readiness"], "FAIL")

    def test_g4_click_indices_require_realistic_browser_bounds(self):
        # Both the failed click and the alternative/repeated decision must
        # use the same 0..99 control cap as the Gemini proof adapter.
        for phase in ("failed", "recovery", "repeat_denied"):
            for index in (-1, 100, 1000, True):
                with self.subTest(phase=phase, index=index):
                    matrix = g4_matrix()
                    record = matrix["records"][6 if phase == "repeat_denied" else 0]
                    step = record["trace"][0 if phase == "failed" else 1]
                    step["action"]["index"] = index
                    self.assertFalse(verify_g4(matrix))
                    self.assertEqual(evaluate(g4=matrix, g5=g5_batch())["readiness"],
                                     "FAIL")
        matrix = g4_matrix()
        matrix["records"][0]["trace"][0]["action"]["index"] = 99
        matrix["records"][0]["trace"][1]["action"]["index"] = 99
        self.assertTrue(verify_g4(matrix))

    def test_g5_batch_rejects_out_of_range_executed_click_proof(self):
        # A self-reported executed click at index 100 must not certify G5
        # when the live model provenance gate would reject that index.
        for index in (-1, 100, 1000, True):
            with self.subTest(index=index):
                batch = g5_batch()
                batch["tasks"][0]["trace"][0]["action"]["index"] = index
                self.assertFalse(verify_g5_batch(batch))
                self.assertEqual(evaluate(g4=g4_matrix(), g5=batch)["readiness"],
                                 "FAIL")
        batch = g5_batch()
        batch["tasks"][0]["trace"][0]["action"]["index"] = 99
        self.assertTrue(verify_g5_batch(batch))

    def test_failing_fifth_task_cannot_hide_unsafe_or_raw_trace(self):
        # A passing 4/5 batch must validate *all five* traces. Otherwise a
        # failed final task may carry an unapproved action or raw page data
        # while the top-level evidence gate still reports PASS.
        bad_traces = (
            [{"outcome": "executed", "action":
              {"action": "navigate", "url": "https://example.invalid/FAKE_SECRET"}}],
            [{"outcome": "executed", "action": {"action": "click", "index": -1}}],
            [{"outcome": "executed", "action": {"action": "click", "index": 100}}],
            [{"outcome": "executed", "action": {"action": "click", "index": True}}],
            [{"outcome": "executed", "action": {"action": "wait", "milliseconds": 99999}}],
            [{"outcome": "executed"}],
            [{"outcome": "executed", "action": {"action": "click", "index": 0},
              "raw_password": "FAKE_SECRET"}],
            [{"outcome": [], "action": {"action": "click", "index": 0}}],
            ["not-an-entry"],
        )
        for trace in bad_traces:
            with self.subTest(trace=trace):
                batch = g5_batch()
                batch["tasks"][-1]["trace"] = trace
                self.assertFalse(verify_g5_batch(batch))
                self.assertEqual(
                    evaluate(g4=g4_matrix(), g5=batch)["readiness"], "FAIL",
                )

        # Failed tasks legitimately have no trace, a rejected planner with
        # no chosen action, or a bounded failed wait/finish.
        valid_traces = (
            [],
            [{"outcome": "planner_rejected"}],
            [{"outcome": "action_failed",
              "action": {"action": "wait", "milliseconds": 0}}],
            [{"outcome": "unverified", "action": "finish"}],
        )
        for trace in valid_traces:
            with self.subTest(valid=trace):
                batch = g5_batch()
                batch["tasks"][-1]["trace"] = trace
                self.assertTrue(verify_g5_batch(batch))

    def test_g5_numbered_trace_has_monotonic_step_identity(self):
        # The real runner numbers every action. Duplicate, out-of-order or
        # partial numbering must not certify a staged 4-of-5 proof.
        base = g5_batch()
        valid = [
            {"step": 1, "outcome": "action_failed",
             "action": {"action": "click", "index": 0}},
            {"step": 2, "outcome": "executed",
             "action": {"action": "click", "index": 1}},
        ]
        base["tasks"][0]["trace"] = valid
        self.assertTrue(verify_g5_batch(base))
        for steps in ((1, 1), (2, 1), (2, 3), (1, 3), (1, None)):
            with self.subTest(steps=steps):
                batch = copy.deepcopy(base)
                for event, number in zip(batch["tasks"][0]["trace"], steps):
                    if number is None:
                        event.pop("step")
                    else:
                        event["step"] = number
                self.assertFalse(verify_g5_batch(batch))
                self.assertEqual(evaluate(g4=g4_matrix(), g5=batch)["readiness"],
                                 "FAIL")
        batch = copy.deepcopy(base)
        batch["tasks"][0]["trace"][0]["step"] = None
        self.assertFalse(verify_g5_batch(batch))
        # Historical offline fixtures without numbering remain admissible,
        # but a partially numbered trace cannot silently drop identities.
        self.assertTrue(verify_g5_batch(g5_batch()))

    def test_task_step_cap_is_required_for_evidence(self):
        for bad in (None, 0, 11, True, "4"):
            with self.subTest(bad=bad):
                batch = g5_batch()
                batch["tasks"][0]["max_steps"] = bad
                self.assertFalse(verify_g5_batch(batch))
        batch = g5_batch()
        batch["tasks"][0]["max_steps"] = 1
        batch["tasks"][0]["trace"] *= 2
        self.assertFalse(verify_g5_batch(batch))

    def test_model_proof_rejects_requests_beyond_task_step_cap(self):
        # Before the fix, an attested proof could claim two API attempts for
        # a one-step task and still pass because only the global cap was used.
        # Extra attempted requests can burn Free Tier RPD without matching
        # the task-specific owner-approved limit.
        batch = g5_batch()
        batch["tasks"][0]["max_steps"] = 1
        proof = model_proof()
        proof["reserved_requests"] = 17
        proof["tasks"][0]["model_requests"] = 2
        proof["model_requests"] = 5
        self.assertTrue(verify_g5_batch(batch))
        self.assertFalse(verify_g5_model(proof, batch))
        self.assertEqual(evaluate(g4=g4_matrix(), g5=batch, model=proof)["readiness"],
                         "FAIL")
        # Exactly one request for a one-step task remains valid.
        proof["tasks"][0]["model_requests"] = 1
        proof["model_requests"] = 4
        self.assertTrue(verify_g5_model(proof, batch))

    def test_reservation_cannot_exceed_sum_of_task_step_caps(self):
        # A truthful preflight cannot reserve 20 Free Tier requests when
        # one of five tasks is explicitly limited to a single step.
        batch = g5_batch()
        batch["tasks"][0]["max_steps"] = 1
        proof = model_proof()
        self.assertTrue(verify_g5_batch(batch))
        self.assertFalse(verify_g5_model(proof, batch))
        proof["reserved_requests"] = 17
        self.assertTrue(verify_g5_model(proof, batch))

    def test_failed_fifth_model_proof_rejects_hidden_fields_and_actions(self):
        """An unqualified fifth task is still part of the safety evidence."""
        tamper_cases = (
            ("raw_navigation", {"model_decision_trace": [
                {"action": "navigate", "url": "https://example.invalid/FAKE_TOKEN"}]}),
            ("raw_credentials", {"model_decision_trace": {
                "raw_password": "FAKE_SECRET"}}),
            ("extra_decision", {"model_decision_trace": [
                {"action": "click", "index": 0}]}),
            ("null_trace", {"model_decision_trace": None}),
            ("inconsistent_status", {"qa_status": "PASS"}),
            ("raw_task_field", {"raw_password": "FAKE_SECRET"}),
        )
        for name, mutation in tamper_cases:
            with self.subTest(name=name):
                candidate = model_proof()
                candidate["tasks"][-1].update(mutation)
                self.assertFalse(verify_g5_model(candidate, g5_batch()))

    def test_model_proof_rejects_raw_fields_at_root(self):
        candidate = model_proof()
        candidate["raw_page_text"] = "FAKE_PAGE_DATA"
        self.assertFalse(verify_g5_model(candidate, g5_batch()))

    def test_g5_model_proof_must_match_same_batch_run_and_counters(self):
        batch = g5_batch()
        for mutation in ("run_id", "requests", "response", "task_id", "success"):
            with self.subTest(mutation=mutation):
                proof = copy.deepcopy(model_proof())
                if mutation == "run_id":
                    proof["run_id"] = "another-run"
                elif mutation == "requests":
                    proof["model_requests"] = 99
                elif mutation == "response":
                    proof["tasks"][0]["model_responses"] = 0
                elif mutation == "task_id":
                    proof["tasks"][0]["task_id"] = "Other"
                else:
                    proof["tasks"][0]["autonomous_success_proven"] = False
                self.assertFalse(verify_g5_model(proof, batch))
                self.assertEqual(
                    evaluate(g4=g4_matrix(), g5=batch, model=proof)["readiness"],
                    "FAIL",
                )

    def test_model_proof_rejects_paid_or_ambiguous_billing_attestation(self):
        batch = g5_batch()
        self.assertTrue(verify_g5_model(model_proof(), batch))
        for billing in ("missing", "paid", "unknown", None, True):
            with self.subTest(billing=billing):
                proof = model_proof()
                if billing == "missing":
                    del proof["billing_tier"]
                else:
                    proof["billing_tier"] = billing
                self.assertFalse(verify_g5_model(proof, batch))
        for attempted in ("missing", False, None, 0):
            with self.subTest(attempted=attempted):
                proof = model_proof()
                if attempted == "missing":
                    del proof["model_request_attempted"]
                else:
                    proof["model_request_attempted"] = attempted
                self.assertFalse(verify_g5_model(proof, batch))

    def test_response_without_browser_click_is_not_autonomous_proof(self):
        batch = g5_batch()
        batch["tasks"][0]["trace"] = []
        self.assertFalse(verify_g5_model(model_proof(), batch))
        batch = g5_batch()
        proof = model_proof()
        proof["tasks"][0]["model_decisions"] = 0
        self.assertFalse(verify_g5_model(proof, batch))
        proof = model_proof()
        proof["tasks"][0]["model_click_executed"] = False
        self.assertFalse(verify_g5_model(proof, batch))

    def test_ordered_model_provenance_rejects_stale_or_forged_click(self):
        batch = g5_batch()
        for mutation in ("wrong_index", "wrong_kind", "missing", "extra",
                         "unexecuted", "false_click"):
            with self.subTest(mutation=mutation):
                proof = copy.deepcopy(model_proof())
                changed = proof["tasks"][0]
                if mutation == "wrong_index":
                    changed["model_decision_trace"][0]["index"] = 3
                elif mutation == "wrong_kind":
                    changed["model_decision_trace"][0] = {"action": "wait",
                                                           "milliseconds": 100}
                elif mutation == "missing":
                    changed.pop("model_decision_trace")
                elif mutation == "extra":
                    changed["model_decision_trace"].append({"action": "click", "index": 0})
                elif mutation == "unexecuted":
                    altered = copy.deepcopy(batch)
                    altered["tasks"][0]["trace"][0]["outcome"] = "action_failed"
                    self.assertFalse(verify_g5_model(proof, altered))
                    continue
                else:
                    changed["model_click_executed"] = False
                self.assertFalse(verify_g5_model(proof, batch))

    def test_model_proof_accepts_ordered_wait_then_click_not_index_set(self):
        batch = g5_batch()
        proof = model_proof()
        batch["tasks"][0]["trace"] = [
            {"action": {"action": "wait", "milliseconds": 300}, "outcome": "executed"},
            {"action": {"action": "click", "index": 0}, "outcome": "executed"},
        ]
        proof["tasks"][0]["model_decisions"] = 2
        proof["tasks"][0]["model_requests"] = 2
        proof["tasks"][0]["model_responses"] = 2
        proof["tasks"][0]["model_decision_trace"] = [
            {"action": "wait", "milliseconds": 300},
            {"action": "click", "index": 0},
        ]
        proof["model_requests"] += 1
        proof["model_responses"] += 1
        self.assertTrue(verify_g5_model(proof, batch))
        proof["tasks"][0]["model_decision_trace"].reverse()
        self.assertFalse(verify_g5_model(proof, batch))

    def test_negative_click_indices_cannot_certify_g4_or_g5(self):
        # Agent decisions cannot click a negative observed-control index.
        # Evidence claiming an executed click at -1 is not execution proof.
        for scenario, row in (("recovery", 0), ("repeat_denied", 6)):
            for position in (0, 1):
                with self.subTest(scenario=scenario, position=position):
                    matrix = g4_matrix()
                    matrix["records"][row]["trace"][position]["action"]["index"] = -1
                    self.assertFalse(verify_g4(matrix))
        for row in range(4):
            with self.subTest(g5_pass_task=row):
                batch = g5_batch()
                batch["tasks"][row]["trace"][0]["action"]["index"] = -1
                self.assertFalse(verify_g5_batch(batch))
        self.assertTrue(verify_g4(g4_matrix()))
        self.assertTrue(verify_g5_batch(g5_batch()))

    def test_g5_task_ids_must_match_safe_runner_format(self):
        # TaskSpec only permits 1..32 ASCII letters/digits/_/-, starting
        # with a letter or digit. The evidence gate must enforce the same
        # policy before trusting arbitrary JSON, even for FAILED tasks.
        invalid = (
            "https://preview.example/FAKE_TOKEN",
            "A\\nFAKE_PAGE_TEXT",
            "Task name with spaces",
            "_leading",
            "T" * 33,
            "개인정보",
            "X" * 4096,
            "",
        )
        for identifier in invalid:
            with self.subTest(identifier_length=len(identifier)):
                batch = g5_batch()
                proof = model_proof()
                batch["tasks"][0]["task_id"] = identifier
                proof["tasks"][0]["task_id"] = identifier
                self.assertFalse(verify_g5_batch(batch))
                self.assertFalse(verify_g5_model(proof, batch))
                self.assertEqual(
                    evaluate(g4=g4_matrix(), g5=batch, model=proof)["readiness"],
                    "FAIL",
                )
        for identifier in ("T", "Safe_1-2", "T" * 32):
            with self.subTest(valid=identifier):
                batch = g5_batch()
                proof = model_proof()
                batch["tasks"][0]["task_id"] = identifier
                proof["tasks"][0]["task_id"] = identifier
                self.assertTrue(verify_g5_batch(batch))
                self.assertTrue(verify_g5_model(proof, batch))

    def test_unhashable_json_identifiers_fail_closed(self):
        # Untrusted JSON permits nested arrays/objects. A bad identifier must
        # produce a failed quality gate, never an uncaught TypeError.
        for invalid_id in ([], {"nested": ["opaque"]}):
            with self.subTest(invalid_type=type(invalid_id).__name__):
                matrix = g4_matrix()
                matrix["records"][0]["scenario"] = invalid_id
                self.assertFalse(verify_g4(matrix))
                self.assertEqual(evaluate(g4=matrix, g5=g5_batch())["readiness"],
                                 "FAIL")

                proof = model_proof()
                proof["tasks"][0]["task_id"] = invalid_id
                self.assertFalse(verify_g5_model(proof, g5_batch()))
                self.assertEqual(
                    evaluate(g4=g4_matrix(), g5=g5_batch(), model=proof)["readiness"],
                    "FAIL",
                )

    def test_unhashable_model_id_cli_returns_sanitized_failure(self):
        from contextlib import redirect_stdout
        from io import StringIO

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            matrix, batch, proof = (root / name for name in
                                    ("matrix.json", "batch.json", "proof.json"))
            matrix.write_text(json.dumps(g4_matrix()), encoding="utf-8")
            batch.write_text(json.dumps(g5_batch()), encoding="utf-8")
            invalid_proof = model_proof()
            invalid_proof["tasks"][0]["task_id"] = {"private": "SECRET_CANARY"}
            proof.write_text(json.dumps(invalid_proof), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                code = main(["--g4-matrix", str(matrix), "--g5-batch", str(batch),
                             "--g5-model-proof", str(proof)])
            self.assertEqual(code, 2)
            self.assertIn('"readiness": "FAIL"', output.getvalue())
            self.assertNotIn("SECRET_CANARY", output.getvalue())

    def test_deeply_nested_json_fails_closed_without_leaking_input(self):
        # A small (<1 MiB) but deeply nested JSON document may raise
        # RecursionError inside json.loads, not JSONDecodeError. All three
        # untrusted evidence inputs must fail closed through the CLI.
        from contextlib import redirect_stdout
        from io import StringIO

        nested = "[" * 12000 + '"SECRET_CANARY"' + "]" * 12000
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            paths = {name: root / (name + ".json")
                     for name in ("matrix", "batch", "proof")}
            valid = {"matrix": json.dumps(g4_matrix()),
                     "batch": json.dumps(g5_batch()),
                     "proof": json.dumps(model_proof())}
            for bad_input in paths:
                with self.subTest(evidence=bad_input):
                    for name, path in paths.items():
                        path.write_text(nested if name == bad_input
                                        else valid[name], encoding="utf-8")
                    output = StringIO()
                    with redirect_stdout(output):
                        code = main([
                            "--g4-matrix", str(paths["matrix"]),
                            "--g5-batch", str(paths["batch"]),
                            "--g5-model-proof", str(paths["proof"]),
                        ])
                    self.assertEqual(code, 2)
                    self.assertIn('"reason": "invalid_evidence"', output.getvalue())
                    self.assertIn('"readiness": "FAIL"', output.getvalue())
                    self.assertNotIn("SECRET_CANARY", output.getvalue())

    def test_duplicate_and_nonfinite_json_fails_closed(self):
        # stdlib json.loads normally accepts duplicate properties (last wins)
        # and NaN/Infinity. Such ambiguous proofs cannot certify S7.
        from contextlib import redirect_stdout
        from io import StringIO

        originals = {"matrix": json.dumps(g4_matrix()),
                     "batch": json.dumps(g5_batch()),
                     "proof": json.dumps(model_proof())}
        mutations = (
            ("matrix", '"final_verdict": "PASS"',
             '"final_verdict": "SECRET_CANARY", "final_verdict": "PASS"'),
            ("batch", '"status": "PASS"',
             '"status": "FAIL", "status": "PASS"'),
            ("proof", '"model_requests": 4',
             '"model_requests": 999, "model_requests": 4'),
            ("matrix", '"outcome": "action_failed"',
             '"outcome": "SECRET_CANARY", "outcome": "action_failed"'),
            ("proof", None, None),
        )
        for case_index, (slot, original, replacement) in enumerate(mutations):
            with self.subTest(case=case_index, slot=slot):
                with tempfile.TemporaryDirectory() as temp:
                    paths = {name: Path(temp) / (name + ".json")
                             for name in originals}
                    for name, source in originals.items():
                        if name == slot:
                            if original is None:
                                source = '{"untrusted": NaN, ' + source[1:]
                            else:
                                self.assertIn(original, source)
                                source = source.replace(original, replacement, 1)
                        paths[name].write_text(source, encoding="utf-8")
                    output = StringIO()
                    with redirect_stdout(output):
                        code = main([
                            "--g4-matrix", str(paths["matrix"]),
                            "--g5-batch", str(paths["batch"]),
                            "--g5-model-proof", str(paths["proof"]),
                        ])
                    self.assertEqual(code, 2)
                    self.assertIn('"reason": "invalid_evidence"', output.getvalue())
                    self.assertIn('"readiness": "FAIL"', output.getvalue())
                    self.assertNotIn("SECRET_CANARY", output.getvalue())

    def test_malformed_json_fails_without_page_data_echo(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            matrix = root / "matrix.json"
            batch = root / "run.json"
            matrix.write_text('{"secret": "SECRET_CANARY"', encoding="utf-8")
            batch.write_text(json.dumps(g5_batch()), encoding="utf-8")
            from contextlib import redirect_stdout
            from io import StringIO
            output = StringIO()
            with redirect_stdout(output):
                code = main(["--g4-matrix", str(matrix), "--g5-batch", str(batch)])
            self.assertEqual(code, 2)
            self.assertIn('"readiness": "FAIL"', output.getvalue())
            self.assertNotIn("SECRET_CANARY", output.getvalue())


if __name__ == "__main__":
    unittest.main()
