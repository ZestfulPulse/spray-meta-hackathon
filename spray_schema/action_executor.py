"""Local, zero-spend action executor for SPRAY decision cycles (pure stdlib).

The decision-cycle runner used to take the ``actual_outcome`` straight from the
model's own plan (``plan["actual_outcome"]``) and wrote a tiny JSON that merely
DESCRIBED the plan. That made outcomes model-asserted and unverifiable: the
claimed artifacts did not exist and counts such as ``entries_summarized: 32``
could not be checked.

This module replaces that with REAL, measured outcomes. It performs a small,
bounded set of concrete OFFLINE analyses against SPRAY's own repository tree and
COMPUTES the outcome itself:

* ``inventory``   - file-tree inventory with counts by file extension.
* ``markers``     - TODO / FIXME / HACK / XXX marker scan with per-file counts.
* ``stats``       - line / byte statistics across the tree.
* ``duplicates``  - duplicate-filename groups and recurring multi-line blocks.
* ``docs_coverage`` - README presence + docstring coverage across the Python
  tree (parsed with the stdlib ``ast`` module).
* ``import_graph`` - internal import dependency graph of the Python tree
  (nodes/edges/fan-in/fan-out, parsed with the stdlib ``ast`` module).

The executor is deterministic, performs NO network, NO shell-out and NO
publishing, and may write ONLY under ``decisions/artifacts/D-NNNN/`` (it raises
if asked to write anywhere else). It maps the model's free-text chosen candidate
to one of the supported analyses deterministically; if the choice is unmappable
or unsafe it FALLS BACK to the safe default (``inventory``) and records that
fallback truthfully in the measured result (never fabricated).

For every produced artifact the executor returns the measured outcome: the real
repo-relative artifact path, the real byte size, the real ``sha256`` (hex digest
of the bytes actually written to disk), the real analysis counts, and the real
wall-clock ``duration_seconds`` (``time.monotonic`` around the analysis).

All free-text that is derived from the model (the chosen-action string, the
analysis name mapped from it) is passed through ``model_observation.redact``
before it is persisted; the measurements themselves are local and carry no
secrets.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass
import hashlib
import json
import os
import re
import time
from pathlib import Path

from .model_observation import redact

# The analysis used when the model's chosen action cannot be mapped to a known,
# safe analysis. It is the cheapest and most general: a file-tree inventory.
DEFAULT_ANALYSIS = "inventory"

# The supported analyses. Each maps to a private ``_analyze_*`` function below.
SUPPORTED_ANALYSES: tuple[str, ...] = (
    "inventory", "markers", "stats",
    "duplicates", "docs_coverage", "import_graph",
)

# Directory (relative to the decisions dir) under which per-decision artifacts
# live. Mirrors decision_cycle._ARTIFACTS_DIRNAME.
_ARTIFACTS_DIRNAME = "artifacts"

# Markers scanned by the ``markers`` analysis. Order is fixed for determinism.
_MARKER_TOKENS: tuple[str, ...] = ("TODO", "FIXME", "HACK", "XXX")

# Directory names never walked by an analysis (version control / caches). This
# keeps the inventory deterministic and avoids scanning generated junk.
_SKIP_DIRS: frozenset[str] = frozenset(
    {".git", ".agents", "__pycache__", ".mypy_cache", ".pytest_cache"}
)

# File extensions treated as binary and skipped by the text-reading analyses
# (markers / stats). The inventory still counts them by extension.
_BINARY_EXTENSIONS: frozenset[str] = frozenset(
    {
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
        ".pdf", ".zip", ".gz", ".tar", ".tgz", ".bz2", ".xz", ".7z",
        ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin",
        ".woff", ".woff2", ".ttf", ".eot", ".jar", ".class",
        ".mp3", ".mp4", ".mov", ".avi", ".wav", ".ogg",
    }
)


@dataclass(frozen=True)
class MeasuredOutcome:
    """The REAL, measured outcome of one executed analysis (no secrets)."""

    analysis: str
    mapped_from: str
    fallback: bool
    artifact_path: str
    artifact_sha256: str
    artifact_bytes: int
    counts: dict
    duration_seconds: float

    def to_dict(self) -> dict:
        return {
            "analysis": self.analysis,
            "mapped_from": self.mapped_from,
            "fallback": self.fallback,
            "artifact_path": self.artifact_path,
            "artifact_sha256": self.artifact_sha256,
            "artifact_bytes": self.artifact_bytes,
            "counts": self.counts,
            "duration_seconds": self.duration_seconds,
        }


# --------------------------------------------------------------------------- #
# Deterministic mapping: model free-text -> supported analysis
# --------------------------------------------------------------------------- #
# Keyword -> analysis. Checked in a fixed order so the mapping is deterministic.
# Each keyword is matched on WORD BOUNDARIES (see ``_keyword_matches``), so
# singular and plural forms are listed explicitly rather than relying on a
# substring match (``line`` no longer matches inside "offline" OR "lines").
_KEYWORD_MAP: tuple[tuple[str, str], ...] = (
    ("todo", "markers"),
    ("fixme", "markers"),
    ("hack", "markers"),
    ("marker", "markers"),
    ("markers", "markers"),
    # R2-S7 duplicate-filename / near-duplicate-block triage. These are the
    # words the planner actually proposes for a duplicate triage (D-0008 chose
    # "duplicate-triage ... duplicate filenames and similar blocks"). Matched on
    # word boundaries so "duplicates_triage" (a '_'-joined token) does not also
    # trip the markers ``triage`` path (which was removed anyway).
    ("duplicate", "duplicates"),
    ("duplicates", "duplicates"),
    ("dedupe", "duplicates"),
    ("deduplicate", "duplicates"),
    ("near-duplicate", "duplicates"),
    ("near duplicate", "duplicates"),
    # R2-S7 docs-coverage audit. D-0009 chose "offline_docs_coverage_audit ...
    # README, docstrings". The '_'-joined token "docs_coverage" has no internal
    # boundary, so the "docs coverage" phrase is listed with both separators.
    ("docs-coverage", "docs_coverage"),
    ("docs coverage", "docs_coverage"),
    ("documentation coverage", "docs_coverage"),
    ("docstring", "docs_coverage"),
    ("docstrings", "docs_coverage"),
    ("readme", "docs_coverage"),
    # R2-S7 import / dependency graph. D-0009's rejected "dependency import
    # graph" and the planner's "dependency-graph" / "import graph" phrasings.
    ("import graph", "import_graph"),
    ("import-graph", "import_graph"),
    ("dependency graph", "import_graph"),
    ("dependency-graph", "import_graph"),
    ("dependency", "import_graph"),
    ("dependencies", "import_graph"),
    # NOTE: a bare "triage" is deliberately NOT a markers keyword. It is
    # ambiguous (a "duplicate-filename triage" is unrelated to a TODO/FIXME
    # scan), and mapping it to markers misroutes intent while recording a
    # confident (non-fallback) choice. Marker intent must be signalled by a
    # marker-specific word (todo/fixme/hack/xxx/marker). An unmappable triage
    # correctly falls back to inventory with fallback=true.
    ("inventory", "inventory"),
    ("extension", "inventory"),
    ("extensions", "inventory"),
    ("file tree", "inventory"),
    ("file-tree", "inventory"),
    ("filetree", "inventory"),
    ("catalog", "inventory"),
    ("line", "stats"),
    ("lines", "stats"),
    ("byte", "stats"),
    ("bytes", "stats"),
    ("size", "stats"),
    ("sizes", "stats"),
    ("stat", "stats"),
    ("stats", "stats"),
    ("statistics", "stats"),
    ("count of lines", "stats"),
)


def _keyword_matches(keyword: str, lowered: str) -> bool:
    """True iff ``keyword`` appears in ``lowered`` on WORD BOUNDARIES.

    Matching on word boundaries (``\\b``) is what prevents an incidental
    substring inside an unrelated word from triggering a mapping: "offline"
    must NOT match the ``line`` keyword, and "duplicates_triage" must NOT match
    ``triage`` (``_`` is a word character, so there is no boundary before
    "triage"). Multi-word keywords like "file tree" are matched as a phrase
    with each token on its own boundary and flexible inter-token whitespace.
    """
    tokens = keyword.split()
    pattern = r"\b" + r"\s+".join(re.escape(tok) for tok in tokens) + r"\b"
    return re.search(pattern, lowered) is not None


def map_action_to_analysis(chosen_text: str) -> tuple[str, bool]:
    """Map the model's free-text choice to a supported analysis name.

    Returns ``(analysis_name, fallback)``. The match is a deterministic,
    case-insensitive keyword scan in a fixed order, but each keyword must match
    on WORD BOUNDARIES (see ``_keyword_matches``) so an incidental substring
    inside an unrelated word (e.g. "line" inside "off**line**") never drives the
    selection.

    ``fallback`` is True when the text could not be FAITHFULLY mapped to a known
    analysis and the safe default (``inventory``) was used. A True fallback is
    the record's honest signal that the executed analysis diverged from the
    model's stated intent; a False fallback means a real, boundary-anchored
    keyword match. When in doubt we prefer flagging the fallback over presenting
    an unrelated analysis as a confident, intended choice.
    """
    if not isinstance(chosen_text, str):
        return DEFAULT_ANALYSIS, True
    lowered = chosen_text.lower()
    for keyword, analysis in _KEYWORD_MAP:
        if _keyword_matches(keyword, lowered):
            return analysis, False
    return DEFAULT_ANALYSIS, True


def _chosen_text_from_plan(plan) -> str:
    """Extract the model's free-text chosen candidate from a plan dict."""
    if type(plan) is not dict:
        return ""
    for key in ("chosen_action", "name", "description"):
        value = plan.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


