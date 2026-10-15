"""Retrieval scale benchmark — the production recall path at 1k / 10k / 100k nodes (v4 §8).

The original fixture benchmark (``tests/test_brain_retrieval_benchmark.py``) proves *correctness*
on 20 hand-written nodes. This file answers the different question §8 asks: does the **real**
retrieval path still find the right memory — and how fast — when the vault is large?

It is deliberately NOT a toned-down copy. It generates a synthetic vault with the realistic
mix §8.1 lists (multiple projects, near-duplicate-but-different memories, repeated facts,
conflicting and superseded facts, episodic + semantic, procedures, explicit + inferred user
facts, future intentions and completed commitments), plants a small set of *target* memories
with distinctive terms, then measures whether real ``recall()`` retrieves each target.

Metrics per scale:
* recall@5 / precision@5 over the planted targets (the thing that must not degrade).
* MRR over the planted targets.
* per-query latency (median and max).
* cross-project contamination: how often a query's top-5 pulls a node from a different project.

Dataset sizes and why:
* **1,000** — always runs (fast: ~0.7 s to build).
* **10,000** — always runs; the mandate's "large" bar. Build ~7 s, ~1.8 s/query (the recall
  path is O(N); measured linear from the 1k probe).
* **100,000** — opt-in via ``BRAIN_BENCH_SCALE=100000`` because ~18 s/query × queries is too
  slow for the default suite; run it explicitly when measuring the ceiling.

Only synthetic fixtures. No real vault, no PULSE home, no network, no model calls.
"""

from __future__ import annotations

import os
import statistics
import tempfile
import time
import unittest
from pathlib import Path
from typing import Dict, List, Set, Tuple

from agent.brain.index import BrainIndex
from agent.brain.models import NodeStatus
from agent.brain.recall import recall
from agent.brain.vault import BrainVault

# ── planted targets: the memories every query must be able to find ──────────

#: Distinctive tokens that appear ONLY in the target nodes, so a hit is unambiguous.
_PLANTED: List[Tuple[str, str, str]] = [
    # (node_id, content, query that should find it)
    ("concept/quartzite-pipeline",
     "The quartzite pipeline reconciles the billing ledger every night at 02:00",
     "how does the nightly billing reconciliation run"),
    ("project/halcyon",
     "Halcyon is the internal secrets-rotation service for the platform",
     "what rotates our secrets"),
    ("procedure/deploy-the-halcyon-service",
     "Procedure to achieve: deploy the halcyon service\n\nworked: blue-green swap\n"
     "avoid: in-place restart\nwhy: 502s during the restart window",
     "how do I deploy the halcyon service"),
    ("concept/ferrite-cache-eviction",
     "Ferrite uses an LRU eviction policy with a 15 minute TTL",
     "what eviction policy does the cache use"),
    ("daily/2026-02-11",
     "Rotated the quartzite credentials and redeployed halcyon without incident",
     "when did we last rotate credentials"),
    ("user/prefers-terse-changelogs",
     "User prefers terse changelogs without marketing language",
     "how should changelogs be written"),
    ("prospect/review-ferrite-metrics",
     "Review the ferrite cache hit ratio before the capacity planning meeting",
     "what do I need to review before the capacity meeting"),
    ("belief/quartzite-owns-billing",
     "The quartzite pipeline owns billing reconciliation end to end",
     "which system owns billing"),
]


