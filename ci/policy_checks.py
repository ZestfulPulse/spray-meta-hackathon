#!/usr/bin/env python3
"""Offline credential-format policy scanner for the SPRAY repository.

Scans tracked repository text files for KNOWN credential FORMATS. This is a
best-effort guard against accidentally committed secrets, NOT a cryptographic
guarantee. It performs no network calls and needs no third-party packages.

CRITICAL SAFETY RULE: when a potential credential is found, this script prints
ONLY the file path, the line number, and the NAME of the matched pattern. It
MUST NEVER print the matched text / secret value itself.

Usage:
    python3 ci/policy_checks.py          # scan the current repo
    python3 ci/policy_checks.py path ... # scan explicit paths

The module is also importable so tests can reuse CREDENTIAL_PATTERNS and keep
the fixture-safety assertions in sync with the scanner.

Exit code: 0 if no hits, non-zero if any credential format is matched.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Each pattern is (name, compiled_regex). Patterns match credential FORMATS.
# They are deliberately shaped so that the fixtures / docs in this repo do NOT
# trigger them, while real credential shapes would.
#
# The AWS-key and PEM patterns are "strict": they match a credential FORMAT
# that is extremely unlikely to appear outside of a real credential, so they
# are applied to every line regardless of context (including comments / docs).
#
# The generic ``password/secret/token/api_key = "..."`` pattern is far looser
# and WILL match benign documentation / example strings. Because SPRAY is a
# credential-hygiene repo that will plausibly add docs illustrating credential
# shapes, this pattern is only applied to lines that look like actual code (see
# ``_is_comment_or_prose_line`` and ``_has_placeholder_allowance`` below). This
# keeps real hardcoded-secret assignments in code flagged while preventing
# benign comment / markdown examples from turning the gate red.
CREDENTIAL_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    (
        "aws_access_key_id",
        re.compile(r"(AKIA|ASIA)[A-Z0-9]{16}"),
    ),
    (
        "aws_secret_access_key_assignment",
        re.compile(
            r"(?i)aws_secret_access_key\s*[:=]\s*['\"]?[A-Za-z0-9/+]{40}['\"]?"
        ),
    ),
    (
        "pem_private_key_header",
        re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    ),
    (
        "generic_hardcoded_secret_assignment",
        re.compile(
            r"(?i)\b(password|passwd|secret|token|api[_-]?key)\b\s*[:=]\s*"
            r"['\"][^'\"\s]+['\"]"
        ),
    ),
]

# Pattern names that are "strict" format matches and are applied to every line
# regardless of context (comments, markdown prose, placeholder markers).
STRICT_PATTERN_NAMES = frozenset(
    {
        "aws_access_key_id",
        "aws_secret_access_key_assignment",
        "pem_private_key_header",
    }
)

# Explicit inline convention: a line that carries one of these markers is
# treated as an intentional example / placeholder and is exempt from the
# context-sensitive generic pattern (but NEVER from the strict AWS/PEM
# patterns). Keep this list small and obvious.
PLACEHOLDER_MARKERS = (
    "pragma: allowlist secret",
    "notasecret",
    "example-only",
    "placeholder",
)

# Leading tokens that indicate a comment-only / prose line in the file types
# this repo contains (Python, YAML, shell, Markdown). Markdown prose and
# comment lines routinely contain illustrative ``password = "..."`` snippets,
# which are documentation, not committed credentials.
_COMMENT_PREFIXES = ("#", "//", "*", "<!--", ">")


def _is_comment_or_prose_line(line: str) -> bool:
    """Return True for comment-only or Markdown-prose lines.

    These lines may legitimately contain illustrative credential-shaped text
    (documentation examples), so the loose generic pattern should not fire on
    them. The strict AWS/PEM patterns are unaffected and still scan every line.
    """
    stripped = line.lstrip()
    if not stripped:
        return False
    return stripped.startswith(_COMMENT_PREFIXES)


def _has_placeholder_allowance(line: str) -> bool:
    """Return True if the line opts out via an explicit placeholder marker."""
    lowered = line.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def _generic_pattern_applies(line: str) -> bool:
    """Whether the loose generic secret pattern should be evaluated on a line.

    Skipped for comment-only / Markdown-prose lines and for lines that carry an
    explicit placeholder marker, so benign documentation examples do not turn
    the gate red. Real credential-shaped assignments in actual code lines are
    still evaluated.
    """
    return not (
        _is_comment_or_prose_line(line) or _has_placeholder_allowance(line)
    )

# File extensions treated as binary and skipped.
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".pdf", ".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".7z",
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin",
    ".woff", ".woff2", ".ttf", ".eot", ".jar", ".class",
    ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg",
}


def _list_tracked_files(root: Path) -> list[Path]:
    """Enumerate files via git ls-files, falling back to a tree walk."""
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=str(root),
            capture_output=True,
            check=True,
            text=True,
        )
        names = [n for n in result.stdout.split("\0") if n]
        if names:
            return [root / n for n in names]
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        pass

    # Fallback: walk the tree, excluding the .git directory.
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if ".git" in path.parts:
            continue
        files.append(path)
    return files


def _is_probably_binary(path: Path) -> bool:
    if path.suffix.lower() in BINARY_EXTENSIONS:
        return True
    try:
        chunk = path.read_bytes()[:4096]
    except OSError:
        return True
    return b"\0" in chunk


def scan_file(path: Path) -> list[tuple[int, str]]:
    """Return a list of (line_number, pattern_name) hits for a single file.

    The matched secret text is intentionally NOT returned, so it can never be
    printed or logged.
    """
    hits: list[tuple[int, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return hits

    for lineno, line in enumerate(text.splitlines(), start=1):
        generic_applies = _generic_pattern_applies(line)
        for name, pattern in CREDENTIAL_PATTERNS:
            # Strict AWS/PEM patterns always scan every line. The loose generic
            # pattern is skipped on comment / prose / placeholder lines so that
            # benign documentation examples do not produce false positives.
            if name not in STRICT_PATTERN_NAMES and not generic_applies:
                continue
            if pattern.search(line):
                hits.append((lineno, name))
    return hits


def scan_paths(paths: list[Path]) -> list[tuple[Path, int, str]]:
    """Scan the given file paths and return (path, line_number, name) hits."""
    all_hits: list[tuple[Path, int, str]] = []
    for path in paths:
        if not path.is_file():
            continue
        if ".git" in path.parts:
            continue
        if _is_probably_binary(path):
            continue
        for lineno, name in scan_file(path):
            all_hits.append((path, lineno, name))
    return all_hits


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    root = Path(__file__).resolve().parent.parent

    if argv:
        paths = [Path(a) for a in argv]
    else:
        paths = _list_tracked_files(root)

    hits = scan_paths(paths)

    if hits:
        print("policy_checks: potential credential formats detected:")
        for path, lineno, name in hits:
            try:
                rel = path.relative_to(root)
            except ValueError:
                rel = path
            # NEVER print the matched text, only location + pattern name.
            print(f"  {rel}:{lineno}: matched pattern '{name}'")
        print(f"policy_checks: FAILED ({len(hits)} hit(s))")
        return 1

    print("policy_checks: OK (no credential formats detected)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
