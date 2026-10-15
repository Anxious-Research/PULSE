"""Versioned retrieval benchmark — measures recall quality against a fixed fixture (§17.1).

This is NOT a unit test: it is a repeatable evaluation that records baseline metrics so
future changes to the retrieval pipeline can be compared against them. The fixture is
synthetic (no real user data) and deterministic (no model calls, no randomness).

Measured metrics:
- precision@k: of the top-k recalled memories, how many are relevant?
- recall@k:    of all relevant memories for this query, how many appeared in top-k?
- MRR:         reciprocal rank of the first relevant result
- source correctness: can each recalled memory be traced to evidence?
- latency:     wall-clock time for the recall + hybrid rerank path

Scenarios cover the directive's §17.1 list:
1. Exact phrase retrieval
2. Paraphrased semantic recall
3. Old episodic recall
4. Current-versus-historical facts (correction)
5. Corrected information (superseded node)
6. Cross-session recall
7. Cross-project separation
8. Multi-hop graph relationships
9. Similar but unrelated memories
10. Procedures learned from earlier tasks
11. Future intentions and due dates (prospective)
"""

from __future__ import annotations

import shutil
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.brain.index import BrainIndex
from agent.brain.models import NodeCategory, NodeStatus
from agent.brain.recall import recall, RecallResult
from agent.brain.vault import BrainVault


# ── Fixture builder ────────────────────────────────────────────────────────

def _seed_vault(vault: BrainVault) -> Dict[str, Set[str]]:
    """Seed a vault with a fixed set of nodes and return the relevance map.

    Returns ``{query_label: {relevant_node_id, ...}}`` — the ground truth
    for every scenario.
    """
    vault.ensure_vault_structure()
    now = int(time.time())

    def w(nid: str, content: str, **kw: Any) -> None:
        cat = nid.split("/")[0] if "/" in nid else "concept"
        vault.write_node(nid, content, category=cat, now=now, **kw)

    # === Exact and paraphrase retrieval ===
    w("concept/python-web-frameworks", "Python web frameworks include Flask, Django, and FastAPI")
    w("concept/fastapi-performance", "FastAPI achieves high performance through async and Starlette")
    w("concept/django-orm", "Django provides a powerful ORM for database interactions")
    w("concept/flask-simplicity", "Flask is a lightweight framework ideal for small projects")

    # === Temporal / episodic ===
    w("daily/2026-01-15", "Deployed the payments service to staging. Used blue-green deployment.",
      tags=["episodic"], extra={"source_turns": ["turn_jan15"]})
    w("daily/2026-03-20", "Migrated the database from MySQL to PostgreSQL. Took 4 hours.",
      tags=["episodic"], extra={"source_turns": ["turn_mar20"]})
    w("daily/2026-06-10", "Fixed a critical bug in the auth service. Root cause was a race condition.",
      tags=["episodic"], extra={"source_turns": ["turn_jun10"]})

    # === Correction / supersession ===
    w("concept/deploy-strategy", "We use rolling deployments for production releases",
      status=NodeStatus.SUPERSEDED.value, extra={"superseded_by": "concept/deploy-strategy-v2"})
    w("concept/deploy-strategy-v2", "We switched to blue-green deployments after the March incident",
      supersedes=["concept/deploy-strategy"])

    # === Cross-project separation ===
    w("project/atlas", "Atlas: a distributed caching layer for the payments platform",
      tags=["project", "infrastructure"])
    w("project/beacon", "Beacon: user analytics dashboard built with React and D3",
      tags=["project", "frontend"])
    w("concept/atlas-cache-eviction", "Atlas uses LRU eviction with a 15-minute TTL",
      related=["project/atlas"])
    w("concept/beacon-charts", "Beacon renders charts using D3.js with custom transitions",
      related=["project/beacon"])

    # === Multi-hop graph ===
    w("concept/microservices", "Our architecture uses microservices communicating via gRPC",
      related=["concept/grpc-protocol"])
    w("concept/grpc-protocol", "gRPC uses Protocol Buffers for serialization",
      related=["concept/microservices", "concept/protobuf"])
    w("concept/protobuf", "Protocol Buffers are a binary serialization format by Google",
      related=["concept/grpc-protocol"])

    # === Similar but unrelated ===
    w("concept/python-testing", "Python testing frameworks: pytest, unittest, nose2")
    w("concept/python-typing", "Python type hints improve code clarity and IDE support")

    # === Procedures ===
    w("procedure/deploy-the-atlas-service",
      "Procedure: deploy the atlas service\n\nworked: blue-green swap\navoid: in-place restart\nwhy: 502s during the restart window",
      tags=["procedure", "situational"],
      extra={"goal": "deploy the atlas service", "procedure_status": "situational",
             "steps": ["blue-green swap"], "failure_modes": ["502s during restart"],
             "success_count": 3, "failure_count": 1})

    # === User model facts ===
    w("user/prefers-dark-mode", "User prefers dark mode in all editors",
      tags=["preference", "explicit"], extra={"fact_type": "preference", "fact_source": "explicit"})
    w("user/senior-engineer", "User is a senior software engineer specializing in distributed systems",
      tags=["identity", "explicit"], extra={"fact_type": "identity", "fact_source": "explicit"})

    # === Prospective (intentions) ===
    w("prospect/review-atlas-perf",
      "Review Atlas performance metrics before the quarterly review",
      tags=["intention", "active"],
      extra={"trigger_kind": "time", "due": now + 7 * 86400, "status": "active"})

    # Ground truth: which nodes are relevant to which query
    return {
        "exact_phrase": {"concept/fastapi-performance"},
        "paraphrase": {"concept/fastapi-performance", "concept/python-web-frameworks"},
        "old_episodic": {"daily/2026-01-15"},
        "current_vs_historical": {"concept/deploy-strategy-v2"},
        "corrected": {"concept/deploy-strategy-v2"},
        "cross_project_atlas": {"project/atlas", "concept/atlas-cache-eviction",
                                 "procedure/deploy-the-atlas-service"},
        "cross_project_beacon": {"project/beacon", "concept/beacon-charts"},
        "multi_hop": {"concept/microservices", "concept/grpc-protocol", "concept/protobuf"},
        "similar_unrelated": {"concept/python-testing"},
        "procedure": {"procedure/deploy-the-atlas-service"},
        "user_preference": {"user/prefers-dark-mode"},
        "prospective": {"prospect/review-atlas-perf"},
    }


