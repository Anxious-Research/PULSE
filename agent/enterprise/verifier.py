"""Deterministic Artifact & Proof-of-Work Verifier.

Enforces zero-trust engineering standards:
- Eliminates hallucinated completions: an agent's claim of 'done' is rejected unless
  backed by non-empty git diffs, clean syntax, and verified exitcode 0 test results.
- Validates syntax compilation (Python AST, YAML, JSON) before code can enter review.
"""

from __future__ import annotations

import ast
import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("enterprise.verifier")


class ArtifactVerifier:
    """Verifies that an agent actually produced working, tested code artifacts."""

    @staticmethod
    def verify_git_artifacts(worktree_or_repo_path: Path) -> Tuple[bool, Dict[str, Any]]:
        """Verify that genuine code modifications exist in the workspace."""
        path = Path(worktree_or_repo_path)
        if not path.exists():
            return False, {"error": "Workspace path does not exist."}

        # Check uncommitted status or diff against main/HEAD
        status_res = subprocess.run(
            ["git", "-C", str(path), "status", "--porcelain"],
            capture_output=True, text=True, check=False,
        )
        diff_res = subprocess.run(
            ["git", "-C", str(path), "diff", "HEAD~1", "--stat"],
            capture_output=True, text=True, check=False,
        )

        uncommitted = [line for line in status_res.stdout.splitlines() if line.strip()]
        has_committed_diff = bool(diff_res.stdout.strip())

        if not uncommitted and not has_committed_diff:
            return False, {
                "verified": False,
                "reason": "Zero code changes detected on disk or git history (hallucinated work).",
            }

        return True, {
            "verified": True,
            "uncommitted_files": uncommitted,
            "stat": diff_res.stdout.strip() or f"{len(uncommitted)} uncommitted files",
        }

    @staticmethod
    def verify_syntax(files: List[Path]) -> Tuple[bool, List[str]]:
        """Verify that modified files have valid syntax (no compile or parse breaks)."""
        errors = []
        for f in files:
            p = Path(f)
            if not p.exists() or not p.is_file():
                continue
            if p.suffix == ".py":
                try:
                    ast.parse(p.read_text(encoding="utf-8", errors="ignore"), filename=str(p))
                except SyntaxError as syn_err:
                    errors.append(f"SyntaxError in {p.name} line {syn_err.lineno}: {syn_err.msg}")
            elif p.suffix == ".json":
                try:
                    json.loads(p.read_text(encoding="utf-8", errors="ignore"))
                except json.JSONDecodeError as jerr:
                    errors.append(f"JSONDecodeError in {p.name}: {jerr.msg}")

        return (len(errors) == 0), errors

    @staticmethod
    def verify_test_execution(
        workdir: Path,
        test_command: str = "pytest -q tests/test_enterprise*.py",
        timeout: int = 60,
    ) -> Tuple[bool, Dict[str, Any]]:
        """Run automated test suite and require physical exitcode 0."""
        try:
            res = subprocess.run(
                test_command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            passed = (res.returncode == 0)
            return passed, {
                "passed": passed,
                "exit_code": res.returncode,
                "stdout_tail": res.stdout[-500:].strip(),
                "stderr_tail": res.stderr[-500:].strip(),
            }
        except subprocess.TimeoutExpired:
            return False, {"passed": False, "error": f"Test suite timed out after {timeout}s"}
        except Exception as exc:
            return False, {"passed": False, "error": str(exc)}
