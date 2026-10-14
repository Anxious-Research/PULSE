"""Prospective memory tests (specs/brain.md §3.6, §10 — Phase E).

The claim under test: an intention the user states once ("remind me to X on Friday")
must survive into future turns on its own — surfacing when due even when the current
question shares no words with it — without ever converting a casual statement into a
commitment, and without ever silently expiring, completing, or deleting a commitment.
The lifecycle is code; history is preserved; completion is explicit.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent.brain import prospects
from agent.brain.session import BrainSettings, brain_turn_context, brain_encode_turn
from agent.brain.vault import BrainVault


class ProspectTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-prospects-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()
        self.now = int(time.time())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class CaptureTests(ProspectTestCase):
    def test_capture_time_triggered(self):
        due = self.now + 3600
        r = prospects.capture_intention(self.vault, "send the invoice", due=due,
                                        source_event="evt-1", now=self.now)
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "active")
        self.assertEqual(r["trigger"], "time")
        item = prospects.find_intention(self.vault, r["intention_id"])
        self.assertEqual(item.due, due)
        self.assertEqual(item.trigger_kind, "time")

    def test_capture_without_trigger_is_captured_unresolved(self):
        r = prospects.capture_intention(self.vault, "maybe look into x", now=self.now)
        self.assertTrue(r["ok"])
        self.assertEqual(r["status"], "captured")
        self.assertEqual(r["trigger"], "condition")
        # An unresolved intention never surfaces as due.
        self.assertEqual(prospects.due_intentions(self.vault, now=self.now), [])

    def test_same_source_event_does_not_duplicate(self):
        a = prospects.capture_intention(self.vault, "send the invoice", due=self.now + 10,
                                        source_event="evt-1", now=self.now)
        b = prospects.capture_intention(self.vault, "send the invoice", due=self.now + 10,
                                        source_event="evt-1", now=self.now)
        self.assertTrue(a["created"])
        self.assertTrue(b.get("duplicate"))
        self.assertEqual(len(prospects.list_intentions(self.vault, include_closed=True)), 1)

    def test_empty_and_no_vault_are_rejected(self):
        self.assertFalse(prospects.capture_intention(self.vault, "", now=self.now)["ok"])
        self.assertFalse(prospects.capture_intention(None, "x", now=self.now)["ok"])


class LifecycleTests(ProspectTestCase):
    def setUp(self):
        super().setUp()
        self.id_ = prospects.capture_intention(
            self.vault, "file the report", due=self.now + 86400,
            source_event="evt-2", now=self.now)["intention_id"]

    def test_complete_is_explicit_and_idempotent_to_replay(self):
        report = prospects.complete_intention(self.vault, self.id_, evidence_ref="turn-42", now=self.now + 5)
        self.assertEqual(report["status"], "completed")
        # Completed intentions leave the due list immediately.
        self.assertEqual(prospects.due_intentions(self.vault, now=self.now + 86400 * 5), [])
        # Re-completing is refused, not silently idempotent: state is explicit.
        again = prospects.complete_intention(self.vault, self.id_, now=self.now + 6)
        self.assertFalse(again["ok"])

    def test_completion_is_never_inferred_from_inactivity(self):
        # Weeks pass, nothing happens: the intention must still be open and due.
        later = self.now + 86400 * 30
        item = prospects.find_intention(self.vault, self.id_)
        self.assertTrue(item.is_open)
        self.assertEqual(item.due_state(now=later), "overdue")

    def test_cancel_preserves_history(self):
        out = prospects.cancel_intention(self.vault, self.id_, reason="no longer needed", now=self.now + 5)
        self.assertEqual(out["status"], "cancelled")
        node = self.vault.read_node(self.id_)
        extra = node.frontmatter.extra or {}
        self.assertEqual(extra["prospect_status"], "cancelled")
        self.assertEqual(extra["cancel_reason"], "no longer needed")
        # History preserved: captured record + transition.
        self.assertGreaterEqual(len(extra.get("history") or []), 2)

    def test_completed_task_does_not_reappear_as_open(self):
        prospects.complete_intention(self.vault, self.id_, now=self.now + 5)
        self.assertEqual(prospects.open_intentions(self.vault), [])
        self.assertEqual(prospects.list_intentions(self.vault, include_closed=True)[0].status, "completed")

    def test_reopen_preserves_history(self):
        prospects.complete_intention(self.vault, self.id_, now=self.now + 5)
        out = prospects.reopen_intention(self.vault, self.id_, now=self.now + 10)
        self.assertEqual(out["status"], "captured")
        item = prospects.find_intention(self.vault, self.id_)
        self.assertTrue(item.is_open)
        self.assertGreaterEqual(len(item.history), 3)  # captured -> completed -> reopened


class TriggerTests(ProspectTestCase):
    def test_due_intentions_time_gated(self):
        prospects.capture_intention(self.vault, "future task", due=self.now + 86400, now=self.now)
        prospects.capture_intention(self.vault, "past task", due=self.now - 60, now=self.now)
        due = prospects.due_intentions(self.vault, now=self.now)
        self.assertEqual([i.text for i in due], ["past task"])

    def test_due_state_transitions(self):
        due = self.now - 3600
        r = prospects.capture_intention(self.vault, "late item", due=due, now=self.now)
        item = prospects.find_intention(self.vault, r["intention_id"])
        self.assertEqual(item.due_state(now=self.now), "due")
        # 15 days past due -> overdue (stale label)
        self.assertEqual(item.due_state(now=self.now + 15 * 86400), "overdue")
        # upcoming
        r2 = prospects.capture_intention(self.vault, "far thing", due=self.now + 86400 * 5, now=self.now)
        self.assertEqual(prospects.find_intention(self.vault, r2["intention_id"]).due_state(now=self.now),
                         "upcoming")

    def test_cue_intentions_match_and_reject(self):
        prospects.capture_intention(self.vault, "check CI status", cue="ci pipeline", now=self.now)
        self.assertEqual([i.text for i in prospects.cue_intentions(self.vault, "the ci pipeline is green")],
                         ["check CI status"])
        self.assertEqual(prospects.cue_intentions(self.vault, "what is for dinner"), [])

    def test_condition_trigger_never_due(self):
        r = prospects.capture_intention(self.vault, "when CI is green, deploy", cue="", now=self.now)
        self.assertEqual(r["trigger"], "condition")
        self.assertEqual(r["status"], "captured")
        self.assertEqual(prospects.due_intentions(self.vault, now=self.now + 86400 * 365), [])


class DetectionTests(ProspectTestCase):
    def test_explicit_markers_are_detected(self):
        hit = prospects.detect_intention("Remind me to call the dentist tomorrow")
        self.assertIsNotNone(hit)
        self.assertIn("call the dentist", hit["intention"])
        self.assertIsNotNone(hit["due"])

    def test_casual_statements_are_not_commitments(self):
        self.assertIsNone(prospects.detect_intention("I might look into rust someday"))
        self.assertIsNone(prospects.detect_intention("we should totally refactor this"))
        self.assertIsNone(prospects.detect_intention(""))

    def test_weekday_due_extraction(self):
        hit = prospects.detect_intention("remember to submit the form by friday")
        self.assertIsNotNone(hit["due"])
        # Next Friday, not today.
        self.assertGreater(hit["due"], self.now)

    def test_ambiguous_date_stays_unresolved(self):
        hit = prospects.detect_intention("remind me to water the plants")
        self.assertIsNotNone(hit)
        self.assertIsNone(hit["due"])  # no time invented
        self.assertTrue(hit["cue"])


class TurnWiringTests(ProspectTestCase):
    """brain_turn_context + brain_encode_turn integration (the production path)."""

    def _agent(self):
        class A:
            pass
        a = A()
        a._agent_config = {"brain": {"enabled": True}}
        return a

    def test_due_intention_surfaces_without_word_overlap(self):
        from agent.brain.session import _prospective_guidance_block
        from agent.brain.vault import BrainVault as BV
        # Direct unit path: the block must fire for an unrelated query.
        r = prospects.capture_intention(self.vault, "call the landlord", due=self.now - 60, now=self.now)
        block = prospects.guidance_block(self.vault, "tell me a joke about computers", now=self.now)
        self.assertIn("call the landlord", block)

    def test_capture_via_encode_turn(self):
        agent = self._agent()
        report = brain_encode_turn(agent, "Remind me to water the plants tomorrow", session_id="s1")
        vault = agent._brain_vault
        items = prospects.open_intentions(vault)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].status, "active")  # tomorrow = resolved time trigger
        self.assertIsNotNone(items[0].due)

    def test_capture_survives_restart(self):
        agent = self._agent()
        brain_encode_turn(agent, "Remember to file taxes by 2027-01-15", session_id="s2")
        # Fresh vault instance = simulated restart: the intention and its due time survive.
        vault2 = BrainVault(vault_dir=agent._brain_vault.vault_dir)
        items = prospects.open_intentions(vault2)
        self.assertEqual(len(items), 1)
        self.assertIn("file taxes", items[0].text)
        self.assertIsNotNone(items[0].due)
        self.assertGreater(items[0].due, self.now)
        # And it becomes due when the time arrives, in the new session.
        later = prospects.due_intentions(vault2, now=items[0].due + 60)
        self.assertEqual([i.text for i in later], [items[0].text])

    def test_prospective_disabled_by_config(self):
        settings = BrainSettings.from_config({"brain": {"prospective_enabled": False}})
        self.assertFalse(settings.prospective_enabled)


if __name__ == "__main__":
    unittest.main()
