"""Unit tests for robust semantic embedding pipeline and vector search in BrainCache.

Tests:
  - Vector serialization & deserialization (float32 binary blobs)
  - Fast cosine similarity over lists and binary blobs
  - LexicalBM25Embedder (saturation + blake2b hashing)
  - SemanticEmbedder local pipeline (fastembed / sentence-transformers)
  - SemanticEmbedder provider fallback (OpenAI / OpenRouter / credentials) with caching
  - Clean lexical fallback on provider error or missing backend (no crash)
  - BrainCache embedding storage (nodes.embedding) and search_vector ranking
"""

from __future__ import annotations

import array
import json
import math
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.brain import similarity as sim
from agent.brain.cache import BrainCache
from agent.brain.vault import BrainVault


class TestVectorBlobSerialization(unittest.TestCase):
    def test_round_trip(self):
        vec = [0.123456, -0.789012, 0.0, 1.0, -1.0, 3.14159]
        blob = sim.serialize_vector(vec)
        self.assertIsInstance(blob, bytes)
        self.assertEqual(len(blob), len(vec) * 4)  # 4 bytes per float32

        unpacked = sim.deserialize_vector(blob)
        self.assertEqual(len(unpacked), len(vec))
        for orig, recovered in zip(vec, unpacked):
            self.assertAlmostEqual(orig, recovered, places=5)

    def test_bytes_passthrough(self):
        raw = b"\x00\x00\x80?\x00\x00\x00@"  # 1.0, 2.0 in float32
        blob = sim.serialize_vector(raw)
        self.assertEqual(blob, raw)

    def test_empty_vector(self):
        self.assertEqual(sim.serialize_vector([]), b"")
        self.assertEqual(sim.deserialize_vector(b""), [])

    def test_tuple_input(self):
        vec = (1.5, -2.5, 0.0)
        blob = sim.serialize_vector(vec)
        recovered = sim.deserialize_vector(blob)
        self.assertEqual(len(recovered), 3)
        self.assertAlmostEqual(recovered[0], 1.5, places=5)
        self.assertAlmostEqual(recovered[1], -2.5, places=5)


class TestFastCosineSimilarity(unittest.TestCase):
    def test_list_vs_list(self):
        v1 = [1.0, 0.0, 0.0]
        v2 = [1.0, 0.0, 0.0]
        self.assertAlmostEqual(sim.cosine(v1, v2), 1.0, places=6)

        v3 = [0.0, 1.0, 0.0]
        self.assertAlmostEqual(sim.cosine(v1, v3), 0.0, places=6)

        v4 = [-1.0, 0.0, 0.0]
        self.assertAlmostEqual(sim.cosine(v1, v4), -1.0, places=6)

    def test_blob_vs_blob(self):
        v1 = [0.5, 0.5, 0.5, 0.5]
        v2 = [0.5, 0.5, 0.5, 0.5]
        b1 = sim.serialize_vector(v1)
        b2 = sim.serialize_vector(v2)
        self.assertAlmostEqual(sim.cosine(b1, b2), 1.0, places=6)

        b3 = sim.serialize_vector([-0.5, -0.5, -0.5, -0.5])
        self.assertAlmostEqual(sim.cosine(b1, b3), -1.0, places=6)

    def test_list_vs_blob_mixed(self):
        v1 = [1.0, 2.0, 3.0]
        v2 = [1.0, 2.0, 3.0]
        b2 = sim.serialize_vector(v2)
        self.assertAlmostEqual(sim.cosine(v1, b2), 1.0, places=6)
        self.assertAlmostEqual(sim.cosine(b2, v1), 1.0, places=6)

    def test_degenerate_and_empty(self):
        self.assertEqual(sim.cosine([], []), 0.0)
        self.assertEqual(sim.cosine(b"", b""), 0.0)
        self.assertEqual(sim.cosine([0.0, 0.0], [1.0, 1.0]), 0.0)
        self.assertEqual(sim.cosine(b"\x00" * 8, [1.0, 1.0]), 0.0)

    def test_large_vector_speed(self):
        dim = 384
        v1 = [0.1 * (i % 10) for i in range(dim)]
        v2 = [0.05 * (i % 10) for i in range(dim)]
        b1 = sim.serialize_vector(v1)
        b2 = sim.serialize_vector(v2)

        t0 = time.perf_counter()
        for _ in range(1000):
            sim.cosine(b1, b2)
        elapsed = time.perf_counter() - t0
        # 1000 comparisons should finish well within 0.5s in Python
        self.assertLess(elapsed, 0.5)


