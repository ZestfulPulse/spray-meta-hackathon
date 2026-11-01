"""Run the opt-in, Free Tier gated G3 Gemini/Chromium failure-replan drill.

This script uses only a synthetic loopback page and system Chrome. The
--free-tier-confirmed switch is an owner attestation; do not set it until the
project's Billing Tier is confirmed as Free in Google AI Studio.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT))

from spray_schema.qa_flow import Requirement, run_qa_flow
from spray_schema.evidence_capture import capture_fixture_evidence
from spray_schema.gemini_planner import GeminiPlanner
from spray_schema.playwright_preview import PreviewBrowser


URL = "http://127.0.0.1:8765/"
TARGET = "Completed"
REQUIREMENTS = (
    Requirement("MUST-01", TARGET, "must"),
    Requirement("SHOULD-02", "Repair instead", "should"),
    Requirement("COULD-03", "Optional guidance", "could"),
)
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FIXTURE = """<!doctype html>
<html><head><title>Local G3 QA</title></head><body>
<p>Pending</p>
<p>Click Continue to complete the local QA task.</p>
<button id="continue" type="button">Continue</button>
<button id="repair" type="button" hidden
 onclick="document.querySelector('p').textContent='Completed'">Repair instead</button>
<script>
window.__sprayArmTransition = () => {
  document.getElementById('continue').disabled = true;
  document.getElementById('repair').hidden = false;
};
</script>
</body></html>"""


class TransitionPreview(PreviewBrowser):
    """Trigger a deterministic synthetic UI race before the first click."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._transition_armed = False

    def click(self, index):
        # The model observes an enabled control. The synthetic page changes
        # before its first attempted click, requiring an observed replan.
        # No model decision or tool selection is scripted by this fixture.
        if not self._transition_armed:
            self.page.evaluate("window.__sprayArmTransition()")
            self._transition_armed = True
        return super().click(index)


def _evidence_root(argument):
    data_root = (Path.home() / "data/SPRAY").resolve()
    if argument:
        root = Path(argument).expanduser()
    else:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        root = Path.home() / "data/SPRAY/output/g3" / f"gemini-live-{stamp}"
    root = root.resolve()
    if root == data_root or data_root not in root.parents:
        raise PermissionError("G3 evidence must stay under ~/data/SPRAY")
    if root.exists() or root.is_symlink():
        raise FileExistsError("evidence directory already exists; refusing overwrite")
    return root



def _executed_alternate_after_failure(trace) -> bool:
    """Require evidence that a *different observed control* was clicked.

    Browser controls can reorder between observations. Comparing numeric
    indices would falsely prove recovery when the same failed control moves
    to another index. Missing fingerprints fail closed, including for older
    traces, rather than claiming an unverified model-based alternative.
    """
    for position, step in enumerate(trace):
        failed_action = step.get("action")
        if (step.get("outcome") != "action_failed"
                or not isinstance(failed_action, dict)
                or failed_action.get("action") != "click"):
            continue
        failed_id = step.get("control_fingerprint")
        if not isinstance(failed_id, str) or len(failed_id) != 64:
            continue
        for later in trace[position + 1:]:
            later_action = later.get("action")
            later_id = later.get("control_fingerprint")
            if (later.get("outcome") == "executed"
                    and isinstance(later_action, dict)
                    and later_action.get("action") == "click"
                    and isinstance(later_id, str)
                    and len(later_id) == 64
                    and later_id != failed_id):
                return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--free-tier-confirmed", action="store_true", required=True,
        help="owner confirms the API key's project is Free Tier with quota remaining")
    parser.add_argument("--evidence-dir", help="new evidence directory under ~/data/SPRAY")
    args = parser.parse_args(argv)
    os.umask(0o077)
    evidence_root = _evidence_root(args.evidence_dir)
    planner = GeminiPlanner(free_tier_confirmed=args.free_tier_confirmed)
    fixture = FIXTURE

    try:
        with TransitionPreview(URL, allowed_origin=URL,
                              chromium_path=CHROME, fixture_html=fixture) as browser:
            browser_version = browser._browser.version
            result = run_qa_flow(
                browser, planner, flow_id="G3Local", requirements=REQUIREMENTS,
                target_text=TARGET, max_steps=4,
                require_alternate_on_failure=True,
            )
            verified = browser.verify(TARGET)
            screenshot = capture_fixture_evidence(
                browser, evidence_root, target_verified=verified)
    except Exception as exc:
        evidence_root.mkdir(mode=0o700, parents=True, exist_ok=True)
        record = {
            "schema": "spray.g3.gemini_chromium.v1",
            "model_mode": "real_gemini_api",
            "model_id": planner.model_id,
            "actual_model_call": planner.responses_received > 0,
            "request_attempts": planner.requests,
            "responses_received": planner.responses_received,
            "billing_tier": "owner_attested_free",
            "cost_estimate_usd": "0.00",
            "failure_type": type(exc).__name__,
            "final_verdict": "FAIL",
            "action_selection_history": planner.action_history,
        }
        (evidence_root / "execution.json").write_text(
            json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        raise

    trace = list(result.trace)
    failed = [step for step in trace if step.get("outcome") == "action_failed"]
    alternate_strategy = _executed_alternate_after_failure(trace)
    passed = (result.status == "PASS" and result.quality_gate == "PASS"
              and verified and planner.responses_received >= 2
              and bool(failed) and alternate_strategy)
    record = {
        "schema": "spray.g3.gemini_chromium.v1",
        "model_mode": "real_gemini_api",
        "model_id": planner.model_id,
        "actual_model_call": planner.responses_received > 0,
        "request_attempts": planner.requests,
        "responses_received": planner.responses_received,
        "billing_tier": "owner_attested_free",
        "cost_estimate_usd": "0.00",
        "cost_basis": "selected model Free Tier token pricing; no paid tools or grounding",
        "browser": "system_google_chrome_via_playwright",
        "browser_version": browser_version,
        "fixture": "synthetic_loopback_only",
        "target": TARGET,
        "final_status": result.status,
        "final_reason": result.reason,
        "quality_gate": result.quality_gate,
        "recon_and_priority_diff": result.final_qa,
        "verified_in_dom": verified,
        "retry_count": len(failed),
        "alternate_strategy_after_failure": alternate_strategy,
        "action_selection_history": planner.action_history,
        "execution_trace": trace,
        "usage": planner.usage,
        "screenshot": screenshot,
        "final_verdict": "PASS" if passed else "FAIL",
    }
    (evidence_root / "execution.json").write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    (evidence_root / "execution.log").write_text(
        "\n".join([
            "model_mode=real_gemini_api",
            f"model_id={planner.model_id}",
            "actual_model_call=" + str(planner.responses_received > 0).lower(),
            f"request_attempts={planner.requests}",
            f"responses_received={planner.responses_received}",
            f"retry_count={len(failed)}",
            f"alternate_strategy_after_failure={str(alternate_strategy).lower()}",
            f"cost_estimate_usd={record['cost_estimate_usd']}",
            f"final_status={result.status}",
            f"quality_gate={result.quality_gate}",
            f"verified_in_dom={str(verified).lower()}",
            f"final_verdict={record['final_verdict']}",
        ]) + "\n", encoding="utf-8")
    print(json.dumps({k: record[k] for k in (
        "model_mode", "model_id", "actual_model_call", "request_attempts",
        "responses_received",
        "retry_count", "alternate_strategy_after_failure", "cost_estimate_usd",
        "final_status", "quality_gate", "verified_in_dom", "final_verdict")}, sort_keys=True))
    print(f"evidence_dir={evidence_root}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
