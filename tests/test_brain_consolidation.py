"""Tests for consolidation — episodic to semantic promotion, deduplication and relinking (specs/brain.md §3.2)."""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent.brain.consolidate import (
    PromotionCandidate,
    consolidate_vault,
    find_promotion_candidates,
    merge_near_duplicates,
    parse_daily_entries,
    relink_mentions,
)
from agent.brain.models import NodeCategory, NodeStatus
from agent.brain.vault import BrainVault


class TestDailyParsing(unittest.TestCase):
    def test_parses_formatted_and_simple_lines(self):
        content = (
            "- 10:30 The deploy broke and we rolled back (turn t1)\n"
            "- 11:15 We fixed the database migration (turn t2)\n"
            "- A plain line without timestamp or turn\n"
            "# Comment header\n"
        )
        entries = parse_daily_entries(content)
        self.assertEqual(len(entries), 3)
        self.assertEqual(entries[0], ("The deploy broke and we rolled back", "t1"))
        self.assertEqual(entries[1], ("We fixed the database migration", "t2"))
        self.assertEqual(entries[2], ("A plain line without timestamp or turn", None))


class ConsolidationTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-consolidation-"))
        self.vault = BrainVault(vault_dir=self.tmp / "brain")
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestPromotion(ConsolidationTestCase):
    def test_single_occurrence_is_not_promoted(self):
        self.vault.write_node(
            "daily/2026-10-08",
            "- 10:00 The deploy broke and we rolled back (turn t1)",
            category=NodeCategory.DAILY.value,
        )
        candidates = find_promotion_candidates(self.vault, min_recurrence=2)
        self.assertEqual(candidates, [])

    def test_recurring_occurrence_is_promoted(self):
        self.vault.write_node(
            "daily/2026-10-07",
            "- 10:00 Database migration failed on timeout (turn t1)",
            category=NodeCategory.DAILY.value,
        )
        self.vault.write_node(
            "daily/2026-10-08",
            "- 11:00 Database migration failed on timeout (turn t2)",
            category=NodeCategory.DAILY.value,
        )
        candidates = find_promotion_candidates(self.vault, min_recurrence=2)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].occurrences, 2)
        self.assertIn("t1", candidates[0].source_turns)
        self.assertIn("t2", candidates[0].source_turns)
        self.assertEqual(candidates[0].category, NodeCategory.CONCEPT.value)

    def test_already_existing_semantic_fact_is_not_repromoted(self):
        self.vault.write_node(
            "concept/database-migration",
            "Database migration failed on timeout",
            category=NodeCategory.CONCEPT.value,
        )
        self.vault.write_node(
            "daily/2026-10-07",
            "- 10:00 Database migration failed on timeout (turn t1)",
            category=NodeCategory.DAILY.value,
        )
        self.vault.write_node(
            "daily/2026-10-08",
            "- 11:00 Database migration failed on timeout (turn t2)",
            category=NodeCategory.DAILY.value,
        )
        candidates = find_promotion_candidates(self.vault, min_recurrence=2)
        self.assertEqual(candidates, [])


class TestMergeNearDuplicates(ConsolidationTestCase):
    def test_near_duplicates_are_merged(self):
        n1 = self.vault.write_node(
            "concept/forgetting-curve",
            "PULSE forgets exponentially: retrievability decays as exp(-delta/stability).",
            title="Forgetting Curve",
        )
        n2 = self.vault.write_node(
            "concept/forgetting-rate",
            "PULSE forgets exponentially: retrievability decays as exp(-delta/stability).",
            title="Forgetting Rate",
        )
        merged = merge_near_duplicates(self.vault, threshold=0.86)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["primary_id"], n1.id)
        self.assertEqual(merged[0]["superseded_id"], n2.id)

        # Check status
        n2_after = self.vault.read_node(n2.id)
        self.assertIsNotNone(n2_after)
        self.assertEqual(n2_after.frontmatter.status, NodeStatus.SUPERSEDED.value)
        self.assertEqual(n2_after.frontmatter.superseded_by, n1.id)

    def test_dry_run_leaves_vault_unchanged(self):
        n1 = self.vault.write_node(
            "concept/topic-a", "Detailed description of a feature in pulse system.", title="Topic A"
        )
        n2 = self.vault.write_node(
            "concept/topic-b", "Detailed description of a feature in pulse system.", title="Topic B"
        )
        merged = merge_near_duplicates(self.vault, threshold=0.86, dry_run=True)
        self.assertEqual(len(merged), 1)
        n2_after = self.vault.read_node(n2.id)
        self.assertEqual(n2_after.frontmatter.status, NodeStatus.ACTIVE.value)


class TestRelinkMentions(ConsolidationTestCase):
    def test_adds_wikilinks_for_mentioned_entities(self):
        n1 = self.vault.write_node(
            "concept/memory-organ",
            "The brain vault is an organ of PULSE.",
            title="Memory Organ",
        )
        n2 = self.vault.write_node(
            "concept/architecture",
            "PULSE architecture includes the Memory Organ and other systems.",
            title="PULSE Architecture",
        )
        relinked = relink_mentions(self.vault)
        self.assertEqual(len(relinked), 1)
        self.assertEqual(relinked[0]["node_id"], n2.id)
        self.assertIn(n1.id, relinked[0]["added_related"])

        n2_after = self.vault.read_node(n2.id)
        self.assertIn(f"[[{n1.id}]]", n2_after.content)

    def test_generic_concepts_do_not_create_indiscriminate_links(self):
        """§3: ambient words every note mentions ("PULSE", "Brain", "architecture") must not
        become linking entities, or every memory links to every other. Only specific,
        multi-word entities establish a defensible semantic edge."""
        self.vault.write_node(
            "concept/one", "PULSE has a Brain and an architecture.", title="System One"
        )
        self.vault.write_node(
            "concept/two", "The Brain architecture of PULSE is a system.", title="System Two"
        )
        self.vault.write_node(
            "concept/three", "A PULSE system with a Brain architecture.", title="System Three"
        )
        relinked = relink_mentions(self.vault)
        # Nothing but generic words is shared, so no edges should form.
        self.assertEqual(relinked, [])


class TestConsolidateVault(ConsolidationTestCase):
    def test_full_consolidation_pass(self):
        self.vault.write_node(
            "daily/2026-10-07",
            "- 10:00 SQLite WAL mode ensures concurrency (turn t1)",
            category=NodeCategory.DAILY.value,
        )
        self.vault.write_node(
            "daily/2026-10-08",
            "- 11:00 SQLite WAL mode ensures concurrency (turn t2)",
            category=NodeCategory.DAILY.value,
        )
        report = consolidate_vault(self.vault, min_recurrence=2)
        self.assertEqual(len(report["promoted"]), 1)
        promoted_id = report["promoted"][0]["node_id"]
        promoted_node = self.vault.read_node(promoted_id)
        self.assertIsNotNone(promoted_node)
        self.assertIn("concurrency", promoted_node.content)


if __name__ == "__main__":
    unittest.main()