class TestLexicalBM25Embedder(unittest.TestCase):
    def setUp(self):
        self.emb = sim.LexicalBM25Embedder(dim=128)

    def test_vector_shape_and_norm(self):
        vec = self.emb.vector("brain vault memory recall")
        self.assertEqual(len(vec), 128)
        norm = sum(x * x for x in vec) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=6)

    def test_empty_text_gives_zero(self):
        vec = self.emb.vector("")
        self.assertEqual(sum(abs(x) for x in vec), 0.0)

    def test_deterministic(self):
        v1 = self.emb.vector("deterministic projection check")
        v2 = self.emb.vector("deterministic projection check")
        self.assertEqual(v1, v2)

    def test_bm25_term_saturation(self):
        # A text repeating the same word 20 times vs 1 time
        v_once = self.emb.vector("apple")
        v_twenty = self.emb.vector("apple " * 20)
        # Cosine between them should be 1.0 because word direction is identical
        self.assertAlmostEqual(sim.cosine(v_once, v_twenty), 1.0, places=6)


class TestSemanticEmbedderLocalPipeline(unittest.TestCase):
    def test_custom_callable_model(self):
        # Test custom object with .vector()
        mock_obj = MagicMock()
        mock_obj.vector.return_value = [0.5, 0.5, 0.5, 0.5]
        embedder = sim.SemanticEmbedder(model=mock_obj, dim=4)
        self.assertTrue(embedder.semantic)
        self.assertEqual(embedder.kind, "custom")
        vec = embedder.vector("custom input")
        mock_obj.vector.assert_called_once_with("custom input")
        norm = sum(x * x for x in vec) ** 0.5
        self.assertAlmostEqual(norm, 1.0, places=6)

        # Test plain callable (function)
        calls = []

        def custom_fn(text: str):
            calls.append(text)
            return [0.6, 0.8]

        fn_embedder = sim.SemanticEmbedder(model=custom_fn, dim=2)
        vec_fn = fn_embedder.vector("function input")
        self.assertEqual(calls, ["function input"])
        self.assertAlmostEqual(vec_fn[0], 0.6, places=5)
        self.assertAlmostEqual(vec_fn[1], 0.8, places=5)

    def test_fastembed_backend_mocked(self):
        mock_fastembed_mod = MagicMock()
        mock_instance = MagicMock()
        mock_instance.embed.return_value = [[0.1] * 384]
        mock_fastembed_mod.TextEmbedding.return_value = mock_instance

        with patch.dict("sys.modules", {"fastembed": mock_fastembed_mod}):
            embedder = sim.SemanticEmbedder(model_name="BAAI/bge-small-en-v1.5")
            vec = embedder.vector("testing fastembed")
            self.assertEqual(embedder.kind, "fastembed")
            self.assertEqual(len(vec), 384)
            mock_instance.embed.assert_called_once()

    def test_sentence_transformers_backend_mocked(self):
        mock_st_mod = MagicMock()
        mock_instance = MagicMock()
        mock_instance.encode.return_value = [[0.2] * 384]
        mock_st_mod.SentenceTransformer.return_value = mock_instance

        # fastembed unavailable, sentence_transformers available
        with patch.dict("sys.modules", {"fastembed": None, "sentence_transformers": mock_st_mod}):
            embedder = sim.SemanticEmbedder(model_name="all-MiniLM-L6-v2")
            vec = embedder.vector("testing sentence transformers")
            self.assertEqual(embedder.kind, "sentence-transformers")
            self.assertEqual(len(vec), 384)
            mock_instance.encode.assert_called_once()


