"""Tests for BrainVault integration in learning_graph and learning_mutations (specs/brain.md §7).

§7 requires the map to be the vault, not a lexical approximation of it: nodes carry the
vault's own category/confidence/tags, edges are resolved ``[[wikilinks]]``, and a link whose
target does not exist yet still shows up as a ghost node.
"""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent.brain.store import BrainStore
from agent.brain.vault import BrainVault
from agent.learning_graph import _memory_cards, build_learning_graph, memory_node_id
from agent.learning_mutations import _mutate_memory, resolve_ghost


class TestBrainLearningGraph(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="pulse-learning-graph-test-")
        self.tmp_path = Path(self._tmpdir.name)
        self.vault_dir = self.tmp_path / "brain"
        self.vault = BrainVault(self.vault_dir)
        self.vault.ensure_vault_structure()
        self.store = BrainStore(vault=self.vault)
        self._patches = [
            patch("agent.brain.vault.get_brain_vault_dir", return_value=self.vault_dir),
            patch("agent.learning_graph.get_pulse_home", return_value=self.tmp_path),
            patch("pulse_constants.get_pulse_home", return_value=self.tmp_path),
            patch("tools.memory_tool.load_on_disk_store", return_value=self.store),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def tearDown(self):
        self._tmpdir.cleanup()

    # -- cards -------------------------------------------------------------

    def test_memory_cards_reads_vault_nodes(self):
        self.store.add("user", "User prefers dark mode")
        self.store.add("memory", "PULSE runs SQLite in WAL mode")

        cards = _memory_cards()
        self.assertEqual(len(cards), 2)
        sources = {c["source"] for c in cards}
        self.assertIn("profile", sources)
        self.assertIn("memory", sources)
        titles = [c["title"] for c in cards]
        self.assertTrue(any("dark mode" in t for t in titles))
        self.assertTrue(any("SQLite" in t for t in titles))
        # Every vault card carries the id the graph needs for edit/delete round-trips.
        self.assertTrue(all(c.get("node_id") for c in cards))

    # -- §7 graph payload --------------------------------------------------

    def _seed_linked_vault(self):
        self.vault.write_node("alpha", "Alpha relates to [[beta]] and [[An Unwritten Idea]].",
                              title="Alpha", category="concept")
        self.vault.write_node("beta", "Beta is a project note.", title="Beta", category="project")
        return _memory_cards()

    def test_graph_nodes_carry_vault_category_confidence_and_tags(self):
        self._seed_linked_vault()

        graph = build_learning_graph()
        by_id = {n["id"]: n for n in graph["nodes"]}
        cards = _memory_cards()
        # ``vaultId`` is the vault's own node id, i.e. the note's path under the vault
        # ("concept/alpha"): write_node normalises a bare id by prefixing its category, so
        # the raw string passed to write_node is not the id the note ends up with.
        alpha = next(n for n in graph["nodes"] if n.get("vaultId") == "concept/alpha")
        alpha_card = next(c for c in cards if c["node_id"] == "concept/alpha")

        self.assertEqual(alpha["kind"], "memory")
        self.assertEqual(alpha["category"], "concept")  # not the placeholder "memory"
        self.assertIn("confidence", alpha)
        self.assertIsInstance(alpha["tags"], list)
        # The cluster tally uses the vault categories too, so the filter panel can trust it.
        cats = {c["category"] for c in graph["clusters"]}
        self.assertIn("concept", cats)
        self.assertIn("project", cats)
        self.assertEqual(len(by_id), len(graph["nodes"]))
        self.assertTrue(cards)
        self.assertTrue(alpha_card)

    def test_graph_edges_are_resolved_wikilinks(self):
        self._seed_linked_vault()

        graph = build_learning_graph()
        by_id = {n["id"]: n for n in graph["nodes"]}
        alpha_id = next(n["id"] for n in graph["nodes"] if n.get("vaultId") == "concept/alpha")
        beta_id = next(n["id"] for n in graph["nodes"] if n.get("vaultId") == "concept/beta")

        wikilinks = [e for e in graph["edges"] if e.get("kind") == "wikilink"]
        self.assertTrue(
            any(e["source"] == alpha_id and e["target"] == beta_id for e in wikilinks),
            "the resolved alpha->beta wikilink edge must be present",
        )
        # Typed-edge contract: an explicit wikilink is asserted at full confidence.
        ab = next(e for e in wikilinks if e["source"] == alpha_id and e["target"] == beta_id)
        self.assertEqual(ab["provenance"], "asserted")
        self.assertEqual(ab["confidence"], 1.0)
        # Direction is preserved: beta does not link back to alpha.
        self.assertFalse(
            any(e["source"] == beta_id and e["target"] == alpha_id for e in wikilinks),
        )
        self.assertGreaterEqual(graph["stats"]["vault_edges"], 1)
        self.assertIn(alpha_id, by_id)

    def test_unresolved_link_becomes_a_ghost_node(self):
        self._seed_linked_vault()

        graph = build_learning_graph()
        ghosts = [n for n in graph["nodes"] if n["kind"] == "ghost"]
        self.assertTrue(ghosts, "a dangling [[link]] must render as a ghost node")
        self.assertTrue(any("unwritten" in n["label"].lower() for n in ghosts))
        ghost_ids = {n["id"] for n in ghosts}
        alpha_id = next(n["id"] for n in graph["nodes"] if n.get("vaultId") == "concept/alpha")
        self.assertTrue(
            any(e["source"] == alpha_id and e["target"] in ghost_ids for e in graph["edges"]),
            "the ghost node must be attached to the note that links to it",
        )
        self.assertEqual(graph["stats"]["ghost_nodes"], len(ghosts))

    # -- edits through a graph id ------------------------------------------

    def test_learning_mutations_mutate_vault_memory(self):
        self.store.add("user", "User prefers dark theme")

        cards = _memory_cards()
        self.assertEqual(len(cards), 1)
        res = _mutate_memory(memory_node_id(cards[0], 0), "User prefers light theme")
        self.assertTrue(res["ok"])
        self.assertEqual(self.store.user_entries, ["User prefers light theme"])

        cards_after = _memory_cards()
        res_del = _mutate_memory(memory_node_id(cards_after[0], 0), None)
        self.assertTrue(res_del["ok"])
        self.assertEqual(len(self.store.user_entries), 0)

    def test_mutation_resolves_a_note_longer_than_the_card_excerpt(self):
        """The id digests the whole note; a >1200-char note must still be editable."""
        long_note = "User prefers dark mode. " + ("Detail sentence about the preference. " * 60)
        self.assertGreater(len(long_note), 1200)
        self.store.add("user", long_note)

        cards = _memory_cards()
        self.assertEqual([c["title"][:20] for c in cards], [cards[0]["title"][:20]])
        res = _mutate_memory(memory_node_id(cards[0], 0), "User prefers light mode")
        self.assertTrue(res["ok"], res)
        self.assertEqual(self.store.user_entries, ["User prefers light mode"])


class TestGhostResolution(unittest.TestCase):
    """§7: a ghost node is a link to a note that does not exist yet — creating that
    note is what makes the link resolve, so the ghost must vanish from the next graph."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory(prefix="pulse-ghost-test-")
        self.tmp_path = Path(self._tmpdir.name)
        self.vault_dir = self.tmp_path / "brain"
        self.vault = BrainVault(self.vault_dir)
        self.vault.ensure_vault_structure()
        self.store = BrainStore(vault=self.vault)
        self._patches = [
            patch("agent.brain.vault.get_brain_vault_dir", return_value=self.vault_dir),
            patch("agent.learning_graph.get_pulse_home", return_value=self.tmp_path),
            patch("pulse_constants.get_pulse_home", return_value=self.tmp_path),
            patch("agent.brain.session.get_write_vault", return_value=self.vault),
            patch("tools.memory_tool.load_on_disk_store", return_value=self.store),
        ]
        for p in self._patches:
            p.start()
        self.addCleanup(lambda: [p.stop() for p in self._patches])

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_resolving_a_ghost_creates_the_note_and_clears_the_link(self):
        self.vault.write_node("concept/alpha", "Alpha relates to [[An Unwritten Idea]].",
                              title="Alpha", category="concept")
        before = build_learning_graph()
        ghosts = [n for n in before["nodes"] if n["kind"] == "ghost"]
        self.assertEqual([g["label"] for g in ghosts], ["An Unwritten Idea"])

        res = resolve_ghost("ghost:An Unwritten Idea", "# An Unwritten Idea\nNow it exists.")

        self.assertTrue(res["ok"], res)
        after = build_learning_graph()
        self.assertEqual([n["id"] for n in after["nodes"] if n["kind"] == "ghost"], [])
        # The new note is a real node, and the link that was dangling is now an edge.
        self.assertIn("An Unwritten Idea", [n["label"] for n in after["nodes"]])
        self.assertEqual(after["stats"]["ghost_nodes"], 0)
        alpha_id = next(n["id"] for n in after["nodes"] if n.get("vaultId") == "concept/alpha")
        created_id = next(n["id"] for n in after["nodes"] if "Unwritten" in n["label"])
        wikilinks = [e for e in after["edges"] if e.get("kind") == "wikilink"]
        self.assertTrue(
            any(e["source"] == alpha_id and e["target"] == created_id for e in wikilinks),
            "the formerly-dangling link must now be a resolved wikilink edge",
        )

    def test_resolve_ghost_rejects_a_real_node_id(self):
        """Editing a real node goes through edit_node; this path only creates ghosts."""
        self.store.add("user", "User prefers dark mode")
        real_id = memory_node_id(_memory_cards()[0], 0)
        res = resolve_ghost(real_id, "anything")
        self.assertFalse(res["ok"])
        self.assertIn("not a ghost node", res["message"])

    def test_resolve_ghost_rejects_empty_content(self):
        res = resolve_ghost("ghost:Something", "   ")
        self.assertFalse(res["ok"])
        self.assertIn("empty note content", res["message"])


if __name__ == "__main__":
    unittest.main()
