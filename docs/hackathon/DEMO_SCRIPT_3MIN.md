# SPRAY: 3-Minute Demo Script

> **Note:** This is the original planning script. The submitted video is a shorter cut (about 1:51): a 60-second overview followed by live evidence from GitHub Actions and the decision records D-0013 and D-0014.

A timestamped script for a 3:00 demo walking ONE full, REAL decision chain
(candidates -> scoring -> prediction committed before acting -> artifact ->
measured outcome -> prediction-vs-actual -> lesson -> next decision citing the
prior lesson) plus the credit-ledger view. The **primary demo anchor is the
real `decisions/D-0017.json` -> `decisions/D-0018.json` chain** on `main`
(executed == chosen with honest numeric misses); the script also walks the real
`decisions/D-0013.json` -> `decisions/D-0014.json` numeric-correction pair as a
supporting example, and closes by pointing at the R3-H4 re-rank cycles
`decisions/D-0019.json` .. `decisions/D-0022.json` (now on `main`) and the four
R3-H5 **anchor-free** cycles `decisions/D-0023.json` .. `decisions/D-0026.json`
on branch `kiro/r3h-s5-anchorfree`.

**Honesty rules for this demo:**

- The decision chains shown are **real**: `decisions/D-0017.json` ->
  `decisions/D-0018.json` (the primary anchor) and `decisions/D-0013.json` ->
  `decisions/D-0014.json` (supporting) are committed, finalized records on
  `main`; the re-rank cycles `decisions/D-0019.json` .. `decisions/D-0022.json`
  are also on `main`, and the anchor-free cycles `decisions/D-0023.json` ..
  `decisions/D-0026.json` are committed on the branch `kiro/r3h-s5-anchorfree`.
  No live Meta call is made
  *during the demo itself*; the records were produced earlier with
  `provider = meta`, `model = muse-spark-1.3` via `api.meta.ai`.
- Two signals are kept distinct: `outcome_verdict` HIT/MISS (executed analysis
  == chosen analysis AND `fallback = false`) and the separate numeric
  `within_range` signal on the `quantitative_claim`. D-0014's corrected range
  lands `within_range = true` but the record is **still** `outcome_verdict =
  MISS` (it fell back to `inventory`), so it is not presented as a clean HIT.
- The commands shown under "run now" are **real and runnable today** against the
  on-branch offline CLI; they do not call any model.

---

## Timeline

### 0:00 to 0:20: The frontier (problem)

Say: "Most agents act and forget. SPRAY writes down a **prediction before it
acts**, judges its own decision against that prediction, and must carry the
**lesson** into its next decision." Show
[README.md](README.md) (problem section) on screen.

### 0:20 to 0:45: The record schema is real and tested

Show [`../../spray_schema/decision_record.py`](../../spray_schema/decision_record.py)
(PR #41, `c94c231`, merged `17e9f26`). Run the offline test suite live:

```
python3 -m unittest discover -s tests -p 'test_*.py'
```

Point at the result line: 854 tests, 22 skipped, 0 failures. This proves the
two-phase store and validator (including the R3-H4 re-rank rules
`tests/test_decision_record_rerank.py` and `tests/test_decision_cycle_rerank.py`
and the R3-H5 anchor-free prompt test `tests/test_decision_cycle_anchor_free.py`)
behave as described, with no live call needed.

### 0:45 to 1:15: Candidates, scoring, and prediction BEFORE acting

Open the real [`../../decisions/D-0013.json`](../../decisions/D-0013.json).
Point out the three self-generated candidates and their scores: **test-gap map
(score 9)**, license-header audit (6), export-surface audit (5). The chosen
action is the test-gap map. The `quantitative_claim` is committed BEFORE acting:
`metric_key = total_files`, predicted range `[20, 30]`, `confidence 0.72`, with
`status = PREDICTED` and the outcome fields null at create time. Show the real
CLI that enforces two separate runs,
[`../../scripts/decision_cli.py`](../../scripts/decision_cli.py):

```
python3 scripts/decision_cli.py create   --decisions-dir decisions --json create.json
# ...the action runs separately...
python3 scripts/decision_cli.py finalize --decisions-dir decisions \
    --id D-0013 --actual-outcome "..." --prediction-vs-actual "..." --lesson "..."
```

Note: the validator requires **>=3 candidates** and non-empty
`scoring_criteria` / `candidate_scores` / `prediction`, or the create is
refused and nothing is persisted.

### 1:15 to 2:15: Action -> artifact -> measured outcome (a real MISS)

Explain that the runner `spray_schema/decision_cycle.py` `run_one_cycle` (on
`main`, first commit `4ea3082`) calls `create_record` first, asserts the
`D-NNNN.json` file is on disk, then produces the artifact under
`decisions/artifacts/D-NNNN/`, then calls `finalize_record` via the
measured-outcome executor `spray_schema/action_executor.py` (first commit
`362dbc3`).

Show D-0013's finalized outcome: the executor **measured `total_files = 182`**,
so `numeric_error.within_range = false` (`absolute_error = 152`) -- a genuine
numeric MISS against the predicted `[20, 30]`. Because the chosen test-gap map
is not executor-supported, the run **fell back to `inventory`**
(`actual_outcome.fallback = true`), so `outcome_verdict = MISS`. This is an
honest miss on both signals, surfaced rather than hidden.

### 2:15 to 2:40: The next decision reacts to the falsified prediction

Open the real [`../../decisions/D-0014.json`](../../decisions/D-0014.json). Its
`chosen_rationale` and `followup_decision` both cite D-0013's MISS (predicted
`[20, 30]` vs measured 182), and `lessons_applied` cites D-0013's finalized
lesson **verbatim**. D-0014 corrects the `quantitative_claim` to `total_files`
`[170, 195]` (`confidence 0.85`); the executor measured **183**, so
`within_range = true`. Be explicit: D-0014 is **still `outcome_verdict = MISS`**
because it again fell back to `inventory`. It is a numeric correction that
landed in range, not a clean HIT. Run the real lessons helper live to show it
now returns real finalized lessons (not `[]`):