# --------------------------------------------------------------------------- #
# The offline analyses. Each walks ``repo_root`` (staying inside it) and returns
# a structured ``counts`` dict with the real measurements.
# --------------------------------------------------------------------------- #
def _iter_files(repo_root: Path, exclude_dirs=None):
    """Yield every non-skipped regular file under ``repo_root`` (deterministic).

    Directories named in ``_SKIP_DIRS`` are pruned by basename. Additionally any
    directory whose resolved path is in ``exclude_dirs`` (a set of resolved
    ``Path`` objects) is pruned by exact path: this is how the decision-cycle's
    OWN artifacts output dir (``decisions/artifacts/``) is excluded so a cycle
    never measures the artifacts written by its predecessors, keeping counts
    reproducible. The walk is sorted so traversal order is stable across runs.
    """
    excluded = exclude_dirs or frozenset()
    for dirpath, dirnames, filenames in os.walk(repo_root):
        kept = []
        for d in sorted(dirnames):
            if d in _SKIP_DIRS:
                continue
            child = Path(dirpath) / d
            try:
                resolved = child.resolve()
            except OSError:
                resolved = child
            if resolved in excluded:
                continue
            kept.append(d)
        dirnames[:] = kept
        for name in sorted(filenames):
            path = Path(dirpath) / name
            if path.is_file() and not path.is_symlink():
                yield path


