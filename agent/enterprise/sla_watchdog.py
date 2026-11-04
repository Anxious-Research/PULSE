"""Enterprise SLA Watchdog & Task Succession Engine.

Guarantees operational continuity when bots fail, freeze, or get laid off:
1. Deadlock & Stall Detection: Reclaims tasks abandoned by crashed or looping bots.
2. Automated Succession: When a bot is quarantined/fired by TalentLedger, reassigns
   all its open tasks to qualified peers or C-Suite supervisors.
3. SLA Escalation: Escalates long-blocked tasks directly to @pulse-ceo.
4. Circular Dependency Detection: Identifies and breaks wait-loops between bots.
5. Background Daemon: Continuous monitoring with configurable check intervals.
"""

from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.sla_watchdog")

# SLA thresholds (in seconds)
SLA_WARNING_THRESHOLD = 3600  # 1 hour
SLA_BREACH_THRESHOLD = 7200   # 2 hours (auto-escalate)
STALLED_TASK_THRESHOLD = 1800  # 30 minutes (reclaim)

# Department-to-bot mapping for peer succession
DEPARTMENT_ROSTER: Dict[str, List[str]] = {
    "engineering": [
        "vp-core-systems",
        "vp-ai-research",
        "vp-cognitive-memory",
        "vp-ml-platform",
        "vp-developer-platform",
        "vp-technical-docs",
        "vp-qa-verification",
    ],
    "operations": [
        "vp-cloud-sre",
        "vp-release-engineering",
        "vp-data-telemetry",
    ],
    "security": ["vp-infosec-redteam"],
    "product": ["vp-product-pmo", "vp-design-systems"],
    "executive": [
        "pulse-ceo",
        "pulse-cto",
        "pulse-cpo",
        "pulse-ciso",
        "pulse-coo",
        "pulse-chro",
        "pulse-cfo",
        "pulse-general-counsel",
    ],
}

# Succession hierarchy: fallback when no peer available
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

BOT_TO_DEPARTMENT: Dict[str, str] = {}
for dept, bots in DEPARTMENT_ROSTER.items():
    for bot in bots:
        BOT_TO_DEPARTMENT[bot] = dept


@dataclass
class TaskDependency:
    """Represents a task waiting on another task or bot."""
    task_id: str
    assignee: str
    waiting_on: Optional[str] = None  # Another bot profile or task ID


class CircularDependencyDetector:
    """Detects circular wait conditions between bots."""

    def __init__(self):
        self.graph: Dict[str, Set[str]] = defaultdict(set)

    def add_dependency(self, bot_a: str, waits_on_bot_b: str) -> None:
        """Record that bot_a is waiting on bot_b."""
        self.graph[bot_a].add(waits_on_bot_b)

    def detect_cycles(self) -> List[List[str]]:
        """Detect all circular dependency chains using DFS."""
        cycles = []
        visited = set()
        rec_stack = set()

        def dfs(node: str, path: List[str]) -> None:
            if node in rec_stack:
                # Found a cycle
                cycle_start = path.index(node)
                cycle = path[cycle_start:] + [node]
                cycles.append(cycle)
                return
            if node in visited:
                return

            visited.add(node)
            rec_stack.add(node)
            path.append(node)

            for neighbor in self.graph.get(node, []):
                dfs(neighbor, path[:])

            rec_stack.remove(node)

        for node in list(self.graph.keys()):
            if node not in visited:
                dfs(node, [])

        return cycles

    def clear(self) -> None:
        """Reset the dependency graph."""
        self.graph.clear()


