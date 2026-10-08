# Brain.md Strict Compliance Audit — 2026-10-08

Line-by-line check of specs/brain.md requirements vs. actual implementation.

---

## §1 Goal — "Unlimited Memory" + Human-Like Model

| Requirement | Status | Evidence |
|------------|--------|----------|
| No char budget, no forced eviction | ✅ COMPLIANT | `BrainStore` accepts but ignores char limits (store.py:89); vault has no budget check |
| Encoding → consolidation → decay → recall → reconsolidation | ✅ COMPLIANT | All 5 phases implemented & tested (encoding.py, consolidate.py, decay.py, recall.py, session.py:336) |
| Markdown + wikilinks + graph is VIEW; human model drives storage/recall | ✅ COMPLIANT | Vault is Markdown (vault.py:202); spreading activation drives recall (index.py:158) |
| No hardcoded keyword tables, no "if X inject Y" | ✅ COMPLIANT | Cue extraction + spreading activation (recall.py:174, index.py:158); no hardcoded rules |
| One store, one writer, one graph, one UI | ❌ **VIOLATION** | **`holographic` plugin coexists with brain** (see §5 below) |

---

## §2 Post-Mortem Guardrails

All 4 failure modes from the previous attempt:

| Guardrail | Status | Evidence |
|-----------|--------|----------|
| §2.1: No split brain, no shadow modules | ❌ **VIOLATION** | **`holographic` memory provider plugin active** (`config.yaml:727 memory.provider: holographic`); injects `fact_store` tool + `## Holographic Memory` prefix alongside brain |
| §2.2: Contract test on imports | ✅ COMPLIANT | `tests/test_memory_contract.py` exists, 10 tests pass |
| §2.3: No data destroyed before replacement works | ✅ COMPLIANT | Migration is additive (migrate.py:129); legacy files untouched |
| §2.4: Verify don't claim | ✅ COMPLIANT | 384 tests pass, real vault exists, BRAIN_AUDIT.md has real output |

**Critical finding:** §2.1 guardrail is violated. Flat memory is gone but `holographic` plugin is a second memory system.

---

## §3 Human Memory Model — Core Logic

### §3.1 Encoding (S5)

| Requirement | Status | Evidence |
|------------|--------|----------|
| Salience = w1·explicit + w2·correction + w3·novelty + w4·recurrence + w5·outcome | ✅ COMPLIANT | `encoding.py:171-175` — all 5 signals computed |
| Only above threshold written | ✅ COMPLIANT | `encoding.py:372` gates on threshold |
| Episodic (daily/YYYY-MM-DD.md) | ✅ COMPLIANT | `encoding.py:484 _append_episodic`, `daily_node_id()` |
| Semantic (concept/, user/, project/, belief/) | ✅ COMPLIANT | `encoding.py:389` kind="semantic" when durable |

### §3.2 Consolidation (S5)

| Requirement | Status | Evidence |
|------------|--------|----------|
| Cluster recent episodic by entities/links | ✅ COMPLIANT | `consolidate.py:117-129` clusters by similarity |
| Promote repeated facts → semantic | ✅ COMPLIANT | `consolidate.py:82 find_promotion_candidates` |
| Merge near-duplicates | ✅ COMPLIANT | `consolidate.py:27 is_near_duplicate` |
| Re-link: add [[wikilink]] both ways | ✅ COMPLIANT | `consolidate.py:221 relink_mentions` |

### §3.3 Decay (S7)

| Requirement | Status | Evidence |
|------------|--------|----------|
| R(t) = exp(-Δdays / (stability · S)) | ✅ COMPLIANT | `decay.py:54 retrievability` exact formula |
| Access → stability ↑ (spacing effect) | ✅ COMPLIANT | `decay.py:29 on_recall` multiplies stability |
| Decay lowers retrievability, never deletes | ✅ COMPLIANT | No deletion code; `decay.py:421` filters dormant in recall only |

### §3.4 Retrieval (S3)

