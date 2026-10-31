"""End-to-end integration tests for episodic memory encoding and semantic concept consolidation.

Verifies:
1. Substantive conversational turns generate concise 1-line episodic events in daily/YYYY-MM-DD.md.
2. System notices and trivial chatter are strictly excluded from episodic memory.
3. find_promotion_candidates clusters daily entries by entity/topic even with varying phrasing.
4. consolidate_vault promotes recurring themes into concept/<slug>.md with bidirectional [[wikilinks]].
5. Periodic background consolidation runs according to BrainSettings.consolidate_every without blocking turns.
6. Idle consolidation hook (maybe_consolidate_on_idle) schedules background consolidation when daily notes exist.
"""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.brain.consolidate import (
    consolidate_vault,
    find_promotion_candidates,
    parse_daily_entries,
)
from agent.brain.encoding import (
    daily_node_id,
    encode_turn,
    is_system_notice,
    synthesize_episodic_turn_event,
)
from agent.brain.models import NodeCategory, NodeStatus
from agent.brain.session import (
    BrainSettings,
    brain_consolidate_async,
    brain_encode_turn,
    maybe_consolidate_on_idle,
)
from agent.brain.vault import BrainVault


class ConsolidationPipelineTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-pipe-"))
        self.vault_dir = self.tmp / "brain"
        self.vault = BrainVault(vault_dir=self.vault_dir)
        self.vault.ensure_vault_structure()
        self.agent = SimpleNamespace(_brain_vault=self.vault, _agent_config={})

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestEpisodicEncodingCalibration(ConsolidationPipelineTestCase):
    def test_substantive_conversation_creates_concise_daily_entry(self):
        user_prompt = "Can you check the database locks and optimize the slow queries?"
        asst_response = (
            "I investigated the database lock contention using terminal and read_file, "
            "configured SQLite WAL mode to avoid write contention, and verified all 12 tests pass."
        )
        report = encode_turn(
            self.vault,
            user_prompt,
            assistant_text=asst_response,
            turn_id="turn-101",
        )
        self.assertEqual(len(report["episodic"]), 1)
        episodic_event = report["episodic"][0]["text"]
        self.assertIn("SQLite WAL mode", episodic_event)
        self.assertIn("verified all 12 tests pass", episodic_event)

        # Verify daily node was written to disk
        daily_id = daily_node_id()
        daily_node = self.vault.read_node(daily_id)
        self.assertIsNotNone(daily_node)
        assert daily_node is not None
        self.assertEqual(daily_node.frontmatter.category, NodeCategory.DAILY.value)
        self.assertIn(episodic_event, daily_node.content or "")
        self.assertIn("turn-101", daily_node.content or "")
        self.assertEqual(daily_node.frontmatter.extra.get("source_turns"), ["turn-101"])

    def test_strict_exclusion_of_system_notices(self):
        system_notices = [
            "[System note: The following is recalled memory context, NOT new user input. Treat as background data.]",
            "[ASYNC DELEGATION BATCH COMPLETE — deleg_4921, task 1/2]",
            "[ASYNC DELEGATION TASK FAILED — deleg_4921, task 2/2]",
            "[PRIOR CONTEXT RESTORED — continuing previous task]",
            "[TASK: Run security scan on auth module]",
            "[CONTEXT COMPACTION: 40 turns truncated]",
            "[IMPORTANT: Background process exited with code 0]",
            "<memory-context>\n[System note: recalled facts]\n</memory-context>",
            "A background fan-out subagent completed task.",
            "[OUT-OF-BAND USER MESSAGE — steering]\nstop that\n[/OUT-OF-BAND USER MESSAGE]",
        ]

        for notice in system_notices:
            with self.subTest(notice=notice[:40]):
                self.assertTrue(is_system_notice(notice))
                report = encode_turn(self.vault, notice, turn_id="turn-sys")
                self.assertEqual(report["episodic"], [])
                self.assertEqual(report["semantic"], [])
                self.assertEqual(report["skipped"], [{"reason": "system_notice"}])

        # Verify that no daily nodes or junk nodes were written
        self.assertEqual(self.vault.list_all_nodes(), [])

    def test_trivial_chatter_produces_no_daily_node(self):
        for phrase in ("hi", "hello there", "thanks!", "thank you so much", "ok", "got it", "cool"):
            with self.subTest(phrase=phrase):
                report = encode_turn(self.vault, phrase, assistant_text="You're welcome!", turn_id="t-triv")
                self.assertEqual(report["episodic"], [])
                self.assertEqual(report["candidates"], [])
        self.assertEqual(self.vault.list_all_nodes(), [])

    def test_synthesize_episodic_turn_event_helper(self):
        event = synthesize_episodic_turn_event(
            "Fix the bug in token expiration",
            "I analyzed auth.py, patched the token validation check with patch tool, and verified tests pass.",
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertIn("patched the token validation", event)
        self.assertIn("verified tests pass", event)
        # Must be single line without newlines
        self.assertNotIn("\n", event)
        self.assertLessEqual(len(event), 140)


class TestEntityTopicClusteringAndPromotion(ConsolidationPipelineTestCase):
    def test_clusters_different_wordings_sharing_entity_and_topic(self):
        # Two entries from different days with different wording about SQLite concurrency & WAL mode
        self.vault.write_node(
            "daily/2026-10-07",
            "- 10:00 Investigated SQLite concurrency lock contention and tested WAL mode (turn t1)",
            category=NodeCategory.DAILY.value,
        )
        self.vault.write_node(
            "daily/2026-10-08",
            "- 11:30 Enabled SQLite WAL mode in database settings to eliminate locks (turn t2)",
            category=NodeCategory.DAILY.value,
        )

        candidates = find_promotion_candidates(self.vault, min_recurrence=2)
        self.assertEqual(len(candidates), 1)
        cand = candidates[0]
        self.assertEqual(cand.occurrences, 2)
        self.assertIn("t1", cand.source_turns)
        self.assertIn("t2", cand.source_turns)
        self.assertEqual(cand.source_daily_nodes, ["daily/2026-10-07", "daily/2026-10-08"])
        self.assertEqual(cand.category, NodeCategory.CONCEPT.value)

    def test_bidirectional_wikilinks_created_on_promotion(self):
        self.vault.write_node(
            "daily/2026-10-05",
            "- 09:15 Configured Redis cache layer with 1 hour TTL for session storage (turn s1)",
            category=NodeCategory.DAILY.value,
        )
        self.vault.write_node(
            "daily/2026-10-06",
            "- 14:00 Redis cache layer hit rate verified at 94 percent in production (turn s2)",
            category=NodeCategory.DAILY.value,
        )

        report = consolidate_vault(self.vault, min_recurrence=2)
        self.assertEqual(len(report["promoted"]), 1)
        promoted_id = report["promoted"][0]["node_id"]
        self.assertTrue(promoted_id.startswith("concept/"))

        # 1. Verify promoted concept node links to daily notes
        concept_node = self.vault.read_node(promoted_id)
        self.assertIsNotNone(concept_node)
        assert concept_node is not None
        self.assertIn("[[daily/2026-10-05]]", concept_node.content or "")
        self.assertIn("[[daily/2026-10-06]]", concept_node.content or "")
        self.assertIn("daily/2026-10-05", concept_node.frontmatter.related)
        self.assertIn("daily/2026-10-06", concept_node.frontmatter.related)

        # 2. Verify daily notes link back to the promoted concept node
        d1 = self.vault.read_node("daily/2026-10-05")
        self.assertIsNotNone(d1)
        assert d1 is not None
        self.assertIn(f"[[{promoted_id}]]", d1.content or "")
        self.assertIn(promoted_id, d1.frontmatter.related)

        d2 = self.vault.read_node("daily/2026-10-06")
        self.assertIsNotNone(d2)
        assert d2 is not None
        self.assertIn(f"[[{promoted_id}]]", d2.content or "")
        self.assertIn(promoted_id, d2.frontmatter.related)


class TestEndToEndPeriodicConsolidation(ConsolidationPipelineTestCase):
    def test_periodic_cadence_promotes_when_consolidate_every_reached(self):
        # Configure consolidate_every=2
        settings = BrainSettings(consolidate_every=2, enabled=True, encode_enabled=True)

        # Turn 1: Substantive action on payments worker
        rep1 = brain_encode_turn(
            self.agent,
            "Can you investigate the timeout in payments worker?",
            "I analyzed worker logs with terminal, investigated payments worker deadlock, and applied patch.",
            session_id="turn-p1",
            settings=settings,
        )
        self.assertEqual(len(rep1["episodic"]), 1)

        # Before turn 2: No concept node should exist yet
        concept_nodes_before = [
            n for n in self.vault.list_all_nodes() if n.frontmatter.category == NodeCategory.CONCEPT.value
        ]
        self.assertEqual(concept_nodes_before, [])

        # Turn 2: Second action on payments worker (recurrence threshold met)
        rep2 = brain_encode_turn(
            self.agent,
            "Can you verify the payments worker fix?",
            "I investigated payments worker deadlock resolution and verified tests pass in test_worker.py.",
            session_id="turn-p2",
            settings=settings,
        )
        self.assertEqual(len(rep2["episodic"]), 1)

        # Wait briefly for background thread consolidation to complete
        time.sleep(0.2)

        # After turn 2: Consolidation ran because consolidate_every=2 was reached!
        concept_nodes_after = [
            n for n in self.vault.list_all_nodes() if n.frontmatter.category == NodeCategory.CONCEPT.value
        ]
        self.assertGreaterEqual(len(concept_nodes_after), 1)
        promoted = concept_nodes_after[0]
        self.assertIn("payments", promoted.id.lower())

        # Verify daily note links to the new concept node
        daily_node = self.vault.read_node(daily_node_id())
        self.assertIsNotNone(daily_node)
        assert daily_node is not None
        self.assertIn(f"[[{promoted.id}]]", daily_node.content or "")

    def test_brain_consolidate_async_runs_in_background(self):
        self.vault.write_node(
            "daily/2026-10-01",
            "- 10:00 Migrated auth service to JWT tokens (turn t1)",
            category=NodeCategory.DAILY.value,
        )
        self.vault.write_node(
            "daily/2026-10-02",
            "- 11:00 Auth service JWT token verification passing in production (turn t2)",
            category=NodeCategory.DAILY.value,
        )

        scheduled = brain_consolidate_async(self.agent, vault=self.vault)
        self.assertTrue(scheduled)

        # Wait for thread to finish
        deadline = time.monotonic() + 3.0
        promoted_found = False
        while time.monotonic() < deadline:
            concepts = [
                n for n in self.vault.list_all_nodes() if n.frontmatter.category == NodeCategory.CONCEPT.value
            ]
            if concepts:
                promoted_found = True
                break
            time.sleep(0.05)

        self.assertTrue(promoted_found, "Background consolidation thread did not promote concepts in time")
        # Give thread a moment to finish file updates
        time.sleep(0.1)

    def test_maybe_consolidate_on_idle(self):
        # Empty vault -> returns False
        self.assertFalse(maybe_consolidate_on_idle(self.agent, vault=self.vault))

        # Vault with daily notes -> returns True and runs
        self.vault.write_node(
            "daily/2026-10-01",
            "- 10:00 Refactored gateway router pipeline (turn t1)",
            category=NodeCategory.DAILY.value,
        )
        scheduled = maybe_consolidate_on_idle(self.agent, vault=self.vault)
        self.assertTrue(scheduled)
        time.sleep(0.1)


if __name__ == "__main__":
    unittest.main()
