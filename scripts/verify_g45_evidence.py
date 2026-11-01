"""Offline G4+G5 evidence gate for G6 handoff; never executes a browser/model.

A passing unit-test run or a self-reported PASS field alone cannot establish
live autonomous readiness. The gate checks the nine-run G4 matrix, the five-task
G5 QA batch, and a separate real Gemini response proof. No URLs, page text,
credentials or raw traces are printed.
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
import sys


EXPECTED_G4 = {
    "recovery": ("PASS", "independent_browser_verification", True, True),
    "no_dom": ("FAIL", "step_limit", False, True),
    "repeat_denied": ("FAIL", "repeated_failed_action", False, False),
}



def _strict_json_object(pairs: list[tuple[str, object]]) -> dict:
    """Reject ambiguous evidence JSON keys at every nesting depth."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON property")
        result[key] = value
    return result


def _reject_nonfinite_json(value: str):
    """JSON evidence cannot contain Python's permissive NaN/Infinity tokens."""
    raise ValueError("non-finite JSON constant")


def _load(path: str | Path) -> dict:
    file = Path(path)
    if file.is_symlink() or not file.is_file() or file.stat().st_size > 1024 * 1024:
        raise ValueError("missing, linked or oversized evidence")
    document = json.loads(file.read_text(encoding="utf-8"),
                          object_pairs_hook=_strict_json_object,
                          parse_constant=_reject_nonfinite_json)
    if type(document) is not dict:
        raise ValueError("evidence root must be an object")
    return document


# Treat handoff artifacts as untrusted JSON. Any extra root, record or trace
# field can carry raw page text, URLs or credentials even when the verdict
# is otherwise consistent. The exact set matches the original G4 runner.
_G4_ROOT_FIELDS = {
    "schema", "mode", "actual_model_call", "external_requests",
    "browser_version", "repetitions_per_scenario", "records", "final_verdict",
}
_G4_RECORD_FIELDS = {
    "scenario", "repetition", "status", "reason", "quality_gate",
    "dom_verified", "alternate_click_proven", "expected_match", "trace",
}
_G4_TRACE_FIELDS = {
    "step", "observation_sha256", "control_fingerprint",
    "action", "outcome", "error_type",
}
_G5_ROOT_FIELDS = {
    "schema", "run_id", "status", "successes", "min_successes", "tasks",
}
_G5_TASK_FIELDS = {
    "task_id", "goal_fingerprint", "max_steps", "status",
    "reason", "quality_gate", "trace",
}

# Mirror spray_schema.g5_batch.TaskSpec's strict on-disk identifier grammar.
# Free-form JSON task IDs can otherwise smuggle URL/page text into a passing
# evidence artifact even when all other fields are allowlisted.
_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")


# Treat exception class metadata as untrusted, even if it has a plausible
# Python identifier grammar. Mirror the runtime closed diagnostic type list.
_SAFE_ERROR_TYPES = frozenset({
    "PermissionError", "TimeoutError", "ConnectionError", "OSError",
    "FileNotFoundError", "RuntimeError", "ValueError", "TypeError",
    "KeyError", "IndexError", "LookupError", "AttributeError",
    "AssertionError", "NotImplementedError", "UnicodeError",
    "ImportError", "EOFError", "MemoryError", "OverflowError",
}) | frozenset({
    "BrowserActionError", "PlannerDecisionError", "MissingControlIdentity",
})


