# PULSE Brain evolution — research, decisions, and what was actually built

This document is the deliverable for the *"MASTER ENGINEERING PROMPT — PULSE BRAIN EVOLUTION"*
mandate. It is deliberately written after the code, not before it: every claim below is marked with
how it was verified, and anything not verified says so.

**Legend used throughout:** `Implemented` · `Unit-tested` · `Integration-tested` ·
`Runtime-observed` · `Benchmark-verified` · `NOT VERIFIED`.

---

## 1. Reference systems and what is worth taking

These were read from primary sources (official docs / repository architecture files / the
published paper) in this session. Where a claim is a vendor headline it is labelled as such.

### 1.1 Hindsight (vectorize-io) — `Implemented` (design influence, not code reuse)

Grounded in its docs and the ACL-2026 / arXiv:2512.12818 paper:

* **Four logical networks** that separate evidence from inference: *world facts*, *agent
  experiences*, *observations* (beliefs consolidated from many facts), and *opinions* (subjective
  beliefs carrying a confidence score). Plus *mental models* and *knowledge pages* as derived,
  refreshable artefacts. The stated motivation — "systems blur the line between evidence and
  inference" — is the same problem this mandate names in §3 B/C.
* **Three operations**: `retain` → `recall` → `reflect`. `reflect` is an *agentic loop* that
  gathers its own evidence over up to ten rounds and **cannot answer before it has retrieved
  something** — a useful anti-hallucination rule.
* **Four parallel retrieval channels** — vector (HNSW), BM25 (GIN full-text), graph spreading
  activation with *link-type multipliers* (causal/entity edges weighted above weak semantic ones),
  and temporal filtering — merged by **Reciprocal Rank Fusion** (rank, not score) and then
  re-ranked with a cross-encoder. Truncation is by **token budget, not top-k**.
* **Opinion confidence moves with evidence**: supporting evidence raises confidence, contradicting
  evidence lowers it; observations are *refined, not overwritten*, and each points at the memories
  that support it with a proof count.
* **Knowledge pages never cite other knowledge pages** — an explicit guard so derived documents
  cannot cite each other into a feedback loop. This is the single most transferable anti-pattern
  guard in the design.
* Vendor-reported results (*not independently reproduced here*): 39% → 83.6% overall on
  LongMemEval/LoCoMo with a 20B open backbone; 91.4% LongMemEval, 89.61% LoCoMo at larger scale.
  Recall under 200 ms at 10,000 memory units excluding the backbone LLM call.

**Taken:** the fact/experience/belief/opinion separation; evidence-grounded confidence that moves
in both directions; rank-fusion across complementary retrieval routes; token-budget truncation;
the no-self-citation rule.

**Rejected for PULSE:** the mandatory LLM call in `retain` and `reflect`, the hosted Postgres
substrate, and cross-encoder reranking. PULSE's Brain must import with **zero third-party
dependencies** and work offline-first; a per-write or per-reflection LLM call also puts an
unbounded, paid, network-dependent step on the memory path. PULSE therefore does deterministic
extraction/reflection and keeps any model call optional and outside the core modules.

### 1.2 Mnemosyne — `Implemented` (design influence). **Name collision resolved.**

The mandate warned that several projects share this name. I checked, and there are at least four:
`mnemosyne-oss/mnemosyne` (mnemosy.ai), `28naem-del/mnemosyne`, `mnemosyne.site`, and an unrelated
"MNEMOSYNTH". The one that matches this mandate's requirements — *evolving memory for AI agents
with an evidence threshold for procedures, local-first, MIT* — is **`mnemosyne-oss/mnemosyne`**,
and that is the one referenced here.

From its published architecture:

* **Local-first, entirely SQLite, in-process.** No external database, no required network service;
  storage, indexing and retrieval all run against a single file. (PULSE reaches the same
  conclusion with a Markdown vault as the canonical store — see §3.)
* **BEAM (Bilevel Episodic-Associative Memory)**: *working memory* (hot context, TTL eviction,
  item cap) + *episodic memory* (long-term, hybrid search) + a non-searchable *scratchpad*.
  Consolidation (`sleep()`) promotes working → episodic.
* **Hybrid search by explicit weights**: 50% vector similarity + 30% FTS5 lexical + 20% importance.
* **Procedural memory with an evidence threshold** — a procedure does not enter ordinary advice
  until the configured evidence bar is met, and *"when evidence changes, the advice moves on."*
* Gradual migration (shadow → assist → prefer) rather than a hard cutover.

