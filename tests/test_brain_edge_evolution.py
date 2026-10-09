"""Priority-1 (edge lifecycle) + Priority-2 (memory genuinely evolves) longitudinal tests.

Everything runs in an isolated PULSE_HOME so the user's live vault is never touched. These are
behavioural tests of the *typed edge model* end to end (persistence round-trip, legacy payloads,
API serialization, supersession integrity, invalid confidence, duplicate evidence) and of
whether memory **evolves** rather than accumulating as a static notes graph.
"""

import os
import tempfile
import time
import unittest


class _IsolatedBrain(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="pulse-evolve-")
        self._prev = os.environ.get("PULSE_HOME")
        os.environ["PULSE_HOME"] = self._home
        from agent.brain.vault import BrainVault
        self.vault = BrainVault()
        self.vault.ensure_vault_structure()

    def tearDown(self):
        import shutil
        if self._prev is None:
            os.environ.pop("PULSE_HOME", None)
        else:
            os.environ["PULSE_HOME"] = self._prev
        shutil.rmtree(self._home, ignore_errors=True)


class TestEdgePersistenceRoundTrip(_IsolatedBrain):
    def test_typed_relation_survives_disk_round_trip(self):
        from agent.brain.relations import Relation, RelationType, Provenance
        tok = Relation(
            target="concept/pulse", rel_type=RelationType.MENTIONS.value,
            provenance=Provenance.INFERRED_ENTITY.value, confidence=0.5, evidence=["h1"],
        ).to_token()
        self.vault.write_node("concept/pulse", "PULSE is the project.", category="concept")
        self.vault.write_node("concept/recall", "Recall uses PULSE.", category="concept",
                              relations=[tok])
        # Re-read from a fresh vault instance (forces parse from disk).
        from agent.brain.vault import BrainVault
        node = BrainVault().read_node("concept/recall")
        rels = node.typed_relations()
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0].target, "concept/pulse")
        self.assertEqual(rels[0].rel_type, RelationType.MENTIONS.value)
        self.assertEqual(rels[0].confidence, 0.5)

    def test_writing_relations_keeps_related_in_sync_for_legacy_readers(self):
        from agent.brain.relations import Relation, RelationType
        tok = Relation(target="concept/pulse", rel_type=RelationType.MENTIONS.value).to_token()
        self.vault.write_node("concept/pulse", "PULSE.", category="concept")
        self.vault.write_node("concept/x", "X mentions PULSE.", category="concept", relations=[tok])
        node = self.vault.read_node("concept/x")
        self.assertIn("concept/pulse", node.frontmatter.related)


class TestLegacyPayloadCompat(_IsolatedBrain):
    def test_legacy_node_without_relations_derives_typed_edges(self):
        # A node written the OLD way: only 'related' + a body [[wikilink]], no 'relations' field.
        self.vault.write_node("concept/a", "A links to [[concept/b]].", category="concept")
        self.vault.write_node("concept/b", "B.", category="concept", related=["concept/a"])
        from agent.brain.index import BrainIndex
        payload = BrainIndex(self.vault).rebuild().to_graph_payload()
        for e in payload["edges"]:
            self.assertIn("kind", e)
            self.assertIn("provenance", e)
            self.assertIn("confidence", e)
            self.assertTrue(0.0 <= e["confidence"] <= 1.0)
        # the explicit wikilink is asserted, not merely inferred
        kinds = {e["kind"] for e in payload["edges"]}
        self.assertIn("wikilink", kinds)


class TestApiSerialization(_IsolatedBrain):
    def test_learning_graph_edges_carry_full_contract(self):
        from agent.brain.relations import Relation, RelationType
        self.vault.write_node("concept/pulse", "PULSE.", category="concept")
        tok = Relation(target="concept/pulse", rel_type=RelationType.MENTIONS.value, confidence=0.5).to_token()
        self.vault.write_node("concept/recall", "Recall uses PULSE.", category="concept", relations=[tok])
        from agent.learning_graph import build_learning_graph
        payload = build_learning_graph()
        for e in payload["edges"]:
            self.assertEqual({"source", "target", "kind", "provenance", "confidence"},
                             set(e) & {"source", "target", "kind", "provenance", "confidence"})
            self.assertTrue(0.0 <= e["confidence"] <= 1.0)