def verify_g4(document: dict) -> bool:
    if (type(document) is not dict or set(document) != _G4_ROOT_FIELDS
            or document.get("external_requests") != "blocked_by_preview_adapter"
            or document.get("schema") != "spray.g4.chromium_matrix.v1"
            or document.get("mode") != "real_chromium_synthetic_loopback_deterministic_planner"
            or document.get("actual_model_call") is not False
            or document.get("final_verdict") != "PASS"
            or document.get("repetitions_per_scenario") != 3
            or type(document.get("browser_version")) is not str
            or not 1 <= len(document["browser_version"]) <= 80
            or not document["browser_version"].strip()
            or any(not (char.isascii() and
                        (char.isalnum() or char in " ._-"))
                   for char in document["browser_version"])):
        return False
    records = document.get("records")
    if type(records) is not list or len(records) != 9:
        return False
    seen = set()
    for item in records:
        if type(item) is not dict or set(item) != _G4_RECORD_FIELDS:
            return False
        scenario, repetition = item.get("scenario"), item.get("repetition")
        if (type(scenario) is not str or scenario not in EXPECTED_G4
                or type(repetition) is not int
                or repetition not in (1, 2, 3) or (scenario, repetition) in seen):
            return False
        seen.add((scenario, repetition))
        status, reason, dom, alternate = EXPECTED_G4[scenario]
        if (type(item.get("dom_verified")) is not bool
                or type(item.get("alternate_click_proven")) is not bool):
            return False
        if (item.get("status"), item.get("reason"),
                item.get("dom_verified"), item.get("alternate_click_proven")) != (
                    status, reason, dom, alternate):
            return False
        if (item.get("quality_gate") != ("PASS" if status == "PASS" else "FAIL")
                or item.get("expected_match") is not True):
            return False
        # The matrix's self-reported expected_match is not action evidence.
        # Require two actual trace steps with an identifiable failed click,
        # then either a distinct executed recovery or a denied repeat.
        trace = item.get("trace")
        if (type(trace) is not list or len(trace) != 2
                or any(type(step) is not dict
                       or set(step) - _G4_TRACE_FIELDS for step in trace)):
            return False
        # Allowed keys still need typed, bounded values. Otherwise a
        # whitelisted name could carry raw page data instead of its digest.
        for step in trace:
            if "observation_sha256" in step:
                digest = step["observation_sha256"]
                if (type(digest) is not str or len(digest) != 64
                        or any(ch not in "0123456789abcdef" for ch in digest)):
                    return False
            if "error_type" in step:
                error = step["error_type"]
                if type(error) is not str or error not in _SAFE_ERROR_TYPES:
                    return False
        failed, followup = trace
        failed_action, next_action = failed.get("action"), followup.get("action")
        failed_id = failed.get("control_fingerprint")
        if (type(failed.get("step")) is not int
                or type(followup.get("step")) is not int
                or failed.get("step") != 1 or followup.get("step") != 2
                or failed.get("outcome") != "action_failed"
                or type(failed_action) is not dict
                or set(failed_action) != {"action", "index"}
                or failed_action.get("action") != "click"
                or type(failed_action.get("index")) is not int
                or not 0 <= failed_action["index"] < 100
                or type(failed_id) is not str or len(failed_id) != 64
                or any(c not in "0123456789abcdef" for c in failed_id)
                or type(next_action) is not dict
                or set(next_action) != {"action", "index"}
                or next_action.get("action") != "click"
                or type(next_action.get("index")) is not int
                or not 0 <= next_action["index"] < 100):
            return False
        if scenario == "repeat_denied":
            # Denying a second action does not prove the failed control was
            # reselected. Use stable identity even if the DOM index moves.
            if (followup.get("outcome") != "repeated_failed_action"
                    or followup.get("control_fingerprint") != failed_id):
                return False
        else:
            next_id = followup.get("control_fingerprint")
            if (followup.get("outcome") != "executed"
                    or type(next_id) is not str or len(next_id) != 64
                    or any(c not in "0123456789abcdef" for c in next_id)
                    or next_id == failed_id):
                return False
    return len(seen) == 9


