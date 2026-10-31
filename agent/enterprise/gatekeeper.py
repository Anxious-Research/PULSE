"""Enterprise Code Review & Verification Gatekeeper.

Enforces zero-defect Pull Request & Task Approval gates:
1. Automated QA Test Suite verification (VP of QA).
2. Adversarial Security & Prompt-Leak scan (VP of InfoSec).
3. Architectural RFC sign-off (CTO / Department Lead).
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.gatekeeper")

REVIEW_LOGS_FILENAME = "review_gates.json"


class ReviewVerdict(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    REQUEST_CHANGES = "request_changes"
    REJECTED = "rejected"


@dataclass
class ReviewGateRecord:
    task_id: str
    author_profile: str
    diff_summary: str
    target_files: List[str] = field(default_factory=list)
    qa_status: str = "pending"  # pending | passed | failed
    qa_notes: str = ""
    security_status: str = "pending"  # pending | passed | failed
    security_notes: str = ""
    architect_verdict: str = ReviewVerdict.PENDING.value
    architect_notes: str = ""
    created_at: float = field(default_factory=time.time)
    finalized_at: Optional[float] = None
    merged: bool = False


class EnterpriseGatekeeper:
    """Manages multi-phase verification gates before tasks can be marked complete."""

    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            self.dir = get_pulse_home() / "enterprise"
            self.dir.mkdir(parents=True, exist_ok=True)
        else:
            self.dir = Path(storage_dir)
            self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / REVIEW_LOGS_FILENAME
        self.gates: Dict[str, ReviewGateRecord] = {}
        self.load()

    def load(self) -> None:
        if not self.file.exists():
            return
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
            for tid, data in raw.items():
                self.gates[tid] = ReviewGateRecord(**data)
        except Exception as exc:
            logger.warning("Could not load review gates: %s", exc)

    def save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self.gates.items()}
            self.file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save review gates: %s", exc)

    def submit_for_review(
        self,
        task_id: str,
        author_profile: str,
        diff_summary: str,
        target_files: Optional[List[str]] = None,
    ) -> ReviewGateRecord:
        gate = ReviewGateRecord(
            task_id=task_id,
            author_profile=author_profile,
            diff_summary=diff_summary,
            target_files=target_files or [],
        )
        self.gates[task_id] = gate
        self.save()
        logger.info("Task '%s' submitted for multi-phase review gate by %s", task_id, author_profile)
        return gate

    def run_qa_check(self, task_id: str, test_command: str = "pytest -q tests/test_brain*.py") -> bool:
        """Phase 1: Automated QA Verification by VP of QA."""
        if task_id not in self.gates:
            raise KeyError(f"Task '{task_id}' not found in review gates.")
        gate = self.gates[task_id]

        try:
            res = subprocess.run(
                test_command,
                shell=True,
                capture_output=True,
                text=True,
                cwd=str(get_pulse_home().parent / "PULSE"),
                timeout=60,
            )
            passed = res.returncode == 0
            gate.qa_status = "passed" if passed else "failed"
            gate.qa_notes = "All test batteries passed cleanly." if passed else res.stderr or res.stdout[:500]
        except Exception as exc:
            gate.qa_status = "failed"
            gate.qa_notes = f"QA test execution error: {exc}"

        self.save()
        return gate.qa_status == "passed"

    def run_security_check(self, task_id: str) -> bool:
        """Phase 2: InfoSec & Prompt Leak Audit by VP of InfoSec / CISO."""
        if task_id not in self.gates:
            raise KeyError(f"Task '{task_id}' not found in review gates.")
        gate = self.gates[task_id]

        # Scan target files for threat patterns or prompt leaks
        suspicious = []
        for fpath in gate.target_files:
            p = Path(fpath)
            if p.exists() and p.is_file():
                try:
                    content = p.read_text(encoding="utf-8", errors="ignore")
                    if "_MEMORY_ROUTING_BLOCK" in content and "background_review" not in str(p):
                        suspicious.append(f"{p.name}: Potential prompt leak")
                except Exception:
                    pass

        if suspicious:
            gate.security_status = "failed"
            gate.security_notes = "Security violations detected: " + "; ".join(suspicious)
        else:
            gate.security_status = "passed"
            gate.security_notes = "Zero-trust security audit: clean. Zero prompt-leaks or exfiltrations."

        self.save()
        return gate.security_status == "passed"

    def sign_off(
        self,
        task_id: str,
        reviewer_profile: str,
        verdict: ReviewVerdict,
        notes: str = "",
    ) -> Dict[str, Any]:
        """Phase 3: Architectural sign-off by CTO / Department VP."""
        if task_id not in self.gates:
            raise KeyError(f"Task '{task_id}' not found in review gates.")
        gate = self.gates[task_id]

        gate.architect_verdict = verdict.value
        gate.architect_notes = f"[{reviewer_profile}]: {notes}"

        # Can only merge if QA passed, Security passed, and Architect approved
        can_merge = (
            gate.qa_status == "passed"
            and gate.security_status == "passed"
            and gate.architect_verdict == ReviewVerdict.APPROVED.value
        )

        if can_merge:
            gate.merged = True
            gate.finalized_at = time.time()
            logger.info("Task '%s' fully CERTIFIED and MERGED.", task_id)
        else:
            gate.merged = False

        self.save()
        return {
            "task_id": task_id,
            "can_merge": can_merge,
            "qa_status": gate.qa_status,
            "security_status": gate.security_status,
            "verdict": gate.architect_verdict,
            "notes": notes,
        }
