# SPRAY: Official Submission Form (Drafts)

English drafts for every official field of the Meta Global AI Developer
Hackathon submission. Fields that are genuinely not-yet-known (team name, public
repo URL, demo video URL) use explicit placeholders. Every factual claim is
grounded in repo evidence (file / SHA / PR / recorded decision record). No
secrets and no owner-identifying personal data appear here (the public org
handle `ZestfulPulse` is allowed).

---

## Team name

`[TEAM NAME PLACEHOLDER]`

## Track

Indie.

## Problem statement

"Push the Frontier."

## Project name

SPRAY.

## Project description

SPRAY gives an agent a new capability: **judging its own decisions against its
own predictions, and changing its next decision because of the outcome.** Each
judge-and-decide cycle is captured as a structured, append-only Decision Record
(`spray_schema/decision_record.py`, PR #41, first commit `c94c231`, merged into
`main` via `17e9f26`). The agent self-generates at least three candidates,
scores them against explicit criteria, writes a **prediction to disk before it
acts** (`create_record`, `status = PREDICTED`, outcome fields null), performs
the action to produce a repo-local artifact, then records the real outcome
(`finalize_record`, `status = FINALIZED`), compares prediction vs. actual, and
extracts a lesson. The next decision must cite the prior decisions it read and
the latest finalized lesson **verbatim**; a validator (`validate_record` in the
same module) rejects a record that ignores available prior lessons, cites a
non-existent decision, or edits a finalized record. The end-to-end runner that
composes this with a Meta Muse Spark planner is `spray_schema/decision_cycle.py`
(`run_one_cycle`, on `main`, first component commit `4ea3082`), and the
measured-outcome executor is `spray_schema/action_executor.py` (on `main`, first
commit `362dbc3`). The R3-H4 work adds a re-rank capability (first commit
`871ecf0`, now merged to `main` via PRs #48/#49): each record can carry an
`announced_next` block (what the prior lesson said to do next) and a `rerank`
block recording a real SECOND scoring pass fed the prior MEASURED outcome, with
two gated validators (`rerank_missing_prior_outcome_citation` and
`divergence_not_reflected_in_choice`). The R3-H5 work then makes the re-scoring
**anchor-free** (first commit `801ea28` on branch `kiro/r3h-s5-anchorfree`): the
re-scoring prompt EXCLUDES the prior lesson's announced next step, while
`announced_next` and the divergence comparison stay computed server-side and
recorded, and each new record carries an additive `anchor_free` flag. SPRAY has
recorded **26 decision cycles** (D-0001 .. D-0022 on `main`, plus the four
anchor-free cycles D-0023 .. D-0026 on the branch) with this machinery; the live
model path is gated behind an owner-supplied key and a fail-closed credit
budget.

**Provenance note (important for honesty):** SPRAY is an older project that
**began on 2026-10-14**, before the hackathon. On `main` HEAD `17e9f26` there
are **278 commits dated before the 2026-10-26 boundary** (pre-hackathon). The
hackathon contribution is specifically the **decision-cycle capability**:
Decision Record infra (PR #41, merged, `c94c231`) plus the provider/runner work
now on `main` (`105fea9` / `528df23` / `4ea3082` / `362dbc3`), the R3-H4 re-rank
mechanism (`announced_next` + `rerank`, first commit `871ecf0`, merged to `main`
via #48/#49), the R3-H5 anchor-free re-scoring change (first commit `801ea28` on
branch `kiro/r3h-s5-anchorfree`), and the 26 recorded cycles (D-0001 .. D-0022
on `main` and D-0023 .. D-0026 on the branch, `decisions/LEDGER.md` first commit
`b783358`). The pre-existing code is **not** claimed as event work. See
[PROVENANCE.md](PROVENANCE.md) for the full before/during split.

## Public repository URL

`[PUBLIC REPO URL PLACEHOLDER]` (the repository is currently private; flipping
it to public is an owner action and out of scope for this submission draft, see
[PUBLIC_RELEASE_CHECKLIST.md](PUBLIC_RELEASE_CHECKLIST.md)).

## Demo video URL

`[DEMO VIDEO URL PLACEHOLDER]` (the 3-minute script is drafted in
[DEMO_SCRIPT_3MIN.md](DEMO_SCRIPT_3MIN.md); its primary demo anchor is the real
D-0017 -> D-0018 chain, and it also walks the real D-0013 -> D-0014 numeric
correction. Recording the video is an owner action).

## Describe your use of Meta products

SPRAY uses Meta's Muse Spark models as the planner behind the decision cycle.
The adapter `spray_schema/meta_planner.MetaPlanner` (on `main`, first commit
`105fea9`) conforms to a provider-agnostic `Planner` protocol
(`spray_schema/provider_planner.py`, same first commit), sends requests to the
OpenAI-compatible Meta Model API base `api.meta.ai`, and reads its API key
header-only from the `META_API_KEY` environment variable (never a code literal,
never logged). S6 redaction (`spray_schema/model_observation.py`, on `main`) is
applied to every outbound request body so no secret, token, or raw page content
reaches the model. The 26 recorded decision cycles ran with `provider = meta`
and `model = muse-spark-1.3` via `api.meta.ai` (the `model` field on every
`decisions/D-*.json`, e.g. `decisions/D-0014.json`, and in
`decisions/LEDGER.md`). The code constant `META_MODEL_ID` remains an overridable
sentinel (`muse-spark-PLACEHOLDER`, settable via the `META_MODEL_ID` env var)
retained for the live gate; the real id used by the cycles is recorded on the
records. A fresh live run is refused unless `META_API_KEY` is present and the
credit-budget check passes.

## Why are Meta's models important for this project

The frontier capability (an agent that predicts, acts, judges itself, and
changes its next decision) needs a planner that can reason over structured
observations and prior lessons to generate genuinely different candidate
actions. Meta's Muse Spark models serve as that planner through a clean,
provider-agnostic interface (`Planner` protocol) so the capability is not
locked to one backend, while S6 redaction and a fail-closed credit budget keep
the integration safe and bounded. The recorded cycles show the loop working:
D-0010 .. D-0012 are three consecutive measured HITs each citing the prior
lesson verbatim; D-0013 committed a `total_files` range of `[20, 30]`, measured
182 (a genuine numeric MISS), and D-0014 cited D-0013's lesson and corrected the
range to `[170, 195]` (measured 183, `within_range = true`); D-0016 .. D-0018
ran executed == chosen analyses (inventory -> markers -> import_graph); the
four R3-H4 cycles D-0019 .. D-0022 added a real re-rank second pass fed the
prior measured outcome (stats -> duplicates -> docs_coverage -> import_graph,
each executed == chosen); and the four R3-H5 **anchor-free** cycles
D-0023 .. D-0026 ran the same second pass with the announced next step REMOVED
from the prompt (inventory -> markers -> stats -> duplicates, each executed ==
chosen, `anchor_free = true`). The headline/primary demo example of the chain's
self-correction stays **D-0017 -> D-0018** (the falsified numeric prediction
`total_markers = 154` outside `[5, 50]` that corrected the next cycle's range).

Honest caveat: the *sequence* of analyses partly follows plans announced in
earlier lessons. On the re-rank cycles the second pass demonstrably re-ranked
candidates from the prior measured outcome, but **outcome-driven divergence
under the strict rule: none observed** - `rerank.diverged_from_announced =
false` for all of D-0019 .. D-0026, including after R3-H5 removed the
announcement anchor. The clearly outcome-driven change remains the numeric
correction (D-0013 -> D-0014, headlined as D-0017 -> D-0018) rather than the
choice of analysis itself.

The **anchor effect** is a SEPARATE finding and is explicitly **NOT**
outcome-driven divergence: with the announced next action hidden (`anchor_free =
true`), 3 of 4 cycles (D-0023, D-0025, D-0026) chose a different ANALYSIS than
the plan had announced, which indicates the earlier plan-consistency across
D-0016 .. D-0022 was partly **anchoring on the agent's own announced plan**
rather than purely independent reasoning. Because the anchor-free model never
saw the announcement, a different choice is not evidence the *outcome* drove it;
D-0025 (which followed the D-0024 numeric miss) may be described only as a
**possible** case of outcome influence, not as proof. This is verifiable from
the records alone (`anchor_free`, `announced_next.announced_action`,
`chosen_action`, `rerank.diverged_from_announced`,
`rerank.prior_outcome_decision_id`). Note (see [ERRATA](../../decisions/ERRATA.md)):
the stored `rerank.why` on D-0023/D-0025/D-0026 inaccurately says "stays
consistent with the announced next step" while the authoritative machine field
`rerank.diverged_from_announced = false` is correct; the append-only records are
left unchanged and the inaccurate prose is corrected in the errata.

## Budget / safety (supporting evidence)

Live Meta spend is bounded by `spray_schema/credit_budget.py` (on `main`, first
commit `528df23`): credit-only (`CASH_BUCKET_USD = 0`), per-call cap
`R2_PER_CALL_CAP_USD = 20` USD, lifetime hard stop `LIFETIME_HARD_STOP_USD =
140` USD, credit expiry `CREDIT_EXPIRY_ISO = 2026-11-05T02:00:00Z`, and a
fail-closed `authorize_meta_spend` that composes the on-branch
[`../../spray_schema/resource_ledger.py`](../../spray_schema/resource_ledger.py).
The per-cycle credit ledger `decisions/LEDGER.md` records cumulative USD 0.52
across the 26 cycles with cash USD 0.00. The offline Quality Gate
(`.github/workflows/ci.yml`,
[`../../ci/policy_checks.py`](../../ci/policy_checks.py), and
[`../../ci/validate_docs.py`](../../ci/validate_docs.py)) runs on every push:
854 unittest tests pass (22 skipped, 0 failures), no credential formats
detected, no broken doc links.

## Optional: feedback for Meta

Publishing a stable, machine-readable model-id list for the Muse Spark series on
a host reachable from restricted CI environments would make model-id
confirmation easier: the `dev.meta.ai` console/docs host was tunnel-blocked from
our offline sandbox, so the code constant is kept as an overridable sentinel
even though the reachable API base `api.meta.ai` served the `muse-spark-1.3`
family used by the recorded cycles.

## Optional: X (Twitter) post draft

> SPRAY now judges its own decisions: it writes a prediction to disk *before*
> it acts, records the real outcome, and must cite the lesson in its next
> decision, planned with Meta's Muse Spark models. Built offline-first and
> fail-closed. #MetaHackathon #PushTheFrontier

(Posting is an owner action; whether the post is published is out of scope for
this draft.)

---

## Evidence index for this form

| Claim | Evidence |
| --- | --- |
| Decision Record infra, two-phase store + validator | `spray_schema/decision_record.py`, PR #41, `c94c231`, merged `17e9f26` (on main) |
| S6 redaction on every outbound body | `spray_schema/model_observation.py` (on main) |
| Fail-closed budget primitives | `spray_schema/resource_ledger.py` (on main) |
| Offline CLI | `scripts/decision_cli.py` (on main) |
| Meta Muse Spark adapter + provider protocol | `spray_schema/meta_planner.py` / `spray_schema/provider_planner.py`, on main, first commit `105fea9` |
| Credit-only budget (cap 20 / stop 140 / expiry) | `spray_schema/credit_budget.py`, on main, first commit `528df23` |
| Measured-outcome executor (six analyses) | `spray_schema/action_executor.py`, on main, first commit `362dbc3` |
| Decision-cycle runner + live gate | `spray_schema/decision_cycle.py` / `scripts/run_decision_cycle.py`, on main, first component commit `4ea3082` |
| Re-rank mechanism (`announced_next` + `rerank` second pass + gated validators) | `spray_schema/decision_record.py` / `spray_schema/decision_cycle.py`, first commit `871ecf0` (merged to main via #48/#49); tests `tests/test_decision_record_rerank.py`, `tests/test_decision_cycle_rerank.py` |
| Anchor-free re-scoring (announced next step removed from prompt) + `anchor_free` field | `spray_schema/decision_cycle.py` / `spray_schema/self_direction_planner.py` / `spray_schema/decision_record.py`, branch `kiro/r3h-s5-anchorfree`, first commit `801ea28`; test `tests/test_decision_cycle_anchor_free.py` |
| 278 pre-boundary commits; SPRAY began 2026-10-14 | [PROVENANCE.md](PROVENANCE.md) |
| 26 recorded decision cycles (D-0001 .. D-0026) | `decisions/D-0001.json` .. `decisions/D-0022.json` (on main) + `decisions/D-0023.json` .. `decisions/D-0026.json` (branch `kiro/r3h-s5-anchorfree`), `decisions/LEDGER.md` (first commit `b783358`), cumulative USD 0.52 / cash 0.00 |
| Outcome-driven divergence under the strict rule: none observed | `rerank.diverged_from_announced = false` in `decisions/D-0019.json` .. `decisions/D-0026.json`; verifiable via `anchor_free`, `announced_next.announced_action`, `chosen_action`, `rerank.prior_outcome_decision_id` |
| Anchor effect (separate finding; 3/4 anchor-free cycles chose a different analysis than announced; NOT outcome-driven divergence; D-0025 only a possible case) | `anchor_free = true` + `chosen_action` != `announced_next.announced_action` on `decisions/D-0023.json`, `decisions/D-0025.json`, `decisions/D-0026.json` (D-0024 chose the announced `markers`), all with `rerank.diverged_from_announced = false` |
| Errata: inaccurate stored `rerank.why` prose on D-0023/D-0025/D-0026 (machine field `diverged_from_announced = false` is correct) | [`../../decisions/ERRATA.md`](../../decisions/ERRATA.md); generator fix in `spray_schema/decision_cycle.py` covered by `tests/test_decision_cycle_rerank.py` |
| Meta model id used by cycles | `muse-spark-1.3` recorded on every `decisions/D-*.json` + `decisions/LEDGER.md`; code constant `META_MODEL_ID` remains the overridable sentinel `muse-spark-PLACEHOLDER` |

See [README.md](README.md) and [ARCHITECTURE.md](ARCHITECTURE.md) for the
full capability description and diagram.
