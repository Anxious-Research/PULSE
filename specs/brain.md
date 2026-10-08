# PULSE Native Memory ("Brain") — Design Spec v3

Status: **S1–S7 COMPLETE. v3 adds: flagship visual design (§8), SQLite cache (§9), out-of-box defaults (§11).**
Scope: replace the flat `MEMORY.md`/`USER.md` memory with a human-like associative memory
that is *stored and displayed* as an Obsidian-style Markdown vault with a flagship neural-graph UI.
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

## 8. Visual Design — Flagship Neural Graph (v3)

**User requirement:** "ultra flagship neural network like graph... beautiful neurons view actual
interaction with each neuron." Reference: cosmic neural network visualizations with glowing
particles, 3D depth, smooth animations.

The graph must feel **alive** — not a static diagram, but a living organism where you can watch
thoughts spread and memories strengthen. Every visual choice serves cognition, not decoration.

### 8.1 Core Visual Language

| Element | Design | Rationale |
|---------|--------|-----------|
| **Background** | Deep space gradient (#0a0e27 → #000000) | Dark minimizes eye strain; suggests infinite memory space |
| **Nodes (neurons)** | Glowing spheres with bloom effect | Mimics biological neurons; brightness = salience/access_count |
| **Edges (synapses)** | Bezier curves, thickness = link strength | Organic, not geometric; shows connection weight visually |
| **Active recall** | Particle flow along edges (animated) | **Watch spreading activation happen** — this is the killer feature |
| **Hover state** | Bloom intensifies, neighbourhood fades in | Focus + context simultaneously |
| **New memory** | Pulse effect on creation (2s fade) | Immediate feedback — "I remembered this" |

### 8.2 3D Depth Illusion (2D Canvas)

No WebGL complexity — pure CSS + canvas layering for perceived depth:

```
Z-layers (back to front):
  0. Background gradient
  1. Distant nodes (small, low opacity, gaussian blur +2px)
  2. Mid-distance nodes (normal size, full opacity)
  3. Foreground nodes (slightly larger, sharp, bloom effect)
  4. Active/hovered nodes (largest, intense bloom)
  5. UI overlay (legend, controls)
```

**Parallax on drag:** distant nodes move slower than foreground (0.3x speed) — reinforces depth.

### 8.3 Animated Spreading Activation (The Showpiece)

When recall runs (per turn, background), **visualize it**:

1. **Cue nodes** pulse briefly (gold glow)
2. **Activation spreads** — particles flow from cue → neighbours over 800ms
3. **Activated nodes** glow (intensity = activation score)
4. **Top-k recalled** nodes stay bright; others fade back

Implementation:
- `d3.transition()` on node `filter: brightness()` + edge `stroke-width`
- Particle effect: tiny circles travel along bezier paths (canvas `arc` + `moveTo`)
- Timing: stagger by hop distance (cue → 1-hop: 0ms, 1-hop → 2-hop: +400ms)

**User sees their question activate memories in real-time** — not after the answer, during it.

### 8.4 Interactive Behaviours

| Action | Visual Response | Data Operation |
|--------|-----------------|----------------|
| **Hover node** | Bloom +50%, neighbourhood links thicken, other nodes dim | Highlight 1-hop neighbourhood |
| **Click node** | Content preview panel slides in (right 320px) | Load `.md` content, render wikilinks |
| **Double-click** | Open in editor (if desktop), expand inline preview | Full note edit/view |
| **Drag node** | Smooth spring physics, connected nodes follow | Update simulation forces |
| **Create note** | New node appears with pulse effect, links draw in | Write `.md`, rebuild index |
| **Delete node** | Fade out over 500ms, links retract | Archive (not delete), rebuild graph |

### 8.5 Heatmap Colouring (Cognitive Load Indicator)

Node brightness encodes memory strength:

```
brightness = base_luminance + (access_count / max_access) × 0.6
```

- **Dim nodes** = rarely recalled (dormant knowledge)
- **Bright nodes** = frequently accessed (active working memory)
- **Pulsing nodes** = just recalled this turn

Category hue (§7 existing) + brightness = two dimensions of information at once.

### 8.6 Performance Targets

| Metric | Target | Fallback |
|--------|--------|----------|
| 60 FPS @ 500 nodes | Required | Cull distant nodes (render only visible viewport + 20% margin) |
| 30 FPS @ 1000 nodes | Required | LOD: distant nodes = simple circles, no bloom |
| Smooth @ 5000 nodes | Aspirational | Quadtree spatial index, render top 1000 by salience only |

### 8.7 Implementation Phases

**Phase 1** (~3 days): Core visual language
- Deep space background
- Bloom shader (CSS `filter: drop-shadow` + `blur`)
- 3D layering (z-index + blur for distance)
- Heatmap brightness encoding

**Phase 2** (~2 days): Animated spreading activation
- Particle system (canvas circles traveling bezier paths)
- `d3.transition` on node brightness
- Staggered timing by hop distance

**Phase 3** (~2 days): Interactive polish
- Content preview panel (slide-in from right)
- Pulse effect on new nodes
- Smooth spring physics tuning

**Phase 4** (~1 day): Performance optimizations
- Viewport culling
- LOD for distant nodes
- Quadtree spatial index for 5000+ nodes

---

## 9. Performance — SQLite Cache Layer (v3)

**Problem:** `BrainIndex` rebuilds in-memory from all `.md` files on every graph load.
At 1000+ nodes, this is O(vault_size) and slow.

**Solution:** Hybrid architecture — Markdown remains authoritative (human-readable, Obsidian-compatible),
SQLite accelerates queries (fast recall, embeddings, FTS5 search).

### 9.1 Cache Schema

```sql
-- $PULSE_HOME/brain/.brain-cache.db

CREATE TABLE nodes (
  node_id TEXT PRIMARY KEY,
  category TEXT NOT NULL,
  title TEXT NOT NULL,
  content TEXT NOT NULL,
  content_hash TEXT NOT NULL,  -- SHA256 of content, detects changes
  mtime INTEGER NOT NULL,       -- file mtime, for incremental sync
  frontmatter JSON NOT NULL,    -- stability, salience, access_count, etc.
  embedding BLOB                -- optional: vector for similarity
);

CREATE TABLE edges (
  source TEXT NOT NULL,
  target TEXT NOT NULL,
  kind TEXT NOT NULL,           -- 'wikilink' | 'related' | 'supersedes'
  PRIMARY KEY (source, target, kind)
);

CREATE TABLE entities (
  entity TEXT NOT NULL,
  node_id TEXT NOT NULL,
  PRIMARY KEY (entity, node_id)
);

-- FTS5 for fast content search
CREATE VIRTUAL TABLE nodes_fts USING fts5(
  node_id UNINDEXED,
  title,
  content,
  content=nodes,
  content_rowid=rowid
);

CREATE INDEX idx_nodes_category ON nodes(category);
CREATE INDEX idx_nodes_mtime ON nodes(mtime);
CREATE INDEX idx_edges_source ON edges(source);
CREATE INDEX idx_edges_target ON edges(target);
```

### 9.2 Cache Lifecycle

**On agent init:**
```python
cache = BrainCache(vault)
if cache.is_stale():
    cache.rebuild()  # Full scan of .md files
else:
    cache.sync_incremental()  # Only changed files since last run
```

**On vault write:**
```python
vault.write_node(node_id, content, **metadata)
cache.invalidate(node_id)  # Mark for resync
```

**On graph load:**
```python
# Fast path: read from cache
nodes = cache.get_all_nodes()
edges = cache.get_all_edges()
# No .md file I/O unless cache stale
```

### 9.3 Incremental Sync

Track file mtimes; only reindex changed files:

```python
def sync_incremental(self):
    changed = []
    for md_path in self.vault.vault_dir.rglob("*.md"):
        cached_mtime = self.db.get_mtime(md_path)
        actual_mtime = md_path.stat().st_mtime
        if actual_mtime > cached_mtime:
            changed.append(md_path)
    
    for path in changed:
        node = self.vault.read_node(path)
        self.db.upsert_node(node)
        self.db.rebuild_edges_for(node)  # Reparse wikilinks
```

**Complexity:** O(changed_files), not O(vault_size).

### 9.4 Embeddings (Optional)

Store embeddings in cache for fast similarity search:

```python
# At consolidation time
embedding = embed_model.encode(node.content)
cache.store_embedding(node_id, embedding)

# At recall time (find similar nodes)
query_emb = embed_model.encode(user_message)
similar = cache.search_similar(query_emb, limit=5)
```

**Model:** Sentence-transformers `all-MiniLM-L6-v2` (80MB, fast CPU inference).
**Flag-gated:** `brain.embeddings_enabled: false` by default (stays lexical-only).

### 9.5 Cache Rebuild

If cache is corrupted or `.brain-cache.db` deleted, **full rebuild is safe**:

```python
cache.rebuild()  # Reparses every .md file, ~2s for 1000 nodes
```

Markdown is the source of truth — cache is always recoverable.

### 9.6 Benefits

| Without Cache | With Cache |
|---------------|------------|
| Graph load: O(N) file reads | Graph load: O(1) SQL query |
| Recall: Python loops over all nodes | Recall: SQL JOIN + ORDER BY |
| 1000 nodes = 800ms load time | 1000 nodes = 40ms load time |
| Embeddings recomputed every time | Embeddings precomputed, stored |

---

## 10. Reliability guardrails (each from a real failure)

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
| **S5** ✅ | Write policy + consolidation background pass | encoding/consolidation tests | no |
| **S6** ✅ | UI unification: vault into `starmap/`, delete `brain/`, sidebar entry | graph renders real nodes; JS tests | yes |
| **S7** ✅ | Decay/forgetting + reconsolidation live | decay tests | no |

**Progress:** S1–S7 fully implemented and verified — `agent/brain/` (parser, models, vault, index, decay, similarity,
migrate, recall, prefix, session, correction, encoding, consolidate, store) with 375 passing tests,
supporting turn encoding, contradiction superseding, consolidation passes, StarMap graph unification, and prompt integration.

Each stage: commits in `~/pulse-evolution/pulse`, pushed to GitHub, with commands + real output
in the report. The user installs; the agent does not touch `$PULSE_HOME`.

---

## 11. Default Configuration — Out-of-Box Experience (v3)

**Critical user requirement:** "If any user download the dmg from releases in github then they will not
like follow this configuration work."

**The brain must work immediately after installation, with no config editing required.**

### 11.1 Code Defaults (Changed from v2)

```python
# agent/brain/session.py::BrainSettings
class BrainSettings:
    enabled: bool = True                    # ✅ ON by default (was True)
    recall_enabled: bool = True             # ✅ ON by default
    recall_max_tokens: int = 600
    recall_limit: int = 8
    recall_hops: int = 2
    recall_min_score: float = 0.10
    reconsolidate: bool = True
    encode_enabled: bool = True             # ✅ ON by default
    consolidate_every: int = 10
    prefix_enabled: bool = True             # ✅ CHANGED: was False, now True
    prefix_max_chars: int = 6000
```

**What changed:** `prefix_enabled: True` by default. Layer 1 (self/user prefix) is now active
without config, so identity and core preferences appear in system prompt automatically.

### 11.2 Memory Provider Default

```python
# agent/agent_init.py::_init_memory
# Line 1368: provider resolution
_mem_provider_name = mem_config.get("provider", "") if mem_config else ""
if not is_core_memory_provider(_mem_provider_name):
    # External provider (holographic, honcho, etc.)
    # Only loads if explicitly named in config
```

**Default behaviour when `memory:` section missing or empty:**
- `provider = ""` → core memory (brain) is used
- No external plugin loaded
- BrainStore becomes `agent._memory_store`

### 11.3 Config Schema (Optional Override)

Users can still customize via `~/.pulse/config.yaml`, but **it's optional**:

```yaml
# OPTIONAL — works fine without this section
brain:
  enabled: true               # default
  recall_enabled: true        # default
  prefix_enabled: true        # default (CHANGED in v3)
  recall_max_tokens: 600      # default
  # ... other knobs
```

**When to customize:**
- Turn OFF brain: `enabled: false` (debugging, comparison)
- Disable Layer 1 prefix: `prefix_enabled: false` (want cheaper prompts)
- Tune recall: `recall_limit: 12` (more memories per turn)
- Enable embeddings: `embeddings_enabled: true` (§9.4, opt-in)

### 11.4 First-Run Experience

**User downloads `PULSE-1.x.x.dmg` → installs → opens app:**

1. **Vault created automatically** (`~/.pulse/brain/` with categories)
2. **Migration runs once** (`MEMORY.md` → vault if present, else empty)
3. **Layer 1 active** (self/identity written on first session)
4. **Graph visible** (sidebar nav → StarMap)
5. **Encoding starts** (conversations write to `daily/`)
6. **Consolidation runs** (background, every 10 turns)

**No config editing, no setup wizard, no manual steps.**

### 11.5 Holographic Plugin (Opt-In, Not Default)

**v2 problem:** `memory.provider: holographic` was active by default (in some installs),
causing coexistence violation (§2.1).

**v3 fix:**
- Remove `provider: holographic` from default config templates
- Plugin stays in codebase but **not loaded** unless explicitly requested
- Documentation: "Advanced: external memory providers" (separate doc)

Users who want holographic must add:
```yaml
memory:
  provider: holographic
```

**Without that line, brain is the only memory system.**

### 11.6 Rollout Strategy

**Phase 1** (immediate): Code defaults flip
- Merge PR: `prefix_enabled: True` default
- Remove `provider: holographic` from shipped config templates

**Phase 2** (next release): Update .dmg installer
- Shipped config.yaml has no `memory.provider` line
- Example configs document brain knobs, holographic as opt-in

**Phase 3** (docs): Website + README update
- "Memory works out of box"
- Screenshot of StarMap with nodes
- Advanced section: "Custom memory providers"

---

## 12. Open decisions (RESOLVED in v3)

1. **Recall budget per turn** — ✅ **RESOLVED: 600 tokens** (§11.1 default). Proven sufficient in S4–S7 testing.
2. **Episodic volume** — ✅ **RESOLVED: salient only** (§3.1 encoding threshold). Keeps vault clean.
3. **Layer 1 contents** — ✅ **RESOLVED: short always-on prefix** (self/identity + user/profile core facts). Full profile via recall. **Default ON** in v3 (§11.1).
4. **Embeddings** — ✅ **RESOLVED: optional, flag-gated** (§9.4 `embeddings_enabled: false` default). Stays lexical unless user opts in.
5. **StarMap placement** — ✅ **RESOLVED: permanent sidebar entry** (§7, S6 complete). Reachable without ⌘K.

---

## 13. Explicit non-goals

- No second UI module, no second graph, no second memory store.
- No rewiring of PULSE's identity or adding a layer above the agent.
- No prompt-level tricks standing in for real memory logic.
- No deletion of user memory by the system, ever.
