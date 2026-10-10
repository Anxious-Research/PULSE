"""Hybrid retrieval: BM25, semantic gating, RRF fusion, and a before/after recall comparison.

Two things are proved here:

1. The channels and the fusion are correct in isolation (BM25 ranks a focused doc over a
   term-stuffed long one; the semantic channel refuses to run without a real embedder; RRF fuses
   and deduplicates deterministically).
2. On a deterministic dataset, hybrid reranking is **measured** against the previous
   (associative-only) recall with the same queries and relevance labels — precision@1, MRR, and
   per-call latency are reported, and the test asserts hybrid is no worse on ranking quality. We
   do not claim improvement without this comparison (the mandate's §3 bar).
"""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent.brain.hybrid import bm25_rank, semantic_rank, semantic_available, rrf_fuse
from agent.brain.recall import recall
from agent.brain.similarity import LexicalHashEmbedder
from agent.brain.vault import BrainVault


class _Doc:
    def __init__(self, nid, title, content):
        self.id = nid
        self.title = title
        self.content = content


class BM25Test(unittest.TestCase):
    def test_focused_doc_outranks_term_stuffed_long_doc(self):
        focused = _Doc("n/focused", "Redis cache", "The Redis cache stores session tokens.")
        long = _Doc(
            "n/long",
            "Infra overview",
            ("The system has many parts. " * 40) + "It also mentions redis once in passing.",
        )
        ranked = bm25_rank([("n/focused", focused), ("n/long", long)], "redis cache")
        self.assertEqual(ranked[0][0], "n/focused", "length normalisation should favour the focused doc")

    def test_no_matching_terms_returns_empty(self):
        d = _Doc("n/a", "Alpha", "nothing relevant here")
        self.assertEqual(bm25_rank([("n/a", d)], "quantum chromodynamics"), [])

    def test_empty_corpus_or_query(self):
        self.assertEqual(bm25_rank([], "x"), [])
        d = _Doc("n/a", "Alpha", "content")
        self.assertEqual(bm25_rank([("n/a", d)], ""), [])

    def test_deterministic(self):
        docs = [("n/%d" % i, _Doc("n/%d" % i, "doc %d" % i, "shared term here")) for i in range(5)]
        self.assertEqual(bm25_rank(docs, "shared term"), bm25_rank(docs, "shared term"))


class SemanticGateTest(unittest.TestCase):
    def test_lexical_hash_embedder_is_not_semantic(self):
        self.assertFalse(semantic_available(LexicalHashEmbedder()))
        self.assertFalse(semantic_available(None))

    def test_semantic_rank_refuses_without_real_model(self):
        # The lexical-hash placeholder must never be treated as a semantic channel.
        d = _Doc("n/a", "Alpha", "content")
        self.assertEqual(semantic_rank([("n/a", d)], "content", embedder=LexicalHashEmbedder()), [])

    def test_semantic_rank_runs_with_a_fake_real_embedder(self):
        class FakeReal:
            semantic = True

            def vector(self, text):
                # crude but real-shaped: dimension per vowel count etc.
                t = text.lower()
                return [t.count("a"), t.count("e"), t.count("i"), float(len(t))]

        d1 = _Doc("n/1", "aaa", "aaaa")
        d2 = _Doc("n/2", "eee", "eeee")
        out = semantic_rank([("n/1", d1), ("n/2", d2)], "aaaa", embedder=FakeReal())
        self.assertTrue(out)
        self.assertEqual(out[0][0], "n/1")


class RRFTest(unittest.TestCase):
    def test_fuses_and_dedupes(self):
        a = [("x", 9.0), ("y", 3.0)]
        b = [("y", 0.9), ("z", 0.1)]
        fused = rrf_fuse([a, b])
        ids = [nid for nid, _ in fused]
        self.assertEqual(set(ids), {"x", "y", "z"})          # deduplicated union
        self.assertEqual(ids[0], "y")                         # ranked by two channels → top

    def test_empty_lists_ignored(self):
        self.assertEqual(rrf_fuse([[], []]), [])
        self.assertEqual([nid for nid, _ in rrf_fuse([[("a", 1.0)], []])], ["a"])

    def test_deterministic_tie_break(self):
        # Two ids tie on fused score → break on id, stably.
        fused = rrf_fuse([[("b", 1.0)], [("a", 1.0)]])
        self.assertEqual([nid for nid, _ in fused], ["a", "b"])


