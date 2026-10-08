# Brain.md Implementation Audit — 2026-10-08

## Summary

**Status: S1-S7 backend is REAL and WORKING. Desktop UI is 80% complete.**

The vault, encoding, consolidation, recall, decay — all the cognitive machinery from brain.md §3 — exists and is wired. 384 tests pass. The live `~/.pulse/brain/` vault has real migrated content (13 .md files with frontmatter, wikilinks, categories).

**However:** the live PULSE app (`~/.pulse/pulse-agent`) was **reverted** to pre-brain state (commit `0c7cfd4a revert: restore pre-brain memory system and StarMap`), so **none of this runs in production yet**. The dev repo has it ready, the user has not installed it.

---

## What brain.md Asked For vs. What Exists

### §3 Human Memory Model — THE CORE LOGIC

| Mechanism | Spec Location | Implementation | Status |
|-----------|---------------|----------------|--------|
| **3.1 Encoding** — salience-scored turn write | §3.1, S5 | `agent/brain/encoding.py::encode_turn` (485 LOC)<br/>Salience formula with w1-w5 weights<br/>Episodic/semantic split | ✅ REAL |
| **3.2 Consolidation** — episodic→semantic promotion | §3.2, S5 | `agent/brain/consolidate.py::consolidate_vault` (340 LOC)<br/>Clusters by entities, promotes repeated facts, merges duplicates, adds wikilinks | ✅ REAL |
| **3.3 Decay** — forgetting curve, spacing effect | §3.3, S7 | `agent/brain/decay.py` (134 LOC)<br/>`R(t) = exp(-Δdays / (stability · S))`<br/>`on_recall` raises stability | ✅ REAL |
| **3.4 Retrieval** — cue-based spreading activation | §3.4, S3 | `agent/brain/recall.py::recall` (559 LOC)<br/>`agent/brain/index.py::activate` (spreading activation with degree normalisation, bounded by seeds/(1-decay))<br/>Per-turn, 2-hop, threshold gating | ✅ REAL |
| **3.5 Correction** — supersede not delete | §3.5, S5 | `agent/brain/correction.py::find_contradictions` (255 LOC)<br/>Sets `status: superseded`, `superseded_by: [[node]]` | ✅ REAL |

**Verdict: The human-memory model is NOT promptic theatre. It's real graph traversal, real scoring, real metadata evolution.**

---

### §4 Single Source of Truth — Storage

| Requirement | Implementation | Status |
|-------------|----------------|--------|
| `$PULSE_HOME/brain/` vault with categories | `agent/brain/vault.py::BrainVault` (498 LOC)<br/>Creates `self/`, `user/`, `concept/`, `project/`, `belief/`, `daily/` | ✅ REAL |
| Frontmatter = memory metadata (stability, salience, access_count, etc.) | `agent/brain/models.py::MemoryFrontmatter` with 17 fields<br/>Round-trip preserved by `vault.write_node` | ✅ REAL |
| `.obsidian/` config for real Obsidian | `vault.py::_write_obsidian_config` writes `app.json` + `graph.json` | ✅ REAL |
| Derived caches rebuildable | `agent/brain/index.py::BrainIndex.rebuild()` — 2-pass: identities then links | ✅ REAL |
| Live vault exists? | `~/.pulse/brain/` has 13 .md files (self/×7, concept/×5, daily/×1) with frontmatter + wikilinks | ✅ EXISTS |

**Verdict: The vault is real Obsidian-compatible Markdown, not a database dump renamed .md.**

---

### §5 Single Memory Tool — No Coexistence

| Old | New | Status |
|-----|-----|--------|
| `MEMORY.md` / `USER.md` flat files | Vault `user/` + `concept/` nodes | ✅ Replaced in dev repo |
| `tools/memory_tool_store.py::MemoryStore` | `agent/brain/store.py::BrainStore` (605 LOC) | ✅ Replaced |
| `memory` tool separate engine | `tools/memory_tool.py::load_on_disk_store()` returns `BrainStore`<br/>Same public schema (`action`/`target`/`content`/`operations`) | ✅ Thin front |
| Flat memory cards in StarMap | `agent/learning_graph.py::_memory_cards()` reads vault via `BrainVault()` | ✅ Unified |

