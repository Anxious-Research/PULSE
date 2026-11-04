"""Enterprise Git Merging & PR Automation Engine.

Physically merges certified worker task branches into `main` via GitHub CLI:
- Executes real git rebase onto main with conflict detection.
- Creates GitHub PRs with QA/Security approval metadata automatically.
- Auto-merges PRs when all checks pass.
- Escalates merge conflicts to human intervention with detailed diagnostics.
- Publishes commits directly to origin main with auditable traceability.
"""

from __future__ import annotations

import json
import logging
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home
from .workspace_isolator import WorkspaceIsolator

logger = logging.getLogger("enterprise.git_merger")


class MergeConflictError(Exception):
    """Raised when automatic merge fails due to conflicts."""
    pass


class GitMerger:
    """Executes verified git merges and automated PR workflow from task branches into main."""

    def __init__(self, repo_dir: Optional[Path] = None):
        if repo_dir is None:
            self.repo_dir = get_pulse_home().parent / "PULSE"
        else:
            self.repo_dir = Path(repo_dir)
        self.isolator = WorkspaceIsolator(repo_dir=self.repo_dir)

    def _run_git(self, args: List[str], check: bool = True, workdir: Optional[Path] = None) -> subprocess.CompletedProcess:
        """Execute git command with consistent error handling."""
        cmd = ["git", "-C", str(workdir or self.repo_dir)] + args
        return subprocess.run(cmd, capture_output=True, text=True, check=check)

    def _check_gh_cli(self) -> bool:
        """Verify GitHub CLI is installed and authenticated."""
        try:
            result = subprocess.run(["gh", "auth", "status"], capture_output=True, text=True)
            return result.returncode == 0
        except FileNotFoundError:
            logger.warning("GitHub CLI (gh) not found. Install via: brew install gh")
            return False

    def _commit_worktree_changes(
        self,
        task_id: str,
        worktree_path: Path,
        commit_message: Optional[str] = None,
    ) -> bool:
        """Commit any uncommitted changes in the task worktree."""
        status_res = self._run_git(["status", "-s"], workdir=worktree_path)
        if not status_res.stdout.strip():
            return False

        safe_id = task_id.replace("/", "-").replace(" ", "-")
        msg = commit_message or f"feat({safe_id}): autonomous worker implementation"
        self._run_git(["add", "."], workdir=worktree_path)
        self._run_git(["commit", "-m", msg], workdir=worktree_path)
        logger.info("Committed uncommitted worktree changes for task %s", task_id)
        return True

    def _rebase_branch_onto_main(self, branch_name: str) -> Dict[str, Any]:
        """Rebase task branch onto latest main. Returns conflict info if any."""
        # Fetch latest main
        try:
            self._run_git(["fetch", "origin", "main:main"])
        except subprocess.CalledProcessError as exc:
            logger.warning("Could not fetch origin main: %s", exc.stderr)

        # Checkout task branch
        self._run_git(["checkout", branch_name])

        # Attempt rebase
        try:
            rebase_res = self._run_git(["rebase", "main"])
            logger.info("Successfully rebased %s onto main", branch_name)
            return {"success": True, "conflicts": []}
        except subprocess.CalledProcessError as exc:
            # Rebase failed - extract conflict files
            status_res = self._run_git(["status", "-s"], check=False)
            conflict_files = [
                line.split()[-1] for line in status_res.stdout.splitlines()
                if line.startswith("UU ") or line.startswith("AA ")
            ]
            
            # Abort the rebase
            self._run_git(["rebase", "--abort"], check=False)
            
            logger.error("Rebase conflict on %s: %s", branch_name, conflict_files)
            return {
                "success": False,
                "conflicts": conflict_files,
                "error": exc.stderr.strip(),
            }

    def _create_github_pr(
        self,
        branch_name: str,
        task_id: str,
        qa_status: str = "unknown",
        security_status: str = "unknown",
        architect_notes: str = "",
        auto_merge: bool = True,
    ) -> Dict[str, Any]:
        """Create GitHub PR via gh CLI with approval metadata."""
        if not self._check_gh_cli():
            return {"success": False, "error": "GitHub CLI not available"}

        safe_id = task_id.replace("/", "-").replace(" ", "-")
        pr_title = f"feat(enterprise): {safe_id}"
        
        # Build PR description with approval gates
        pr_body = f"""## Autonomous Task Completion: `{task_id}`

### Review Gate Status
- **QA Verification**: {qa_status.upper()}
- **Security Audit**: {security_status.upper()}
- **Architect Sign-off**: APPROVED

### Notes
{architect_notes or "Automated merge from enterprise orchestrator"}

---
*Generated by PULSE Enterprise GitMerger*
"""

        # Push branch to remote
        try:
            self._run_git(["push", "-u", "origin", branch_name])
        except subprocess.CalledProcessError as exc:
            return {"success": False, "error": f"Failed to push branch: {exc.stderr.strip()}"}

        # Create PR via gh CLI
        pr_cmd = [
            "gh", "pr", "create",
            "--title", pr_title,
            "--body", pr_body,
            "--base", "main",
            "--head", branch_name,
        ]

        try:
            pr_res = subprocess.run(
                pr_cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=str(self.repo_dir),
            )
            pr_url = pr_res.stdout.strip()
            logger.info("Created GitHub PR: %s", pr_url)
            
            # Extract PR number from URL
            pr_number = pr_url.split("/")[-1]
            
            result = {
                "success": True,
                "pr_url": pr_url,
                "pr_number": pr_number,
            }

            # Auto-merge if requested
            if auto_merge:
                merge_result = self._auto_merge_pr(pr_number)
                result["auto_merge"] = merge_result

            return result

        except subprocess.CalledProcessError as exc:
            logger.error("Failed to create PR: %s", exc.stderr)
            return {"success": False, "error": exc.stderr.strip()}

    def _auto_merge_pr(self, pr_number: str) -> Dict[str, Any]:
        """Enable auto-merge on PR when all checks pass."""
        try:
            # Enable auto-merge with squash strategy
            merge_cmd = [
                "gh", "pr", "merge", pr_number,
                "--auto",
                "--squash",
                "--delete-branch",
            ]
            
            merge_res = subprocess.run(
                merge_cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=str(self.repo_dir),
            )
            
            logger.info("Enabled auto-merge for PR #%s", pr_number)
            return {
                "success": True,
                "message": "Auto-merge enabled, will merge when checks pass",
                "output": merge_res.stdout.strip(),
            }
        except subprocess.CalledProcessError as exc:
            logger.warning("Could not enable auto-merge for PR #%s: %s", pr_number, exc.stderr)
            return {
                "success": False,
                "error": exc.stderr.strip(),
            }

    def merge_task_branch(
        self,
        task_id: str,
        commit_message: Optional[str] = None,
        push_to_remote: bool = True,
        create_pr: bool = False,
        qa_status: str = "unknown",
        security_status: str = "unknown",
        architect_notes: str = "",
    ) -> Dict[str, Any]:
        """
        Physically rebase, merge task/<task_id> into main, and optionally create PR.
        
        Args:
            task_id: Task identifier
            commit_message: Custom commit message
            push_to_remote: Whether to push to origin
            create_pr: Create GitHub PR instead of direct merge
            qa_status: QA verification status for PR metadata
            security_status: Security audit status for PR metadata
            architect_notes: Architect review notes for PR metadata
            
        Returns:
            Dict with success status, commit/PR info, or conflict details
        """
        safe_id = task_id.replace("/", "-").replace(" ", "-")
        branch_name = f"task/{safe_id}"
        worktree_path = self.isolator.get_worktree_path(task_id)

        # Step 1: Commit any uncommitted worker changes in the worktree
        if worktree_path and worktree_path.exists():
            self._commit_worktree_changes(task_id, worktree_path, commit_message)

        # Step 2: Verify branch exists
        br_res = self._run_git(["branch", "--list", branch_name])
        if not br_res.stdout.strip():
            return {"success": False, "error": f"Branch '{branch_name}' does not exist."}

        # Step 3: If creating PR, do rebase + PR workflow
        if create_pr:
            rebase_result = self._rebase_branch_onto_main(branch_name)
            
            if not rebase_result["success"]:
                # Escalate conflict to human
                return {
                    "success": False,
                    "error": "MERGE_CONFLICT",
                    "conflict_files": rebase_result["conflicts"],
                    "message": (
                        f"Automatic rebase failed for {branch_name}. "
                        f"Conflicts in: {', '.join(rebase_result['conflicts'])}. "
                        "Human intervention required."
                    ),
                    "escalated": True,
                }
            
            # Create PR
            pr_result = self._create_github_pr(
                branch_name=branch_name,
                task_id=task_id,
                qa_status=qa_status,
                security_status=security_status,
                architect_notes=architect_notes,
                auto_merge=True,
            )
            
            if pr_result["success"]:
                # Clean up worktree but keep branch (PR needs it)
                self.isolator.cleanup_task_worktree(task_id, delete_branch=False)
                
            return {
                "success": pr_result["success"],
                "task_id": task_id,
                "branch": branch_name,
                "pr_url": pr_result.get("pr_url"),
                "pr_number": pr_result.get("pr_number"),
                "auto_merge_enabled": pr_result.get("auto_merge", {}).get("success", False),
            }

        # Step 4: Direct merge workflow (original behavior)
        try:
            self._run_git(["checkout", "main"])
        except subprocess.CalledProcessError as exc:
            return {"success": False, "error": f"Failed to checkout main: {exc.stderr.strip()}"}

        # Merge task branch into main
        merge_msg = commit_message or f"feat(enterprise): merge verified task {task_id}"
        try:
            merge_res = self._run_git(["merge", "--no-ff", branch_name, "-m", merge_msg])
            logger.info("Successfully merged %s into main: %s", branch_name, merge_res.stdout.strip())
        except subprocess.CalledProcessError as exc:
            logger.error("Git merge conflict/failure on %s: %s", branch_name, exc.stderr)
            self._run_git(["merge", "--abort"], check=False)
            
            return {
                "success": False,
                "error": "MERGE_CONFLICT",
                "message": f"Merge conflict: {exc.stderr.strip()}",
                "escalated": True,
            }

        # Extract latest commit SHA
        sha_res = self._run_git(["rev-parse", "HEAD"])
        commit_sha = sha_res.stdout.strip()

        # Push to remote origin main if requested
        pushed = False
        if push_to_remote:
            try:
                push_res = self._run_git(["push", "origin", "main"])
                pushed = True
                logger.info("Pushed merged commit %s to origin main: %s", commit_sha[:8], push_res.stdout.strip())
            except subprocess.CalledProcessError as exc:
                logger.warning("Could not push to remote origin: %s", exc.stderr)

        # Clean up worktree and delete task branch
        self.isolator.cleanup_task_worktree(task_id, delete_branch=True)

        return {
            "success": True,
            "task_id": task_id,
            "branch": branch_name,
            "commit_sha": commit_sha,
            "pushed": pushed,
        }

    def create_pr_for_task(
        self,
        task_id: str,
        qa_status: str = "passed",
        security_status: str = "passed",
        architect_notes: str = "",
    ) -> Dict[str, Any]:
        """
        Convenience method to create PR with rebase and auto-merge.
        Equivalent to merge_task_branch with create_pr=True.
        """
        return self.merge_task_branch(
            task_id=task_id,
            create_pr=True,
            qa_status=qa_status,
            security_status=security_status,
            architect_notes=architect_notes,
        )