class HybridVsBaselineComparison(unittest.TestCase):
    """Before/after: associative-only recall vs hybrid-reranked recall on a labelled dataset.

    The dataset is built to isolate exactly what hybrid adds: each query has a short focused gold
    note AND a long decoy note **with the same title words** (so the associative cue-coverage term
    saturates to the same score for both — a genuine tie the associative pipeline cannot break).
    BM25's document-length normalisation is what distinguishes them, so this measures the value of
    the lexical channel directly. The test reports precision@1, MRR and latency, and asserts hybrid
    is strictly better here while never regressing — we do not claim improvement without the numbers.
    """

    DATASET = [
        ("redis session cache ttl", "concept/redis-cache"),
        ("postgres connection pool size", "concept/pg-pool"),
        ("jwt token expiry window", "concept/jwt-expiry"),
        ("kafka consumer lag partition", "concept/kafka-lag"),
        ("grpc deadline propagation calls", "concept/grpc-deadline"),
    ]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-hybrid-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()
        T0 = 1_800_000_000
        focused = {
            "concept/redis-cache": ("Redis session cache ttl", "Redis session cache ttl is five minutes."),
            "concept/pg-pool": ("Postgres connection pool size", "Postgres connection pool size is twenty."),
            "concept/jwt-expiry": ("Jwt token expiry window", "Jwt token expiry window is fifteen minutes."),
            "concept/kafka-lag": ("Kafka consumer lag partition", "Kafka consumer lag partition is alerted on."),
            "concept/grpc-deadline": ("Grpc deadline propagation calls", "Grpc deadline propagation calls cascade."),
        }
        for nid, (title, content) in focused.items():
            self.vault.write_node(nid, content, title=title, tags=["infra"], salience=0.6, now=T0)
        # One long decoy per query: SAME title words as the gold (so associative cue coverage ties),
        # but a long body that only repeats the terms once among unrelated prose. A length-blind
        # ranker cannot separate it from the focused gold; BM25 ranks it below.
        for (title, _), gold in zip(focused.values(), [k for k in focused]):
            decoy_id = gold.replace("concept/", "concept/decoy-")
            body = f"{title}. " + ("Unrelated architecture overview prose. " * 40)
            self.vault.write_node(decoy_id, body, title=title, tags=["infra"], salience=0.6, now=T0)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _measure(self, hybrid: bool):
        hits_at_1 = 0
        rr_sum = 0.0
        t0 = time.perf_counter()
        for query, gold in self.DATASET:
            res = recall(query, vault=self.vault, limit=5, min_score=0.0, hybrid=hybrid)
            ids = [h.node_id for h in res.hits]
            if ids and ids[0] == gold:
                hits_at_1 += 1
            rank = next((i for i, nid in enumerate(ids) if nid == gold), None)
            if rank is not None:
                rr_sum += 1.0 / (rank + 1)
        elapsed = time.perf_counter() - t0
        n = len(self.DATASET)
        return {
            "precision_at_1": hits_at_1 / n,
            "mrr": rr_sum / n,
            "latency_ms_per_query": 1000.0 * elapsed / n,
        }

    def test_hybrid_improves_ranking_on_ties_and_report_numbers(self):
        baseline = self._measure(hybrid=False)
        hybrid = self._measure(hybrid=True)
        print("\n[hybrid-vs-baseline] associative-only:", baseline)
        print("[hybrid-vs-baseline] hybrid (BM25 tie-break):", hybrid)
        # Measured improvement: associative ties the gold with its decoy; hybrid breaks the tie.
        self.assertGreater(hybrid["precision_at_1"], baseline["precision_at_1"],
                           "hybrid should improve precision@1 on associative ties")
        self.assertGreaterEqual(round(hybrid["mrr"], 6), round(baseline["mrr"], 6),
                                "hybrid regressed MRR")
        # Latency stays in the same order of magnitude (reranking is O(admitted), not O(vault²)).
        self.assertLess(hybrid["latency_ms_per_query"], 200.0, "hybrid latency unexpectedly high")

    def test_confident_associative_order_is_not_regressed(self):
        # With a focused gold and NO tying decoy, associative is already correct; hybrid must not
        # move it (no regression on the easy case).
        tmp = Path(tempfile.mkdtemp(prefix="pulse-hybrid-easy-"))
        try:
            vault = BrainVault(vault_dir=tmp)
            vault.ensure_vault_structure()
            T0 = 1_800_000_000
            vault.write_node("concept/answer", "The deploy command is make release.",
                             title="Deploy command", tags=["ops"], salience=0.6, now=T0)
            vault.write_node("concept/other", "The database runs on port 5432.",
                             title="Database port", tags=["ops"], salience=0.6, now=T0)
            base = [h.node_id for h in recall("deploy command", vault=vault, limit=5,
                                              min_score=0.0, hybrid=False).hits]
            hyb = [h.node_id for h in recall("deploy command", vault=vault, limit=5,
                                             min_score=0.0, hybrid=True).hits]
            self.assertEqual(base[0], "concept/answer")
            self.assertEqual(hyb[0], "concept/answer", "hybrid regressed a confident associative hit")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
