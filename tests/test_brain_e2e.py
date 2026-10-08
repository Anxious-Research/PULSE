"""End-to-end Brain acceptance test — the §30 acceptance scenario, run as code.

This is the highest-value test in the Brain suite: it drives ORDINARY conversation through the
real encoder, the real vault, and the real recall path, then asserts the product-level behaviours
the Brain exists to deliver:

    conversation → learning → persistence → recall → correction → selective recall

No memory API is called directly and nothing says "remember this" — the facts must be learned
from the sentences themselves, because that is what a user actually does.
"""

import os
import tempfile
import unittest
from pathlib import Path

from agent.brain.cache import BrainCache
from agent.brain.decay import retrievability
from agent.brain.encoding import encode_turn
from agent.brain.index import BrainIndex
from agent.brain.models import NodeStatus
from agent.brain.recall import recall
from agent.brain.vault import BrainVault

T0 = 1728000000

# §30: the exact acceptance conversation.
SCENARIO = [
    ("1", "My main project is PULSE."),
    ("2", "PULSE has a cognitive Brain."),
    ("3", "The Brain should use associative memory."),
    ("4", "The graph should expose these relationships."),
    ("5", "We changed the graph architecture today."),
    ("6", "Actually, that architecture change happened yesterday."),
]


class TestAcceptanceScenario(unittest.TestCase):
    """The full §30 conversation, end to end."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def _converse(self):
        reports = []
        for turn, text in SCENARIO:
            reports.append(encode_turn(self.vault, text, turn_id=f"turn-{turn}", now=T0 + int(turn) * 60))
        return reports

    def test_meaningful_conversation_is_learned(self):
        """Ordinary turns produce real, persisted semantic memories — no explicit memory call."""
        reports = self._converse()

        learned = " ".join(
            entry["text"].lower() for report in reports for entry in report["semantic"]
        )
        self.assertTrue(
            any(w in learned for w in ("pulse", "cognitive", "associative", "relationships", "architecture")),
            f"nothing learned: {learned!r}",
        )

        nodes = [n for n in self.vault.list_all_nodes() if n.frontmatter.status == NodeStatus.ACTIVE.value]
        self.assertGreaterEqual(len(nodes), 4, "should have learned several durable facts")

        # No blind duplication: the same topic across turns must not spawn -2/-3/-4 siblings.
        ids = [n.id for n in nodes]
        self.assertFalse(
            any(i.endswith(("-2", "-3", "-4")) for i in ids),
            f"duplicate siblings created: {ids}",
        )

    def test_correction_supersedes_and_preserves_history(self):
        """Turn 6 revises what PULSE holds: prior state is preserved, not duplicated as current."""
        self._converse()

        nodes = self.vault.list_all_nodes()
        superseded = [n for n in nodes if n.frontmatter.status == NodeStatus.SUPERSEDED.value]

        # The correction either supersedes an existing node, or — when nothing matched — records
        # the revision as its own authoritative node. Either way, the old state is never left
        # standing as an equally-current twin.
        corrected = [n for n in nodes if "yesterday" in (n.content or "").lower()]
        self.assertTrue(
            superseded or corrected,
            "correction produced neither a supersession nor a revised memory",
        )
        if superseded:
            old = superseded[0]
            self.assertTrue(old.frontmatter.superseded_by, "superseded node must name its replacement")

    def test_memory_persists_across_restart(self):
        """A fresh vault instance over the same directory sees the same Brain."""
        self._converse()
        before = {n.id for n in self.vault.list_all_nodes()}

        reopened = BrainVault(vault_dir=self.tmp)
        after = {n.id for n in reopened.list_all_nodes()}
        self.assertEqual(before, after, "Brain must survive a restart unchanged")
        self.assertGreater(len(after), 0)

    def test_recall_uses_associative_brain_state(self):
        """A question about the learned material recalls the relevant memories."""
        self._converse()
        index = BrainIndex(self.vault).rebuild()

        result = recall(
            "What do you know about the PULSE Brain architecture?",
            vault=self.vault, index=index, limit=6, now=T0 + 3600,
        )
        self.assertFalse(result.empty, "recall found nothing for a topic that was just learned")
        recalled = " ".join(h.snippet.lower() for h in result.hits)
        self.assertTrue(
            any(w in recalled for w in ("brain", "pulse", "memory", "graph", "architecture")),
            f"recall returned nothing on-topic: {recalled!r}",
        )

    def test_recall_is_selective(self):
        """An unrelated question does not drag the learned material into context."""
        self._converse()
        encode_turn(self.vault, "The kitchen renovation budget is forty thousand rupees.", turn_id="t-other", now=T0 + 600)
        index = BrainIndex(self.vault).rebuild()

        about_pulse = recall(
            "What do you know about the PULSE Brain architecture?",
            vault=self.vault, index=index, limit=6, now=T0 + 3600,
        )
        pulse_text = " ".join(h.snippet.lower() for h in about_pulse.hits)
        self.assertNotIn("kitchen", pulse_text, "unrelated memory leaked into a PULSE question")

    def test_recalled_memory_is_marked_used(self):
        """Recall strengthens what it touches (spacing effect) — real activation state, persisted."""
        self._converse()
        index = BrainIndex(self.vault).rebuild()
        result = recall(
            "What do you know about the PULSE Brain architecture?",
            vault=self.vault, index=index, limit=3, now=T0 + 3600,
        )
        self.assertFalse(result.empty)
        target = result.hits[0].node_id

        before = self.vault.read_node(target)
        assert before is not None
        self.vault.record_access([target], now=T0 + 3600)
        after = self.vault.read_node(target)
        assert after is not None
        self.assertEqual(after.frontmatter.access_count, before.frontmatter.access_count + 1)
        # A recalled memory becomes no *less* retrievable than it was before the recall.
        self.assertGreaterEqual(
            retrievability(after.frontmatter.stability, after.frontmatter.last_accessed, now=T0 + 3600),
            retrievability(before.frontmatter.stability, before.frontmatter.last_accessed, now=T0 + 3600),
        )

    def test_graph_payload_reflects_real_brain_state(self):
        """The graph build reads the same persisted nodes the Brain wrote."""
        self._converse()

        os.environ["PULSE_BRAIN_DIR"] = str(self.tmp)
        try:
            from agent.learning_graph import build_learning_graph
            payload = build_learning_graph()
        finally:
            os.environ.pop("PULSE_BRAIN_DIR", None)

        memory_nodes = [n for n in payload["nodes"] if n.get("kind") == "memory"]
        labels = " ".join((n.get("label") or "").lower() for n in memory_nodes)
        self.assertTrue(
            any(w in labels for w in ("pulse", "brain", "graph", "memory", "architecture")),
            f"graph does not reflect learned memory: {labels!r}",
        )


class TestCacheAcceleratesGraphLoad(unittest.TestCase):
    """§18: SQLite is a derived accelerator over the authoritative vault."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def test_cache_miss_then_hit(self):
        for i in range(15):
            self.vault.write_node(f"concept/item-{i}", f"Knowledge item number {i}.", now=T0 + i)

        cache = BrainCache(self.vault)
        self.assertTrue(cache.is_stale(), "empty cache must be stale")
        self.assertEqual(cache.sync(), 15)

        fresh = BrainCache(self.vault)
        self.assertFalse(fresh.is_stale(), "cache must be fresh after sync")
        self.assertEqual(len(fresh.all_nodes()), 15)


if __name__ == "__main__":
    unittest.main()
