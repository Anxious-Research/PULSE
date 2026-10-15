# Brain retrieval — benchmark report

Measured results for the production recall path (`agent.brain.recall.recall`), produced by the
two committed benchmark suites. Every number below comes from an actual run of the commands in
§5; nothing here is estimated. Datasets are **synthetic fixtures only** — no real vault, no user
data, no network, no model calls.

## 1. What is measured

| Bench | File | What it proves |
|---|---|---|
| Correctness fixture | `tests/test_brain_retrieval_benchmark.py` | 12 hand-written scenarios (exact, paraphrase, temporal, correction, cross-project, multi-hop, procedure, prospective) on a 20-node vault |
| Scale | `tests/test_brain_retrieval_scale.py` | The same production path at 1,000 / 10,000 / (opt-in) 100,000 nodes, with a realistic memory mix and planted targets |

## 2. Fixture benchmark — 20 nodes

Run: `pytest tests/test_brain_retrieval_benchmark.py -s`

| Metric | Value |
|---|---|
| Mean precision@3 | 0.65 |
| Mean recall@5 | 0.93 |
| Mean MRR | **1.00** |
| Mean latency | 2.3 ms/query |
| Gates | P@3 ≥ 0.30, MRR ≥ 0.40, R@5 ≥ 0.25 |

Every scenario retrieves its target. The corrected fact outranks its superseded predecessor
(`test_corrected_fact_wins_over_superseded`), and each project query's top result comes from the
right project.

## 3. Scale benchmark — 1,000 and 10,000 nodes

Run: `pytest tests/test_brain_retrieval_scale.py -s`

The synthetic corpus mixes concept/project/daily nodes across 7 domains with deliberately
overlapping vocabulary, plus planted targets: a procedure, a user preference, a future
intention, a belief, an episodic line, and a superseded/current fact pair.

| Metric | 1,000 nodes | 10,000 nodes |
|---|---|---|
| Nodes written | 1,002 | 10,002 |
| Build time | 0.52 s | 5.7–6.2 s |
| Index rebuild | 0.10 s | 1.2–1.4 s |
| **Recall@5 (mean)** | **1.000** | **1.000** |
| **MRR (mean)** | **0.944** | **0.944** |
| Top-1 correct | 8/9 | 8/9 |
| Latency median | 134 ms | ≈1,400 ms |
| Latency max | 173 ms | 1.8–2.6 s |
| Gates | R@5 ≥ 0.85, MRR ≥ 0.70, median < 1.5 s | same, median < 8 s |

**Finding.** Retrieval *quality is flat* from 1k → 10k: identical recall@5 (1.000) and identical
MRR (0.944), with the same single miss. The one miss is the query *"how does the nightly billing
reconciliation run"*, whose target ranks in the top-5 but not first (MRR 0.50) at **both** scales
— so it is a query-formulation/ranking characteristic, not a scale effect. Latency grows
linearly with vault size, which is the documented design property (`docs/brain-operations.md`
§6): recall is O(N) per turn and invisible at real vault sizes.

## 4. What is NOT verified here

- **100,000 nodes** is implemented but **opt-in** (`BRAIN_BENCH_SCALE=100000`) because the O(N)
  path costs ≈18 s/query at that size. It is not part of the default suite and its results are
  not claimed here — run it explicitly if the ceiling matters.
- **Semantic embeddings are off by default** in these runs (the hybrid path is BM25 + associative
  + RRF). The benchmark therefore measures the shipped default configuration. It does not
  evaluate a semantic-embedding configuration because none is active by default.
- **Cross-project contamination** is asserted on the fixture benchmark (top-1 correctness) and
  not re-measured at scale in this report.
- Latency numbers are wall-clock on one machine (macOS, Python 3.14) and will differ elsewhere;
  the gates are set an order of magnitude loose so they catch regressions, not noise.

## 5. Commands

```bash
PY=~/.pulse/cache/scratch/pulse-test-venv/bin/python

# correctness fixture (fast)
$PY -m pytest tests/test_brain_retrieval_benchmark.py -v -s

# scale (1k + 10k, ~30 s)
$PY -m pytest tests/test_brain_retrieval_scale.py -v -s

# 100k ceiling (opt-in, slow)
BRAIN_BENCH_SCALE=100000 $PY -m pytest tests/test_brain_retrieval_scale.py -v -s
```

## 6. Baseline use

The gates in both files are set from the measured behaviour above, so a ranking or performance
regression shows up as a **specific number dropping** rather than as a vague slowdown. When
changing the retrieval pipeline, run both suites and compare against this table.
