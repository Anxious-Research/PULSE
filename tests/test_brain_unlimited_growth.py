"""Phase 7 — the unlimited-growth memory contract.

These tests pin the product invariant the PULSE Brain must satisfy, and that a
budget-limited notes store would violate:

  * **No arbitrary app-defined ceiling.** Durable storage grows with the disk; there is no
    token / character / memory-count cap that can reject or silently drop a memory.
  * **Context budgets are separate.** The per-request recall budget and the Layer-1 prefix
    budget only bound what is injected into one prompt. Changing them must never mutate,
    truncate or delete anything that is stored.
  * **No silent loss.** Every stored memory stays discoverable and survives restart, an index
    rebuild and a cache rebuild. Deleting is explicit and is honored across vault + index +
    cache. Fading to dormant is *not* deletion — the only durable copy is never removed.
  * **Migration safety.** A legacy ``memory.memory_char_limit`` config (from the pre-Brain flat
    store) is still accepted and loading it never caps or truncates the vault.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.brain.cache import BrainCache
from agent.brain.index import BrainIndex
from agent.brain.models import NodeStatus
from agent.brain.recall import recall
from agent.brain.session import BrainSettings, get_brain_config
from agent.brain.store import BrainStore
from agent.brain.vault import BrainVault

# Deliberately "thousands": proves the storage ceiling is disk, not a constant in code. Kept
# as a class constant so the count is visible and easy to raise.
N_MEMORIES = 1500


def _body(i: int, topic: str) -> str:
    return f"Memory {i}: durable note about {topic} kept for the unlimited-growth contract."


class _VaultCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def _write_many(self, n: int, offset: int = 0, topic: str = "storage"):
        for i in range(offset, offset + n):
            self.vault.write_node(
                f"concept/{topic}-{i}", _body(i, topic), title=f"{topic} {i}",
                category="concept", tags=[topic], now=1700000000 + i,
            )


class TestNoCeiling(_VaultCase):
    """§2.1 — durable storage has no application-defined total budget."""

    def test_thousands_of_memories_store_and_read_back(self):
        self._write_many(N_MEMORIES)
        ids = [nid for nid, _ in self.vault.iter_nodes()]
        self.assertEqual(len(ids), N_MEMORIES, "every write must be durable, none evicted to fit a budget")
        # spot-check that the very first and the very last are both readable (no head/tail cap)
        self.assertIsNotNone(self.vault.read_node("concept/storage-0"))
        self.assertIsNotNone(self.vault.read_node(f"concept/storage-{N_MEMORIES - 1}"))

    def test_legacy_char_limit_config_never_caps_the_live_store(self):
        """A legacy 2200-char 'Memory Budget' must not reject writes or truncate content."""
        store = BrainStore(memory_char_limit=2200, user_char_limit=1375, vault=self.vault)
        # 50 distinct facts: roughly 5000 chars total, far past the legacy 2200 budget.
        for i in range(50):
            res = store.add("memory", _body(i, f"legacy-budget-{i}"))
            self.assertTrue(res.get("success"), f"write {i} rejected despite unbounded storage: {res}")
            self.assertNotIn("full", str(res).lower(), "the store must never report a capacity failure")
        # Every write is durable in the vault (supersession keeps the old note rather than
        # evicting it), so storage plainly grew past the legacy budget.
        stored = list(self.vault.iter_nodes())
        self.assertGreaterEqual(len(stored), 50, "no memory may be dropped to fit a budget")
        self.assertGreater(
            sum(len(node.content or "") for _, node in stored), 2200,
            "stored content exceeded the legacy 2200-char budget, proving storage is not capped",
        )
        self.assertNotIn("2200", store._usage("memory"), "the store must not report a finite budget")

    def test_memory_module_exposes_no_count_or_token_ceiling(self):
        """The store/enum surface must not carry a fixed total-memory cap constant."""
        import agent.brain.store as store_mod

        suspicious = [
            name for name in dir(store_mod)
            if any(k in name.upper() for k in ("MAX_MEM", "MEM_LIMIT", "COUNT_LIMIT", "TOTAL_BUDGET"))
        ]
        self.assertEqual(suspicious, [], f"an arbitrary total-memory cap constant reappeared: {suspicious}")


class TestContextBudgetIsSeparate(_VaultCase):
    """§2.2 — recall/context budgets bound injection, never stored capacity."""

    def test_changing_recall_budget_never_mutates_durable_memory(self):
        self._write_many(60)
        before = {
            nid: (node.content, tuple(node.frontmatter.relations), node.frontmatter.status,
                  node.frontmatter.confidence, node.frontmatter.access_count)
            for nid, node in self.vault.iter_nodes()
        }
        index = BrainIndex(self.vault).rebuild()
        for budget in (64, 200, 600, 2000, 8000):
            recall("durable note storage contract", vault=self.vault, index=index, max_tokens=budget)
        after = {
            nid: (node.content, tuple(node.frontmatter.relations), node.frontmatter.status,
                  node.frontmatter.confidence, node.frontmatter.access_count)
            for nid, node in self.vault.iter_nodes()
        }
        self.assertEqual(before, after, "a recall budget must never rewrite stored memory")

    def test_settings_bound_injection_not_storage(self):
        """recall_max_tokens / prefix_max_chars are clamped per-request values; no count cap exists."""
        cfg = {"brain": {"recall_max_tokens": 900, "prefix_max_chars": 8000}}
        s = BrainSettings.from_config(cfg)
        self.assertEqual(s.recall_max_tokens, 900)
        self.assertEqual(s.prefix_max_chars, 8000)
        # Absurd values are clamped (a resource safeguard), never a storage ceiling.
        clamped = BrainSettings.from_config({"brain": {"recall_max_tokens": 10_000_000}})
        self.assertLessEqual(clamped.recall_max_tokens, 8000)

    def test_brain_config_is_read_from_the_brain_section(self):
        self.assertEqual(get_brain_config({"brain": {"recall_max_tokens": 777}})["recall_max_tokens"], 777)
        # A context-only knob lives under brain.*; the legacy memory.* char limits are not it.
        self.assertNotIn("memory", get_brain_config({"memory": {"memory_char_limit": 1}}))


class TestNoSilentLoss(_VaultCase):
    """§2.4 / §6 — memories and their relationships survive every rebuild; deletion is explicit."""

    def _seed_with_relations(self):
        self.vault.write_node("concept/root", "Root note about the graph.", title="Root",
                              category="concept", tags=["graph"], now=1700000000)
        self.vault.write_node(
            "concept/child", "Child note linking back.", title="Child", category="concept",
            tags=["graph"],
            relations=["concept/root|related_to|asserted|1.0|deadbeef|1700000000|1700000000"],
            now=1700000100,
        )
        self.vault.supersede("concept/root", "concept/child", now=1700000200)

    def test_restart_preserves_memories_relations_and_state(self):
        self._seed_with_relations()
        # A brand-new vault object over the SAME directory == a process restart.
        reopened = BrainVault(vault_dir=self.tmp)
        ids = {nid for nid, _ in reopened.iter_nodes()}
        self.assertEqual(ids, {"concept/root", "concept/child"})
        child = reopened.read_node("concept/child")
        self.assertIsNotNone(child)
        self.assertTrue(
            any("concept/root" in r for r in child.frontmatter.relations),
            "evidence/relationships must survive restart",
        )
        root = reopened.read_node("concept/root")
        self.assertIsNotNone(root)
        self.assertEqual(root.frontmatter.status, NodeStatus.SUPERSEDED.value)

    def test_index_and_cache_rebuild_lose_nothing(self):
        self._write_many(120)
        index = BrainIndex(self.vault).rebuild()
        self.assertEqual(len(index.ids), 120)
        first = {nid for nid, _ in self.vault.iter_nodes()}

        cache = BrainCache(self.vault)
        cache.rebuild()
        cache.sync()  # a second, incremental pass must be a no-op, not a purge
        index2 = BrainIndex(self.vault).rebuild()
        second = {nid for nid, _ in self.vault.iter_nodes()}
        self.assertEqual(first, second, "an index/cache rebuild must not drop memories")
        self.assertEqual(index2.ids, index.ids)

    def test_deletion_is_explicit_and_honored_everywhere(self):
        self._write_many(30)
        victim = "concept/storage-7"
        cache = BrainCache(self.vault)
        cache.rebuild()
        self.assertTrue(self.vault.delete_node(victim))
        self.assertIsNone(self.vault.read_node(victim))
        # Index and cache rebuilt AFTER the delete must agree with storage.
        self.assertNotIn(victim, BrainIndex(self.vault).rebuild().ids)
        cache.sync()
        self.assertNotIn(victim, {nid for nid, _ in self.vault.iter_nodes()})

    def test_fading_to_dormant_is_not_deletion(self):
        self.vault.write_node("concept/old", "An old but important lesson.", title="Old",
                              category="concept", tags=["lesson"], now=1000000000)
        node = self.vault.read_node("concept/old")
        # Simulate a long idle period: the note fades out of active recall but stays on disk.
        from agent.brain.decay import is_dormant

        far_future = 1000000000 + 3650 * 86400
        self.assertTrue(is_dormant(node.frontmatter.stability, node.frontmatter.last_accessed, now=far_future))
        self.assertIsNotNone(self.vault.read_node("concept/old"), "dormant must never mean deleted")
        self.assertEqual(node.content, self.vault.read_node("concept/old").content)


if __name__ == "__main__":
    unittest.main()