class TestSemanticEmbedderProviderFallback(unittest.TestCase):
    def test_provider_resolution_from_explicit_args(self):
        embedder = sim.SemanticEmbedder(
            provider="openai",
            api_key="sk-test-explicit",
            base_url="https://api.openai.com/v1",
            model_name="text-embedding-3-small",
        )
        self.assertEqual(embedder.provider, "openai")
        self.assertEqual(embedder.api_key, "sk-test-explicit")
        self.assertEqual(embedder.model_name, "text-embedding-3-small")

    def test_provider_resolution_from_env(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-env-test"}):
            p, m, k, u = sim._resolve_provider_credentials()
            self.assertEqual(p, "openai")
            self.assertEqual(k, "sk-env-test")
            self.assertEqual(m, "text-embedding-3-small")

    def test_openrouter_resolution_from_env(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "sk-or-test"}, clear=True):
            p, m, k, u = sim._resolve_provider_credentials()
            self.assertEqual(p, "openrouter")
            self.assertEqual(k, "sk-or-test")
            self.assertEqual(m, "openai/text-embedding-3-small")

    def test_provider_config_resolution(self):
        mock_cfg = {
            "brain": {
                "embedding_provider": "openrouter",
                "embedding_model": "custom-embed-model",
                "embedding_api_key": "sk-cfg-key",
            }
        }
        with patch("agent.brain.similarity._get_embedding_config", return_value=mock_cfg["brain"]):
            p, m, k, u = sim._resolve_provider_credentials()
            self.assertEqual(p, "openrouter")
            self.assertEqual(m, "custom-embed-model")
            self.assertEqual(k, "sk-cfg-key")

    def test_remote_embedding_http_call_and_caching(self):
        fake_response = {
            "data": [
                {
                    "index": 0,
                    "embedding": [0.6, 0.8],
                }
            ]
        }
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp

        with patch("urllib.request.urlopen", return_value=mock_resp) as mock_urlopen:
            embedder = sim.SemanticEmbedder(
                provider="openai",
                api_key="sk-test-123",
                model_name="text-embedding-3-small",
                dim=2,
            )
            # First call makes HTTP request
            vec1 = embedder.vector("hello remote")
            self.assertEqual(mock_urlopen.call_count, 1)
            self.assertAlmostEqual(vec1[0], 0.6, places=5)
            self.assertAlmostEqual(vec1[1], 0.8, places=5)

            # Second call with same text uses memory cache
            vec2 = embedder.vector("hello remote")
            self.assertEqual(mock_urlopen.call_count, 1)
            self.assertEqual(vec1, vec2)

            # Different text makes second HTTP request
            embedder.vector("another text")
            self.assertEqual(mock_urlopen.call_count, 2)

    def test_provider_error_falls_back_to_lexical(self):
        # Simulate network failure on urlopen
        with patch("urllib.request.urlopen", side_effect=OSError("Network unreachable")):
            embedder = sim.SemanticEmbedder(
                provider="openai",
                api_key="sk-test",
                fallback_to_lexical=True,
            )
            # Must NOT raise; cleanly falls back to lexical vector
            vec = embedder.vector("survives network error")
            self.assertIsInstance(vec, list)
            norm = sum(x * x for x in vec) ** 0.5
            self.assertAlmostEqual(norm, 1.0, places=6)

    def test_provider_error_raises_when_fallback_disabled(self):
        with patch("urllib.request.urlopen", side_effect=OSError("Network down")):
            embedder = sim.SemanticEmbedder(
                provider="openai",
                api_key="sk-test",
                fallback_to_lexical=False,
            )
            with self.assertRaises(RuntimeError):
                embedder.vector("will fail")


class TestSemanticEmbedderLexicalFallback(unittest.TestCase):
    def test_clean_fallback_when_no_backends_available(self):
        # Ensure no env keys or local models
        with patch.dict(os.environ, {}, clear=True):
            with patch("agent.brain.similarity.SemanticEmbedder.local_available", return_value=False):
                with patch("agent.brain.similarity._resolve_provider_credentials", return_value=("", "", "", "")):
                    embedder = sim.SemanticEmbedder()
                    self.assertFalse(embedder.semantic)
                    self.assertEqual(embedder.kind, "lexical-fallback")

                    # Generates normalized vectors cleanly
                    vec = embedder.vector("safe fallback query")
                    self.assertEqual(len(vec), sim.DEFAULT_DIM)
                    norm = sum(x * x for x in vec) ** 0.5
                    self.assertAlmostEqual(norm, 1.0, places=6)

    def test_unavailable_raised_when_fallback_false(self):
        with patch.dict(os.environ, {}, clear=True):
            with patch("agent.brain.similarity.SemanticEmbedder.local_available", return_value=False):
                with patch("agent.brain.similarity._resolve_provider_credentials", return_value=("", "", "", "")):
                    with self.assertRaises(sim.SemanticEmbedder.Unavailable):
                        sim.SemanticEmbedder(fallback_to_lexical=False).vector("probe")


