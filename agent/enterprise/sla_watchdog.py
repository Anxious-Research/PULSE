"""Enterprise SLA Watchdog & Task Succession Engine.

Guarantees operational continuity when bots fail, freeze, or get laid off:
1. Deadlock & Stall Detection: Reclaims tasks abandoned by crashed or looping bots.
2. Automated Succession: When a bot is quarantined/fired by TalentLedger, reassigns
   all its open tasks to qualified peers or C-Suite supervisors.
3. SLA Escalation: Escalates long-blocked tasks directly to @pulse-ceo.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.sla_watchdog")

SUCCESSION_MAP: Dict[str, str] = {
    # Engineering -> CTO
    "vp-core-systems": "pulse-cto",
    "vp-ai-research": "pulse-cto",
    "vp-cognitive-memory": "pulse-cto",
    "vp-ml-platform": "pulse-cto",
    "vp-developer-platform": "pulse-cto",
    "vp-technical-docs": "pulse-cto",
    "vp-qa-verification": "pulse-cto",

    # Operations -> COO / CISO
    "vp-cloud-sre": "pulse-coo",
    "vp-release-engineering": "pulse-coo",
    "vp-infosec-redteam": "pulse-ciso",
    "vp-data-telemetry": "pulse-coo",

    # Product -> CPO
    "vp-product-pmo": "pulse-cpo",
    "vp-design-systems": "pulse-cpo",

    # C-Suite -> CEO
    "pulse-cto": "pulse-ceo",
    "pulse-cpo": "pulse-ceo",
    "pulse-ciso": "pulse-ceo",
    "pulse-coo": "pulse-ceo",
    "pulse-chro": "pulse-ceo",
    "pulse-cfo": "pulse-ceo",
    "pulse-general-counsel": "pulse-ceo",
}


class SLAWatchdog:
    """Monitors SLA deadlines and manages task succession."""

    def __init__(self, board_slug: str = "enterprise-main"):
        self.board = board_slug

    def reassign_tasks_for_quarantined_bot(self, quarantined_profile: str) -> List[Dict[str, Any]]:
        """Reassign all open tasks from a fired/quarantined bot to its succession successor."""
        successor = SUCCESSION_MAP.get(quarantined_profile, "pulse-ceo")
        reassigned = []

        # List open tasks assigned to quarantined bot
        cmd = ["pulse", "kanban", "--board", self.board, "list", "--assignee", quarantined_profile, "--json"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            tasks = json.loads(res.stdout) if res.stdout.strip() else []
            for t in tasks:
                tid = t.get("id")
                status = t.get("status")
                if status in ("done", "archived"):
                    continue

                # Reassign task
                reassign_cmd = [
                    "pulse", "kanban", "--board", self.board, "reassign",
                    "--task-id", tid,
                    "--to", successor,
                ]
                subprocess.run(reassign_cmd, capture_output=True, text=True, check=True)

                # Add succession audit comment
                comment_cmd = [
                    "pulse", "kanban", "--board", self.board, "comment",
                    "--task-id", tid,
                    "--body", f"⚠️ Automated Succession: Task reassigned from @{quarantined_profile} (Quarantined/Fired) to @{successor}.",
                ]
                subprocess.run(comment_cmd, capture_output=True, text=True, check=False)

                reassigned.append({"task_id": tid, "from": quarantined_profile, "to": successor})
                logger.warning("Task %s reassigned from @%s to successor @%s", tid, quarantined_profile, successor)

        except Exception as exc:
            logger.error("Failed to reassign tasks for %s: %s", quarantined_profile, exc)

        return reassigned

    def check_stalled_tasks(self, max_stalled_seconds: int = 1800) -> List[Dict[str, Any]]:
        """Check for tasks running with no progress and escalate if stalled."""
        escalated = []
        cmd = ["pulse", "kanban", "--board", self.board, "list", "--status", "running", "--json"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            running_tasks = json.loads(res.stdout) if res.stdout.strip() else []
            now = time.time()
            for t in running_tasks:
                updated_at = t.get("updated_at") or t.get("created_at") or now
                if now - updated_at > max_stalled_seconds:
                    tid = t.get("id")
                    # Reclaim stale task to ready
                    reclaim_cmd = ["pulse", "kanban", "--board", self.board, "reclaim", "--task-id", tid]
                    subprocess.run(reclaim_cmd, capture_output=True, text=True, check=False)

                    # Post escalation comment
                    comment_cmd = [
                        "pulse", "kanban", "--board", self.board, "comment",
                        "--task-id", tid,
                        "--body", f"⏱️ SLA Breach Watchdog: Task running > {max_stalled_seconds // 60}m without heartbeat. Reclaimed to ready.",
                    ]
                    subprocess.run(comment_cmd, capture_output=True, text=True, check=False)
                    escalated.append({"task_id": tid, "assignee": t.get("assignee"), "action": "reclaimed"})
                    logger.warning("SLA Watchdog reclaimed stalled task %s", tid)

        except Exception as exc:
            logger.debug("SLA watchdog check note: %s", exc)

        return escalated
