#!/usr/bin/env python3
"""Offline Markdown/docs validator for the SPRAY repository.

Two checks, both fully offline (no http(s) fetches):

1. Mandatory docs: a configurable list of documents that MUST exist right now.
   Deliberately minimal so CI can pass BEFORE the foundation PR merges: only
   README.md is mandatory. Draft-only foundation files are NOT required.

2. Relative local Markdown links: for every ``[text](path)`` link that points
   to a local path, verify the target exists. A missing target is a FAILURE
   only if it is NOT in the draft-only allowlist of not-yet-merged foundation
   files. Links to allowlisted files are reported as non-failing
   "skipped (draft-only, pending foundation merge)" notes. External http(s)
   links and anchors are not fetched or validated.

Exit code: 0 when all mandatory docs exist and no non-allowlisted broken local
links remain; non-zero otherwise.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

# --- Configuration (edit these lists as the repo grows) ----------------------

# Docs that MUST exist right now. Keep minimal: do NOT require draft-only
# foundation files that live only on the unmerged branch.
MANDATORY_DOCS = [
    "README.md",
]

# Draft-only / not-yet-merged foundation files. A broken local link to any of
# these is reported as a non-failing informational note instead of a failure.
DRAFT_ONLY_ALLOWLIST = {
    "docs/SPRAY_CONSTITUTION.md",
    "docs/ARCHITECTURE.md",
    "docs/BIRTH_CHECKLIST.md",
    "docs/KIRO_HANDOFF_PROOF.md",
    ".kiro/steering/product.md",
}

# -----------------------------------------------------------------------------

# Markdown inline link: [text](target)
_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")


def _is_external(target: str) -> bool:
    lowered = target.strip().lower()
    return (
        lowered.startswith("http://")
        or lowered.startswith("https://")
        or lowered.startswith("mailto:")
        or lowered.startswith("#")
    )


def _normalize(root: Path, target: Path) -> str:
    """Return a repo-relative, posix-style path string for comparison.

    The target is first collapsed (``..``/``.`` segments resolved) so that a
    link authored from a subdirectory (e.g. ``ci/README.md`` -> ``../docs/
    ARCHITECTURE.md``, which joins to ``ci/../docs/ARCHITECTURE.md``) yields the
    same repo-relative key as the draft-only allowlist (``docs/
    ARCHITECTURE.md``). Without this collapse the allowlist lookup would miss
    and a draft-only link from a subdirectory would be reported as a hard
    failure instead of a non-failing draft-only skip. ``resolve()`` is used so
    the comparison key matches the ``resolve()``-based existence check.
    """
    resolved = Path(os.path.normpath(target)).resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        return Path(os.path.normpath(target)).as_posix()


def find_markdown_files(root: Path) -> list[Path]:
    generated = {".git", ".venv"}
    return sorted(p for p in root.rglob("*.md")
                  if not generated.intersection(p.relative_to(root).parts))


def validate(root: Path) -> tuple[list[str], list[str]]:
    """Return (failures, notes)."""
    failures: list[str] = []
    notes: list[str] = []

    # 1. Mandatory docs.
    for doc in MANDATORY_DOCS:
        if not (root / doc).is_file():
            failures.append(f"mandatory doc missing: {doc}")

    # 2. Relative local links.
    for md in find_markdown_files(root):
        try:
            text = md.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            failures.append(f"could not read {md}: {exc}")
            continue

        md_rel = md.relative_to(root).as_posix()
        for lineno, line in enumerate(text.splitlines(), start=1):
            for match in _LINK_RE.finditer(line):
                target = match.group(1).strip()
                if not target or _is_external(target):
                    continue
                # Strip optional title and anchor: path "title" / path#anchor
                target = target.split()[0]
                target = target.split("#", 1)[0]
                if not target:
                    continue

                # Resolve relative to the markdown file's directory.
                resolved = (md.parent / target).resolve()
                if resolved.is_file() or resolved.is_dir():
                    continue

                rel_target = _normalize(root, (md.parent / target))
                if rel_target in DRAFT_ONLY_ALLOWLIST:
                    notes.append(
                        f"{md_rel}:{lineno}: link to '{target}' "
                        "skipped (draft-only, pending foundation merge)"
                    )
                else:
                    failures.append(
                        f"{md_rel}:{lineno}: broken local link to '{target}'"
                    )

    return failures, notes


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parent.parent
    failures, notes = validate(root)

    for note in notes:
        print(f"validate_docs: NOTE  {note}")

    if failures:
        print("validate_docs: FAILURES:")
        for failure in failures:
            print(f"  {failure}")
        print(f"validate_docs: FAILED ({len(failures)} issue(s))")
        return 1

    print("validate_docs: OK (mandatory docs present, no broken local links)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
