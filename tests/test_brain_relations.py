"""Tests for the typed edge model (agent/brain/relations.py)."""

import unittest

from agent.brain.relations import (
    ASSERTED_CONFIDENCE,
    CONFLICT_PENALTY,
    INFERRED_BASE,
    INFERRED_CEILING,
    Provenance,
    Relation,
    RelationType,
    derive_from_frontmatter,
    dump_relations,
    evidence_hash,
    merge_into,
    parse_relations,
    validate_targets,
)


class TestRelationRoundTrip(unittest.TestCase):
    def test_token_round_trip_preserves_every_field(self):
        rel = Relation(
            target="concept/forgetting-curve",
            rel_type=RelationType.MENTIONS.value,
            provenance=Provenance.INFERRED_ENTITY.value,
            confidence=0.5,
            evidence=["a1b2c3", "d4e5f6"],
            created_at=1_700_000_000,
            updated_at=1_700_000_500,
        )
        back = Relation.from_token(rel.to_token())
        self.assertEqual(back.to_dict(), rel.to_dict())

    def test_bare_target_is_a_legacy_inferred_relation(self):
        rel = Relation.from_token("concept/pulse")
        self.assertEqual(rel.target, "concept/pulse")
        self.assertEqual(rel.rel_type, RelationType.RELATED_TO.value)
        self.assertEqual(rel.provenance, Provenance.INFERRED_ENTITY.value)

    def test_empty_token_is_none(self):
        self.assertIsNone(Relation.from_token(""))
        self.assertIsNone(Relation.from_token("   "))

    def test_unknown_type_and_provenance_coerce_to_safe_defaults(self):
        rel = Relation.from_token("concept/x|bogus|made-up|0.7")
        self.assertEqual(rel.rel_type, RelationType.RELATED_TO.value)
        self.assertEqual(rel.provenance, Provenance.INFERRED_ENTITY.value)

    def test_delimiters_in_target_do_not_corrupt_token(self):
        rel = Relation(target="concept/a|b,c").normalized()
        tok = rel.to_token()
        back = Relation.from_token(tok)
        # pipes/commas are sanitized out of fields, so the token stays parseable
        self.assertEqual(tok.count("|"), 6)
        self.assertIsNotNone(back)


class TestConfidenceBounds(unittest.TestCase):
    def test_confidence_is_clamped_into_unit_interval(self):
        self.assertEqual(Relation(target="x", confidence=5.0).normalized().confidence, 1.0)
        self.assertEqual(Relation(target="x", confidence=-3.0).normalized().confidence, 0.0)

    def test_invalid_confidence_falls_back_to_zero_floor(self):
        rel = Relation.from_token("concept/x|mentions|inferred_entity|not-a-number")
        self.assertEqual(rel.confidence, 0.0)

    def test_asserted_ceiling_is_one_inferred_ceiling_is_below_one(self):
        asserted = Relation(target="x", provenance=Provenance.ASSERTED.value)
        inferred = Relation(target="y", provenance=Provenance.INFERRED_ENTITY.value)
        self.assertEqual(asserted.ceiling(), ASSERTED_CONFIDENCE)
        self.assertLess(inferred.ceiling(), 1.0)
        self.assertEqual(inferred.ceiling(), INFERRED_CEILING)


class TestConfidenceEvolution(unittest.TestCase):
    def test_distinct_evidence_raises_confidence_with_diminishing_returns(self):
        rel = Relation(target="x", provenance=Provenance.INFERRED_ENTITY.value, confidence=INFERRED_BASE)
        c0 = rel.confidence
        self.assertTrue(rel.add_evidence("alpha sentence"))
        c1 = rel.confidence
        self.assertTrue(rel.add_evidence("beta sentence"))
        c2 = rel.confidence
        self.assertGreater(c1, c0)
        self.assertGreater(c2, c1)
        # diminishing returns: second gain is smaller than the first
        self.assertLess(c2 - c1, c1 - c0)

    def test_repeated_identical_evidence_never_raises_confidence(self):
        rel = Relation(target="x", provenance=Provenance.INFERRED_ENTITY.value, confidence=INFERRED_BASE)
        self.assertTrue(rel.add_evidence("same sentence"))
        c1 = rel.confidence
        # the SAME evidence again is a no-op — regenerating an inference does not inflate trust
        self.assertFalse(rel.add_evidence("same sentence"))
        self.assertFalse(rel.add_evidence("SAME SENTENCE"))  # hash is case-insensitive
        self.assertEqual(rel.confidence, c1)

    def test_inferred_confidence_never_reaches_certainty(self):
        rel = Relation(target="x", provenance=Provenance.INFERRED_ENTITY.value, confidence=INFERRED_BASE)
        for i in range(100):
            rel.add_evidence(f"evidence number {i}")
        self.assertLessEqual(rel.confidence, INFERRED_CEILING)
        self.assertLess(rel.confidence, 1.0)

    def test_conflict_weakens_without_deleting(self):
        rel = Relation(target="x", provenance=Provenance.INFERRED_ENTITY.value, confidence=0.9)
        rel.weaken()
        self.assertAlmostEqual(rel.confidence, round(0.9 - CONFLICT_PENALTY, 4))
        self.assertGreaterEqual(rel.confidence, 0.0)


