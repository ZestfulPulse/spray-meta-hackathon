"""Offline tests for the R2-S6 local action executor (pure stdlib).

These prove the executor performs REAL analyses and computes measured outcomes
entirely offline (no network, no shell-out, no publishing). Every repo root and
decisions dir is an isolated ``tempfile.TemporaryDirectory`` so the real
``decisions/`` dir and the live repo tree are NEVER touched or depended on for
exact counts.
"""
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from spray_schema import action_executor
from spray_schema.action_executor import (
    DEFAULT_ANALYSIS,
    SUPPORTED_ANALYSES,
    execute_chosen_action,
    map_action_to_analysis,
)


def _seed_tree(root: Path):
    """Seed a known tree: 2 .py, 1 .md, 1 .txt; 3 TODO + 1 FIXME markers."""
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text(
        "def f():\n    pass  # TODO one\n# TODO two\n", encoding="utf-8")
    (root / "pkg" / "util.py").write_text(
        "x = 1  # FIXME later\n", encoding="utf-8")
    (root / "README.md").write_text("# Title\nTODO three\n", encoding="utf-8")
    (root / "notes.txt").write_text("plain\ntext\nfile\n", encoding="utf-8")
    # A skipped dir must NOT be counted.
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("TODO ignored\n", encoding="utf-8")


class MappingTests(unittest.TestCase):
    def test_keyword_mapping_is_deterministic(self):
        self.assertEqual(map_action_to_analysis("scan for TODO markers"),
                         ("markers", False))
        self.assertEqual(map_action_to_analysis("fixme triage pass"),
                         ("markers", False))
        self.assertEqual(map_action_to_analysis("file-tree inventory"),
                         ("inventory", False))
        self.assertEqual(map_action_to_analysis("count lines and bytes"),
                         ("stats", False))

    def test_unmappable_falls_back_to_default(self):
        analysis, fallback = map_action_to_analysis("pursue alpha opportunity")
        self.assertEqual(analysis, DEFAULT_ANALYSIS)
        self.assertTrue(fallback)

    def test_non_string_falls_back(self):
        self.assertEqual(map_action_to_analysis(None), (DEFAULT_ANALYSIS, True))

    def test_offline_does_not_map_to_stats_via_line_substring(self):
        """Regression (review issue 2): "offline" must NOT match the ``line``
        keyword. A docs-coverage audit that merely says "offline" has no
        faithful keyword, so it falls back to inventory (fallback True) rather
        than being routed to ``stats`` and recorded as a confident choice."""
        analysis, fallback = map_action_to_analysis(
            "docs_coverage audit: scan local repository files, fully offline")
        self.assertEqual(analysis, DEFAULT_ANALYSIS)
        self.assertTrue(fallback)
        self.assertNotEqual(analysis, "stats")

    def test_duplicates_triage_does_not_map_to_markers_via_triage(self):
        """Regression (review issue 2): "duplicates_triage" must NOT match the
        ``triage`` keyword (``_`` is a word char, so there is no boundary). A
        duplicate-filename triage is unmappable here and must flag fallback
        rather than be routed to the unrelated ``markers`` scan."""
        analysis, fallback = map_action_to_analysis(
            "Generate ./analysis/duplicates_triage.md via filename grouping")
        self.assertEqual(analysis, DEFAULT_ANALYSIS)
        self.assertTrue(fallback)
        self.assertNotEqual(analysis, "markers")

    def test_explicit_line_count_request_maps_to_stats_non_fallback(self):
        """An explicit, faithful "line count"/"byte size" request still maps to
        ``stats`` as a real (non-fallback) match on word boundaries."""
        for action in ("produce a line count report",
                       "measure byte size of each file",
                       "compute line and byte statistics"):
            with self.subTest(action=action):
                analysis, fallback = map_action_to_analysis(action)
                self.assertEqual(analysis, "stats")
                self.assertFalse(fallback)

    def test_marker_word_still_maps_to_markers(self):
        """A marker-specific word (fixme/todo/...) is still a faithful markers
        match on word boundaries, so the fix did not regress genuine markers."""
        self.assertEqual(map_action_to_analysis("fixme triage pass"),
                         ("markers", False))
        self.assertEqual(map_action_to_analysis("scan for TODO markers"),
                         ("markers", False))

    def test_bare_triage_does_not_map_to_markers(self):
        """Regression (review issue 2): a bare "triage" (even hyphenated, as in
        "duplicate-triage") is ambiguous and must NOT be routed to the markers
        scan as a confident choice; it falls back to inventory instead."""
        for action in ("run a general triage pass",
                       "large-file triage"):
            with self.subTest(action=action):
                analysis, fallback = map_action_to_analysis(action)
                self.assertEqual(analysis, DEFAULT_ANALYSIS)
                self.assertTrue(fallback)
                self.assertNotEqual(analysis, "markers")


