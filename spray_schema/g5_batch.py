"""Bounded, auditable G5 read-only Preview QA batch runner.

This orchestrator does not grant browser permissions. Callers must supply a
trusted browser factory (normally PreviewBrowser with the task's exact origin
and click allowlist) and a bounded planner. No writes to target websites.
Evidence stores hashes and outcomes, never page text, credentials or URLs.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import unicodedata
from typing import Callable
from urllib.parse import urlsplit
from uuid import uuid4

from .playwright_preview import _origin, _unsafe_navigation, _unsafe_control_label
from .agent_loop import _KNOWN_EXCEPTION_NAMES, _safe_exception_type
from .qa_flow import Requirement, run_qa_flow

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_HEX64 = re.compile(r"^[a-f0-9]{64}$")
_OUTCOMES = frozenset({
    "executed", "action_failed", "planner_rejected",
    "repeated_failed_action", "unverified",
})
# These redacted labels are emitted by agent_loop itself. Reject all other
# exception class names, including identifiers that happen to end in Error.
_SAFE_TRACE_ERRORS = _KNOWN_EXCEPTION_NAMES | frozenset({
    "BrowserActionError", "PlannerDecisionError", "MissingControlIdentity",
})


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    url: str
    approved_origin: str
    target_text: str
    requirements: tuple[Requirement, ...]
    allowed_click_labels: tuple[str, ...]
    max_steps: int = 4
    require_alternate_on_failure: bool = False

    def __post_init__(self):
        if type(self.task_id) is not str or not _ID.fullmatch(self.task_id):
            raise ValueError("invalid task ID")
        if type(self.url) is not str or type(self.approved_origin) is not str:
            raise ValueError("invalid task URL or origin")
        origin = _origin(self.url)
        if origin != _origin(self.approved_origin):
            raise PermissionError("task outside approved origin")
        host = urlsplit(self.url).hostname
        if origin.startswith("http://") and host not in ("localhost", "127.0.0.1"):
            raise PermissionError("remote Preview requires HTTPS")
        if _unsafe_navigation(self.url):
            raise PermissionError("unsafe task URL")
        if type(self.target_text) is not str or not 1 <= len(self.target_text) <= 100:
            raise ValueError("invalid task target")
        if (type(self.requirements) is not tuple
                or not 1 <= len(self.requirements) <= 12
                or any(type(r) is not Requirement for r in self.requirements)
                or not any(r.text == self.target_text and r.priority == "must"
                           for r in self.requirements)):
            raise ValueError("target must be a must requirement")
        if (type(self.allowed_click_labels) is not tuple
                or not 1 <= len(self.allowed_click_labels) <= 15
                or any(type(label) is not str or not 1 <= len(label.strip()) <= 100
                       for label in self.allowed_click_labels)
                or len({label.strip().casefold() for label in self.allowed_click_labels})
                   != len(self.allowed_click_labels)):
            raise ValueError("explicit unique click allowlist required")
        # A manifest that explicitly approves a destructive control cannot
        # become a read-only task. Reject it before starting Chromium or
        # consuming free-tier model requests, not after a planner choice.
        if any(_unsafe_control_label(label.strip())
               for label in self.allowed_click_labels):
            raise PermissionError("unsafe click label cannot be approved")
        if type(self.max_steps) is not int or not 1 <= self.max_steps <= 10:
            raise ValueError("invalid task step cap")
        if type(self.require_alternate_on_failure) is not bool:
            raise ValueError("invalid strict recovery gate")


@dataclass(frozen=True)
class TaskOutcome:
    task_id: str
    status: str
    reason: str
    quality_gate: str
    max_steps: int
    trace: tuple[dict, ...]


@dataclass(frozen=True)
class BatchOutcome:
    run_id: str
    status: str
    successes: int
    min_successes: int
    task_outcomes: tuple[TaskOutcome, ...]


def _safe_trace(trace) -> tuple[dict, ...]:
    """Allowlist only non-sensitive fields from the action trace."""
    safe = []
    for entry in trace[:10]:
        if type(entry) is not dict:
            continue
        item = {}
        step = entry.get("step")
        if type(step) is int and 1 <= step <= 10:
            item["step"] = step
        for key in ("observation_sha256", "control_fingerprint"):
            value = entry.get(key)
            if type(value) is str and _HEX64.fullmatch(value):
                item[key] = value
        outcome = entry.get("outcome")
        if outcome in _OUTCOMES:
            item["outcome"] = outcome
        action = entry.get("action")
        if type(action) is dict:
            if action.get("action") == "click" and type(action.get("index")) is int:
                item["action"] = {"action": "click", "index": action["index"]}
            elif (action.get("action") == "wait"
                  and type(action.get("milliseconds")) is int):
                item["action"] = {"action": "wait", "milliseconds": action["milliseconds"]}
        elif action == "finish":
            item["action"] = "finish"
        error_type = entry.get("error_type")
        if type(error_type) is str and error_type in _SAFE_TRACE_ERRORS:
            item["error_type"] = error_type
        safe.append(item)
    return tuple(safe)


def _validate_evidence_dir(evidence_dir: str | Path) -> Path:
    """Reject invalid destinations before opening a browser or calling a model."""
    base = (Path.home() / "data/SPRAY/output/g5").resolve()
    root = Path(evidence_dir).expanduser().resolve()
    if root == base or base not in root.parents:
        raise PermissionError("G5 evidence must be inside ~/data/SPRAY/output/g5")
    if root.exists() or root.is_symlink():
        raise FileExistsError("refusing to overwrite existing G5 evidence")
    return root


def _goal_fingerprint(task: TaskSpec) -> str:
    """Redacted identity of observable MUST goals, independent of task ID/URL.

    A URL, click-label or case-ID change alone must not manufacture five
    separate QA outcomes. Hash only normalized expected text, never page data.
    """
    def normalize(value: str) -> str:
        normalized = unicodedata.normalize("NFKC", value)
        visible = "".join(ch for ch in normalized
                          if unicodedata.category(ch) != "Cf")
        return " ".join(visible.split()).casefold()

    must = sorted({normalize(req.text) for req in task.requirements
                   if req.priority == "must"})
    canonical = json.dumps([normalize(task.target_text), must],
                           ensure_ascii=False, separators=(",", ":"))
    return sha256(canonical.encode("utf-8")).hexdigest()


def _save_evidence(outcome: BatchOutcome, evidence_dir: str | Path,
                   tasks: tuple[TaskSpec, ...]) -> None:
    """Exclusive, private evidence directory beneath ~/data/SPRAY/output/g5."""
    root = _validate_evidence_dir(evidence_dir)
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    payload = {
        "schema": "spray.g5.batch.v1",
        "run_id": outcome.run_id,
        "status": outcome.status,
        "successes": outcome.successes,
        "min_successes": outcome.min_successes,
        "tasks": [
            {**asdict(item), "goal_fingerprint": _goal_fingerprint(task)}
            for item, task in zip(outcome.task_outcomes, tasks)
        ],
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(root / "run.json", flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, sort_keys=True, indent=2)
        handle.write("\n")


def run_batch(tasks: tuple[TaskSpec, ...], *, browser_factory,
              planner_factory, min_successes: int,
              stopped: Callable[[], bool] = lambda: False,
              evidence_dir: str | Path | None = None,
              stop_when_threshold_unreachable: bool = False) -> BatchOutcome:
    """Run up to five approved tasks, fail closed, and preserve bounded evidence.

    Factories are trusted dependencies, not delegated to the model. A
    PermissionError stops the entire batch; other task failures are isolated.
    """
    if (type(tasks) is not tuple or not 1 <= len(tasks) <= 5
            or any(type(t) is not TaskSpec for t in tasks)
            or len({t.task_id for t in tasks}) != len(tasks)):
        raise ValueError("expected 1-5 distinct approved tasks")
    if type(min_successes) is not int or not 1 <= min_successes <= len(tasks):
        raise ValueError("invalid success threshold")
    if not callable(browser_factory) or not callable(planner_factory) or not callable(stopped):
        raise ValueError("invalid runner dependencies")
    if type(stop_when_threshold_unreachable) is not bool:
        raise ValueError("invalid threshold stop mode")
    if evidence_dir is not None:
        _validate_evidence_dir(evidence_dir)
    run_id = uuid4().hex
    outcomes = []
    halted = False
    halt_reason = "owner_pause_or_halt"
    for task in tasks:
        # Do not consume additional Gemini RPD once the required success
        # count becomes unreachable. Keep unrun tasks visible in evidence.
        if not halted and stop_when_threshold_unreachable:
            remaining = len(tasks) - len(outcomes)
            successes_so_far = sum(o.status == "PASS" for o in outcomes)
            if successes_so_far + remaining < min_successes:
                halted = True
                halt_reason = "success_threshold_unreachable"
        if halted or stopped():
            halted = True
            outcomes.append(TaskOutcome(task.task_id, "NOT_RUN", halt_reason,
                                        "NOT_VERIFIED", task.max_steps, ()))
            continue
        browser = None
        try:
            # Retain the adapter even when __enter__ raises (for example,
            # blocked navigation during page.goto). A failed context enter
            # never binds an "as browser" variable, but its policy counters
            # must still stop the entire batch rather than be ignored.
            browser = browser_factory(task)
            with browser as active_browser:
                # A blocked request or denied control is a per-task STOP,
                # not a recoverable click failure. Do not ask the planner for
                # another action after the browser has denied a policy action.
                def task_stopped():
                    return (stopped()
                            or getattr(active_browser, "_blocked_policy_actions", 0) > 0
                            or getattr(active_browser, "_blocked_requests", 0) > 0)

                if task_stopped():
                    raise PermissionError("owner paused or browser policy denied")
                planner = planner_factory(task)
                result = run_qa_flow(
                    active_browser, planner, flow_id=task.task_id,
                    requirements=task.requirements, target_text=task.target_text,
                    max_steps=task.max_steps, stopped=task_stopped,
                    require_alternate_on_failure=task.require_alternate_on_failure,
                )
            owner_paused = stopped()
            policy_denied = (getattr(browser, "_blocked_policy_actions", 0) > 0
                             or getattr(browser, "_blocked_requests", 0) > 0)
            # DOM verification alone is not an unattended task execution:
            # a page already showing the target (or a wait-only trace) must
            # not inflate the G5 success count. Match the S7 evidence gate.
            executed_click = any(
                type(step) is dict and step.get("outcome") == "executed"
                and type(step.get("action")) is dict
                and step["action"].get("action") == "click"
                and type(step["action"].get("index")) is int
                # Match both the S7 evidence verifier and the real-model
                # click-provenance cap. A negative or impossible control index
                # is never evidence that a browser click actually executed.
                and 0 <= step["action"]["index"] < 100
                for step in result.trace
            )
            missing_action_proof = (result.status == "PASS"
                                    and result.quality_gate == "PASS"
                                    and not executed_click)
            success = (result.status == "PASS" and result.quality_gate == "PASS"
                       and executed_click and not owner_paused and not policy_denied)
            outcomes.append(TaskOutcome(
                task.task_id, "PASS" if success else "FAIL",
                ("owner_pause" if owner_paused else
                 "browser_policy_denied" if policy_denied else
                 "no_executed_click" if missing_action_proof else result.reason),
                ("FAIL" if owner_paused or policy_denied else
                 "NOT_VERIFIED" if missing_action_proof else result.quality_gate),
                task.max_steps, _safe_trace(result.trace),
            ))
            if owner_paused or policy_denied or result.reason == "owner_pause":
                halted = True
        except Exception as exc:
            # Navigation can fail inside __enter__ after a policy-blocked
            # request, before "with ... as browser" assigns its variable.
            # Treat any recorded denial as a batch-wide safety stop even
            # when Playwright raises TimeoutError instead of PermissionError.
            policy_denied = (browser is not None and
                             (getattr(browser, "_blocked_policy_actions", 0) > 0
                              or getattr(browser, "_blocked_requests", 0) > 0))
            is_permission = isinstance(exc, PermissionError)
            owner_paused = stopped()
            outcomes.append(TaskOutcome(
                task.task_id, "FAIL",
                ("browser_policy_denied" if policy_denied else
                 "owner_pause" if owner_paused else
                 "runner_error_" + _safe_exception_type(exc, "RunnerError")),
                "FAIL", task.max_steps, (),
            ))
            if policy_denied or is_permission or owner_paused:
                halted = True
    successes = sum(item.status == "PASS" for item in outcomes)
    status = ("PASS" if successes >= min_successes and not halted else "FAIL")
    summary = BatchOutcome(run_id, status, successes, min_successes, tuple(outcomes))
    if evidence_dir is not None:
        _save_evidence(summary, evidence_dir, tasks)
    return summary