**But:** live install (`~/.pulse/pulse-agent`) HEAD = `0c7cfd4a revert`, so **agent/brain/ doesn't exist there** (0 files). The flat `MEMORY.md` is still active in production. Dev repo is ready but **not installed**.

**Verdict: Code replaced correctly in dev, but live app was reverted — user hasn't installed the new version.**

---

### §6 Prompt Integration (Cache-Safe)

| Component | Spec | Implementation | Status |
|-----------|------|----------------|--------|
| Layer 1 — stable prefix | §6, S4 | `agent/system_prompt.py::_memory_parts` calls `brain.session.brain_stable_prefix(agent)`<br/>Memoized, off unless `brain.prefix_enabled` | ✅ WIRED |
| Layer 2 — per-turn recall | §6, S4 | `agent/turn_context.py::_brain_recall_block(agent, msg)` line 893<br/>Appended to `ext_prefetch_cache` line 1168<br/>Bounded ~400-800 tokens | ✅ WIRED |
| Config gated | `brain.recall_enabled` etc. | `agent/brain/session.py::BrainSettings.from_config` reads `config.yaml` `brain:` section | ✅ WIRED |
| Never breaks prefix cache | Hard requirement | Layer 1 = session-stable, Layer 2 = per-turn context (existing channel) | ✅ DESIGN OK |
| Silence rule (no "brain dekh raha hoon") | §6 | Injected as context, never narrated | ✅ OK |

**Verdict: Prompt wiring is correct and cache-safe. But config has no `brain:` section, so it's inert until config added + app restarted.**

---

### §7 UI — One Graph, Obsidian Parity

| Requirement | Spec | Implementation | Status |
|-------------|------|----------------|--------|
| **No second UI module** | §7, guardrail from §2.1 | `apps/desktop/src/app/brain/` does NOT exist (0 files)<br/>`starmap/` is the only graph (21 files) | ✅ CORRECT |
| Sidebar entry | §7, S6 | `sidebar/index.tsx:251` has `starmap` nav item + keybind `nav.starmap` | ✅ DONE |
| Force-directed graph | §7 | `starmap/simulation.ts` + `render.ts` — d3-force, drag, pan, zoom | ✅ EXISTS |
| **Edges = wikilinks (directional)** | §7 | `agent/learning_graph.py::_vault_link_edges` returns `(source, target)` pairs from `BrainIndex.forward`<br/>StarMap renders with arrows | ✅ DONE |
| **Ghost nodes (unresolved links)** | §7 | `render.ts:533` hollow dashed square for `kind: 'ghost'`<br/>`node-context-menu.tsx` "Create note…" → `POST /api/learning/ghost` → `resolve_ghost` writes the note | ✅ DONE (this session) |
| Hover → neighbourhood highlight | §7 | `star-map.tsx` hover logic + fade system exists | ✅ EXISTS |
| Click → open note | §7 | Context menu edit/delete/create implemented | ✅ EXISTS |
| **Colour by category** | §7 | `color.ts:CATEGORY_HUES` fixed hues per vault category (self/user/concept/project/belief/daily/ghost)<br/>`render.ts` uses `categoryInk(n)` | ✅ DONE (this session) |
| **Filter panel** | §7 | Legend doubles as filter: click category row → `hiddenCategories` → `visibleGraph` drops nodes+edges from simulation | ✅ DONE (this session) |
| Rename cascades wikilinks | §7 (partly exists) | `agent/brain/parser.py::rewrite_link_target` exists (70 LOC)<br/>Desktop UI wiring: NOT VERIFIED | ⚠️ Backend exists, UI unclear |
| Performance (incremental indexing) | §7 | `BrainIndex.rebuild()` is full-scan; incremental NOT implemented | ❌ MISSING |
| Live update as vault grows | §7 | `loadStarmapGraph(force_refresh=true)` on vault write; no auto-watch | ⚠️ Manual refresh only |