class NewAnalysisMappingTests(unittest.TestCase):
    """R2-S7: the D-0008/D-0009-style chosen actions now map to real analyses
    with fallback=False (they no longer silently fall back to inventory)."""

    def test_d0008_duplicate_triage_maps_to_duplicates(self):
        analysis, fallback = map_action_to_analysis(
            "Create repo-local offline file .analysis/duplicate-triage.md by "
            "local-only scan for duplicate filenames and similar blocks")
        self.assertEqual(analysis, "duplicates")
        self.assertFalse(fallback)

    def test_d0009_docs_coverage_maps_to_docs_coverage(self):
        analysis, fallback = map_action_to_analysis(
            "Create repo-local file ./artifacts/offline_docs_coverage_audit.md "
            "via offline-only scan for README, docstrings and comments")
        self.assertEqual(analysis, "docs_coverage")
        self.assertFalse(fallback)

    def test_dependency_graph_maps_to_import_graph(self):
        for action in ("offline parse of Python imports to produce dependency graph",
                       "build an import graph of the package",
                       "produce a dependency-graph artifact"):
            with self.subTest(action=action):
                analysis, fallback = map_action_to_analysis(action)
                self.assertEqual(analysis, "import_graph")
                self.assertFalse(fallback)

    def test_new_keywords_are_word_boundary_matched(self):
        # "_"-joined tokens do not create a false boundary (docs_coverage as one
        # token is NOT the "docs coverage" phrase), so this stays a fallback.
        analysis, fallback = map_action_to_analysis(
            "run the docs_coverage step, fully offline")
        self.assertEqual(analysis, DEFAULT_ANALYSIS)
        self.assertTrue(fallback)


def _seed_new_analysis_tree(root: Path):
    """Seed a tree with KNOWN duplicate basenames, documented/undocumented
    python objects, a README, and known internal imports."""
    (root / "pkg").mkdir()
    (root / "pkg" / "__init__.py").write_text("", encoding="utf-8")
    # mod_a: documented module + documented function + undocumented function,
    # imports the sibling helper (an internal edge).
    (root / "pkg" / "mod_a.py").write_text(
        '"""Module A docstring."""\n'
        "from pkg import helper\n"
        "\n"
        "def documented():\n"
        '    """Has a docstring."""\n'
        "    return helper.value()\n"
        "\n"
        "def undocumented():\n"
        "    return 1\n",
        encoding="utf-8")
    # helper: undocumented module, one documented function.
    (root / "pkg" / "helper.py").write_text(
        "def value():\n"
        '    """Return a value."""\n'
        "    return 2\n",
        encoding="utf-8")
    # Two files sharing the basename "config.py" -> one duplicate group.
    (root / "config.py").write_text("SETTING = 1\n", encoding="utf-8")
    (root / "pkg" / "config.py").write_text("SETTING = 1\n", encoding="utf-8")
    (root / "README.md").write_text("# Project\nDocs here.\n", encoding="utf-8")


class DuplicatesAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.repo_tmp = tempfile.TemporaryDirectory()
        self.dec_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo_tmp.cleanup)
        self.addCleanup(self.dec_tmp.cleanup)
        self.repo_root = Path(self.repo_tmp.name)
        self.decisions_dir = Path(self.dec_tmp.name)
        _seed_new_analysis_tree(self.repo_root)

    def test_duplicates_counts_and_artifact(self):
        plan = {"chosen_action": "scan for duplicate filenames and blocks"}
        measured = execute_chosen_action(
            self.decisions_dir, "D-0001", plan, repo_root=self.repo_root)
        self.assertEqual(measured["analysis"], "duplicates")
        self.assertFalse(measured["fallback"])
        counts = measured["counts"]
        # "config.py" is the only shared basename -> exactly one group of 2.
        self.assertEqual(counts["duplicate_basename_groups"], 1)
        self.assertEqual(counts["files_in_duplicate_groups"], 2)
        self.assertIn("config.py", counts["duplicate_basenames"])
        self.assertEqual(counts["headline"], 1)
        # The two config.py files share a recurring 3-line... actually 1 line
        # each, so no multi-line block repeats; deterministic non-negative int.
        self.assertIsInstance(counts["duplicate_block_count"], int)
        self.assertGreaterEqual(counts["duplicate_block_count"], 0)
        # Artifact sha256 matches on-disk bytes.
        artifact_abs = Path(self.decisions_dir) / measured["artifact_path"]
        self.assertTrue(artifact_abs.is_file())
        self.assertEqual(
            measured["artifact_sha256"],
            hashlib.sha256(artifact_abs.read_bytes()).hexdigest())
        self.assertTrue(measured["artifact_path"].endswith("duplicates.json"))

    def test_duplicates_is_deterministic(self):
        plan = {"chosen_action": "duplicate triage"}
        a = execute_chosen_action(
            self.decisions_dir, "D-0001", plan, repo_root=self.repo_root)
        b = execute_chosen_action(
            self.decisions_dir, "D-0002", plan, repo_root=self.repo_root)
        self.assertEqual(a["counts"], b["counts"])


class DocsCoverageAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.repo_tmp = tempfile.TemporaryDirectory()
        self.dec_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo_tmp.cleanup)
        self.addCleanup(self.dec_tmp.cleanup)
        self.repo_root = Path(self.repo_tmp.name)
        self.decisions_dir = Path(self.dec_tmp.name)
        _seed_new_analysis_tree(self.repo_root)

    def test_docs_coverage_counts_and_artifact(self):
        plan = {"chosen_action": "docs-coverage audit of README and docstrings"}
        measured = execute_chosen_action(
            self.decisions_dir, "D-0001", plan, repo_root=self.repo_root)
        self.assertEqual(measured["analysis"], "docs_coverage")
        self.assertFalse(measured["fallback"])
        counts = measured["counts"]
        self.assertTrue(counts["readme_present"])
        # Objects: 5 modules (__init__, mod_a, helper, config, pkg/config) + 3
        # functions (documented, undocumented, value) = 8 total.
        self.assertEqual(counts["python_objects_total"], 8)
        # Documented: mod_a module + documented() + value() = 3.
        self.assertEqual(counts["python_objects_with_docstring"], 3)
        self.assertEqual(counts["documented"], 3)
        self.assertEqual(counts["undocumented"], 5)
        self.assertAlmostEqual(counts["docstring_coverage_ratio"], 0.375)
        self.assertEqual(counts["headline"], 3)
        artifact_abs = Path(self.decisions_dir) / measured["artifact_path"]
        self.assertEqual(
            measured["artifact_sha256"],
            hashlib.sha256(artifact_abs.read_bytes()).hexdigest())
        self.assertTrue(measured["artifact_path"].endswith("docs_coverage.json"))


class ImportGraphAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.repo_tmp = tempfile.TemporaryDirectory()
        self.dec_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo_tmp.cleanup)
        self.addCleanup(self.dec_tmp.cleanup)
        self.repo_root = Path(self.repo_tmp.name)
        self.decisions_dir = Path(self.dec_tmp.name)
        _seed_new_analysis_tree(self.repo_root)

    def test_import_graph_counts_and_artifact(self):
        plan = {"chosen_action": "produce an import graph of the package"}
        measured = execute_chosen_action(
            self.decisions_dir, "D-0001", plan, repo_root=self.repo_root)
        self.assertEqual(measured["analysis"], "import_graph")
        self.assertFalse(measured["fallback"])
        counts = measured["counts"]
        # Nodes: pkg (from __init__), pkg.mod_a, pkg.helper, config, pkg.config
        # = 5 modules.
        self.assertEqual(counts["modules"], 5)
        # "from pkg import helper" resolves to the package node pkg, so the only
        # internal edge is pkg.mod_a -> pkg.
        self.assertEqual(counts["edges"], 1)
        self.assertIn("pkg.mod_a -> pkg", counts["edge_list"])
        self.assertEqual(counts["headline"], 1)
        self.assertEqual(counts["fan_out"].get("pkg.mod_a"), 1)
        self.assertEqual(counts["fan_in"].get("pkg"), 1)
        artifact_abs = Path(self.decisions_dir) / measured["artifact_path"]
        self.assertEqual(
            measured["artifact_sha256"],
            hashlib.sha256(artifact_abs.read_bytes()).hexdigest())
        self.assertTrue(measured["artifact_path"].endswith("import_graph.json"))

    def test_import_graph_is_deterministic(self):
        plan = {"chosen_action": "dependency graph"}
        a = execute_chosen_action(
            self.decisions_dir, "D-0001", plan, repo_root=self.repo_root)
        b = execute_chosen_action(
            self.decisions_dir, "D-0002", plan, repo_root=self.repo_root)
        self.assertEqual(a["counts"], b["counts"])