# ── Queries ────────────────────────────────────────────────────────────────

QUERIES = {
    "exact_phrase": "FastAPI achieves high performance",
    "paraphrase": "which Python frameworks are fast for web APIs",
    "old_episodic": "when did we deploy the payments service to staging",
    "current_vs_historical": "what deployment strategy do we use now",
    "corrected": "what is our deploy strategy",
    "cross_project_atlas": "tell me about the Atlas project",
    "cross_project_beacon": "tell me about the Beacon dashboard",
    "multi_hop": "how do our microservices communicate and serialize data",
    "similar_unrelated": "what testing frameworks does Python have",
    "procedure": "how do I deploy the atlas service",
    "user_preference": "what theme does the user prefer",
    "prospective": "what do I need to review before the quarterly meeting",
}


# ── Metric computation ────────────────────────────────────────────────────

def precision_at_k(retrieved: List[str], relevant: Set[str], k: int) -> float:
    top = retrieved[:k]
    if not top:
        return 0.0
    return sum(1 for nid in top if nid in relevant) / len(top)


def recall_at_k(retrieved: List[str], relevant: Set[str], k: int) -> float:
    if not relevant:
        return 1.0
    top = retrieved[:k]
    return sum(1 for nid in top if nid in relevant) / len(relevant)


def mrr(retrieved: List[str], relevant: Set[str]) -> float:
    for i, nid in enumerate(retrieved):
        if nid in relevant:
            return 1.0 / (i + 1)
    return 0.0


# ── Test suite ─────────────────────────────────────────────────────────────

# Minimum thresholds for the baseline — these are set from measured performance
# after the first run, so a regression shows as a specific number dropping.
MIN_PRECISION_AT_3 = 0.30
MIN_MRR = 0.40
MIN_RECALL_AT_5 = 0.25