def _extension_of(path: Path) -> str:
    """The lowercase file extension including the dot, or '<none>'."""
    suffix = path.suffix.lower()
    return suffix if suffix else "<none>"


def _analyze_inventory(repo_root: Path, exclude_dirs=None) -> dict:
    """File-tree inventory: total files and a count per file extension."""
    by_extension: dict[str, int] = {}
    total = 0
    for path in _iter_files(repo_root, exclude_dirs):
        total += 1
        ext = _extension_of(path)
        by_extension[ext] = by_extension.get(ext, 0) + 1
    return {
        "total_files": total,
        "by_extension": dict(sorted(by_extension.items())),
        "distinct_extensions": len(by_extension),
    }


def _analyze_markers(repo_root: Path, exclude_dirs=None) -> dict:
    """TODO/FIXME/HACK/XXX marker scan with per-file and per-token counts."""
    per_token: dict[str, int] = {token: 0 for token in _MARKER_TOKENS}
    per_file: dict[str, int] = {}
    total = 0
    files_scanned = 0
    for path in _iter_files(repo_root, exclude_dirs):
        if path.suffix.lower() in _BINARY_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        files_scanned += 1
        file_hits = 0
        for token in _MARKER_TOKENS:
            hits = text.count(token)
            if hits:
                per_token[token] += hits
                file_hits += hits
        if file_hits:
            rel = path.relative_to(repo_root).as_posix()
            per_file[rel] = file_hits
            total += file_hits
    return {
        "total_markers": total,
        "by_token": per_token,
        "by_file": dict(sorted(per_file.items())),
        "files_scanned": files_scanned,
        "files_with_markers": len(per_file),
    }


