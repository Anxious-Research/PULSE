# PULSE Native Memory ("Brain") — Design Spec v2

Status: **DRAFT — awaiting approval. No code to be written until approved.**
Scope: replace the flat `MEMORY.md`/`USER.md` memory with a human-like associative memory
that is *stored and displayed* as an Obsidian-style Markdown vault.
Constraint: PULSE is one entity. The brain is an organ, not a second app, not a layer on top.

---

## 1. Goal

Make PULSE learn, remember, and recall the way a human does, and expose that memory as a
graph the user can see, read, edit, and delete like an Obsidian vault.

| Requirement (user's words) | Design answer |
|---|---|
| "unlimited memory" | No character budget, no forced eviction. Nothing is ever deleted to make room (see §5). |
| "human jaisi memory… jin logic se human ki memory chalti hai" | Encoding → consolidation → decay → cue-based associative recall → reconsolidation (§3). |
| "dikhne me obsidian vault me dikhe, lekin ho human ke jaisi" | Markdown + `[[wikilinks]]` + force-directed graph is the *view*; the human model drives *what gets stored and what gets recalled* (§3, §7). |
| "promptic behaviour se nahi, koi shortcut nahi" | Real scoring and real graph traversal. No hardcoded keyword tables, no "if user said X then inject Y". |
| "har jagah synchronised… conflicting app na banao" | Exactly one store, one writer, one graph, one UI module (§4, §8). |
| "professional, reliable, 100%" | Contract tests + failure isolation, derived from the actual failure in §2. |

**"Unlimited" caveat, stated up front:** storage is unlimited; *recall* is not.
A human also does not re-experience every memory each moment — a cue activates a few. If we
injected the whole vault every turn the context window would die. So: **unlimited vault,
bounded per-turn recall (§6).** This is a design property, not a limitation.

---

## 2. Post-mortem of the failed attempt (why this spec has guardrails)

The previous attempt produced a worse PULSE. Root causes, all verified:

1. **Split brain, two of everything.** A new `apps/desktop/src/app/brain/` UI module was added
   next to the existing `starmap/` module (21 files). Instead of one graph, two coexisted; the
   old one was left half-wired (`openStarMapNodeMenuFor` still imported by the context menu,
   nothing registering it). *Guardrail: §8, single implementation, delete don't shadow.*
2. **A public symbol was deleted while its importers stayed.** `MemoryStore`,
   `get_builtin_memory_config`, `get_builtin_memory_store_flags` were removed from
   `tools/memory_tool.py`, but `agent/agent_init.py:1334` still imported them. The import failed
   inside `with suppress(Exception)`, so `mem_config` was never bound; the later
   `mem_config.get(...)` raised `UnboundLocalError` on **every run**
   (`Memory provider plugin init failed` in `agent.log`). Result: no memory at all, old or new.
   *Guardrail: §9 contract test + §9 failure isolation.*
3. **Data destroyed before the replacement worked.** `~/.pulse/memories/` was emptied while the
   replacement was still a non-functional scaffold (8 static files, none of them a real memory).
   The user's accumulated memory content is unrecoverable. *Guardrail: §10, migrate and verify
   before removing anything, and never delete user data as part of a refactor.*
4. **Claimed rather than verified.** "16 tests OK" and "graph works" were reported while the
   live graph was empty (`nodes: 2, edges: 0`) and the memory init was crashing.

Each guardrail below traces to one of these four.

---

## 3. The human-memory model (the actual logic)

This is the part that must be real. Each mechanism maps to a concrete, testable function.

### 3.1 Encoding — what becomes a memory
Not every turn writes a memory (that produces noise, which is exactly what "faltu knowledge"
looks like). At turn end, candidate memories are extracted and **salience-scored**:

```
salience = w1·explicit      (user stated a fact/decision/preference directly)
         + w2·correction    (user corrected PULSE — always high)
         + w3·novelty       (not already represented in the vault)
         + w4·recurrence    (same theme seen before)
         + w5·outcome       (something was decided/shipped/broke)
```
Only candidates above threshold are written. Two record kinds are produced:

- **Episodic** — "what happened": turn-scoped, dated → `daily/YYYY-MM-DD.md`. Cheap to write,
  high volume, naturally time-ordered. Analogous to hippocampal capture.
- **Semantic** — "what is true": durable, decontextualised → `concept/`, `user/`, `project/`,
  `belief/`. Created mostly by consolidation (§3.2), not directly.

### 3.2 Consolidation — episodic → semantic
A background pass (after a turn, debounced; never in the response path) does what sleep does:

- Cluster recent episodic entries by shared entities/links.
- Promote repeated or important facts into a semantic node (`concept/<slug>.md`) with links.
- Merge near-duplicates (lexical + embedding similarity) instead of appending a second copy.
- Re-link: when a new node mentions an existing entity, add `[[wikilink]]` both ways.

This is why the vault grows *logically* instead of as an append-only log.

### 3.3 Decay & forgetting — nothing is deleted
Every node carries `stability` and `last_accessed`. Retrieval strength decays with time and
disuse:

```
R(t) = exp(-Δdays / (stability · S))        # forgetting curve
access  → stability ↑ (spacing effect)      # recall makes it stickier
```
Decay lowers *retrievability*, never deletes. A decayed node is still in the vault, still
searchable, still visible in the graph — it is simply less likely to surface on its own.
This is the honest answer to "unlimited": the vault is unbounded, attention is not.

Explicit deletion is a **user action only** (in the UI / via the tool). The system never
garbage-collects user memory.

### 3.4 Retrieval — cue-based associative recall
Per turn, no global scan:

1. **Cue extraction** — entities, topics, intent from the current message + recent turns.
2. **Spreading activation** — activation seeded on nodes matching cues, propagated over the
   link graph 2 hops with decay per hop, normalised by node degree so a well-connected hub
   hands each neighbour a small share instead of dominating recall (a real requirement of
   associative memory models):
   ```
   a(v) = Σ_u  a(u) · w(u,v) / degree(u)
   ```
   This is *conserving*: total activation never exceeds `seeds / (1 − decay)`, and activation
   can never exceed the seed's own. (An earlier draft divided by `log(1 + degree)`; `n/log(1+n)`
   grows with `n`, so that amplified hubs instead of dampening them.)