def verify_g5_batch(document: dict) -> bool:
    if (type(document) is not dict or set(document) != _G5_ROOT_FIELDS
            or document.get("schema") != "spray.g5.batch.v1"
            or document.get("status") != "PASS"
            or type(document.get("min_successes")) is not int
            or document["min_successes"] != 4):
        return False
    run_id = document.get("run_id")
    if (type(run_id) is not str or not 1 <= len(run_id) <= 64
            or any(not (ch.isascii() and (ch.isalnum() or ch in "_-"))
                   for ch in run_id)):
        return False
    tasks = document.get("tasks")
    if type(tasks) is not list or len(tasks) != 5:
        return False
    ids = [item.get("task_id") for item in tasks if type(item) is dict]
    if (len(ids) != 5
            or any(type(i) is not str or not _TASK_ID.fullmatch(i) for i in ids)
            or len(set(ids)) != 5):
        return False
    # Require identity evidence for five distinct observable MUST goals.
    # Legacy artifacts with identical "Completed" targets have no proof of
    # five independent tasks and must never certify a current G6 handoff.
    fingerprints = [item.get("goal_fingerprint") for item in tasks]
    if (any(type(fp) is not str or len(fp) != 64
            or any(char not in "0123456789abcdef" for char in fp)
            for fp in fingerprints)
            or len(set(fingerprints)) != 5):
        return False
    # A 4/5 threshold never overrides a safety stop. run_batch marks the
    # entire batch FAIL after owner PAUSE or a browser policy denial, even if
    # four other tasks passed. Reject inconsistent/tampered evidence here too.
    safety_stop_reasons = {
        "owner_pause", "owner_pause_or_halt", "browser_policy_denied",
        "runner_error_PermissionError",
    }
    for item in tasks:
        # Fail closed on extra fields for both failed and passing tasks.
        # "reason" is optional in older synthetic fixtures, never arbitrary
        # new keys such as raw_page_text or URL.
        if set(item) - _G5_TASK_FIELDS:
            return False
        status, gate = item.get("status"), item.get("quality_gate")
        if gate not in ("PASS", "FAIL", "NOT_VERIFIED"):
            return False
        # TaskSpec enforces 1..10. Evidence missing the actual task step cap
        # cannot prove a model's Free Tier quota was respected.
        steps = item.get("max_steps")
        trace = item.get("trace")
        if (type(steps) is not int or not 1 <= steps <= 10
                or type(trace) is not list or len(trace) > steps):
            return False
        # A 4/5 score cannot conceal unsafe actions or raw page data in
        # the failed fifth task. Validate every recorded step, not just the
        # executed-click proof of successful tasks.
        allowed_fields = {"step", "observation_sha256", "control_fingerprint",
                          "outcome", "action", "error_type"}
        allowed_outcomes = {"executed", "action_failed", "planner_rejected",
                            "repeated_failed_action", "unverified"}
        for entry in trace:
            if type(entry) is not dict or set(entry) - allowed_fields:
                return False
            outcome = entry.get("outcome")
            if type(outcome) is not str or outcome not in allowed_outcomes:
                return False
            seq = entry.get("step")
            if "step" in entry and (type(seq) is not int or not 1 <= seq <= steps):
                return False
            for digest_key in ("observation_sha256", "control_fingerprint"):
                if digest_key in entry:
                    digest = entry[digest_key]
                    if (type(digest) is not str or len(digest) != 64
                            or any(ch not in "0123456789abcdef" for ch in digest)):
                        return False
            if "error_type" in entry:
                error_type = entry["error_type"]
                if (type(error_type) is not str
                        or error_type not in _SAFE_ERROR_TYPES):
                    return False
            action = entry.get("action")
            if action is None:
                if outcome != "planner_rejected":
                    return False
            elif action == "finish":
                if outcome != "unverified":
                    return False
            elif type(action) is dict:
                if action.get("action") == "click":
                    if (set(action) != {"action", "index"}
                            or type(action.get("index")) is not int
                            or not 0 <= action["index"] < 100):
                        return False
                elif action.get("action") == "wait":
                    if (set(action) != {"action", "milliseconds"}
                            or type(action.get("milliseconds")) is not int
                            or not 0 <= action["milliseconds"] <= 2000):
                        return False
                else:
                    return False
            else:
                return False
        # Real action traces are emitted with contiguous 1-based sequence
        # numbers. Preserve legacy wholly unnumbered synthetic fixtures, but
        # never certify mixed, repeated, reordered or skipped numbered steps.
        if (any("step" in entry for entry in trace)
                and any(entry.get("step") != number
                        for number, entry in enumerate(trace, 1))):
            return False
        reason = item.get("reason")
        if type(reason) not in (str, type(None)):
            return False
        if (type(reason) is str
                and (not 1 <= len(reason) <= 96
                     or not reason.isascii() or not reason.isidentifier())):
            return False
        if reason in safety_stop_reasons or status == "NOT_RUN":
            return False
        # A PASS requires a PASS quality gate. A failed task cannot claim a
        # passed gate; both inconsistencies would inflate a 4-of-5 report.
        if (status == "PASS") != (gate == "PASS"):
            return False
    # A pre-completed DOM is not a G5 autonomous task. Even the synthetic
    # batch quality gate needs a recorded executed browser click for each PASS.
    for item in tasks:
        if item.get("status") != "PASS":
            continue
        trace = item.get("trace")
        if (type(trace) is not list or not any(
                type(step) is dict and step.get("outcome") == "executed"
                and type(step.get("action")) is dict
                and step["action"].get("action") == "click"
                and type(step["action"].get("index")) is int
                and 0 <= step["action"]["index"] < 100
                for step in trace)):
            return False
    count = sum(item.get("status") == "PASS" and item.get("quality_gate") == "PASS"
                for item in tasks)
    if (count < 4 or document.get("successes") != count
            or any(item.get("status") not in ("PASS", "FAIL", "NOT_RUN")
                   for item in tasks)):
        return False
    return True


