# PULSE Brain — research comparison

**What this document is.** A standalone, sourced comparison of seven agent-memory systems against
PULSE's native memory ("Brain"). Companion documents: `docs/brain-evolution.md` (what was built —
decisions D1–D9, defects G10–G17, live-loop evidence), `docs/brain-operations.md` (backup, restore,
recovery, measured scale), and `specs/brain.md` (the design spec).

**Scope.** Hindsight (vectorize-io) · Zep/Graphiti (getzep) · Mem0 · Letta/MemGPT · Honcho (Plastic
Labs) · A-MEM · Mnemosyne (mnemosyne-oss). For each: (1) the mechanism it actually uses, (2) its
strengths and limitations, (3) the technique PULSE adopted / adapted / rejected — and why, (4)
expected trade-offs. §8 is the comparison matrix; §9 the synthesis; §10 the source list.

**Method.** Each system was read from its own primary sources (paper, official docs, repository)
during the research pass that produced this document; sources are linked inline and listed in §10.
Performance numbers are reported exactly as their authors report them — **none were independently
reproduced here**. PULSE-side claims are verified against this repository (files named inline).
System documentation is a moving target; versions are noted where a source states one.

### Verification labels

| Label | Meaning |
|---|---|
| `[PRIMARY]` | Read from the system's own primary source (paper / official docs / repository) during this pass. |
| `[VENDOR]` | A performance headline reported by the system's authors or vendor — self-reported, not reproduced here. |
| `[GENERAL]` | General knowledge, or PULSE's own prior documentation — **not** re-verified against a primary source this pass. |
| `[PULSE]` | Verified against this repository (spec, code, or companion doc), named inline. |

### PULSE Brain, in one paragraph (the baseline everything is compared against)

PULSE Brain is a Markdown-vault-based, dependency-free, local-first memory system `[PULSE —
specs/brain.md, docs/brain-evolution.md]`. The Markdown vault is the single canonical store; a
SQLite FTS5 cache is strictly an accelerator (`specs/brain.md` §9). The pipeline is
encoding → consolidation → decay → cue-based associative recall → reconsolidation. Retrieval is
cue seeding → spreading activation over typed edges (2 hops, degree-normalised) → admission gate →
**BM25 + RRF hybrid fusion** → bounded injection (`agent/brain/hybrid.py`, `agent/brain/recall.py`).
Beliefs are separate nodes carrying evidence-grounded confidence that moves both ways
(`agent/brain/beliefs.py`); procedures are per-goal outcome records whose status is a pure function
of their outcome log (`agent/brain/outcomes.py`); prospective memory stores intentions with
time/cue triggers (`agent/brain/prospects.py`); reflection is deterministic, bounded and
evidence-citing, running in the background off the reply path (`agent/brain/reflection.py`,
`brain_reflect()` in `agent/brain/session.py`). The core imports zero third-party packages.

---

## 1. Hindsight (Vectorize)

### 1.1 Mechanism `[PRIMARY]`