3. **Ranking** — combine activation × retrievability (§3.3) × salience; take top-k.
4. **Contextual gating** — if the best score is below threshold, inject **nothing**. (User
   requirement: don't inject irrelevant memory every turn.)
5. **Reconsolidation** — recalled nodes get `access_count += 1`, `last_accessed = now`
   (strengthened by use), and are marked as recall evidence in the transcript.

Retrieval returns memories *plus their connections* ("konsi knowledge ka connection kis
knowledge se hai, kab kya hua kaise hua") — that is what makes the answer feel like remembering
rather than a lookup.

### 3.5 Interference & correction
If new information contradicts an existing node, the old node is **superseded**, not orphaned:
`status: superseded`, `superseded_by: [[new-node]]`, kept in place for history. Corrections from
the user always supersede. This is the mechanism that fixes "galat direction me knowledge store
kar raha hai".

---

## 4. Single source of truth (storage)

Canonical store: **`$PULSE_HOME/brain/`** — plain Markdown files, valid Obsidian vault
(opens in real Obsidian, `.obsidian/` config included).

```
$PULSE_HOME/brain/
  self/       identity.md, wiring.md, config.md, state.md, skills.md, tools.md, gateway.md
  user/       profile.md, preferences.md
  concept/    <slug>.md          # learned domain knowledge
  project/    <slug>.md          # active work, decisions, outcomes
  belief/     <slug>.md          # working hypotheses, evolving
  daily/      YYYY-MM-DD.md      # episodic log
  .obsidian/  app.json, graph.json
  .index.json                    # derived cache — rebuildable, never authoritative
```

Frontmatter is the memory metadata (this is what makes it a *memory* and not a note dump):

```yaml
---
title: User prefers Hinglish replies
category: user
tags: [preference, language]
created_at: 1791303250
updated_at: 1791303250
last_accessed: 1791303250
access_count: 7
stability: 1.8          # decay resistance, grows on recall
salience: 0.9           # encoding importance
confidence: 0.95
status: active          # active | superseded | archived | dormant
supersedes: []
superseded_by: null
source_turn: 20261007_140413_f23194
---
```

Derived caches (`links index`, backlinks, embeddings) are **rebuildable**; if they are lost the
vault must still be fully functional. No state lives only in a cache.

---

## 5. What replaces what (no coexistence)

| Today | After |
|---|---|
| `MEMORY.md` (flat, char-budgeted) | `user/` + `concept/` nodes, no budget |
| `USER.md` (flat, char-budgeted) | `user/profile.md`, `user/preferences.md` |
| `tools/memory_tool_store.py` (`MemoryStore`) | vault store (`BrainVault`), same public tool contract kept |
| `memory` tool (separate engine) | `memory` tool = a thin front over the vault engine |
| StarMap graph (skills + flat memory cards) | StarMap graph (skills + vault nodes) — one graph |

There is **one** `memory` tool with an unchanged external schema (`action`/`target`/`content`/
`old_text`/`operations`), so nothing else in PULSE has to change. Internal engine swaps; the
contract does not.

---

## 6. Prompt integration (cache-safe, silent)

Hard invariant from the PULSE skill: **never break prompt caching.** Two layers:

- **Layer 1 — stable prefix.** `self/` core + `user/profile.md` compiled once per session into
  the system prompt. Small (< ~1.5k tokens). Session-stable → cache prefix stays valid.
- **Layer 2 — per-turn recall.** The cue-based block from §3.4 is injected into the **per-turn
  context**, never the system prompt. Bounded (~400–800 tokens, hard cap). Omitted when below
  threshold.

Silence rule: PULSE never narrates memory activity, exactly as it never narrates reading its
own system prompt. No "brain dekh raha hoon". If recall is used, it simply informs the answer.

### 6.1 Where it is wired (S4)

| Concern | Location | Notes |
| --- | --- | --- |
| Layer 2 — per-turn recall | `agent/turn_context.py::_brain_recall_block`, appended to `ext_prefetch_cache` | Reuses the existing per-turn injection channel, so the `api_content` sidecar, replay and multimodal handling are shared. No second channel, no drift. |
| Layer 1 — stable prefix | `agent/system_prompt.py::_memory_parts` → `brain_stable_prefix` | Off unless `brain.prefix_enabled`. Memoized per session; cleared by `reset_agent_cache` on the same boundary the legacy store reloads. |
| Config → behaviour | `agent/brain/session.py::BrainSettings.from_config` | Reads the `brain` section of `config.yaml` (documented in `cli-config.yaml.example`). |

Both call sites assign a default **before** any risky call and use `except` with an explicit
fallback — never `with suppress(Exception): x = ...` followed by a use of `x`. That exact shape
is what turned a failed import into an `UnboundLocalError` and killed memory on every run; it is
pinned by a test (`test_suppress_misuse_would_leave_a_local_unbound`).

Config (all optional; defaults shown):

```yaml
brain:
  enabled: true             # master switch
  recall_enabled: true
  recall_max_tokens: 600    # hard cap on the injected block
  recall_limit: 8
  recall_hops: 2
  recall_min_score: 0.10    # below this, inject nothing rather than filler
  reconsolidate: true       # recall strengthens the note (spacing effect)
  prefix_enabled: false     # Layer 1 edits the cached prefix, so it is opt-in
  prefix_max_chars: 6000
```

---

## 7. UI — one graph, Obsidian-identical interaction

**One implementation only.** The existing `apps/desktop/src/app/starmap/` module (physics
simulation, render, timeline, context menus, playback, tests — 21 files) is the single graph
UI and gets the vault wired into it. The separate `brain/` desktop module is **not** recreated.

Required behaviour (Obsidian parity):
- Force-directed graph, drag nodes, pan/zoom, physics settle.
- Edges = resolved `[[wikilinks]]`; directional arrows; unresolved links render as ghost nodes.
- Hover → highlight immediate neighbourhood; click → open note; open note → local graph.
- Inline edit / delete; rename cascades `[[wikilink]]` updates vault-wide (already partly exists).
- Colour by category (`self`/`user`/`concept`/`project`/`belief`/`daily`); filter panel.
- Live update as the vault grows; explicit refresh; performance held by incremental indexing.
- Reachable from the **sidebar** and ⌘K (base currently has ⌘K only).

Graph payload source: `agent/learning_graph.py` extends its node/edge build to include vault
nodes with category/confidence/tags — the same endpoint the desktop already calls.

---

## 8. Reliability guardrails (each from a real failure)

1. **No shadowing.** One module per concern. When unifying, *delete* the losing module and fix
   every importer in the same commit — never leave both alive.
2. **Contract test (§9)** on every symbol other modules import from the memory/vault modules.
3. **Failure isolation.** Vault unreachable ⇒ degrade to "no recall". It must never raise during
   agent init, and never be swallowed by a `suppress()` that leaves a variable unbound.
4. **No data loss in refactors.** Migration is additive and verified first. Never delete user
   memory as part of a code change. Never `rm -rf` inside `$PULSE_HOME`.
5. **Verify, don't claim.** Every completion report carries a command and its real output.
   "Tests pass" without the output is not a report.
6. **Scope discipline.** No writes to `$PULSE_HOME` as part of development; code changes land in
   the repo, the user installs.

## 9. Test plan

| Layer | Tests |
|---|---|
| Store | write/read/update/delete node, frontmatter round-trip, atomic write, concurrent-write lock |
| Parser | wikilink extraction, alias resolution, unresolved links, rename cascade |
| Decay | forgetting curve monotonic, spacing effect raises stability, never deletes |
| Recall | cue extraction, spreading activation 2-hop with fan-out divisor, threshold ⇒ empty, bounded token budget |
| Encoding | salience gate rejects trivia, corrections always encoded |
| Consolidation | duplicate merge, episodic→semantic promotion, supersede on contradiction |
| **Contract** | every symbol imported from `tools/memory_tool*` / vault modules exists (this is the §2.2 bug) |
| Isolation | vault raises ⇒ agent init succeeds, memory tool returns a clean error |
| API | `build_learning_graph` returns vault nodes + skill nodes with links |
| UI (jsdom) | graph renders N nodes/N edges from a fixture; edits/deletes reflected |

## 10. Rollout — staged, each independently verifiable and revertible

| Stage | Deliverable | Verify | Touches prompt/UI? |
| --- | --- | --- | --- |
| **S1** ✅ | Vault store + frontmatter + derived index (no UI, no prompt) | store/parser/decay tests | no |
| **S2** ✅ | Migration importer (`MEMORY.md`/`USER.md` → vault), idempotent, dry-run first | run on a copy, diff | no |
| **S3** ✅ | Recall engine (cue → activation → bounded block), flag-gated | recall tests | no |
| **S4** ✅ | Prompt integration (Layer 1/2), flag-gated | cache-prefix unchanged; inject/skip behaviour | yes |
| **S5** | Write policy + consolidation background pass | encoding/consolidation tests | no |
| **S6** | UI unification: vault into `starmap/`, delete `brain/`, sidebar entry | graph renders real nodes; JS tests | yes |
| **S7** | Decay/forgetting + reconsolidation live | decay tests | no |

**Progress:** S1–S4 done — `agent/brain/` (parser, models, vault, index, decay, similarity,
migrate, recall, prefix, session) with 200+ passing tests, wired into `turn_context` and
`system_prompt` behind `brain.*` config. Behaviour is unchanged on a fresh install: an empty
vault yields no recall and no prefix, so nothing can regress until real notes exist. S4 is the
first stage that can alter a turn, and both its layers are individually switchable.
Remaining: S5 (write policy + consolidation — the `memory` tool still writes the legacy flat
store), S6 (UI), S7 (live decay/reconsolidation).

Each stage: commits in `~/pulse-evolution/pulse`, pushed to GitHub, with commands + real output
in the report. The user installs; the agent does not touch `$PULSE_HOME`.

---

## 11. Open decisions (need user input before S1)

1. **Recall budget per turn** — 400 / 800 / 1200 tokens hard cap on Layer 2? (Default: 600.)
2. **Episodic volume** — write a `daily/` episode per turn, or only when the turn produced a
   decision/outcome? (Default: only when salient — keeps the vault clean.)
3. **Layer 1 contents** — should `user/profile.md` be fully injected every session (predictable,
   costs prefix tokens) or should the profile also go through recall (cheaper, less predictable)?
   (Default: a short always-on profile + the rest via recall.)
4. **Embeddings** — is a local embedding model acceptable for dedupe/near-duplicate merge, or
   must everything stay lexical (no extra dependency/model download)? (Default: lexical first,
   embeddings optional behind a flag.)
5. **StarMap placement** — confirm a permanent sidebar entry (base has ⌘K only).

---

## 12. Explicit non-goals

- No second UI module, no second graph, no second memory store.
- No rewiring of PULSE's identity or adding a layer above the agent.
- No prompt-level tricks standing in for real memory logic.
- No deletion of user memory by the system, ever.
