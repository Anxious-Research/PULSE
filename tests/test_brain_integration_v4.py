"""Integration tests: user model → LLM turn context, and intention → scheduler (Brain v4 P0s).

These exercise the PRODUCTION paths, not internal helpers:

* User model (§5.3): a fact captured by ``brain_encode_turn`` must appear in the block
  that ``brain_turn_context`` — the real per-turn channel — assembles; after a correction,
  a fresh session (new agent object) must see the corrected state; the original evidence
  stays inspectable as a superseded node.
* Prospective → scheduler (§6.4): a time-triggered intention is scheduled through the real
  cron store on capture, does not fire early, is removed on completion or cancellation,
  survives restart (vault + cron store on disk), and duplicate syncs do not duplicate jobs.

Cron storage is routed to a temp dir via ``cron.jobs.use_cron_store`` — the same mechanism
the cron test suite uses — so no real cron store or PULSE home is touched.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent.brain import prospects
from agent.brain import prospect_scheduler
from agent.brain.session import brain_encode_turn, brain_turn_context
from agent.brain.vault import BrainVault


class _Agent:
    """Minimal agent surface for brain_turn_context / brain_encode_turn.

    Duck-typed like the agent stubs in tests/test_brain_prospects.py.
    """

    def __init__(self, vault_dir: Path):
        self.session_id = "sess_test_1"
        self._agent_config = {"brain": {"enabled": True}}
        self._vault_dir = vault_dir
        self._brain_vault = None
        self._brain_index_cache = None
        self._brain_prefix_cache = None


def _make_vault(tmp: Path) -> BrainVault:
    vault = BrainVault(vault_dir=tmp)
    vault.ensure_vault_structure()
    return vault


def _make_agent(tmp: Path, vault: BrainVault) -> _Agent:
    agent = _Agent(tmp)
    agent._brain_vault = vault
    return agent


class UserModelContextIntegration(unittest.TestCase):
    """§5.3: capture → persist → fresh session → current state guides context."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-um-e2e-"))
        self.vault = _make_vault(self.tmp)
        self.agent = _make_agent(self.tmp, self.vault)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fresh_session_agent(self) -> _Agent:
        """A brand-new agent object on the same vault — what an app restart produces."""
        return _make_agent(self.tmp, self.vault)

    def test_preference_reaches_fresh_session_context(self):
        report = brain_encode_turn(
            self.agent,
            "I really prefer concise answers over long explanations",
            session_id="sess-A",
        )
        self.assertIsInstance(report, dict)

        ctx = brain_turn_context(self._fresh_session_agent(), "how should I format my replies")
        self.assertIn("concise answers", ctx, "explicit preference must reach turn context")
        self.assertIn("User model", ctx)

    def test_corrected_preference_replaces_stale_in_context(self):
        brain_encode_turn(self.agent, "I prefer dark mode for all interfaces", session_id="s1")
        brain_encode_turn(self.agent, "Actually I now prefer light mode everywhere",
                          session_id="sess-corr")

        ctx = brain_turn_context(self._fresh_session_agent(), "which theme should you use for me")
        self.assertIn("light mode", ctx, "corrected state must guide context")
        self.assertNotIn("prefer dark mode", ctx, "superseded fact must not read as current")

        superseded = [n for n in self.vault.list_all_nodes()
                      if n.frontmatter.status == "superseded"]
        self.assertTrue(
            any("dark mode" in (n.content or "") for n in superseded),
            "original fact must remain inspectable as history",
        )

    def test_no_user_model_block_when_vault_empty(self):
        ctx = brain_turn_context(self._fresh_session_agent(), "explain merge sort")
        self.assertNotIn("User model", ctx, "empty vault must not inject a model block")


