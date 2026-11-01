# SPRAY 의사결정 크레딧 원장 (LEDGER)

Per-cycle credit ledger for every SPRAY decision cycle. One row per decision
record (`decisions/D-NNNN.json`), in id order. All spend is **credits only**
(cash USD 0.00). `estimated_credit_cost_usd` is the per-call estimate recorded
on each record; the **cumulative** column is the running credit total across
all cycles to date. No secret (API key or model identifier beyond the already
public `model` column) appears in this file.

- Provider / model / per-cycle cost are sourced from each committed
  `decisions/D-NNNN.json` record.
- Cash spent: USD 0.00 (credits only).
- R2 per-call cap: USD 20.00. Lifetime hard stop: USD 140.00.

| Decision | Provider | Model | Est. credit cost (USD) | Cumulative (USD) |
| --- | --- | --- | --- | --- |
| D-0001 | meta | muse-spark-1.3 | 0.02 | 0.02 |
| D-0002 | meta | muse-spark-1.3 | 0.02 | 0.04 |
| D-0003 | meta | muse-spark-1.3 | 0.02 | 0.06 |
| D-0004 | meta | muse-spark-1.3 | 0.02 | 0.08 |
| D-0005 | meta | muse-spark-1.3 | 0.02 | 0.10 |
| D-0006 | meta | muse-spark-1.3 | 0.02 | 0.12 |
| D-0007 | meta | muse-spark-1.3 | 0.02 | 0.14 |
| D-0008 | meta | muse-spark-1.3 | 0.02 | 0.16 |
| D-0009 | meta | muse-spark-1.3 | 0.02 | 0.18 |
| D-0010 | meta | muse-spark-1.3 | 0.02 | 0.20 |
| D-0011 | meta | muse-spark-1.3 | 0.02 | 0.22 |
| D-0012 | meta | muse-spark-1.3 | 0.02 | 0.24 |
| D-0013 | meta | muse-spark-1.3 | 0.02 | 0.26 |
| D-0014 | meta | muse-spark-1.3 | 0.02 | 0.28 |
| D-0015 | meta | muse-spark-1.3 | 0.02 | 0.30 |
| D-0016 | meta | muse-spark-1.3 | 0.02 | 0.32 |
| D-0017 | meta | muse-spark-1.3 | 0.02 | 0.34 |
| D-0018 | meta | muse-spark-1.3 | 0.02 | 0.36 |
| D-0019 | meta | muse-spark-1.3 | 0.02 | 0.38 |
| D-0020 | meta | muse-spark-1.3 | 0.02 | 0.40 |
| D-0021 | meta | muse-spark-1.3 | 0.02 | 0.42 |
| D-0022 | meta | muse-spark-1.3 | 0.02 | 0.44 |
| D-0023 | meta | muse-spark-1.3 | 0.02 | 0.46 |
| D-0024 | meta | muse-spark-1.3 | 0.02 | 0.48 |
| D-0025 | meta | muse-spark-1.3 | 0.02 | 0.50 |
| D-0026 | meta | muse-spark-1.3 | 0.02 | 0.52 |

Note: D-0008 and D-0009 are **SUPERSEDED (R2-S7)** (see `DECISIONS.md`); their
rows and records are kept for audit and their credit cost still counts toward
the cumulative total. The replacement measured cycles D-0010..D-0012 were run
live via the Meta adapter and each recorded an honest `outcome_verdict` (all
HIT; executor-supported, `fallback=false`). The R2-S8 falsifiable cycles
D-0013..D-0015 were run live via the Meta adapter, each carrying a structured
`quantitative_claim` committed BEFORE acting and a measured `numeric_error`
recorded AFTER acting: D-0013 predicted `total_files` in [20, 30] but the
executor measured 182 (a genuine, truthful MISS, `within_range` false); the
model then reacted to that outcome and corrected its baseline, so D-0014
(measured 183) and D-0015 (measured 184) both landed `within_range` true. All
three executed the executor's inventory analysis via `fallback` (the chosen
test-gap/license/dead-export actions are not yet executor-supported), so each
`outcome_verdict` is an honest MISS on the intended-analysis match.

