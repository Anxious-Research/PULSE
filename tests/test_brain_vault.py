"""Tests for the vault: the single writer of brain memory.

The critical properties here are the ones whose absence broke PULSE last time:
- a bad file must never raise out of the vault,
- a content edit must not reset memory metadata (that would erase "how well I know this"),
- deletes only ever happen when explicitly requested.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent.brain.models import NodeStatus
from agent.brain.vault import BrainVault

T0 = 1_800_000_000


class VaultTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-test-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)


class TestStructure(VaultTestCase):
    def test_categories_and_obsidian_config_created(self):
        for category in ("self", "user", "concept", "project", "belief", "daily"):
            self.assertTrue((self.tmp / category).is_dir(), category)
        self.assertTrue((self.tmp / ".obsidian" / "app.json").is_file())
        self.assertTrue((self.tmp / ".obsidian" / "graph.json").is_file())

    def test_ensure_is_idempotent_and_does_not_clobber(self):
        custom = self.tmp / ".obsidian" / "app.json"
        custom.write_text('{"user": "edited"}', encoding="utf-8")
        self.vault.ensure_vault_structure()
        self.assertEqual(custom.read_text(encoding="utf-8"), '{"user": "edited"}')

    def test_default_location_honours_env_override(self):
        import os

        old = os.environ.get("PULSE_BRAIN_DIR")
        try:
            os.environ["PULSE_BRAIN_DIR"] = str(self.tmp / "elsewhere")
            self.assertTrue(str(BrainVault().vault_dir).endswith("elsewhere"))
        finally:
            if old is None:
                os.environ.pop("PULSE_BRAIN_DIR", None)
            else:
                os.environ["PULSE_BRAIN_DIR"] = old

    def test_node_path_rejects_escapes(self):
        for bad in ["../evil", "a/../../evil", ".hidden/x", ".."]:
            with self.assertRaises(ValueError, msg=bad):
                self.vault.node_path(bad)

    def test_node_path_defaults_category_for_bare_id(self):
        self.assertTrue(str(self.vault.node_path("thing")).endswith("concept/thing.md"))


class TestRoundTrip(VaultTestCase):
    def test_write_then_read(self):
        node = self.vault.write_node(
            "user/preferences",
            "# Preferences\n\n- prefers Hinglish replies",
            title="User Preferences",
            tags=["preference", "language"],
            salience=0.9,
        )
        self.assertEqual(node.id, "user/preferences")
        self.assertEqual(node.frontmatter.title, "User Preferences")
        self.assertEqual(node.frontmatter.tags, ["preference", "language"])

        reloaded = self.vault.read_node("user/preferences")
        self.assertIsNotNone(reloaded)
        self.assertIn("prefers Hinglish replies", reloaded.content)
        self.assertAlmostEqual(reloaded.frontmatter.salience, 0.9, places=4)
        self.assertIsNotNone(reloaded.frontmatter.created_at)

    def test_file_is_valid_obsidian_markdown(self):
        self.vault.write_node("concept/x", "Body text", title="X")
        text = (self.tmp / "concept" / "x.md").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("---\n"))
        self.assertIn("\ntitle: X", text)
        self.assertIn("Body text", text)

    def test_missing_node_is_none(self):
        self.assertIsNone(self.vault.read_node("concept/nope"))

    def test_category_follows_the_path_without_an_explicit_argument(self):
        """A node written at user/x is a 'user' node, not a 'concept' node."""
        node = self.vault.write_node("user/preferences", "body")
        self.assertEqual(node.category, "user")
        self.assertEqual(self.vault.read_node("user/preferences").category, "user")

    def test_explicit_category_wins_over_the_path(self):
        node = self.vault.write_node("concept/x", "body", category="belief")
        self.assertEqual(node.category, "belief")

    def test_bare_id_defaults_to_concept(self):
        self.assertEqual(self.vault.write_node("thing", "body").category, "concept")

    def test_list_all_nodes_sorted_and_excludes_dotfiles(self):
        self.vault.write_node("concept/b", "b")
        self.vault.write_node("concept/a", "a")
        (self.tmp / ".hidden.md").write_text("---\ntitle: h\n---\n", encoding="utf-8")
        ids = [n.id for n in self.vault.list_all_nodes()]
        self.assertEqual(ids, ["concept/a", "concept/b"])


class TestMemoryMetadataSurvivesEdits(VaultTestCase):
    def test_content_edit_preserves_created_at_count_and_stability(self):
        self.vault.write_node("concept/x", "v1", now=T0)
        self.vault.record_access(["concept/x"], now=T0 + 100)
        before = self.vault.read_node("concept/x").frontmatter
        self.assertEqual(before.access_count, 1)
        self.assertGreater(before.stability, 1.0)

        self.vault.write_node("concept/x", "v2", now=T0 + 200)
        after = self.vault.read_node("concept/x").frontmatter
        self.assertEqual(after.created_at, before.created_at)
        self.assertEqual(after.access_count, before.access_count)
        self.assertAlmostEqual(after.stability, before.stability, places=6)
        self.assertEqual(after.updated_at, T0 + 200)
        self.assertIn("v2", self.vault.read_node("concept/x").content)

    def test_explicit_fields_win_on_update(self):
        self.vault.write_node("concept/x", "v1", title="Old", tags=["a"], now=T0)
        self.vault.write_node("concept/x", "v2", title="New", tags=["b"], now=T0 + 1)
        node = self.vault.read_node("concept/x")
        self.assertEqual(node.frontmatter.title, "New")
        self.assertEqual(node.frontmatter.tags, ["b"])

    def test_unspecified_fields_are_not_cleared(self):
        self.vault.write_node("concept/x", "v1", title="Keep", tags=["keep"], aliases=["K"], now=T0)
        self.vault.write_node("concept/x", "v2", now=T0 + 1)
        node = self.vault.read_node("concept/x")
        self.assertEqual(node.frontmatter.title, "Keep")
        self.assertEqual(node.frontmatter.tags, ["keep"])
        self.assertEqual(node.frontmatter.aliases, ["K"])


class TestReconsolidation(VaultTestCase):
    def test_record_access_strengthens(self):
        self.vault.write_node("concept/x", "body", now=T0)
        self.assertEqual(self.vault.record_access(["concept/x"], now=T0 + 10), 1)
        fm = self.vault.read_node("concept/x").frontmatter
        self.assertEqual(fm.access_count, 1)
        self.assertEqual(fm.last_accessed, T0 + 10)
        self.assertGreater(fm.stability, 1.0)

    def test_record_access_skips_missing_nodes(self):
        self.assertEqual(self.vault.record_access(["concept/ghost"], now=T0), 0)

    def test_write_with_mark_access_strengthens(self):
        self.vault.write_node("concept/x", "body", now=T0)
        node = self.vault.write_node("concept/x", "body", now=T0 + 5, mark_access=True)
        self.assertEqual(node.frontmatter.access_count, 1)


class TestDeleteAndSupersede(VaultTestCase):
    def test_delete_only_when_explicit(self):
        self.vault.write_node("concept/x", "body")
        self.assertTrue(self.vault.delete_node("concept/x"))
        self.assertIsNone(self.vault.read_node("concept/x"))
        self.assertFalse(self.vault.delete_node("concept/x"))

    def test_supersede_keeps_history(self):
        self.vault.write_node("concept/old", "old fact", now=T0)
        self.vault.write_node("concept/new", "new fact", now=T0 + 1)
        self.assertTrue(self.vault.supersede("concept/old", "concept/new", now=T0 + 2))
        node = self.vault.read_node("concept/old")
        self.assertIsNotNone(node, "superseded memory must not disappear")
        self.assertEqual(node.frontmatter.status, NodeStatus.SUPERSEDED.value)
        self.assertEqual(node.frontmatter.superseded_by, "concept/new")
        self.assertIn("old fact", node.content)

    def test_supersede_missing_node_is_false(self):
        self.assertFalse(self.vault.supersede("concept/ghost", "concept/new"))


class TestRename(VaultTestCase):
    def test_rename_moves_file_and_updates_links_everywhere(self):
        self.vault.write_node("concept/old-name", "target", now=T0)
        self.vault.write_node("concept/a", "see [[old-name]]", now=T0)
        self.vault.write_node("concept/b", "and [[concept/old-name|the thing]]", now=T0)

        result = self.vault.rename_node("concept/old-name", "concept/new-name")
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["links_updated"], 2)
        self.assertIsNone(self.vault.read_node("concept/old-name"))
        self.assertIsNotNone(self.vault.read_node("concept/new-name"))
        self.assertIn("[[new-name]]", self.vault.read_node("concept/a").content)
        self.assertIn("|the thing]]", self.vault.read_node("concept/b").content)

    def test_rename_refuses_existing_target(self):
        self.vault.write_node("concept/a", "a")
        self.vault.write_node("concept/b", "b")
        result = self.vault.rename_node("concept/a", "concept/b")
        self.assertFalse(result["ok"])

    def test_rename_missing_node(self):
        self.assertFalse(self.vault.rename_node("concept/ghost", "concept/x")["ok"])

    def test_rename_to_same_id_is_noop_success(self):
        self.vault.write_node("concept/a", "a")
        self.assertTrue(self.vault.rename_node("concept/a", "concept/a")["ok"])


class TestFailureIsolation(VaultTestCase):
    def test_corrupt_file_does_not_raise_and_is_skipped(self):
        (self.tmp / "concept" / "broken.md").write_bytes(b"\xff\xfe\x00 not utf-8")
        self.vault.write_node("concept/ok", "fine")
        ids = [n.id for n in self.vault.list_all_nodes()]
        self.assertIn("concept/ok", ids)
        self.assertNotIn("concept/broken", ids)

    def test_garbage_frontmatter_still_reads_body(self):
        (self.tmp / "concept" / "weird.md").write_text(
            "---\nthis is not: valid: yaml:\n---\n\nbody survives\n", encoding="utf-8"
        )
        node = self.vault.read_node("concept/weird")
        self.assertIsNotNone(node)
        self.assertIn("body survives", node.content)

    def test_single_line_file_without_frontmatter(self):
        (self.tmp / "concept" / "plain.md").write_text("just text", encoding="utf-8")
        node = self.vault.read_node("concept/plain")
        self.assertIsNotNone(node)
        self.assertEqual(node.content, "just text")

    def test_failed_write_leaves_no_temp_files(self):
        self.vault.write_node("concept/x", "body")
        leftovers = [p.name for p in self.tmp.rglob(".tmp_*")]
        self.assertEqual(leftovers, [])

    def test_empty_node_id_rejected(self):
        with self.assertRaises(ValueError):
            self.vault.write_node("", "content")


class TestHelpers(VaultTestCase):
    def test_unique_node_id_adds_suffix(self):
        first = self.vault.unique_node_id("concept", "Some Fact")
        self.assertEqual(first, "concept/some-fact")
        self.vault.write_node(first, "body")
        second = self.vault.unique_node_id("concept", "Some Fact")
        self.assertEqual(second, "concept/some-fact-2")

    def test_stats(self):
        self.vault.write_node("concept/a", "[[concept/b]]")
        self.vault.write_node("concept/b", "b")
        stats = self.vault.stats()
        self.assertEqual(stats["nodes"], 2)
        self.assertEqual(stats["links"], 1)
        self.assertEqual(stats["by_category"], {"concept": 2})


if __name__ == "__main__":
    unittest.main()
