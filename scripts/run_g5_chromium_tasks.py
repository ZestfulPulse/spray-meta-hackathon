"""Opt-in five-task G5 Chromium smoke drill on synthetic loopback pages.

All five tasks are distinct read-only UI checks. A deterministic test planner
selects observed controls; this does NOT prove autonomous Gemini operation or
five real Cloudflare Preview tasks. No model API, login, external writes or
Production traffic. Use --chromium-path or SPRAY_CHROMIUM_PATH if needed.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.run_g3_gemini_chromium import TransitionPreview
from spray_schema.g5_batch import TaskSpec, run_batch
from spray_schema.playwright_preview import PreviewBrowser
from spray_schema.qa_flow import Requirement

URL = "http://127.0.0.1:8765/"
BUTTONS = {
    "G5Overview": "Open overview",
    "G5Details": "Navigate to details",
    "G5Insights": "Show insights",
    "G5Section": "Expand section",
    "G5Recovery": "Continue",
}

# Five separate observable DOM goals. Varying only click permissions or
# control labels must never inflate the G5 five-task evaluation.
TARGETS = {
    "G5Overview": "Overview opened",
    "G5Details": "Details displayed",
    "G5Insights": "Insights visible",
    "G5Section": "Section expanded",
    "G5Recovery": "Recovery completed",
}


def fixture(task_id: str) -> str:
    if task_id == "G5Recovery":
        return """<!doctype html><title>G5 recovery</title>
<p id="status">Pending</p>
<button id="continue" type="button">Continue</button>
<button id="repair" type="button" hidden
onclick="document.getElementById('status').textContent='Completed'">Repair instead</button>
<script>window.__sprayArmTransition = () => {
document.getElementById('continue').disabled=true;
document.getElementById('repair').hidden=false;
};</script>""".replace("='Completed'", f"='{TARGETS[task_id]}'")
    label = BUTTONS[task_id]
    return ("""<!doctype html><title>G5 synthetic QA</title>
<p id="status">Pending</p><button type="button"
onclick="document.getElementById('status').textContent='Completed'">"""
            + label + "</button>").replace(
                "='Completed'", f"='{TARGETS[task_id]}'")


class ObservedLabelPlanner:
    def __init__(self, label: str):
        self.label = label

    def choose(self, *, observation, target_text, history):
        wanted = "Repair instead" if self.label == "Continue" and history else self.label
        for index, control in enumerate(observation["controls"]):
            if control.get("label") == wanted and control.get("enabled") is True:
                return {"action": "click", "index": index}
        return {"action": "finish"}


def build_tasks() -> tuple[TaskSpec, ...]:
    tasks = []
    for task_id, label in BUTTONS.items():
        labels = (label, "Repair instead") if task_id == "G5Recovery" else (label,)
        target = TARGETS[task_id]
        tasks.append(TaskSpec(
            task_id=task_id, url=URL, approved_origin=URL,
            target_text=target,
            requirements=(Requirement("MUST-01", target, "must"),),
            allowed_click_labels=labels,
            max_steps=3 if task_id == "G5Recovery" else 2,
            require_alternate_on_failure=task_id == "G5Recovery",
        ))
    # Match the real Preview preflight's semantic distinctness rule. A smoke
    # matrix of one goal cloned five ways must not be called 5/5.
    goals = {
        (task.url, task.target_text,
         tuple(sorted((req.text, req.priority) for req in task.requirements)))
        for task in tasks
    }
    if len(goals) != len(tasks):
        raise ValueError("G5 synthetic QA requires five distinct observable goals")
    return tuple(tasks)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--chromium-path", default=os.environ.get("SPRAY_CHROMIUM_PATH"))
    parser.add_argument("--evidence-dir", help="new directory under ~/data/SPRAY/output/g5")
    args = parser.parse_args(argv)
    os.umask(0o077)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    evidence_dir = args.evidence_dir or str(
        Path.home() / "data/SPRAY/output/g5" / ("chromium-" + stamp + "-" + uuid4().hex[:8])
    )

    def browser_factory(task):
        adapter = TransitionPreview if task.task_id == "G5Recovery" else PreviewBrowser
        return adapter(
            task.url, allowed_origin=task.approved_origin,
            chromium_path=args.chromium_path, fixture_html=fixture(task.task_id),
            allowed_click_labels=task.allowed_click_labels,
        )

    result = run_batch(
        build_tasks(), browser_factory=browser_factory,
        planner_factory=lambda task: ObservedLabelPlanner(BUTTONS[task.task_id]),
        min_successes=4, evidence_dir=evidence_dir,
    )
    print(json.dumps({
        "mode": "real_chromium_synthetic_loopback_deterministic_planner",
        "actual_model_call": False,
        "run_id": result.run_id, "status": result.status,
        "successes": result.successes, "total_tasks": len(result.task_outcomes),
        "evidence_dir": str(evidence_dir),
    }, sort_keys=True))
    return 0 if result.status == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