class ExecuteTests(unittest.TestCase):
    def setUp(self):
        self.repo_tmp = tempfile.TemporaryDirectory()
        self.dec_tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.repo_tmp.cleanup)
        self.addCleanup(self.dec_tmp.cleanup)
        self.repo_root = Path(self.repo_tmp.name)
        self.decisions_dir = Path(self.dec_tmp.name)
        _seed_tree(self.repo_root)

    def _assert_measured_shape(self, measured, decisions_dir):
        artifact_abs = Path(decisions_dir) / measured["artifact_path"]
        self.assertTrue(artifact_abs.is_file())
        data = artifact_abs.read_bytes()
        self.assertGreater(len(data), 0)
        self.assertEqual(measured["artifact_bytes"], len(data))
        # Recorded sha256 equals hashlib.sha256 of the file bytes.
        self.assertEqual(measured["artifact_sha256"],
                         hashlib.sha256(data).hexdigest())
        # duration is a finite non-negative number.
        self.assertIsInstance(measured["duration_seconds"], float)
        self.assertGreaterEqual(measured["duration_seconds"], 0.0)
        self.assertEqual(measured["duration_seconds"],
                         measured["duration_seconds"])  # not NaN
        # The artifact lives under decisions/artifacts/D-NNNN/.
        self.assertIn("artifacts/", measured["artifact_path"])

    def test_inventory_counts_match(self):
        plan = {"chosen_action": "produce a file inventory by extension"}
        measured = execute_chosen_action(
            self.decisions_dir, "D-0001", plan, repo_root=self.repo_root)
        self.assertEqual(measured["analysis"], "inventory")
        self.assertFalse(measured["fallback"])
        counts = measured["counts"]
        self.assertEqual(counts["total_files"], 4)  # .git/config is skipped
        self.assertEqual(counts["by_extension"][".py"], 2)
        self.assertEqual(counts["by_extension"][".md"], 1)
        self.assertEqual(counts["by_extension"][".txt"], 1)
        self._assert_measured_shape(measured, self.decisions_dir)

    def test_markers_counts_match(self):
        plan = {"chosen_action": "TODO/FIXME marker triage"}
        measured = execute_chosen_action(
            self.decisions_dir, "D-0002", plan, repo_root=self.repo_root)
        self.assertEqual(measured["analysis"], "markers")
        self.assertFalse(measured["fallback"])
        counts = measured["counts"]
        # 3 TODO (one per seeded line) + 1 FIXME; .git/config excluded.
        self.assertEqual(counts["by_token"]["TODO"], 3)
        self.assertEqual(counts["by_token"]["FIXME"], 1)
        self.assertEqual(counts["total_markers"], 4)
        self.assertEqual(counts["files_with_markers"], 3)
        self._assert_measured_shape(measured, self.decisions_dir)

    def test_stats_counts_match(self):
        plan = {"chosen_action": "compute line and byte statistics"}
        measured = execute_chosen_action(
            self.decisions_dir, "D-0003", plan, repo_root=self.repo_root)
        self.assertEqual(measured["analysis"], "stats")
        counts = measured["counts"]
        self.assertGreater(counts["total_bytes"], 0)
        self.assertGreater(counts["total_lines"], 0)
        self.assertEqual(counts["files_scanned"], 4)
        self._assert_measured_shape(measured, self.decisions_dir)

    def test_unmappable_action_falls_back_and_records_truthfully(self):
        plan = {"chosen_action": "pursue the alpha growth opportunity"}
        measured = execute_chosen_action(
            self.decisions_dir, "D-0004", plan, repo_root=self.repo_root)
        self.assertEqual(measured["analysis"], DEFAULT_ANALYSIS)
        self.assertTrue(measured["fallback"])
        # The fallback is recorded truthfully: the artifact body says so.
        artifact_abs = Path(self.decisions_dir) / measured["artifact_path"]
        body = json.loads(artifact_abs.read_text(encoding="utf-8"))
        self.assertTrue(body["fallback"])
        self.assertEqual(body["analysis"], "inventory")
        self._assert_measured_shape(measured, self.decisions_dir)

    def test_artifact_filename_is_analysis_name(self):
        for analysis, action in (
            ("inventory", "file inventory"),
            ("markers", "TODO scan"),
            ("stats", "byte stats"),
        ):
            with self.subTest(analysis=analysis):
                measured = execute_chosen_action(
                    self.decisions_dir, "D-0001",
                    {"chosen_action": action}, repo_root=self.repo_root)
                self.assertTrue(
                    measured["artifact_path"].endswith(analysis + ".json"))

    def test_refuses_to_write_outside_artifacts_root(self):
        # An artifacts_root whose per-decision dir escapes the root is refused.
        with tempfile.TemporaryDirectory() as outside:
            evil_root = Path(outside) / "root"
            evil_root.mkdir()
            # record_id with traversal would resolve outside evil_root.
            with self.assertRaises(ValueError):
                execute_chosen_action(
                    self.decisions_dir, "../escape",
                    {"chosen_action": "inventory"},
                    repo_root=self.repo_root, artifacts_root=evil_root)

    def test_bad_repo_root_raises(self):
        with self.assertRaises(ValueError):
            execute_chosen_action(
                self.decisions_dir, "D-0001", {"chosen_action": "inventory"},
                repo_root=self.repo_root / "does-not-exist")

    def test_excludes_own_artifacts_output_dir_from_walk(self):
        """Regression (review issue 3): a cycle must NOT measure the artifacts
        its predecessors wrote. We analyze a repo_root that CONTAINS a
        decisions/artifacts tree and confirm those files are pruned so counts
        are reproducible across cycles."""
        # Make the decisions dir live inside the analyzed repo tree and seed a
        # prior cycle's artifact under decisions/artifacts/.
        decisions_inside = self.repo_root / "decisions"
        (decisions_inside / "artifacts" / "D-0001").mkdir(parents=True)
        (decisions_inside / "artifacts" / "D-0001" / "stats.json").write_text(
            '{"prior":"artifact with TODO noise"}\n', encoding="utf-8")
        # Baseline inventory over the seeded tree EXCLUDING the artifacts dir.
        measured = execute_chosen_action(
            decisions_inside, "D-0002",
            {"chosen_action": "file inventory"}, repo_root=self.repo_root)
        # The 4 seeded files are counted; the prior artifact + the new D-0002
        # artifact dir are both pruned, so the count does not include them.
        self.assertEqual(measured["counts"]["total_files"], 4)
        # Re-running a second cycle yields the SAME count (reproducible).
        measured2 = execute_chosen_action(
            decisions_inside, "D-0003",
            {"chosen_action": "file inventory"}, repo_root=self.repo_root)
        self.assertEqual(measured2["counts"]["total_files"], 4)

    def test_supported_analyses_constant(self):
        self.assertEqual(set(SUPPORTED_ANALYSES),
                         {"inventory", "markers", "stats",
                          "duplicates", "docs_coverage", "import_graph"})
        self.assertIn(DEFAULT_ANALYSIS, SUPPORTED_ANALYSES)