class SLAWatchdog:
    """Monitors SLA deadlines, detects deadlocks, and manages task succession."""

    def __init__(self, board_slug: str = "enterprise-main"):
        self.board = board_slug
        self.running = False
        self.daemon_thread: Optional[threading.Thread] = None
        self.check_interval = 60  # Check every 60 seconds
        self.dependency_detector = CircularDependencyDetector()

    def get_bot_department(self, profile: str) -> str:
        """Determine the department for a given bot profile."""
        return BOT_TO_DEPARTMENT.get(profile, "general")

    def find_peer_successor(self, profile: str, exclude_quarantined: Optional[List[str]] = None) -> str:
        """Find an active peer from the same department, or escalate to supervisor."""
        dept = self.get_bot_department(profile)
        peers = DEPARTMENT_ROSTER.get(dept, [])
        exclude_set = set(exclude_quarantined or [])

        # Try to find an active peer
        for peer in peers:
            if peer != profile and peer not in exclude_set:
                return peer

        # No peer available, escalate to hierarchy
        return SUCCESSION_MAP.get(profile, "pulse-ceo")

    def reassign_tasks_for_quarantined_bot(
        self, quarantined_profile: str, reason: str = "Quarantined/Fired"
    ) -> List[Dict[str, Any]]:
        """Reassign all open tasks from a fired/quarantined bot to department peer or supervisor."""
        successor = self.find_peer_successor(quarantined_profile)
        reassigned = []

        # List open tasks assigned to quarantined bot
        cmd = ["pulse", "kanban", "--board", self.board, "list", "--assignee", quarantined_profile, "--json"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            tasks = json.loads(res.stdout) if res.stdout.strip() else []
            for t in tasks:
                tid = t.get("id")
                status = t.get("status")
                if status in ("done", "archived"):
                    continue

                # Reassign task
                reassign_cmd = [
                    "pulse",
                    "kanban",
                    "--board",
                    self.board,
                    "reassign",
                    "--task-id",
                    tid,
                    "--to",
                    successor,
                ]
                subprocess.run(reassign_cmd, capture_output=True, text=True, check=True, timeout=10)

                # Add succession audit comment
                comment_cmd = [
                    "pulse",
                    "kanban",
                    "--board",
                    self.board,
                    "comment",
                    "--task-id",
                    tid,
                    "--body",
                    f"⚠️ Automated Succession: Task reassigned from @{quarantined_profile} ({reason}) to @{successor}.",
                ]
                subprocess.run(comment_cmd, capture_output=True, text=True, check=False, timeout=10)

                reassigned.append({"task_id": tid, "from": quarantined_profile, "to": successor, "reason": reason})
                logger.warning("Task %s reassigned from @%s to @%s (%s)", tid, quarantined_profile, successor, reason)

        except subprocess.TimeoutExpired:
            logger.error("Timeout while reassigning tasks for %s", quarantined_profile)
        except Exception as exc:
            logger.error("Failed to reassign tasks for %s: %s", quarantined_profile, exc)

        return reassigned

    def check_stalled_tasks(self, max_stalled_seconds: int = STALLED_TASK_THRESHOLD) -> List[Dict[str, Any]]:
        """Check for tasks running with no progress and reclaim if stalled."""
        escalated = []
        cmd = ["pulse", "kanban", "--board", self.board, "list", "--status", "running", "--json"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            running_tasks = json.loads(res.stdout) if res.stdout.strip() else []
            now = time.time()
            for t in running_tasks:
                updated_at = t.get("updated_at") or t.get("created_at") or now
                age_seconds = now - updated_at

                if age_seconds > max_stalled_seconds:
                    tid = t.get("id")
                    assignee = t.get("assignee", "unknown")

                    # Reclaim stale task to ready
                    reclaim_cmd = ["pulse", "kanban", "--board", self.board, "reclaim", "--task-id", tid]
                    subprocess.run(reclaim_cmd, capture_output=True, text=True, check=False, timeout=10)

                    # Post escalation comment
                    comment_cmd = [
                        "pulse",
                        "kanban",
                        "--board",
                        self.board,
                        "comment",
                        "--task-id",
                        tid,
                        "--body",
                        f"⏱️ SLA Watchdog: Task running > {max_stalled_seconds // 60}m without heartbeat. Reclaimed to ready.",
                    ]
                    subprocess.run(comment_cmd, capture_output=True, text=True, check=False, timeout=10)
                    escalated.append(
                        {"task_id": tid, "assignee": assignee, "age_seconds": age_seconds, "action": "reclaimed"}
                    )
                    logger.warning("SLA Watchdog reclaimed stalled task %s (age: %ds)", tid, age_seconds)

        except subprocess.TimeoutExpired:
            logger.error("Timeout while checking stalled tasks")
        except Exception as exc:
            logger.debug("SLA watchdog check note: %s", exc)

        return escalated

    def check_sla_breaches(self) -> List[Dict[str, Any]]:
        """Check for tasks breaching 2-hour SLA and escalate to CEO/CTO."""
        breached = []
        cmd = ["pulse", "kanban", "--board", self.board, "list", "--status", "running", "--json"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            running_tasks = json.loads(res.stdout) if res.stdout.strip() else []
            now = time.time()

            for t in running_tasks:
                created_at = t.get("created_at") or now
                age_seconds = now - created_at
                tid = t.get("id")
                assignee = t.get("assignee", "unknown")

                if age_seconds > SLA_BREACH_THRESHOLD:
                    # Escalate to CEO or CTO based on department
                    dept = self.get_bot_department(assignee)
                    escalate_to = "pulse-cto" if dept == "engineering" else "pulse-ceo"

                    # Reassign to executive
                    reassign_cmd = [
                        "pulse",
                        "kanban",
                        "--board",
                        self.board,
                        "reassign",
                        "--task-id",
                        tid,
                        "--to",
                        escalate_to,
                    ]
                    subprocess.run(reassign_cmd, capture_output=True, text=True, check=True, timeout=10)

                    # Add escalation audit comment
                    comment_cmd = [
                        "pulse",
                        "kanban",
                        "--board",
                        self.board,
                        "comment",
                        "--task-id",
                        tid,
                        "--body",
                        f"🚨 SLA BREACH: Task exceeded {SLA_BREACH_THRESHOLD // 3600}h deadline. Auto-escalated from @{assignee} to @{escalate_to}.",
                    ]
                    subprocess.run(comment_cmd, capture_output=True, text=True, check=False, timeout=10)

                    breached.append(
                        {
                            "task_id": tid,
                            "original_assignee": assignee,
                            "escalated_to": escalate_to,
                            "age_hours": age_seconds / 3600,
                        }
                    )
                    logger.critical("SLA BREACH: Task %s escalated to @%s after %.1fh", tid, escalate_to, age_seconds / 3600)

        except subprocess.TimeoutExpired:
            logger.error("Timeout while checking SLA breaches")
        except Exception as exc:
            logger.error("Failed to check SLA breaches: %s", exc)

        return breached

    def detect_circular_dependencies(self) -> List[List[str]]:
        """Scan running tasks for circular wait conditions between bots."""
        self.dependency_detector.clear()
        cmd = ["pulse", "kanban", "--board", self.board, "list", "--status", "running", "--json"]

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=10)
            running_tasks = json.loads(res.stdout) if res.stdout.strip() else []

            # Build dependency graph from task metadata
            for t in running_tasks:
                assignee = t.get("assignee")
                # Check for "blocked_by" or "waiting_on" fields in task metadata
                body = t.get("body", "")
                # Simple heuristic: look for @mentions indicating dependencies
                if assignee and "@" in body:
                    # Extract mentioned bots (waiting dependencies)
                    import re
                    mentions = re.findall(r"@([\w\-]+)", body)
                    for mentioned_bot in mentions:
                        if mentioned_bot != assignee and mentioned_bot in BOT_TO_DEPARTMENT:
                            self.dependency_detector.add_dependency(assignee, mentioned_bot)

            cycles = self.dependency_detector.detect_cycles()
            if cycles:
                logger.warning("Circular dependencies detected: %s", cycles)
                # Break cycles by reassigning one task in each cycle to CEO
                for cycle in cycles:
                    if len(cycle) > 1:
                        # Find tasks assigned to first bot in cycle
                        victim_bot = cycle[0]
                        victim_tasks = [t for t in running_tasks if t.get("assignee") == victim_bot]
                        if victim_tasks:
                            victim_task = victim_tasks[0]
                            tid = victim_task.get("id")
                            reassign_cmd = [
                                "pulse",
                                "kanban",
                                "--board",
                                self.board,
                                "reassign",
                                "--task-id",
                                tid,
                                "--to",
                                "pulse-ceo",
                            ]
                            subprocess.run(reassign_cmd, capture_output=True, text=True, check=True, timeout=10)
                            comment_cmd = [
                                "pulse",
                                "kanban",
                                "--board",
                                self.board,
                                "comment",
                                "--task-id",
                                tid,
                                "--body",
                                f"🔄 Circular Dependency Detected: {' → '.join(cycle)}. Breaking cycle by reassigning to @pulse-ceo.",
                            ]
                            subprocess.run(comment_cmd, capture_output=True, text=True, check=False, timeout=10)
                            logger.warning("Broke circular dependency by reassigning task %s to CEO", tid)

            return cycles

        except subprocess.TimeoutExpired:
            logger.error("Timeout while detecting circular dependencies")
        except Exception as exc:
            logger.debug("Circular dependency check note: %s", exc)

        return []

    def run_watchdog_cycle(self) -> Dict[str, Any]:
        """Execute one full watchdog check cycle."""
        logger.debug("SLA Watchdog: Starting check cycle")
        results = {
            "timestamp": time.time(),
            "stalled_tasks": [],
            "sla_breaches": [],
            "circular_dependencies": [],
        }

        try:
            # 1. Check for stalled tasks (30min+ no update)
            results["stalled_tasks"] = self.check_stalled_tasks()

            # 2. Check for SLA breaches (2hr+ running)
            results["sla_breaches"] = self.check_sla_breaches()

            # 3. Detect circular dependencies
            results["circular_dependencies"] = self.detect_circular_dependencies()

        except Exception as exc:
            logger.error("Watchdog cycle error: %s", exc)

        return results

    def daemon_loop(self) -> None:
        """Background daemon loop running watchdog checks."""
        logger.info("SLA Watchdog daemon started (interval: %ds)", self.check_interval)
        while self.running:
            try:
                self.run_watchdog_cycle()
            except Exception as exc:
                logger.error("Watchdog daemon error: %s", exc)

            # Sleep in small increments to allow quick shutdown
            for _ in range(self.check_interval):
                if not self.running:
                    break
                time.sleep(1)

        logger.info("SLA Watchdog daemon stopped")

    def start_daemon(self, check_interval: int = 60) -> None:
        """Start the background watchdog daemon thread."""
        if self.running:
            logger.warning("Watchdog daemon already running")
            return

        self.check_interval = check_interval
        self.running = True
        self.daemon_thread = threading.Thread(target=self.daemon_loop, daemon=True, name="SLAWatchdog")
        self.daemon_thread.start()
        logger.info("SLA Watchdog daemon thread started")

    def stop_daemon(self) -> None:
        """Stop the background watchdog daemon."""
        if not self.running:
            return

        logger.info("Stopping SLA Watchdog daemon...")
        self.running = False
        if self.daemon_thread:
            self.daemon_thread.join(timeout=5)
        logger.info("SLA Watchdog daemon stopped")


# Convenience functions for external invocation

def start_watchdog_daemon(board_slug: str = "enterprise-main", check_interval: int = 60) -> SLAWatchdog:
    """Start the SLA watchdog as a background daemon."""
    watchdog = SLAWatchdog(board_slug)
    watchdog.start_daemon(check_interval)
    return watchdog


def run_watchdog_once(board_slug: str = "enterprise-main") -> Dict[str, Any]:
    """Run a single watchdog check cycle (for cron/manual invocation)."""
    watchdog = SLAWatchdog(board_slug)
    return watchdog.run_watchdog_cycle()


if __name__ == "__main__":
    # CLI entry point for manual testing
    import sys

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")

    if len(sys.argv) > 1 and sys.argv[1] == "daemon":
        # Run as daemon
        interval = int(sys.argv[2]) if len(sys.argv) > 2 else 60
        watchdog = start_watchdog_daemon(check_interval=interval)
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            watchdog.stop_daemon()
    else:
        # Run once
        results = run_watchdog_once()
        print(json.dumps(results, indent=2))