| Requirement | Status | Evidence |
|------------|--------|----------|
| Cue extraction from current message | ✅ COMPLIANT | `recall.py:174 extract_cues` |
| Spreading activation 2-hop, degree-normalised | ✅ COMPLIANT | `index.py:158 activate`, division by degree at line 219 |
| Bounded: total ≤ seeds/(1-decay) | ✅ COMPLIANT | Test pins it (`test_brain_index.py:TestSpreadingActivation`) |
| Ranking: activation × retrievability × salience | ✅ COMPLIANT | `recall.py:417 rank_score` |
| Gate: below threshold → inject nothing | ✅ COMPLIANT | `recall.py:452` returns empty if best < min_score |
| Reconsolidation: access_count++, last_accessed, strengthen | ✅ COMPLIANT | `session.py:336 vault.record_access`, `vault.py:373-403` |

### §3.5 Correction (S5)

| Requirement | Status | Evidence |
|------------|--------|----------|
| Supersede not delete: status=superseded, superseded_by=[[node]] | ✅ COMPLIANT | `correction.py:221 find_contradictions`, models.py:107 |

---

## §4 Storage — Single Source of Truth

| Requirement | Status | Evidence |
|------------|--------|----------|
| `$PULSE_HOME/brain/` Markdown vault | ✅ COMPLIANT | `vault.py:61 get_brain_vault_dir` |
| Categories: self/, user/, concept/, project/, belief/, daily/ | ✅ COMPLIANT | `vault.py:89 VALID_CATEGORIES` |
| `.obsidian/` config | ✅ COMPLIANT | `vault.py:151 _write_obsidian_config` |
| Frontmatter = memory metadata (17 fields per spec) | ✅ COMPLIANT | `models.py:101-109` all 17 fields |
| `.index.json` **derived cache, rebuildable** | ❌ **MISSING** | Spec says it should exist; `BrainIndex` rebuilds in-memory only, no `.index.json` written |

**Gap:** §4 specifies `.index.json` as a derived cache on disk (line 150 of brain.md). Not implemented.

---

## §5 What Replaces What — No Coexistence

| Old | New (Spec) | Actual Status |
|-----|-----------|---------------|
| `MEMORY.md` / `USER.md` flat | vault nodes | ✅ `BrainStore` replaces them |
| `tools/memory_tool_store.py::MemoryStore` | `agent/brain/store.py::BrainStore` | ✅ Replaced |
| `memory` tool separate engine | `memory` tool = thin front | ✅ `load_on_disk_store` returns `BrainStore` |
| StarMap: skills + flat cards | StarMap: skills + vault nodes | ✅ `_memory_cards()` reads vault |

**BUT CRITICAL VIOLATION:**

**`memory.provider: holographic` plugin is ACTIVE** (config.yaml:727).

This plugin:
- Injects `## Holographic Memory` prefix into system prompt (confirmed: I see it RIGHT NOW)
- Adds `fact_store` tool (5 actions: add/search/probe/related/reason)
- Stores data in `~/.pulse/profiles/default/holographic.db` SQLite (separate from vault)
- Is a **second memory system** coexisting with the brain

**brain.md §5 line 189 says:** "There is **one** `memory` tool... Internal engine swaps; the contract does not."

**brain.md §12 line 325 says:** "No second UI module, no second graph, **no second memory store.**"

**THIS IS THE EXACT §2.1 FAILURE MODE:** "two of everything... two coexisted."

### Required Fix

**Delete or disable the `holographic` plugin.**

Options:
1. Remove `memory.provider: holographic` from config → defaults to built-in brain
2. Delete `/Users/ankitsingh/.pulse/pulse-agent/plugins/memory/holographic/` entirely
3. Disable `fact_store` tool if you want to keep the plugin infrastructure but not use it

The brain vault is the **only** memory store. No external provider.

---

## §6 Prompt Integration (S4)

