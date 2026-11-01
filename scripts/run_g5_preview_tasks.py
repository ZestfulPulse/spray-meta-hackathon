"""G5 opt-in real-Preview evaluation from five owner-approved read-only tasks.

Default is validation-only (zero browser/model calls). Execution requires
explicit origin ownership, Gemini Free Tier, conservative RPD reservation,
a pre-existing API key, and exact click allowlists in the manifest. Only
GET/HEAD/OPTIONS within one HTTPS origin are permitted by PreviewBrowser.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
import re
from pathlib import Path
import sys
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from spray_schema.g5_batch import TaskSpec, run_batch, _goal_fingerprint
from spray_schema.gemini_planner import GeminiPlanner
from spray_schema.playwright_preview import PreviewBrowser, _origin
from spray_schema.qa_flow import Requirement


def _semantic_goal_text(value: str) -> str:
    """Collapse non-goal Unicode/case/spacing variants for five-task independence."""
    normalized = unicodedata.normalize("NFKC", value)
    # Zero-width formatting characters cannot make a duplicate UI goal new.
    visible = "".join(ch for ch in normalized
                      if unicodedata.category(ch) != "Cf")
    return " ".join(visible.split()).casefold()


def _canonical_unreserved(component: str) -> str:
    """Normalize RFC 3986 unreserved escapes for workflow identity only.

    %72eports and reports identify the same path/SPA route. Keep escapes
    for reserved delimiters (%2F, %3F, etc.) because they can select
    distinct server or router states; never rewrite the navigation URL.
    """
    def replace(match):
        char = chr(int(match.group(1), 16))
        if char.isascii() and (char.isalnum() or char in "-._~"):
            return char
        return "%" + match.group(1).upper()

    return re.sub(r"%([0-9a-fA-F]{2})", replace, component)


def _semantic_route(url: str) -> str:
    """Do not count marketing tags as independent observable Preview views.

    Canonicalize independent query-key order in both ordinary and SPA hash
    router queries, preserving the value order of repeated keys. Preserve
    actual path/route/anchor state. Ignore only known analytics fields for
    *goal identity*; navigation still uses the exact owner-approved URL.
    """
    parts = urlsplit(url)

    def canonical_query(query: str) -> str:
        analytics = {"gclid", "fbclid", "msclkid", "dclid"}
        relevant = [
            (key, value)
            for key, value in parse_qsl(query, keep_blank_values=True)
            if not (key.casefold().startswith("utm_")
                    or key.casefold() in analytics)
        ]
        # Preserve the order of repeated values for the same key.
        relevant.sort(key=lambda pair: pair[0])
        return urlencode(relevant)

    # Hash-router query parameters are as significant as ordinary URL
    # queries. Analytics tags and independent-key ordering inside #/view?...
    # must not manufacture five distinct G5 QA goals. Keep an optional
    # trailing nested anchor exactly as supplied.
    fragment = parts.fragment
    if "?" in fragment:
        route, _, raw_query = fragment.partition("?")
        query, anchor_mark, anchor = raw_query.partition("#")
        canonical = canonical_query(query)
        fragment = (_canonical_unreserved(route)
                    + ("?" + canonical if canonical else "")
                    + ("#" + _canonical_unreserved(anchor) if anchor_mark else ""))
    else:
        fragment = _canonical_unreserved(fragment)
    return urlunsplit((parts.scheme, parts.netloc,
                       _canonical_unreserved(parts.path),
                       canonical_query(parts.query), fragment))


def _semantic_workflow(task: TaskSpec) -> tuple:
    """Only independently observable MUST outcomes distinguish G5 workflows.

    Different task IDs, click permissions, retry knobs or optional SHOULD
    requirements are not new goals. Preserve meaningful route/query state
    (it can select different Preview views), but ignore known analytics tags.
    """
    target = _semantic_goal_text(task.target_text)
    must = tuple(sorted({_semantic_goal_text(req.text)
                         for req in task.requirements if req.priority == "must"}))
    if not target or not must or any(not text for text in must):
        raise ValueError("G5 target and MUST goals must contain visible text")
    return (_semantic_route(task.url), target, must)


def load_manifest(path: str | Path) -> tuple[TaskSpec, ...]:
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    if type(document) is not dict or set(document) != {"tasks"}:
        raise ValueError("manifest must contain only tasks")
    items = document["tasks"]
    if type(items) is not list or len(items) != 5:
        raise ValueError("G5 evaluation requires exactly five tasks")
    tasks = []
    required = {"task_id", "url", "approved_origin", "target_text",
                "requirements", "allowed_click_labels"}
    optional = {"max_steps", "require_alternate_on_failure"}
    for item in items:
        if type(item) is not dict or not required <= set(item) or set(item) - required - optional:
            raise ValueError("invalid task manifest fields")
        requirements = item["requirements"]
        if type(requirements) is not list or not 1 <= len(requirements) <= 12:
            raise ValueError("invalid task requirements")
        parsed = []
        for req in requirements:
            if type(req) is not dict or set(req) not in (
                {"case_id", "text"}, {"case_id", "text", "priority"}
            ):
                raise ValueError("invalid requirement fields")
            parsed.append(Requirement(**req))
        labels = item["allowed_click_labels"]
        if type(labels) is not list:
            raise ValueError("click allowlist must be a list")
        tasks.append(TaskSpec(
            task_id=item["task_id"], url=item["url"],
            approved_origin=item["approved_origin"],
            target_text=item["target_text"], requirements=tuple(parsed),
            allowed_click_labels=tuple(labels),
            max_steps=item.get("max_steps", 4),
            require_alternate_on_failure=item.get("require_alternate_on_failure", False),
        ))
    if len({task.task_id for task in tasks}) != 5:
        raise ValueError("duplicate task ID")
    # Five task IDs must represent five distinct observable QA goals.
    # Click permissions and recovery policy are execution safeguards, not
    # task goals: changing them must not inflate a 4-of-5 success rate.
    # Ignore superficial case IDs, allowlist variants, step caps and policy
    # toggles when determining whether two tasks are actually independent.
    workflows = {_semantic_workflow(task) for task in tasks}
    if len(workflows) != 5:
        raise ValueError("G5 evaluation requires five distinct workflows")
    if len({_origin(task.approved_origin) for task in tasks}) != 1:
        raise PermissionError("G5 tasks must share one approved Preview origin")
    return tuple(tasks)


def validate_execution(tasks, *, allow_origin: str | None,
                       owner_confirmed_origin: bool,
                       free_tier_confirmed: bool,
                       rpd_limit: int | None, rpd_used: int | None,
                       max_requests_per_task: int) -> dict:
    if owner_confirmed_origin is not True:
        raise PermissionError("owner must confirm Preview origin ownership")
    if not allow_origin or _origin(allow_origin) != _origin(tasks[0].approved_origin):
        raise PermissionError("CLI origin must match approved task origin")
    if free_tier_confirmed is not True:
        raise PermissionError("Gemini Free Tier attestation required")
    if (type(rpd_limit) is not int or type(rpd_used) is not int
            or not 1 <= rpd_limit <= 100000 or not 0 <= rpd_used <= rpd_limit):
        raise ValueError("valid current RPD limit and usage are required")
    if type(max_requests_per_task) is not int or not 1 <= max_requests_per_task <= 4:
        raise ValueError("invalid per-task request cap")
    reservation = sum(min(max_requests_per_task, t.max_steps) for t in tasks)
    if rpd_limit - rpd_used < reservation:
        raise PermissionError("insufficient attested free-tier daily quota")
    if not os.environ.get("GEMINI_API_KEY"):
        raise PermissionError("existing GEMINI_API_KEY is required")
    # A route change alone can pass the manifest workflow check, but S7
    # requires five distinct observable MUST-goal fingerprints. Refuse
    # a live evaluation that is guaranteed to fail the evidence gate,
    # before opening Chromium or spending any of the reserved free RPD.
    if len(tasks) != 5 or len({_goal_fingerprint(task) for task in tasks}) != 5:
        raise ValueError("G5 live execution requires five distinct observable MUST goals")
    return {"rpd_limit": rpd_limit, "rpd_used_before": rpd_used,
            "reserved_requests": reservation, "task_count": len(tasks)}


def _safe_model_decision_trace(decisions) -> list[dict] | None:
    """Persist only ordered click indices / wait lengths, never model payloads."""
    if type(decisions) not in (tuple, list) or len(decisions) > 4:
        return None
    safe = []
    for entry in decisions:
        if type(entry) is not dict or type(entry.get("decision")) is not dict:
            return None
        decision = entry["decision"]
        if (set(decision) == {"action", "index"}
                and decision.get("action") == "click"
                and type(decision["index"]) is int and 0 <= decision["index"] < 100):
            safe.append({"action": "click", "index": decision["index"]})
        elif (set(decision) == {"action", "milliseconds"}
              and decision.get("action") == "wait"
              and type(decision["milliseconds"]) is int
              and 0 <= decision["milliseconds"] <= 2000):
            safe.append({"action": "wait", "milliseconds": decision["milliseconds"]})
        elif decision == {"action": "finish"}:
            safe.append({"action": "finish"})
        else:
            return None
    return safe


def _executed_model_click(outcome, planner) -> bool:
    """Bind every recorded model choice to the corresponding browser action."""
    decisions = getattr(planner, "action_history", ()) if planner is not None else ()
    chosen = _safe_model_decision_trace(decisions)
    trace = outcome.trace
    if chosen is None or len(chosen) != len(trace):
        return False
    executed_model_click = False
    for decision, step in zip(chosen, trace):
        expected = "finish" if decision["action"] == "finish" else decision
        if type(step) is not dict or step.get("action") != expected:
            return False
        if decision["action"] == "click" and step.get("outcome") == "executed":
            executed_model_click = True
    return executed_model_click


def summarize_model_proof(batch, planners: dict, *, reserved_requests: int) -> dict:
    """Separate real model-decision evidence from a passing DOM quality gate.

    A page that already contains the target can pass QA without any model
    decision. It must NOT be counted as an autonomous Gemini success.
    """
    tasks = []
    total_requests = 0
    total_responses = 0
    qualified = 0
    caps_ok = True
    for outcome in batch.task_outcomes:
        planner = planners.get(outcome.task_id)
        requests = planner.requests if planner is not None else 0
        responses = planner.responses_received if planner is not None else 0
        max_requests = planner.max_requests if planner is not None else 0
        decisions = getattr(planner, "action_history", ()) if planner is not None else ()
        decision_count = len(decisions) if type(decisions) in (tuple, list) else -1
        valid = (type(requests) is int and type(responses) is int
                 and type(max_requests) is int and
                 0 <= decision_count <= responses <= requests <= max_requests)
        if not valid:
            caps_ok = False
        total_requests += requests if type(requests) is int and requests >= 0 else 0
        total_responses += responses if type(responses) is int and responses >= 0 else 0
        model_click = valid and _executed_model_click(outcome, planner)
        model_proven = (outcome.status == "PASS" and outcome.quality_gate == "PASS"
                        and model_click)
        qualified += int(model_proven)
        tasks.append({
            "task_id": outcome.task_id, "qa_status": outcome.status,
            "model_requests": requests, "model_responses": responses,
            "model_decisions": decision_count,
            "model_decision_trace": _safe_model_decision_trace(decisions),
            "model_click_executed": model_click,
            "autonomous_success_proven": model_proven,
        })
    passed = (batch.status == "PASS" and qualified >= batch.min_successes
              and caps_ok and total_requests <= reserved_requests)
    return {
        "schema": "spray.g5.model_proof.v1",
        "run_id": batch.run_id,
        "mode": "owner_attested_free_tier_real_gemini_preview",
        "billing_tier": "owner_attested_free_not_independently_verified",
        "model_request_attempted": total_requests > 0,
        "actual_model_call": total_responses > 0,
        "model_requests": total_requests,
        "model_responses": total_responses,
        "reserved_requests": reserved_requests,
        "autonomous_successes": qualified,
        "required_successes": batch.min_successes,
        "tasks": tasks,
        "final_verdict": "PASS" if passed else "FAIL",
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--execute", action="store_true", help="otherwise validate only")
    parser.add_argument("--allow-origin")
    parser.add_argument("--owner-confirmed-origin", action="store_true")
    parser.add_argument("--free-tier-confirmed", action="store_true")
    parser.add_argument("--rpd-limit", type=int)
    parser.add_argument("--rpd-used", type=int)
    parser.add_argument("--max-requests-per-task", type=int, default=4)
    parser.add_argument("--chromium-path", default=os.environ.get("SPRAY_CHROMIUM_PATH"))
    parser.add_argument("--evidence-dir", help="new path beneath ~/data/SPRAY/output/g5")
    args = parser.parse_args(argv)
    tasks = load_manifest(args.manifest)
    if not args.execute:
        print(json.dumps({
            "mode": "validation_only", "actual_model_call": False,
            "tasks": [task.task_id for task in tasks],
            "approved_origin_count": 1,
            "evidence_goals_distinct": (
                len({_goal_fingerprint(task) for task in tasks}) == 5
            ),
            "model_requests": 0,
        }, sort_keys=True))
        return 0

    budget = validate_execution(
        tasks, allow_origin=args.allow_origin,
        owner_confirmed_origin=args.owner_confirmed_origin,
        free_tier_confirmed=args.free_tier_confirmed,
        rpd_limit=args.rpd_limit, rpd_used=args.rpd_used,
        max_requests_per_task=args.max_requests_per_task,
    )
    os.umask(0o077)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = args.evidence_dir or str(
        Path.home() / "data/SPRAY/output/g5" / ("gemini-" + stamp + "-" + uuid4().hex[:8])
    )
    pause_file = Path.home() / "data/SPRAY/PAUSE"

    def browser_factory(task):
        return PreviewBrowser(
            task.url, allowed_origin=task.approved_origin,
            chromium_path=args.chromium_path,
            allowed_click_labels=task.allowed_click_labels,
        )

    planners = {}

    def planner_factory(task):
        planner = GeminiPlanner(
            free_tier_confirmed=True,
            max_requests=min(args.max_requests_per_task, task.max_steps),
        )
        planners[task.task_id] = planner
        return planner

    result = run_batch(
        tasks, browser_factory=browser_factory, planner_factory=planner_factory,
        min_successes=4, stopped=pause_file.exists, evidence_dir=evidence_dir,
        stop_when_threshold_unreachable=True,
    )
    proof = summarize_model_proof(
        result, planners, reserved_requests=budget["reserved_requests"],
    )
    # run_batch has already created a private evidence directory. Write the
    # model proof separately so a DOM-only PASS cannot masquerade as Gemini QA.
    fd = os.open(Path(evidence_dir) / "model_proof.json",
                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(proof, handle, indent=2, sort_keys=True)
        handle.write("\n")
    print(json.dumps({
        "mode": proof["mode"], "qa_status": result.status,
        "status": proof["final_verdict"], "successes": result.successes,
        "autonomous_successes": proof["autonomous_successes"],
        "model_requests": proof["model_requests"],
        "model_responses": proof["model_responses"],
        "task_count": len(result.task_outcomes), "run_id": result.run_id,
        "reserved_requests": budget["reserved_requests"],
        "evidence_dir": str(evidence_dir),
    }, sort_keys=True))
    return 0 if proof["final_verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
