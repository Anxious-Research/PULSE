"""Deterministic Artifact & Proof-of-Work Verifier.

Enforces zero-trust engineering standards:
- Eliminates hallucinated completions: an agent's claim of 'done' is rejected unless
  backed by non-empty git diffs, clean syntax, and verified exitcode 0 test results.
- Validates syntax compilation (Python AST, YAML, JSON) before code can enter review.
- Parses pytest XML/JSON output for actual pass/fail counts (no self-reporting).
- Requires physical evidence: git diffs, file artifacts, shell exit codes.
- Integrates with Gatekeeper QA phase for multi-stage verification gates.
"""

from __future__ import annotations

import ast
import json
import logging
import re
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("enterprise.verifier")


@dataclass
class TestExecutionResult:
    """Parsed test execution metrics from physical output files."""
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    errors: int = 0
    total: int = 0
    duration_seconds: float = 0.0
    exit_code: int = -1
    evidence_file: Optional[str] = None
    raw_output: str = ""
    verified: bool = False
    
    @property
    def success_rate(self) -> float:
        """Calculate pass rate percentage."""
        return (self.passed / self.total * 100.0) if self.total > 0 else 0.0
    
    @property
    def all_passed(self) -> bool:
        """True if all tests passed and exit code was 0."""
        return self.verified and self.exit_code == 0 and self.failed == 0 and self.errors == 0


@dataclass
class GitArtifactEvidence:
    """Physical git evidence of work performed."""
    has_uncommitted_changes: bool = False
    has_committed_changes: bool = False
    files_changed: int = 0
    insertions: int = 0
    deletions: int = 0
    changed_files: List[str] = field(default_factory=list)
    diff_stat: str = ""
    verified: bool = False
    
    @property
    def has_any_changes(self) -> bool:
        """True if any code modifications detected."""
        return self.has_uncommitted_changes or self.has_committed_changes


@dataclass
class FileArtifactEvidence:
    """Physical file artifact verification."""
    path: str
    exists: bool = False
    size_bytes: int = 0
    is_empty: bool = True
    syntax_valid: bool = False
    syntax_errors: List[str] = field(default_factory=list)
    verified: bool = False


