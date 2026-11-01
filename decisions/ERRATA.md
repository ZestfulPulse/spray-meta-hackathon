# Decision Record Errata

**Purpose.** This errata corrects inaccurate machine-generated prose found on a
small number of finalized decision records without editing those records.

Decision records (`D-*.json`) are **append-only / finalized** artifacts: once a
record is written and finalized it is left byte-for-byte unchanged. Inaccurate
generated prose is therefore corrected here, in this errata, rather than by
editing the records themselves. The authoritative machine fields on each record
are unchanged and remain correct; only a human-readable explanatory string is
addressed below.

## ERRATUM-1: inaccurate `rerank.why` on D-0023, D-0025, D-0026

The records [D-0023.json](D-0023.json), [D-0025.json](D-0025.json), and
[D-0026.json](D-0026.json) each store a `rerank.why` string that reads exactly:

```
prior measured outcome fed the second scoring pass; chosen action stays consistent with the announced next step
```

### Why this string is inaccurate on these three records

On each of these three records the **chosen** analysis differs from the analysis
implied by `announced_next.announced_action`, so the half-sentence "chosen action
stays consistent with the announced next step" is literally false for them:

- **D-0023** chose a repo-local **file inventory**, whereas the announced next
  step was a **docs_coverage / stats** follow-up.
- **D-0025** chose **stats** (line/byte size statistics), whereas the announced
  next step was a **markers** triage.
- **D-0026** chose a **duplicates** scan, whereas the announced next step was a
  **stats** line/byte analysis.

In every case the chosen analysis is a different analysis than the announced one,
so the stored sentence does not describe what actually happened.

> Note: the "announced" analysis names above reflect the **primary intent** of
> the stored `announced_action` text, and the mechanical `map_action_to_analysis`
> label may differ on a long announced string (for example, D-0026's
> `announced_action` maps to `markers` under the keyword mapper); but in every
> listed case the chosen analysis differs from the announced one under either
> reading, so `diverged_from_announced=false` remains correct.

### The authoritative machine field is correct

On all three records the authoritative machine field
`rerank.diverged_from_announced` is `false`, and **that is correct** under the
strict divergence rule. The strict rule flags a divergence only when a
*surprising* prior outcome re-ranks the candidates so that a **different-analysis
candidate is promoted to the top AND the cycle actually chooses that candidate**.
That promotion did not occur on any of these three cycles (the prior outcome was
not surprising enough to re-rank the top candidate), so `false` is the right
value. The inaccuracy is confined to the human-readable `why` prose, not the
machine verdict.

### D-0024 is not affected

[D-0024.json](D-0024.json) stores the same sentence, but on D-0024 the chosen
analysis **equals** the announced one, so the sentence is accurate there. D-0024
is therefore **not** listed as an erratum.

### Corrective action

The generator has been corrected in
[../spray_schema/decision_cycle.py](../spray_schema/decision_cycle.py) so that a
cycle whose chosen analysis differs from the announced next step (without a
strict surprising-prior divergence) now emits an accurate `why` that states the
chosen analysis differs, instead of claiming it "stays consistent with the
announced next step". The chosen-equals-announced case still emits the accurate
"stays consistent" wording. The fix is covered by an offline test in
[../tests/test_decision_cycle_rerank.py](../tests/test_decision_cycle_rerank.py).

This fix affects **future** records only. The three existing records
(D-0023, D-0025, D-0026) are intentionally left **byte-for-byte unchanged**
because the record set is append-only; this errata is the disclosure of record.
