"""P5 perf guard: brain graph build + recall scale within bounds on representative vaults.

A committable, bounded version of the runtime measurement (the 100k run is done out-of-band and
reported in the task notes — too slow for CI). It asserts the *shape* of the cost holds:

* graph-payload build stays well under a second at 2k nodes (the desktop panel's load path);
* hybrid recall adds only a small overhead over associative-only recall (reranking is O(admitted),
  not O(vault)), so turning hybrid on does not change recall's scaling class.

Isolated PULSE_HOME; the user's real vault is never touched.
"""

from __future__ import annotations

import os
import random
import shutil
import tempfile
import time
import unittest
from pathlib import Path

from agent.brain.index import BrainIndex
from agent.brain.recall import recall
from agent.brain.vault import BrainVault


def _build(n: int, vault_dir: Path) -> BrainVault:
    v = BrainVault(vault_dir=vault_dir)
    v.ensure_vault_structure()
    cats = ["concept", "project", "self", "user", "daily", "belief"]
    topics = ["redis cache", "postgres pool", "jwt auth", "kafka lag", "grpc deadline",
              "docker deploy", "k8s ingress", "oauth flow", "s3 bucket", "lambda cold"]
    rng = random.Random(42)
    ids: list[str] = []
    for i in range(n):
        cat = cats[i % len(cats)]
        topic = topics[i % len(topics)]
        nid = f"{cat}/node-{i}-{topic.replace(' ', '-')}"
        related = [rng.choice(ids)] if ids and rng.random() < 0.15 else None
        v.write_node(nid, f"Note {i} about {topic} and related infrastructure details.",
                     title=f"{topic} {i}", tags=[topic.split()[0]], salience=0.5,
                     related=related, now=1_800_000_000 + i)
        ids.append(nid)
    return v


class BrainPerfGuard(unittest.TestCase):
    N = 2000  # representative mid-size vault; keeps the test ~a few seconds

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-p5-guard-"))
        self.old_home = os.environ.get("PULSE_HOME")
        os.environ["PULSE_HOME"] = str(self.tmp)
        self.vault = _build(self.N, self.tmp / "brain")
        self.index = BrainIndex(self.vault).rebuild()

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("PULSE_HOME", None)
        else:
            os.environ["PULSE_HOME"] = self.old_home
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_graph_payload_build_is_sub_second(self):
        t0 = time.perf_counter()
        payload = self.index.to_graph_payload()
        elapsed = time.perf_counter() - t0
        self.assertEqual(len(payload["nodes"]), self.N)
        self.assertLess(elapsed, 1.0, f"graph payload build too slow at {self.N} nodes: {elapsed:.3f}s")

    def test_hybrid_overhead_is_bounded(self):
        q = "redis cache deploy"
        recall(q, vault=self.vault, index=self.index, limit=8, now=1_900_000_000)  # warm
        t0 = time.perf_counter()
        for _ in range(3):
            recall(q, vault=self.vault, index=self.index, limit=8, now=1_900_000_000, hybrid=False)
        base = (time.perf_counter() - t0) / 3
        t0 = time.perf_counter()
        for _ in range(3):
            recall(q, vault=self.vault, index=self.index, limit=8, now=1_900_000_000, hybrid=True)
        hyb = (time.perf_counter() - t0) / 3
        # Reranking only touches the admitted set, so it must not blow up relative to baseline.
        self.assertLess(hyb, base * 2.0 + 0.05, f"hybrid overhead too large: base={base*1000:.1f}ms hybrid={hyb*1000:.1f}ms")

    def test_recall_still_returns_relevant_hits_at_scale(self):
        # Query soon after the notes were written so decay hasn't driven retrievability to ~0
        # (the notes are timestamped around 1_800_000_000 + i).
        res = recall("redis cache", vault=self.vault, index=self.index, limit=8,
                     min_score=0.0, include_dormant=True, now=1_800_002_100)
        self.assertTrue(res.hits, "recall returned nothing on a populated vault")
        self.assertTrue(any("redis" in h.title.lower() for h in res.hits))


if __name__ == "__main__":
    unittest.main()