class MetricValueTests(unittest.TestCase):
    """R2-S8: the metric_value accessor reads the TRUE headline value back out
    of a counts dict, fail-closed for unrecognized keys / non-int values."""

    def test_recognized_metric_keys_cover_every_headline(self):
        self.assertEqual(
            action_executor.RECOGNIZED_METRIC_KEYS,
            {"total_files", "total_markers", "total_lines", "total_bytes",
             "duplicate_basename_groups", "documented", "edges"})

    def test_reads_true_value(self):
        counts = {"total_files": 7, "by_extension": {".py": 7}}
        self.assertEqual(action_executor.metric_value(counts, "total_files"), 7)

    def test_unrecognized_key_returns_none(self):
        self.assertIsNone(
            action_executor.metric_value({"bogus": 3}, "bogus"))

    def test_missing_key_returns_none(self):
        self.assertIsNone(
            action_executor.metric_value({"total_files": 1}, "edges"))

    def test_non_int_value_returns_none(self):
        self.assertIsNone(
            action_executor.metric_value({"edges": "5"}, "edges"))

    def test_bool_value_rejected(self):
        # bool subclasses int; a bool must not be read as a metric value.
        self.assertIsNone(
            action_executor.metric_value({"documented": True}, "documented"))

    def test_non_dict_counts_returns_none(self):
        self.assertIsNone(action_executor.metric_value(None, "edges"))


class FindingsFromCountsTests(unittest.TestCase):
    """R2-S8: findings are deterministic, bounded, secret-free, empty-safe."""

    def test_duplicates_findings(self):
        counts = {"duplicate_basename_groups": 3, "duplicate_block_count": 2}
        self.assertEqual(
            action_executor.findings_from_counts("duplicates", counts),
            ["duplicate_basename_groups=3", "duplicate_block_count=2"])

    def test_docs_coverage_findings(self):
        counts = {"undocumented": 12, "documented": 40}
        self.assertEqual(
            action_executor.findings_from_counts("docs_coverage", counts),
            ["undocumented=12"])

    def test_import_graph_findings(self):
        counts = {"edges": 5, "external_imports": 9}
        self.assertEqual(
            action_executor.findings_from_counts("import_graph", counts),
            ["edges=5", "external_imports=9"])

    def test_empty_safe_when_nothing_notable(self):
        self.assertEqual(
            action_executor.findings_from_counts("duplicates",
                                                 {"duplicate_basename_groups": 0}),
            [])

    def test_inventory_and_stats_surface_no_findings(self):
        self.assertEqual(
            action_executor.findings_from_counts("inventory",
                                                 {"total_files": 10}), [])
        self.assertEqual(
            action_executor.findings_from_counts("stats",
                                                 {"total_lines": 99}), [])

    def test_non_dict_counts_is_empty(self):
        self.assertEqual(
            action_executor.findings_from_counts("duplicates", None), [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
