# SPRAY

> **Meta Global AI Developer Hackathon entry · Indie Developer Track · Push the Frontier.**
> This repository is a history-free public snapshot of the private SPRAY repository (main @ c8fff08).
> Start here: [hackathon README](docs/hackathon/README.md) · [26 decision records](decisions/DECISIONS.md) · [credit ledger](decisions/LEDGER.md) · [errata](decisions/ERRATA.md) · [before/during provenance](docs/hackathon/PROVENANCE.md)

SPRAY gives an AI agent a capability most agents lack: judging its own decisions against predictions it committed before acting. In each cycle, Meta's Muse Spark 1.3 reads SPRAY's previous decision records, proposes at least three candidate actions and scores them. The agent then commits a numeric prediction to Git before executing. A deterministic executor measures the real result, and the agent records prediction vs. actual plus a lesson that the next cycle must cite. Because predictions are committed first, results cannot be retrofitted.

Across 26 live cycles (USD 0.52 of credits, zero cash), the records show real self-correction: D-0013 predicted 20-30 files and measured 182; D-0014 cited that miss, corrected its range to 170-195, and measured 183. A controlled experiment also revealed an anchoring bias: when the agent's own announced plan was hidden, it chose a different action in 3 of 4 cycles.

We report limits openly. Outcome-driven re-ranking under our strict rule has not yet been observed, and errata are published in [decisions/ERRATA.md](decisions/ERRATA.md). SPRAY began before the hackathon; [PROVENANCE.md](docs/hackathon/PROVENANCE.md) separates prior work from the decision-cycle capability built during the event.

---

## Long-term project status

The status below describes SPRAY's long-term AWS runtime plan. "NOT RUNNING" refers to that runtime; the 26 decision cycles above ran live on Meta's Muse Spark 1.3.

**Born: 2026-10-14 · Status: P0 Bootstrap · NOT RUNNING**

SPRAY is a proposed AWS-native autonomous economic agent. Its goal is to discover opportunities, build genuine value, and pursue sustainable, lawful net profit without depending on a personal computer.

**Infrastructure:** GitHub (source and evidence), AWS (runtime and memory), Kiro (development through supported official integration). TOPIA supervises independently; `ops.zestfulpulse.com` will display verified status.

**Hard boundaries:** no unauthorized spending, account acquisition, impersonation, fabricated evidence/revenue, spam, manipulation, credential exposure, audit modification, or bypass of pause/termination. The initial support period is *not* a fixed life span: only verified and authorized resources support continued operation. If funds and credits run out without an authorized funded continuation, the agent stops safely.

## Status
- Repo: created
- Charter/spec/architecture: work in progress
- Kiro issue-to-PR: NOT VERIFIED
- AWS deployed: NO
- Self-directed behavior/economic activity: NOT STARTED
- TOPIA/Ops integration: NOT STARTED
- CI: offline quality gate (policy + schema tests) added, see [ci/README.md](ci/README.md)
- Resource ledger & externally enforced budget contract (design): see [docs/RESOURCE_LEDGER.md](docs/RESOURCE_LEDGER.md)
- Decision records & per-cycle credit ledger: see [decisions/DECISIONS.md](decisions/DECISIONS.md) and [decisions/LEDGER.md](decisions/LEDGER.md)
- Hackathon provenance (built before vs during the event): see [docs/hackathon/PROVENANCE.md](docs/hackathon/PROVENANCE.md)
- Pre-public-release checklist & full-history secret-scan result: see [docs/hackathon/PUBLIC_RELEASE_CHECKLIST.md](docs/hackathon/PUBLIC_RELEASE_CHECKLIST.md)
- Hackathon judge package (problem, architecture, submission-form drafts, 3-min demo): see [docs/hackathon/README.md](docs/hackathon/README.md), [docs/hackathon/ARCHITECTURE.md](docs/hackathon/ARCHITECTURE.md), [docs/hackathon/SUBMISSION_FORM.md](docs/hackathon/SUBMISSION_FORM.md), [docs/hackathon/DEMO_SCRIPT_3MIN.md](docs/hackathon/DEMO_SCRIPT_3MIN.md)

No secrets in this repo. No automated infrastructure deployment until budget, IAM and independent stop controls are proven. Development evidence belongs in GitHub issues and PRs.