```
python3 scripts/decision_cli.py lessons --decisions-dir decisions
```

One-line superseded honesty beat: "The early cycles D-0001 .. D-0009 are kept
but **SUPERSEDED** (see `decisions/DECISIONS.md`): they had model-asserted
outcomes and/or silent inventory fallbacks that hid misses, and were replaced by
the later measured machinery (R2-S6 measured outcomes, R2-S7 HIT/MISS
executed-match, R2-S8 numeric within_range) that makes misses visible."

### 2:40 to 3:00: Credit ledger view and the live gate

Show the real [`../../decisions/LEDGER.md`](../../decisions/LEDGER.md): one row
per cycle D-0001 .. D-0026, `provider = meta`, `model = muse-spark-1.3`, est.
USD 0.02 each, **cumulative USD 0.52, cash USD 0.00**. Describe the credit-only
budget `spray_schema/credit_budget.py` (on `main`, `528df23`): cap USD 20 per
call, lifetime hard stop USD 140, credit expiry `2026-11-05T02:00:00Z`, cash
bucket zero, fail-closed `authorize_meta_spend`, composing on-branch
[`../../spray_schema/resource_ledger.py`](../../spray_schema/resource_ledger.py).

Primary-anchor beat (say in one breath): "The headline chain is the real
`decisions/D-0017.json` -> `decisions/D-0018.json`: D-0017 chose and ran
`markers` (measured `total_markers = 154`, outside its `[5, 50]` range -
`within_range = false`) yet is an honest `outcome_verdict = HIT` because
executed == chosen with `fallback = false`; D-0018 then chose and ran
`import_graph` (measured `edges = 165`, outside `[180, 320]`), carrying D-0017's
numeric miss forward. The R3-H3 caveat was that choice-change is partly
plan-following. R3-H4 (first commit `871ecf0`) added a real re-rank second pass
fed the prior measured outcome; R3-H5 (first commit `801ea28`) then removed the
announced next step from the re-scoring prompt entirely (`anchor_free = true` on
D-0023 .. D-0026). Across all eight cycles
`decisions/D-0019.json` .. `decisions/D-0026.json` the pass demonstrably
re-ranked candidates, but **outcome-driven divergence under the strict rule:
none observed** (`rerank.diverged_from_announced = false` for all eight) -
stated honestly, no divergence engineered, no miss forced. A SEPARATE finding
is the **anchor effect**: with the announcement hidden, 3 of 4 anchor-free
cycles (D-0023, D-0025, D-0026) chose a different analysis than announced, which
indicates the earlier plan-consistency was partly anchoring on the agent's own
announced plan - this is NOT outcome-driven divergence (the model never saw the
announcement), and D-0025 is only a possible case of outcome influence, not
proof. One-line errata note: the stored `rerank.why` on D-0023/D-0025/D-0026
inaccurately says it 'stays consistent with the announced next step' while the
machine field `diverged_from_announced = false` is correct; the records are
append-only and the prose is corrected in
[ERRATA](../../decisions/ERRATA.md)."
Then run the owner-gated live runner to show it refuses safely when the key is
absent:

```
python3 scripts/run_decision_cycle.py       # prints one LIVE-GATED line, exits 0 when the gate is closed
```

Close: "A fresh live run is gated until the owner supplies the key and passes
the credit check; the capability, its guards, and 22 recorded cycles
(D-0001 .. D-0022) are already on `main`, with four more anchor-free cycles
(D-0023 .. D-0026) on the R3-H5 branch - 26 cycles in all."

---

## Appendix: record shape (illustrative)

> **This appendix shows the SHAPE of a `create` payload only.** The real,
> committed cycles are `decisions/D-0001.json` .. `decisions/D-0022.json` (on
> `main`) and `decisions/D-0023.json` .. `decisions/D-0026.json` (on branch
> `kiro/r3h-s5-anchorfree`); the demo above anchors on the real
> `decisions/D-0017.json` -> `decisions/D-0018.json` chain and also walks the
> real `decisions/D-0013.json` -> `decisions/D-0014.json` pair. The JSON below
> is an illustrative field-shape example for the `scripts/decision_cli.py
> create` input and is **not** a committed record.

```json
{
  "id": "D-XXXX",
  "observed_state": {"self_chosen_domain": "SPRAY self-directed falsifiable repo analysis"},
  "prior_decision_ids_read": ["D-0001"],
  "candidates": [
    {"name": "candidate-A"},
    {"name": "candidate-B"},
    {"name": "candidate-C"}
  ],
  "scoring_criteria": ["zero-side-effect compliance", "distinct value", "incremental fit"],
  "candidate_scores": [{"candidate": "candidate-A", "score": 9}],
  "chosen_action": "produce a repo-local offline artifact with zero side effects",
  "chosen_rationale": "applies the prior lesson's next-step directive",
  "rejected": [{"candidate": "candidate-B", "why": "lower incremental fit"}],
  "prediction": "offline scan will produce a repo-local artifact with zero side effects",
  "lessons_applied": []
}
```

To exercise the shape locally against a scratch directory (never the real
`decisions/`):

```
python3 scripts/decision_cli.py create --decisions-dir /tmp/spray-demo --json create.json
```

---

See [README.md](README.md), [ARCHITECTURE.md](ARCHITECTURE.md),
[SUBMISSION_FORM.md](SUBMISSION_FORM.md), and [PROVENANCE.md](PROVENANCE.md).
