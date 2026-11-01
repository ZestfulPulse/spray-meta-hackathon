# G3 Gemini local proof

The optional G3 runner sends only an allowlisted, redacted observation from a
synthetic loopback page to the Gemini API. The model can return only a bounded
`click`, `wait`, or `finish` decision; Chromium verifies the target text.

## Billing precondition

Use the runner only after confirming the API key's project is on the **Free
Tier** and has remaining model quota in Google AI Studio. The model-list API
does not report the project's billing tier. Paid-tier projects must not run
this proof. The runner has no paid fallback and requires an explicit
`--free-tier-confirmed` owner attestation. It uses the existing
`GEMINI_API_KEY` environment variable and does not create or change credentials.

The implementation selects `gemini-3.5-flash-lite`, caps the run at four
requests, does not enable paid tools or grounding, and records usage counts and
the estimated cost. A free-tier attestation is not a cloud-side billing cap.

## Run

From the repository root, after the Free Tier precondition is confirmed:

```sh
.venv/bin/python scripts/run_g3_gemini_chromium.py --free-tier-confirmed
```

The runner uses installed system Google Chrome and the existing Playwright
dependency. It accesses only `127.0.0.1`, writes a text-masked screenshot,
sanitized model decision history, execution trace, retries, usage, and final
verdict under `~/data/SPRAY/output/g3/`. It refuses to overwrite an evidence
directory or write evidence outside `~/data/SPRAY`.

The synthetic fixture creates a deterministic stale-control race: after the
initial observation, immediately before the first model-selected click, the
observed control becomes disabled and a safe alternative appears. This is an
intentional local test fault, not evidence of arbitrary real-site recovery.
The model must observe the new state and independently select a different
allowed action. No model decision is scripted by the live runner.

The live path now uses the existing `run_qa_flow` integration, not a second
browser engine. Recon records screen, controls, UI states and requirement
coverage. The priority gate independently checks must/should/could cases;
missing optional cases do not create a false must failure. Sanitized recon
metadata is the only QA inventory passed to Gemini. Execution evidence stores
the final priority diff and quality-gate verdict.

A passing live proof requires at least two real model responses, a failed
Chromium action, a different subsequent strategy, independent DOM verification
of `Completed`, and a PASS must-requirement gate. Offline fake-HTTP tests
verify this wiring but never count as real Gemini inference.

Offline integration tests: `python3 -m unittest tests.test_g3_gemini_qa_integration -v`.
Opt-in real Chromium with fake HTTP transport: `RUN_BROWSER_E2E=1
SPRAY_CHROMIUM_PATH=/path/to/chromium python3 -m unittest
tests.test_g3_gemini_qa_integration -v`. These tests do not call Google.
