"""Tests for PULSE Codebase Introspection and Self-Knowledge Generator."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.brain.vault import BrainVault
from agent.brain.introspect import CodebaseIntrospector


class TestBrainIntrospect(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="pulse_test_introspect_"))
        self.vault = BrainVault(self.temp_dir)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_file_inspection(self):
        introspector = CodebaseIntrospector()
        res = introspector.inspect_file("agent/learning_graph.py")
        self.assertTrue(res["exists"])
        self.assertGreater(res["lines"], 50)
        self.assertIn("SkillNode", res["classes"])

    def test_generate_full_self_knowledge(self):
        introspector = CodebaseIntrospector()
        count = introspector.generate_full_self_knowledge(self.vault)
        self.assertGreaterEqual(count, 5)

        # Check that wiring index exists
        wiring = self.vault.read_node("self/wiring")
        self.assertIsNotNone(wiring)
        self.assertIn("PULSE Complete Architecture", wiring.content)

        # Check that agent-runtime subsystem was indexed
        runtime = self.vault.read_node("self/subsystems/agent-runtime")
        self.assertIsNotNone(runtime)
        self.assertIn("run_agent.py", runtime.content)
        self.assertIn("agent/turn_context.py", runtime.content)

        # Check that brain-engine subsystem was indexed
        brain_sub = self.vault.read_node("self/subsystems/brain-engine")
        self.assertIsNotNone(brain_sub)
        self.assertIn("agent/brain/vault.py", brain_sub.content)


if __name__ == "__main__":
    unittest.main()
