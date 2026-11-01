# SPRAY Public-Release Checklist

Before making this repository public (for the Meta Global AI Developer
Hackathon submission), work through this checklist. The RESULT section below
reports an **actual** full-history secret scan that was run to produce this
document. No matched secret value is printed anywhere in this file, in the scan
output, or in any log — only categories, counts, and file/commit locations.

## Checklist

- [ ] **Secret / credential scan of full history (all refs).** Scan every blob
  reachable from all refs, not just the current tree, using the repo's own
  credential patterns (`ci/policy_checks.py`). See RESULT below. Treat any
  real-credential hit as a blocker.
- [ ] **Personal / owner-identifying strings in committed files.** Check for
  real personal emails and personal names in tracked files. The public
  owner/org handle `ZestfulPulse` is intentional and allowed.
- [ ] **Personal / owner-identifying strings in commit metadata.** Review
  author/committer names and emails across all refs. Decide whether any private
  address needs a mailmap or history consideration before going public.
- [ ] **Issue / PR content review.** Review open and closed issues and PRs for
  pasted logs, tokens, internal URLs, or private discussion that should not be
  public.
- [ ] **Decision-record field re-check.** Before any `decisions/D-*.json`
  record is committed, re-check each field for leaked internal data (see the
  field list below).
- [ ] **Repository-visibility decision.** Flipping the repo to public is **out
  of scope** for this change; this is a reminder item for the owner only.

## RESULT — actual full-history secret scan

The scan was produced by a **throwaway** script (run from outside the repo at
`/projects/scan_history.py`, deleted before commit, never committed) that
**imports** `CREDENTIAL_PATTERNS`, `STRICT_PATTERN_NAMES`, and the
comment/prose/placeholder context helper from `ci/policy_checks.py` without
modifying that file. It enumerates every blob across all refs via
`git rev-list --all --objects`, reads each blob with `git cat-file -p`, and
applies the same strict-vs-generic context rules the CI gate uses. For every
match it records **only** `{category, blob_sha, file_path, line_number}` — never
the matched substring.

**Exact command run (contains no secrets — safe to show):**

```
python3 /projects/scan_history.py          # imports ci.policy_checks patterns; scans git rev-list --all --objects
```

Supporting enumeration commands used for the identity / personal-string
counts:

```
git log --all --format='%ae%n%ce' | sort -u | wc -l     # distinct author/committer emails
git log --all --format='%an%n%cn' | sort -u | wc -l     # distinct author/committer names
git grep -nIE '[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}' -- .   # email-shaped strings in tracked files (locations only)
git grep -li 'zestfulpulse'                              # owner-handle occurrences (allowed)
git rev-list --all --objects | grep decisions/D-         # committed decision records (D-0001 .. D-0026)
```

This re-run covers **all refs including the new branch `kiro/r3h-s5-anchorfree`**
(the R3-H5 anchor-free batch) and the committed records `decisions/D-0001.json`
.. `decisions/D-0026.json` (D-0001 .. D-0022 on `main`; D-0023 .. D-0026 on
`kiro/r3h-s5-anchorfree`).

### Credential categories scanned

All four `ci/policy_checks.py` categories were applied:

- `aws_access_key_id` — strict, scans every line.
- `aws_secret_access_key_assignment` — strict, scans every line.
- `pem_private_key_header` — strict, scans every line.
- `generic_hardcoded_secret_assignment` — loose; skipped on comment / prose /
  placeholder lines exactly as the gate does.

### Per-category counts (all refs; distinct text blobs scanned)

| Category | Hits (all refs) | Real credentials |
| --- | --- | --- |
| `aws_access_key_id` | 4 | 0 |
| `aws_secret_access_key_assignment` | 0 | 0 |
| `pem_private_key_header` | 1 | 0 |
| `generic_hardcoded_secret_assignment` | 13 | 0 |
| **Total** | **18** | **0** |

**All 18 hits are intentional test fixtures or spec examples, not real
credentials.** They live only in test and spec files (none in runtime
`spray_schema/` code), by location:

| Category | File (location only) | Nature |
| --- | --- | --- |
| `generic_hardcoded_secret_assignment` | `tests/test_policy_checks.py` (5 lines) | Fixtures for the scanner's **own** unit test |
| `aws_access_key_id` | `tests/test_policy_checks.py` (2 lines) | Fixtures for the scanner's own unit test |
| `pem_private_key_header` | `tests/test_policy_checks.py` (1 line) | Fixture for the scanner's own unit test |
| `generic_hardcoded_secret_assignment` | `tests/test_gemini_planner.py` (6 lines) | Planner test fixtures |
| `generic_hardcoded_secret_assignment` | `tests/test_model_observation_quoted_secrets.py` (1 line) | Redaction test fixture |
| `aws_access_key_id` | `tests/test_meta_request_redaction.py` (2 lines, history-only blob) | Redaction test fixture |
| `generic_hardcoded_secret_assignment` | `docs/specs/R1-S2.md` (1 line, history-only blob) | Spec example text |

