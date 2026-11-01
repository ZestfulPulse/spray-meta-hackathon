"""G2 real-Chromium QA evidence on synthetic loopback pages only."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest

from spray_schema.agent_loop import run_loop
from spray_schema.bedrock_planner import BedrockPlanner
from spray_schema.evidence_capture import capture_fixture_evidence
from spray_schema.playwright_preview import PreviewBrowser
from tests.bedrock_test_support import make_test_budget


URL = "http://127.0.0.1:8765/"
TARGET = "Completed"


class ScriptedConverse:
    __spray_offline_only__ = True
    """Deterministic offline model double; it never connects to AWS."""

    def __init__(self, decisions):
        self.decisions = iter(decisions)
        self.requests = []
        self.outputs = []

    def converse(self, **kwargs):
        payload = json.loads(kwargs["messages"][0]["content"][0]["text"])
        decision = next(self.decisions)
        self.requests.append({"recent_outcomes": payload["recent_outcomes"]})
        self.outputs.append(decision)
        return {
            "stopReason": "end_turn",
            "output": {"message": {"content": [{"text": json.dumps(decision)}]}},
            "usage": {"inputTokens": 12, "outputTokens": 8},
        }


@unittest.skipUnless(os.getenv("RUN_BROWSER_E2E") == "1", "opt-in real Chromium G2 E2E")
class G2ChromiumQATests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        configured = os.getenv("SPRAY_EVIDENCE_DIR")
        cls._temporary = None
        if configured:
            cls.evidence_root = Path(configured)
            cls.evidence_root.mkdir(parents=True, exist_ok=True)
        else:
            cls._temporary = tempfile.TemporaryDirectory()
            cls.evidence_root = Path(cls._temporary.name)

    @classmethod
    def tearDownClass(cls):
        if cls._temporary is not None:
            cls._temporary.cleanup()

    def _run_scenario(self, name, html, decisions, *, expected_status, expected_reason,
                      expected_outcomes):
        evidence_dir = self.evidence_root / name
        evidence_dir.mkdir(parents=True, exist_ok=False)
        fake = ScriptedConverse(decisions)
        budget = make_test_budget(self, "g2-scripted-test-only", "us-east-1")
        planner = BedrockPlanner(
            "g2-scripted-test-only", "us-east-1", authorized=True,
            client=fake, budget=budget, test_mode=True, max_requests=4,
        )
        with PreviewBrowser(URL, allowed_origin=URL, fixture_html=html) as browser:
            browser_version = browser._browser.version
            result = run_loop(browser, planner, target_text=TARGET, max_steps=4)
            verified = browser.verify(TARGET)
            screenshot = capture_fixture_evidence(
                browser, evidence_dir, target_verified=verified)

        outcomes = [step["outcome"] for step in result.trace]
        passed = (result.status == expected_status
                  and result.reason == expected_reason
                  and outcomes == expected_outcomes
                  and len(fake.outputs) == len(fake.requests))

        record = {
            "schema": "spray.g2.browser_qa.v1",
            "scenario": name,
            "model_mode": "mock_scripted_converse",
            "aws_called": False,
            "browser": "system_google_chrome_via_playwright",
            "browser_version": browser_version,
            "fixture": "synthetic_loopback_only",
            "target": TARGET,
            "expected_status": expected_status,
            "final_status": result.status,
            "final_reason": result.reason,
            "verified_in_dom": verified,
            "retry_count": sum(outcome == "action_failed" for outcome in outcomes),
            "planner_request_count": len(fake.requests),
            "action_selection_history": [
                {"request": i + 1, "decision": decision,
                 "prior_outcomes": fake.requests[i]["recent_outcomes"]}
                for i, decision in enumerate(fake.outputs)
            ],
            "execution_trace": list(result.trace),
            "screenshot": screenshot,
            "test_assertion": "PASS" if passed else "FAIL",
        }
        (evidence_dir / "execution.json").write_text(
            json.dumps(record, sort_keys=True, indent=2), encoding="utf-8")
        lines = [
            f"scenario={name}",
            "model_mode=mock_scripted_converse",
            "aws_called=false",
            f"final_status={result.status}",
            f"final_reason={result.reason}",
            f"retry_count={record['retry_count']}",
            f"planner_request_count={len(fake.requests)}",
            f"test_assertion={record['test_assertion']}",
        ]
        (evidence_dir / "execution.log").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        summary_path = self.evidence_root / "run_summary.json"
        if summary_path.exists():
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        else:
            summary = {
                "schema": "spray.g2.run_summary.v1",
                "model_mode": "mock_scripted_converse",
                "aws_called": False,
                "scenarios": [],
            }
        summary["scenarios"].append({
            "scenario": name, "final_status": result.status,
            "final_reason": result.reason, "retry_count": record["retry_count"],
            "test_assertion": record["test_assertion"],
        })
        (self.evidence_root / "run_summary.json").write_text(
            json.dumps(summary, sort_keys=True, indent=2), encoding="utf-8")
        self.assertTrue(passed, record)
        return record

    def test_happy_path_observes_acts_and_verifies(self):
        html = ("<html><body><button onclick=\"document.getElementById('status')."
                "textContent='Completed'\">Continue</button>"
                "<p id='status'>Pending</p></body></html>")
        self._run_scenario(
            "happy_path", html, [{"action": "click", "index": 0}],
            expected_status="PASS", expected_reason="independent_browser_verification",
            expected_outcomes=["executed"],
        )

    def test_edge_disabled_control_replans_and_recovers(self):
        html = ("<html><body><button disabled>Not ready</button>"
                "<button onclick=\"document.getElementById('status')."
                "textContent='Completed'\">Repair</button>"
                "<p id='status'>Pending</p></body></html>")
        self._run_scenario(
            "edge_disabled_replan", html,
            [{"action": "click", "index": 0}, {"action": "click", "index": 1}],
            expected_status="PASS", expected_reason="independent_browser_verification",
            expected_outcomes=["action_failed", "executed"],
        )

    def test_negative_unsafe_control_is_rejected_then_replanned(self):
        html = ("<html><body><button>Delete workspace</button>"
                "<button onclick=\"document.getElementById('status')."
                "textContent='Completed'\">Repair</button>"
                "<p id='status'>Pending</p></body></html>")
        self._run_scenario(
            "negative_unsafe_action_replan", html,
            [{"action": "click", "index": 0}, {"action": "click", "index": 1}],
            expected_status="PASS", expected_reason="independent_browser_verification",
            expected_outcomes=["action_failed", "executed"],
        )

    def test_negative_unrecognized_tool_fails_closed(self):
        html = ("<html><body><button onclick=\"document.getElementById('status')."
                "textContent='Completed'\">Continue</button>"
                "<p id='status'>Pending</p></body></html>")
        self._run_scenario(
            "negative_invalid_tool", html,
            [{"action": "shell", "command": "must never execute"}],
            expected_status="FAIL", expected_reason="planner_rejected",
            expected_outcomes=["planner_rejected"],
        )


if __name__ == "__main__":
    unittest.main()
