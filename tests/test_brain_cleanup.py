"""Tests for brain vault cleanup (junk detection & duplicate supersession)."""

import tempfile
import unittest
from pathlib import Path

from agent.brain.cleanup import cleanup_duplicates, cleanup_junk_nodes, is_junk_node
from agent.brain.models import Frontmatter, NodeCategory, NodeStatus
from agent.brain.vault import BrainVault

T0 = 1728000000


class TestJunkDetection(unittest.TestCase):
    def test_junk_spec_fragment(self):
        fm = Frontmatter(
            title="correction supersession",
            category=NodeCategory.CONCEPT.value,
            salience=0.48,
            access_count=0,
        )
        is_junk, reason = is_junk_node("concept/correction-supersession", "├── correction / supersession", fm)
        self.assertTrue(is_junk, f"should be junk: {reason}")
        self.assertIn("title", reason.lower())

    def test_junk_incomplete_phrase(self):
        fm = Frontmatter(title="not a duplicate of what the", salience=0.45, access_count=0)
        is_junk, reason = is_junk_node("concept/not-a-duplicate-of-what-the", "not a duplicate of what the", fm)
        self.assertTrue(is_junk, f"should be junk: {reason}")
        self.assertIn("incomplete phrase", reason)

    def test_not_junk_high_salience(self):
        fm = Frontmatter(salience=0.55, access_count=0)
        is_junk, reason = is_junk_node("concept/outcome", "the deploy broke", fm)
        self.assertFalse(is_junk, reason)

    def test_not_junk_high_access(self):
        fm = Frontmatter(salience=0.45, access_count=5)
        is_junk, reason = is_junk_node("concept/foo", "short", fm)
        self.assertFalse(is_junk, reason)

    def test_not_junk_self_category(self):
        fm = Frontmatter(category=NodeCategory.SELF.value, salience=0.45, access_count=0)
        is_junk, reason = is_junk_node("self/identity", "PULSE is an entity.", fm)
        self.assertFalse(is_junk, reason)

    def test_not_junk_long_content(self):
        fm = Frontmatter(salience=0.45, access_count=0)
        long_text = " ".join(["word"] * 50)
        is_junk, reason = is_junk_node("concept/doc", long_text, fm)
        self.assertFalse(is_junk, reason)


class TestCleanupJunkNodes(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def test_dry_run_identifies_junk(self):
        self.vault.write_node("concept/junk1", "├── correction / supersession", salience=0.48, now=T0)
        self.vault.write_node("concept/good", "PULSE has a Brain with associative memory.", salience=0.55, now=T0)
        
        result = cleanup_junk_nodes(self.vault, dry_run=True)
        
        self.assertEqual(result["inspected"], 2)
        self.assertEqual(len(result["junk_nodes"]), 1)
        self.assertEqual(result["junk_nodes"][0]["id"], "concept/junk1")
        self.assertEqual(result["archived"], 0, "dry_run should not archive")

    def test_real_run_archives_junk(self):
        self.vault.write_node("concept/junk1", "pretend you remember", salience=0.48, now=T0)
        self.vault.write_node("concept/good", "Real knowledge here.", salience=0.55, now=T0)
        
        result = cleanup_junk_nodes(self.vault, dry_run=False, now=T0)
        
        self.assertEqual(result["archived"], 1)
        node = self.vault.read_node("concept/junk1")
        assert node is not None  # type narrowing
        self.assertEqual(node.frontmatter.status, NodeStatus.ARCHIVED.value)

    def test_preserves_high_access_nodes(self):
        self.vault.write_node("concept/used", "short", salience=0.45, now=T0)
        self.vault.record_access(["concept/used"], now=T0)
        self.vault.record_access(["concept/used"], now=T0)
        self.vault.record_access(["concept/used"], now=T0)
        
        result = cleanup_junk_nodes(self.vault, dry_run=False, now=T0)
        
        self.assertEqual(result["archived"], 0)
        node = self.vault.read_node("concept/used")
        assert node is not None
        self.assertEqual(node.frontmatter.access_count, 3)
        self.assertEqual(node.frontmatter.status, NodeStatus.ACTIVE.value)


class TestCleanupDuplicates(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def test_supersedes_near_duplicates(self):
        text = "target shape of the library"
        self.vault.write_node("concept/target-shape-of-the-library", text, now=T0)
        self.vault.write_node("concept/target-shape-of-the-library-2", text, now=T0 + 1)
        self.vault.write_node("concept/target-shape-of-the-library-3", text, now=T0 + 2)
        
        result = cleanup_duplicates(self.vault, dry_run=False, now=T0)
        
        self.assertEqual(result["superseded"], 2)
        
        keeper = self.vault.read_node("concept/target-shape-of-the-library")
        assert keeper is not None
        self.assertEqual(keeper.frontmatter.status, NodeStatus.ACTIVE.value)
        
        dupe1 = self.vault.read_node("concept/target-shape-of-the-library-2")
        assert dupe1 is not None
        self.assertEqual(dupe1.frontmatter.status, NodeStatus.SUPERSEDED.value)
        self.assertEqual(dupe1.frontmatter.superseded_by, "concept/target-shape-of-the-library")

    def test_keeps_most_accessed_duplicate(self):
        text = "fix the skill in place when"
        self.vault.write_node("concept/fix-the-skill-in-place-when", text, now=T0)
        self.vault.write_node("concept/fix-the-skill-in-place-when-2", text, now=T0 + 1)
        
        # Mark -2 as more used
        self.vault.record_access(["concept/fix-the-skill-in-place-when-2"], now=T0)
        self.vault.record_access(["concept/fix-the-skill-in-place-when-2"], now=T0)
        
        result = cleanup_duplicates(self.vault, dry_run=False, now=T0)
        
        self.assertEqual(result["superseded"], 1)
        
        keeper = self.vault.read_node("concept/fix-the-skill-in-place-when-2")
        assert keeper is not None
        self.assertEqual(keeper.frontmatter.status, NodeStatus.ACTIVE.value)
        
        old = self.vault.read_node("concept/fix-the-skill-in-place-when")
        assert old is not None
        self.assertEqual(old.frontmatter.status, NodeStatus.SUPERSEDED.value)

    def test_ignores_dissimilar_nodes(self):
        self.vault.write_node("concept/alpha", "completely different content A", now=T0)
        self.vault.write_node("concept/alpha-2", "completely different content B", now=T0)
        
        result = cleanup_duplicates(self.vault, dry_run=False, now=T0)
        
        self.assertEqual(result["superseded"], 0)


if __name__ == "__main__":
    unittest.main()
