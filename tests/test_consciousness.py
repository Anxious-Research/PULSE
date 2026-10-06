"""Tests for PULSE Associative Consciousness & Multi-Hop Spreading Activation."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.brain.vault import BrainVault
from agent.brain.belief import BeliefEngine
from agent.brain.consciousness import ConsciousnessEngine


class TestAssociativeConsciousness(unittest.TestCase):

    def setUp(self):
        self.temp_dir = Path(tempfile.mkdtemp(prefix="pulse_test_conscious_"))
        self.vault = BrainVault(self.temp_dir)
        self.vault.ensure_vault_structure()
        self.engine = ConsciousnessEngine(self.vault)

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_spreading_activation_and_misconception_guard(self):
        # 1. Create a chain: Prompt -> [concept/python] -> [[concept/asyncio]] -> [[belief/concurrency]]
        self.vault.write_node(
            node_id="concept/python",
            content="Python features high-level data structures and links to [[concept/asyncio]].",
            title="Python Language",
            category="concept",
            tags=["python"],
        )

        self.vault.write_node(
            node_id="concept/asyncio",
            content="AsyncIO provides asynchronous I/O and task concurrency in [[concept/python]].",
            title="AsyncIO Architecture",
            category="concept",
            tags=["concurrency", "async"],
        )

        # 2. Add an evolving belief
        be = BeliefEngine(self.vault)
        old_b = be.record_belief(
            title="AsyncIO Threads Misconception",
            content="AsyncIO uses operating system threads for every coroutine.",
            confidence=0.5,
        )
        new_b, _ = be.evolve_belief(
            old_belief_id=old_b.id,
            new_title="Single-Threaded Event Loop (True Model)",
            new_content="AsyncIO runs on a single-threaded cooperative event loop.",
            reason="Verified Python stdlib asyncio documentation",
            new_confidence=1.0,
        )

        # 3. Link AsyncIO concept to the new belief
        self.vault.write_node(
            node_id="concept/asyncio",
            content="AsyncIO provides cooperative scheduling linked to [[beliefs/Single-Threaded Event Loop (True Model)]].",
            title="AsyncIO Architecture",
            category="concept",
            tags=["concurrency", "async"],
        )

        # 4. Trigger consciousness stream with a cue about "python"
        stream = self.engine.format_consciousness_stream("How does python concurrency work under the hood?")
        self.assertIsNotNone(stream)

        # Verify:
        # - Primary node (Python) is activated
        self.assertIn("Python Language", stream)
        # - 2-Hop associative node (AsyncIO) is activated
        self.assertIn("AsyncIO", stream)
        # - Active belief is loaded
        self.assertIn("Single-Threaded Event Loop", stream)
        # - Superseded misconception alert is flagged
        self.assertIn("Historical Misconception Alert", stream)
        self.assertIn("AsyncIO Threads Misconception", stream)


if __name__ == "__main__":
    unittest.main()
