"""Tests for PULSE Turn-by-Turn Cognitive Ingestion."""

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.brain.vault import BrainVault
from agent.brain.graph import BrainGraph
from agent.brain.ingest import CognitiveIngestor, auto_ingest_turn_to_brain


class TestBrainIngest(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.vault = BrainVault(vault_dir=Path(self.test_dir))
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    def test_turn_ingest_creates_and_links_nodes(self):
        ingestor = CognitiveIngestor(vault=self.vault)

        user_msg = "Remember: Always write clean TypeScript code with strict types."
        asst_msg = "Understood. Maintaining clean TypeScript with strict typing."

        res = ingestor.ingest_turn(user_msg, asst_msg)
        self.assertTrue(res["ingested"])
        self.assertGreaterEqual(len(res["updated_nodes"]), 1)

        # Verify user preferences note was updated
        graph = BrainGraph(self.vault)
        graph.rebuild_index()

        node = graph.get_node("user/preferences")
        self.assertIsNotNone(node)
        assert node is not None
        self.assertIn("User Preferences", node.title)
        self.assertIn("Always write clean TypeScript", node.content)


if __name__ == "__main__":
    unittest.main()
