# SPRAY Provenance — What Was Built Before vs During the Hackathon

**Event:** Meta Global AI Developer Hackathon — Indie track — theme "Push the Frontier."
**Hackathon boundary:** `2026-10-26T00:00:00` (anything first committed on or after this instant counts as "during").
**SPRAY start:** `2026-10-14` (first commit / birth record).

This document exists to keep the hackathon submission honest. SPRAY is an
older project than the hackathon, so most of its code predates the event. The
"frontier" contribution of the event is the **decision-cycle** capability: an
agent that records a prediction before acting, then judges its own decision
against that prediction and lets the outcome change its next decision.

## How components are classified

Every component below is classified **strictly by its FIRST COMMIT DATE**
(`git log --reverse` for the relevant path), **not** by the date its pull
request was merged. Several pre-hackathon branches were merged into `main`
during the event; merge date is not authorship date, so those stay classified
as PRE with an explicit note that the merge spans the boundary.

Overall commit-date split on `main` (HEAD `17e9f26`):

| Metric | Count | Command |
| --- | --- | --- |
| Commits dated **before** 2026-10-26 | **278** | `git log --before=2026-10-26T00:00:00 --oneline \| wc -l` |
| Commits dated **on/after** 2026-10-26 | **97** | `git log --since=2026-10-26T00:00:00 --oneline \| wc -l` |

Every row cites a verifiable first-commit SHA and/or PR number. SHAs and PR
numbers are plain text (not links); file paths are real repo paths.

## BEFORE 2026-10-26 (pre-hackathon)

| Component | First-commit date | First-commit SHA | PR | Status |
| --- | --- | --- | --- | --- |
| First commit / birth record (`README.md`) | 2026-10-14 | `b7fbdcf` | — | On main |
| Offline CI building blocks (`ci/` policy + validate + schema tests) | 2026-10-14 | `8bc7fa7` | #6 | On main |
| CI workflow + README (`.github/workflows/ci.yml`) | 2026-10-14 | `9451253` | — | On main |
| Resource-ledger design docs (`docs/RESOURCE_LEDGER.md`) | 2026-10-14 | `d4900d6` | #8 | On main |
| Bedrock planner + agent loop (`spray_schema/bedrock_planner.py`, `spray_schema/agent_loop.py`) | 2026-10-23 | `61eb29c` | — | On main |
| Bedrock persistent budget (`spray_schema/bedrock_budget.py`) | 2026-10-23 | `23a8eac` | — | On main |
| Evidence-capture redaction (`spray_schema/evidence_capture.py`) | 2026-10-23 | `825d1c3` | — | On main |
| G4/G5 Chromium evidence-gate workflow (`.github/workflows/g45-chromium-smoke.yml`) | 2026-10-24 | `a3429259` | — | On main |

**Merge-date-spans-boundary note (still classified PRE):** PR #35 (merge
`642023d`, 2026-10-27) and PR #37 (merge `3e4a310`, 2026-10-27) were **merged
during** the hackathon, but their underlying R1 G4/G5 commits are dated
`2026-10-24` (pre-boundary, on branches `test/g4-negative-dom-gate-20261024`
and `feat/g45-unattended-batch-20261024`). By first-commit date they are
**PRE-hackathon**; only the merge event lands after the boundary. They are not
claimed as hackathon work.

## DURING the hackathon (on/after 2026-10-26)

| Component | First-commit date | First-commit SHA | PR | Status |
| --- | --- | --- | --- | --- |
| Decision Record infra (`decisions/`, `spray_schema/decision_record.py`, `scripts/decision_cli.py`) | 2026-10-27 08:59 | `c94c231` | #41 | **MERGED** via `17e9f26` |
| G5 regression fix (observed-enabled stale-control race treated as recoverable) | 2026-10-27 08:20 | `a934aa8` | #40 | **MERGED** via `8d82ffe` |
| Provider-agnostic planner + Meta adapter (`spray_schema/provider_planner.py`, `spray_schema/meta_planner.py`) | 2026-10-27 09:55 | `105fea9` | #42 | **MERGED** on main |
| Credit-only Meta spend / credit budget (`spray_schema/credit_budget.py`) | 2026-10-27 09:59 | `528df23` | #42 | **MERGED** on main |
| Decision-cycle runner (`spray_schema/decision_cycle.py`, `scripts/run_decision_cycle.py`) | 2026-10-27 12:04 | `4ea3082` | #43 | **MERGED** on main |
| Measured-outcome executor (`spray_schema/action_executor.py`) | 2026-10-28 | `362dbc3` | — | **MERGED** on main |
| Per-cycle credit ledger (`decisions/LEDGER.md`) | 2026-10-28 | `b783358` | — | **MERGED** on main |
| Re-rank mechanism (`announced_next` + `rerank` fields + gated validators in `spray_schema/decision_record.py`, `spray_schema/decision_cycle.py`) | 2026-10-28 | `871ecf0` | #48/#49 | **MERGED** on main |
| Anchor-free re-scoring prompt + additive `anchor_free` record field (`spray_schema/decision_cycle.py`, `spray_schema/self_direction_planner.py`, `spray_schema/decision_record.py`) | 2026-10-29 | `801ea28` | — | **ON BRANCH** `kiro/r3h-s5-anchorfree` |
| Decision-record errata (`decisions/ERRATA.md`) + generator fix for accurate `rerank.why` (`spray_schema/decision_cycle.py`) | 2026-10-29 | — | — | **ON BRANCH** `kiro/r3h-s6-finalize` |
| Real decision cycles (`decisions/D-0001.json` .. `decisions/D-0022.json` on main; `decisions/D-0023.json` .. `decisions/D-0026.json` on branch) | 2026-10-28/29 | sample `917c490` (D-0013) / `993c4aa` (D-0023) | — | **RECORDED** (26 cycles) |