| Requirement | Status | Evidence |
|------------|--------|----------|
| Layer 1 — stable prefix (self/user/, session-stable) | ✅ COMPLIANT | `system_prompt.py:530 brain_stable_prefix` |
| Layer 2 — per-turn recall (bounded, per-turn context) | ✅ COMPLIANT | `turn_context.py:893 _brain_recall_block` |
| Config-gated | ✅ COMPLIANT | `session.py:130 BrainSettings.from_config` |
| Never breaks prefix cache | ✅ COMPLIANT | Layer 1 session-stable, Layer 2 per-turn (separate channels) |
| Silence rule (no "brain dekh raha hoon") | ✅ COMPLIANT | Injected as context, never narrated |
| **But: config missing `brain:` section** | ⚠️ **CONFIG GAP** | No `brain:` in config.yaml → defaults used, but `prefix_enabled=false` by default |

### Required Fix

Add to `~/.pulse/config.yaml`:

```yaml
brain:
  enabled: true
  recall_enabled: true
  recall_max_tokens: 600
  recall_limit: 8
  recall_hops: 2
  recall_min_score: 0.10
  reconsolidate: true
  prefix_enabled: true         # Layer 1: self/user prefix
  encode_enabled: true          # Turn writes
  consolidate_every: 10
```

---

## §7 UI — One Graph, Obsidian Parity (S6)

| Requirement | Status | Evidence |
|------------|--------|----------|
| **No second UI module** | ✅ COMPLIANT | `apps/desktop/src/app/brain/` does not exist |
| One graph: `starmap/` only | ✅ COMPLIANT | 21 files in starmap/, no other graph module |
| Sidebar entry | ✅ COMPLIANT | `sidebar/index.tsx:251` starmap nav item |
| Force-directed graph | ✅ COMPLIANT | `simulation.ts` + `render.ts` d3-force |
| **Edges = resolved wikilinks, directional** | ✅ COMPLIANT | `learning_graph.py:_vault_link_edges` |
| **Ghost nodes (unresolved links)** | ✅ COMPLIANT | `render.ts:533` hollow dashed square + create-note flow |
| Hover → highlight neighbourhood | ✅ COMPLIANT | `star-map.tsx:666-669` hover/fade system |
| Click → open note | ✅ COMPLIANT | Context menu edit/delete/create |
| **Colour by category** | ✅ COMPLIANT | `color.ts:107 CATEGORY_HUES` + `render.ts` |
| **Filter panel** | ✅ COMPLIANT | `star-map.tsx:173 hiddenCategories` + legend toggle |
| **Open note → local graph** | ❌ **MISSING** | Only global graph; no 2-hop neighbourhood view on node open |
| **Rename cascades wikilinks** | ⚠️ **UNVERIFIED** | Backend `parser.py:363 rewrite_link_target` exists; desktop UI wiring unclear |
| **Incremental indexing** | ❌ **MISSING** | `BrainIndex.rebuild()` full-scans every time (O(vault_size)) |
| **Live update as vault grows** | ⚠️ **MANUAL ONLY** | `loadStarmapGraph(force=true)` after write; no file watcher |

### Required Fixes

1. **Local graph on note open** (~1 day):
   - New UI component or mode in StarMap
   - Filter graph to clicked node + 2-hop neighbourhood
   - Show in sidebar panel or modal

2. **Incremental indexing** (~1-2 days):
   - `BrainIndex` track mtimes per file
   - `rebuild()` → `update(changed_files)`
   - O(vault) → O(changes)

3. **Vault file watcher** (~4 hours):
   - FSEvents/chokidar watch `~/.pulse/brain/**/*.md`
   - Auto-call `loadStarmapGraph(true)` on change
   - Debounce to avoid thrash

4. **Rename cascade desktop wiring** (~2 hours):
   - Verify rename UI calls backend `rewrite_link_target`
   - Add test if missing

---

## §9 Contract Test (S1)

| Requirement | Status | Evidence |
|------------|--------|----------|
| Every imported symbol exists | ✅ COMPLIANT | `tests/test_memory_contract.py` 10 tests pass |
| Pins the §2.2 bug | ✅ COMPLIANT | Tests check imports don't raise |

