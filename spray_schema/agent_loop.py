"""Bounded observe -> model decision -> browser action -> independent verification.

No autonomous financial or external writes. The planner is never an authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Callable


@dataclass(frozen=True)
class RunResult:
    status: str
    reason: str
    trace: tuple[dict, ...]


# Exception class names are metadata from dependencies, not trustworthy input.
# A 40-character identifier ending in "Error" can still encode private data
# if a dynamic class was created from page text. Preserve only known classes.
_KNOWN_EXCEPTION_NAMES = frozenset({
    "PermissionError", "TimeoutError", "ConnectionError", "OSError",
    "FileNotFoundError", "RuntimeError", "ValueError", "TypeError",
    "KeyError", "IndexError", "LookupError", "AttributeError",
    "AssertionError", "NotImplementedError", "UnicodeError",
    "ImportError", "EOFError", "MemoryError", "OverflowError",
})


def _safe_exception_type(exc: Exception, fallback: str) -> str:
    name = type(exc).__name__
    return name if name in _KNOWN_EXCEPTION_NAMES else fallback


def _decision(raw: object, observation: dict) -> dict:
    if type(raw) is not dict or set(raw) - {"action", "index", "milliseconds"}:
        raise ValueError("invalid model decision")
    action = raw.get("action")
    if action == "finish" and set(raw) == {"action"}:
        return {"action": "finish"}
    if action == "wait" and set(raw) == {"action", "milliseconds"}:
        ms = raw["milliseconds"]
        if type(ms) is not int or not 0 <= ms <= 2000:
            raise ValueError("invalid wait duration")
        return {"action": "wait", "milliseconds": ms}
    if action != "click" or set(raw) != {"action", "index"}:
        raise ValueError("tool not permitted")
    index = raw["index"]
    controls = observation.get("controls")
    if (type(index) is not int or type(controls) is not list
            or index < 0 or index >= len(controls)
            or controls[index].get("kind") not in ("button", "link")):
        raise ValueError("invalid observed control")
    return {"action": "click", "index": index}


def _control_fingerprint(observation: dict, decision: dict) -> str | None:
    """Identify a clicked control across index changes without storing its label.

    A dynamic page may reorder controls after a failed click. Comparing only
    indices would allow the same failed control at a new index, or reject a
    genuinely different control at the old index. Missing labels fall back to
    the conservative legacy index comparison.
    """
    if decision["action"] != "click":
        return None
    control = observation["controls"][decision["index"]]
    if type(control) is not dict:
        return None
    kind, label = control.get("kind"), control.get("label")
    if (kind not in ("button", "link") or type(label) is not str
            or not label.strip() or len(label) > 100):
        return None
    encoded = json.dumps([kind, label.strip()], ensure_ascii=False).encode("utf-8")
    return sha256(encoded).hexdigest()


def run_loop(browser, planner, *, target_text: str, max_steps: int = 4,
             stopped: Callable[[], bool] = lambda: False,
             require_alternate_on_failure: bool = False) -> RunResult:
    """Execute only observed clicks. Browser independently verifies the target.

    Planner.choose(observation=..., target_text=..., history=...) returns a
    structured action. Untrusted page content cannot expand the action catalog.
    """
    if not isinstance(target_text, str) or not 0 < len(target_text) <= 100:
        raise ValueError("invalid verification target")
    if type(max_steps) is not int or not 1 <= max_steps <= 10:
        raise ValueError("invalid step cap")
    if type(require_alternate_on_failure) is not bool:
        raise ValueError("invalid alternate-strategy gate")
    trace = []
    history = []
    # A wait is not a recovery strategy: keep a failed click pending until
    # a genuinely different click executes, even across intermediate waits.
    pending_failed_click = None
    pending_failed_fingerprint = None
    failed_action_seen = False
    alternate_click_executed = False
    for step in range(max_steps):
        if stopped():
            return RunResult("FAIL", "owner_pause", tuple(trace))
        observation = browser.observe()
        # Observation may take time; owner PAUSE must win before planning.
        if stopped():
            return RunResult("FAIL", "owner_pause", tuple(trace))
        verified = browser.verify(target_text)
        # A DOM check can finish after an owner PAUSE, even when incomplete.
        # Never consult a model or report success after consent is revoked.
        if stopped():
            return RunResult("FAIL", "owner_pause", tuple(trace))
        if verified:
            if require_alternate_on_failure and failed_action_seen and not alternate_click_executed:
                return RunResult("FAIL", "completion_without_alternate", tuple(trace))
            return RunResult("PASS", "independent_browser_verification", tuple(trace))
        digest = sha256(json.dumps(observation, sort_keys=True).encode()).hexdigest()
        try:
            proposed = planner.choose(observation=observation, target_text=target_text,
                                      history=tuple(history))
            # A model request can outlive an owner pause. Never execute its
            # stale decision after the owner has revoked permission to act.
            if stopped():
                return RunResult("FAIL", "owner_pause", tuple(trace))
            decision = _decision(proposed, observation)
            # A model choosing a control already observed as disabled is not
            # evidence of a browser race or a successful recovery attempt.
            # Keep this proof-only: legacy non-proof flows remain unchanged.
            if (require_alternate_on_failure and decision["action"] == "click"
                    and observation["controls"][decision["index"]].get("enabled") is False):
                raise ValueError("model selected an observed disabled control")
        except Exception as exc:
            trace.append({"step": step + 1, "observation_sha256": digest,
                          "outcome": "planner_rejected",
                          "error_type": _safe_exception_type(exc, "PlannerDecisionError")})
            return RunResult("FAIL", "planner_rejected", tuple(trace))
        # A wait must not erase the failed-click identity. Otherwise a model
        # could choose click(A) -> failed, wait -> click(A) and bypass the
        # strict alternate-strategy proof gate.
        if require_alternate_on_failure and pending_failed_click is not None and decision["action"] == "click":
            current_fingerprint = _control_fingerprint(observation, decision)
            # G5 uses this strict gate directly, not only the G3 evidence
            # verifier. Missing control identity must fail closed: indices
            # alone cannot prove a new control after a DOM reorder.
            if pending_failed_fingerprint is None or current_fingerprint is None:
                trace.append({"step": step + 1, "observation_sha256": digest,
                              "action": decision, "outcome": "planner_rejected",
                              "error_type": "MissingControlIdentity"})
                return RunResult("FAIL", "unverifiable_alternative", tuple(trace))
            if pending_failed_fingerprint == current_fingerprint:
                trace.append({"step": step + 1, "observation_sha256": digest,
                              "action": decision, "outcome": "repeated_failed_action",
                              "control_fingerprint": current_fingerprint})
                return RunResult("FAIL", "repeated_failed_action", tuple(trace))
        elif (require_alternate_on_failure and history and
              history[-1]["outcome"] == "action_failed" and
              decision == history[-1]["action"]):
            # Preserve the original guard for consecutive failed waits.
            trace.append({"step": step + 1, "observation_sha256": digest,
                          "action": decision, "outcome": "repeated_failed_action"})
            return RunResult("FAIL", "repeated_failed_action", tuple(trace))
        if decision["action"] == "finish":
            trace.append({"step": step + 1, "observation_sha256": digest,
                          "action": "finish", "outcome": "unverified"})
            return RunResult("FAIL", "finish_without_verification", tuple(trace))
        try:
            if decision["action"] == "click":
                browser.click(decision["index"])
            else:
                browser.wait(decision["milliseconds"])
            outcome = "executed"
        except Exception as exc:
            outcome = "action_failed"
            # Preserve the error *class* for S6 failure diagnosis, never the
            # exception message, which may contain page text or credentials.
            # Browser libraries may define arbitrary exception class names.
            action_error_type = _safe_exception_type(exc, "BrowserActionError")
            if decision["action"] == "click":
                failed_action_seen = True
                pending_failed_click = decision
                pending_failed_fingerprint = _control_fingerprint(observation, decision)
            history.append({"action": decision, "outcome": outcome,
                            "error_type": action_error_type})
        else:
            if decision["action"] == "click":
                if pending_failed_click is not None:
                    alternate_click_executed = True
                pending_failed_click = None
                pending_failed_fingerprint = None
            history.append({"action": decision, "outcome": outcome})
        trace_entry = {"step": step + 1, "observation_sha256": digest,
                       "action": decision, "outcome": outcome}
        if outcome == "action_failed":
            trace_entry["error_type"] = action_error_type
        # Bind a click to the observed control identity, not its mutable index.
        # Only the digest is persisted; raw control labels are never added to
        # execution traces or evidence.
        if decision["action"] == "click":
            fingerprint = _control_fingerprint(observation, decision)
            if fingerprint is not None:
                trace_entry["control_fingerprint"] = fingerprint
        trace.append(trace_entry)
        if stopped():
            return RunResult("FAIL", "owner_pause", tuple(trace))
        verified = browser.verify(target_text)
        # A DOM check can finish after an owner PAUSE, even when incomplete.
        # Never consult a model or report success after consent is revoked.
        if stopped():
            return RunResult("FAIL", "owner_pause", tuple(trace))
        if verified:
            if require_alternate_on_failure and failed_action_seen and not alternate_click_executed:
                return RunResult("FAIL", "completion_without_alternate", tuple(trace))
            return RunResult("PASS", "independent_browser_verification", tuple(trace))
    return RunResult("FAIL", "step_limit", tuple(trace))