### Merged / recorded details

- **PR #42** (provider-agnostic planner + Meta adapter + credit budget) is now
  **merged on `main`**. First-component commits `105fea9` and `528df23` are both
  dated 2026-10-27.
- **PR #43** (decision-cycle runner) is now **merged on `main`**. Its first
  component commit (earliest touching `spray_schema/decision_cycle.py`) is
  `4ea3082` (2026-10-27 12:04).
- **Re-rank mechanism (R3-H4).** The `announced_next` + `rerank` second-pass
  record fields and their two gated validators
  (`rerank_missing_prior_outcome_citation`,
  `divergence_not_reflected_in_choice`) are new during-hackathon work; first
  commit `871ecf0` (2026-10-28), now **merged to `main`** via PRs #48/#49. On
  the four live re-rank cycles the second pass demonstrably re-ranked candidates
  from the prior measured outcome, but `rerank.diverged_from_announced = false`
  for all of D-0019 .. D-0022 (no divergence was engineered). This is
  unit-tested in `tests/test_decision_record_rerank.py` and
  `tests/test_decision_cycle_rerank.py`.
- **Anchor-free re-scoring (R3-H5).** The next step removes the announcement
  anchor from the re-scoring prompt: when the second pass is active, each prior
  lesson's "...next proceed to X" clause is stripped before it enters the
  planner prompt, while `announced_next` and the divergence comparison stay
  server-side. Each new record carries an additive `anchor_free` boolean. First
  commit `801ea28` (2026-10-29) on branch `kiro/r3h-s5-anchorfree`; unit-tested
  in `tests/test_decision_cycle_anchor_free.py`. The four live anchor-free
  cycles D-0023 .. D-0026 still recorded `rerank.diverged_from_announced =
  false` for all four: **outcome-driven divergence under the strict rule: none
  observed**. No divergence was engineered and no miss was forced. A SEPARATE
  finding (explicitly NOT outcome-driven divergence) is the **anchor effect**:
  with the announcement hidden from the prompt, 3 of 4 cycles (D-0023, D-0025,
  D-0026) chose a different analysis than the plan had announced, which
  indicates the earlier D-0016 .. D-0022 plan-consistency was partly anchoring
  on the agent's own announced plan rather than purely independent reasoning;
  because the anchor-free model never saw the announcement, a different choice
  is not evidence the outcome drove it, and D-0025 is only a possible case, not
  proof. A note of record: the stored `rerank.why` on D-0023/D-0025/D-0026
  inaccurately says it "stays consistent with the announced next step" while the
  machine field `diverged_from_announced = false` is correct; the append-only
  records are left unchanged and the prose is corrected in
  [ERRATA](../../decisions/ERRATA.md).
- **Real decision cycles: 26 recorded.** `decisions/D-0001.json` ..
  `decisions/D-0022.json` exist on `main` (D-0019 .. D-0022 landed on `main`
  when PRs #48/#49 merged) and `decisions/D-0023.json` .. `decisions/D-0026.json`
  exist on branch `kiro/r3h-s5-anchorfree`; all are summarized per cycle in
  `decisions/LEDGER.md` (first commit `b783358`): `provider = meta`,
  `model = muse-spark-1.3`, est. USD 0.02 each, cumulative USD 0.52, cash USD
  0.00. Sample first-commit SHAs: `decisions/D-0013.json` = `917c490`,
  `decisions/D-0023.json` = `993c4aa`. The cycles were produced via the Meta
  adapter against the reachable OpenAI-compatible API base `api.meta.ai`; as a
  historical note, the `dev.meta.ai` console/docs host was tunnel-blocked from
  this sandbox and is not the inference host. The live runner CLI remains
  owner-gated (it prints a `LIVE-GATED` line and exits `0` when `META_API_KEY`
  is absent).

## Summary

- Most of SPRAY (278 pre-boundary commits) predates the hackathon and is **not**
  claimed as event work.
- The hackathon contribution is the decision-cycle capability: Decision Record
  infra (#41, merged) and the provider/runner/executor work now on `main`
  (#42, #43, plus `action_executor.py`), the R3-H4 re-rank capability
  (`announced_next` + `rerank` second-pass fields and their gated validators,
  first commit `871ecf0`, merged to `main` via #48/#49), and the R3-H5
  anchor-free re-scoring change (first commit `801ea28` on branch
  `kiro/r3h-s5-anchorfree`).
- **26 decision cycles are recorded** (`decisions/D-0001.json` ..
  `decisions/D-0022.json` on `main`; `decisions/D-0023.json` ..
  `decisions/D-0026.json` on branch `kiro/r3h-s5-anchorfree`), tracked in
  `decisions/LEDGER.md` (cumulative USD 0.52, cash USD 0.00). See
  `decisions/DECISIONS.md` and
  `spray_schema/decision_record.py` for the record schema and the SUPERSEDED
  early cycles kept for audit.
- CI gate definition: `.github/workflows/ci.yml` (offline Quality Gate running
  the unittest suite plus `ci/policy_checks.py` and `ci/validate_docs.py`).

For the pre-public-release secret/identity scan results, see
[PUBLIC_RELEASE_CHECKLIST.md](PUBLIC_RELEASE_CHECKLIST.md). For the judge-facing
package (problem statement, architecture diagram, submission-form drafts, and
the 3-minute demo script) built on this before/during split, see
[README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md),
[SUBMISSION_FORM.md](SUBMISSION_FORM.md), and
[DEMO_SCRIPT_3MIN.md](DEMO_SCRIPT_3MIN.md).
