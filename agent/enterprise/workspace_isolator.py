"""Enterprise Git Worktree & Workspace Isolation Engine.

Ensures concurrent multi-bot execution does not corrupt shared repository state:
- Every active worker task executes in an isolated Git worktree under ~/.pulse/workspaces/tasks/<task_id>/.
- Operates on a dedicated branch `task/<task_id>` branched from current `main`.
- Prevents file collision, concurrent git lock contention, and dirty worktree cross-talk.
"""

from __future__ import annotations

import logging
import subprocess
import shutil
from pathlib import Path
from typing import Dict, List, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.workspace_isolator")


class WorkspaceIsolator:
    """Provisions and cleans up isolated git worktrees per task."""

    def __init__(self, repo_dir: Optional[Path] = None, workspaces_dir: Optional[Path] = None):
        if repo_dir is None:
            self.repo_dir = get_pulse_home().parent / "PULSE"
        else:
            self.repo_dir = Path(repo_dir)

        if workspaces_dir is None:
            self.workspaces_dir = get_pulse_home() / "workspaces" / "tasks"
        else:
            self.workspaces_dir = Path(workspaces_dir)
        self.workspaces_dir.mkdir(parents=True, exist_ok=True)

    def create_task_worktree(self, task_id: str, base_ref: str = "main") -> Path:
        """Create an isolated git worktree and branch for the given task."""
        safe_id = task_id.replace("/", "-").replace(" ", "-")
        worktree_path = self.workspaces_dir / safe_id
        branch_name = f"task/{safe_id}"

        # If already exists, return existing worktree
        if worktree_path.exists() and (worktree_path / ".git").exists():
            logger.info("Reusing existing worktree for task '%s' at %s", task_id, worktree_path)
            return worktree_path

        # Clean stale path if broken
        if worktree_path.exists():
            shutil.rmtree(worktree_path, ignore_errors=True)

        cmd = [
            "git", "-C", str(self.repo_dir),
            "worktree", "add", "-b", branch_name, str(worktree_path), base_ref,
        ]
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info("Created isolated worktree for task '%s' at %s (branch: %s)", task_id, worktree_path, branch_name)
            return worktree_path
        except subprocess.CalledProcessError as exc:
            # If branch already exists, checkout without -b
            if "already exists" in exc.stderr:
                cmd_existing = [
                    "git", "-C", str(self.repo_dir),
                    "worktree", "add", str(worktree_path), branch_name,
                ]
                subprocess.run(cmd_existing, capture_output=True, text=True, check=True)
                return worktree_path
            logger.error("Failed to create git worktree for %s: %s", task_id, exc.stderr)
            raise RuntimeError(f"Git worktree creation failed: {exc.stderr.strip()}") from exc

    def get_worktree_path(self, task_id: str) -> Optional[Path]:
        safe_id = task_id.replace("/", "-").replace(" ", "-")
        p = self.workspaces_dir / safe_id
        return p if (p.exists() and (p / ".git").exists()) else None

    def cleanup_task_worktree(self, task_id: str, delete_branch: bool = True) -> bool:
        """Prune and remove task worktree and optionally delete its branch."""
        safe_id = task_id.replace("/", "-").replace(" ", "-")
        worktree_path = self.workspaces_dir / safe_id
        branch_name = f"task/{safe_id}"

        # 1. Prune git worktree
        if worktree_path.exists():
            try:
                subprocess.run(
                    ["git", "-C", str(self.repo_dir), "worktree", "remove", "--force", str(worktree_path)],
                    capture_output=True, text=True, check=False,
                )
            except Exception:
                pass
            shutil.rmtree(worktree_path, ignore_errors=True)

        subprocess.run(
            ["git", "-C", str(self.repo_dir), "worktree", "prune"],
            capture_output=True, text=True, check=False,
        )

        # 2. Delete branch if requested
        if delete_branch:
            subprocess.run(
                ["git", "-C", str(self.repo_dir), "branch", "-D", branch_name],
                capture_output=True, text=True, check=False,
            )

        logger.info("Cleaned up worktree for task '%s'", task_id)
        return True

    def list_active_worktrees(self) -> Dict[str, str]:
        """List active task worktrees from git worktree list."""
        cmd = ["git", "-C", str(self.repo_dir), "worktree", "list", "--porcelain"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            active = {}
            current_path = ""
            for line in res.stdout.splitlines():
                if line.startswith("worktree "):
                    current_path = line.split(" ", 1)[1].strip()
                elif line.startswith("branch "):
                    branch = line.split(" ", 1)[1].strip()
                    if "task/" in branch:
                        active[branch] = current_path
            return active
        except Exception as exc:
            logger.warning("Could not list git worktrees: %s", exc)
            return {}