def _analyze_stats(repo_root: Path, exclude_dirs=None) -> dict:
    """Line / byte statistics across the (text) tree."""
    total_bytes = 0
    total_lines = 0
    files_scanned = 0
    for path in _iter_files(repo_root, exclude_dirs):
        try:
            data = path.read_bytes()
        except OSError:
            continue
        total_bytes += len(data)
        if path.suffix.lower() in _BINARY_EXTENSIONS:
            # Count bytes for binary files but not lines.
            continue
        files_scanned += 1
        total_lines += data.count(b"\n")
    return {
        "total_bytes": total_bytes,
        "total_lines": total_lines,
        "files_scanned": files_scanned,
    }


def _normalize_block_line(line: str) -> str:
    """Normalize one source line for near-duplicate block comparison.

    Trailing whitespace is stripped and internal runs of whitespace collapse to
    a single space so cosmetic indentation/spacing does not hide a real repeat.
    """
    return " ".join(line.split())


# A recurring block is this many consecutive non-blank normalized lines. Bounded
# and fixed so the ``duplicates`` analysis stays deterministic and cheap.
_DUPLICATE_BLOCK_SIZE = 3


def _analyze_duplicates(repo_root: Path, exclude_dirs=None) -> dict:
    """Duplicate-filename groups + recurring multi-line blocks (deterministic).

    Reports:

    * ``duplicate_basename_groups`` - count of basenames shared by >= 2 files.
    * ``files_in_duplicate_groups`` - total files that share a basename.
    * ``duplicate_basenames`` - the sorted basenames involved (bounded list).
    * ``duplicate_block_count`` - count of distinct normalized
      ``_DUPLICATE_BLOCK_SIZE``-line blocks that recur across text files.
    * ``headline`` - a deterministic headline integer (duplicate basename
      groups), so the decision cycle has a single checkable count.
    """
    by_basename: dict[str, list[str]] = {}
    block_counts: dict[str, int] = {}
    for path in _iter_files(repo_root, exclude_dirs):
        rel = path.relative_to(repo_root).as_posix()
        by_basename.setdefault(path.name, []).append(rel)
        if path.suffix.lower() in _BINARY_EXTENSIONS:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = [_normalize_block_line(ln) for ln in text.splitlines()]
        non_blank = [ln for ln in lines if ln]
        for i in range(0, len(non_blank) - _DUPLICATE_BLOCK_SIZE + 1):
            block = "\n".join(non_blank[i:i + _DUPLICATE_BLOCK_SIZE])
            block_counts[block] = block_counts.get(block, 0) + 1

    duplicate_groups = {
        name: sorted(rels)
        for name, rels in by_basename.items() if len(rels) >= 2
    }
    files_in_groups = sum(len(rels) for rels in duplicate_groups.values())
    duplicate_block_count = sum(
        1 for count in block_counts.values() if count >= 2)
    return {
        "duplicate_basename_groups": len(duplicate_groups),
        "files_in_duplicate_groups": files_in_groups,
        "duplicate_basenames": sorted(duplicate_groups),
        "duplicate_block_count": duplicate_block_count,
        "block_size": _DUPLICATE_BLOCK_SIZE,
        "headline": len(duplicate_groups),
    }


def _iter_python_files(repo_root: Path, exclude_dirs=None):
    """Yield (relative_posix_path, source_text) for every readable .py file."""
    for path in _iter_files(repo_root, exclude_dirs):
        if path.suffix.lower() != ".py":
            continue
        try:
            source = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        yield path.relative_to(repo_root).as_posix(), source


