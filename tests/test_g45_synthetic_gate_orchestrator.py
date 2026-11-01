"""Offline controls for the opt-in G4/G5 original-runner orchestrator."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest import mock

SCRIPT = Path(__file__).resolve().parents[1] / "scripts/run_g45_synthetic_gate.py"
spec = importlib.util.spec_from_file_location("g45_synthetic_gate", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
HEAD = "a" * 40


class SyntheticGateOrchestratorTests(unittest.TestCase):
    def _pinned_source_fixture(self, directory):
        """Build a real, disposable Git checkout with every pinned source path."""
        root = Path(directory)
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        for name in (*module.SOURCES, "scripts/run_g45_synthetic_gate.py"):
            file = root / name
            file.parent.mkdir(parents=True, exist_ok=True)
            file.write_text("pass\\n".replace("\\n", "\n"), encoding="utf-8")
        subprocess.run(["git", "-C", str(root), "add", "scripts",
                        "spray_schema"], check=True)
        subprocess.run(["git", "-C", str(root), "-c", "user.name=test",
                        "-c", "user.email=test@example.invalid", "commit",
                        "-qm", "baseline"], check=True)
        sha = subprocess.check_output(
            ["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        return root, sha

    def test_pristine_pinned_source_blobs_are_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            root, sha = self._pinned_source_fixture(td)
            with mock.patch.object(module, "ROOT", root):
                self.assertEqual(module._checkout(sha, module._safe_env(None)), sha)

    def test_reject_assume_unchanged_tracked_source_mutation(self):
        """git diff --quiet alone can report clean for a modified code blob."""
        with tempfile.TemporaryDirectory() as td:
            root, sha = self._pinned_source_fixture(td)
            name = "scripts/run_g4_chromium_matrix.py"
            subprocess.run(["git", "-C", str(root), "update-index",
                            "--assume-unchanged", name], check=True)
            (root / name).write_text("raise RuntimeError('tampered')\\n".replace("\\n", "\n"),
                                     encoding="utf-8")
            self.assertEqual(subprocess.run(
                ["git", "-C", str(root), "diff", "--quiet"], check=False).returncode, 0)
            with mock.patch.object(module, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "source blob"):
                    module._checkout(sha, module._safe_env(None))

    def test_reject_ignored_python_import_shadow(self):
        """An ignored scripts/json.py is absent from untracked git ls-files."""
        with tempfile.TemporaryDirectory() as td:
            root, sha = self._pinned_source_fixture(td)
            (root / ".git/info/exclude").write_text("scripts/json.py\\n".replace("\\n", "\n"),
                                                    encoding="utf-8")
            (root / "scripts/json.py").write_text("raise RuntimeError('shadow')\\n".replace("\\n", "\n"),
                                                   encoding="utf-8")
            untracked = subprocess.check_output(
                ["git", "-C", str(root), "ls-files", "--others",
                 "--exclude-standard"], text=True).strip()
            self.assertEqual(untracked, "")
            with mock.patch.object(module, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "ignored"):
                    module._checkout(sha, module._safe_env(None))

    def _run_fake(self, *, g4_ok=True, g5_ok=True, proof="PARTIAL"):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            calls = []

            def fake_run(command, *, env, timeout):
                calls.append(command)
                if "run_g4_chromium_matrix.py" in command[1]:
                    dirpath = Path(command[command.index("--evidence-dir") + 1])
                    dirpath.mkdir(parents=True)
                    (dirpath / "matrix.json").write_text("{}")
                    for index in range(9):
                        (dirpath / f"test-{index}.png").write_bytes(b"PNG")
                    return (0 if g4_ok else 1,
                            {"final_verdict": "PASS" if g4_ok else "FAIL",
                             "scenario_runs": 9, "expected_matches": 9})
                if "run_g5_chromium_tasks.py" in command[1]:
                    dirpath = Path(command[command.index("--evidence-dir") + 1])
                    dirpath.mkdir(parents=True)
                    (dirpath / "run.json").write_text("{}")
                    return (0 if g5_ok else 1,
                            {"status": "PASS" if g5_ok else "FAIL",
                             "successes": 5 if g5_ok else 3, "total_tasks": 5})
                self.assertIn("verify_g45_evidence.py", command[1])
                self.assertNotIn("--g5-model-proof", command)
                return 2, {"readiness": proof,
                           "g4_synthetic_chromium": "PASS",
                           "g5_batch_quality": "PASS",
                           "g5_real_model_proof": "NOT_VERIFIED"}

            with (mock.patch.object(module.Path, "home", return_value=root),
                  mock.patch.object(module, "_checkout", return_value=HEAD),
                  mock.patch.object(module, "_digest_sources", return_value={}),
                  mock.patch.object(module, "_run", side_effect=fake_run)):
                outcome = module.execute(expected_head=HEAD)
            saved = list((root / "data/SPRAY/output/g45").glob("*/summary.json"))
            self.assertEqual(len(saved), 1)
            self.assertEqual(json.loads(saved[0].read_text()), outcome)
            self.assertEqual(stat.S_IMODE(saved[0].stat().st_mode), 0o600)
            return outcome, calls

    def test_success_is_partial_never_live_pass(self):
        result, calls = self._run_fake()
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["g4_original_chromium"], "PASS")
        self.assertEqual(result["g5_original_chromium"], "PASS")
        self.assertEqual(result["s7_evidence_gate"], "PASS")
        self.assertEqual(result["g6_readiness"], "PARTIAL")
        self.assertEqual(result["g5_live_model"], "NOT_VERIFIED")

    def test_independent_g5_still_runs_after_g4_fail(self):
        result, calls = self._run_fake(g4_ok=False)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["g4_original_chromium"], "FAIL")
        self.assertEqual(result["g5_original_chromium"], "PASS")
        self.assertEqual(result["s7_evidence_gate"], "NOT_VERIFIED")
        self.assertEqual(result["g6_readiness"], "FAIL")

    def test_verifier_cannot_upgrade_synthetic_to_full_pass(self):
        result, calls = self._run_fake(proof="PASS")
        self.assertEqual(len(calls), 3)
        self.assertEqual(result["s7_evidence_gate"], "FAIL")
        self.assertEqual(result["g6_readiness"], "FAIL")

    def test_refuse_fake_g5_success(self):
        result, _ = self._run_fake(g5_ok=False)
        self.assertEqual(result["g5_original_chromium"], "FAIL")

    def test_child_environment_strips_all_credentials_and_proxy(self):
        with mock.patch.dict(os.environ, {
            "HOME": "/tmp/safe", "GEMINI_API_KEY": "secret",
            "AWS_SECRET_ACCESS_KEY": "secret", "PYTHONPATH": "/tmp/hijack",
            "HTTPS_PROXY": "http://wrong.invalid",
        }, clear=True):
            env = module._safe_env(None)
        self.assertEqual(env["HOME"], "/tmp/safe")
        self.assertEqual(env["PYTHONDONTWRITEBYTECODE"], "1")
        for secret in ("GEMINI_API_KEY", "AWS_SECRET_ACCESS_KEY",
                       "PYTHONPATH", "HTTPS_PROXY"):
            self.assertNotIn(secret, env)

    def test_missing_chromium_is_preflight_failure(self):
        with self.assertRaises(FileNotFoundError):
            module._safe_env("/definitely/nonexistent/chromium")

    def test_stale_head_is_refused_before_any_browser(self):
        with mock.patch.object(module.subprocess, "run", return_value=mock.Mock(
            returncode=0, stdout="b" * 40 + "\n")):
            with self.assertRaisesRegex(RuntimeError, "HEAD mismatch"):
                module._checkout(HEAD, {"PATH": "/usr/bin"})

    def test_non_json_or_timeout_is_not_a_pass(self):
        with mock.patch.object(module.subprocess, "run", return_value=mock.Mock(
            returncode=0, stdout="unexpected output", stderr="private")):
            rc, data = module._run(["python", "test.py"], env={}, timeout=30)
        self.assertEqual(rc, -1)
        self.assertEqual(data, {})


    def test_requires_explicit_pinned_head_before_git_actions(self):
        with mock.patch.object(module.subprocess, "run") as runner:
            with self.assertRaisesRegex((ValueError, RuntimeError), "HEAD|head|pin"):
                module._checkout(None, {"PATH": "/usr/bin"})
            runner.assert_not_called()

    def test_reject_untracked_python_source_in_real_checkout(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            (root / "spray_schema").mkdir()
            (root / "spray_schema" / "keep.py").write_text("pass\n")
            subprocess.run(["git", "-C", str(root), "add", "spray_schema/keep.py"], check=True)
            subprocess.run(["git", "-C", str(root), "-c", "user.name=test",
                            "-c", "user.email=test@example.invalid", "commit", "-qm", "baseline"], check=True)
            sha = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            (root / "spray_schema" / "json.py").write_text("raise RuntimeError('untracked import')\n")
            with mock.patch.object(module, "ROOT", root):
                with self.assertRaisesRegex(RuntimeError, "untracked"):
                    module._checkout(sha, module._safe_env(None))

    def test_reject_symlinked_evidence_tree_before_subprocess(self):
        with tempfile.TemporaryDirectory() as td:
            home = Path(td) / "home"
            outside = Path(td) / "outside"
            outside.mkdir()
            link_parent = home / "data/SPRAY/output"
            link_parent.mkdir(parents=True)
            (link_parent / "g45").symlink_to(outside, target_is_directory=True)
            with (mock.patch.object(module.Path, "home", return_value=home),
                  mock.patch.object(module, "_checkout", return_value=HEAD),
                  mock.patch.object(module, "_digest_sources", return_value={}),
                  mock.patch.object(module, "_run") as run):
                with self.assertRaisesRegex(RuntimeError, "symlink"):
                    module.execute(expected_head=HEAD)
                run.assert_not_called()
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