@dataclass
class VerificationReport:
    """Comprehensive verification evidence bundle."""
    task_id: str
    verified: bool = False
    git_evidence: Optional[GitArtifactEvidence] = None
    test_evidence: Optional[TestExecutionResult] = None
    file_artifacts: List[FileArtifactEvidence] = field(default_factory=list)
    rejection_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    
    def add_rejection(self, reason: str) -> None:
        """Add a rejection reason and mark as unverified."""
        self.rejection_reasons.append(reason)
        self.verified = False
    
    def add_warning(self, warning: str) -> None:
        """Add a non-blocking warning."""
        self.warnings.append(warning)
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dictionary for logging/storage."""
        return {
            "task_id": self.task_id,
            "verified": self.verified,
            "git_evidence": self.git_evidence.__dict__ if self.git_evidence else None,
            "test_evidence": self.test_evidence.__dict__ if self.test_evidence else None,
            "file_artifacts": [a.__dict__ for a in self.file_artifacts],
            "rejection_reasons": self.rejection_reasons,
            "warnings": self.warnings,
        }


class ArtifactVerifier:
    """Verifies that an agent actually produced working, tested code artifacts."""

    @staticmethod
    def verify_git_artifacts(worktree_or_repo_path: Path) -> Tuple[bool, GitArtifactEvidence]:
        """Verify that genuine code modifications exist in the workspace.
        
        Returns:
            (verified, evidence) tuple where verified=True only if physical changes detected.
        """
        path = Path(worktree_or_repo_path)
        evidence = GitArtifactEvidence()
        
        if not path.exists():
            logger.error("Workspace path does not exist: %s", path)
            return False, evidence
        
        try:
            # Check for uncommitted changes
            status_res = subprocess.run(
                ["git", "-C", str(path), "status", "--porcelain"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            
            if status_res.returncode == 0:
                uncommitted = [line.strip() for line in status_res.stdout.splitlines() if line.strip()]
                evidence.has_uncommitted_changes = len(uncommitted) > 0
                evidence.changed_files.extend([line[3:].strip() for line in uncommitted if len(line) > 3])
            
            # Check for committed changes in recent history
            diff_stat_res = subprocess.run(
                ["git", "-C", str(path), "diff", "HEAD~1", "--stat"],
                capture_output=True, text=True, check=False, timeout=10,
            )
            
            if diff_stat_res.returncode == 0 and diff_stat_res.stdout.strip():
                evidence.has_committed_changes = True
                evidence.diff_stat = diff_stat_res.stdout.strip()
                
                # Parse stat summary: "N files changed, X insertions(+), Y deletions(-)"
                match = re.search(r"(\d+)\s+files?\s+changed", diff_stat_res.stdout)
                if match:
                    evidence.files_changed = int(match.group(1))
                
                match = re.search(r"(\d+)\s+insertions?\(\+\)", diff_stat_res.stdout)
                if match:
                    evidence.insertions = int(match.group(1))
                
                match = re.search(r"(\d+)\s+deletions?\(-\)", diff_stat_res.stdout)
                if match:
                    evidence.deletions = int(match.group(1))
            
            # Additional check: get diff against main/master if HEAD~1 failed
            if not evidence.has_committed_changes and not evidence.has_uncommitted_changes:
                for base_branch in ["main", "master", "develop"]:
                    diff_res = subprocess.run(
                        ["git", "-C", str(path), "diff", base_branch, "--stat"],
                        capture_output=True, text=True, check=False, timeout=10,
                    )
                    if diff_res.returncode == 0 and diff_res.stdout.strip():
                        evidence.has_committed_changes = True
                        evidence.diff_stat = diff_res.stdout.strip()
                        break
            
            evidence.verified = evidence.has_any_changes
            
        except subprocess.TimeoutExpired:
            logger.error("Git command timed out for path: %s", path)
            return False, evidence
        except Exception as exc:
            logger.error("Git verification failed: %s", exc)
            return False, evidence
        
        return evidence.verified, evidence

    @staticmethod
    def verify_syntax(files: List[Path]) -> Tuple[bool, List[FileArtifactEvidence]]:
        """Verify that modified files have valid syntax (no compile or parse breaks).
        
        Returns:
            (all_valid, evidence_list) tuple where all_valid=True only if all files compile.
        """
        evidence_list = []
        all_valid = True
        
        for f in files:
            p = Path(f)
            artifact = FileArtifactEvidence(path=str(p))
            
            if not p.exists():
                artifact.exists = False
                artifact.syntax_errors.append(f"File does not exist: {p}")
                all_valid = False
                evidence_list.append(artifact)
                continue
            
            if not p.is_file():
                artifact.exists = False
                artifact.syntax_errors.append(f"Path is not a regular file: {p}")
                all_valid = False
                evidence_list.append(artifact)
                continue
            
            artifact.exists = True
            artifact.size_bytes = p.stat().st_size
            artifact.is_empty = artifact.size_bytes == 0
            
            # Syntax validation by file type
            if p.suffix == ".py":
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                    ast.parse(content, filename=str(p))
                    artifact.syntax_valid = True
                    artifact.verified = True
                except SyntaxError as syn_err:
                    error_msg = f"SyntaxError in {p.name} line {syn_err.lineno}: {syn_err.msg}"
                    artifact.syntax_errors.append(error_msg)
                    artifact.syntax_valid = False
                    all_valid = False
                except Exception as exc:
                    artifact.syntax_errors.append(f"Parse error in {p.name}: {exc}")
                    artifact.syntax_valid = False
                    all_valid = False
            
            elif p.suffix == ".json":
                try:
                    json.loads(p.read_text(encoding="utf-8", errors="ignore"))
                    artifact.syntax_valid = True
                    artifact.verified = True
                except json.JSONDecodeError as jerr:
                    artifact.syntax_errors.append(f"JSONDecodeError in {p.name}: {jerr.msg}")
                    artifact.syntax_valid = False
                    all_valid = False
            
            elif p.suffix in (".yaml", ".yml"):
                try:
                    import yaml
                    yaml.safe_load(p.read_text(encoding="utf-8", errors="ignore"))
                    artifact.syntax_valid = True
                    artifact.verified = True
                except Exception as exc:
                    artifact.syntax_errors.append(f"YAML parse error in {p.name}: {exc}")
                    artifact.syntax_valid = False
                    all_valid = False
            
            else:
                # For non-validated file types, mark as verified if exists and non-empty
                artifact.syntax_valid = True
                artifact.verified = True
            
            evidence_list.append(artifact)
        
        return all_valid, evidence_list

    @staticmethod
    def parse_pytest_xml(xml_path: Path) -> Optional[TestExecutionResult]:
        """Parse pytest JUnit XML output for deterministic test counts.
        
        Returns:
            TestExecutionResult with actual counts from XML, or None if parsing fails.
        """
        result = TestExecutionResult(evidence_file=str(xml_path))
        
        try:
            tree = ET.parse(xml_path)
            root = tree.getroot()
            
            # JUnit XML can have <testsuites> or direct <testsuite>
            testsuites = root.findall(".//testsuite")
            if not testsuites:
                testsuites = [root] if root.tag == "testsuite" else []
            
            for suite in testsuites:
                result.total += int(suite.get("tests", 0))
                result.failed += int(suite.get("failures", 0))
                result.errors += int(suite.get("errors", 0))
                result.skipped += int(suite.get("skipped", 0))
                result.duration_seconds += float(suite.get("time", 0.0))
            
            result.passed = result.total - result.failed - result.errors - result.skipped
            result.verified = True
            
            logger.info("Parsed pytest XML: %d total, %d passed, %d failed, %d errors, %d skipped",
                       result.total, result.passed, result.failed, result.errors, result.skipped)
            
        except ET.ParseError as exc:
            logger.error("Failed to parse pytest XML at %s: %s", xml_path, exc)
            return None
        except Exception as exc:
            logger.error("Unexpected error parsing pytest XML: %s", exc)
            return None
        
        return result

    @staticmethod
    def parse_pytest_json(json_path: Path) -> Optional[TestExecutionResult]:
        """Parse pytest JSON report for deterministic test counts.
        
        Requires pytest-json-report plugin: pytest --json-report --json-report-file=report.json
        
        Returns:
            TestExecutionResult with actual counts from JSON, or None if parsing fails.
        """
        result = TestExecutionResult(evidence_file=str(json_path))
        
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            
            summary = data.get("summary", {})
            result.total = summary.get("total", 0)
            result.passed = summary.get("passed", 0)
            result.failed = summary.get("failed", 0)
            result.skipped = summary.get("skipped", 0)
            result.errors = summary.get("error", 0)
            result.duration_seconds = data.get("duration", 0.0)
            result.exit_code = data.get("exitcode", -1)
            result.verified = True
            
            logger.info("Parsed pytest JSON: %d total, %d passed, %d failed, %d errors, %d skipped",
                       result.total, result.passed, result.failed, result.errors, result.skipped)
            
        except json.JSONDecodeError as exc:
            logger.error("Failed to parse pytest JSON at %s: %s", json_path, exc)
            return None
        except Exception as exc:
            logger.error("Unexpected error parsing pytest JSON: %s", exc)
            return None
        
        return result

    @staticmethod
    def parse_pytest_output(stdout: str, stderr: str) -> TestExecutionResult:
        """Parse pytest stdout/stderr for test counts when no XML/JSON available.
        
        This is a fallback parser for direct pytest output. Less reliable than XML/JSON.
        """
        result = TestExecutionResult(raw_output=stdout + "\n" + stderr)
        
        # Parse pytest summary line: "5 passed, 2 failed, 1 skipped in 0.23s"
        pattern = r"(\d+)\s+(passed|failed|skipped|error)"
        for match in re.finditer(pattern, stdout + stderr, re.IGNORECASE):
            count = int(match.group(1))
            status = match.group(2).lower()
            
            if status == "passed":
                result.passed = count
            elif status == "failed":
                result.failed = count
            elif status == "skipped":
                result.skipped = count
            elif status == "error":
                result.errors = count
        
        result.total = result.passed + result.failed + result.skipped + result.errors
        
        # Parse duration: "in 0.23s"
        duration_match = re.search(r"in\s+([\d.]+)s", stdout + stderr)
        if duration_match:
            result.duration_seconds = float(duration_match.group(1))
        
        result.verified = result.total > 0
        
        return result

    @staticmethod
    def verify_test_execution(
        workdir: Path,
        test_command: str = "pytest -q tests/test_enterprise*.py",
        xml_output: Optional[Path] = None,
        json_output: Optional[Path] = None,
        timeout: int = 120,
    ) -> Tuple[bool, TestExecutionResult]:
        """Run automated test suite and require physical exitcode 0 + parsed results.
        
        Priority order for evidence:
        1. pytest JSON report (most reliable, requires pytest-json-report)
        2. pytest JUnit XML (standard, use --junit-xml flag)
        3. stdout/stderr parsing (fallback, less reliable)
        
        Args:
            workdir: Working directory for test execution
            test_command: Shell command to run tests
            xml_output: Path to JUnit XML output file (if using --junit-xml)
            json_output: Path to JSON report file (if using --json-report)
            timeout: Maximum execution time in seconds
            
        Returns:
            (verified, result) tuple where verified=True only if exit code 0 AND tests passed.
        """
        result = TestExecutionResult()
        
        try:
            # Run the test command
            proc = subprocess.run(
                test_command,
                shell=True,
                cwd=str(workdir),
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            
            result.exit_code = proc.returncode
            result.raw_output = proc.stdout + "\n" + proc.stderr
            
            # Parse structured output if available
            if json_output and json_output.exists():
                parsed = ArtifactVerifier.parse_pytest_json(json_output)
                if parsed:
                    result = parsed
                    result.exit_code = proc.returncode
            
            elif xml_output and xml_output.exists():
                parsed = ArtifactVerifier.parse_pytest_xml(xml_output)
                if parsed:
                    result = parsed
                    result.exit_code = proc.returncode
            
            else:
                # Fallback: parse stdout/stderr
                result = ArtifactVerifier.parse_pytest_output(proc.stdout, proc.stderr)
                result.exit_code = proc.returncode
            
            # Verification passes only if:
            # 1. Exit code is 0
            # 2. At least some tests ran
            # 3. No failures or errors
            verified = result.all_passed and result.total > 0
            
            if not verified:
                logger.warning("Test verification failed: exit_code=%d, passed=%d, failed=%d, errors=%d, total=%d",
                              result.exit_code, result.passed, result.failed, result.errors, result.total)
            
            return verified, result
            
        except subprocess.TimeoutExpired:
            result.exit_code = -1
            result.raw_output = f"Test suite timed out after {timeout}s"
            logger.error("Test execution timed out after %ds", timeout)
            return False, result
        
        except Exception as exc:
            result.exit_code = -1
            result.raw_output = str(exc)
            logger.error("Test execution failed with exception: %s", exc)
            return False, result

    @staticmethod
    def verify_deliverable_artifacts(artifact_paths: List[Path]) -> List[FileArtifactEvidence]:
        """Verify that requested deliverable files exist and are non-empty.
        
        Args:
            artifact_paths: List of file paths that should exist as task deliverables
            
        Returns:
            List of FileArtifactEvidence for each artifact (verified=True if exists and non-empty)
        """
        evidence_list = []
        
        for path in artifact_paths:
            p = Path(path)
            artifact = FileArtifactEvidence(path=str(p))
            
            if p.exists() and p.is_file():
                artifact.exists = True
                artifact.size_bytes = p.stat().st_size
                artifact.is_empty = artifact.size_bytes == 0
                artifact.verified = not artifact.is_empty
                
                if artifact.is_empty:
                    artifact.syntax_errors.append("File exists but is empty")
            else:
                artifact.exists = False
                artifact.syntax_errors.append("File does not exist or is not a regular file")
            
            evidence_list.append(artifact)
        
        return evidence_list

    @staticmethod
    def comprehensive_verification(
        task_id: str,
        workdir: Path,
        require_git_changes: bool = True,
        require_tests: bool = True,
        test_command: Optional[str] = None,
        deliverable_files: Optional[List[Path]] = None,
        changed_files: Optional[List[Path]] = None,
    ) -> VerificationReport:
        """Perform comprehensive multi-phase artifact verification.
        
        This is the main entry point for Gatekeeper integration. Enforces:
        1. Git artifacts: Physical code changes exist
        2. Syntax validation: Changed files compile cleanly
        3. Test execution: Automated tests pass with exit code 0
        4. Deliverables: Required files exist and are non-empty
        
        Args:
            task_id: Unique task identifier
            workdir: Working directory (typically git repo root)
            require_git_changes: Reject if no git changes detected
            require_tests: Reject if tests fail or don't run
            test_command: Override default test command
            deliverable_files: List of required artifact files
            changed_files: List of files to syntax-check (auto-detected from git if None)
            
        Returns:
            VerificationReport with all evidence and pass/fail verdict
        """
        report = VerificationReport(task_id=task_id)
        
        # Phase 1: Git artifact verification
        if require_git_changes:
            git_verified, git_evidence = ArtifactVerifier.verify_git_artifacts(workdir)
            report.git_evidence = git_evidence
            
            if not git_verified:
                report.add_rejection(
                    "No physical git changes detected. Agent may have hallucinated work completion."
                )
            else:
                logger.info("Git verification passed: %d files changed, %d insertions, %d deletions",
                           git_evidence.files_changed, git_evidence.insertions, git_evidence.deletions)
        
        # Phase 2: Syntax validation of changed files
        files_to_check = changed_files or []
        if not files_to_check and report.git_evidence:
            # Auto-detect from git evidence
            files_to_check = [workdir / f for f in report.git_evidence.changed_files]
        
        if files_to_check:
            syntax_valid, file_evidence = ArtifactVerifier.verify_syntax(files_to_check)
            report.file_artifacts.extend(file_evidence)
            
            if not syntax_valid:
                errors = [e for artifact in file_evidence for e in artifact.syntax_errors]
                report.add_rejection(f"Syntax validation failed: {'; '.join(errors[:3])}")
            else:
                logger.info("Syntax verification passed for %d files", len(files_to_check))
        
        # Phase 3: Test execution verification
        if require_tests:
            if test_command is None:
                test_command = "pytest -q tests/"
            
            # Try to use structured output for deterministic parsing
            xml_output = workdir / ".pytest_cache" / "verification.xml"
            enhanced_command = f"{test_command} --junit-xml={xml_output}"
            
            test_verified, test_result = ArtifactVerifier.verify_test_execution(
                workdir=workdir,
                test_command=enhanced_command,
                xml_output=xml_output,
                timeout=120,
            )
            report.test_evidence = test_result
            
            if not test_verified:
                if test_result.exit_code != 0:
                    report.add_rejection(
                        f"Test suite failed with exit code {test_result.exit_code}. "
                        f"Passed: {test_result.passed}, Failed: {test_result.failed}, "
                        f"Errors: {test_result.errors}"
                    )
                elif test_result.total == 0:
                    report.add_rejection("No tests were executed. Cannot verify correctness.")
                else:
                    report.add_rejection(f"Tests failed: {test_result.failed} failures, {test_result.errors} errors")
            else:
                logger.info("Test verification passed: %d/%d tests passed (%.1f%%)",
                           test_result.passed, test_result.total, test_result.success_rate)
        
        # Phase 4: Deliverable artifact verification
        if deliverable_files:
            artifact_evidence = ArtifactVerifier.verify_deliverable_artifacts(deliverable_files)
            report.file_artifacts.extend(artifact_evidence)
            
            missing = [a for a in artifact_evidence if not a.verified]
            if missing:
                report.add_rejection(
                    f"Required deliverable artifacts missing or empty: {', '.join(a.path for a in missing[:3])}"
                )
            else:
                logger.info("All %d required deliverables verified", len(deliverable_files))
        
        # Final verdict: verified only if no rejections
        report.verified = len(report.rejection_reasons) == 0
        
        if report.verified:
            logger.info("✅ Comprehensive verification PASSED for task '%s'", task_id)
        else:
            logger.warning("❌ Comprehensive verification FAILED for task '%s': %s",
                          task_id, "; ".join(report.rejection_reasons))
        
        return report


def integrate_with_gatekeeper(
    gatekeeper_instance,
    task_id: str,
    workdir: Path,
    test_command: Optional[str] = None,
) -> Dict[str, Any]:
    """Integration helper: Run comprehensive verification and submit to Gatekeeper.
    
    This bridges the ArtifactVerifier with the EnterpriseGatekeeper's QA phase.
    
    Args:
        gatekeeper_instance: Instance of EnterpriseGatekeeper
        task_id: Task ID to verify
        workdir: Working directory for verification
        test_command: Optional override for test command
        
    Returns:
        Dict with verification report and gatekeeper submission result
    """
    # Run comprehensive verification
    report = ArtifactVerifier.comprehensive_verification(
        task_id=task_id,
        workdir=workdir,
        require_git_changes=True,
        require_tests=True,
        test_command=test_command,
    )
    
    # Submit to gatekeeper if gate exists
    if task_id in gatekeeper_instance.gates:
        gate = gatekeeper_instance.gates[task_id]
        
        if report.verified:
            gate.qa_status = "passed"
            gate.qa_notes = (
                f"✅ Artifact verification PASSED. "
                f"Git: {report.git_evidence.files_changed if report.git_evidence else 0} files changed. "
                f"Tests: {report.test_evidence.passed if report.test_evidence else 0}/"
                f"{report.test_evidence.total if report.test_evidence else 0} passed."
            )
        else:
            gate.qa_status = "failed"
            gate.qa_notes = (
                f"❌ Artifact verification FAILED. "
                f"Rejections: {'; '.join(report.rejection_reasons)}"
            )
        
        gatekeeper_instance.save()
    
    return {
        "task_id": task_id,
        "verification_report": report.to_dict(),
        "gatekeeper_updated": task_id in gatekeeper_instance.gates,
    }
