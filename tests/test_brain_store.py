"""Tests for BrainStore — the MemoryStore-compatible facade over the vault (specs/brain.md §5)."""

import tempfile
import unittest
from pathlib import Path

from agent.brain.models import NodeStatus
from agent.brain.store import BrainStore
from agent.brain.vault import BrainVault


class TestBrainStore(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="pulse-brain-store-test-")
        self.vault_dir = Path(self._tmpdir.name)
        self.vault = BrainVault(self.vault_dir)
        self.vault.ensure_vault_structure()
        self.store = BrainStore(vault=self.vault)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_add_without_character_budget(self):
        # The vault has no budget limit (§1). Large content is accepted.
        large_content = "This is a detailed fact about the project. " * 50
        res = self.store.add("memory", large_content)
        self.assertTrue(res["success"])
        self.assertIn("Entry added.", res["message"])
        self.assertEqual(len(self.store.memory_entries), 1)
        self.assertEqual(self.store.memory_entries[0], large_content.strip())

    def test_add_duplicate_does_not_create_duplicate(self):
        self.store.add("user", "User prefers Hindi")
        res = self.store.add("user", "User prefers Hindi")
        self.assertTrue(res["success"])
        self.assertIn("already exists", res["message"])
        self.assertEqual(len(self.store.user_entries), 1)

    def test_replace_supersedes_old_node(self):
        self.store.add("user", "User prefers dark mode")
        self.assertEqual(len(self.store.user_entries), 1)

        res = self.store.replace("user", "prefers dark", "User prefers light mode")
        self.assertTrue(res["success"])
        self.assertEqual(self.store.user_entries, ["User prefers light mode"])

        # Check raw vault state: old node is superseded, not deleted (§3.5, §12)
        all_nodes = self.vault.list_all_nodes()
        self.assertEqual(len(all_nodes), 2)
        statuses = {n.frontmatter.status for n in all_nodes}
        self.assertIn(NodeStatus.SUPERSEDED.value, statuses)
        self.assertIn(NodeStatus.ACTIVE.value, statuses)

    def test_remove_archives_node(self):
        self.store.add("user", "User prefers dark mode")
        self.assertEqual(len(self.store.user_entries), 1)

        res = self.store.remove("user", "prefers dark")
        self.assertTrue(res["success"])
        self.assertEqual(len(self.store.user_entries), 0)

        # Raw file still exists in vault with status archived (§3.3, §12)
        all_nodes = self.vault.list_all_nodes()
        self.assertEqual(len(all_nodes), 1)
        self.assertEqual(all_nodes[0].frontmatter.status, NodeStatus.ARCHIVED.value)

    def test_apply_batch(self):
        ops = [
            {"action": "add", "content": "PULSE uses SQLite"},
            {"action": "add", "content": "PULSE uses Obsidian format"},
        ]
        res = self.store.apply_batch("memory", ops)
        self.assertTrue(res["success"])
        self.assertEqual(len(self.store.memory_entries), 2)

        # Batch with replace and remove
        ops2 = [
            {"action": "replace", "old_text": "SQLite", "content": "PULSE uses SQLite with WAL mode"},
            {"action": "remove", "old_text": "Obsidian"},
            {"action": "add", "content": "PULSE has force graph"},
        ]
        res2 = self.store.apply_batch("memory", ops2)
        self.assertTrue(res2["success"])
        entries = self.store.memory_entries
        self.assertEqual(len(entries), 2)
        self.assertIn("PULSE uses SQLite with WAL mode", entries)
        self.assertIn("PULSE has force graph", entries)

    def test_mutate_closure(self):
        self.store.add("user", "User prefers vim")
        self.assertEqual(len(self.store.user_entries), 1)

        def _closure(entries, limit):
            self.assertEqual(entries, ["User prefers vim"])
            return ["User prefers neovim"], "updated editor"

        res = self.store._mutate("user", _closure)
        self.assertTrue(res["success"])
        self.assertEqual(self.store.user_entries, ["User prefers neovim"])

    def test_format_for_system_prompt_returns_none(self):
        # Format for system prompt is deliberately None for BrainStore to avoid duplicate blocks (§5)
        self.store.add("memory", "Some memory")
        self.assertIsNone(self.store.format_for_system_prompt("memory"))
        self.assertIsNone(self.store.format_for_system_prompt("user"))

    def test_resolve_entry(self):
        self.store.add("user", "User prefers concise answers")
        resolved = self.store.resolve_entry("user", "concise", "replace")
        self.assertTrue(resolved["success"])
        self.assertEqual(resolved["matched_entry"], "User prefers concise answers")


if __name__ == "__main__":
    unittest.main()