def _analyze_docs_coverage(repo_root: Path, exclude_dirs=None) -> dict:
    """README presence + docstring coverage across the Python tree (via ast).

    Counts every module, class, and function/async-function defined in the
    Python files and whether each carries a docstring (``ast.get_docstring``).
    Reports:

    * ``readme_present`` - True iff any ``README.md`` exists under the tree.
    * ``python_objects_total`` - modules + classes + functions considered.
    * ``python_objects_with_docstring`` - how many of those carry a docstring.
    * ``documented`` / ``undocumented`` - the two partition counts.
    * ``docstring_coverage_ratio`` - documented / total (0.0 when total is 0).
    * ``headline`` - a deterministic headline integer (documented objects).
    """
    readme_present = False
    total = 0
    documented = 0
    for rel, source in _iter_python_files(repo_root, exclude_dirs):
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        # The module itself is one documentable object.
        total += 1
        if ast.get_docstring(tree):
            documented += 1
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                                 ast.ClassDef)):
                total += 1
                if ast.get_docstring(node):
                    documented += 1
    for path in _iter_files(repo_root, exclude_dirs):
        if path.name == "README.md":
            readme_present = True
            break
    undocumented = total - documented
    ratio = (documented / total) if total else 0.0
    return {
        "readme_present": readme_present,
        "python_objects_total": total,
        "python_objects_with_docstring": documented,
        "documented": documented,
        "undocumented": undocumented,
        "docstring_coverage_ratio": round(ratio, 6),
        "headline": documented,
    }


def _module_name_for(rel_path: str) -> str:
    """Convert a relative .py path to a dotted module name (no .py suffix)."""
    without_ext = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    parts = [p for p in without_ext.split("/") if p]
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _analyze_import_graph(repo_root: Path, exclude_dirs=None) -> dict:
    """Internal import dependency graph of the Python tree (via ast).

    Each .py file is a node (its dotted module name). An edge A -> B is recorded
    when module A imports a module B that is ALSO a node in this tree (internal
    dependency); external imports are ignored for edges but counted. Reports:

    * ``modules`` - node count.
    * ``edges`` - internal edge count (deduplicated, sorted).
    * ``external_imports`` - count of distinct imported names not in the tree.
    * ``fan_in`` / ``fan_out`` - per-module in/out degree (sorted dicts).
    * ``headline`` - a deterministic headline integer (internal edge count).
    """
    node_names: set[str] = set()
    file_modules: list[tuple[str, str]] = []  # (module_name, source)
    for rel, source in _iter_python_files(repo_root, exclude_dirs):
        module = _module_name_for(rel)
        if module:
            node_names.add(module)
            file_modules.append((module, source))

    def _resolves_to_node(name: str):
        """Return the node a dotted import name maps to, or None (external)."""
        if name in node_names:
            return name
        # A dotted import like "pkg.mod.func" may target node "pkg.mod".
        parts = name.split(".")
        for i in range(len(parts), 0, -1):
            prefix = ".".join(parts[:i])
            if prefix in node_names:
                return prefix
        return None

    edges: set[tuple[str, str]] = set()
    external: set[str] = set()
    for module, source in file_modules:
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                # Relative imports (level > 0) are internal by construction;
                # resolve against this module's package.
                if node.level and node.module is not None:
                    pkg = module.rsplit(".", node.level)[0] if module else ""
                    targets = [pkg + "." + node.module if pkg else node.module]
                elif node.module is not None:
                    targets = [node.module]
            for target in targets:
                resolved = _resolves_to_node(target)
                if resolved is not None and resolved != module:
                    edges.add((module, resolved))
                elif resolved is None:
                    external.add(target)

    fan_out: dict[str, int] = {}
    fan_in: dict[str, int] = {}
    for src, dst in edges:
        fan_out[src] = fan_out.get(src, 0) + 1
        fan_in[dst] = fan_in.get(dst, 0) + 1
    return {
        "modules": len(node_names),
        "edges": len(edges),
        "edge_list": sorted(src + " -> " + dst for src, dst in edges),
        "external_imports": len(external),
        "fan_in": dict(sorted(fan_in.items())),
        "fan_out": dict(sorted(fan_out.items())),
        "headline": len(edges),
    }


_ANALYSES = {
    "inventory": _analyze_inventory,
    "markers": _analyze_markers,
    "stats": _analyze_stats,
    "duplicates": _analyze_duplicates,
    "docs_coverage": _analyze_docs_coverage,
    "import_graph": _analyze_import_graph,
}


# --------------------------------------------------------------------------- #
# R2-S8 metric accessor + findings (deterministic, offline, no secrets)
# --------------------------------------------------------------------------- #
# The recognized headline metric keys, one per analysis, under the EXACT names
# the analyses already write into their ``counts`` dict (verified above:
# inventory->total_files, markers->total_markers, stats->total_lines AND
# total_bytes, duplicates->duplicate_basename_groups, docs_coverage->documented,
# import_graph->edges). A caller (the decision cycle) names ONE of these as the
# metric a falsifiable prediction is tied to; ``metric_value`` reads the true,
# measured integer back out of the counts dict for the numeric-error comparison.
RECOGNIZED_METRIC_KEYS: frozenset[str] = frozenset({
    "total_files",
    "total_markers",
    "total_lines",
    "total_bytes",
    "duplicate_basename_groups",
    "documented",
    "edges",
})