Why the CI gate is still green while the all-refs scan reports 18: the gate
(`python ci/policy_checks.py`) scans only the **current tracked tree** and the
loose generic pattern is skipped on comment / prose / placeholder lines, so
these fixtures do not turn the gate red. `python ci/policy_checks.py` exits `0`
("OK (no credential formats detected)") on `main` HEAD. The all-refs scan
additionally reaches **historical / other-branch blobs** (e.g.
`tests/test_meta_request_redaction.py` and `docs/specs/R1-S2.md`, which are not
on the current tree), which is why its count is higher. The scan was reported
truthfully: **0 real-credential findings; 18 fixture/example matches by
location.**

### Identities (counts only — no addresses or personal names transcribed)

| Metric | Count |
| --- | --- |
| Distinct author/committer identity lines (`%an %ae` / `%cn %ce`) across all refs | 5 |
| Distinct author/committer **emails** across all refs | 5 |
| Distinct author/committer **names** across all refs | 5 |

Action for owner: review these 5 identities before public release and decide
whether any private email needs a `.mailmap`. No address or personal name is
transcribed into this document.

### Personal / owner-identifying strings in committed files (locations only)

| Category | Count | Locations |
| --- | --- | --- |
| Email-shaped strings in tracked files | 8 matches across 5 files | `tests/test_g45_synthetic_gate_orchestrator.py`, `tests/test_meta_request_redaction.py`, `tests/test_model_observation_protocol_relative.py`, `tests/test_model_observation_urls.py`, `tests/test_self_direction_planner.py` |
| Owner/org handle `ZestfulPulse` (allowed) | 22 files | e.g. `README.md` (`ops.zestfulpulse.com`), test and doc files |

The 8 email-shaped strings use the placeholder domains `example.invalid`,
`host.example`, and `preview.example` (test-fixture addresses),
not personal mailboxes. The `ZestfulPulse` handle is the intentional public
owner/org identity and is allowed. No real personal email address was found in
tracked files. (The count rose from 6-across-3 to 8-across-5 since the last
scan: the newly-counted `tests/test_meta_request_redaction.py` and
`tests/test_self_direction_planner.py` each carry one `@host.example`
placeholder fixture.)

### Decisions / artifacts PII scan (committed records)

The committed decision records now span `decisions/D-0001.json` ..
`decisions/D-0026.json` (including the four R3-H5 anchor-free cycles
D-0023 .. D-0026 on branch `kiro/r3h-s5-anchorfree`), so they were scanned for
leaked data. The same throwaway, outside-the-tree scanner approach was used:
every `decisions/D-*.json` record plus `decisions/DECISIONS.md`,
`decisions/LEDGER.md`, and every `decisions/artifacts/*/*.json` artifact
(**54 files in all** under `decisions/`) was read and checked for email-shaped
strings, owner-identifying absolute filesystem paths (`/home`, `/Users`,
`/root`, `C:\Users`), and credential shapes. Only `{category, file, count}` is
reported; no matched value is printed.

| Category | Hits | Locations |
| --- | --- | --- |
| Email-shaped strings | 0 | — |
| Owner-identifying absolute paths (`/home`, `/Users`, `/root`, `C:\Users`) | 0 | — |
| Credential shapes (any `ci/policy_checks.py` category) | 0 | — |

All path-shaped values inside the records are **repo-relative** (for example
`artifacts/D-0013/inventory.json` and, for the anchor-free batch,
`artifacts/D-0023/inventory.json`, `artifacts/D-0024/markers.json`,
`artifacts/D-0025/stats.json`, `artifacts/D-0026/duplicates.json`), not absolute
host paths. The scan of the decision records and artifacts is **clean**: 0
emails, 0 owner-identifying absolute paths, 0 credential hits across all 54
files. Nothing in `decisions/` requires owner escalation before public release.

### Issue / PR content to review before public release

- Open/closed PRs referenced in provenance: #40, #41, #42, #43, #48, #49 (all
  merged on `main`). Review their descriptions, diffs, and comments for pasted
  secrets, tokens, or internal URLs.
- Earlier PRs #6, #8, #35, #37 (pre-hackathon work). Review for the same.
- Issue #39 (the task-tracking issue) and any linked issues: review for private
  logs or credentials before the repo is public.

### Decision-record fields to re-check (from `spray_schema/decision_record.py`)

