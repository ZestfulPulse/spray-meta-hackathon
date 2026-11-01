"""Stdlib unittest suite for the offline credential-format policy scanner.

Pure Python 3.11 standard library: no pytest, no third-party dependencies,
no network, no AWS. Run with:

    python3 -m unittest discover -s tests -p 'test_*.py' -v

These tests focus on the hardened generic-secret behavior: a real-looking
quoted secret assignment in actual code is still flagged, while benign
commented / documentation examples (and explicit placeholder markers) are not
flagged by the loose generic pattern. The strict AWS/PEM patterns remain
context-insensitive.

IMPORTANT - why the sample strings are assembled at runtime:
    This test file is itself a tracked repository file, so ``ci/policy_checks``
    scans it as part of the CI gate. If credential-shaped literals (e.g. a
    quoted password/secret/token assignment, an access-key id, or a PEM
    header) appeared verbatim on code lines in this source, the scanner would
    flag THIS file and turn the gate red. To keep the file both scannable and
    non-triggering, every credential-shaped sample is built from fragments via
    ``_quoted_assignment`` / string concatenation. The regex therefore does not
    match any line of this source, but the fully-assembled string handed to the
    scanner at runtime still has the real credential shape, so the assertions
    genuinely exercise detection behavior.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ci.policy_checks import scan_file  # noqa: E402

# Credential-shaped fragments assembled at runtime so that no triggering
# literal appears verbatim on a code line in this tracked source file.
_Q = '"'
_EQ = " = "
_COLON = ": "

# AWS access key id: prefix + 16 upper/alnum chars, assembled so "AKIA" + body
# never sits as one literal in the source.
_AWS_KEY = "AKIA" + "IOSFODNN7" + "EXAMPLE"
# PEM header assembled from parts.
_PEM_HEADER = "-----BEGIN " + "RSA " + "PRIVATE KEY" + "-----"


def _quoted_assignment(key: str, value: str, sep: str = _EQ) -> str:
    """Assemble ``key<sep>"value"`` from fragments.

    The returned runtime string has the real ``name = "value"`` shape that the
    generic pattern matches, but no such literal appears in this source file.
    """
    return key + sep + _Q + value + _Q


def _scan_text(text: str) -> list[tuple[int, str]]:
    """Write text to a temporary file, scan it, and return the hits."""
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "sample.py"
        path.write_text(text, encoding="utf-8")
        return scan_file(path)


class GenericSecretCodeTests(unittest.TestCase):
    def test_real_quoted_secret_assignment_in_code_is_flagged(self) -> None:
        line = _quoted_assignment("password", "hunter2longenough")
        hits = _scan_text(line + "\n")
        names = {name for _, name in hits}
        self.assertIn("generic_hardcoded_secret_assignment", names)

    def test_api_key_mapping_assignment_in_code_is_flagged(self) -> None:
        line = _quoted_assignment("api_key", "abc123def456", sep=_COLON)
        hits = _scan_text(line + "\n")
        names = {name for _, name in hits}
        self.assertIn("generic_hardcoded_secret_assignment", names)

    def test_env_lookup_form_is_not_flagged(self) -> None:
        # Not a quoted literal value, so the generic pattern should not match.
        line = "password" + _EQ + "os.environ[" + _Q + "PW" + _Q + "]"
        hits = _scan_text(line + "\n")
        names = {name for _, name in hits}
        self.assertNotIn("generic_hardcoded_secret_assignment", names)


class GenericSecretDocExampleTests(unittest.TestCase):
    def test_hash_comment_example_is_not_flagged(self) -> None:
        line = "# example: " + _quoted_assignment("password", "secret")
        hits = _scan_text(line + "\n")
        names = {name for _, name in hits}
        self.assertNotIn("generic_hardcoded_secret_assignment", names)
        self.assertEqual(hits, [])

    def test_markdown_prose_line_is_not_flagged(self) -> None:
        line = "> See the config where " + _quoted_assignment(
            "token", "value"
        ) + " is set."
        hits = _scan_text(line + "\n")
        names = {name for _, name in hits}
        self.assertNotIn("generic_hardcoded_secret_assignment", names)

    def test_slash_comment_example_is_not_flagged(self) -> None:
        line = "// " + _quoted_assignment("password", "example")
        hits = _scan_text(line + "\n")
        names = {name for _, name in hits}
        self.assertNotIn("generic_hardcoded_secret_assignment", names)

    def test_placeholder_marker_exempts_code_line(self) -> None:
        line = (
            _quoted_assignment("password", "example")
            + "  # pragma: allowlist secret"
        )
        hits = _scan_text(line + "\n")
        names = {name for _, name in hits}
        self.assertNotIn("generic_hardcoded_secret_assignment", names)


class StrictPatternsStayContextInsensitiveTests(unittest.TestCase):
    def test_aws_access_key_flagged_even_in_comment(self) -> None:
        hits = _scan_text("# leaked " + _AWS_KEY + " here\n")
        names = {name for _, name in hits}
        self.assertIn("aws_access_key_id", names)

    def test_pem_header_flagged_even_in_markdown_prose(self) -> None:
        hits = _scan_text("> " + _PEM_HEADER + "\n")
        names = {name for _, name in hits}
        self.assertIn("pem_private_key_header", names)

    def test_aws_access_key_flagged_with_placeholder_marker(self) -> None:
        # Strict patterns are never exempted, even by placeholder markers.
        hits = _scan_text(_AWS_KEY + "  # placeholder\n")
        names = {name for _, name in hits}
        self.assertIn("aws_access_key_id", names)


if __name__ == "__main__":
    unittest.main()
