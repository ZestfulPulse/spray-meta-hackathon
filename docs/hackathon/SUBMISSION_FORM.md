# SPRAY: Submission Form (as submitted)

This file mirrors the text submitted to the Meta Global AI Developer Hackathon. Every claim is grounded in files in this repository.

## Team name
Spray

## Track
Indie Developer Track

## Problem statement
Push the Frontier

## Public GitHub repository
https://github.com/ZestfulPulse/spray-meta-hackathon

## 3-minute demo video
https://youtu.be/FLySlihAbhw

## Project description
SPRAY gives an AI agent a capability most agents lack: judging its own decisions against predictions it committed before acting. In each cycle, Meta's Muse Spark 1.3 reads SPRAY's previous decision records, proposes at least three candidate actions and scores them. The agent then commits a numeric prediction to Git before executing. A deterministic executor measures the real result, and the agent records prediction vs. actual plus a lesson that the next cycle must cite. Because predictions are committed first, results cannot be retrofitted.

Across 26 live cycles (USD 0.52 of credits, zero cash), the records show real self-correction: D-0013 predicted 20-30 files and measured 182; D-0014 cited that miss, corrected its range to 170-195, and measured 183. A controlled experiment also revealed an anchoring bias: when the agent's own announced plan was hidden, it chose a different action in 3 of 4 cycles.

We report limits openly. Outcome-driven re-ranking under our strict rule has not yet been observed, and errata are published in decisions/ERRATA.md. SPRAY began before the hackathon; docs/hackathon/PROVENANCE.md separates prior work from the decision-cycle capability built during the event.

## Describe your use of Meta products to build the submitted project
Meta Model API with muse-spark-1.3 is SPRAY's planner. A MetaPlanner adapter (spray_schema/meta_planner.py) implements a provider-agnostic Planner interface and calls the OpenAI-compatible endpoint at api.meta.ai, reading the API key only from the environment. Every outbound request passes through SPRAY's redaction layer, and a fail-closed credit budget (credits only, lifetime stop USD 140, cash 0) must authorize each call. All 26 decision cycles record provider = meta and model = muse-spark-1.3 in decisions/D-*.json and decisions/LEDGER.md, at about USD 0.02 per cycle.

## Why are Meta's models important to your solution?
The capability depends on a planner that can read structured prior records, generate different candidate actions, and commit to a falsifiable numeric prediction. Muse Spark 1.3 generated the candidates, scores and predictions for all 26 recorded cycles at about two cents per cycle, with a 1M-token context. That cost made dozens of fully logged cycles affordable within hackathon credits, so we could run controlled experiments such as the anchor-free comparison instead of a single demo run. The provider-agnostic interface keeps SPRAY portable, but every recorded result was produced with Muse Spark.

## Feedback on Meta products
The playground's "Get code" view and the Billing credit display were clear and helpful. Two suggestions: publish a machine-readable model-ID list on a host reachable from restricted CI sandboxes (our sandbox could reach api.meta.ai but not dev.meta.ai), and offer a hard spend cap in addition to the spend-limit notification, so autonomous agents can never fall through to card billing.

## Feedback on partner technologies
We did not use Fireworks, Modal or Baseten in this submission.

## Evidence index
| Claim | Where to verify |
| --- | --- |
| 26 live decision cycles on muse-spark-1.3 | decisions/D-0001.json .. decisions/D-0026.json, decisions/LEDGER.md |
| Prediction committed before the result | predicted_at and outcome_at in each decision record |
| D-0013 -> D-0014 numeric self-correction | decisions/D-0013.json, decisions/D-0014.json |
| Anchor effect (3 of 4 anchor-free cycles chose differently) | decisions/D-0023.json .. decisions/D-0026.json |
| Strict outcome-driven re-ranking: not yet observed | rerank.diverged_from_announced = false in D-0019 .. D-0026 |
| Errata | decisions/ERRATA.md |
| Credit-only budget (cash 0, lifetime stop USD 140) | spray_schema/credit_budget.py |
| Before/during hackathon split | docs/hackathon/PROVENANCE.md |
| Tests pass on GitHub Actions (856 tests) | Actions tab, workflow "CI Quality Gate" |
