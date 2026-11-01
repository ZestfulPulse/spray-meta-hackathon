# SPRAY CI Quality Gate

This directory holds SPRAY's **offline** continuous-integration checks and the
GitHub Actions workflow that runs them. The workflow is defined in
[`../.github/workflows/ci.yml`](../.github/workflows/ci.yml) and runs on every
`pull_request` and on `push` to `main` and the feature branch. It uses only the
Python standard library on GitHub-hosted runners: no third-party packages, no
network calls, no AWS, no deployment, and no auto-merge.

The three checks it runs, all from the repository root:

- [`policy_checks.py`](policy_checks.py) — credential-format scanner
- [`validate_docs.py`](validate_docs.py) — docs and local Markdown link validator
- `python3 -m unittest discover -s tests -p 'test_*.py' -v` — stdlib test suite

## What CI proves

1. **No known credential FORMATS are present in tracked files.** The policy
   scanner inspects tracked text files for known credential shapes (AWS access
   key IDs, AWS secret-key assignments, PEM private-key headers, and generic
   hard-coded secret assignments). This is a best-effort format scan, **not a
   guarantee that zero secrets exist**. When something matches, CI reports only
   the file path, line number, and pattern name; it never prints the matched
   value.

   The AWS access-key, AWS secret-key, and PEM private-key patterns are
   **strict** and scan every line regardless of context. The generic pattern
   for a quoted `password`, `secret`, `token`, or `api_key` assignment is
   looser, so to avoid false positives on documentation it is **not** applied
   to comment-only or
   Markdown-prose lines (lines beginning with `#`, `//`, `*`, `<!--`, or `>`),
   nor to any line carrying an explicit placeholder marker (`pragma: allowlist
   secret`, `notasecret`, `example-only`, or `placeholder`). This lets docs
   illustrate credential shapes without turning the gate red, while real
   hard-coded secret assignments in code are still flagged. The strict patterns
   are never exempted.
2. **Required docs exist and local Markdown links resolve.** The docs validator
   confirms the mandatory documents are present and that relative Markdown links
   point at real files. Draft-only foundation files that live only on the
   unmerged foundation branch are **skipped** (reported as non-failing notes)
   until foundation PR #1 merges, so the gate can stay green beforehand.
3. **The stdlib-only Python test suite passes.** This includes the event
   envelope schema sample test, which checks the required fields, the allowed
   verification statuses (`unverified`, `verified`, `rejected`), and that the
   fixtures contain no raw secrets.

## What CI does NOT prove

- It does **not** deploy anything.
- It does **not** touch AWS or any cloud resource.
- It does **not** verify runtime or economic behavior.
- It does **not** prove the absence of ALL secrets — only known credential
  FORMATS are detected.
- It does **not** enforce the SPRAY Constitution policy boundaries, because the
  foundation (constitution, architecture, and related docs) is still draft
  PR #1 and is not assumed to exist on `main`.
- It runs **entirely offline** with no network dependencies, so it cannot catch
  anything that would only surface with external services.

## The authoritative green run

Local runs in a sandbox are a convenience, not the source of truth. The
**authoritative green run is the GitHub Actions run on the pull request**. The
exact workflow run link must be recorded in the PR by the orchestrator or
reviewer so the evidence lives alongside the change. These checks are consistent
with the hard boundaries in the top-level [`../README.md`](../README.md): no
secrets committed, no credential exposure, no AWS deployment, and no auto-merge.