The R2-S9 cycles D-0016..D-0018 closed that gap: the live planner is now
constrained to the six executor-supported analyses and a chosen action that
would fall back is REJECTED at create (not silently substituted), so for these
three cycles the **executed analysis == the chosen analysis with
`fallback=false`** (contrast D-0013..D-0015, which all fell back to
`inventory`). D-0016 chose and ran `inventory` (measured `total_files=186`,
within its predicted [175, 195] range), D-0017 then chose and ran `markers`
(measured `total_markers=154`, outside its [5, 50] range), and D-0018 chose and
ran `import_graph` (measured `edges=165`, just outside its [180, 320] range).
Each carries a `quantitative_claim` committed BEFORE acting and a measured
`numeric_error` recorded AFTER acting, and each `outcome_verdict` is an honest
HIT on the intended-analysis match (executed==chosen). Cumulative credit spend
through D-0018: USD 0.36 (cash USD 0.00).

The R3-H4 re-rank cycles D-0019..D-0022 were run live via the Meta adapter, each
carrying the same S8+S9 invariants (falsifiable `quantitative_claim` committed
BEFORE acting, executed analysis == chosen analysis with `fallback=false`,
measured `numeric_error` after acting) PLUS an `announced_next` block that cites
the immediately-prior finalized lesson verbatim and a `rerank` block recording a
genuine SECOND scoring pass fed that prior's MEASURED outcome
(`prior_outcome_decision_id` + the prior `metric_key`/`measured`/`within_range`
and `outcome_verdict`). D-0019 chose and ran `stats` (`total_lines=29543`,
within [25000, 45000]); D-0020 chose and ran `duplicates`
(`duplicate_basename_groups=2`, outside [5, 25]); D-0021 chose and ran
`docs_coverage` (`documented=353`, outside [90, 140]); D-0022 chose and ran
`import_graph` (`edges=170`, within [150, 180]). Each `outcome_verdict` is an
honest HIT on the intended-analysis match (executed==chosen). On the re-rank
question: in every one of the four cycles the second pass DID re-order the
candidates after reading the prior measured outcome (when a prior prediction
landed `within_range` false the pass promoted the matching recount/recalibration
candidate to new_rank 1), but the live planner each time still chose the action
its prior lesson had announced as the next step, so `diverged_from_announced` is
**false** for all of D-0019..D-0022. No divergence was engineered and no miss was
forced; this is the honest recorded outcome. Cumulative credit spend through
D-0022: USD 0.44 (cash USD 0.00).

The R3-H5 **anchor-free** batch D-0023..D-0026 ran four live cycles via the Meta
adapter under one change: in the re-scoring pass the planner prompt EXCLUDED the
prior lesson's announced next action (the "next proceed to X" directive was
stripped from each prior lesson before it entered the prompt, `anchor_free=true`
on every one of these four records). The `announced_next` and
`rerank.diverged_from_announced` comparison is still computed server-side from
the RAW (un-stripped) prior lesson, so it is unaffected by the prompt change.
All four cycles carried the same S8+S9+R3-H4 invariants: a falsifiable
`quantitative_claim` committed BEFORE acting, the **executed analysis == the
chosen analysis with `fallback=false`**, a measured `numeric_error` recorded
AFTER acting, and a `rerank` block citing the immediately-prior finalized
measured record (D-0023 cites D-0022, D-0024 cites D-0023, D-0025 cites D-0024,
D-0026 cites D-0025). D-0023 chose and ran `inventory` (`total_files=198`,
outside [180, 195]); D-0024 chose and ran `markers` (`total_markers=235`,
outside [130, 175]); D-0025 chose and ran `stats` (`total_lines=30571`, within
[28000, 31500]); D-0026 chose and ran `duplicates` (`duplicate_basename_groups
=2`, within [1, 5]). Each `outcome_verdict` is an honest HIT on the
intended-analysis match (executed==chosen). On the divergence question:
**`diverged_from_announced` is `false` for all four of D-0023..D-0026**; each
record's own `why` reads "prior measured outcome fed the second scoring pass;
chosen action stays consistent with the announced next step". Removing the
announcement anchor from the prompt did NOT produce a cycle that the machinery
records as a divergence. (The chosen action did differ from the server-side
`announced_next.announced_action` on D-0023, D-0025 and D-0026 — e.g. D-0025
chose `stats` while the announced action mapped to `markers` — but the
validator-enforced divergence rule requires the surprising-prior re-rank to also
promote a candidate whose analysis differs from the announced one, which did not
happen, so the flag honestly stays `false` rather than overclaiming.) No
divergence was engineered and no miss was forced; this is the honest recorded
outcome of the anchor-free batch. Cumulative credit spend through D-0026: USD
0.52 (cash USD 0.00).