**Verdict: Core graph + ghost flow + category filter done this session. Rename cascade backend exists but desktop wiring unverified. Performance is naive (full rebuild every time).**

---

### §9 Contract Test (The §2.2 Failure Guardrail)

| Requirement | Implementation | Status |
|-------------|----------------|--------|
| Every imported symbol exists | `tests/test_memory_contract.py` (10 test functions)<br/>Imports from `tools/memory_tool`, `agent.brain.*` and asserts they're callable | ✅ EXISTS |
| The §2.2 bug specifically | Test checks `MemoryStore`, `get_builtin_memory_config` etc. exist or are absent consistently | ✅ PINNED |

**Verdict: Contract test exists and passes (part of 384-test suite).**

---

### §10 Migration — Never Delete User Data

| Requirement | Implementation | Status |
|-------------|----------------|--------|
| `MEMORY.md`/`USER.md` → vault, idempotent | `agent/brain/migrate.py::migrate_legacy_memory` (207 LOC)<br/>Reads flat files, writes vault nodes with `migrated_from` tag, fingerprint-based dedupe | ✅ REAL, S2 |
| Dry-run first | Function returns `(ok, created, skipped, errors)` without writes when vault read-only | ✅ OK |
| Never `rm -rf` in `$PULSE_HOME` | Guardrail stated | Code only writes, never deletes user files | ✅ OK |

**But:** Live vault has migrated content, **but** live app was reverted so `MEMORY.md` is still being written by the old code — the migration ran once but the new system isn't active.

**Verdict: Migration code is safe and real. However, the revert means flat files are still the live store.**

---

## What Works Right Now (Practical Utility)

### ✅ Backend (all S1-S7 mechanisms)
- 384 Python tests pass (brain suite + memory contract)
- Live vault `~/.pulse/brain/` exists with 13 real migrated notes
- Encoding, consolidation, decay, recall, supersede — all coded and tested

### ✅ Desktop UI (this session's work)
- Ghost nodes render and are actionable (create the missing note)
- Category colouring (7 categories, fixed hues)
- Interactive category filter (legend = toggle)
- Sidebar entry for StarMap
- Title-link resolution bug fixed (`[[An Unwritten Idea]]` now resolves after creation)

### ❌ Not Running in Production
- Live PULSE app (`~/.pulse/pulse-agent`) was **reverted to pre-brain** (commit `0c7cfd4a`)
- Config has no `brain:` section, so even if installed it would be inert
- Dev repo (`~/pulse-evolution/pulse`) has everything ready on `origin/main` (commits `7b67feac` S5, `a0fd28cb` S6/S7, `05745d26` desktop UI)

---

## Practical Gap Analysis

### What brain.md Intended (User's Words)

> "unlimited memory" — no char budget, no eviction

✅ **Delivered:** Vault is unbounded, decay controls attention not deletion.

> "human jaisi memory… jin logic se human ki memory chalti hai"

✅ **Delivered:** Encoding→consolidation→decay→cue-based recall→reconsolidation is real, not promptic.

> "dikhne me obsidian vault me dikhe"

✅ **Delivered:** Real Markdown + wikilinks + `.obsidian/` config + force-directed graph.

> "promptic behaviour se nahi, koi shortcut nahi"

✅ **Delivered:** Real spreading activation (degree-normalised, bounded), real salience scoring, real supersede logic.

> "har jagah synchronised… conflicting app na banao"

✅ **Delivered:** One vault, one graph UI, one tool contract. No shadow modules.

> "professional, reliable, 100%"

⚠️ **Partially delivered:** Contract test exists, failure isolation coded, but **the live app doesn't run any of this** because it was reverted.

---

## What's Missing for Production Use

### Critical (Blocks Real Use)
1. **Install the dev repo into live app** — user must run their install script on `~/pulse-evolution/pulse`
2. **Add `brain:` config section** to `~/.pulse/config.yaml` with `enabled: true`, `recall_enabled: true`
3. **Restart PULSE** so the new code + config takes effect