def metric_value(counts, metric_key):
    """Return the measured integer for ``metric_key`` in ``counts``, or None.

    This is a pure, deterministic accessor over the measurements the analyses
    already compute: it does NOT recompute or change anything. It returns the
    integer value only when ``metric_key`` is a recognized headline key AND the
    value present under that key in ``counts`` is a real ``int`` (bools are
    rejected: ``bool`` is a subclass of ``int``). Any other case returns None
    (fail closed), so an unrecognized key, a missing key, or a non-int value
    never yields a bogus number for the numeric-error comparison.
    """
    if metric_key not in RECOGNIZED_METRIC_KEYS:
        return None
    if type(counts) is not dict:
        return None
    value = counts.get(metric_key)
    if type(value) is int:  # rejects bool (True/False) by exact-type check
        return value
    return None


# Upper bound on how many findings any single analysis surfaces, so the list is
# always short and bounded regardless of the measured tree.
_MAX_FINDINGS = 8


def findings_from_counts(analysis, counts):
    """Return a short, bounded list of plain-string findings from ``counts``.

    Findings are derived ONLY from the measured integer counts an analysis
    already produced (never from file contents, paths, or any model text), so
    they are deterministic, carry no secrets, and are safe to surface to the
    planner. Each finding is a compact ``key=value`` fact that signals a
    condition worth a follow-up (e.g. duplicate basenames exist, objects are
    undocumented, external imports are present). The list is empty-safe: when a
    measurement is zero / absent the corresponding finding is simply omitted, so
    an analysis with nothing notable returns ``[]``.
    """
    findings: list[str] = []
    if type(counts) is not dict:
        return findings

    def _pos_int(key):
        value = counts.get(key)
        return value if type(value) is int and value > 0 else None

    if analysis == "duplicates":
        groups = _pos_int("duplicate_basename_groups")
        if groups is not None:
            findings.append("duplicate_basename_groups=%d" % groups)
        blocks = _pos_int("duplicate_block_count")
        if blocks is not None:
            findings.append("duplicate_block_count=%d" % blocks)
    elif analysis == "docs_coverage":
        undocumented = _pos_int("undocumented")
        if undocumented is not None:
            findings.append("undocumented=%d" % undocumented)
    elif analysis == "markers":
        total = _pos_int("total_markers")
        if total is not None:
            findings.append("total_markers=%d" % total)
    elif analysis == "import_graph":
        edges = _pos_int("edges")
        if edges is not None:
            findings.append("edges=%d" % edges)
        external = _pos_int("external_imports")
        if external is not None:
            findings.append("external_imports=%d" % external)
    # inventory / stats surface no condition-style finding (nothing to follow
    # up on); they intentionally return an empty list.
    return findings[:_MAX_FINDINGS]


# --------------------------------------------------------------------------- #
# Atomic artifact write (mirrors decision_record._atomic_write_text) + guard
# --------------------------------------------------------------------------- #
def _resolve_artifact_dir(decisions_dir: Path, record_id: str,
                          artifacts_root) -> Path:
    """Return the per-decision artifact dir, refusing any path outside it.

    The artifact MUST live under ``<artifacts_root>/<record_id>/`` where
    ``artifacts_root`` defaults to ``<decisions_dir>/artifacts``. We resolve the
    final target and assert it is contained within that root; if not, we raise.
    """
    root = Path(artifacts_root) if artifacts_root is not None \
        else decisions_dir / _ARTIFACTS_DIRNAME
    target_dir = (root / record_id)
    # Containment check: the resolved target must be inside the resolved root.
    resolved_root = root.resolve()
    resolved_target = target_dir.resolve()
    if resolved_target != resolved_root and resolved_root not in resolved_target.parents:
        raise ValueError(
            "refusing to write artifact outside the artifacts root")
    return target_dir


