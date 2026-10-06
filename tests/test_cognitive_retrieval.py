"""Tests for Cognitive Retrieval and Turn Subgraph Recall."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.brain.vault import BrainVault
from agent.brain.retrieval import build_static_brain_prompt, retrieve_turn_subgraph


class TestCognitiveRetrieval(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="pulse_test_retrieval_"))
        self.vault = BrainVault(self.temp_dir)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_static_brain_prompt(self):
        prompt = build_static_brain_prompt(self.vault)
        self.assertIsNotNone(prompt)
        self.assertIn("PULSE Native Cognitive Brain & Self-Knowledge", prompt)
        self.assertIn("PULSE Agent", prompt)

    def test_turn_subgraph_recall(self):
        self.vault.write_node(
            node_id="concept/sqlite-wal",
            content="SQLite Write-Ahead Logging allows concurrent reads and atomic writes.",
            title="SQLite WAL Mode",
            category="concept",
            tags=["database", "storage"],
        )

        self.vault.write_node(
            node_id="user/preferences",
            content="User prefers SQLite with WAL mode for local storage.",
            title="Database Preferences",
            category="user",
            tags=["preferences"],
        )

        recall = retrieve_turn_subgraph(
            user_prompt="Can we optimize our database storage with SQLite WAL mode?",
            vault=self.vault,
        )

        self.assertIsNotNone(recall)
        self.assertIn("Active Cognitive Brain Recall", recall)
        self.assertIn("SQLite WAL Mode", recall)


if __name__ == "__main__":
    unittest.main()
