"""Stdlib unittest suite for the offline docs / Markdown link validator.

Pure Python 3.11 standard library: no pytest, no third-party dependencies,
no network, no AWS. Run with:

    python3 -m unittest discover -s tests -p 'test_*.py' -v

Focus: a draft-only link authored from a subdirectory file (which joins to a
path containing ``..``) must be normalized before the allowlist lookup so it is
treated as a non-failing draft-only skip, not a hard failure.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ci.validate_docs import validate  # noqa: E402


class DraftOnlyLinkFromSubdirectoryTests(unittest.TestCase):
    def _build_repo(self, root: Path, subdir_link: str) -> None:
        # Mandatory doc so the mandatory-docs check passes.
        (root / "README.md").write_text("# Root\n", encoding="utf-8")
        # A file inside a subdirectory that links to a draft-only doc using a
        # relative path with '..'. The joined path is 'ci/../docs/...'.
        ci_dir = root / "ci"
        ci_dir.mkdir()
        (ci_dir / "README.md").write_text(
            f"See [architecture]({subdir_link}).\n", encoding="utf-8"
        )

    def test_draft_only_link_from_subdir_is_skipped_not_failed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            # docs/ARCHITECTURE.md is draft-only (does NOT exist on disk).
            self._build_repo(root, "../docs/ARCHITECTURE.md")

            failures, notes = validate(root)

            self.assertEqual(
                failures,
                [],
                msg=f"draft-only link should not fail, got failures: {failures}",
            )
            self.assertTrue(
                any("skipped (draft-only" in note for note in notes),
                msg=f"expected a draft-only skip note, got notes: {notes}",
            )

    def test_non_allowlisted_link_from_subdir_still_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            # A broken link NOT in the draft-only allowlist must still fail.
            self._build_repo(root, "../docs/NOPE_MISSING.md")

            failures, notes = validate(root)

            self.assertTrue(
                any("broken local link" in f for f in failures),
                msg=f"expected a broken-link failure, got failures: {failures}",
            )

    def test_generated_virtualenv_markdown_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root / "README.md").write_text("# Root\n", encoding="utf-8")
            generated = root / ".venv" / "lib" / "site-packages" / "package"
            generated.mkdir(parents=True)
            (generated / "SKILL.md").write_text(
                "See [missing](./not-shipped.md).\n", encoding="utf-8"
            )

            failures, notes = validate(root)

            self.assertEqual(failures, [])
            self.assertEqual(notes, [])


if __name__ == "__main__":
    unittest.main()