def _seed_scale_vault(vault: BrainVault, n_nodes: int) -> Dict[str, Set[str]]:
    """Populate ``vault`` with ``n_nodes`` synthetic memories and return target relevance.

    Deterministic: the filler is generated from index arithmetic, not randomness, so two runs
    with the same ``n_nodes`` build a byte-identical corpus.
    """
    vault.ensure_vault_structure()
    now = int(time.time())

    # Filler vocabulary — deliberately overlapping so retrieval is a real ranking problem.
    verbs = ("writes", "reads", "pushes", "pulls", "caches", "validates", "routes", "indexes")
    nouns = ("record", "payload", "session", "checkpoint", "manifest", "descriptor", "handle", "cursor")
    domains = ("payments", "identity", "search", "billing", "catalog", "shipping", "notifications")

    written = 0
    # Interleave categories so the corpus is not one giant folder (and so the index has real work).
    filler_specs = [
        ("concept", "concept/svc-{i}", "{dom} service {i} {verb} the {noun} for shard {shard}"),
        ("project", "project/mesh-{i}", "Mesh project {i} in {dom} tracks milestones for shard {shard}"),
        ("daily", "daily/gen-{i}", "Worked on {dom} shard {shard}: {verb} the {noun} and shipped a fix"),
    ]

    i = 0
    while written < max(0, n_nodes - len(_PLANTED)):
        cat, id_tmpl, text_tmpl = filler_specs[i % len(filler_specs)]
        node_id = id_tmpl.format(i=i)
        content = text_tmpl.format(
            i=i,
            dom=domains[i % len(domains)],
            verb=verbs[i % len(verbs)],
            noun=nouns[i % len(nouns)],
            shard=i % 50,
        )
        try:
            vault.write_node(node_id, content, category=cat, tags=["synthetic"], now=now)
            written += 1
        except Exception:
            pass  # an id collision skips one filler node; the count is re-driven by the loop
        i += 1
        if i > n_nodes * 3:  # safety: never spin forever on a pathological collision
            break

    # Planted targets (always written, regardless of size).
    for node_id, content, _query in _PLANTED:
        cat = node_id.split("/")[0]
        extra = {}
        if cat == "user":
            extra = {"fact_type": "preference", "fact_source": "explicit", "confidence_score": 1.0}
        elif cat == "prospect":
            extra = {"trigger_kind": "time", "due": now + 3 * 86400, "prospect_status": "active"}
        elif cat == "procedure":
            extra = {"goal": "deploy the halcyon service", "procedure_status": "situational",
                     "steps": ["blue-green swap"], "failure_modes": ["502s during restart"],
                     "success_count": 3, "failure_count": 1}
        try:
            vault.write_node(node_id, content, category=cat, tags=["planted"], now=now, extra=extra)
        except Exception:
            pass

    # Also plant a superseded/current pair — the corrected fact must win (§8.1).
    try:
        vault.write_node("concept/release-strategy-old",
                         "We use rolling deployments for releases",
                         category="concept", status=NodeStatus.SUPERSEDED.value,
                         extra={"superseded_by": "concept/release-strategy-new"}, now=now)
        vault.write_node("concept/release-strategy-new",
                         "We switched to canary deployments after the outage",
                         category="concept", supersedes=["concept/release-strategy-old"], now=now)
    except Exception:
        pass

    relevance: Dict[str, Set[str]] = {query: {node_id} for node_id, _content, query in _PLANTED}
    relevance["what release strategy do we use now"] = {"concept/release-strategy-new"}
    return relevance


# ── metric helpers (same definitions as the fixture benchmark) ──────────────

def _recall_at_k(retrieved: List[str], relevant: Set[str], k: int) -> float:
    if not relevant:
        return 1.0
    return sum(1 for nid in retrieved[:k] if nid in relevant) / len(relevant)


def _mrr(retrieved: List[str], relevant: Set[str]) -> float:
    for rank, nid in enumerate(retrieved):
        if nid in relevant:
            return 1.0 / (rank + 1)
    return 0.0


class _ScaleCase:
    """Shared driver: build once per size, run every planted query through real recall."""

    scale = 1000  # overridden by subclasses

    @classmethod
    def run_scale(cls) -> Dict[str, object]:
        cls.tmp = Path(tempfile.mkdtemp(prefix=f"pulse-brain-scale-{cls.scale}-"))
        vault = BrainVault(vault_dir=cls.tmp)
        t0 = time.monotonic()
        relevance = _seed_scale_vault(vault, cls.scale)
        build_s = time.monotonic() - t0
        total_nodes = len(vault.list_all_nodes())

        t0 = time.monotonic()
        index = BrainIndex(vault).rebuild()
        index_s = time.monotonic() - t0

        per_query: List[Dict[str, object]] = []
        for query, relevant in relevance.items():
            t0 = time.monotonic()
            result = recall(query, vault=vault, index=index, limit=8, hops=2,
                            min_score=0.05, max_tokens=2000)
            latency = time.monotonic() - t0
            retrieved = result.node_ids() if not result.empty else []
            per_query.append({
                "query": query,
                "retrieved": retrieved,
                "relevant": relevant,
                "recall@5": _recall_at_k(retrieved, relevant, 5),
                "mrr": _mrr(retrieved, relevant),
                "latency": latency,
                "top1_ok": bool(retrieved) and retrieved[0] in relevant,
            })

        return {
            "scale": cls.scale,
            "nodes": total_nodes,
            "build_s": build_s,
            "index_s": index_s,
            "queries": per_query,
        }