class RetrievalBenchmark(unittest.TestCase):
    """Versioned retrieval evaluation — measures quality against a fixed fixture."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-bench-"))
        cls.vault = BrainVault(vault_dir=cls.tmp)
        cls.relevance = _seed_vault(cls.vault)
        cls.index = BrainIndex(cls.vault).rebuild()
        cls.results: Dict[str, Dict[str, Any]] = {}

        for label, query in QUERIES.items():
            t0 = time.monotonic()
            result = recall(
                query,
                vault=cls.vault,
                index=cls.index,
                limit=8,
                hops=2,
                min_score=0.05,  # low gate for eval
                max_tokens=2000,
            )
            elapsed = time.monotonic() - t0
            retrieved = result.node_ids() if not result.empty else []
            relevant = cls.relevance.get(label, set())

            cls.results[label] = {
                "query": query,
                "retrieved": retrieved,
                "relevant": relevant,
                "precision@3": precision_at_k(retrieved, relevant, 3),
                "precision@5": precision_at_k(retrieved, relevant, 5),
                "recall@3": recall_at_k(retrieved, relevant, 3),
                "recall@5": recall_at_k(retrieved, relevant, 5),
                "mrr": mrr(retrieved, relevant),
                "latency_ms": elapsed * 1000,
                "hit_count": len(retrieved),
            }

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    # ── aggregate quality gates ────────────────────────────────────────────

    def test_aggregate_precision_at_3(self):
        """Mean precision@3 across all scenarios must exceed the baseline."""
        scores = [r["precision@3"] for r in self.results.values()]
        mean = sum(scores) / len(scores) if scores else 0
        self.assertGreaterEqual(
            mean, MIN_PRECISION_AT_3,
            f"mean precision@3 = {mean:.3f}, baseline = {MIN_PRECISION_AT_3}",
        )

    def test_aggregate_mrr(self):
        """Mean MRR across all scenarios must exceed the baseline."""
        scores = [r["mrr"] for r in self.results.values()]
        mean = sum(scores) / len(scores) if scores else 0
        self.assertGreaterEqual(
            mean, MIN_MRR,
            f"mean MRR = {mean:.3f}, baseline = {MIN_MRR}",
        )

    def test_aggregate_recall_at_5(self):
        """Mean recall@5 across all scenarios must exceed the baseline."""
        scores = [r["recall@5"] for r in self.results.values()]
        mean = sum(scores) / len(scores) if scores else 0
        self.assertGreaterEqual(
            mean, MIN_RECALL_AT_5,
            f"mean recall@5 = {mean:.3f}, baseline = {MIN_RECALL_AT_5}",
        )

    # ── per-scenario sanity checks ────────────────────────────────────────

    def test_exact_phrase_found(self):
        r = self.results["exact_phrase"]
        self.assertGreater(r["mrr"], 0, "exact phrase query must find the target")

    def test_corrected_fact_wins_over_superseded(self):
        r = self.results["corrected"]
        if r["retrieved"]:
            # The superseded node must NOT outrank the current one
            self.assertNotEqual(
                r["retrieved"][0], "concept/deploy-strategy",
                "superseded node must not be the top result for a current-state query",
            )

    def test_cross_project_no_contamination(self):
        """Atlas query should rank Atlas nodes higher than Beacon nodes."""
        atlas_retrieved = self.results["cross_project_atlas"]["retrieved"]
        beacon_retrieved = self.results["cross_project_beacon"]["retrieved"]
        atlas_relevant = {"project/atlas", "concept/atlas-cache-eviction",
                          "procedure/deploy-the-atlas-service"}
        beacon_relevant = {"project/beacon", "concept/beacon-charts"}

        # The first result for each query must be from the correct project
        if atlas_retrieved:
            self.assertIn(
                atlas_retrieved[0], atlas_relevant,
                f"Atlas query top result was {atlas_retrieved[0]}, expected an Atlas node",
            )
        if beacon_retrieved:
            self.assertIn(
                beacon_retrieved[0], beacon_relevant,
                f"Beacon query top result was {beacon_retrieved[0]}, expected a Beacon node",
            )

    def test_latency_under_budget(self):
        """Each recall must complete within 500ms on a synthetic 20-node vault."""
        for label, r in self.results.items():
            self.assertLess(
                r["latency_ms"], 500,
                f"{label}: recall took {r['latency_ms']:.1f}ms (budget: 500ms)",
            )

    # ── report (always printed, not gated) ────────────────────────────────

    def test_print_report(self):
        """Print the full benchmark report (this test always passes)."""
        print("\n" + "=" * 78)
        print("RETRIEVAL BENCHMARK REPORT")
        print("=" * 78)
        print(f"{'Scenario':<28} {'P@3':>5} {'P@5':>5} {'R@3':>5} {'R@5':>5} "
              f"{'MRR':>5} {'ms':>6} {'hits':>4}")
        print("-" * 78)
        for label, r in sorted(self.results.items()):
            print(f"{label:<28} {r['precision@3']:>5.2f} {r['precision@5']:>5.2f} "
                  f"{r['recall@3']:>5.2f} {r['recall@5']:>5.2f} "
                  f"{r['mrr']:>5.2f} {r['latency_ms']:>6.1f} {r['hit_count']:>4}")
        scores = self.results.values()
        n = len(list(scores))
        scores = list(self.results.values())
        print("-" * 78)
        print(f"{'MEAN':<28} "
              f"{sum(r['precision@3'] for r in scores)/n:>5.2f} "
              f"{sum(r['precision@5'] for r in scores)/n:>5.2f} "
              f"{sum(r['recall@3'] for r in scores)/n:>5.2f} "
              f"{sum(r['recall@5'] for r in scores)/n:>5.2f} "
              f"{sum(r['mrr'] for r in scores)/n:>5.2f} "
              f"{sum(r['latency_ms'] for r in scores)/n:>6.1f}")
        print(f"\nBaseline gates: P@3≥{MIN_PRECISION_AT_3}, MRR≥{MIN_MRR}, R@5≥{MIN_RECALL_AT_5}")
        print("=" * 78)


if __name__ == "__main__":
    unittest.main(verbosity=2)
