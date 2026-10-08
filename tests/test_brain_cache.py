"""§9 SQLite cache layer tests (brain.md v3).

Covers: full rebuild, staleness detection, incremental sync, FTS5 search,
spreading-activation recall, edge extraction, and rebuild-from-scratch safety.
All tests use a temp vault — the live ~/.pulse brain is never touched.
"""

import tempfile
import time
import unittest
from pathlib import Path

from agent.brain.cache import BrainCache, SCHEMA_VERSION
from agent.brain.vault import BrainVault


class BrainCacheTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="braincache_test_"))
        self.vault = BrainVault(self.tmp)
        self.vault.ensure_vault_structure()
        self.vault.write_node(
            "concept/alpha", "Alpha links to [[concept/beta]] and [[gamma]].", title="Alpha"
        )
        self.vault.write_node(
            "concept/beta", "Beta is about maritime recall and spreading activation.", title="Beta"
        )
        self.vault.write_node("concept/gamma", "Gamma covers cooling schedules.", title="Gamma")
        self.vault.write_node(
            "user/preferences", "The user prefers concise answers.", title="Preferences"
        )
        self.cache = BrainCache(self.vault)

    def tearDown(self):
        self.cache.close()

    def test_rebuild_indexes_every_node(self):
        self.assertEqual(self.cache.rebuild(), 4)
        self.assertFalse(self.cache.is_stale())

    def test_empty_cache_is_stale(self):
        self.assertTrue(self.cache.is_stale())
        self.assertTrue(self.cache.is_empty())

    def test_edges_from_wikilinks(self):
        self.cache.rebuild()
        edges = set(self.cache.all_edges())
        self.assertIn(("concept/alpha", "concept/beta", "wikilink"), edges)
        # Unresolved wikilink target is still an edge (raw target preserved).
        self.assertIn(("concept/alpha", "gamma", "wikilink"), edges)

    def test_fts_search(self):
        self.cache.rebuild()
        self.assertEqual([n["id"] for n in self.cache.search("maritime")], ["concept/beta"])
        self.assertEqual(self.cache.search("nonexistentterm"), [])

    def test_recall_spreads_one_hop(self):
        self.cache.rebuild()
        hits = {h["id"] for h in self.cache.recall(["alpha"], limit=5)}
        # Direct hit + its wikilink neighbours surface together.
        self.assertIn("concept/alpha", hits)
        self.assertIn("concept/beta", hits)

    def test_incremental_sync_only_touches_changed(self):
        self.cache.rebuild()
        self.assertFalse(self.cache.is_stale())
        time.sleep(1.1)
        self.vault.write_node("concept/beta", "Beta now mentions keel and ballast.", title="Beta")
        self.assertTrue(self.cache.is_stale())
        self.assertEqual(self.cache.sync(), 1)
        self.assertEqual([n["id"] for n in self.cache.search("keel")], ["concept/beta"])

    def test_ensure_fresh_is_noop_when_current(self):
        self.cache.rebuild()
        self.assertEqual(self.cache.ensure_fresh(), 0)

    def test_rebuild_survives_deleted_db(self):
        self.cache.rebuild()
        db_path = self.cache.db_path
        self.cache.close()
        db_path.unlink()
        fresh = BrainCache(self.vault)
        try:
            self.assertTrue(fresh.is_empty())
            self.assertEqual(fresh.rebuild(), 4)
        finally:
            fresh.close()

    def test_schema_version_recorded(self):
        self.cache.rebuild()
        self.assertEqual(self.cache._get_meta("schema_version"), str(SCHEMA_VERSION))

    def test_update_node_reflects_edit(self):
        self.cache.rebuild()
        self.vault.write_node("concept/gamma", "Gamma now covers cooling and annealing.", title="Gamma")
        self.cache.update_node("concept/gamma")
        self.assertEqual([n["id"] for n in self.cache.search("annealing")], ["concept/gamma"])

    def test_empty_cues_returns_nothing(self):
        self.cache.rebuild()
        self.assertEqual(self.cache.recall([]), [])
        self.assertEqual(self.cache.recall(["   "]), [])


if __name__ == "__main__":
    unittest.main()