def verify_g5_model(document: dict, batch: dict) -> bool:
    proof_fields = {
        "schema", "run_id", "mode", "final_verdict", "actual_model_call",
        "billing_tier", "model_request_attempted", "model_requests",
        "model_responses", "reserved_requests", "autonomous_successes",
        "required_successes", "tasks",
    }
    if type(document) is not dict or set(document) != proof_fields:
        return False
    if (document.get("schema") != "spray.g5.model_proof.v1"
            or document.get("mode") != "owner_attested_free_tier_real_gemini_preview"
            or document.get("final_verdict") != "PASS"
            or document.get("run_id") != batch.get("run_id")
            or document.get("actual_model_call") is not True
            # A claimed no-cost proof must not omit or contradict the billing
            # provenance emitted by summarize_model_proof. This is still an
            # owner attestation, NOT independent provider billing validation.
            or document.get("billing_tier") != "owner_attested_free_not_independently_verified"
            or document.get("model_request_attempted") is not True
            or document.get("required_successes") != 4):
        return False
    requests, responses, reserved = (document.get("model_requests"),
                                     document.get("model_responses"),
                                     document.get("reserved_requests"))
    # The reservation is made before model execution using each task's
    # actual step cap. A proof cannot claim a larger RPD reservation than
    # the owner-approved five-task run could have required.
    maximum_reservation = sum(min(4, item["max_steps"]) for item in batch["tasks"])
    if (any(type(v) is not int for v in (requests, responses, reserved))
            or not 0 < responses <= requests <= reserved <= 20
            or reserved > maximum_reservation):
        return False
    tasks = document.get("tasks")
    if type(tasks) is not list or len(tasks) != 5:
        return False
    batch_by_id = {item["task_id"]: item for item in batch["tasks"]}
    # Evidence is untrusted JSON. Nested list/dict IDs must fail closed,
    # rather than raising TypeError while building a set of task IDs.
    task_ids = [item.get("task_id") for item in tasks if type(item) is dict]
    if (len(task_ids) != 5
            or any(type(task_id) is not str or not _TASK_ID.fullmatch(task_id)
                   for task_id in task_ids)
            or len(set(task_ids)) != 5
            or set(task_ids) != set(batch_by_id)):
        return False
    counted = 0
    counted_requests = 0
    counted_responses = 0
    for item in tasks:
        if type(item) is not dict:
            return False
        task_fields = {
            "task_id", "qa_status", "model_requests", "model_responses",
            "model_decisions", "model_decision_trace", "model_click_executed",
            "autonomous_success_proven",
        }
        if set(item) != task_fields:
            return False
        if item.get("qa_status") != batch_by_id[item["task_id"]].get("status"):
            return False
        req, resp = item.get("model_requests"), item.get("model_responses")
        decisions = item.get("model_decisions")
        if (type(req) is not int or type(resp) is not int
                or type(decisions) is not int
                or not 0 <= decisions <= resp <= req
                       <= min(4, batch_by_id[item["task_id"]]["max_steps"])
                or type(item.get("model_click_executed")) is not bool):
            return False
        counted_requests += req
        counted_responses += resp
        # A single coincident click is NOT Gemini action provenance. For every
        # claimed autonomous success, the redacted model decision sequence must
        # match the browser's executed/failed actions in the original order.
        # Unqualified failed tasks may have incomplete planner traces (4/5 gate).
        chosen = item.get("model_decision_trace")
        if type(chosen) is not list or len(chosen) != decisions:
            return False
        # Failed tasks are also evidence: never ignore raw payloads, injected
        # actions, or extra fields merely because they were not successful.
        for action in chosen:
            if type(action) is not dict:
                return False
            if action.get("action") == "click":
                if (set(action) != {"action", "index"}
                        or type(action.get("index")) is not int
                        or not 0 <= action["index"] < 100):
                    return False
            elif action.get("action") == "wait":
                if (set(action) != {"action", "milliseconds"}
                        or type(action.get("milliseconds")) is not int
                        or not 0 <= action["milliseconds"] <= 2000):
                    return False
            elif action != {"action": "finish"}:
                return False
        if item["model_click_executed"]:
            trace = batch_by_id[item["task_id"]].get("trace")
            if (type(chosen) is not list or type(trace) is not list
                    or len(chosen) != decisions or len(chosen) != len(trace)):
                return False
            executed_click = False
            for action, step in zip(chosen, trace):
                if type(action) is not dict or type(step) is not dict:
                    return False
                if action.get("action") == "click":
                    if (set(action) != {"action", "index"}
                            or type(action.get("index")) is not int
                            or not 0 <= action["index"] < 100):
                        return False
                    executed_click |= step.get("outcome") == "executed"
                elif action.get("action") == "wait":
                    if (set(action) != {"action", "milliseconds"}
                            or type(action.get("milliseconds")) is not int
                            or not 0 <= action["milliseconds"] <= 2000):
                        return False
                elif action == {"action": "finish"}:
                    pass
                else:
                    return False
                expected = "finish" if action["action"] == "finish" else action
                if step.get("action") != expected:
                    return False
            if not executed_click:
                return False
        proven = (batch_by_id[item["task_id"]]["status"] == "PASS"
                  and batch_by_id[item["task_id"]]["quality_gate"] == "PASS"
                  and item.get("qa_status") == "PASS"
                  and item["model_click_executed"] and decisions > 0)
        if item.get("autonomous_success_proven") is not proven:
            return False
        counted += int(proven)
    return (counted >= 4 and counted == document.get("autonomous_successes")
            and counted_requests == requests and counted_responses == responses)


