"""Execute original G4/G5 synthetic Chromium drills and the G6 evidence gate.

No model API, Preview site, CI or deployment is invoked by this orchestrator.
Requires a clean, pinned SPRAY checkout and existing Playwright/Chromium.
Passing the offline drill produces G6 readiness PARTIAL, never live PASS.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
SOURCES = (
    "scripts/run_g4_chromium_matrix.py",
    "scripts/run_g5_chromium_tasks.py",
    "scripts/verify_g45_evidence.py",
    "spray_schema/agent_loop.py",
    "spray_schema/qa_flow.py",
    "spray_schema/g5_batch.py",
    "spray_schema/playwright_preview.py",
    "scripts/run_g3_gemini_chromium.py",
    "spray_schema/gemini_planner.py",
    "spray_schema/model_observation.py",
    "spray_schema/evidence_capture.py",
)


def _run(command: list[str], *, env: dict[str, str], timeout: int) -> tuple[int, dict]:
    """Return only bounded machine-readable output. Never persist raw errors."""
    try:
        process = subprocess.run(command, cwd=ROOT, env=env, timeout=timeout,
                                 text=True, capture_output=True, check=False)
        if len(process.stdout) > 16384:
            return process.returncode, {}
        lines = [line for line in process.stdout.splitlines() if line.strip()]
        data = json.loads(lines[-1]) if lines else {}
        if type(data) is not dict:
            data = {}
        return process.returncode, data
    except (OSError, subprocess.TimeoutExpired, ValueError, UnicodeError,
            json.JSONDecodeError):
        return -1, {}


def _safe_env(chromium_path: str | None) -> dict[str, str]:
    env = {key: os.environ[key] for key in (
        "PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR",
        "PLAYWRIGHT_BROWSERS_PATH", "XDG_RUNTIME_DIR", "FONTCONFIG_PATH",
    ) if key in os.environ}
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if chromium_path:
        if not Path(chromium_path).is_file():
            raise FileNotFoundError("Chromium executable does not exist")
        env["SPRAY_CHROMIUM_PATH"] = chromium_path
    return env


def _checkout(expected_head: str | None, env: dict[str, str]) -> str:
    if (type(expected_head) is not str or len(expected_head) != 40
            or any(c not in "0123456789abcdef" for c in expected_head.lower())):
        raise ValueError("an explicit 40-character pinned HEAD is required")
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=ROOT, env=env, text=True,
                              capture_output=True, timeout=10, check=False)
    result = git("rev-parse", "HEAD")
    head = result.stdout.strip().lower()
    if result.returncode or len(head) != 40 or any(c not in "0123456789abcdef" for c in head):
        raise RuntimeError("missing Git checkout")
    if head != expected_head.lower():
        raise RuntimeError("HEAD mismatch; refuse stale checkout")
    if git("diff", "--quiet").returncode or git("diff", "--cached", "--quiet").returncode:
        raise RuntimeError("dirty tracked worktree; refuse unpinned evidence")
    untracked = git("ls-files", "--others", "--exclude-standard")
    if untracked.returncode or untracked.stdout.strip():
        raise RuntimeError("untracked files in pinned checkout; refuse import shadowing")
    # Git index flags (assume-unchanged/skip-worktree) can hide modified
    # tracked code from 'git diff --quiet'. Bind every executed project
    # source AND this orchestrator to the actual pinned commit's Git blob.
    for source in (*SOURCES, "scripts/run_g45_synthetic_gate.py"):
        committed = git("rev-parse", f"HEAD:{source}")
        on_disk = git("hash-object", "--", source)
        expected = committed.stdout.strip().lower()
        actual = on_disk.stdout.strip().lower()
        if (committed.returncode or on_disk.returncode
                or len(expected) != 40
                or any(char not in "0123456789abcdef" for char in expected)
                or actual != expected):
            raise RuntimeError("source blob mismatch with pinned HEAD")
    # Ignored import-shadow modules escape --others --exclude-standard.
    # Reject even ignored caches/assets inside executable source trees.
    # For certification use a fresh checkout, not a dirty developer venv.
    ignored = git("ls-files", "--others", "--ignored",
                  "--exclude-standard", "--", "scripts", "spray_schema")
    if ignored.returncode or ignored.stdout.strip():
        raise RuntimeError("ignored source files in pinned checkout")
    return head


def _digest_sources() -> dict[str, str]:
    result = {}
    for name in SOURCES:
        file = ROOT / name
        if not file.is_file() or file.is_symlink():
            raise FileNotFoundError("missing or linked source module")
        result[name] = sha256(file.read_bytes()).hexdigest()
    return result


def _evidence_base() -> Path:
    """Refuse pre-existing links that could redirect browser evidence off-tree."""
    home = Path.home()
    base = home / "data/SPRAY/output"
    for entry in (home / "data", home / "data/SPRAY", base,
                  base / "g4", base / "g5", base / "g45"):
        if entry.is_symlink():
            raise RuntimeError("symlinked evidence directory; refuse redirected output")
    return base


def _private_save(path: Path, data: dict) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=False)
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")


def execute(*, expected_head: str | None = None,
            chromium_path: str | None = None, timeout: int = 480) -> dict:
    if type(timeout) is not int or not 30 <= timeout <= 900:
        raise ValueError("invalid stage timeout")
    env = _safe_env(chromium_path)
    head = _checkout(expected_head, env)
    digests = _digest_sources()
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    rid = stamp + "-" + uuid4().hex[:12]
    output = _evidence_base()
    g4 = output / "g4" / ("gate-" + rid)
    g5 = output / "g5" / ("gate-" + rid)
    summary_path = output / "g45" / ("gate-" + rid) / "summary.json"
    commands = (
        ("g4", [sys.executable, str(ROOT / SOURCES[0]), "--evidence-dir", str(g4)]),
        ("g5", [sys.executable, str(ROOT / SOURCES[1]), "--evidence-dir", str(g5)]),
    )
    result: dict[str, object] = {
        "schema": "spray.g45.synthetic_recheck.v1",
        "commit": head,
        "source_sha256": digests,
        "mode": "synthetic_loopback_deterministic_planner",
        "actual_model_call": False,
        "real_preview": "NOT_VERIFIED",
        "g5_live_model": "NOT_VERIFIED",
        "latest_ci": "NOT_VERIFIED",
        "g4_original_chromium": "NOT_VERIFIED",
        "g5_original_chromium": "NOT_VERIFIED",
        "s7_evidence_gate": "NOT_VERIFIED",
        "g6_readiness": "NOT_VERIFIED",
    }
    for name, command in commands:
        if chromium_path:
            command += ["--chromium-path", chromium_path]
        rc, data = _run(command, env=env, timeout=timeout)
        if name == "g4":
            ok = (rc == 0 and data.get("final_verdict") == "PASS"
                  and data.get("scenario_runs") == 9
                  and data.get("expected_matches") == 9
                  and (g4 / "matrix.json").is_file()
                  and len(list(g4.glob("*.png"))) == 9)
            result["g4_original_chromium"] = "PASS" if ok else "FAIL"
        else:
            ok = (rc == 0 and data.get("status") == "PASS"
                  and data.get("total_tasks") == 5
                  and type(data.get("successes")) is int
                  and 4 <= data["successes"] <= 5
                  and (g5 / "run.json").is_file())
            result["g5_original_chromium"] = "PASS" if ok else "FAIL"
    # This exact original verifier is invoked without --g5-model-proof.
    if (result["g4_original_chromium"] == "PASS"
            and result["g5_original_chromium"] == "PASS"):
        rc, checked = _run([
            sys.executable, str(ROOT / SOURCES[2]),
            "--g4-matrix", str(g4 / "matrix.json"),
            "--g5-batch", str(g5 / "run.json"),
        ], env=env, timeout=30)
        valid_partial = (rc == 2 and checked.get("readiness") == "PARTIAL"
                         and checked.get("g4_synthetic_chromium") == "PASS"
                         and checked.get("g5_batch_quality") == "PASS"
                         and checked.get("g5_real_model_proof") == "NOT_VERIFIED")
        result["s7_evidence_gate"] = "PASS" if valid_partial else "FAIL"
        result["g6_readiness"] = "PARTIAL" if valid_partial else "FAIL"
    else:
        result["g6_readiness"] = "FAIL"
    # Local paths are deliberately not copied into the private report.
    _private_save(summary_path, result)
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-head", required=True,
                        help="required exact 40-character branch HEAD")
    parser.add_argument("--chromium-path")
    parser.add_argument("--timeout", type=int, default=480)
    args = parser.parse_args(argv)
    try:
        result = execute(expected_head=args.expected_head,
                         chromium_path=args.chromium_path, timeout=args.timeout)
    except (ValueError, OSError, RuntimeError) as exc:
        print(json.dumps({"status": "FAIL", "error_type": type(exc).__name__}))
        return 1
    print(json.dumps({key: result[key] for key in (
        "commit", "g4_original_chromium", "g5_original_chromium",
        "s7_evidence_gate", "g6_readiness", "g5_live_model",
    )}, sort_keys=True))
    return 0 if result["s7_evidence_gate"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
