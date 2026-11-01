"""Opt-in G4 real-Chromium matrix on synthetic loopback fixtures only.

Runs 3 repetitions each: failure -> alternate -> DOM PASS; alternate
executed without DOM completion -> FAIL; repeated failed click -> FAIL.
No Gemini API calls, external network access, deployment or target writes.
This is real Chromium with deterministic planners, NOT live-model evidence.
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

from scripts.run_g3_gemini_chromium import TransitionPreview, _executed_alternate_after_failure
from spray_schema.qa_flow import Requirement, run_qa_flow
from spray_schema.playwright_preview import PreviewBrowser

URL = "http://127.0.0.1:8765/"
TARGET = "Completed"
REQS = (Requirement("MUST-01", TARGET, "must"),)
HTML = """<!doctype html><title>SPRAY G4 loopback</title>
<p id="status">Pending</p>
<button id="continue" type="button">Continue</button>
<button id="repair" type="button" hidden
onclick="document.getElementById('status').textContent='Completed'">Repair instead</button>
<script>window.__sprayArmTransition = () => {
document.getElementById('continue').disabled=true;
document.getElementById('repair').hidden=false;
};</script>"""
NEGATIVE_HTML = HTML.replace(
    "document.getElementById('status').textContent='Completed'",
    "document.getElementById('status').textContent='Pending'",
)


class FailOncePreview(PreviewBrowser):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._failed = False

    def click(self, index):
        if not self._failed:
            self._failed = True
            raise PermissionError("synthetic stale control")
        return super().click(index)


class ReplanPlanner:
    def choose(self, *, observation, target_text, history):
        if history:
            for i, control in enumerate(observation["controls"]):
                if control["label"] == "Repair instead" and control["enabled"]:
                    return {"action": "click", "index": i}
        for i, control in enumerate(observation["controls"]):
            if control["label"] == "Continue" and control["enabled"]:
                return {"action": "click", "index": i}
        return {"action": "finish"}


class RepeatPlanner:
    def choose(self, *, observation, target_text, history):
        return {"action": "click", "index": 0}


SCENARIOS = {
    "recovery": ("PASS", "independent_browser_verification", True, True),
    "no_dom": ("FAIL", "step_limit", False, True),
    "repeat_denied": ("FAIL", "repeated_failed_action", False, False),
}


def _evidence_root(argument: str | None) -> Path:
    base = (Path.home() / "data/SPRAY/output/g4").resolve()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    root = Path(argument).expanduser() if argument else base / ("chromium-" + stamp + "-" + uuid4().hex[:8])
    root = root.resolve()
    if root == base or base not in root.parents:
        raise PermissionError("G4 evidence must stay beneath ~/data/SPRAY/output/g4")
    if root.exists() or root.is_symlink():
        raise FileExistsError("refusing to overwrite G4 evidence")
    return root


def run_matrix(*, evidence_root: Path, chromium_path: str | None = None,
               repetitions: int = 3) -> dict:
    if type(repetitions) is not int or repetitions != 3:
        raise ValueError("G4 gate requires exactly three repetitions")
    if evidence_root.exists() or evidence_root.is_symlink():
        raise FileExistsError("G4 evidence directory already exists")
    evidence_root.mkdir(mode=0o700, parents=True, exist_ok=False)
    records = []
    browser_version = None
    for scenario, expected in SCENARIOS.items():
        for repeat in range(1, repetitions + 1):
            fixture = NEGATIVE_HTML if scenario == "no_dom" else HTML
            adapter = FailOncePreview if scenario == "repeat_denied" else TransitionPreview
            planner = RepeatPlanner() if scenario == "repeat_denied" else ReplanPlanner()
            try:
                with adapter(URL, allowed_origin=URL, chromium_path=chromium_path,
                             fixture_html=fixture) as browser:
                    browser_version = browser._browser.version
                    result = run_qa_flow(
                        browser, planner, flow_id="G4" + scenario.replace("_", ""),
                        requirements=REQS, target_text=TARGET, max_steps=2,
                        require_alternate_on_failure=True,
                    )
                    verified = browser.verify(TARGET)
                    alternate = _executed_alternate_after_failure(result.trace)
                    # All screenshots are of synthetic, credential-free fixtures.
                    browser.page.screenshot(
                        path=str(evidence_root / f"{scenario}-{repeat}.png"),
                        full_page=True,
                    )
                actual = (result.status, result.reason, verified, alternate)
                matched = actual == expected and (
                    result.quality_gate == ("PASS" if expected[0] == "PASS" else "FAIL")
                )
                records.append({
                    "scenario": scenario, "repetition": repeat,
                    "status": result.status, "reason": result.reason,
                    "quality_gate": result.quality_gate,
                    "dom_verified": verified, "alternate_click_proven": alternate,
                    "expected_match": matched,
                    "trace": list(result.trace),
                })
            except Exception as exc:
                records.append({
                    "scenario": scenario, "repetition": repeat,
                    "status": "FAIL", "reason": "runner_error_" + type(exc).__name__,
                    "quality_gate": "FAIL", "dom_verified": False,
                    "alternate_click_proven": False, "expected_match": False,
                    "trace": [],
                })
    verdict = "PASS" if len(records) == 9 and all(r["expected_match"] for r in records) else "FAIL"
    evidence = {
        "schema": "spray.g4.chromium_matrix.v1",
        "mode": "real_chromium_synthetic_loopback_deterministic_planner",
        "actual_model_call": False,
        "external_requests": "blocked_by_preview_adapter",
        "browser_version": browser_version,
        "repetitions_per_scenario": repetitions,
        "records": records,
        "final_verdict": verdict,
    }
    fd = os.open(evidence_root / "matrix.json", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(evidence, handle, indent=2, sort_keys=True)
        handle.write("\n")
    return evidence


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-dir")
    parser.add_argument("--chromium-path", default=os.environ.get("SPRAY_CHROMIUM_PATH"))
    args = parser.parse_args(argv)
    os.umask(0o077)
    root = _evidence_root(args.evidence_dir)
    result = run_matrix(evidence_root=root, chromium_path=args.chromium_path)
    print(json.dumps({
        "mode": result["mode"], "final_verdict": result["final_verdict"],
        "scenario_runs": len(result["records"]),
        "expected_matches": sum(r["expected_match"] for r in result["records"]),
        "evidence_dir": str(root),
    }, sort_keys=True))
    return 0 if result["final_verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