class ProspectSchedulerIntegration(unittest.TestCase):
    """§6.4: intention → cron store → due time → cancellation/completion semantics."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-prosp-sched-"))
        self.vault = _make_vault(self.tmp)
        from cron import jobs as cron_jobs_mod

        self._jobs = cron_jobs_mod
        self._store_ctx = cron_jobs_mod.use_cron_store(self.tmp / "cron")
        self._store_ctx.__enter__()
        self.now = int(time.time())

    def tearDown(self):
        try:
            self._store_ctx.__exit__(None, None, None)
        except Exception:
            pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _capture(self, text: str, *, due: int, event: str = "evt-1"):
        return prospects.capture_intention(
            self.vault, text, due=due, source_event=event, now=self.now,
        )

    def test_capture_schedules_one_shot_job(self):
        due = self.now + 3600
        result = self._capture("send the invoice", due=due)
        self.assertTrue(result["ok"])
        intention_id = result["intention_id"]

        jobs = self._jobs.list_jobs()
        self.assertEqual(len(jobs), 1, f"expected exactly one reminder job, got {len(jobs)}")
        job = jobs[0]
        self.assertEqual(job["schedule"]["kind"], "once")
        self.assertIn("send the invoice", job["prompt"])

        # The job id is persisted on the intention node (idempotency key).
        node = self.vault.read_node(intention_id)
        self.assertTrue(node.frontmatter.extra.get("cron_job_id"))

    def test_future_commitment_does_not_fire_early(self):
        due = self.now + 7200
        self._capture("renew the domain", due=due)
        due_now = self._jobs.get_due_jobs()
        self.assertEqual(due_now, [], "a future intention must not be due now")

    def test_reschedule_moves_job_without_duplicating(self):
        due1 = self.now + 3600
        result = self._capture("prepare the report", due=due1)
        intention_id = result["intention_id"]
        job_id_1 = self.vault.read_node(intention_id).frontmatter.extra.get("cron_job_id")

        # Reschedule the same intention to a new due time.
        node = self.vault.read_node(intention_id)
        extra = dict(node.frontmatter.extra or {})
        due2 = self.now + 7200
        extra["due"] = due2
        self.vault.write_node(intention_id, node.content, extra=extra)
        report = prospect_scheduler.sync_intention_job(self.vault, intention_id, now=self.now)

        self.assertIn(report["action"], ("updated", "exists"))
        jobs = self._jobs.list_jobs()
        self.assertEqual(len(jobs), 1, "reschedule must move the job, not add one")
        self.assertEqual(jobs[0]["id"], job_id_1)

    def test_duplicate_sync_is_idempotent(self):
        due = self.now + 3600
        result = self._capture("back up the server", due=due)
        intention_id = result["intention_id"]

        for _ in range(3):
            prospect_scheduler.sync_intention_job(self.vault, intention_id, now=self.now)

        self.assertEqual(len(self._jobs.list_jobs()), 1, "repeated syncs must not duplicate jobs")

    def test_completion_removes_job(self):
        due = self.now + 3600
        result = self._capture("submit the tax form", due=due)
        intention_id = result["intention_id"]
        self.assertEqual(len(self._jobs.list_jobs()), 1)

        done = prospects.complete_intention(self.vault, intention_id, evidence_ref="evt-done")
        self.assertTrue(done["ok"])
        self.assertEqual(self._jobs.list_jobs(), [], "completed commitment must not fire later")

    def test_cancellation_removes_job(self):
        due = self.now + 3600
        result = self._capture("call the dentist", due=due)
        intention_id = result["intention_id"]

        cancelled = prospects.cancel_intention(self.vault, intention_id, reason="no longer needed")
        self.assertTrue(cancelled["ok"])
        self.assertEqual(self._jobs.list_jobs(), [], "cancelled commitment must not fire later")

    def test_condition_trigger_never_scheduled(self):
        # No due time → unresolved condition. Must not invent a schedule.
        result = prospects.capture_intention(
            self.vault, "maybe look into vector databases", source_event="evt-c", now=self.now,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["trigger"], "condition")
        self.assertEqual(self._jobs.list_jobs(), [], "unresolved condition must not be scheduled")

    def test_survives_restart(self):
        due = self.now + 3600
        self._capture("water the plants", due=due)

        # "Restart": a brand-new vault object reading the same on-disk store.
        reopened = _make_vault(self.tmp)
        open_items = prospects.open_intentions(reopened)
        self.assertEqual(len(open_items), 1, "commitment must persist across restart")
        self.assertEqual(len(self._jobs.list_jobs()), 1, "reminder job must persist across restart")

    def test_ambiguous_time_does_not_invent_schedule(self):
        # detect_intention on a bare commitment without any date → no due, no job.
        detected = prospects.detect_intention("remember to review the pull request")
        self.assertIsNotNone(detected)
        self.assertIsNone(detected.get("due"))
        result = prospects.capture_intention(
            self.vault, detected["intention"], due=detected.get("due"),
            source_event="evt-amb", now=self.now,
        )
        self.assertTrue(result["ok"])
        self.assertEqual(self._jobs.list_jobs(), [], "no invented deadline")


if __name__ == "__main__":
    unittest.main()