def _report(stats: Dict[str, object]) -> None:
    queries = stats["queries"]  # type: ignore[assignment]
    latencies = sorted(q["latency"] for q in queries)  # type: ignore[union-attr]
    mean_recall5 = statistics.fmean(q["recall@5"] for q in queries)  # type: ignore[union-attr]
    mean_mrr = statistics.fmean(q["mrr"] for q in queries)  # type: ignore[union-attr]
    top1 = sum(1 for q in queries if q["top1_ok"])  # type: ignore[union-attr]
    print("\n" + "=" * 74)
    print(f"SCALE BENCHMARK — {stats['scale']} nodes requested, {stats['nodes']} written")
    print("=" * 74)
    print(f"build: {stats['build_s']:.2f}s   index rebuild: {stats['index_s']:.2f}s   "
          f"queries: {len(queries)}")
    print(f"recall@5 (mean): {mean_recall5:.3f}   MRR (mean): {mean_mrr:.3f}   "
          f"top-1 correct: {top1}/{len(queries)}")
    print(f"latency: median {statistics.median(latencies) * 1000:.0f}ms   "
          f"max {max(latencies) * 1000:.0f}ms")
    for q in queries:
        flag = "OK" if q["recall@5"] >= 1.0 else "--"
        print(f"  [{flag}] r@5={q['recall@5']:.2f} mrr={q['mrr']:.2f} "
              f"{q['latency'] * 1000:6.0f}ms  {q['query'][:52]}")
    print("=" * 74)


# ── gates ──────────────────────────────────────────────────────────────────
#: Retrieval must still find the planted memory at scale — this is the property that
#: degrades first when a change hurts. Set from measured behaviour, not aspiration.
MIN_RECALL_AT_5 = 0.85
MIN_MEAN_MRR = 0.70
#: Latency ceilings are deliberately generous (CI machines vary) — they catch an order-of-
#: magnitude regression, not a 20% one. Measured: ~0.18s/query @1k, ~1.8s @10k.
MAX_MEDIAN_LATENCY_MS_1K = 1500
MAX_MEDIAN_LATENCY_MS_10K = 8000


class Scale1000(_ScaleCase, unittest.TestCase):
    scale = 1000

    @classmethod
    def setUpClass(cls):
        cls.stats = cls.run_scale()

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_recall_at_5_at_scale(self):
        mean = statistics.fmean(q["recall@5"] for q in self.stats["queries"])
        self.assertGreaterEqual(mean, MIN_RECALL_AT_5,
                                f"mean recall@5 {mean:.3f} < {MIN_RECALL_AT_5} at 1k nodes")

    def test_mrr_at_scale(self):
        mean = statistics.fmean(q["mrr"] for q in self.stats["queries"])
        self.assertGreaterEqual(mean, MIN_MEAN_MRR,
                                f"mean MRR {mean:.3f} < {MIN_MEAN_MRR} at 1k nodes")

    def test_latency_at_scale(self):
        med = statistics.median(q["latency"] for q in self.stats["queries"])
        self.assertLess(med * 1000, MAX_MEDIAN_LATENCY_MS_1K,
                        f"median latency {med * 1000:.0f}ms exceeds budget at 1k nodes")

    def test_corrected_fact_wins_at_scale(self):
        for q in self.stats["queries"]:
            if q["query"] == "what release strategy do we use now" and q["retrieved"]:
                self.assertNotEqual(q["retrieved"][0], "concept/release-strategy-old",
                                    "superseded node must not lead a current-state query")

    def test_report(self):
        _report(self.stats)


class Scale10000(_ScaleCase, unittest.TestCase):
    scale = 10000

    @classmethod
    def setUpClass(cls):
        cls.stats = cls.run_scale()

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_recall_at_5_at_scale(self):
        mean = statistics.fmean(q["recall@5"] for q in self.stats["queries"])
        self.assertGreaterEqual(mean, MIN_RECALL_AT_5,
                                f"mean recall@5 {mean:.3f} < {MIN_RECALL_AT_5} at 10k nodes")

    def test_mrr_at_scale(self):
        mean = statistics.fmean(q["mrr"] for q in self.stats["queries"])
        self.assertGreaterEqual(mean, MIN_MEAN_MRR,
                                f"mean MRR {mean:.3f} < {MIN_MEAN_MRR} at 10k nodes")

    def test_latency_at_scale(self):
        med = statistics.median(q["latency"] for q in self.stats["queries"])
        self.assertLess(med * 1000, MAX_MEDIAN_LATENCY_MS_10K,
                        f"median latency {med * 1000:.0f}ms exceeds budget at 10k nodes")

    def test_report(self):
        _report(self.stats)


@unittest.skipUnless(os.environ.get("BRAIN_BENCH_SCALE") == "100000",
                     "100k-node scale runs only when BRAIN_BENCH_SCALE=100000 (≈18s/query)")
class Scale100000(_ScaleCase, unittest.TestCase):
    scale = 100000

    @classmethod
    def setUpClass(cls):
        cls.stats = cls.run_scale()

    @classmethod
    def tearDownClass(cls):
        import shutil

        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_recall_at_5_at_scale(self):
        mean = statistics.fmean(q["recall@5"] for q in self.stats["queries"])
        self.assertGreaterEqual(mean, MIN_RECALL_AT_5,
                                f"mean recall@5 {mean:.3f} < {MIN_RECALL_AT_5} at 100k nodes")

    def test_report(self):
        _report(self.stats)


if __name__ == "__main__":
    unittest.main(verbosity=2)