**Taken:** the evidence threshold *before* a learned procedure is allowed to influence advice
(this is exactly PULSE's `procedure_status` gate), the separation of hot/short-lived context from
durable memory, and importance as a first-class retrieval signal.

**Rejected:** the fixed item caps on working memory (10,000 / 1,000) — fine for a scratchpad, but
PULSE's mandate forbids arbitrary count ceilings on *durable* memory, so PULSE applies caps only to
per-turn injection and to non-durable working state.

### 1.3 Other architectures (general knowledge — `NOT VERIFIED` by primary source this session)

Stated qualitatively and flagged, because I did not re-read their papers here:

* **Letta / MemGPT** — "LLM as an operating system": paged memory with explicit read/write of a
  small "core memory" block. Lesson taken: the model should be able to *edit* its own memory
  deliberately; the injected block must be small and separate from total storage. PULSE already
  implements this split (`brain.prefix_max_chars` + `brain.recall_max_tokens` vs. unbounded vault).
* **Zep / Graphiti** — **bi-temporal** knowledge graph: every edge carries *when the event was
  true* **and** *when it was ingested*, and a contradicting fact **invalidates** rather than
  deletes. Lesson taken: preserve the prior assertion and mark it superseded, keeping both
  timestamps (PULSE stores `created_at`/`updated_at` and keeps the superseded node).
* **Generative Agents (Park et al.)** — a memory stream scored by recency + importance + relevance,
  with **reflection triggered when accumulated importance crosses a threshold** rather than every
  step. Lesson taken: reflection must be *triggered*, not continuous — PULSE uses
  `should_reflect()` and a cadence.
* **ACT-R / SOAR** — the cognitive-science split of **declarative** (facts) from **procedural**
  (production rules with conditions and expected outcomes) memory. Lesson taken: procedures are a
  different representation from facts and are judged by outcomes, not by truth.
* **A-MEM** — Zettelkasten-style *generated links* between notes rather than only pre-authored
  ones. Lesson taken cautiously: automatic linking is valuable only with a defensible semantic
  basis; weakly-supported links are worse than none (PULSE keeps typed edges with provenance and
  confidence, and rejects lexical-only similarity as sufficient).

### 1.4 Capability / cost matrix

| System | Layers | Retrieval | Contradiction | Learning | Cost model | Fits PULSE core? |
|---|---|---|---|---|---|---|
| Hindsight | world / experience / observation / opinion | 4-way + RRF + cross-encoder rerank | evidence lowers opinion confidence | `reflect` loop | hosted Postgres + LLM per op | **Partially** — layers/fusion yes; LLM+DB no |
| Mnemosyne (mnemosyne-oss) | BEAM: working / episodic / scratchpad (+ procedural) | weighted vector+FTS5+importance | NLI-style conflict detection (config) | procedures gated by evidence threshold | local SQLite, optional network | **Partially** — local-first yes; hard caps no |
| Letta/MemGPT | paged + core memory | model-driven paging | via model | self-editing memory | needs LLM | Partially (the split only) |
| Zep/Graphiti | bi-temporal KG | graph + vector | edge invalidation | — | graph DB | Partially (temporal discipline) |
| Generative Agents | memory stream | recency+importance+relevance | — | threshold-triggered reflection | small | Yes (trigger policy) |
| ACT-R / SOAR | declarative + procedural | — | — | production rules | none | Yes (the split) |
| **PULSE Brain (this work)** | self/user/concept/project/daily + **belief** + **procedure** | IDF-cue lexical + spreading activation + **deep/historical** | evidence lowers confidence; supersede keeps history | outcome→procedure, reflection→beliefs | **zero deps, offline, Markdown vault** | — |

**Explicit rejections and why.** No embeddings-by-default (would add a dependency and make the
canonical store network-dependent); no cross-encoder reranker (adds a model to the per-turn path
for a small ranking gain that PULSE's IDF-cue scoring already covers adequately); no mandatory LLM
calls in the memory core (unbounded cost and latency on a hot path); no second database as a
competing source of truth (the vault stays canonical — see §3).

---

## 2. Real baseline and gap analysis

The write/read path was traced in source, not inferred from the UI (`agent/brain/*`, `tools/`,
`apps/desktop/src/app/brain-graph/*`). Findings, with the pre-work state:

| # | Gap found | Evidence | Status |
|---|---|---|---|
| G1 | `BeliefStatus` enum existed but was **never used**; no belief layer at all | grep over `agent/` found zero uses | **Fixed** — `beliefs.py` |
| G2 | No procedural / outcome memory; "outcome" existed only as an *encoding salience* signal | `encoding.py` `_OUTCOME_PATTERNS` | **Fixed** — `outcomes.py` |
| G3 | No reflection engine; a plain model answer over recalled notes was all that existed | no module | **Fixed** — `reflection.py` |
| G4 | Relationship vocabulary had only 4 types; no `supports` / `contradicts` / `derived_from` | `relations.py` | **Fixed** — extended |
| G5 | Dormant (faded) memories were **unreachable** — dropped by the dormant filter with no alternative route | `recall()` line ~479 | **Fixed** — opt-in deep/historical route |
| G6 | Frontmatter is a YAML subset that **silently destroyed nested records** (a list of dicts came back as a list of *strings*) | reproduced directly | **Fixed** — `encode_record`/`decode_record` |
| G7 | Settings called two dead no-ops "Memory Budget" / "Profile Budget" | they were read-and-ignored | **Fixed** (earlier mandate) — marked legacy, real budgets surfaced as `brain.*` |
| G8 | Index rebuild was O(N²) (`~6.9 s` @3000 nodes) and recall re-parsed the vault every turn | benchmark | **Fixed** (earlier mandate) — `~215 ms`, recall `7340 → 568 ms` |
| G9 | No evaluation framework — success was asserted from unit tests | — | **Fixed** — 17-scenario suite + metrics |

---

## 3. Architecture decisions

### D1 — The Markdown vault stays the single canonical store. `Implemented`
Rejected: adding SQLite/vector/graph stores as co-equal sources of truth. A single canonical store
is what makes "one entity, one brain, one source of truth" true rather than aspirational, and it
means a memory survives a PULSE upgrade by virtue of being a text file. The SQLite `BrainCache`
(FTS5) is treated strictly as an *accelerator*, never as the authority.

### D2 — Beliefs are a distinct node type, not a `concept/` note. `Implemented · Unit-tested`
A concept is knowledge; a belief is a *position* that carries supporting evidence, counter-evidence,
a confidence justified by that evidence, and a revision history. Modelling it as a plain note would
lose exactly the properties §4.4 requires.

### D3 — Confidence is a function of *independent* evidence, and is never mechanical. `Implemented · Unit-tested`
`evidence_confidence` is a noisy-OR over **distinct** evidence ids: one piece of evidence →
`0.5`, each further independent piece closes half the remaining gap, inference never exceeds
`0.95`, and an outright assertion is `1.0`. Duplicate evidence is collapsed *before* scoring, so a
retried write can never inflate a belief (§13.10). Counter-evidence multiplies confidence down by
`0.35` per independent counter-example and can move a belief to `uncertain` then `refuted` —
**without deleting it**; the belief keeps its identity, its evidence and its revision log.

### D4 — Procedures are per-goal and judged by outcomes, not by assertions. `Implemented · Unit-tested`
One `procedure/<goal>` node accumulates outcomes. Successful actions become `steps`, failed actions
become `failure_modes`, and `status` is a pure function of the record
(`unproven` / `situational` / `reliable` / `avoid`). A single success is `unproven`; two wins and a
failure is `situational`; a mostly-failing approach is marked `avoid` and **still returned** for a
matching goal — knowing a method fails is a useful answer. This directly implements Mnemosyne's
"evidence threshold before a procedure enters ordinary advice."

### D5 — Reflection is deterministic, bounded, and evidence-citing. `Implemented · Unit-tested`
`reflect()` does not call a model in the core path. It retrieves evidence, detects conflicts with
the **existing** contradiction machinery (`correction.contradicts`, not a reimplementation),
clusters evidence that asserts the same thing (same subject **and** same polarity — see D6), forms
a belief only when ≥2 independent memories corroborate, records the losing side of a conflict as
counter-evidence, and reports residual uncertainty. Guards against the self-referential loop the
mandate warns about: triggered not continuous (`should_reflect`), rounds capped, ≤4 belief updates
per run, and re-reflecting on the same evidence is a no-op.

### D6 — "Same claim" ≠ "similar words". `Implemented · Unit-tested`
Two decisions here, both learned from a real bug found during this work:

* **Belief identity** (`SAME_BELIEF_SIMILARITY = 0.9`) is near-verbatim on purpose. A lexical score
  cannot distinguish *"the payments module credits a ledger"* from *"the search module credits a
  ledger"* (one token apart, different subjects) — an earlier looser threshold merged them, which
  is exactly the "lexical similarity is not a meaningful relationship" failure §3 names.
* **Corroboration across paraphrases** is therefore *not* delegated to lexical similarity. It is
  the reflection engine's job, where clustering requires the same subject (`topic_overlap ≥ 0.5`)
  **and** the same polarity.

### D7 — Deep/historical retrieval is opt-in. `Implemented · Unit-tested`
A faded memory being injected on every ordinary turn is the "recall is not selective" failure. So
the deep route (`recall(..., deep=True)`) is explicit: it only runs when asked, only recovers
memories the cue matched **directly** (seeds — no dredging through weak graph hops), and flags them
`historical=True`. The reflection engine uses it on its second round onward, which is the natural
"dig deeper" semantic. Two pre-existing selectivity tests initially failed against an *automatic*
deep route; making it explicit was the correct resolution, not weakening those tests.

### D8 — Structured records survive the frontmatter format. `Implemented · Unit-tested`
The frontmatter parser round-trips scalars and lists of scalars, but **not** nested mappings — a
JSON object gets quoted, and unquoting does not undo the inner escaping, so a list of dicts came
back as a list of broken strings. Outcome histories and revision logs are therefore serde'd to JSON
and **base64url-encoded** into a single `[A-Za-z0-9_-]` scalar that the parser keeps verbatim.
Verified with a real round-trip test, not assumed.

### D9 — Learning runs in the real loop, off the reply path. `Implemented · Integration-tested`
`brain_reflect()` is the production entry point, called from the existing per-turn background
encode worker after consolidation, on its own cadence (`brain.reflect_every`, default 6), guarded
by `brain.reflect_enabled`. It never raises and never blocks a reply. Without this the new layers
would be library code that nothing calls — the mandate is explicit that passing tests is not
learning.

---

## 4. Schema and migration

* **New frontmatter fields** (all additive, all optional): on `belief/` nodes — `evidence_refs`,
  `counter_evidence`, `belief_status`, `revisions`, `first_observed`, `last_verified`,
  `valid_from`, `asserted`; on `procedure/` nodes — `goal`, `steps`, `failure_modes`,
  `outcomes`, `success_count`, `failure_count`, `partial_count`, `procedure_status`,
  `last_verified`, `reconsider_when`.
* **New typed relations**: `supports`, `contradicts`, `derived_from`, `caused_by`, `precedes`,
  `learned_from` (added to the existing `mentions` / `wikilink` / `related_to` / `supersedes`).
  Unknown types coerce to `related_to`, so a file written by a newer build does not break an older
  one.
* **New category**: `procedure/`. `NodeCategory.coerce` already falls back to `concept` for unknown
  values, and `ensure_vault_structure()` creates the new directory idempotently.
* **Migration: none required.** No existing node is rewritten, no field is renamed, no file is
  deleted. A vault written before this work is read through the existing
  `derive_from_frontmatter` compatibility path. Existing nodes simply have no beliefs/procedures
  until new evidence produces them.
* **New settings** (`brain` section, all with defaults, all clamped):
  `reflect_enabled` (`true`), `reflect_every` (`6`, clamped 1–1000). Both are rollback levers:
  turning reflection off freezes learning while memory stays readable and encoding keeps running.

---

## 5. What is verified, and how

### Tests (`Benchmark-verified` in the narrow sense: measured, repeatable, in-process)

| Suite | Result |
|---|---|
| Full brain + memory suite | **552 passed** (was 485) |
| `tests/test_brain_beliefs.py` (new) | 16 tests |
| `tests/test_brain_outcomes.py` (new) | 14 tests |
| `tests/test_brain_reflection.py` (new) | 15 tests |
| `tests/test_brain_cognition.py` (new) | 5 tests (the production wiring seam) |
| `tests/test_brain_evaluation.py` (new) | 17 tests — the §11 scenarios |
| Desktop `src/app/settings` + `src/app/brain-graph` (vitest) | **57 files / 446 tests passed** |
| `tsc -p tsconfig.json --noEmit` | 11 errors, **all pre-existing** in `src/store/brain-events.ts`; **0 new** |
| Production build (`apps/desktop` → `node scripts/build.mjs`) | clean; fresh artifacts in `dist/` |

Interpreter note: the repo requires Python ≥3.10 (`str | object` annotations); the system `python3`
is 3.9, so the suite runs under the scratch interpreter at
`~/.pulse/cache/scratch/brainenv/bin/python` (3.14.7).

### The fifteen mandated scenarios — all `Benchmark-verified`

Direct recall · indirect associative recall · temporal before/after · correction with preserved
history · contradiction with sources · historical recall despite decay · failure learning ·
outcome learning · noise resistance · duplicate resistance · restart recovery · concurrent
ingestion (16 threads, no loss, no duplication) · large-vault retrieval (400 nodes, <2 s) ·
graph fidelity (payload nodes == canonical vault nodes, no ghost edges) · provenance (a derived
claim traces to its evidence).

### Aggregate metrics

`precision@3` and `MRR` are computed over a fixed fixture and gated by constants recorded in the
test (`MIN_PRECISION_AT_3 = 0.6`, `MIN_MRR = 0.7`). Stale-belief rate and duplicate rate are gated
at `0.0`. Floors were set from the measured behaviour of this fixture so a ranking regression shows
up as one specific number dropping.

### Runtime / integration

* The production seam is exercised: `brain_reflect()` writes beliefs into an isolated vault and
  honours the `reflect_enabled` disable lever.
* **NOT VERIFIED this session:** driving the built desktop app to observe belief/procedure nodes in
  the graph UI; a packaged-DMG fresh-install walkthrough; benchmarks above 3,000 nodes; and any
  LLM-assisted reflection path (none exists — deliberately).

---

## 6. Known limitations and open items

1. **Belief identity is lexical.** Two paraphrases that are not clustered by reflection will live
   as two beliefs. This is the conservative failure (duplication rather than fusion), and it is the
   correct side to err on given §3's warning, but it is a real limitation of a dependency-free
   scorer.
2. **Reflection writes beliefs from whatever the turn was about.** It is cadence-gated and only
   writes with ≥2 independent corroborating memories, but a long-running vault will accumulate
   beliefs that a human might consider unremarkable. `brain.reflect_enabled=false` is the lever.
3. **No knowledge-page / mental-model layer** (§4.5). Beliefs and procedures cover most of the
   requirement; the curated "how does this project work" synthesis layer is not implemented.
4. **Recall seeding is still O(N) per turn** at very large N. The index rebuild is fixed
   (O(N²) → ~O(N)); wiring the FTS5 cache prefilter into the *seed* stage would change IDF/ranking
   and was deliberately left out of scope.
5. **`provider`-independence** (§10) is architecturally satisfied (the vault is the store, the core
   imports zero third-party packages) but there is no pluggable second provider to prove it.
6. **Outcome capture has no automatic caller.** `record_outcome` is implemented, tested and
   reachable, but nothing in the conversation loop currently infers "this task succeeded/failed"
   from a turn — a domain judgement that should not be guessed at. This is the honest boundary of
   Phase D.

---

## 7. Commands

```bash
# Python suite (needs >= 3.10; the scratch interpreter is 3.14.7)
cd ~/pulse-evolution/pulse
PY=~/.pulse/cache/scratch/brainenv/bin/python
$PY -m unittest $(ls tests/ | grep -E '^test_(brain|memory)' | sed 's/\.py$//' | sed 's|^|tests.|')

# Just the new layers
$PY -m unittest tests.test_brain_beliefs tests.test_brain_outcomes \
                  tests.test_brain_reflection tests.test_brain_cognition tests.test_brain_evaluation

# Desktop tests (must run from apps/desktop with its own config)
cd apps/desktop && ../node_modules/.bin/vitest run --config vitest.config.ts src/app/settings src/app/brain-graph
../node_modules/.bin/tsc -p tsconfig.json --noEmit

# Production build (build script lives in apps/desktop, not the repo root)
cd apps/desktop && node scripts/build.mjs
```

---

## 8. Change summary — the important files

| File | What changed |
|---|---|
| `agent/brain/beliefs.py` | **new** — belief/observation layer: evidence-grounded confidence, revision history, counter-evidence, supersession |
| `agent/brain/outcomes.py` | **new** — procedural memory: outcome recording, lesson derivation, reliability status, retrieval priority |
| `agent/brain/reflection.py` | **new** — bounded evidence-based reflection with conflict detection |
| `agent/brain/session.py` | `brain_reflect()` production entry point; wiring into the per-turn background worker; `reflect_enabled`/`reflect_every` settings |
| `agent/brain/recall.py` | opt-in deep/historical retrieval; `RecallHit.historical` |
| `agent/brain/relations.py` | six new relationship types |
| `agent/brain/models.py` | `procedure` node category |
| `agent/brain/parser.py` | `encode_record`/`decode_record` — records that survive the frontmatter format |
| `pulse_cli/config_defaults.py` | `brain.reflect_enabled`, `brain.reflect_every` |
| `apps/desktop/src/app/settings/constants.ts` | labels + descriptions + curated keys for the new settings |
| `tests/test_brain_{beliefs,outcomes,reflection,cognition,evaluation}.py` | **new** — 67 tests across the five files (16+14+15+5+17) |