Sources: the paper *"Hindsight is 20/20: Building Agent Memory that Retains, Recalls, and Reflects"*
([arXiv:2512.12818](https://arxiv.org/html/2512.12818)), the
[README](https://github.com/vectorize-io/hindsight), and
[docs — RAG vs Memory](https://hindsight.vectorize.io/developer/rag-vs-hindsight).

* **Four logical networks.** A memory bank partitions memory into `world` (objective facts about
  the external world), `experience` (the agent's own first-person history), `opinion` (subjective
  judgments, each a tuple `(text, confidence ∈ [0,1], timestamp)`), and `observation`
  (*preference-neutral* entity summaries synthesised from the underlying facts; note the README
  describes observations more loosely as "consolidated, evidence-backed beliefs", but the paper's
  definition — preference-neutral summaries, no confidence score — is the one used here). The
  paper's stated motivation: existing systems "blur the line between evidence and inference".
* **Three operations.** `retain` (LLM narrative fact extraction with normalised temporal ranges,
  entity resolution, graph link construction), `recall` (multi-strategy retrieval under a caller
  token budget `k`, not a fixed top-k), `reflect` (preference-conditioned reasoning that invokes
  recall first, then forms/updates opinions). Two components: **Tempr** (retain + recall) and
  **Cara** (reflect).
* **Typed graph.** Links are `temporal` (weight decays with Δt), `semantic` (cosine ≥ threshold),
  `entity` (shared canonical entity), `causal` (`causes` / `caused_by` / `enables` / `prevents`).
  Each edge has a type and a weight in `[0,1]`, enabling traversal-based retrieval.
* **Retrieval is a four-channel pipeline**: (1) semantic — HNSW-pgvector cosine; (2) keyword —
  BM25 over a GIN index; (3) graph — spreading activation where activation propagates as
  `A(fⱼ,t+1) = max(A(fᵢ,t)·w·δ·μ(ℓ))` with **link-type multipliers** (causal/entity edges
  weighted above weak semantic/temporal ones); (4) temporal — rule-based date parsing with a
  `flan-t5-small` fallback, matched against facts' occurrence intervals. Channels are merged with
  **Reciprocal Rank Fusion** (`k=60`), re-ranked by a **cross-encoder** (`ms-marco-MiniLM-L-6-v2`),
  then packed to the token budget in rank order.
* **Opinion evolution.** When new facts arrive, candidate opinions are found by entity overlap /
  embedding similarity; an LLM assesses the relationship and the confidence is stepped:
  reinforce `+α`, weaken `−α`, contradict `−2α`, neutral unchanged; the opinion text may also be
  refined. Opinions are explicitly described as "trajectories rather than static labels".
* **Behavioural profile.** `Θ = (skepticism, literalism, empathy) ∈ {1..5}³` plus bias strength
  `β ∈ [0,1]`, verbalised into the system prompt; `reflect` shapes opinion formation through it.
* **Observations** are regenerated asynchronously by an LLM when the facts mentioning an entity
  change — background synthesis, not on the write path.
* **Storage:** PostgreSQL + pgvector, or Oracle AI Database 23ai — a hosted/self-hosted server
  product with 25+ LLM provider options. Mental models / knowledge pages are derived, refreshable
  artefacts organised like a wiki (README).
* The claim that knowledge pages never cite other knowledge pages (a loop guard) is recorded in
  `docs/brain-evolution.md` §1.1 `[GENERAL]` — not re-verified against Hindsight docs this pass.
* **Benchmarks** `[VENDOR]`: with a 20B open backbone, 39.0% → **83.6%** on LongMemEval and
  75.78% → 85.67% on LoCoMo over a full-context baseline; with larger backbones, **91.4%**
  (LongMemEval) and **89.61%** (LoCoMo). The README states the benchmark data was independently
  reproduced by the Virginia Tech Sanghani Center and The Washington Post — that reproduction claim
  is itself vendor-reported and was not inspected here.

### 1.2 Strengths and limitations

**Strengths.** (a) The evidence/inference separation is the cleanest in this survey: facts,
experiences, observations and opinions are structurally distinct, and opinions carry explicit
confidence that moves with evidence. (b) Retrieval fuses four genuinely different relevance
notions with rank-based fusion — robust because RRF needs no cross-channel score calibration.
(c) `recall` returns a *token budget*, not a top-k — the right interface when the consumer is a
context window. (d) The reflect loop cannot answer before retrieving; extraction produces
narrative, cross-turn facts (2–5 per conversation) rather than sentence fragments. (e) Big,
publicly reported benchmark gains and a published paper.

**Limitations.** (a) Every stage — extraction, opinion assessment, observation synthesis,
background merge — needs an LLM, so the memory path carries unbounded, paid, network-dependent
steps; the paper's cheapest published configuration still uses a 20B backbone. (b) Requires a
server substrate (Postgres+pgvector / Oracle). (c) The disposition machinery shapes *opinion
formation* — a behavioural-prompt concern that a memory organ may not want to own. (d) Benchmark
numbers are architecture-level; they say nothing about a zero-dependency offline deployment.

### 1.3 What PULSE took — and why

**Adopted.**

* **Epistemic separation** (evidence vs inference): PULSE's `belief/` is a first-class category,
  distinct from `concept/`, and `procedure/`, `prospect/` are further separate node types —
  `agent/brain/models.py`, `agent/brain/beliefs.py` `[PULSE]`. The paper's key mapping note for
  PULSE readers: a PULSE **belief** corresponds to Hindsight's **opinion** (a position with
  confidence), not its observation. PULSE has no observation-network equivalent — the closest gap
  is the missing knowledge-page layer (`docs/brain-evolution.md` §6 limitation 3).
* **Evidence-grounded confidence that moves in both directions**: supporting evidence raises
  confidence, counter-evidence lowers it, and the statement survives either way —
  `agent/brain/beliefs.py` (noisy-OR over distinct evidence; `+0.5` base, ceiling `0.95`, asserted
  `1.0`; each independent counter-example multiplies by `0.35`; refuted beliefs keep identity and
  history) `[PULSE]`.
* **Multi-channel retrieval + RRF**: `agent/brain/hybrid.py` implements Okapi BM25 (`k1=1.5`,
  `b=0.75`), a semantic channel that returns empty unless a *real* trained embedder is present, and
  the associative spreading-activation channel — fused with RRF (`k=60`, Cormack et al. 2009) and
  wired into the production recall path in `agent/brain/recall.py` `[PULSE]`.
* **Token-budget truncation**: recall returns at most `brain.recall_max_tokens` (600) rendered
  tokens — same interface idea as Hindsight's `Recall(B,Q,k)` `[PULSE — specs/brain.md §6]`.
* **No-self-citation discipline**: PULSE's derived layers cite *evidence* (episodes, concepts) via
  typed edges (`derived_from` / `supports` / `contradicts`), never other derived beliefs —
  `agent/brain/relations.py`, `docs/brain-evolution.md` §4 `[PULSE]`.

**Rejected — and why.**

* **LLM calls on the retain/reflect path** (narrative fact extraction, evidence assessment,
  background merge, observation regeneration). PULSE's mandate is zero third-party dependencies,
  offline-first, and no unbounded paid network step on the memory path; extraction, assessment and
  reflection are deterministic (`agent/brain/encoding.py`, `reflection.py`).
* **Hosted Postgres/pgvector substrate.** The Markdown vault stays the single canonical store
  (`specs/brain.md` §4; decisions D1). A second database is forbidden as a competing source of
  truth.
* **Cross-encoder reranking.** A trained model on the per-turn path for a ranking gain that the
  admission gate + RRF already covers adequately; rejected explicitly in `docs/brain-evolution.md`
  §1.4.
* **Disposition parameters (skepticism/literalism/empathy).** PULSE is one entity with an identity
  file; personality shaping is not the memory organ's job.

### 1.4 Expected trade-offs

| PULSE accepts | PULSE pays |
|---|---|
| Offline, deterministic, zero-dependency memory path; no per-write cost or latency | No LLM-grade extraction: paraphrase/coreference misses are the known limitation (`brain-evolution.md` §6 #1) |
| Auditable evidence chains — every belief traces to episode/concept nodes | No cross-encoder: lower top-k precision on hard semantic queries than Hindsight's reranked pipeline |
| Coarser but cheaper belief arithmetic: noisy-OR + fixed conflict penalty, no model call | Hindsight's α-step reinforcement with LLM assessment is more nuanced (graded weaken vs contradict, text refinement) |
| RRF gives most of the multi-channel gain without calibration | Semantic channel dormant until a real embedder is installed (by design — a lexical-hash ranking is not semantics, `hybrid.py` docstring) |

---

## 2. Zep / Graphiti (getzep)

### 2.1 Mechanism `[PRIMARY]`

Sources: *"Zep: A Temporal Knowledge Graph Architecture for Agent Memory"*
([arXiv:2501.13956](https://arxiv.org/abs/2501.13956)), the
[Graphiti repository](https://github.com/getzep/graphiti), and the
[temporal-model docs](https://getzep-graphiti.mintlify.app/concepts/temporal-model).

* **Three-tier knowledge graph.** The episode subgraph stores raw input (messages, text, JSON) as a
  **non-lossy** ground-truth stream; the semantic entity subgraph holds entities and relationship
  edges extracted from episodes (bidirectional indices trace every derived fact back to its
  source episode); the community subgraph clusters strongly connected entities with high-level
  summaries.
* **Bi-temporal model.** Every edge tracks **four** timestamps: `valid_at` / `invalid_at` (when the
  fact was true in the real world) and `created_at` / `expired_at` (when the system learned or
  invalidated it). This powers point-in-time queries ("who was CEO in 2023?"), audit queries
  ("what did we know on date X?"), and correct handling of late-arriving information.
* **Contradictions invalidate, never delete.** When a new episode contradicts existing knowledge,
  an LLM compares new edges against semantically related existing ones; the old edge is updated
  (`invalid_at` ← the new fact's `valid_at`; `expired_at` ← now) and a new edge is created. New
  information is consistently prioritised; the complete history of what was known and when is
  preserved.
* **Retrieval:** hybrid semantic embeddings + keyword (BM25) + graph traversal, with temporal
  filters (`SearchFilters(valid_time=…)`). Incremental graph construction — new data integrates
  without batch recomputation. Zep claims pre-configured retrieval with sub-200ms performance at
  scale; Graphiti OSS is bring-your-own graph database (Neo4j, FalkorDB, Kuzu, Neptune), Zep runs a
  proprietary Context Graph Engine.
* **Benchmarks** `[VENDOR]`: DMR 94.8% vs MemGPT 93.4%; on LongMemEval, "accuracy improvements of
  up to 18.5% while simultaneously reducing response latency by 90%" vs baseline implementations.

### 2.2 Strengths and limitations

**Strengths.** (a) The most rigorous temporal model in the survey — four timestamps and
point-in-time queries are strictly more expressive than "created/updated". (b) Non-lossy episodes
give full provenance: every derived fact traces to source data for citation or quotation.
(c) Invalidation-not-deletion preserves relationship evolution, which is exactly what an evolving
belief system needs. (d) Hybrid retrieval (graph + semantic + keyword) and incremental updates.

**Limitations.** (a) Requires a graph database (OSS) or a managed service; a second store beside
the application's own data. (b) Contradiction detection is *LLM-driven* — per-episode model cost,
and its precision is a model-quality property, not a deterministic one. (c) Modelled around
facts; subjective beliefs, confidence and behavioural consistency are out of scope (the Hindsight
paper explicitly notes this contrast). (d) Community summaries add another LLM-generated layer to
keep fresh.

### 2.3 What PULSE took — and why

**Adopted.**

* **Invalidation-not-deletion**: PULSE supersedes — `status: superseded`, `superseded_by: [[…]]`,
  the prior node kept in place (`specs/brain.md` §3.5) — and beliefs move to `uncertain` →
  `refuted` *without* deletion, keeping statement, evidence and revision log
  (`agent/brain/beliefs.py`) `[PULSE]`. Corrections from the user always supersede.
* **Temporal metadata on every node**: `created_at` / `updated_at` / `last_accessed` /
  `access_count` on all nodes; belief nodes add `first_observed`, `last_verified`, `valid_from`,
  `asserted` (`docs/brain-evolution.md` §4) `[PULSE]`.

**Adapted.**

* **Bi-temporal discipline without the four-timestamp engine.** PULSE keeps "when recorded /
  when updated" plus belief-validity fields — not full valid-time/transaction-time separation — and
  replaces point-in-time queries with the **opt-in deep/historical recall route**
  (`recall(..., deep=True)`, hits flagged `historical=True`, seeds only — decision D7). "What did
  we think before" is answerable by asking for it, not by a first-class query surface.

**Rejected — and why.**

* **Graph database substrate** (Neo4j/FalkorDB/Kuzu/Neptune or a managed engine): a second
  canonical store is forbidden (D1); the vault's wikilink graph is the graph.
* **LLM-driven contradiction detection at write time**: PULSE reuses its existing deterministic
  contradiction machinery (`correction.contradicts`) — reflection detects conflicts with the same
  code, not a reimplementation (`reflection.py`, D5) `[PULSE]`.
* **Community summarisation**: not implemented; the honest analogue is limitation 3 (no
  knowledge-page layer) in `docs/brain-evolution.md` §6.

### 2.4 Expected trade-offs

| PULSE accepts | PULSE pays |
|---|---|
| No graph DB, no per-write model call; contradiction detection is deterministic and testable | Surface-level contradiction detection: semantically-obvious conflicts that share no tokens are caught only when reflection clusters evidence (same subject ≥0.5 overlap **and** same polarity) |
| History is never deleted; superseded nodes stay in the vault and the graph | No point-in-time query surface: historical recall is cue-matched, opt-in, and flags results rather than filtering by a time range |
| Deep/historical recall is selective by design (D7 — two selectivity tests failed against an automatic deep route) | Cannot answer "what was true on date X across the whole vault" as a query; no community/summary layer for high-level synthesis |

---

## 3. Mem0

### 3.1 Mechanism `[PRIMARY]`

Sources: *"Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory"*
([arXiv:2504.19413](https://arxiv.org/abs/2504.19413)) and
[docs — How Mem0 works](https://docs.mem0.ai/core-concepts/how-it-works).

* **Two phases, add and search.** `add` runs after a useful interaction; `search` runs before the
  next model call. The app includes whichever returned memories it wants in the prompt.
* **Write path**: context lookup against related existing memories → **LLM fact extraction**
  (preferences, decisions, plans) → deduplication + embedding → entity extraction. Extraction is
  **additive**: a new fact ("I moved from Austin to Seattle") is stored without silently rewriting
  the old one; explicit `update`/`delete` is the correction mechanism. `infer=False` stores raw
  content verbatim.
* **Retrieval signals**: semantic (vector), keyword, entity (boost memories sharing entities with
  the query), and temporal (time metadata vs the query's temporal intent). The managed Platform
  fuses all four; the OSS build depends on the configured vector store and has entity-overlap
  boosting only — **no graph memory in OSS**.
* **Stores**: SQL database (facts + metadata, *the source of truth*), vector database
  (embeddings), entity store (entities/relationships — graph memory on Platform). Scoping by
  `user_id` / `agent_id` / `run_id` / metadata is the multi-tenancy mechanism.
* **Benchmarks** `[VENDOR]`: 26% relative improvement in LLM-as-a-Judge metric over OpenAI on
  LOCOMO; the graph-memory variant scores ~2% higher overall than base Mem0; 91% lower p95 latency
  and >90% token savings vs a full-context approach.

### 3.2 Strengths and limitations

**Strengths.** (a) The cleanest write/read split of any system here: extraction at `add`, ranking
at `search`, application decides inclusion. (b) LLM extraction produces clean, deduplicated facts —
retrieval operates on distilled memories, not transcripts. (c) Explicitly additive semantics with
a correction API — honest about the danger of silent rewrites. (d) Strong published efficiency
story (latency, token cost) and multi-level scoping.

**Limitations.** (a) The core write path is an LLM call — cost, latency, network, and an
extraction-quality dependency on the model. (b) Multi-store architecture: facts in SQL, vectors in
a vector DB, entities in an entity store — three things to keep consistent. (c) OSS is weaker than
Platform (no graph memory), so capability depends on deployment shape. (d) No belief/confidence
layer — conflicts are resolved by database update semantics, as the Hindsight paper also observes.

### 3.3 What PULSE took — and why

**Adopted.**

* **Phase separation**: encoding runs in the background after the reply (`brain_encode_turn` in the
  per-turn background worker); recall runs before the answer. `[PULSE — specs/brain.md §6]`
* **Dedupe on write, additive by default**: PULSE's near-duplicate handling plus the
  restatement-as-corroboration fix (G12/G13) — a repeated claim is recorded as *evidence on the
  existing node*, not a new node, and a verbatim repeat is detected **before** the salience gate so
  the strongest evidence available is never discarded `[PULSE — docs/brain-evolution.md §2.1]`.
* **Entity signal in retrieval**: PULSE's entity anchors (`_CODED_NAME_HEADS`, G11) and the
  wikilink graph stand in for Mem0's entity store `[PULSE]`.

**Adapted.**

* **Extraction is deterministic, not LLM-driven**: a salience gate
  (`w1·explicit + w2·correction + w3·novelty + w4·recurrence + w5·outcome`, `specs/brain.md` §3.1)
  admits only candidates above threshold. PULSE keeps Mem0's "additive, no silent rewrite" rule but
  extends it: both sides of a conflict are preserved (supersede + counter-evidence) rather than
  letting the newer write win.

**Rejected — and why.**

* **LLM fact extraction as the write path** — the same core rejection as for Hindsight: no
  unbounded model call per write, offline-first.
* **Multi-store architecture** (SQL + vector + entity store) — D1: one canonical store.
* **Managed Platform semantics** — PULSE is local-first; no server dependency.

### 3.4 Expected trade-offs

| PULSE accepts | PULSE pays |
|---|---|
| No per-write LLM cost/latency; nothing leaves the machine; no three-store consistency problem | Under-extraction: an implicit preference an LLM would distil on first pass may wait for a restatement or reflection to surface (`brain-evolution.md` §6 #1–2) |
| Conflict handling that preserves both sides (supersede + counter-evidence) | Mem0's clean "latest fact wins" retrieval is simpler; PULSE's recall must rank across live + superseded + refuted material (superseded nodes are excluded by default, history is opt-in) |
| One canonical store; the "vector DB" is optional and rebuildable | Mem0's published gains come from its LLM pipeline `[VENDOR]` — no head-to-head benchmark against PULSE exists, in either direction |

---

## 4. Letta / MemGPT

### 4.1 Mechanism `[PRIMARY]` for the paper; `[GENERAL]` for product details

Source: *"MemGPT: Towards LLMs as Operating Systems"*
([arXiv:2310.08560](https://arxiv.org/abs/2310.08560)) — abstract and paper structure verified;
product-era details below are from secondary documentation (not re-verified this pass).

* **LLM as an operating system / virtual context management.** The context window is "RAM"; the
  agent pages information between it and external storage the way an OS pages between RAM and disk,
  borrowing the paging analogy from classical hierarchical memory systems.
* **Main context — three regions**: system instructions (read-only), *working context* (a
  fixed-size, self-editable block of text the LLM maintains via function calls), and a *FIFO
  queue* of recent messages whose front slot holds a recursive summary of evicted messages.
* **External context — two tiers**: *recall storage* (the complete message log, searchable with
  paginated queries) and *archival storage* (an arbitrary-length read/write database of text
  objects — facts, documents).
* **Self-directed management**: the LLM issues function calls (`recall_storage.search(...)`,
  `archival_storage.insert(...)`, working-context edits) to page information itself; a
  page-warning interrupt fires as the queue approaches capacity so the agent can persist anything
  important before eviction `[GENERAL — the 70% threshold claim comes from secondary notes]`.
* **Productised as Letta**: three tiers — *core memory* (editable blocks always in the prompt,
  e.g. `persona` / `human`, per-block character limits), *recall memory* (searchable conversation
  history), *archival memory* (long-term store) — with self-edit tools (append / replace / patch /
  rethink) `[GENERAL — secondary docs]`.
* **Benchmarks** `[GENERAL — reported in the paper per secondary notes]`: GPT-4 + MemGPT raised
  DMR accuracy from 32.1% → 92.5% and ROUGE-L from 0.296 → 0.814; dominant on nested key-value
  retrieval where baselines collapse at depth 1.

### 4.2 Strengths and limitations

**Strengths.** (a) The tier split — small always-in-context block vs unbounded searchable storage
— is the fundamental answer to "unlimited memory, bounded attention", and it predates most of the
field. (b) Genuinely **agentic** memory: the model decides what to page in and what to persist.
(c) Eviction removes information from the *prompt*, never from the system — full history stays
searchable. (d) The OS analogy is exact enough to reason about: RAM, disk, page faults, warnings.

**Limitations.** (a) Every memory operation is an LLM function call — the model must *remember to
remember*, and paging costs tokens and turns. (b) A fixed-context processor (8K GPT-4 in the
original experiments) is baked into the design's raison d'être; long-context models relieve the
pressure the architecture manages. (c) Recursive summarisation is lossy at the prompt level
(exact recall requires searching back). (d) Product complexity (blocks, tools, sandboxes) for what
is conceptually a paging scheme.

### 4.3 What PULSE took — and why

**Adopted.**

* **Bounded in-context block vs unbounded store**: PULSE's Layer 1 (stable prefix ≤
  `brain.prefix_max_chars` = 6000) + Layer 2 (per-turn recall ≤ `brain.recall_max_tokens` = 600)
  against an unbounded vault `[PULSE — specs/brain.md §6]`. This is MemGPT's core insight with the
  tiers renamed.
* **Deliberate agent-editable memory**: PULSE keeps an explicit `memory` tool (append / replace /
  delete on the vault) so the agent can *edit* its own memory, not just read it — the contract is
  unchanged from before the Brain rewrite (`specs/brain.md` §5) `[PULSE]`.

**Adapted.**

* **Model-driven paging → infrastructure-driven selective recall.** PULSE's "page fault" is
  cue-triggered: cue extraction → spreading activation → gate. The model never has to issue a
  retrieval call for memory to work; recall is computed per turn and injected when above
  threshold, empty when not (`specs/brain.md` §3.4).

**Rejected — and why.**

* **LLM-managed context** (model decides eviction and writes summaries): not available —
  the memory core makes no model calls, and PULSE never evicts from the vault at all (decay lowers
  *retrievability*, never content — §3.3).
* **Recursive LLM summaries of evicted content**: nothing is evicted, so nothing needs
  summarising.

### 4.4 Expected trade-offs

| PULSE accepts | PULSE pays |
|---|---|
| Deterministic retrieval that works even when the model issues no tool calls; cache-safe (Layer 2 never touches the system prompt) | Loses the agent's ability to deliberately page in *specific* context at *its* moment of need; PULSE's recall is whatever the cue engine decided |
| No information destroyed — decay ≠ deletion, and full text is always in the vault | No compaction pressure means vault size only grows (O(N) per-turn cost, measured in `brain-operations.md` §6; ~50 ms at 426 nodes) |
| Zero LLM cost on the recall path | No model-written summaries for context compression — the model must work from raw recalled nodes |

---

## 5. Honcho (Plastic Labs)

### 5.1 Mechanism `[PRIMARY]`

Sources: [docs — Architecture & Intuition (v3)](https://honcho.dev/docs/v3/documentation/core-concepts/architecture),
[docs — Peer Representations](https://honcho.dev/docs/v3/documentation/core-concepts/representation),
[reasoning.mdx](https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/core-concepts/reasoning.mdx),
and the [honcho-memory skill](https://raw.githubusercontent.com/plastic-labs/honcho/main/skills/honcho-memory/SKILL.md).

* **Peer-centric model.** Everything revolves around **peers** (any participant — human or AI;
  treated uniformly). Peers are containers for reasoning *across all their sessions*; **sessions**
  scope interaction threads; **messages** are the raw input. No messages → no reasoning → no
  memory.
* **Asynchronous reasoning layer (the "Deriver").** Messages are written immediately to
  PostgreSQL; reasoning tasks go to background queues. Workers extract premises and generate
  **conclusions via formal logic** — *deductive* (certain, from premises), *inductive* (patterns
  across messages), *abductive* (simplest explanations for observed behaviour). Conclusions are
  stored in vector collections for retrieval.
* **Representation artefacts**: conclusions; **summaries** (short every 20 messages, long every
  60 by default); **peer cards** (cached biographical basics — name, occupation, interests).
  The **representation** is the synthesised understanding, queryable and injectable into a system
  prompt (`get_representation`); **session context** (`get_session_context`) folds in recent
  messages + summary.
* **Perspective-taking**: `observe_me` (does Honcho reason about a peer at all) and
  `observe_others` (does a peer form representations of *other* peers from only what it observed) —
  local vs global representations. The docs frame this as theory-of-mind-style relativity.
* The reasoning models are described as custom models trained to perform formal logical reasoning;
  the rationale given is that LLMs sustain consistent reasoning across thousands of conclusions
  where humans fatigue.

### 5.2 Strengths and limitations

**Strengths.** (a) The strongest *synthesis* orientation in the survey: the deliverable is a
derived, reasoned understanding of a peer, not a bag of retrieved snippets. (b) Async by design —
writes are fast, reasoning is background, reads are "near-instant" on cached representation.
(c) The conclusions/inductive/abductive framing produces insights (patterns, explanations) that
retrieval systems structurally cannot. (d) Perspective mechanics (observe_me / observe_others) are
unique here and directly serve multi-party scenarios.

**Limitations.** (a) Requires PostgreSQL + background workers + vector storage + LLM reasoning —
the heaviest operational footprint in the set. (b) Veracity: conclusions are LLM outputs; nothing
in the docs gives them an evidence-confidence contract the way Hindsight opinions or PULSE beliefs
have. (c) Peer-scoped — it models *participants*, not task/world state (no procedure or intention
memory). (d) Reasoning quality is model-quality; no offline path.

### 5.3 What PULSE took — and why

**Adopted.**

* **Background reasoning cadence**: PULSE's learning loop runs in the existing per-turn background
  encode worker after consolidation, on its own cadence (`brain.reflect_every` = 6, guarded by
  `brain.reflect_enabled`), never blocking a reply — decision D9, integration-tested
  (`brain_reflect()` in `agent/brain/session.py`) `[PULSE]`.
* **Representation as a first-class artefact**: PULSE's synthesised layers — beliefs, procedures,
  prospective intentions, and the Layer-1 identity prefix — are the moral equivalent of Honcho's
  representation: something derived from messages that is *queried back into* future turns
  (`_procedure_guidance_block`, `_prospective_guidance_block`) `[PULSE]`.

**Adapted.**

* **Multi-peer → single entity.** PULSE is deliberately "one entity, one brain" (`specs/brain.md`
  scope note); the only perspective split is `self/` vs `user/` vault categories. Honcho's peer
  model would be over-general for a personal agent.

**Rejected — and why.**

* **LLM-based formal-logic reasoning layer** (trained reasoning models, provider-agnostic LLM
  workers): same dependency/offline rejection as elsewhere; PULSE's conclusions are deterministic
  evidence clusters (reflection requires ≥2 independent corroborating memories, same subject and
  polarity) `[PULSE — reflection.py]`.
* **PostgreSQL + queues + vector store**: D1.
* **Perspective machinery**: not needed for a single user↔agent relationship.

### 5.4 Expected trade-offs

| PULSE accepts | PULSE pays |
|---|---|
| Deterministic conclusions (subject + polarity clusters) — no hallucinated inference, no model cost, fully auditable | Fewer novel inferences: PULSE will not abduce "the user is time-constrained because deadlines dominate" from weak signals; Honcho's reasoning layer can |
| One canonical store; works offline | No cross-session "peer card" cached biography — PULSE's equivalent is the user/ nodes + Layer-1 prefix, updated by consolidation |
| — | No perspective model: PULSE cannot represent "what does the user believe PULSE knows" |

---

## 6. A-MEM

### 6.1 Mechanism `[PRIMARY]`

Source: *"A-MEM: Agentic Memory for LLM Agents"* ([arXiv:2502.12110](https://arxiv.org/pdf/2502.12110v9)).

* **Zettelkasten-inspired note construction.** Each interaction becomes a structured note
  `mᵢ = {content, timestamp, keywords, tags, contextual description, embedding, links}`; keywords,
  tags and the contextual description are **LLM-generated** to enrich retrieval beyond raw content.
  Each note is one self-contained unit of knowledge.
* **Autonomous link generation.** A new note's embedding is compared against the whole collection
  (cosine top-k); an **LLM** then decides which connections to establish, based on shared
  attributes and similar contexts. Notes can belong to multiple "boxes" simultaneously —
  connections are not a fixed taxonomy.
* **Memory evolution.** Integrating a new memory can trigger updates to the contextual
  representations of *existing* memories — the network refines its own understanding over time,
  producing higher-order patterns.
* **Retrieval:** query embedding → similarity search; notes linked to a retrieved note (same box)
  are co-accessed automatically — a cheap multi-hop assist.
* **Reported result** `[VENDOR — authors' evaluation]`: consistent improvement over SOTA baselines
  across six foundation models on long-term conversation benchmarks.

### 6.2 Strengths and limitations

**Strengths.** (a) Lifts the unit of memory from a vector to a *note with context* — keywords,
tags and a generated description give retrieval more surface to match. (b) Links are generated by
judgment (LLM), not just similarity thresholds, so relationships can be subtle, causal or
conceptual. (c) Memory evolution means the graph improves as it grows instead of fossilising.
(d) Simple, well-specified pipeline; no exotic infrastructure.

**Limitations.** (a) LLM dependency at write time for every note's metadata and every linking
decision. (b) Link quality inherits model fallibility — the paper provides no principled
confidence or provenance on links; a bad link is a permanent structural error that retrieval then
propagates (co-access). (c) Evolution can rewrite the context of existing notes, an auditability
risk (what did the note say before?). (d) Evaluated on conversational QA; long-run graph hygiene
(link rot, duplication) is not characterised.

### 6.3 What PULSE took — and why

**Adopted — cautiously.**

* **Automatic linking *with a defensible semantic basis*.** PULSE keeps typed, provenance-carrying
  edges (`mentions`, `wikilink`, `related_to`, `supports`, `contradicts`, `derived_from`,
  `caused_by`, `precedes`, `learned_from` — `agent/brain/relations.py`) `[PULSE]`. Links are
  created from entity anchors, wikilinks, consolidation re-linking, and reflection — each edge
  carries its origin.
* **Evolution in place**: consolidation re-links ("when a new node mentions an existing entity,
  add `[[wikilink]]` both ways", `specs/brain.md` §3.2) and beliefs are *refined, not overwritten* —
  every confidence change appends a revision record `[PULSE — beliefs.py]`.

**Rejected — and why.**

* **LLM-generated keywords/tags/descriptions at write time**: dependency/offline rejection.
* **Similarity alone as a connection basis.** This is the sharpest A-MEM rejection: PULSE's decision
  D6 documents a real bug where a loose lexical threshold fused *"the payments module credits a
  ledger"* and *"the search module credits a ledger"* (one token apart, different subjects). Belief
  identity is therefore near-verbatim (0.9), and cross-paraphrase corroboration is delegated to
  reflection, which checks subject **and** polarity explicitly. `docs/brain-evolution.md` §1.3
  states the principle: *weakly-supported links are worse than none*.

### 6.4 Expected trade-offs

| PULSE accepts | PULSE pays |
|---|---|
| Every edge has a reason; no silently-wrong links to propagate through co-access | Sparser graph than A-MEM: no LLM discovering subtle conceptual links between notes that share no entity/wikilink |
| Revision history on refinements — "why did PULSE change its mind, and when" is answerable | No automatic note enrichment (keywords/context descriptions) — retrieval works on raw title+body text (BM25 + activation) |
| Deterministic, inspectable graph in Obsidian | Multi-hop reach depends on links that exist; A-MEM's generated links reach further, at the risk of being wrong |

---

## 7. Mnemosyne (mnemosyne-oss)

### 7.1 Mechanism `[PRIMARY]`

Source: [mnemosyne-oss/mnemosyne repository](https://github.com/mnemosyne-oss/mnemosyne) (README).
Claims recorded only in PULSE's prior doc are tagged `[GENERAL]`.

* **Local-first, entirely SQLite, in-process.** No external service required; MIT-licensed; the
  stated positioning is "zero-cloud … one pure-Python dependency". Optional extras add local
  embeddings (`fastembed` / `sentence-transformers`, ~800 MB–1.5 GB RAM) or use a remote embedding
  API; the core profile runs in ~50 MB.
* **BEAM (Bilevel Episodic-Associative Memory)**: *working memory* (hot context, auto-injected,
  TTL-based eviction, item cap) → *episodic memory* (long-term, hybrid search) plus a
  *TripleStore* (temporal knowledge graph with version chains); a non-searchable scratchpad is
  recorded in `docs/brain-evolution.md` §1.2 `[GENERAL]`. Consolidation (`sleep()`) promotes
  working → episodic with summaries.
* **Hybrid scoring, fixed weights**: **50% vector similarity + 30% FTS5 lexical + 20% importance**,
  all inside SQLite (env-configurable; defaults confirmed in the README's configuration table).
  Recency halflife default 168 h.
* **Storage efficiency engineering**: information-theoretic binarisation (MIB) compresses
  384-dim float32 embeddings to 48 bytes (32×); Hamming distance computed inside SQLite; no ANN
  index, no external vector DB.
* **Evidence threshold for procedures** — a procedure does not enter ordinary advice until a
  configured evidence bar is met — recorded in `docs/brain-evolution.md` §1.2 `[GENERAL]`; not
  located in the README during this pass. Same label applies to the "shadow → assist → prefer"
  migration and NLI-style conflict detection (config).
* **Benchmarks** `[VENDOR]`: BEAM end-to-end QA 65.2% at 100K (v3.0.0, May 2026), vs Hindsight
  73.4% in the same published row — with an explicit judge caveat in the README that the two runs
  used different judges and are not directly comparable. Retrieval-only: Recall@10 flat at ~20%
  from 100K → 10M messages, 35 ms latency at 10M, storage sub-linear (9.4× savings from episodic
  compression), 100% abstention accuracy. A 98.9% LongMemEval Recall@All@5 figure was **withdrawn by
  the maintainers** for lack of a reproducible methodology or run log — disclosed in the README.

### 7.2 Strengths and limitations

**Strengths.** (a) Proves the local-first position end-to-end: one SQLite file, no cloud, credible
scale behaviour (flat recall latency across 100× corpus growth, sub-linear storage).
(b) Fixed, explainable scoring weights; every component (vector, lexical, importance) visible.
(c) Storage engineering that trades compression for footprint rather than accuracy-critical data.
(d) The procedural evidence-threshold idea (as documented) is a genuinely different learning
mechanism from retrieval-only systems. (e) Remarkable benchmark honesty: a withdrawn result and a
judge caveat are disclosed in the README — rare and worth noting.

**Limitations.** (a) Still a **second store**: memory lives in SQLite, parallel to the user's
documents — it is a memory *service*, not a memory *vault* the user can read and edit directly.
(b) Absolute retrieval quality is modest (20% Recall@10; the README says the *flatness*, not the
level, is the result worth citing). (c) Embeddings wanted: default profile embeds remotely (text
of memories leaves the machine) or needs ~800 MB+ of local model. (d) Fixed-weight fusion needs
calibration; weights are global, not per-query. (e) Working-memory item caps (10,000 default) are
governed by configuration, not by an evidence-driven policy.

### 7.3 What PULSE took — and why

**Adopted.**

* **Evidence threshold before a learned procedure influences advice** → PULSE's `procedure_status`
  gate (`unproven` / `situational` / `reliable` / `avoid`, a pure function of the outcome log —
  decision D4) `[PULSE — agent/brain/outcomes.py]`.
* **Separation of hot context from durable memory** → Layer 1/Layer 2 injection vs the vault
  `[PULSE]`.
* **Importance as a first-class retrieval signal** → PULSE's salience feeds activation and
  admission `[PULSE]`.
* **The local-first conclusion itself** — PULSE reaches the same end state with a different
  canonical substrate: Markdown vault instead of SQLite (see §3 of `brain-evolution.md`).

**Adapted.**

* **Fixed-weight hybrid (0.5/0.3/0.2) → rank fusion (RRF).** PULSE merges BM25 + associative (+
  optional semantic, + temporal) by rank, not by weighted score: no cross-channel calibration,
  robust to an empty channel (the semantic channel is legitimately empty on a bare install).
  `[PULSE — agent/brain/hybrid.py]`.

**Rejected — and why.**

* **Fixed item caps on durable memory** (working-memory limits): forbidden by PULSE's mandate —
  no arbitrary count ceilings on durable memory; caps apply only to per-turn injection and
  non-durable working state (`brain-evolution.md` §1.2).
* **SQLite as the canonical store**: the Markdown vault stays canonical; PULSE's SQLite FTS5 cache
  is an accelerator that may be deleted and rebuilt at any time (`specs/brain.md` §9).
* **Mandatory embeddings**: PULSE's semantic channel is gated on a *real* trained embedder and
  simply returns nothing otherwise — never a hash placeholder masquerading as semantics.

### 7.4 Expected trade-offs

| PULSE accepts | PULSE pays |
|---|---|
| Human-readable, user-editable canonical memory (Obsidian-compatible) — the store is the UI | No SQL-grade query surface; O(N) scans per turn (measured: recall 585 ms, encode ~2.9 s at 5,000 nodes; ~50 ms at 426 — `brain-operations.md` §6) |
| RRF: no calibration, graceful channel degradation | No explicit importance term in the fusion itself (salience acts through activation/admission); a fixed-weight sum can be tuned more directly |
| Semantic retrieval is honest (real embedder or nothing) | Bare installs get no semantic channel at all — weaker paraphrase retrieval than Mnemosyne-with-embeddings |
| No count ceilings on durable memory | Storage grows unboundedly; scaling story is measured-but-linear, not engineered-down |

---

## 8. Comparison matrices

### 8.1 Mechanism and architecture

All rows `[PRIMARY]` for the system they describe (sources in §10), unless tagged.

| System | Memory model | Canonical store | Write path | Retrieval | Time & contradiction | Learning loop | Works with no LLM? |
|---|---|---|---|---|---|---|---|
| **Hindsight** | 4 networks: world / experience / opinion / observation | Postgres+pgvector (or Oracle 23ai) | LLM narrative fact extraction, entity + link construction | 4 parallel channels (vector, BM25, graph activation w/ link-type multipliers, temporal) → RRF (k=60) → cross-encoder → token budget | Temporal ranges on facts; opinions step ±α on evidence | `reflect`: opinion formation / reinforcement, behavioural profile | No — LLM in retain, reflect, observation refresh |
| **Zep / Graphiti** | 3-tier temporal KG: episodes / entities / communities | Graph DB (Neo4j, FalkorDB, Kuzu, Neptune) or managed engine | Episode ingestion → LLM entity/edge extraction → edge resolution | Semantic + BM25 + graph traversal + temporal filters | **Bi-temporal**: valid_at/invalid_at + created_at/expired_at; LLM-detected contradiction → edge invalidation | Community summaries; edge invalidation | No — LLM extraction & contradiction detection |
| **Mem0** | Flat facts + entities (+ graph on Platform) | SQL (facts) + vector DB + entity store | LLM fact extraction → dedupe → embed → entity extraction; additive | Semantic + keyword + entity + temporal (Platform); OSS: vector + entity boost | Database update semantics; no belief layer | None intrinsic (extraction + dedup) | No — LLM extraction is the core |
| **Letta / MemGPT** | OS paging: main context (instr. / working / FIFO) + recall + archival tiers | Application DB + vector store | Agent's own function calls; auto message log | Agent-issued search (recall/archival); model-driven paging | Summarisation on eviction; no validity model | Self-editing memory; background summariser | No — the LLM *is* the memory manager |
| **Honcho** | Peer representations: conclusions + summaries + peer cards | PostgreSQL + queues + vector collections | Messages → background reasoning (deductive/inductive/abductive) | Query representation / session context ("near-instant") | Summaries by cadence (20/60 messages); no contradiction model documented | Async deriver; continual representation update | No — custom reasoning models |
| **A-MEM** | Zettelkasten notes w/ LLM metadata + evolving links | Note collection (flat + embeddings) | LLM generates keywords/tags/context; embedding top-k → LLM link decision; evolution of existing notes | Embedding similarity + linked-note co-access | Evolution rewrites context; no validity model | Link generation + memory evolution | No — LLM on every note + link decision |
| **Mnemosyne** | BEAM: working / episodic / TripleStore (+ scratchpad) `[GENERAL]` | SQLite (single file) | Deterministic `remember()` (optional LLM extraction flag); consolidation `sleep()` | Fixed-weight hybrid: 0.5 vector + 0.3 FTS5 + 0.2 importance, in-SQLite | Version chains in TripleStore; temporal triples (`valid_from`, as-of queries) | Consolidation promotes working → episodic | Partial — core store/recall work; embeddings & extraction optional (remote or local) |
| **PULSE Brain** `[PULSE]` | Human-model: episodic / semantic / belief / procedure / prospect categories in one vault | **Markdown vault** (SQLite FTS5 cache = accelerator) | Deterministic salience-gated encoding (background, after reply) | Cue seeding → spreading activation → gate → **BM25+RRF fusion** → bounded injection | Supersede-not-delete; created/updated + belief validity fields; deterministic conflict detection | Deterministic reflection: evidence clusters → beliefs (≥2 corroborations); outcomes → procedures | **Yes** — full pipeline is stdlib-only, offline |

### 8.2 Cost, dependency and PULSE verdict

| System | Infra required | LLM calls on memory path | Local-first default | Reported benchmark `[VENDOR]` | PULSE verdict |
|---|---|---|---|---|---|
| Hindsight | Postgres+pgvector server | Every retain / reflect / observation refresh | Self-hostable | 83.6% LongMemEval (20B); 91.4% larger | **Adapted heavily** — layers, confidence, RRF, token budget; rejected LLM path & DB |
| Zep / Graphiti | Graph DB or managed service | Extraction + contradiction detection per episode | Self-hostable (graph DB still external) | DMR 94.8%; LongMemEval +18.5%, −90% latency | **Adapted** — invalidation-not-deletion, temporal fields; rejected graph DB & LLM contradiction |
| Mem0 | SQL + vector DB (+ entity store) | Every `add` | Self-hostable OSS (Platform managed) | LOCOMO +26% vs OpenAI; −91% p95 latency | **Adapted** — phase split, dedupe/additive, entity signal; rejected LLM extraction & multi-store |
| Letta / MemGPT | App DB + vector store | Every memory operation (by design) | Self-hostable | DMR 32.1% → 92.5% `[GENERAL]` | **Partially adopted** — bounded-block vs unbounded-store split, self-editing; rejected model-driven paging |
| Honcho | Postgres + workers + vector store + LLMs | All reasoning (custom models) | Self-hostable (heavy) | 90.4% LongMem (per Mnemosyne's comparison table) `[GENERAL]` | **Concepts adopted** — background cadence, representation artefact; rejected reasoning layer & stack |
| A-MEM | Note store + embedder | Every note construction + link decision | Research code | "Superior to SOTA baselines" (paper) `[VENDOR]` | **One rule adopted (cautiously)** — auto-linking only with defensible basis; rejected similarity-only links |
| Mnemosyne | SQLite (one file) | Optional (extraction/consolidation extras) | **Yes** | BEAM 65.2% v3.0.0; flat recall @ scale | **Closest cousin** — evidence-threshold procedures, hot/cold split, importance; rejected caps & SQLite-canonical |
| PULSE Brain `[PULSE]` | None (stdlib; optional FTS5 cache, optional embedder) | None in the core | **Yes, by construction** | 623 brain+memory tests; live-loop observed (no public benchmark) | — |

### 8.3 Adoption ledger — technique by technique

| # | Technique | Origin | PULSE decision | Where it lives | Trade-off accepted |
|---|---|---|---|---|---|
| 1 | Fact/belief/epistemic separation | Hindsight | **Adopted** | `belief/` category, `beliefs.py` | PULSE belief ≈ Hindsight *opinion*; no observation-network equivalent |
| 2 | Evidence-grounded confidence, both directions | Hindsight | **Adapted** (noisy-OR, −0.35/counter-example) | `beliefs.py` | Coarser than α-step LLM-assessed updates |
| 3 | Multi-channel retrieval + RRF + token budget | Hindsight | **Adapted** | `hybrid.py`, `recall.py`, `recall_max_tokens=600` | No cross-encoder; semantic channel needs a real embedder |
| 4 | Invalidation-not-deletion | Zep/Graphiti | **Adopted** | supersede + `status`, `superseded_by` | — |
| 5 | Bi-temporal edges (4 timestamps) | Zep/Graphiti | **Adapted** (created/updated + belief validity; no point-in-time engine) | node frontmatter; `deep=True` route | No date-range queries; historical recall is opt-in |
| 6 | LLM contradiction detection at write | Zep/Graphiti | **Rejected** | deterministic `correction.contradicts` | Fewer semantically-deep catches |
| 7 | LLM fact extraction on write | Hindsight, Mem0, A-MEM, Honcho | **Rejected** | salience-gated encoding | Under-extraction; no per-write cost |
| 8 | Dedupe + additive writes; restatement = evidence | Mem0 (additive), PULSE fix (G12/G13) | **Adopted** | `encode_turn`, `record_corroboration()` | — |
| 9 | Bounded in-context block vs unbounded store | Letta/MemGPT | **Adopted** | Layer 1 (6000 chars) + Layer 2 (600 tokens) vs vault | No model-driven paging |
| 10 | Background reasoning cadence | Honcho | **Adopted** | `reflect_every=6`, background worker (D9) | No perspective model |
| 11 | Auto-linking (defensible basis only) | A-MEM (cautionary) | **Adapted, restricted** | typed edges + provenance; D6 | Sparser graph |
| 12 | Evidence threshold before procedures advise | Mnemosyne | **Adopted** | `procedure_status` (D4) | — |
| 13 | Fixed-weight hybrid fusion | Mnemosyne | **Replaced with RRF** | `hybrid.py` | No explicit importance weight in fusion |
| 14 | Hosted DB / graph DB / multi-store substrate | Hindsight, Zep, Mem0, Letta, Honcho | **Rejected** | Markdown vault canonical (D1); SQLite cache = accelerator | O(N) scans; no query engine |
| 15 | Cross-encoder rerank | Hindsight | **Rejected** | — | Lower precision on hard queries |
| 16 | Disposition/behavioural parameters | Hindsight | **Rejected** | — | Personality shaped by PULSE's identity files, not the memory organ |

---

## 9. Synthesis

**The three questions every system answers differently.**

1. **Where does inference happen — at write or at read?** Hindsight, Mem0, A-MEM and Honcho all run
   models *at write time* (extraction, linking, reasoning); Zep adds LLM contradiction detection at
   write. Letta runs no write-time model at all — the model is the *reader and pager*. PULSE is the
   odd one out: write-time work is deterministic (salience gate, restatement detection) and
   inference is deferred to a bounded background reflection pass with an evidence bar. The cost is
   extraction fidelity; the gain is that every stored memory is explainable without a model.
2. **What does retrieval fuse?** The field has converged on hybrid retrieval — Hindsight (4
   channels + RRF), Zep (semantic + BM25 + graph), Mem0 (4 signals), Mnemosyne (weighted
   3-signal). PULSE's `hybrid.py` implements the same idea in its dependency-free form: BM25 +
   spreading-activation associative + optional semantic, fused by RRF, admitted by a gate. The
   notably-absent piece everywhere except Hindsight is a *neural reranker* — the one component
   PULSE consciously trades away.
3. **How does knowledge change its mind?** Zep invalidates edges (temporally precise). Hindsight
   steps opinion confidence with LLM-assessed evidence. Mem0 updates rows (newer wins). Mnemosyne
   version-chains triples. Honcho keeps a running reasoned representation. PULSE does an explicit
   deterministic synthesis: noisy-OR confidence, −0.35 per independent counter-example, status
   transitions (`active → uncertain → refuted`), full revision log, evidence preserved as edges —
   the cheapest mechanism in the set that still makes belief change an auditable event (verified
   live: `0.725 → 0.471 → 0.306` with two counter-examples, statement and revision log intact
   `[PULSE — brain-evolution.md §5]`).

**What PULSE gives up, stated plainly.**

* **Extraction fidelity** — the single largest gap. Every LLM-based system understands paraphrases
  and implicit facts that a deterministic salience gate will miss on first pass. PULSE's mitigation
  (restatement → corroboration, reflection clustering) recovers repeated claims, not one-off
  implicit ones. This is limitation #1–2 in `brain-evolution.md` §6.
* **Neural reranking** — Hindsight's cross-encoder is the strongest precision layer in the survey;
  PULSE's RRF ordering is explainable and free, and measurably decent (`precision@3 ≥ 0.6`,
  `MRR ≥ 0.7` on the evaluation fixture `[PULSE]`), but not competitive with a trained reranker on
  adversarial queries.
* **Point-in-time queries** — Graphiti's four-timestamp model is strictly more expressive than
  PULSE's created/updated + opt-in historical route.
* **Scale engineering** — Mnemosyne's flat-latency-at-10M and PULSE's linear O(N) are different
  bets: PULSE's vault is measured at `~50 ms` per turn at 426 nodes and `585 ms` recall at 5,000
  (`brain-operations.md` §6) — fine for a personal brain, not yet a 10M-message archive.

**What PULSE has that none of the others default to.**

* A **canonical store that is a readable, editable text file** — the user can open the whole memory
  in Obsidian, and a memory survives a PULSE upgrade by virtue of being a text file (D1, D8).
* **Zero-dependency, offline operation as the default**, not an option: the core imports no
  third-party packages; embeddings are gated on a real model and simply absent otherwise.
* **Three epistemic layers no single compared system has together**: evidence-grounded beliefs
  (Hindsight-like), outcome-judged procedures with an evidence threshold (Mnemosyne-like), and
  prospective intentions with time/cue triggers (no counterpart in any system surveyed — Honcho has
  no intentions, Graphiti no commitments, Mem0 no procedures).
* **An honest, measured local loop**: 623 brain+memory tests, live end-to-end observations,
  scale benchmarks with real numbers, and limitations written down (`brain-evolution.md` §§5–6).
  No public benchmark-vs-benchmark claim is made anywhere — deliberately.

**Benchmark posture — why this comparison makes no "PULSE beats X" claim.** Published numbers
across these systems are not comparable: different backbones (Hindsight's 83.6% used a 20B model),
different judges (Mnemosyne's own README warns its 65.2% and Hindsight's 73.4% were scored under
different judges), different harnesses (DMR, LOCOMO, LongMemEval, BEAM). PULSE has no
benchmark-harness result at all — its evidence is test-level and live-loop, and this document does
not imply otherwise.

**Candidates for the next evolution pass** (traced to specific primitives above, none committed):

1. **Background observation regeneration** (Hindsight): refresh a synthesized entity/summary layer
   when the facts beneath it change — the natural fix for PULSE's missing knowledge-page layer
   (`brain-evolution.md` §6 #3), deterministic-first.
2. **`invalid_at` on superseded nodes** (Graphiti): a dated invalidation field would upgrade the
   opt-in historical route toward point-in-time answering at almost no cost.
3. **Importance/salience as an explicit fusion term** (Mnemosyne): PULSE's salience already gates
   admission; adding it as an RRF channel (or tie-breaker) is a small, testable change.
4. **Retrieval-quality evaluation at scale** (all): the current floors (`precision@3 ≥ 0.6`,
   `MRR ≥ 0.7`) run on a fixed fixture; a larger labelled set would make the hybrid vs associative
   decision measurable rather than argued.

---

## 10. Sources

Read during this research pass; label indicates how much of each was verified here.

| # | Source | Label | Notes |
|---|---|---|---|
| S1 | Hindsight paper — *Hindsight is 20/20* — https://arxiv.org/html/2512.12818 (`arXiv:2512.12818`) | `[PRIMARY]` | Sections 1–5 read (four networks, three ops, Tempr/Cara, 4-way retrieval, RRF, cross-encoder, token budget, opinion reinforcement, disposition) |
| S2 | Hindsight repository README — https://github.com/vectorize-io/hindsight | `[PRIMARY]` | Memory types, banks, knowledge pages, storage, integrations; benchmark table `[VENDOR]` |
| S3 | Hindsight docs — RAG vs Memory — https://hindsight.vectorize.io/developer/rag-vs-hindsight | `[PRIMARY]` | The 6-step retrieval pipeline (parse → 4 channels → RRF → rerank → disposition) |
| S4 | Zep paper — *A Temporal Knowledge Graph Architecture for Agent Memory* — https://arxiv.org/abs/2501.13956 (`arXiv:2501.13956`) | `[PRIMARY]` | Subgraphs, bi-temporal model, edge invalidation, DMR/LongMemEval results `[VENDOR]` |
| S5 | Graphiti repository — https://github.com/getzep/graphiti | `[PRIMARY]` | Context graphs, episodes/provenance, hybrid retrieval, Zep-vs-Graphiti split |
| S6 | Graphiti temporal-model docs — https://getzep-graphiti.mintlify.app/concepts/temporal-model | `[PRIMARY]` | Four timestamps, invalidation code paths, point-in-time queries |
| S7 | Mem0 paper — *Building Production-Ready AI Agents with Scalable Long-Term Memory* — https://arxiv.org/abs/2504.19413 (`arXiv:2504.19413`) | `[PRIMARY]` | Abstract + introduction; benchmark claims `[VENDOR]` |
| S8 | Mem0 docs — How it works — https://docs.mem0.ai/core-concepts/how-it-works | `[PRIMARY]` | Extraction/retrieval phases, four retrieval signals, store split, OSS-vs-Platform graph gap |
| S9 | MemGPT paper — *Towards LLMs as Operating Systems* — https://arxiv.org/abs/2310.08560 (`arXiv:2310.08560`) | `[PRIMARY]` (abstract); `[GENERAL]` for interior details | Tier structure and function-call management per abstract + secondary notes (paper-notes, docs sites); DMR numbers `[GENERAL]` |
| S10 | Honcho docs — Architecture v3 — https://honcho.dev/docs/v3/documentation/core-concepts/architecture | `[PRIMARY]` | Peers/sessions/messages, async data flow, Postgres+queues+vectors |
| S11 | Honcho docs — Peer Representations v3 — https://honcho.dev/docs/v3/documentation/core-concepts/representation | `[PRIMARY]` | Conclusions (deductive/inductive/abductive), summaries (20/60), peer cards, observe_me/observe_others |
| S12 | Honcho docs — Reasoning — https://github.com/plastic-labs/honcho/blob/main/docs/v3/documentation/core-concepts/reasoning.mdx | `[PRIMARY]` | Formal-logic framing, explicit/deductive output structure |
| S13 | Honcho memory skill — https://raw.githubusercontent.com/plastic-labs/honcho/main/skills/honcho-memory/SKILL.md | `[PRIMARY]` | Peer/session/conclusion model, async reasoning, representation vs context reads |
| S14 | A-MEM paper — *Agentic Memory for LLM Agents* — https://arxiv.org/pdf/2502.12110v9 (`arXiv:2502.12110`) | `[PRIMARY]` | Note construction, link generation, memory evolution, retrieval |
| S15 | Mnemosyne repository — https://github.com/mnemosyne-oss/mnemosyne | `[PRIMARY]` | BEAM, hybrid weights, MIB vectors, config defaults, sync, benchmark disclosures `[VENDOR]` |
| S16 | PULSE internal — `specs/brain.md`, `docs/brain-evolution.md`, `docs/brain-operations.md`, `agent/brain/{hybrid,recall,beliefs,outcomes,prospects,reflection}.py` | `[PULSE]` | All PULSE-side claims; verified against the repository during this pass |
