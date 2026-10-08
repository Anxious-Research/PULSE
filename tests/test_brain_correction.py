"""Tests for contradiction detection (specs/brain.md §3.5).

Two properties matter most, and both are the reason this is a separate module rather than a
line inside encoding:

1. A restatement is NOT a contradiction. If it were, every re-phrasing of a fact would
   supersede its own source and the vault would fill with tombstones.
2. A same-topic statement with matching polarity is NOT a contradiction either — that is
   consolidation's near-duplicate merge (§3.2), not correction.
"""

from __future__ import annotations

import unittest

from agent.brain.correction import (
    contradicts,
    find_contradictions,
    numbers,
    polarity,
    topic_overlap,
    topic_signature,
)


class TestPolarity(unittest.TestCase):
    def test_plain_assertion_is_positive(self):
        self.assertEqual(polarity("PULSE stores every memory in a vault"), 1)

    def test_single_negation_flips(self):
        self.assertEqual(polarity("PULSE does not delete memories"), -1)

    def test_double_negation_is_positive(self):
        """Parity over negation markers: "does not avoid" asserts the thing happens."""
        self.assertEqual(polarity("PULSE does not avoid storing memories"), 1)

    def test_hinglish_negation_counts(self):
        self.assertEqual(polarity("aisa nahi karna hai"), -1)


class TestTopicSignature(unittest.TestCase):
    def test_negations_are_excluded_from_the_topic(self):
        """The disagreement lives in the negation, so sameness must not be diluted by it."""
        self.assertEqual(
            topic_signature("PULSE deletes memories"),
            topic_signature("PULSE does not delete memories"),
        )

    def test_numbers_are_excluded_from_the_topic(self):
        self.assertEqual(
            topic_signature("the recall budget is 600"),
            topic_signature("the recall budget is 800"),
        )

    def test_different_subjects_do_not_share_a_topic(self):
        self.assertEqual(topic_overlap("kubernetes ingress tls", "forgetting curve decay"), 0.0)

    def test_overlap_is_jaccard_not_containment(self):
        """A note that merely MENTIONS a topic must not count as being about it."""
        self.assertLess(topic_overlap("recall budget", "recall budget and prefix budget and token limits"), 0.5)

    def test_empty_input_is_zero_overlap(self):
        self.assertEqual(topic_overlap("", "anything"), 0.0)
        self.assertEqual(topic_overlap("hello", ""), 0.0)


class TestNumbers(unittest.TestCase):
    def test_extracts_integers_and_decimals(self):
        self.assertEqual(numbers("budget 600 and 1.5 multiplier"), {"600", "1.5"})

    def test_no_numbers(self):
        self.assertEqual(numbers("no numbers here"), set())


class TestStemming(unittest.TestCase):
    """Real data writes the same fact inflected differently; without this the signatures miss."""

    def test_third_person_s_matches_bare_verb(self):
        self.assertEqual(topic_signature("the tool deletes memories"), topic_signature("the tool delete memories"))

    def test_plural_matches_singular(self):
        self.assertEqual(topic_signature("nodes"), topic_signature("node"))

    def test_ing_matches_bare_verb(self):
        self.assertEqual(topic_signature("the server logging"), topic_signature("the server log"))

    def test_ies_becomes_y(self):
        self.assertIn("memory", topic_signature("memories"))

    def test_does_not_stem_the_auxiliary_into_a_negative(self):
        """The trap: stripping "s" from "notes" must not produce the negation "not"."""
        self.assertNotIn("not", topic_signature("the notes are here"))

    def test_ss_ending_is_preserved(self):
        self.assertIn("class", topic_signature("class hierarchy"))


class TestContradicts(unittest.TestCase):
    def test_negation_flip_on_the_same_topic_contradicts(self):
        self.assertTrue(
            contradicts(
                "The memory tool does not write the legacy flat store any more",
                "The memory tool writes the legacy flat store",
            )
        )

    def test_restatement_is_not_a_contradiction(self):
        """The exact failure this guard exists for: a re-phrasing must not tombstone its source."""
        self.assertFalse(
            contradicts(
                "The memory tool writes the legacy flat store",
                "The memory tool writes the legacy flat store",
            )
        )

    def test_same_polarity_different_topic_is_not_a_contradiction(self):
        self.assertFalse(
            contradicts("PULSE stores memory in markdown", "PULSE does not use a database")
        )

    def test_changed_number_on_the_same_topic_contradicts(self):
        self.assertTrue(
            contradicts("the recall budget is 800 tokens", "the recall budget is 600 tokens")
        )

    def test_number_change_without_shared_topic_does_not_contradict(self):
        self.assertFalse(
            contradicts("the server has 32 cores", "the recall budget is 600 tokens")
        )

    def test_hinglish_correction_contradicts_a_hinglish_claim(self):
        """Same language: the realistic Hinglish case works. Cross-language is documented as out
        of scope (see the module docstring) rather than faked."""
        self.assertTrue(
            contradicts(
                "memory injection nahi hona chahiye har turn",
                "memory injection har turn hona chahiye",
            )
        )

    def test_empty_inputs_never_contradict(self):
        self.assertFalse(contradicts("", "anything"))
        self.assertFalse(contradicts("something", ""))


class TestFindContradictions(unittest.TestCase):
    def test_returns_ids_of_contradicted_nodes(self):
        candidates = [
            ("concept/a", "the recall budget is 600 tokens"),
            ("concept/b", "the vault lives under pulse home"),
            ("concept/c", "the recall budget is 600"),
        ]
        got = find_contradictions("the recall budget is 800 tokens", candidates)
        self.assertIn("concept/a", got)
        self.assertNotIn("concept/b", got)

    def test_ordered_most_similar_first(self):
        """When one correction refutes several notes, the closest claim is superseded first."""
        candidates = [
            ("concept/far", "the recall budget is 600 tokens per turn approximately speaking"),
            ("concept/near", "the recall budget is 600 tokens"),
        ]
        got = find_contradictions("the recall budget is 800 tokens", candidates)
        self.assertEqual(got[0], "concept/near")

    def test_no_contradictions_returns_empty(self):
        self.assertEqual(find_contradictions("kubernetes ingress", [("a", "forgetting curve")]), [])


if __name__ == "__main__":
    unittest.main()