Before any live `decisions/D-*.json` record is committed, re-check each of these
fields for leaked internal data (reference `decisions/DECISIONS.md` and
`spray_schema/decision_record.py`):

`id`, `observed_state`, `prior_decision_ids_read`, `candidates`,
`scoring_criteria`, `candidate_scores`, `chosen_action`, `chosen_rationale`,
`rejected`, `prediction`, `status`, `actual_outcome`, `prediction_vs_actual`,
`lesson`, `lessons_applied`. The `observed_state`, `actual_outcome`, and
`lesson` free-text fields are the most likely to accidentally carry environment
details and should get the closest review. The 26 committed records
(`decisions/D-0001.json` .. `decisions/D-0026.json`, including the anchor-free
batch D-0023 .. D-0026 which additionally carry the `anchor_free`,
`announced_next`, and `rerank` fields) plus `decisions/DECISIONS.md`,
`decisions/LEDGER.md`, and the `decisions/artifacts/` outputs were scanned (see
the "Decisions / artifacts PII scan" subsection above) and are clean: 0 emails,
0 owner-identifying absolute paths, 0 credential hits, with all record paths
repo-relative. Any **future** record appended to `decisions/` should be
re-checked against this same field list before commit.

## Option B: clean snapshot of main (owner creates the new public repo)

Instead of flipping the existing private repository (with its full history) to
public, the owner may publish a **clean snapshot of `main` with NO git
history** into a **new public repository the owner creates**. Creating repos and
changing visibility are owner actions and are deliberately **out of scope** for
this change; this section is a **plan / file list only**, not an executed
publish step. A clean snapshot carries no commit metadata, so the identity and
full-history concerns in the sections above do not apply to it; the snapshot
ships only the files enumerated below.

The snapshot file set is derived mechanically: base include = `git ls-files`
(the tracked tree, **228 files** on `main` HEAD of this branch, which is the 227
tracked files on `main` plus the new `decisions/ERRATA.md`) **minus** the
justified exclusions below. Every excluded item is already untracked /
gitignored, so it does not appear in `git ls-files` and is therefore not in the
snapshot anyway; it is listed explicitly for the owner's benefit.

### INCLUDE list (the tracked tree = what the snapshot ships)

- **`spray_schema/`**: runtime modules (decision-cycle runner, record schema +
  validator, action executor, planner adapters, credit budget, resource ledger,
  model-observation redaction, etc.).
- **`scripts/`**: CLIs and evidence scripts (`decision_cli.py`,
  `run_decision_cycle.py`, and the G3/G4/G5 Chromium/preview runners +
  `verify_g45_evidence.py`).
- **`tests/`**: the full stdlib `unittest` suite.
- **`ci/`**: `policy_checks.py`, `validate_docs.py`, `README.md`, `__init__.py`.
- **`decisions/`**: the records `decisions/D-0001.json` ..
  `decisions/D-0026.json`, the artifacts under `decisions/artifacts/` (one
  per cycle), `decisions/DECISIONS.md`, `decisions/LEDGER.md`, and
  `decisions/ERRATA.md` (55 tracked files under `decisions/`).
- **`docs/`**: including **`docs/hackathon/`** (`ARCHITECTURE.md`, `README.md`,
  `SUBMISSION_FORM.md`, `DEMO_SCRIPT_3MIN.md`, **`PROVENANCE.md`**,
  `PUBLIC_RELEASE_CHECKLIST.md`) and the `docs/*.md` design docs (the
  `P0_G3_*` / `P0_G4_*` / `P0_G5_*` notes, `G3_GEMINI_LOCAL_PROOF.md`,
  `PR7_INTEGRATION_GATE.md`, `RESOURCE_LEDGER.md`).
- **`evidence/g45/`**: the G4/G5 JSON evidence artifacts (plus the one tracked
  `.py` focused-evidence helper).
- **Root files**: `README.md`, `requirements-dev.txt`, `.gitignore`.
- **`.github/workflows/`**: `ci.yml` and `g45-chromium-smoke.yml`.

**Explicit confirmation:** `docs/hackathon/PROVENANCE.md` **is in the INCLUDE
list** (it is a tracked file under `docs/hackathon/`), so the before/during
provenance split ships with the snapshot.

### EXCLUDE list (with justification)

- **`.git` history**: a clean snapshot has **no history**; only the working
  tree is published. This drops all commit metadata (authors, committers,
  emails) and every history-only blob.
- **`.agents/`**: orchestration / agent task state; **gitignored and already
  untracked**, so it is not in `git ls-files`. Listed explicitly so the owner
  knows internal agent state is not published.
