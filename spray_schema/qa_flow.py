"""Read-only UI recon and must/should/could quality gate around the existing SPRAY loop.

Concepts only from replica-recon/test/diff; no upstream code or assets copied.
The existing guarded browser adapter remains the sole action authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlsplit

from .agent_loop import run_loop

WEIGHTS = {"must": 3, "should": 2, "could": 1}


@dataclass(frozen=True)
class Requirement:
    case_id: str
    text: str
    priority: str = "must"

    def __post_init__(self):
        if (type(self.case_id) is not str or not 1 <= len(self.case_id) <= 32
                or not self.case_id.replace("-", "").replace("_", "").isalnum()
                or type(self.text) is not str or not 1 <= len(self.text) <= 100
                or type(self.priority) is not str or self.priority not in WEIGHTS):
            raise ValueError("invalid QA requirement")


@dataclass(frozen=True)
class QAResult:
    status: str
    reason: str
    quality_gate: str
    final_qa: dict
    trace: tuple[dict, ...]


class ReconBrowser:
    """Observe UI state and requirements without broadening browser permissions."""

    def __init__(self, browser, *, flow_id: str, requirements: tuple[Requirement, ...]):
        if (type(flow_id) is not str or not 1 <= len(flow_id) <= 32
                or not flow_id.replace("-", "").replace("_", "").isalnum()):
            raise ValueError("invalid flow ID")
        if (type(requirements) is not tuple or not 1 <= len(requirements) <= 12
                or any(type(r) is not Requirement for r in requirements)
                or len({r.case_id for r in requirements}) != len(requirements)):
            raise ValueError("invalid QA requirements")
        self.browser = browser
        self.flow_id = flow_id
        self.requirements = requirements
        self.last_qa: dict = {}

    def observe(self) -> dict:
        observation = self.browser.observe()
        if type(observation) is not dict:
            raise ValueError("invalid browser observation")
        checks = [
            {"case_id": req.case_id, "priority": req.priority,
             "verified": self.browser.verify(req.text) is True}
            for req in self.requirements
        ]
        total = sum(WEIGHTS[r.priority] for r in self.requirements)
        covered = sum(WEIGHTS[r["priority"]] for r in checks if r["verified"])
        missing = sorted((r for r in checks if not r["verified"]),
                         key=lambda r: -WEIGHTS[r["priority"]])
        url = observation.get("url", "")
        route = urlsplit(url).path if isinstance(url, str) else ""
        controls = observation.get("controls", [])
        counts = {"button": 0, "link": 0}
        if isinstance(controls, list):
            for item in controls:
                if isinstance(item, dict) and item.get("kind") in counts:
                    counts[item["kind"]] += 1
        states = {"forms": None, "inputs": None, "alerts": None,
                  "busy": None, "disabled": None}
        page = getattr(self.browser, "page", None)
        if page is not None:
            for key, selector in (("forms", "form"),
                                  ("inputs", "input, textarea, select"),
                                  ("alerts", "[role='alert']"),
                                  ("busy", "[aria-busy='true']"),
                                  ("disabled", "button:disabled, input:disabled")):
                states[key] = min(page.locator(selector).count(), 100)
        qa = {"flow_id": self.flow_id,
              "screen": {"route": route, "title": str(observation.get("title", ""))[:100]},
              "components": counts, "states": states,
              "requirements": checks,
              "missing_in_priority_order": [r["case_id"] for r in missing],
              "weighted_coverage_percent": round(100 * covered / total, 1)}
        self.last_qa = qa
        return {**observation, "url": route, "qa": qa}

    def verify(self, text: str) -> bool:
        return self.browser.verify(text) is True

    def click(self, index: int) -> None:
        self.browser.click(index)

    def wait(self, milliseconds: int) -> None:
        self.browser.wait(milliseconds)


def run_qa_flow(browser, planner, *, flow_id: str,
                requirements: tuple[Requirement, ...], target_text: str,
                max_steps: int = 4,
                stopped: Callable[[], bool] = lambda: False,
                require_alternate_on_failure: bool = False) -> QAResult:
    """Replan through the existing agent loop; independently gate every must."""
    wrapped = ReconBrowser(browser, flow_id=flow_id, requirements=requirements)
    if not any(r.text == target_text and r.priority == "must" for r in requirements):
        raise ValueError("verification target must be a must requirement")
    try:
        result = run_loop(wrapped, planner, target_text=target_text,
                          max_steps=max_steps, stopped=stopped,
                          require_alternate_on_failure=require_alternate_on_failure)
    except Exception as exc:
        return QAResult("FAIL", "browser_error_" + type(exc).__name__,
                        "FAIL", wrapped.last_qa, ())
    # Revocation after run_loop returns still wins. In particular, a paused
    # run must not skip final recon and reuse a previously PASS QA snapshot.
    if stopped():
        return QAResult("FAIL", "owner_pause", "FAIL", wrapped.last_qa, result.trace)
    # A final recon error must fail closed, without losing executed actions.
    try:
        wrapped.observe()
    except Exception as exc:
        if stopped():
            return QAResult("FAIL", "owner_pause", "FAIL", wrapped.last_qa, result.trace)
        return QAResult("FAIL", "final_recon_error_" + type(exc).__name__,
                        "FAIL", wrapped.last_qa, result.trace)
    # A PAUSE arriving during final per-requirement DOM checks must override
    # even a previously PASS run_loop result and a completed QA snapshot.
    if stopped():
        return QAResult("FAIL", "owner_pause", "FAIL", wrapped.last_qa, result.trace)
    qa = wrapped.last_qa
    if result.status != "PASS":
        gate = "FAIL"
    elif not qa or any(not r["verified"] and r["priority"] == "must"
                       for r in qa["requirements"]):
        gate = "NOT_VERIFIED"
    else:
        gate = "PASS"
    return QAResult(result.status, result.reason, gate, qa, result.trace)
