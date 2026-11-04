"""Master Autonomous Continuous Collaboration & Governance Loop.

Orchestrates full enterprise self-management when human is not present:
1. Cost Guard Circuit Breaker: Prevents runaway token burn / wallet drain.
2. Kanban Dispatcher: Continuously claims and executes ready tasks on enterprise-main.
3. Channel Watcher: Facilitates autonomous cross-bot debates and @mention replies.
4. Automated Gatekeeper & Git Merger: Runs QA tests, CISO security scan, CTO sign-off,
   and physically executes the git merge into main and pushes to GitHub.
5. Talent Performance: Live PIP calibration and stack ranking.
6. SLA Watchdog: Deadlock breaker and automatic task succession for quarantined bots.
7. Anti-Amnesia Memory: Persists architectural milestones and ADRs into Brain Vault.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home
from .channel_watcher import ChannelWatcher
from .gatekeeper import EnterpriseGatekeeper, ReviewVerdict
from .git_merger import GitMerger
from .cost_guard import CostGuard
from .verifier import ArtifactVerifier
from .sla_watchdog import SLAWatchdog
from .talent_ops import TalentLedger
from .rfc_system import RFCEngine
from .cfo_budget import CFOBudgetEngine

logger = logging.getLogger("enterprise.autonomous_loop")


class AutonomousEnterpriseLoop:
    """Master engine executing the corporate loop continuously."""

    def __init__(self, board_slug: str = "enterprise-main"):
        self.board = board_slug
        self.watcher = ChannelWatcher()
        self.gatekeeper = EnterpriseGatekeeper()
        self.merger = GitMerger()
        self.cost_guard = CostGuard()
        self.sla = SLAWatchdog(board_slug=board_slug)
        self.talent = TalentLedger()
        self.rfc = RFCEngine()
        self.cfo = CFOBudgetEngine()

    def tick(self) -> Dict[str, Any]:
        """Execute one complete enterprise orchestration tick."""
        tick_results = {
            "timestamp": time.time(),
            "cost_guard_allowed": True,
            "kanban_dispatch": None,
            "debates_dispatched": [],
            "gates_verified": [],
            "merges_executed": [],
            "sla_escalations": [],
            "rfcs_synced_to_brain": [],
            "errors": [],
        }

        # 0. Check Cost Guard Circuit Breaker
        allowed, reason = self.cost_guard.check_allow_execution()
        tick_results["cost_guard_allowed"] = allowed
        if not allowed:
            tick_results["errors"].append(reason)
            logger.warning("[AUTONOMOUS LOOP] %s", reason)
            return tick_results

        # 1. SLA Watchdog Pass (Reclaim stalled tasks & succession)
        try:
            escalated = self.sla.check_stalled_tasks(max_stalled_seconds=1800)
            tick_results["sla_escalations"] = escalated
        except Exception as exc:
            tick_results["errors"].append(f"SLA watchdog check failed: {exc}")

        # 2. Kanban Auto-Dispatch Pass
        try:
            cmd = ["pulse", "kanban", "--board", self.board, "dispatch", "--max", "2", "--json"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            tick_results["kanban_dispatch"] = res.stdout.strip()
        except Exception as exc:
            tick_results["errors"].append(f"Kanban dispatch failed: {exc}")

        # 2.5 Proactive Channel Dialogue Activity
        try:
            self._ensure_channel_dialogue_activity()
        except Exception as exc:
            tick_results["errors"].append(f"Channel dialogue initiator failed: {exc}")

        # 3. Channel Watcher Pass (Autonomous Bot-to-Bot Debate)
        try:
            debates = self.watcher.check_and_dispatch_debates(max_dispatches_per_pass=2)
            tick_results["debates_dispatched"] = debates
        except Exception as exc:
            tick_results["errors"].append(f"Channel watcher failed: {exc}")

        # 4. Automated Gatekeeper & Real Git Merge Pass
        try:
            verified_gates, merges = self._process_pending_review_gates_and_merges()
            tick_results["gates_verified"] = verified_gates
            tick_results["merges_executed"] = merges
        except Exception as exc:
            tick_results["errors"].append(f"Gatekeeper/Merge pass failed: {exc}")

        # 5. Anti-Amnesia Memory Sync (Persist Approved RFCs & Milestones into Brain Vault)
        try:
            synced_rfcs = self._sync_approved_rfcs_to_brain()
            tick_results["rfcs_synced_to_brain"] = synced_rfcs
        except Exception as exc:
            tick_results["errors"].append(f"Brain memory sync failed: {exc}")

        return tick_results

    def _ensure_channel_dialogue_activity(self) -> None:
        """Ensure channels have active discussions rather than standing in total silence."""
        now = time.time()
        # 1. C-Suite Boardroom Check
        csuite_msgs = self.watcher.channels.get_messages("c-suite-boardroom", limit=5)
        last_time = csuite_msgs[-1].timestamp if csuite_msgs else 0
        if now - last_time > 1800:  # 30 minutes of silence
            self.watcher.channels.post_message(
                channel="c-suite-boardroom",
                sender_profile="pulse-ceo",
                content=(
                    "🏛️ **[EXECUTIVE STANDUP]** @pulse-cto please report on engineering sprint velocity "
                    "and core architecture health. @pulse-cfo are our token consumption budgets within Q4 limits?"
                ),
            )
            logger.info("CEO initiated executive standup debate in #c-suite-boardroom.")

        # 2. Engineering Architecture Check
        eng_msgs = self.watcher.channels.get_messages("eng-architecture", limit=5)
        last_eng_time = eng_msgs[-1].timestamp if eng_msgs else 0
        if now - last_eng_time > 1800:
            under_review = [d for d in self.rfc.rfcs.values() if d.status == "under_review"]
            if under_review:
                target_rfc = under_review[0]
                self.watcher.channels.post_message(
                    channel="eng-architecture",
                    sender_profile="pulse-cto",
                    content=(
                        f"⚙️ **[ARCHITECTURE SYNC]** {target_rfc.rfc_id} ({target_rfc.title}) is UNDER_REVIEW. "
                        f"@vp-core-systems and @vp-cognitive-memory please submit your architectural reviews."
                    ),
                )
            else:
                self.watcher.channels.post_message(
                    channel="eng-architecture",
                    sender_profile="pulse-cto",
                    content=(
                        "⚙️ **[ENGINEERING SYNC]** @vp-core-systems what is our current test coverage and build pipeline status? "
                        "@vp-cloud-sre any latency spikes on persistent memory recall?"
                    ),
                )
            logger.info("CTO initiated engineering sync in #eng-architecture.")

    def _sync_approved_rfcs_to_brain(self) -> List[str]:
        """Archive approved enterprise RFCs into Brain Vault project nodes to prevent context amnesia."""
        synced = []
        brain_proj_dir = get_pulse_home() / "brain" / "project"
        brain_proj_dir.mkdir(parents=True, exist_ok=True)

        for rfc_id, doc in self.rfc.rfcs.items():
            if doc.status in ("approved", "implemented"):
                slug = doc.title.lower().replace(" ", "-").replace("/", "-")
                node_file = brain_proj_dir / f"{rfc_id.lower()}-{slug}.md"
                if not node_file.exists():
                    content = (
                        f"# Architecture Decision Record: {doc.title} ({doc.rfc_id})\n\n"
                        f"**Author**: @{doc.author}  \n"
                        f"**Status**: `{doc.status.upper()}`  \n"
                        f"**Approved By**: {', '.join(f'@{k}' for k, v in doc.approvals.items() if v)}  \n\n"
                        f"## Architectural Invariants\n{doc.technical_design}\n\n"
                        f"## Security & Governance Guardrails\n{doc.security_considerations}\n"
                    )
                    node_file.write_text(content, encoding="utf-8")
                    synced.append(rfc_id)
                    logger.info("Archived %s into permanent Brain Vault project node %s", rfc_id, node_file.name)
        return synced

    def _process_pending_review_gates_and_merges(self) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Process tasks waiting for automated QA, Security, or Architectural sign-offs, and merge verified code."""
        verified = []
        merges = []

        for task_id, gate in list(self.gatekeeper.gates.items()):
            if gate.merged:
                continue

            # Phase 1: Automated QA check
            if gate.qa_status == "pending":
                qa_ok = self.gatekeeper.run_qa_check(task_id, test_command="pytest -q tests/test_enterprise*.py")
                logger.info("Task %s QA check outcome: %s", task_id, "PASS" if qa_ok else "FAIL")

            # Phase 2: Security check
            if gate.security_status == "pending":
                sec_ok = self.gatekeeper.run_security_check(task_id)
                logger.info("Task %s Security check outcome: %s", task_id, "PASS" if sec_ok else "FAIL")

            # Phase 3: Architectural sign-off if both passed
            if gate.qa_status == "passed" and gate.security_status == "passed" and gate.architect_verdict == "pending":
                sign_res = self.gatekeeper.sign_off(
                    task_id=task_id,
                    reviewer_profile="pulse-cto",
                    verdict=ReviewVerdict.APPROVED,
                    notes="Automated QA and Security certifications passed cleanly.",
                )
                verified.append(sign_res)

                # Phase 4: Physical Git Merge & Remote Push
                if sign_res.get("can_merge"):
                    merge_res = self.merger.merge_task_branch(
                        task_id=task_id,
                        commit_message=f"feat({task_id}): certified merge by @pulse-cto",
                        push_to_remote=False,  # Set False in local worker loop; manual push on milestone
                    )
                    merges.append(merge_res)
                    logger.info("Task %s physically merged into main: %s", task_id, merge_res.get("success"))

                    # Phase 5: Update Talent Ledger for Author
                    self.talent.record_task_outcome(
                        profile=gate.author_profile,
                        task_id=task_id,
                        success=merge_res.get("success", False),
                        reason="Task certified and merged into main.",
                    )

        return verified, merges

    def run_forever(self, interval_seconds: int = 15, max_ticks: Optional[int] = None) -> None:
        """Run the enterprise autonomous loop indefinitely in background."""
        logger.info("Starting Autonomous Enterprise Loop on board '%s' (interval=%ds)...", self.board, interval_seconds)
        ticks = 0
        while True:
            try:
                res = self.tick()
                debates_count = len(res["debates_dispatched"])
                gates_count = len(res["gates_verified"])
                merges_count = len(res["merges_executed"])
                if debates_count > 0 or gates_count > 0 or merges_count > 0:
                    logger.info("Tick %d complete: %d debates, %d gates, %d merges.", ticks + 1, debates_count, gates_count, merges_count)
            except Exception as exc:
                logger.error("Error during enterprise tick: %s", exc)

            ticks += 1
            if max_ticks is not None and ticks >= max_ticks:
                logger.info("Reached max_ticks=%d. Halting loop.", max_ticks)
                break

            time.sleep(interval_seconds)