class TestMerge(unittest.TestCase):
    def test_new_target_type_is_appended(self):
        rels = []
        merge_into(rels, Relation(target="a", rel_type=RelationType.MENTIONS.value), evidence_text="e1")
        merge_into(rels, Relation(target="b", rel_type=RelationType.MENTIONS.value), evidence_text="e2")
        self.assertEqual({r.target for r in rels}, {"a", "b"})

    def test_duplicate_corroborates_with_new_evidence_only(self):
        rels = [Relation(target="a", rel_type=RelationType.MENTIONS.value,
                         provenance=Provenance.INFERRED_ENTITY.value, confidence=INFERRED_BASE,
                         evidence=[evidence_hash("e1")])]
        merge_into(rels, Relation(target="a", rel_type=RelationType.MENTIONS.value), evidence_text="e2")
        self.assertEqual(len(rels), 1)
        self.assertEqual(len(rels[0].evidence), 2)
        self.assertGreater(rels[0].confidence, INFERRED_BASE)

    def test_duplicate_same_evidence_is_idempotent(self):
        rels = [Relation(target="a", rel_type=RelationType.MENTIONS.value,
                         provenance=Provenance.INFERRED_ENTITY.value, confidence=INFERRED_BASE,
                         evidence=[evidence_hash("e1")])]
        c0 = rels[0].confidence
        merge_into(rels, Relation(target="a", rel_type=RelationType.MENTIONS.value), evidence_text="e1")
        self.assertEqual(len(rels[0].evidence), 1)
        self.assertEqual(rels[0].confidence, c0)

    def test_asserted_upgrades_inferred(self):
        rels = [Relation(target="a", rel_type=RelationType.WIKILINK.value,
                         provenance=Provenance.INFERRED_ENTITY.value, confidence=INFERRED_BASE)]
        merge_into(rels, Relation(target="a", rel_type=RelationType.WIKILINK.value,
                                  provenance=Provenance.ASSERTED.value, confidence=ASSERTED_CONFIDENCE))
        self.assertEqual(rels[0].provenance, Provenance.ASSERTED.value)
        self.assertEqual(rels[0].confidence, ASSERTED_CONFIDENCE)


class TestLegacyCompat(unittest.TestCase):
    def test_derive_types_from_legacy_frontmatter(self):
        rels = derive_from_frontmatter(
            related=["concept/a"],
            supersedes=["concept/old"],
            wikilink_targets=["concept/b"],
            created_at=123,
        )
        by_target = {r.target: r for r in rels}
        self.assertEqual(by_target["concept/b"].rel_type, RelationType.WIKILINK.value)
        self.assertEqual(by_target["concept/b"].provenance, Provenance.ASSERTED.value)
        self.assertEqual(by_target["concept/b"].confidence, ASSERTED_CONFIDENCE)
        self.assertEqual(by_target["concept/old"].rel_type, RelationType.SUPERSEDES.value)
        self.assertEqual(by_target["concept/a"].provenance, Provenance.INFERRED_ENTITY.value)
        self.assertEqual(by_target["concept/a"].confidence, INFERRED_BASE)

    def test_parse_dump_round_trip_of_a_collection(self):
        rels = [
            Relation(target="a", rel_type=RelationType.WIKILINK.value, provenance=Provenance.ASSERTED.value, confidence=1.0),
            Relation(target="b", rel_type=RelationType.MENTIONS.value, confidence=0.5, evidence=["h1"]),
        ]
        back = parse_relations(dump_relations(rels))
        self.assertEqual([r.to_dict() for r in back], [r.to_dict() for r in rels])


class TestTargetValidation(unittest.TestCase):
    def test_edges_to_missing_nodes_are_dropped(self):
        rels = [Relation(target="concept/exists"), Relation(target="concept/ghost")]
        kept = validate_targets(rels, {"concept/exists"})
        self.assertEqual([r.target for r in kept], ["concept/exists"])


if __name__ == "__main__":
    unittest.main()