---

## §10 Migration (S2)

| Requirement | Status | Evidence |
|------------|--------|----------|
| Additive, idempotent | ✅ COMPLIANT | `migrate.py:129` fingerprint dedupe |
| Dry-run first | ✅ COMPLIANT | Returns (ok, created, skipped, errors) |
| Never delete user memory | ✅ COMPLIANT | No rm/unlink of MEMORY.md/USER.md |

---

## Summary: What Violates / What's Missing

### CRITICAL (Blocks Compliance)

1. **§2.1 / §5 / §12 VIOLATION: `holographic` plugin coexists with brain**
   - Config has `memory.provider: holographic`
   - Second memory store (SQLite `holographic.db`)
   - Second tool (`fact_store` 5 actions)
   - Second system-prompt prefix (`## Holographic Memory`)
   - **Must be removed** — brain.md says "one store" repeatedly

2. **§6: Config missing `brain:` section**
   - Defaults work but `prefix_enabled=false`
   - Encoding/recall/consolidation won't run until config added

### MEDIUM (Spec Compliance Gaps)

3. **§4: `.index.json` derived cache not written**
   - Spec line 150 says it should exist on disk
   - `BrainIndex` is in-memory only
   - Low-priority (rebuild works fine)

4. **§7: Local graph on note open missing**
   - Spec: "open note → local graph"
   - Current: only global graph

5. **§7: Incremental indexing missing**
   - O(vault) full rebuild every graph load
   - Will choke on 1000+ nodes

6. **§7: No vault file watcher**
   - Manual refresh only (`loadStarmapGraph(force=true)`)
   - Should auto-refresh when `.md` changes

7. **§7: Rename cascade desktop UI unverified**
   - Backend exists (`rewrite_link_target`)
   - Desktop wiring unclear

---

## Action Plan (Priority Order)

### P0 — BLOCKERS (Must fix for compliance)

1. **Remove `holographic` plugin coexistence**
   - Edit `/Users/ankitsingh/.pulse/config.yaml`: delete line `provider: holographic` under `memory:`
   - Or set `memory: {}` (empty = use built-in brain)
   - Restart PULSE
   - Verify: new session should NOT show `## Holographic Memory` or `fact_store` tool

2. **Add `brain:` config section**
   - Add the full config block (see §6 above)
   - Restart PULSE
   - Verify: new session shows vault recall, Layer 1 prefix

### P1 — Spec Gaps (For full §7 compliance)

3. **Incremental indexing** (~1-2 days)
4. **Local graph on note open** (~1 day)
5. **Vault file watcher** (~4 hours)
6. **Rename cascade desktop verification** (~2 hours)

### P2 — Nice to Have

7. **`.index.json` persistent cache** (spec says it should exist; low-impact)

---

## Verification Commands

After P0 fixes:

```bash
# 1. Check config
grep -A10 "^brain:" ~/.pulse/config.yaml
grep "provider:" ~/.pulse/config.yaml | grep memory

# 2. Restart PULSE (desktop + daemon)
# (user does this)

# 3. Start new session, check prompt
# Should see: "## Holographic Memory" with vault node count
# Should NOT see: flat "MEMORY (your personal notes) [60% — N/2200 chars]"
# Should NOT see: fact_store tool

# 4. Check vault grows
ls -lt ~/.pulse/brain/daily/
# Should see new daily/ entries after conversations

# 5. Check consolidation runs
# (background, check logs or vault concept/ growth over time)
```

---

## Conclusion

**Core brain.md logic (§3) is 100% compliant** — encoding, consolidation, decay, recall, correction all real and tested.

**Critical violation:** `holographic` plugin coexists with brain, breaking §2.1 / §5 / §12 "one store" rule.

**Medium gaps:** §7 UI polish (local graph, incremental indexing, file watcher, rename verification).

**Fix P0 first (remove holographic + add brain config), then §7 gaps.**