def evaluate(*, g4: dict, g5: dict, model: dict | None = None) -> dict:
    g4_ok = verify_g4(g4)
    g5_ok = verify_g5_batch(g5)
    model_ok = g5_ok and model is not None and verify_g5_model(model, g5)
    return {
        "schema": "spray.g45.g6_handoff.v1",
        "g4_synthetic_chromium": "PASS" if g4_ok else "FAIL",
        "g5_batch_quality": "PASS" if g5_ok else "FAIL",
        "g5_real_model_proof": ("PASS" if model_ok else
                                "NOT_VERIFIED" if model is None else "FAIL"),
        "readiness": ("PASS" if g4_ok and g5_ok and model_ok else
                      "PARTIAL" if g4_ok and g5_ok and model is None else "FAIL"),
        "limitations": [
            "G4 uses a deterministic planner on synthetic loopback pages.",
            "G5 Free Tier is owner-attested, not independently verified.",
            "Evidence fields are self-reported; confirm original execution artifacts.",
        ],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--g4-matrix", required=True)
    parser.add_argument("--g5-batch", required=True)
    parser.add_argument("--g5-model-proof")
    args = parser.parse_args(argv)
    try:
        result = evaluate(
            g4=_load(args.g4_matrix), g5=_load(args.g5_batch),
            model=_load(args.g5_model_proof) if args.g5_model_proof else None,
        )
    except (ValueError, OSError, UnicodeError, json.JSONDecodeError, RecursionError):
        result = {"schema": "spray.g45.g6_handoff.v1",
                  "readiness": "FAIL", "reason": "invalid_evidence"}
    print(json.dumps(result, sort_keys=True))
    return 0 if result["readiness"] == "PASS" else 2


if __name__ == "__main__":
    sys.exit(main())