- **`.kiro/`**: editor / agent internals; untracked, not part of the tracked
  tree.
- **`__pycache__/` and `*.pyc`**: Python bytecode; gitignored, never tracked.
- **`docs/specs/`**: **not tracked on `main`**: `docs/specs/R1-S2.md` exists
  only as a git-history blob, so it is **not part of a tracked-file snapshot**
  and is excluded for free (there is no tracked `docs/specs/` path to ship).
- **Any throwaway scanner script**: the snapshot-scoped scanner described in
  the next subsection was written **outside** the repository tree and deleted
  after running; it was never committed and is not in the tree.

## Snapshot-scoped secret / personal-data scan (Option B)

Separate from the full-history scan above, a secret/personal-data scan was
re-run **scoped to the snapshot file set** (the INCLUDE list = tracked files
only). The scanner was a **throwaway** script written **outside** the repo
(under `/projects/`, not inside `/projects/sandbox/SPRAY`), run once, its counts
captured, then **deleted**; it was never committed. It **imports**
`CREDENTIAL_PATTERNS`, `STRICT_PATTERN_NAMES`, and the comment/prose/placeholder
context helpers from `ci/policy_checks.py` **without modifying that file**, and
additionally scans for email-shaped strings and owner-identifying absolute paths
(`/home`, `/Users`, `/root`, `C:\Users`). For every match it records **only**
`{category, file, count}`, never the matched substring.

Because a clean-snapshot scan is scoped to the **tracked tree** (not all refs),
it does **not** reach the history-only / other-branch fixture blobs that made
the full-history count 18; scoped to the files the snapshot actually ships, the
credential count is **0**.

### Per-category credential counts (snapshot = 228 tracked files)

| Category | Hits (snapshot) | Real credentials |
| --- | --- | --- |
| `aws_access_key_id` | 0 | 0 |
| `aws_secret_access_key_assignment` | 0 | 0 |
| `pem_private_key_header` | 0 | 0 |
| `generic_hardcoded_secret_assignment` | 0 | 0 |
| **Total** | **0** | **0** |

All credential categories are **0** over the tracked snapshot (the only
credential-shaped literals in the repo are test fixtures / spec examples that
live in history-only or other-branch blobs, outside the tracked tree, so they
are not shipped). `python ci/policy_checks.py` continues to exit `0` over the
tracked tree ("OK (no credential formats detected)").

### Email-shaped and owner-identifying absolute paths (snapshot; locations only)

| Category | Count | Files |
| --- | --- | --- |
| Email-shaped strings | 8 matches across 5 files | `tests/test_g45_synthetic_gate_orchestrator.py`, `tests/test_meta_request_redaction.py`, `tests/test_model_observation_protocol_relative.py`, `tests/test_model_observation_urls.py`, `tests/test_self_direction_planner.py` |
| Owner-identifying absolute paths (`/home`, `/Users`, `/root`, `C:\Users`) | 0 | — |

The 8 email-shaped strings all use **placeholder domains** (`example.invalid`,
`host.example`, `preview.example`) in test fixtures, **0 personal** mailboxes.
No owner-identifying absolute host path appears in any tracked file.

### Decisions records + artifacts subset (snapshot)

| Category | Hits | Locations |
| --- | --- | --- |
| Email-shaped strings | 0 | — |
| Owner-identifying absolute paths (`/home`, `/Users`, `/root`, `C:\Users`) | 0 | — |
| Credential shapes (any `ci/policy_checks.py` category) | 0 | — |

The 55 tracked files under `decisions/` (records D-0001 .. D-0026,
`decisions/artifacts/*/*.json`, `DECISIONS.md`, `LEDGER.md`, `ERRATA.md`) are
**clean: 0 emails / 0 owner-identifying absolute paths / 0 credential hits**.

### Escalation statement (Option B snapshot)

**Nothing in the tracked snapshot requires owner escalation before public
release.** Credential categories are 0; the 8 email-shaped matches are
placeholder-domain test fixtures (0 personal); owner-identifying absolute paths
are 0; and the `decisions/` subset is 0/0/0. The `ZestfulPulse` org handle is
the intentional public identity and is allowed. The clean snapshot carries no
git history, so the author/committer-identity review in the full-history section
above does not apply to it.

## Safety statement

No matched secret value was printed in this document, in the scan's standard
output, or in any log. The scanner records and reports only the category, the
blob/commit identifier, the file path, and the line number. The throwaway scan
script was run from outside the repository and deleted before this change was
committed; it is not part of the repository. See
[PROVENANCE.md](PROVENANCE.md) for the before/during build classification and
`.github/workflows/ci.yml` for the CI gate that enforces `ci/policy_checks.py`
on every push.
