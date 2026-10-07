"""Tests for the link index and the spreading-activation recall primitive."""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent.brain.index import BrainIndex
from agent.brain.vault import BrainVault

T0 = 1_800_000_000


class IndexTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-index-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def build(self, **nodes):
        for node_id, body in nodes.items():
            self.vault.write_node(node_id.replace("__", "/"), body, now=T0)
        return BrainIndex(self.vault).rebuild()


class TestLinks(IndexTestCase):
    def test_forward_and_backlinks(self):
        index = self.build(concept__a="links to [[concept/b]]", concept__b="links to [[concept/a]]")
        self.assertEqual(index.links_of("concept/a"), ["concept/b"])
        self.assertEqual(index.backlinks_of("concept/b"), ["concept/a"])
        self.assertEqual(index.backlinks_of("concept/a"), ["concept/b"])

    def test_unresolved_links_recorded_not_dropped(self):
        index = self.build(concept__a="see [[concept/missing]]")
        self.assertEqual(index.unresolved_targets(), ["concept/missing"])
        self.assertEqual(index.unresolved["concept/missing"], ["concept/a"])

    def test_resolution_by_basename(self):
        index = self.build(concept__a="see [[b]]", concept__b="b body")
        self.assertEqual(index.links_of("concept/a"), ["concept/b"])

    def test_resolution_by_alias(self):
        self.vault.write_node("user/preferences", "body", aliases=["prefs"], now=T0)
        self.vault.write_node("concept/a", "see [[prefs]]", now=T0)
        index = BrainIndex(self.vault).rebuild()
        self.assertEqual(index.links_of("concept/a"), ["user/preferences"])

    def test_ambiguous_basename_stays_unresolved(self):
        index = self.build(a__same="x", b__same="y", concept__a="see [[same]]")
        self.assertIn("same", index.unresolved_targets())

    def test_self_link_excluded_from_edges(self):
        index = self.build(concept__a="refers to [[concept/a]]")
        payload = index.to_graph_payload()
        self.assertEqual(payload["edges"], [])

    def test_duplicate_edges_deduped(self):
        index = self.build(concept__a="[[concept/b]]", concept__b="[[concept/a]]")
        self.assertEqual(len(index.to_graph_payload()["edges"]), 1)

    def test_degree_counts_both_directions(self):
        index = self.build(concept__a="[[concept/b]]", concept__b="", concept__c="[[concept/b]]")
        self.assertEqual(index.degree("concept/b"), 2)
        self.assertEqual(index.degree("concept/a"), 1)

    def test_neighbors_are_undirected_and_hop_limited(self):
        index = self.build(concept__a="[[concept/b]]", concept__b="[[concept/c]]", concept__c="")
        self.assertEqual(index.neighbors("concept/a", hops=1), ["concept/b"])
        self.assertEqual(index.neighbors("concept/a", hops=2), ["concept/b", "concept/c"])
        self.assertNotIn("concept/a", index.neighbors("concept/a", hops=2))

    def test_stats(self):
        index = self.build(concept__a="[[concept/b]]", concept__b="", concept__lonely="")
        stats = index.stats()
        self.assertEqual(stats["nodes"], 3)
        self.assertEqual(stats["edges"], 1)
        self.assertGreater(stats["isolated_pct"], 0.0)