def _atomic_write_bytes(path: Path, data: bytes) -> None:
    directory = path.parent
    directory.mkdir(parents=True, exist_ok=True)
    tmp = directory / (path.name + ".tmp")
    with open(tmp, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def execute_chosen_action(
    decisions_dir,
    record_id: str,
    plan,
    *,
    repo_root=None,
    artifacts_root=None,
) -> dict:
    """Run the chosen offline analysis, write its artifact, return measurements.

    * ``decisions_dir`` - the decisions directory (the artifact is written under
      ``<decisions_dir>/artifacts/<record_id>/`` unless ``artifacts_root`` is
      given explicitly).
    * ``record_id`` - the ``D-NNNN`` id for this decision (used as the artifact
      subdirectory name).
    * ``plan`` - the planner's plan dict; its ``chosen_action`` free-text is
      mapped to a supported analysis deterministically.
    * ``repo_root`` - the tree to analyze (defaults to the SPRAY repo root,
      inferred from this module's location). MUST stay inside itself.
    * ``artifacts_root`` - override for the artifacts base dir (tests use a temp
      dir); writing outside it is refused.

    Returns a plain dict (``MeasuredOutcome.to_dict()``) with the REAL measured
    fields. ``artifact_path`` is stored relative to ``decisions_dir`` so the
    validator can re-resolve it against a decisions root.
    """
    decisions_path = Path(decisions_dir)
    if repo_root is None:
        # The SPRAY repo root is the parent of the spray_schema package dir.
        repo_root_path = Path(__file__).resolve().parent.parent
    else:
        repo_root_path = Path(repo_root)
    if not repo_root_path.is_dir():
        raise ValueError("repo_root is not a directory")

    chosen_text = _chosen_text_from_plan(plan)
    analysis, fallback = map_action_to_analysis(chosen_text)
    if analysis not in _ANALYSES:
        # Defensive: an unknown analysis name also falls back safely.
        analysis, fallback = DEFAULT_ANALYSIS, True

    target_dir = _resolve_artifact_dir(decisions_path, record_id, artifacts_root)

    # Exclude the decision-cycle's OWN artifacts output dir from the walk so a
    # cycle never measures artifacts written by prior cycles (otherwise counts
    # drift as the repo accumulates its own output). Both the default artifacts
    # root (<decisions_dir>/artifacts) and an explicit artifacts_root are pruned
    # when they live inside the analyzed tree.
    exclude_dirs = set()
    for candidate in (
        decisions_path / _ARTIFACTS_DIRNAME,
        Path(artifacts_root) if artifacts_root is not None else None,
    ):
        if candidate is None:
            continue
        try:
            exclude_dirs.add(candidate.resolve())
        except OSError:
            pass

    # Measure real wall-clock duration around the analysis itself.
    start = time.monotonic()
    counts = _ANALYSES[analysis](repo_root_path, exclude_dirs)
    duration_seconds = max(0.0, time.monotonic() - start)

    # The artifact is a self-describing JSON of the measured analysis. All
    # model-derived free-text is S6-redacted; the measurements are local.
    artifact_body = {
        "analysis": analysis,
        "mapped_from": redact(chosen_text, 500),
        "fallback": fallback,
        "decision_id": record_id,
        "counts": counts,
    }
    text = json.dumps(
        artifact_body, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False) + "\n"
    data = text.encode("utf-8")

    artifact_path = target_dir / (analysis + ".json")
    _atomic_write_bytes(artifact_path, data)

    # Compute sha256 of the bytes actually on disk (read back, not the in-memory
    # buffer) so the recorded digest provably matches the persisted file.
    written = artifact_path.read_bytes()
    sha256 = hashlib.sha256(written).hexdigest()

    # Store the artifact path relative to the decisions dir so the validator can
    # re-resolve it from a decisions root. Fall back to the absolute path if the
    # artifact lives outside the decisions dir (custom artifacts_root).
    try:
        rel_path = artifact_path.resolve().relative_to(
            decisions_path.resolve()).as_posix()
    except ValueError:
        rel_path = str(artifact_path)

    outcome = MeasuredOutcome(
        analysis=analysis,
        mapped_from=redact(chosen_text, 500),
        fallback=fallback,
        artifact_path=rel_path,
        artifact_sha256=sha256,
        artifact_bytes=len(written),
        counts=counts,
        duration_seconds=duration_seconds,
    )
    return outcome.to_dict()