class TestBrainCacheVectorStorageAndSearch(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="braincache_vec_test_"))
        self.vault = BrainVault(self.tmp)
        self.vault.ensure_vault_structure()

        self.vault.write_node(
            "concept/database",
            "Relational databases and SQLite B-tree storage indices.",
            title="Database Architecture",
        )
        self.vault.write_node(
            "concept/gardening",
            "Cultivating organic tomatoes and soil pH management.",
            title="Gardening Basics",
        )
        self.vault.write_node(
            "concept/astronomy",
            "Stellar nucleosynthesis and gravitational collapse of stars.",
            title="Astrophysics",
        )

        # Mock embedder that creates distinct deterministic vectors
        self.mock_embedder = MagicMock()
        self.mock_embedder.semantic = True
        # 3 distinct orthogonal-ish vectors
        self.db_vec = [1.0, 0.0, 0.0, 0.0]
        self.garden_vec = [0.0, 1.0, 0.0, 0.0]
        self.astro_vec = [0.0, 0.0, 1.0, 0.0]

        def fake_vector(text: str):
            t = str(text).lower()
            if "database" in t or "sqlite" in t:
                return self.db_vec
            if "garden" in t or "tomato" in t:
                return self.garden_vec
            return self.astro_vec

        self.mock_embedder.vector.side_effect = fake_vector

        self.cache = BrainCache(
            self.vault,
            embeddings_enabled=True,
            embedder=self.mock_embedder,
        )

    def tearDown(self):
        self.cache.close()

    def test_rebuild_stores_embedding_blobs(self):
        indexed = self.cache.rebuild()
        self.assertEqual(indexed, 3)

        embedded = self.cache.embedded_nodes()
        self.assertEqual(len(embedded), 3)

        node_ids = {nid for nid, _ in embedded}
        self.assertIn("concept/database", node_ids)
        self.assertIn("concept/gardening", node_ids)
        self.assertIn("concept/astronomy", node_ids)

        for _, blob in embedded:
            self.assertIsInstance(blob, bytes)
            self.assertEqual(len(blob), 4 * 4)  # 4 floats * 4 bytes = 16 bytes

    def test_store_embedding_explicit(self):
        self.cache.rebuild()
        new_vec = [0.7071, 0.7071, 0.0, 0.0]
        self.cache.store_embedding("concept/database", new_vec)

        embedded = dict(self.cache.embedded_nodes())
        recovered = sim.deserialize_vector(embedded["concept/database"])
        self.assertAlmostEqual(recovered[0], 0.7071, places=3)
        self.assertAlmostEqual(recovered[1], 0.7071, places=3)

    def test_search_vector_ranking(self):
        self.cache.rebuild()

        # Query vector identical to database vector
        query = [1.0, 0.0, 0.0, 0.0]
        hits = self.cache.search_vector(query, top_k=3)
        self.assertEqual(len(hits), 3)

        # Top hit must be concept/database with score 1.0
        top = hits[0]
        self.assertEqual(top["id"], "concept/database")
        self.assertEqual(top["title"], "Database Architecture")
        self.assertAlmostEqual(top["score"], 1.0, places=5)
        self.assertAlmostEqual(top["similarity"], 1.0, places=5)
        self.assertAlmostEqual(top["distance"], 0.0, places=5)

        # Other orthogonal nodes should have score 0.0 and distance 1.0
        self.assertAlmostEqual(hits[1]["score"], 0.0, places=5)
        self.assertAlmostEqual(hits[1]["distance"], 1.0, places=5)

    def test_search_vector_with_blob_input(self):
        self.cache.rebuild()
        query_blob = sim.serialize_vector([0.0, 1.0, 0.0, 0.0])
        hits = self.cache.search_vector(query_blob, top_k=1)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "concept/gardening")
        self.assertAlmostEqual(hits[0]["score"], 1.0, places=5)

    def test_search_vector_threshold(self):
        self.cache.rebuild()
        query = [1.0, 0.0, 0.0, 0.0]
        # Only the database node has score 1.0; others have score 0.0
        hits = self.cache.search_vector(query, top_k=10, threshold=0.5)
        self.assertEqual(len(hits), 1)
        self.assertEqual(hits[0]["id"], "concept/database")

    def test_search_similar_alias(self):
        self.cache.rebuild()
        hits1 = self.cache.search_vector([1.0, 0.0, 0.0, 0.0], top_k=2)
        hits2 = self.cache.search_similar([1.0, 0.0, 0.0, 0.0], limit=2)
        self.assertEqual([h["id"] for h in hits1], [h["id"] for h in hits2])

    def test_search_vector_empty_cases(self):
        self.cache.rebuild()
        self.assertEqual(self.cache.search_vector([], top_k=5), [])
        self.assertEqual(self.cache.search_vector(b"", top_k=5), [])
        self.assertEqual(self.cache.search_vector([1.0, 0.0], top_k=0), [])


if __name__ == "__main__":
    unittest.main()
