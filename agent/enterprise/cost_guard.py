"""Enterprise Cost Guard & Inference Circuit Breaker Engine.

Protects against runaway inference loops and catastrophic token spend:
- Enforces strict rolling hourly token caps and daily USD cost ceilings.
- Automatically trips the circuit breaker to freeze autonomous execution if breached.
- Pervasive incident generation to alert @pulse-cfo and Founder Ankit Singh.
- Requires explicit CFO authorization to reset or raise limits.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.cost_guard")

CIRCUIT_BREAKER_FILENAME = "cost_circuit_breaker.json"

DEFAULT_HOURLY_TOKEN_LIMIT = 500_000   # 500k tokens per hour
DEFAULT_DAILY_USD_LIMIT = 15.0         # $15.00 USD per day


@dataclass
class CostGuardState:
    hourly_token_limit: int = DEFAULT_HOURLY_TOKEN_LIMIT
    daily_usd_limit: float = DEFAULT_DAILY_USD_LIMIT
    current_hour_tokens: int = 0
    current_hour_timestamp: float = field(default_factory=time.time)
    current_day_usd: float = 0.0
    current_day_timestamp: float = field(default_factory=time.time)
    tripped: bool = False
    tripped_reason: str = ""
    tripped_at: Optional[float] = None
    reset_history: list[str] = field(default_factory=list)


class CostGuard:
    """Hardware and execution circuit breaker for autonomous multi-agent systems."""

    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            self.dir = get_pulse_home() / "enterprise"
        else:
            self.dir = Path(storage_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / CIRCUIT_BREAKER_FILENAME
        self.state = CostGuardState()
        self.load()

    def load(self) -> None:
        if not self.file.exists():
            return
        try:
            data = json.loads(self.file.read_text(encoding="utf-8"))
            self.state = CostGuardState(**data)
        except Exception as exc:
            logger.warning("Could not load cost guard state: %s", exc)

    def save(self) -> None:
        try:
            data = asdict(self.state)
            self.file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save cost guard state: %s", exc)

    def _refresh_windows(self) -> None:
        now = time.time()
        # Reset hourly window if > 3600s
        if now - self.state.current_hour_timestamp >= 3600:
            self.state.current_hour_tokens = 0
            self.state.current_hour_timestamp = now

        # Reset daily window if > 86400s
        if now - self.state.current_day_timestamp >= 86400:
            self.state.current_day_usd = 0.0
            self.state.current_day_timestamp = now

    def check_allow_execution(self, estimated_tokens: int = 1000) -> tuple[bool, str]:
        """Check whether the agent is allowed to execute or if the circuit breaker is tripped."""
        self._refresh_windows()

        if self.state.tripped:
            return False, f"🛑 CIRCUIT BREAKER TRIPPED: {self.state.tripped_reason}. Execution halted."

        if self.state.current_hour_tokens + estimated_tokens > self.state.hourly_token_limit:
            self.trip(f"Hourly token limit exceeded ({self.state.current_hour_tokens:,} / {self.state.hourly_token_limit:,})")
            return False, f"🛑 CIRCUIT BREAKER TRIPPED: Hourly token limit reached."

        if self.state.current_day_usd >= self.state.daily_usd_limit:
            self.trip(f"Daily cost limit exceeded (${self.state.current_day_usd:.2f} / ${self.state.daily_usd_limit:.2f})")
            return False, f"🛑 CIRCUIT BREAKER TRIPPED: Daily USD cost limit reached."

        return True, "Execution permitted."

    def record_spend(self, tokens: int, cost_per_m_tokens: float = 3.0) -> None:
        """Record live token usage and compute cost."""
        self._refresh_windows()
        usd = (tokens / 1_000_000) * cost_per_m_tokens

        self.state.current_hour_tokens += tokens
        self.state.current_day_usd += usd

        # Check breach
        if self.state.current_hour_tokens > self.state.hourly_token_limit:
            self.trip(f"Hourly token quota breached ({self.state.current_hour_tokens:,} tokens)")
        elif self.state.current_day_usd > self.state.daily_usd_limit:
            self.trip(f"Daily cost ceiling breached (${self.state.current_day_usd:.2f})")

        self.save()

    def trip(self, reason: str) -> None:
        """Trip the circuit breaker, freezing all background autonomous loops."""
        self.state.tripped = True
        self.state.tripped_reason = reason
        self.state.tripped_at = time.time()
        self.save()
        logger.critical("🛑 [COST GUARD] EMERGENCY FREEZE: %s", reason)

    def reset(self, authorized_by: str, increase_hourly_tokens: Optional[int] = None) -> bool:
        """Reset the breaker. Only CFO or Sovereign Founder Ankit Singh may authorize."""
        auth_clean = authorized_by.lower().strip()
        if "cfo" not in auth_clean and "ankit" not in auth_clean and "ceo" not in auth_clean:
            logger.warning("Unauthorized reset attempt by %s rejected.", authorized_by)
            return False

        self.state.tripped = False
        self.state.tripped_reason = ""
        self.state.tripped_at = None
        if increase_hourly_tokens:
            self.state.hourly_token_limit += increase_hourly_tokens

        note = f"[{time.strftime('%Y-%m-%d %H:%M')}] Reset by {authorized_by}"
        self.state.reset_history.append(note)
        self.save()
        logger.info("Cost circuit breaker reset successfully by %s.", authorized_by)
        return True
