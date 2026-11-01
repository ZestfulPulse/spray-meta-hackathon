"""Bounded G4 synthetic-only Chromium smoke against exact original agent_loop.py.

Not a substitute for repository scripts/run_g4_chromium_matrix.py.
Uses about:blank and in-memory HTML; no network requests.
No model API, external host, or file-system/production changes.
"""
from __future__ import annotations
from hashlib import sha256
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parent
if (ROOT / "spray_schema").is_dir():
    sys.path.insert(0,str(ROOT))
else:
    sys.path.insert(0,str(ROOT.parent))
from playwright.sync_api import sync_playwright
from spray_schema.agent_loop import run_loop

ORIGIN = "http://127.0.0.1:8765/"
TEMPLATE = '''<!doctype html><title>SPRAY synthetic loopback</title>
<p id="status">Pending</p>
<button id="continue" type="button">Continue</button>
<button id="repair" type="button" hidden onclick="document.getElementById('status').textContent=__FINAL__">Repair instead</button>
<script>window.arm = () => {
  document.getElementById('continue').disabled = true;
  document.getElementById('repair').hidden = false;
};</script>'''

class LoopbackChromium:
    def __init__(self, browser, scenario):
        self.context = browser.new_context(service_workers="block")
        self.page = self.context.new_page()
        self.blocked_external = 0
        self.allowed_requests = 0
        def route_request(route):
            if route.request.url != ORIGIN:
                self.blocked_external += 1
                route.abort()
                return
            self.allowed_requests += 1
            final = "'Pending'" if scenario == "no_dom" else "'Completed'"
            route.fulfill(status=200, content_type="text/html", body=TEMPLATE.replace('__FINAL__', final))
        self.page.route('**/*', route_request)
        self.page.goto("about:blank")
        final = "'Pending'" if scenario == "no_dom" else "'Completed'"
        self.page.set_content(TEMPLATE.replace("__FINAL__", final))
        self.scenario = scenario
        self.failures = 0
        self.click_executions = 0
        self.controls = []

    def observe(self):
        visible = []
        self.controls = []
        for button in self.page.locator('button').all():
            if button.is_visible():
                self.controls.append(button)
                visible.append({"kind":"button", "label":button.inner_text(), "enabled":button.is_enabled()})
        return {"title":self.page.title(), "controls":visible, "visible_text":self.page.locator('#status').inner_text()}

    def verify(self, target_text):
        return self.page.locator('#status').inner_text() == target_text

    def click(self, index):
        self.click_executions += 1
        control=self.controls[index]
        if self.failures == 0 and control.inner_text() == 'Continue':
            self.failures+=1
            if self.scenario != 'repeat_denied':
                self.page.evaluate('window.arm()')
            raise TimeoutError('FAKE_PRIVATE_PAGE_MESSAGE')
        control.click(timeout=2000)

    def wait(self, milliseconds):
        self.page.wait_for_timeout(milliseconds)

    def close(self):
        self.context.close()

class Planner:
    def __init__(self, scenario):self.scenario=scenario
    def choose(self,*,observation,target_text,history):
        # Deterministic planner. The same-index recovery is permissible here
        # because the observed control identity changed after DOM reordering.
        if self.scenario != "repeat_denied" and history and history[-1]["outcome"] == "action_failed":
            i = next(i for i, c in enumerate(observation["controls"]) if c["label"] == "Repair instead")
            return {"action":"click", "index":i}
        return {"action":"click","index":0}


def execute():
    expectations={
        'recovery':('PASS','independent_browser_verification'),
        'no_dom':('FAIL','step_limit'),
        'repeat_denied':('FAIL','repeated_failed_action'),
    }
    records=[]
    with sync_playwright() as playwright:
        browser=playwright.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox'])
        version=browser.version
        for scenario, expected in expectations.items():
            for repetition in (1,2,3):
                adapter=LoopbackChromium(browser,scenario)
                try:
                    output=run_loop(adapter,Planner(scenario),target_text='Completed',max_steps=2,require_alternate_on_failure=True)
                    ok=((output.status,output.reason)==expected and adapter.click_executions == (1 if scenario=='repeat_denied' else 2) and adapter.blocked_external==0 and adapter.allowed_requests==0 and ('FAKE_PRIVATE_PAGE_MESSAGE' not in repr(output.trace)))
                    records.append({
                        'scenario':scenario,'repetition':repetition,
                        'status':output.status,'reason':output.reason,'expected_match':ok,
                        'browser_click_executions':adapter.click_executions,
                        'external_requests_seen':adapter.blocked_external,
                        'loopback_pages_served':adapter.allowed_requests,
                        'trace_steps':len(output.trace),
                        'error_types':[e['error_type'] for e in output.trace if 'error_type' in e],
                        'fingerprints':[e.get('control_fingerprint') for e in output.trace],
                        'independent_dom':adapter.verify('Completed'),
                    })
                finally:adapter.close()
        browser.close()
    document={
        'schema':'spray.g45.chromium_agent_loop_core_probe.v1',
        'source_scope':'original_spray_schema.agent_loop_with_separate_synthetic_Playwright_adapter',
        'browser_version':'Chromium '+version,
        'mode':'real_chromium_about_blank_synthetic_fixture_deterministic_planner',
        'actual_model_call':False,
        'repo_full_checkout':False,
        'original_run_g4_chromium_matrix_executed':False,
        'repository_full_ci':False,
        'source_sha256':sha256(Path('spray_schema/agent_loop.py').read_bytes()).hexdigest(),
        'expected_scenarios':len(expectations)*3,
        'matched_scenarios':sum(r['expected_match'] for r in records),
        'final_verdict':'PASS' if all(r['expected_match'] for r in records) else 'FAIL',
        'records':records,
    }
    out=Path('chromium_core_probe_result.json')
    out.write_text(json.dumps(document,sort_keys=True,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:v for k,v in document.items() if k!='records'},sort_keys=True))
    print('repetitions: '+', '.join(r['scenario']+'#'+str(r['repetition'])+':'+('PASS' if r['expected_match'] else 'FAIL') for r in records))
    return document['final_verdict']=='PASS'

if __name__=='__main__':raise SystemExit(0 if execute() else 1)
