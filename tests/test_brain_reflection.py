"""Tests for Cognitive Sleep & Brain Reflection."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.brain.vault import BrainVault
from agent.brain.reflection import reflect_and_consolidate_brain


class TestBrainReflection(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="pulse_test_reflection_"))
        self.vault = BrainVault(self.temp_dir)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_reflection_cycle(self):
        # Create a concept node
        self.vault.write_node(
            node_id="concept/agentic-coding",
            content="Agentic coding involves proactive tool use and deep introspection.",
            title="Agentic Coding",
            category="concept",
        )

        # Run reflection
        res = reflect_and_consolidate_brain(self.vault)
        self.assertGreaterEqual(res["self_notes_synced"], 5)
        self.assertGreaterEqual(res["total_nodes"], 6)
        self.assertNotIn("error", res)


if __name__ == "__main__":
    unittest.main()
