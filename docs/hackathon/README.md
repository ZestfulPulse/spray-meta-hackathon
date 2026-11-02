# SPRAY: Meta Global AI Developer Hackathon (Judge README)

> **Snapshot note:** This repository is a history-free snapshot. All 26 decision records (D-0001 .. D-0026) are included here; mentions of branch `kiro/r3h-s5-anchorfree` refer to where D-0023 .. D-0026 were first developed. The test suite at snapshot time is 856 tests (GitHub Actions: "CI Quality Gate"). Commit SHAs and PR numbers below refer to the private development repository.

**Track:** Indie · **Theme:** "Push the Frontier" · **Org handle:** ZestfulPulse

This is the judge-facing entry point for SPRAY's hackathon submission. Every
factual claim below cites a real file, commit SHA, pull-request number, and/or
recorded decision record. The decision-cycle capability has now produced **26
recorded decision cycles**: `decisions/D-0001.json` .. `decisions/D-0022.json`
on `main` (the R3-H4 re-rank cycles D-0019 .. D-0022 landed on `main` when PRs
#48/#49 merged), plus the four R3-H5 **anchor-free** re-scoring cycles
`decisions/D-0023.json` .. `decisions/D-0026.json` on the branch of this
submission, `kiro/r3h-s5-anchorfree`. The real evidence and its honesty caveats
are summarized in the "Evidence status" section.

## The problem (what frontier are we pushing?)

The hackathon theme is "Push the Frontier": give an agent a genuinely **new
capability**. SPRAY's frontier contribution is self-judgment:

> An agent that writes down a **prediction before it acts**, then **judges its
> own decision against that prediction** after the fact, and lets the
> **outcome change its next decision**, using Meta's Muse Spark models as the
> planner.

Most agents act and forget. SPRAY records a structured Decision Record for each
judge-and-decide cycle, finalizes it with the real outcome, extracts a
**lesson**, and the next decision must cite the prior decisions it read and the
latest finalized lesson **verbatim**. A validator enforces that decisions
actually build on one another instead of restarting from scratch.

## How SPRAY judges its own decisions (the pipeline)

The pipeline is grounded in the on-branch module
[`../../spray_schema/decision_record.py`](../../spray_schema/decision_record.py)
(PR #41, first commit `c94c231`, merged into `main` via `17e9f26`). It is a
two-phase, append-only, offline store:

1. **Self-generated candidates.** The planner proposes opportunity candidates.
   The validator (`validate_record`) requires **at least 3** candidates, each
   with a non-empty name or description.
2. **Explicit scoring.** The record carries non-empty `scoring_criteria` and
   `candidate_scores` lists; a record missing either is rejected.
3. **Prediction committed BEFORE acting.** `create_record` writes the record
   with `status = PREDICTED` and the `actual_outcome`,
   `prediction_vs_actual`, and `lesson` fields all `null`. The prediction is
   persisted to disk (atomic temp-file + `os.replace`) **before** any action
   runs, so it cannot be back-fitted to the result.
4. **Action produces a repo-local artifact.** The action is run and produces a
   local artifact under `decisions/artifacts/D-NNNN/` (for example
   `decisions/artifacts/D-0013/inventory.json`).
5. **Actual outcome recorded.** `finalize_record` fills **only**
   `actual_outcome`, `prediction_vs_actual`, and `lesson`, and flips
   `status` to `FINALIZED`. It refuses to alter any other field and refuses
   to finalize a record twice. A `FINALIZED` record may never be edited again.
6. **`prediction_vs_actual` plus `lesson`.** The comparison and the lesson
   learned are stored on the now-finalized record.
7. **Next decision cites the past.** The next record lists the prior decision
   ids it read (`prior_decision_ids_read`) and cites the latest finalized
   lesson **verbatim** in `lessons_applied`.

Each decision is summarized as one Korean line in
[`../../decisions/DECISIONS.md`](../../decisions/DECISIONS.md) at create time.
That ledger now holds **D-0001 .. D-0026** (one line each) together with three
**SUPERSEDED** sections (R2-S6, an R2-S6 review follow-up, and R2-S7) that keep
superseded early cycles for audit rather than deleting them.

### Two orthogonal honesty signals (do not conflate)

A finalized record carries up to two independent verdicts:

- **`outcome_verdict` (HIT/MISS).** A HIT means the **executed** analysis
  equals the **chosen/intended** analysis **and** `actual_outcome.fallback =
  false` (an R2-S7 notion). If the executor silently fell back to a different
  analysis, the verdict is an honest MISS even if a number happened to land.
- **Numeric `within_range` (R2-S8).** A separate signal on the record's
  `quantitative_claim`: did the measured metric land inside the predicted
  range?

These are orthogonal. A record can be `outcome_verdict = MISS` while numerically
`within_range = true` (see D-0014), and `outcome_verdict = HIT` while
numerically `within_range = false` (see D-0017 and D-0018).

### Re-ranking on a prior measured outcome (R3-H4, new during-hackathon work)

The R3-H4 work adds a machinery for letting a surprising or failed prior outcome
change the *next* choice, and records exactly whether it did. It is new
during-hackathon work: first commit on branch `kiro/r3h-s4-rerank` is `871ecf0`
("feat(r3h-s4): add announced_next + rerank second-pass fields with gated
validators"), which adds two additive, create-time, non-finalize-mutable fields
to the record schema in
[`../../spray_schema/decision_record.py`](../../spray_schema/decision_record.py):

- **`announced_next`.** Captures what the immediately-prior finalized lesson
  said to do next (`announced_action`, the verbatim `lesson_text`, and
  `source_decision_id`). This records the *announced plan* so it can be compared
  against what the agent actually chooses.
- **`rerank`.** A real SECOND scoring pass, fed the prior record's MEASURED
  outcome (`prior_outcome`: the prior `metric_key` / `measured` /
  `within_range` and `outcome_verdict`, keyed by `prior_outcome_decision_id`).
  It records each candidate's `old_rank` and `new_rank`, a boolean
  `diverged_from_announced`, and a `why`. When a prior prediction landed
  `within_range = false` the pass promotes the matching recount/recalibration
  candidate to `new_rank = 1`. This is an actual re-score driven by the measured
  outcome, not a post-hoc label.

Two **gated** validator rules enforce the honesty of this machinery; both fire
only when a `rerank` block is present, so every pre-existing record
D-0001..D-0018 (which carry no `rerank`) still validates exactly as before:

- **`rerank_missing_prior_outcome_citation`.** A record carrying a `rerank`
  block MUST cite via `prior_outcome_decision_id` a prior record that exists and
  is FINALIZED carrying a measured outcome (`numeric_error` and/or
  `outcome_verdict`). A rerank that fails to cite the prior measured outcome is
  rejected, so a re-rank pass cannot be claimed without the outcome it read.
- **`divergence_not_reflected_in_choice`.** When a rerank claims
  `diverged_from_announced = true`, the record's `chosen_action` must map to a
  DIFFERENT analysis than `announced_next.announced_action` maps to, and
  `rerank.why` must be non-empty. This only checks that a CLAIMED divergence is
  real; it never forces divergence.

These rules are covered by the on-branch tests
`tests/test_decision_record_rerank.py` and
`tests/test_decision_cycle_rerank.py` (part of the 854-test suite).

### Anchor-free re-scoring (R3-H5, new during-hackathon work)

R3-H4 left an open question: the four live cycles never diverged from their
announced next step, but the planner's re-scoring prompt still **saw** the prior
lesson's full text, which literally ends "...next proceed to X". That
announcement is an **anchor**. R3-H5 removes it. The first commit on branch
`kiro/r3h-s5-anchorfree` is `801ea28` ("feat(r3h-s5): anchor-free re-scoring
prompt + additive anchor_free record field + offline tests"). The design change
is purely in what the **re-scoring prompt** contains:

- When the re-scoring pass is active, each prior lesson string is passed through
  a strip step that removes the announced-next clause (using the same
  `_ANNOUNCED_NEXT_RE` boundary logic `decision_cycle.py` uses to extract it)
  **before** it enters the planner prompt. The planner's second pass now sees
  only the prior MEASURED outcome, the prediction error, and the candidates -
  not the sentence telling it what to do next. A pure, testable prompt-builder
  function lets an offline test assert the announced action text is **absent**
  from the built prompt while the post-choice comparison still records it.
- `announced_next` and `rerank.diverged_from_announced` / `rerank.why` are
  **still computed server-side** in `decision_cycle.py` (unchanged) and recorded
  on the record, so the divergence comparison is unaffected by what the prompt
  showed.
- Each new record carries an additive, create-time, non-finalize-mutable
  `anchor_free` boolean (shape-validated, with a gated rule so the pre-H5
  records D-0001 .. D-0022 and all fixtures still validate unchanged).

The honest live result of removing the anchor is reported in "Evidence status"
below: **no natural divergence, including after removing the announcement
anchor**. This is unit-tested in `tests/test_decision_cycle_anchor_free.py` (the
offline prompt-absence assertion) alongside the existing re-rank tests; the
suite is now 854 tests.

## How each decision drives the next (validator rules)

`validate_record` in
[`../../spray_schema/decision_record.py`](../../spray_schema/decision_record.py)
enforces that decisions form a real chain:

- **Sequential ids.** A record's `D-NNNN` id must be exactly one greater than
  the highest prior record (first record is `D-0001`).
- **Cited prior ids must exist.** Every id in `prior_decision_ids_read` must
  match an earlier record.
- **Cited lesson text must match verbatim.** Each `lessons_applied` entry must
  reference a prior record that is `FINALIZED` and whose `lesson` text matches
  the cited text byte-for-byte (`lesson_text_mismatch` otherwise).
- **No ignoring the past.** A record that has at least one available prior
  finalized lesson but cites none is rejected
  (`available_prior_lessons_not_cited`). `D-0001` is exempt because it has no
  priors.
- **Finalized records are immutable.** `validate_finalized_edit` rejects any
  change to an already-finalized record.

These rules are covered by the on-branch test suite
(`tests/test_decision_record.py` plus the rest of `tests/`), which stays at 854
tests passing (22 skipped, 0 failures).

## The runner that composes it (on `main`)

The end-to-end runner is now **on `main`**:

- [`../../spray_schema/decision_cycle.py`](../../spray_schema/decision_cycle.py)
  (first commit `4ea3082`): `run_one_cycle(...)` reads prior records and
  lessons, asks the planner for a self-generated plan (>=3 candidates plus
  scoring plus choice plus prediction plus outcome plus lesson), calls
  `create_record` (PREDICTED) **first**, asserts the `D-NNNN.json` file is on
  disk, **then** produces the artifact, **then** calls `finalize_record`. It
  also exposes `live_gate_status(...)` and the `LIVE-GATED` token.
- [`../../scripts/run_decision_cycle.py`](../../scripts/run_decision_cycle.py):
  the offline/live-gated CLI.

The provider-agnostic planner protocol and the Meta adapter
([`../../spray_schema/provider_planner.py`](../../spray_schema/provider_planner.py)
and
[`../../spray_schema/meta_planner.py`](../../spray_schema/meta_planner.py), both
first commit `105fea9`) and the measured-outcome executor
([`../../spray_schema/action_executor.py`](../../spray_schema/action_executor.py),
first commit `362dbc3`) are likewise on `main`.

## Run instructions

### Offline path (works right now, no key, no network)

Use the on-branch CLI
[`../../scripts/decision_cli.py`](../../scripts/decision_cli.py), which exposes
`create`, `finalize`, `validate`, and `lessons` subcommands:

```
# Validate a candidate record payload against existing records:
python3 scripts/decision_cli.py validate --decisions-dir decisions --json candidate.json

# Print the structured prior-lessons list the planner would read:
python3 scripts/decision_cli.py lessons --decisions-dir decisions

# Two SEPARATE runs keep the prediction honest:
python3 scripts/decision_cli.py create   --decisions-dir decisions --json create.json
python3 scripts/decision_cli.py finalize --decisions-dir decisions \
    --id D-0001 --actual-outcome "..." --prediction-vs-actual "..." --lesson "..."
```

Run the full offline test suite (stdlib `unittest`, not pytest):

```
python3 -m unittest discover -s tests -p 'test_*.py'
```

### Live-gated path (owner-gated runner)

The runner CLI
[`../../scripts/run_decision_cycle.py`](../../scripts/run_decision_cycle.py) is
the live-gated entry point. In this sandbox it prints a **single line beginning
with the `LIVE-GATED` token** and exits `0` without making any call when
`META_API_KEY` is absent or the model id is left at the placeholder sentinel.
The recorded cycles D-0001 .. D-0026 were run with `provider = meta`,
`model = muse-spark-1.3` via the OpenAI-compatible API base `api.meta.ai` (the
`model` field on every `decisions/D-*.json` and in
[`../../decisions/LEDGER.md`](../../decisions/LEDGER.md)). The code constant
`META_MODEL_ID` in
[`../../spray_schema/meta_planner.py`](../../spray_schema/meta_planner.py)
remains the overridable sentinel `muse-spark-PLACEHOLDER` (settable via the
`META_MODEL_ID` env var); the real id used by the cycles is recorded on the
records themselves. A fresh live run still requires the owner to supply the key
and pass the credit-budget check.

```
python3 scripts/run_decision_cycle.py            # prints a LIVE-GATED line, exits 0 when the gate is closed
```

## Credit / budget guards

Live spend is bounded by the credit-only budget in
[`../../spray_schema/credit_budget.py`](../../spray_schema/credit_budget.py)
(first commit `528df23`), which composes the on-branch fail-closed primitives in
[`../../spray_schema/resource_ledger.py`](../../spray_schema/resource_ledger.py):

- **Credits only, cash bucket zero** (`CASH_BUCKET_USD = 0`). Any positive
  spend is proven un-fundable from cash, so only non-fungible credit can ever
  fund a call. The per-cycle credit ledger
  [`../../decisions/LEDGER.md`](../../decisions/LEDGER.md) records cumulative
  **USD 0.52** across the 26 cycles with **cash USD 0.00**.
- **R2 per-call / per-round cap** `R2_PER_CALL_CAP_USD = 20` (USD). Inclusive
  ceiling; strictly over is denied.
- **Lifetime hard stop** `LIFETIME_HARD_STOP_USD = 140` (USD).
- **Credit expiry** `CREDIT_EXPIRY_ISO = 2026-11-05T02:00:00Z`; a call at or
  after that instant is refused.
- **Fail-closed** `authorize_meta_spend(...)`: any unknown, missing,
  non-finite, or negative input, or an unparseable timestamp, denies.

## Evidence status

There are now **26 recorded decision cycles**: `decisions/D-0001.json` ..
`decisions/D-0022.json` on `main` (the R3-H4 re-rank cycles D-0019 .. D-0022
merged to `main` via PRs #48/#49), plus the four R3-H5 anchor-free cycles
`decisions/D-0023.json` .. `decisions/D-0026.json` on branch
`kiro/r3h-s5-anchorfree`. All are tracked per cycle in
[`../../decisions/LEDGER.md`](../../decisions/LEDGER.md) (provider `meta`, model
`muse-spark-1.3`, est. USD 0.02 each, cumulative USD 0.52, cash USD 0.00). The
real evidence, with its honesty caveats:

- **Three consecutive measured HITs.** D-0010, D-0011, D-0012 are each
  `outcome_verdict = HIT` with `fallback = false` (executed `import_graph` /
  stats / `import_graph`) and carry no numeric claim. Each cites the prior
  record's finalized lesson verbatim, so they form three measured cycles that
  build on one another.
- **A real numeric MISS, then a corrected range.** D-0013 committed a
  `quantitative_claim` of `total_files` in `[20, 30]` (confidence 0.72) BEFORE
  acting; the executor measured **182**, so `within_range = false` (a genuine
  numeric miss). Because the chosen test-gap map is not executor-supported the
  run fell back to `inventory` (`fallback = true`), making `outcome_verdict =
  MISS`. D-0014 reacts to that falsified prediction: it cites D-0013's lesson
  verbatim, corrects the range to `[170, 195]` (confidence 0.85), and the
  measured **183** lands `within_range = true`. **D-0014 is still
  `outcome_verdict = MISS`** (it again fell back to `inventory`), so it is a
  numeric correction, not a clean HIT.
- **Executed == chosen with honest numeric misses.** D-0016, D-0017, D-0018 are
  each `outcome_verdict = HIT` with `fallback = false` (executed `inventory` /
  `markers` / `import_graph`). D-0016 measured `total_files = 186` inside
  `[175, 195]` (`within_range = true`); D-0017 measured `total_markers = 154`
  outside `[5, 50]` (`within_range = false`); D-0018 measured `edges = 165`
  outside `[180, 320]` (`within_range = false`). The D-0017 numeric miss is
  carried into D-0018. These are HITs on the executed-analysis match **and**
  honest numeric misses at the same time. **D-0017 -> D-0018 remains the primary
  demo example** (see [DEMO_SCRIPT_3MIN.md](DEMO_SCRIPT_3MIN.md)).
- **Four re-rank cycles (R3-H4), with the honest divergence result.** The four
  live cycles D-0019 .. D-0022 each carry the S8+S9 invariants (numeric
  `quantitative_claim` committed before acting, executed analysis == chosen
  analysis with `fallback = false`) PLUS an `announced_next` block citing the
  immediately-prior finalized lesson verbatim and a real `rerank` second pass
  fed the prior MEASURED outcome. The measured results: D-0019 chose/ran `stats`
  (`total_lines = 29543` inside `[25000, 45000]`, `within_range = true`);
  D-0020 chose/ran `duplicates` (`duplicate_basename_groups = 2` outside
  `[5, 25]`, `within_range = false`); D-0021 chose/ran `docs_coverage`
  (`documented = 353` outside `[90, 140]`, `within_range = false`); D-0022
  chose/ran `import_graph` (`edges = 170` inside `[150, 180]`,
  `within_range = true`). Each is `outcome_verdict = HIT` (executed == chosen,
  `fallback = false`). On the re-rank question, the honest result is stated in
  the caveat below: the second pass demonstrably re-ranked candidates, but no
  cycle diverged from its `announced_next`.
- **Four anchor-free cycles (R3-H5), with the honest divergence result.** The
  four live cycles D-0023 .. D-0026 carry the same S8+S9 invariants PLUS an
  `anchor_free = true` flag: their re-scoring prompt was built **without** the
  prior lesson's announced next step (see the anchor-free design section above),
  while `announced_next` and the divergence comparison stayed server-side. Each
  cites the immediately-prior finalized measured record (D-0023 cites D-0022,
  D-0024 cites D-0023, D-0025 cites D-0024, D-0026 cites D-0025). The measured
  results: D-0023 chose/ran `inventory` (`total_files = 198` outside `[180,195]`,
  `within_range = false`); D-0024 chose/ran `markers` (`total_markers = 235`
  outside `[130,175]`, `within_range = false`); D-0025 chose/ran `stats`
  (`total_lines = 30571` inside `[28000,31500]`, `within_range = true`); D-0026
  chose/ran `duplicates` (`duplicate_basename_groups = 2` inside `[1,5]`,
  `within_range = true`). All are `outcome_verdict = HIT` (executed == chosen,
  `fallback = false`). The honest divergence result: **outcome-driven divergence
  under the strict rule: none observed** - `rerank.diverged_from_announced =
  false` for all four records (and for all of D-0019 .. D-0026). (Nuance stated
  accurately: on D-0023, D-0025 and D-0026 the anchor-free chosen action DID
  differ from the server-side `announced_next.announced_action`, but the
  validator-enforced divergence rule ALSO requires the surprising-prior re-rank
  to have promoted a different-analysis candidate to the top, which did not
  occur, so the flag honestly stays `false` rather than overclaiming.) No
  divergence was engineered and no miss was forced.
- **The anchor effect (a SEPARATE finding, NOT outcome-driven divergence).**
  Removing the announcement from the re-scoring prompt (`anchor_free = true`)
  surfaced something distinct from outcome-driven divergence: with the announced
  next action hidden, **3 of 4 cycles (D-0023, D-0025, D-0026) chose a different
  ANALYSIS than the plan had announced** (D-0023 chose `inventory` vs an
  announced docs_coverage/stats follow-up; D-0025 chose `stats` vs an announced
  markers triage; D-0026 chose `duplicates` vs an announced stats line/byte
  analysis), while D-0024 chose what was announced (`markers`). This indicates
  the earlier plan-consistency across D-0016 .. D-0022 was partly **anchoring on
  the agent's own announced plan** rather than purely independent reasoning. It
  is explicitly **NOT** evidence of outcome-driven divergence: the anchor-free
  model never saw the announcement, so making a different choice is not evidence
  that the *outcome* drove the change. D-0025 (which followed the D-0024 numeric
  miss) may be described only as a **possible** case of outcome influence, not
  as proof. The clearly outcome-driven change remains the numeric range
  correction (D-0013 -> D-0014, headlined as D-0017 -> D-0018), not the choice
  of analysis.
- **Errata on the stored `rerank.why` prose.** The stored `rerank.why` string on
  D-0023, D-0025 and D-0026 inaccurately reads "...stays consistent with the
  announced next step" even though those cycles chose a different analysis than
  announced; the authoritative machine field `rerank.diverged_from_announced =
  false` is correct on all of them. The records are append-only and left
  unchanged; the inaccurate prose is disclosed and corrected in
  [ERRATA](../../decisions/ERRATA.md), and the generator is fixed so future
  records are accurate.

**Caveat (plan-following vs outcome-driven).** The analysis-choice *sequence*
partly follows plans announced in earlier lessons (each lesson literally ends
"...next proceed to X"), so the executed-analysis progression is partly
plan-following rather than a purely surprise-driven pivot. The clearly
**outcome-driven** change is the numeric correction: D-0013's falsified
`[20, 30]` vs measured 182 drove D-0014's corrected `[170, 195]`.

R3-H3 noted this as "choice-change is partly plan-following". R3-H4 added the
re-rank machinery described above (first commit `871ecf0`), and R3-H5 then
removed the announcement anchor from the re-scoring prompt (first commit
`801ea28`); both are unit-tested. **Outcome-driven divergence under the strict
rule: none observed** across all eight re-rank/anchor-free cycles D-0019 ..
D-0026: the second scoring pass **did** demonstrably re-rank candidates after
reading the prior measured outcome, **but no cycle diverged from its announced
next step** (`rerank.diverged_from_announced = false` for all eight records).
The machinery exists and fires a re-rank, but it did not flip a choice under the
strict rule. **No divergence was engineered and no miss was forced.**

**The anchor effect is a separate finding, not outcome-driven divergence.**
With the announcement hidden from the re-scoring prompt (`anchor_free = true`),
3 of 4 cycles (D-0023, D-0025, D-0026) chose a different analysis than the plan
had announced, which indicates the earlier D-0016 .. D-0022 plan-consistency was
partly **anchoring on the agent's own announced plan** rather than purely
independent reasoning. This is **not** evidence of outcome-driven divergence,
because the anchor-free model never saw the announcement, so a different choice
is not evidence the *outcome* drove it; D-0025 is only a **possible** case of
outcome influence, not proof. The clearly outcome-driven change remains the
numeric range correction D-0013 -> D-0014 (headlined as D-0017 -> D-0018), not
the choice of analysis itself. The stored `rerank.why` on D-0023/D-0025/D-0026
inaccurately says "stays consistent with the announced next step" while
`diverged_from_announced = false` is correct; this is disclosed and corrected in
[ERRATA](../../decisions/ERRATA.md). All of this is verifiable from the records
alone: compare each record's `announced_next.announced_action` against its
`chosen_action`, read the `anchor_free` flag, and read
`rerank.diverged_from_announced` and `rerank.prior_outcome_decision_id` in
`decisions/D-0019.json` .. `decisions/D-0026.json`.

## Limitations

- **Narrow judged domain.** Decision quality is judged by a small, zero-spend
  offline analysis-action domain: the six executor-supported analyses in
  [`../../spray_schema/action_executor.py`](../../spray_schema/action_executor.py)
  (`inventory`, `markers`, `stats`, `duplicates`, `docs_coverage`,
  `import_graph`). This keeps outcomes measurable and side-effect-free, but it
  is a deliberately small action space, not a general task domain.
- **Outcome-driven divergence under the strict rule: none observed; the anchor
  effect is a separate finding.** The *sequence* of chosen analyses partly
  follows plans announced in prior lessons. R3-H4 added a real re-rank second
  pass (first commit `871ecf0`, unit-tested) that re-scores candidates from the
  prior measured outcome; R3-H5 then removed the announced-next clause from the
  re-scoring prompt entirely (first commit `801ea28`, unit-tested) so the
  planner's second pass no longer saw the announcement anchor. Across the eight
  live cycles D-0019 .. D-0026 (the four anchor-free ones being D-0023 ..
  D-0026) the pass demonstrably re-ranked candidates, but
  `rerank.diverged_from_announced = false` for all eight, so **no cycle diverged
  from its announced next step under the strict rule** (outcome-driven
  divergence: none observed). The **anchor effect** is a SEPARATE finding, not
  outcome-driven divergence: with the announcement hidden (`anchor_free =
  true`), 3 of 4 cycles (D-0023, D-0025, D-0026) chose a different analysis than
  announced, which indicates the earlier D-0016 .. D-0022 plan-consistency was
  partly anchoring on the agent's own announced plan. Because the anchor-free
  model never saw the announcement, a different choice is NOT evidence the
  outcome drove it; D-0025 is only a **possible** case, not proof. The clearly
  outcome-driven change remains the numeric range correction (D-0013 -> D-0014),
  not the choice of analysis itself. The stored `rerank.why` on
  D-0023/D-0025/D-0026 inaccurately says "stays consistent with the announced
  next step" while `diverged_from_announced = false` is correct; this is
  disclosed and corrected in [ERRATA](../../decisions/ERRATA.md). This is stated
  plainly rather than overclaimed, and is checkable from the records alone
  (`anchor_free`, `announced_next`, `rerank`).
- **No real-Chromium smoke in this sandbox.** The G4/G5 real-Chromium
  evidence-gate smoke was not run in this offline sandbox (that path is
  offline/draft-gated); see PROVENANCE.md for its pre-hackathon classification.

## Related documents

- [PROVENANCE.md](PROVENANCE.md): what was built before vs during the event
  (authoritative before/during split; SPRAY began 2026-10-14).
- [PUBLIC_RELEASE_CHECKLIST.md](PUBLIC_RELEASE_CHECKLIST.md): pre-public
  secret/identity scan result.
- [ERRATA](../../decisions/ERRATA.md): correction of record of the inaccurate
  stored `rerank.why` prose on D-0023/D-0025/D-0026 (machine field
  `diverged_from_announced = false` is correct; records left append-only).
- [ARCHITECTURE.md](ARCHITECTURE.md): component and data-flow diagram.
- [SUBMISSION_FORM.md](SUBMISSION_FORM.md): drafts for the official submission
  fields.
- [DEMO_SCRIPT_3MIN.md](DEMO_SCRIPT_3MIN.md): timestamped 3-minute demo script.
- CI gate: [`../../ci/policy_checks.py`](../../ci/policy_checks.py),
  [`../../ci/validate_docs.py`](../../ci/validate_docs.py), enforced by
  `.github/workflows/ci.yml`.