class TestEdgeIntegrityOnSupersession(_IsolatedBrain):
    def test_superseding_a_node_removes_its_active_edges(self):
        from agent.brain.relations import Relation, RelationType
        from agent.brain.index import BrainIndex
        self.vault.write_node("concept/arch-a", "Architecture A is current.", category="concept")
        tok = Relation(target="concept/arch-a", rel_type=RelationType.MENTIONS.value, confidence=0.5).to_token()
        self.vault.write_node("concept/design", "The design uses Architecture A.", category="concept",
                              relations=[tok])
        before = BrainIndex(self.vault).rebuild().to_graph_payload()
        self.assertTrue(any({"concept/arch-a", "concept/design"} == {e["source"], e["target"]}
                            for e in before["edges"]))
        # Supersede arch-a with arch-b.
        self.vault.write_node("concept/arch-b", "Architecture B replaces A.", category="concept")
        self.assertTrue(self.vault.supersede("concept/arch-a", "concept/arch-b"))
        after = BrainIndex(self.vault).rebuild().to_graph_payload()
        # No active edge may still point at the superseded node (req #9: no misleading edges).
        self.assertFalse(any("concept/arch-a" in (e["source"], e["target"]) for e in after["edges"]),
                         "superseded node must not keep active edges")

    def test_edges_never_reference_a_nonexistent_node(self):
        from agent.brain.relations import Relation, RelationType
        from agent.brain.index import BrainIndex
        tok = Relation(target="concept/ghost", rel_type=RelationType.MENTIONS.value, confidence=0.5).to_token()
        self.vault.write_node("concept/real", "Real mentions a ghost.", category="concept", relations=[tok])
        payload = BrainIndex(self.vault).rebuild().to_graph_payload()
        ids = set(payload["nodes"])
        for e in payload["edges"]:
            self.assertIn(e["source"], ids)
            self.assertIn(e["target"], ids)


class TestInvalidConfidence(_IsolatedBrain):
    def test_out_of_range_confidence_is_clamped_on_persist(self):
        from agent.brain.relations import Relation, RelationType
        # Hand-craft an out-of-range token (simulating a corrupt / hand-edited file).
        bad = f"concept/pulse|{RelationType.MENTIONS.value}|inferred_entity|9.9||"
        self.vault.write_node("concept/pulse", "PULSE.", category="concept")
        self.vault.write_node("concept/y", "Y.", category="concept", relations=[bad])
        node = self.vault.read_node("concept/y")
        rels = node.typed_relations()
        self.assertTrue(all(0.0 <= r.confidence <= 1.0 for r in rels))


class TestDuplicateEvidenceDoesNotInflate(_IsolatedBrain):
    def test_reconsolidating_identical_text_does_not_raise_confidence(self):
        from agent.brain.consolidate import relink_mentions
        # Two notes that mention each other's entity.
        self.vault.write_node("concept/forgetting-curve", "The forgetting curve governs decay.", category="concept")
        self.vault.write_node("concept/decay-note", "Decay follows the Forgetting Curve closely.", category="concept")
        relink_mentions(self.vault, now=time.time())
        n1 = self.vault.read_node("concept/decay-note")
        rels1 = {(r.target, r.rel_type): r.confidence for r in n1.typed_relations()}
        # Run consolidation again over the SAME unchanged text.
        relink_mentions(self.vault, now=time.time() + 10)
        n2 = self.vault.read_node("concept/decay-note")
        rels2 = {(r.target, r.rel_type): r.confidence for r in n2.typed_relations()}
        self.assertEqual(rels1, rels2, "identical evidence must not inflate confidence")


