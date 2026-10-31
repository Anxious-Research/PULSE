"""Enterprise CFO Token Economics & Compute Budgeting Engine.

Tracks inference expenditures and enforces departmental token quotas:
- Granular tracking of prompt/completion tokens and estimated USD costs per division.
- Soft quota warnings (80% utilization) alerted to @pulse-cfo.
- Hard quota enforcement (100% utilization) to prevent rogue inference loops.
- Financial burn summaries and token ROI analytics.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.cfo")

BUDGET_STORE_FILENAME = "cfo_budgets.json"

# Default Token Quotas per Department (in thousands of tokens)
DEFAULT_QUOTAS_K_TOKENS = {
    "executive": 5_000,      # 5M tokens
    "engineering": 25_000,   # 25M tokens
    "product": 5_000,        # 5M tokens
    "operations": 10_000,    # 10M tokens
}


@dataclass
class DepartmentBudget:
    department: str
    quota_tokens: int
    used_tokens: int = 0
    estimated_cost_usd: float = 0.0
    is_frozen: bool = False
    warning_issued: bool = False
    last_updated: float = field(default_factory=time.time)


class CFOBudgetEngine:
    """Manages corporate token budgets and compute quotas."""

    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            self.dir = get_pulse_home() / "enterprise"
        else:
            self.dir = Path(storage_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / BUDGET_STORE_FILENAME
        self.budgets: Dict[str, DepartmentBudget] = {}
        self.load()

    def load(self) -> None:
        if not self.file.exists():
            # Initialize with default departmental allocations
            for dept, k_tokens in DEFAULT_QUOTAS_K_TOKENS.items():
                self.budgets[dept] = DepartmentBudget(
                    department=dept,
                    quota_tokens=k_tokens * 1000,
                )
            self.save()
            return

        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
            for dept, bdata in raw.items():
                self.budgets[dept] = DepartmentBudget(**bdata)
        except Exception as exc:
            logger.warning("Could not load CFO budgets: %s", exc)

    def save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self.budgets.items()}
            self.file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save CFO budgets: %s", exc)

    def record_usage(
        self,
        department: str,
        profile: str,
        tokens: int,
        cost_per_m_tokens: float = 3.0,
    ) -> Dict[str, Any]:
        """Record token consumption and check quota constraints."""
        dept = department.lower()
        if dept not in self.budgets:
            self.budgets[dept] = DepartmentBudget(department=dept, quota_tokens=10_000_000)

        b = self.budgets[dept]
        b.used_tokens += tokens
        b.estimated_cost_usd += (tokens / 1_000_000) * cost_per_m_tokens
        b.last_updated = time.time()

        utilization = b.used_tokens / max(1, b.quota_tokens)

        # Check soft cap (80%)
        if utilization >= 0.80 and not b.warning_issued:
            b.warning_issued = True
            logger.warning("⚠️ CFO ALERT: Dept '%s' reached 80%% compute quota (%d/%d tokens).", dept, b.used_tokens, b.quota_tokens)

        # Check hard cap (100%)
        if utilization >= 1.0:
            b.is_frozen = True
            logger.critical("🛑 CFO FREEZE: Dept '%s' breached 100%% compute quota. Operations suspended.", dept)

        self.save()
        return {
            "department": dept,
            "used_tokens": b.used_tokens,
            "quota_tokens": b.quota_tokens,
            "utilization_pct": round(utilization * 100, 1),
            "is_frozen": b.is_frozen,
            "estimated_cost_usd": round(b.estimated_cost_usd, 4),
        }

    def grant_budget_extension(self, department: str, additional_tokens: int) -> DepartmentBudget:
        """CFO authorization to extend compute quota."""
        dept = department.lower()
        if dept not in self.budgets:
            self.budgets[dept] = DepartmentBudget(department=dept, quota_tokens=additional_tokens)
        else:
            b = self.budgets[dept]
            b.quota_tokens += additional_tokens
            b.is_frozen = False
            b.warning_issued = False
        self.save()
        logger.info("CFO granted +%d tokens to department '%s'.", additional_tokens, dept)
        return self.budgets[dept]

    def generate_burn_report(self) -> str:
        """Produce an executive financial report for @pulse-ceo and Founder Ankit Singh."""
        lines = [
            "# CFO COMPUTE & TOKEN ECONOMICS REPORT",
            f"**Timestamp:** {time.strftime('%Y-%m-%d %H:%M:%S')}",
            "**Reporting Authority:** Chief Financial Officer (@pulse-cfo)",
            "",
            "| Department | Quota (Tokens) | Used (Tokens) | Utilization | Est. Spend (USD) | Status |",
            "|---|---|---|---|---|---|",
        ]
        total_tokens = 0
        total_usd = 0.0
        for dept, b in self.budgets.items():
            util = (b.used_tokens / max(1, b.quota_tokens)) * 100
            status = "🛑 FROZEN" if b.is_frozen else ("⚠️ WARNING" if b.warning_issued else "✅ NORMAL")
            lines.append(
                f"| `{dept.capitalize()}` | {b.quota_tokens:,} | {b.used_tokens:,} | {util:.1f}% | ${b.estimated_cost_usd:.2f} | {status} |"
            )
            total_tokens += b.used_tokens
            total_usd += b.estimated_cost_usd

        lines.extend([
            "",
            f"**Total Enterprise Token Burn:** {total_tokens:,} tokens",
            f"**Total Compute Expenditure:** ${total_usd:.2f} USD",
        ])
        return "\n".join(lines)