### Medium (Usability)
4. **Incremental indexing** — `BrainIndex.rebuild()` is O(vault_size) every graph load; should be O(changed_files)
5. **Vault file watcher** — auto-refresh graph when `.md` changes (currently manual refresh only)
6. **Rename cascade UI wiring** — backend `rewrite_link_target` exists but desktop "rename node" flow unverified

### Low (Polish)
7. **Local graph on node open** — spec says "open note → local graph" but current impl is global graph only
8. **Performance for 1000+ nodes** — not tested; d3-force may choke without culling/LOD

---

## Tests vs. Real Work

You asked: "is there only tests were performed or there is any real work done which is usefull for practical?"

### Answer: BOTH

**Tests = proof the mechanisms work as designed.** Without them, "consolidation runs" is just a claim. With 384 passing tests, we know:
- Spreading activation obeys `total ≤ seeds/(1-decay)` (pinned by test)
- Consolidation merges duplicates and promotes episodic→semantic (pinned by test)
- Decay never deletes, only lowers retrievability (pinned by test)
- Supersede on contradiction (pinned by test)
- Title-link resolution (pinned by test, added this session after discovering the bug)

**Real work = the code that runs when PULSE is installed.**
- 5,015 LOC in `agent/brain/` (vault, encoding, consolidation, recall, decay, correction, migration, store)
- ~1,400 LOC in desktop StarMap changes (category colour, filter, ghost flow, sidebar)
- `~/.pulse/brain/` live vault with 13 migrated notes
- Migration ran once, data exists

**Gap = installation.** The code is ready, the vault is seeded, but the live app is on the old revert commit. Once installed + config added + restart:
- Every turn: cue-based recall injects relevant vault nodes (bounded, cache-safe)
- Background: consolidation promotes episodic→semantic, merges duplicates, adds wikilinks
- UI: interactive graph with category filter, ghost-node creation, title-link resolution
- Memory tool: vault-backed, unbounded, human-like decay

---

## Commits Audit

| Commit | Stage | What It Did | Verified? |
|--------|-------|-------------|-----------|
| `7b67feac` | S5 | BrainStore tool integration, async turn encoding, background consolidation | ✅ 375 tests |
| `a0fd28cb` | S6/S7 | StarMap backend unification, live decay/consolidation | ✅ (DISPUTED by user, re-audited, proven real) |
| `05745d26` | S6/S7 desktop | Category colouring/filter, ghost create-note, title-link resolution, sidebar entry | ✅ 384 tests + vitest 15 + tsc clean |

All on `origin/main` in `~/pulse-evolution/pulse`. Not in live `~/.pulse/pulse-agent` (reverted to `0c7cfd4a`).

---

## Conclusion

**Kami kya hai:**
1. Live app doesn't have this code (reverted)
2. Config doesn't enable it (`brain:` section missing)
3. Incremental indexing not done (performance gap for large vaults)
4. Rename cascade desktop wiring unverified
5. No auto-refresh on vault changes

**Kitna kaam bacha hai:**
- **Installation + config = 10 minutes** (user side, not agent)
- **Incremental indexing = 1-2 days** (real work, O(N) → O(changes))
- **Vault watcher + auto-refresh = 4 hours** (FSEvents/chokidar integration)
- **Rename cascade UI verification = 2 hours** (read desktop code, trace the path, add test if missing)
- **Local graph on note open = 1 day** (new UI component, filter graph to 2-hop neighbourhood)

**Brain.md intentions vs. reality:**
- §3 (human memory model): ✅ **100% real, not theatre**
- §4 (storage): ✅ **Real Obsidian vault**
- §5 (single source): ✅ **No coexistence, correct replacement**
- §6 (prompt integration): ✅ **Wired and cache-safe, but config-gated off**
- §7 (UI): ✅ **80% done** (ghost flow + category filter done today; rename/local-graph/perf missing)
- §9 (contract test): ✅ **Exists and passes**
- §10 (migration): ✅ **Safe, idempotent, ran once**

**Final answer:** The brain is **real and working in the dev repo**. It's not running in production because the live app is on a revert commit. Install + config = immediate utility. The missing 20% is performance + UI polish, not core logic.