class TestMemoryEvolves(_IsolatedBrain):
    """Priority-2: deterministic longitudinal checks that memory evolves, not just accumulates."""

    def test_progressive_association_builds_then_correction_updates_belief(self):
        from agent.brain.index import BrainIndex
        from agent.brain.models import NodeStatus

        # 1. Multiple related experiences build a meaningful association. Here the association
        #    is asserted through explicit [[wikilinks]] the notes write about a shared concept —
        #    a real, defensible relationship (not lexical coincidence).
        self.vault.write_node("concept/hebbian", "Hebbian learning strengthens co-active links.", category="concept")
        self.vault.write_node("concept/recall-note", "Recall relies on [[concept/hebbian]] to retrieve cues.", category="concept")
        self.vault.write_node("concept/assoc-note", "Associations form through [[concept/hebbian]] over time.", category="concept")

        from agent.brain.consolidate import relink_mentions
        relink_mentions(self.vault, now=time.time())
        payload = BrainIndex(self.vault).rebuild().to_graph_payload()
        # Real associations formed around the specific shared concept (not an empty graph).
        self.assertTrue(payload["edges"], "related experiences must form associations")
        # The association is a defensible typed edge, not an arbitrary label.
        self.assertTrue(all(e["kind"] in ("mentions", "wikilink", "related_to", "supersedes")
                            for e in payload["edges"]))
        # The two notes both connect to the shared concept — a hub forms organically.
        hub_edges = [e for e in payload["edges"] if "concept/hebbian" in (e["source"], e["target"])]
        self.assertGreaterEqual(len(hub_edges), 2, "the shared concept should become a small hub")

        # 2. A corrected fact updates the active belief but keeps correction provenance.
        self.vault.write_node("project/arch-a", "The project migrated to architecture A today.", category="project")
        self.vault.write_node("project/arch-b", "The project migrated to architecture B.", category="project")
        self.assertTrue(self.vault.supersede("project/arch-a", "project/arch-b"))

        # Active belief is B; A is retained as history (not erased).
        a = self.vault.read_node("project/arch-a")
        b = self.vault.read_node("project/arch-b")
        self.assertEqual(a.frontmatter.status, NodeStatus.SUPERSEDED.value)
        self.assertEqual(a.frontmatter.superseded_by, "project/arch-b")
        self.assertEqual(b.frontmatter.status, NodeStatus.ACTIVE.value)
        # The superseded node still exists on disk — correction provenance survives.
        from agent.brain.vault import BrainVault
        self.assertIsNotNone(BrainVault().read_node("project/arch-a"))

    def test_contradiction_weakens_confidence_without_deletion(self):
        from agent.brain.relations import Relation, RelationType, Provenance
        rel = Relation(target="concept/pulse", rel_type=RelationType.MENTIONS.value,
                       provenance=Provenance.INFERRED_ENTITY.value, confidence=0.9)
        before = rel.confidence
        rel.weaken()
        self.assertLess(rel.confidence, before)
        self.assertGreater(rel.confidence, 0.0, "contradiction must weaken, not delete")

    def test_evolved_state_persists_across_restart(self):
        from agent.brain.consolidate import relink_mentions
        self.vault.write_node("concept/pulse", "PULSE is the project.", category="concept")
        self.vault.write_node("concept/recall", "Recall is central to PULSE.", category="concept")
        relink_mentions(self.vault, now=time.time())
        rels_before = self.vault.read_node("concept/recall").typed_relations()
        # Simulate a restart: brand-new vault object reading the same PULSE_HOME.
        from agent.brain.vault import BrainVault
        reopened = BrainVault().read_node("concept/recall").typed_relations()
        self.assertEqual([r.to_dict() for r in rels_before], [r.to_dict() for r in reopened])


if __name__ == "__main__":
    unittest.main()