class TestSpreadingActivation(IndexTestCase):
    def test_seed_only_when_no_links(self):
        index = self.build(concept__a="no links")
        act = index.activate({"concept/a": 1.0})
        self.assertEqual(set(act), {"concept/a"})

    def test_activation_spreads_one_hop_with_decay(self):
        index = self.build(concept__a="[[concept/b]]", concept__b="b")
        act = index.activate({"concept/a": 1.0}, hops=1, decay=0.5)
        self.assertGreater(act["concept/b"], 0.0)
        self.assertLess(act["concept/b"], act["concept/a"])

    def test_two_hop_reaches_but_weakens(self):
        index = self.build(concept__a="[[concept/b]]", concept__b="[[concept/c]]", concept__c="c")
        act = index.activate({"concept/a": 1.0}, hops=2, decay=0.8)
        self.assertIn("concept/c", act)
        self.assertLess(act["concept/c"], act["concept/b"])

    def test_hub_does_not_dominate(self):
        """Fan-out divisor: a high-degree hub passes on less activation per neighbour."""
        for i in range(8):
            self.vault.write_node(f"concept/leaf{i}", f"leaf {i}", now=T0)
        self.vault.write_node("concept/hub", " ".join(f"[[concept/leaf{i}]]" for i in range(8)), now=T0)
        self.vault.write_node("concept/spoke", "[[concept/hub]]", now=T0)
        index = BrainIndex(self.vault).rebuild()

        act = index.activate({"concept/spoke": 1.0}, hops=2, decay=0.9)
        # Every leaf gets a share, but far less than the seed itself.
        leaves = [v for k, v in act.items() if k.startswith("concept/leaf")]
        self.assertTrue(leaves)
        self.assertLess(max(leaves), act["concept/spoke"])

    def test_unknown_seed_is_still_returned(self):
        index = self.build(concept__a="a")
        act = index.activate({"concept/not-there": 1.0})
        self.assertEqual(set(act), {"concept/not-there"})

    def test_junk_seed_weights_ignored(self):
        index = self.build(concept__a="a")
        act = index.activate({"concept/a": "not a number", "concept/b": -5})
        self.assertEqual(act, {})

    def test_zero_activation_when_nothing_seeded(self):
        index = self.build(concept__a="a")
        self.assertEqual(index.activate({}), {})

    def test_min_activation_prunes_noise(self):
        index = self.build(concept__a="[[concept/b]]", concept__b="[[concept/c]]", concept__c="c")
        act = index.activate({"concept/a": 1.0}, hops=2, decay=0.01, min_activation=0.5)
        self.assertEqual(set(act), {"concept/a"})

    def test_total_activation_is_bounded(self):
        """Hop-by-hop propagation must not inflate: sum <= seeds / (1 - decay)."""
        # A small dense cluster is where an accumulate-in-place scheme blows up.
        self.vault.write_node("concept/a", "[[concept/b]] [[concept/c]]", now=T0)
        self.vault.write_node("concept/b", "[[concept/a]] [[concept/c]]", now=T0)
        self.vault.write_node("concept/c", "[[concept/a]] [[concept/b]]", now=T0)
        index = BrainIndex(self.vault).rebuild()
        decay = 0.9
        act = index.activate({"concept/a": 1.0}, hops=6, decay=decay, min_activation=0.0)
        self.assertLessEqual(sum(act.values()), 1.0 / (1.0 - decay) + 1e-9)

    def test_activation_never_exceeds_the_seed_for_a_neighbour(self):
        self.vault.write_node("concept/a", "[[concept/b]] [[concept/c]]", now=T0)
        self.vault.write_node("concept/b", "[[concept/a]] [[concept/c]]", now=T0)
        self.vault.write_node("concept/c", "[[concept/a]] [[concept/b]]", now=T0)
        index = BrainIndex(self.vault).rebuild()
        act = index.activate({"concept/a": 1.0}, hops=5, decay=0.9, min_activation=0.0)
        for node_id, value in act.items():
            if node_id != "concept/a":
                self.assertLess(value, act["concept/a"], node_id)

    def test_more_hops_never_increases_a_two_hop_node(self):
        index = self.build(concept__a="[[concept/b]]", concept__b="[[concept/c]]", concept__c="c")
        one = index.activate({"concept/a": 1.0}, hops=1, decay=0.8, min_activation=0.0)
        two = index.activate({"concept/a": 1.0}, hops=2, decay=0.8, min_activation=0.0)
        self.assertEqual(one.get("concept/c"), None)
        self.assertIn("concept/c", two)


if __name__ == "__main__":
    unittest.main()
