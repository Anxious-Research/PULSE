"""Talent Operations & Performance Governance Engine.

Provides Big-Tech style HR & performance calibration for autonomous bots:
- Stack ranking and performance scoring based on real task deliveries.
- Automated PIP (Performance Improvement Plan) on repeated failures or low quality.
- Automated quarantine / layoff protocols when failure thresholds are breached.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.talent_ops")

LEDGER_FILENAME = "talent_ledger.json"
PIP_THRESHOLD_SCORE = 70.0
PIP_CONSECUTIVE_FAILURES = 2
QUARANTINE_CONSECUTIVE_FAILURES_ON_PIP = 2


@dataclass
class BotPerformanceRecord:
    profile: str
    role: str = "specialist"
    department: str = "general"
    tasks_assigned: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0
    qa_test_failures: int = 0
    security_infractions: int = 0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    performance_score: float = 100.0
    status: str = "good_standing"  # good_standing | pip | quarantined
    pip_started_at: Optional[float] = None
    quarantined_at: Optional[float] = None
    last_evaluated_at: float = field(default_factory=time.time)
    audit_notes: List[str] = field(default_factory=list)

    def calculate_score(self) -> float:
        if self.tasks_assigned == 0:
            return 100.0
        # Base completion ratio (60% weight)
        completion_ratio = self.tasks_completed / max(1, self.tasks_assigned)
        base = completion_ratio * 70.0

        # Penalties: test failures (-5 each), security infractions (-20 each), consecutive failures (-10 each)
        penalties = (self.qa_test_failures * 5.0) + (self.security_infractions * 20.0) + (self.consecutive_failures * 10.0)
        
        # Streak bonus (+2 per consecutive success up to 15)
        streak_bonus = min(15.0, self.consecutive_successes * 2.0)

        score = max(0.0, min(100.0, base + 15.0 + streak_bonus - penalties))
        self.performance_score = round(score, 1)
        return self.performance_score


class TalentLedger:
    """Persistent storage and management for enterprise bot performance."""

    def __init__(self, ledger_path: Optional[Path] = None):
        if ledger_path is None:
            ent_dir = get_pulse_home() / "enterprise"
            ent_dir.mkdir(parents=True, exist_ok=True)
            self.path = ent_dir / LEDGER_FILENAME
        else:
            self.path = Path(ledger_path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.records: Dict[str, BotPerformanceRecord] = {}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            for profile, data in raw.items():
                self.records[profile] = BotPerformanceRecord(**data)
        except Exception as exc:
            logger.warning("Could not load talent ledger: %s", exc)

    def save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self.records.items()}
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save talent ledger: %s", exc)

    def get_or_register(self, profile: str, role: str = "specialist", department: str = "general") -> BotPerformanceRecord:
        if profile not in self.records:
            self.records[profile] = BotPerformanceRecord(
                profile=profile, role=role, department=department
            )
            self.save()
        return self.records[profile]

    def record_task_outcome(
        self,
        profile: str,
        task_id: str,
        success: bool,
        reason: str = "",
        test_failure: bool = False,
        security_infraction: bool = False,
    ) -> Dict[str, Any]:
        """Record the outcome of a task attempt and update the bot's career status."""
        rec = self.get_or_register(profile)
        rec.tasks_assigned += 1
        rec.last_evaluated_at = time.time()

        if success:
            rec.tasks_completed += 1
            rec.consecutive_successes += 1
            rec.consecutive_failures = 0
            note = f"Task '{task_id}' succeeded: {reason}"
            rec.audit_notes.append(f"[{time.strftime('%Y-%m-%d %H:%M')}] {note}")
            # Check for PIP rehabilitation
            if rec.status == "pip" and rec.consecutive_successes >= 3:
                rec.status = "good_standing"
                rec.pip_started_at = None
                rec.audit_notes.append(f"[{time.strftime('%Y-%m-%d %H:%M')}] REHABILITATED from PIP to good_standing.")
        else:
            rec.tasks_failed += 1
            rec.consecutive_failures += 1
            rec.consecutive_successes = 0
            if test_failure:
                rec.qa_test_failures += 1
            if security_infraction:
                rec.security_infractions += 1

            note = f"Task '{task_id}' failed: {reason}"
            rec.audit_notes.append(f"[{time.strftime('%Y-%m-%d %H:%M')}] {note}")

            # Check for PIP or Quarantine
            if rec.status == "good_standing":
                if rec.consecutive_failures >= PIP_CONSECUTIVE_FAILURES or (
                    rec.tasks_assigned >= 3 and rec.calculate_score() < PIP_THRESHOLD_SCORE
                ):
                    rec.status = "pip"
                    rec.pip_started_at = time.time()
                    rec.audit_notes.append(
                        f"[{time.strftime('%Y-%m-%d %H:%M')}] PLACED ON PIP: consecutive_failures={rec.consecutive_failures}, score={rec.performance_score}"
                    )
            elif rec.status == "pip":
                if rec.consecutive_failures >= (PIP_CONSECUTIVE_FAILURES + QUARANTINE_CONSECUTIVE_FAILURES_ON_PIP) or (
                    rec.tasks_assigned >= 5 and rec.calculate_score() < 40.0
                ):
                    rec.status = "quarantined"
                    rec.quarantined_at = time.time()
                    rec.audit_notes.append(
                        f"[{time.strftime('%Y-%m-%d %H:%M')}] TERMINATED/QUARANTINED: failed while on PIP."
                    )

        rec.calculate_score()
        # Cap audit notes to last 30
        rec.audit_notes = rec.audit_notes[-30:]
        self.save()

        return {
            "profile": profile,
            "status": rec.status,
            "score": rec.performance_score,
            "consecutive_failures": rec.consecutive_failures,
            "tasks_completed": rec.tasks_completed,
            "tasks_assigned": rec.tasks_assigned,
        }

    def stack_ranking(self) -> Dict[str, List[Dict[str, Any]]]:
        """Sort bots into Tier 1 (High Performers >= 85), Tier 2 (Core 70-84), and Tier 3 (At Risk / PIP < 70)."""
        all_recs = sorted(self.records.values(), key=lambda r: -r.calculate_score())
        tier1, tier2, tier3 = [], [], []
        for r in all_recs:
            info = {
                "profile": r.profile,
                "role": r.role,
                "score": r.performance_score,
                "status": r.status,
                "completed": f"{r.tasks_completed}/{r.tasks_assigned}",
            }
            if r.status == "quarantined" or r.performance_score < 70.0:
                tier3.append(info)
            elif r.performance_score >= 85.0:
                tier1.append(info)
            else:
                tier2.append(info)

        return {"top_performers": tier1, "core_engineers": tier2, "at_risk_or_pip": tier3}


def evaluate_bot_performance(ledger_path: Optional[Path] = None) -> Dict[str, Any]:
    """Convenience helper for automated cron stack ranking."""
    ledger = TalentLedger(ledger_path)
    return ledger.stack_ranking()
