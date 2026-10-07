"""Tests for the similarity backends and near-duplicate detection."""

from __future__ import annotations

import subprocess
import sys
import unittest

from agent.brain import similarity as sim


class TestLexicalEmbedder(unittest.TestCase):
    def setUp(self):
        self.emb = sim.LexicalHashEmbedder(dim=128)

    def test_vector_shape_and_normalisation(self):
        vec = self.emb.vector("hello world hello")
        self.assertEqual(len(vec), 128)
        norm = sum(v * v for v in vec) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=6)

    def test_empty_text_gives_zero_vector(self):
        self.assertEqual(sum(abs(v) for v in self.emb.vector("")), 0.0)

    def test_deterministic_within_process(self):
        self.assertEqual(self.emb.vector("stable text"), self.emb.vector("stable text"))

    def test_deterministic_across_processes(self):
        """blake2b, not salted hash(): the same text must vectorise identically anywhere."""
        code = (
            "from agent.brain.similarity import LexicalHashEmbedder;"
            "v=LexicalHashEmbedder(dim=64).vector('cross process check');"
            "print(sum(round(x,9) for x in v))"
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env={"PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[1])},
            check=False,
        )
        self.assertEqual(out.returncode, 0, out.stderr)
        local = sum(round(x, 9) for x in sim.LexicalHashEmbedder(dim=64).vector("cross process check"))
        self.assertAlmostEqual(float(out.stdout.strip()), local, places=6)


class TestSimilarityScores(unittest.TestCase):
    def test_identical_text_is_one(self):
        self.assertAlmostEqual(sim.similarity("the same sentence", "the same sentence"), 1.0, places=6)

    def test_near_identical_ranks_high(self):
        score = sim.similarity(
            "user prefers Hinglish replies in chat",
            "user prefers Hinglish replies in the chat",
        )
        self.assertGreater(score, 0.85)

    def test_unrelated_ranks_low(self):
        score = sim.similarity(
            "user prefers Hinglish replies",
            "kubernetes ingress controller tls termination",
        )
        self.assertLess(score, 0.35)

    def test_similarity_is_symmetric(self):
        a, b = "graph rendering pipeline", "pipeline for rendering graphs"
        self.assertAlmostEqual(sim.similarity(a, b), sim.similarity(b, a), places=9)

    def test_empty_input_is_zero_not_error(self):
        self.assertEqual(sim.similarity("", "x"), 0.0)
        self.assertEqual(sim.similarity("   ", "x"), 0.0)

    def test_cosine_handles_degenerate_vectors(self):
        self.assertEqual(sim.cosine([], []), 0.0)
        self.assertEqual(sim.cosine([0.0, 0.0], [1.0, 1.0]), 0.0)
        self.assertAlmostEqual(sim.cosine([1.0, 0.0], [1.0, 0.0]), 1.0, places=9)


class TestNearDuplicate(unittest.TestCase):
    def test_exact_repeat_is_duplicate(self):
        self.assertTrue(sim.is_near_duplicate("remember this fact", "remember this fact"))

    def test_reworded_short_fact_is_duplicate(self):
        self.assertTrue(
            sim.is_near_duplicate("pulse uses a brain vault", "pulse uses the brain vault")
        )

    def test_distinct_facts_are_not_duplicates(self):
        self.assertFalse(sim.is_near_duplicate("pulse uses a brain vault", "user prefers dark mode"))

    def test_best_match_picks_closest(self):
        corpus = ["user prefers dark mode", "user prefers Hinglish replies", "unrelated thing"]
        result = sim.best_match("user prefers Hinglish", corpus, threshold=0.4)
        self.assertIsNotNone(result)
        self.assertEqual(result[0], "user prefers Hinglish replies")

    def test_best_match_below_threshold_is_none(self):
        self.assertIsNone(sim.best_match("totally different", ["alpha beta"], threshold=0.99))

    def test_best_match_empty_corpus(self):
        self.assertIsNone(sim.best_match("x", []))


class TestBackendSelection(unittest.TestCase):
    def test_get_embedder_never_raises_and_reports_a_name(self):
        emb = sim.get_embedder()
        self.assertTrue(hasattr(emb, "vector"))
        self.assertIn(emb.name, {"lexical-hashed", "semantic"})

    def test_falls_back_to_lexical_when_semantic_unavailable(self):
        if not sim.SemanticEmbedder.available():
            self.assertIsInstance(sim.get_embedder(prefer_semantic=True), sim.LexicalHashEmbedder)

    def test_semantic_availability_probe_is_safe(self):
        self.assertIsInstance(sim.SemanticEmbedder.available(), bool)


class TestTokenize(unittest.TestCase):
    def test_stopwords_dropped_but_not_everything(self):
        self.assertEqual(sim.tokenize("the and of"), ["the", "and", "of"])
        self.assertEqual(sim.tokenize("the brain vault"), ["brain", "vault"])

    def test_punctuation_and_case(self):
        self.assertEqual(sim.tokenize("Hello, World!"), ["hello", "world"])


if __name__ == "__main__":
    unittest.main()
