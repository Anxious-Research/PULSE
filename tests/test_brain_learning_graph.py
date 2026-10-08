"""Tests for BrainVault integration in learning_graph and learning_mutations (specs/brain.md §7)."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain.models import NodeStatus
from agent.brain.store import BrainStore
from agent.brain.vault import BrainVault
from agent.learning_graph import _memory_cards, build_learning_graph, memory_fingerprint, memory_node_id
from agent.learning_mutations import _locate_memory, _mutate_memory


class TestBrainLearningGraph(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="pulse-learning-graph-test-")
        self.tmp_path = Path(self._tmpdir.name)
        self.vault_dir = self.tmp_path / "brain"
        self.vault = BrainVault(self.vault_dir)
        self.vault.ensure_vault_structure()
        self.store = BrainStore(vault=self.vault)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_memory_cards_reads_vault_nodes(self):
        self.store.add("user", "User prefers dark mode")
        self.store.add("memory", "PULSE runs SQLite in WAL mode")

        with patch("agent.brain.vault.get_brain_vault_dir", return_value=self.vault_dir), \
             patch("agent.learning_graph.get_pulse_home", return_value=self.tmp_path):
            cards = _memory_cards()
            self.assertEqual(len(cards), 2)
            sources = {c["source"] for c in cards}
            self.assertIn("profile", sources)
            self.assertIn("memory", sources)
            titles = [c["title"] for c in cards]
            self.assertTrue(any("dark mode" in t for t in titles))
            self.assertTrue(any("SQLite" in t for t in titles))

    def test_learning_mutations_mutate_vault_memory(self):
        self.store.add("user", "User prefers dark theme")

        with patch("agent.brain.vault.get_brain_vault_dir", return_value=self.vault_dir), \
             patch("pulse_constants.get_pulse_home", return_value=self.tmp_path), \
             patch("tools.memory_tool.load_on_disk_store", return_value=self.store):
            cards = _memory_cards()
            self.assertEqual(len(cards), 1)
            card = cards[0]
            node_id = memory_node_id(card, 0)

            # Update entry
            res = _mutate_memory(node_id, "User prefers light theme")
            self.assertTrue(res["ok"])
            self.assertEqual(self.store.user_entries, ["User prefers light theme"])

            # Delete entry
            cards_after = _memory_cards()
            node_id_after = memory_node_id(cards_after[0], 0)
            res_del = _mutate_memory(node_id_after, None)
            self.assertTrue(res_del["ok"])
            self.assertEqual(len(self.store.user_entries), 0)


if __name__ == "__main__":
    unittest.main()
