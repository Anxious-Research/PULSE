# Brain v3 Implementation Roadmap — 2026-10-08

## What's Done (Committed Today)

### ✅ Spec Complete: brain.md v3
- **§8 Visual Design**: Full specification for flagship neural graph (cosmic theme, glowing particles, animated spreading activation, 3D depth illusion, heatmap brightness)
- **§9 SQLite Cache**: Hybrid architecture design (Markdown authoritative + SQLite fast query layer, incremental sync, optional embeddings)
- **§11 Out-of-Box Defaults**: Brain active by default, no config required for .dmg users
- **§12 Resolved Decisions**: All v2 open questions answered

### ✅ Code: Out-of-Box Activation
- **`agent/brain/session.py`**: `prefix_enabled = True` (default changed from False)
- Users who download .dmg now get:
  - Brain active immediately
  - StarMap visible in sidebar
  - Encoding runs automatically
  - Layer 1 prefix (self/identity) appears in system prompt
  - **No config editing required**

### ✅ Tests Pass
- 384 Python tests OK (brain suite)
- 15 vitest starmap tests pass
- `tsc` clean

---

## What Needs Building

### P1: Visual Enhancements (§8 Implementation)

**Phase 1: Core Visual Language** (~3 days)
```typescript
// apps/desktop/src/app/starmap/visual-v3.ts
- Deep space gradient background (#0a0e27 → #000000)
- Bloom shader via CSS filter: drop-shadow + blur
- Z-layer depth: blur +2px for distant nodes
- Heatmap brightness: (access_count / max) × 0.6
```

**Phase 2: Animated Spreading Activation** (~2 days)
```typescript
// The showpiece — visualize recall in real-time
- Particle system: canvas circles travel bezier paths
- d3.transition on node brightness (activation score)
- Staggered timing: cue→1-hop: 0ms, 1-hop→2-hop: +400ms
- Gold pulse on cue nodes → spread → top-k stay bright
```

**Phase 3: Interactive Polish** (~2 days)
```typescript
- Content preview panel (slide from right, 320px)
- Pulse effect on new nodes (2s fade)
- Smooth spring physics tuning
- Double-click → open in editor
```

**Phase 4: Performance** (~1 day)
```typescript
- Viewport culling (render visible + 20% margin)
- LOD: distant nodes = simple circles
- Quadtree spatial index for 5000+ nodes
```

**Timeline:** ~8 days total (1 dev week + 1 day)

---

### P2: SQLite Cache Layer (§9 Implementation)

**Part A: Schema & Core** (~2 days)
```python
# agent/brain/cache.py
class BrainCache:
    def __init__(self, vault: BrainVault):
        self.db = vault.vault_dir / ".brain-cache.db"
        self._init_schema()  # nodes, edges, entities, nodes_fts tables
    
    def is_stale(self) -> bool:
        """Check if any .md mtime > cache mtime."""
    
    def rebuild(self):
        """Full rescan of vault (startup fallback)."""
```

**Part B: Incremental Sync** (~1 day)
```python
def sync_incremental(self):
    """O(changed_files), not O(vault_size)."""
    changed = [p for p in vault.rglob("*.md") 
               if p.stat().st_mtime > self.db.get_mtime(p)]
    for path in changed:
        node = vault.read_node(path)
        self.db.upsert_node(node)
        self.db.rebuild_edges_for(node)
```

**Part C: Fast Recall** (~1 day)
```python
def recall_fast(self, cues: List[str], limit=8) -> List[Node]:
    """SQL-powered spreading activation."""
    # FTS5 match → JOIN edges for 2-hop → rank → LIMIT
    return self.db.execute(RECALL_QUERY, cues, limit)
```

**Part D: Optional Embeddings** (~1 day)
```python
def store_embedding(self, node_id, vector):
    self.db.execute("UPDATE nodes SET embedding = ? WHERE node_id = ?", 
                    (vector.tobytes(), node_id))

def search_similar(self, query_vec, limit=5):
    # Cosine similarity in SQL (or numpy fallback)
```

**Timeline:** ~5 days

---

### P3: Config Cleanup (Holographic Removal)

**Current Problem:** 
- Your config still has `memory.provider: holographic`
- User's existing config will carry it forward
- New .dmg users won't have it, but upgrades will

**Solution Options:**

**Option A: Migration Script** (~2 hours)
```python
# agent/brain/migrate.py::migrate_config_v3
def migrate_config_v3(config_path: Path):
    """Remove memory.provider if it's holographic."""
    cfg = yaml.safe_load(config_path.read_text())
    if cfg.get("memory", {}).get("provider") == "holographic":
        cfg["memory"] = {}
        config_path.write_text(yaml.dump(cfg))
```

**Option B: Runtime Warning** (immediate)
```python
# agent/agent_init.py
if _mem_provider_name == "holographic":
    logger.warning(
        "holographic memory provider is deprecated; brain is now built-in. "
        "Remove 'memory.provider: holographic' from config.yaml"
    )
```

**Option C: Auto-disable in v3** (immediate)
```python
# Force brain when holographic detected
if _mem_provider_name == "holographic":
    logger.info("brain v3: ignoring holographic, using built-in brain")
    _mem_provider_name = ""  # Force core memory
```

**Recommendation:** Option C (immediate, no user action) + Option B (warn) + Option A (clean up file on next save).

---

## Your Question: "can we do this type of system"

**Answer: YES — and most of it exists already.**

| Your Vision | Status |
|-------------|--------|
| Real autonomous PULSE entity | ✅ EXISTS (encoding/consolidation/decay/recall/reconsolidation) |
| Human-like memory | ✅ EXISTS (spreading activation, forgetting curve, spacing effect) |
| SQLite for fast responses | 🔨 **SPEC DONE** (§9), implementation = 5 days |
| Learning & growing like human | ✅ EXISTS (episodic→semantic promotion, wikilinks auto-added) |
| Live Obsidian-identical graph | ✅ EXISTS (StarMap, force-directed, drag/zoom, editable) |
| Ultra attractive neurons view | 🔨 **SPEC DONE** (§8), implementation = 8 days |
| Actual interaction with neurons | ✅ PARTIAL (hover/click/edit works; animated activation = 8 days) |

**Total implementation time for your full vision:**
- **P1 Visual (§8)**: 8 days
- **P2 Cache (§9)**: 5 days
- **P3 Config cleanup**: 2 hours
- **Total**: ~13 days (2 weeks)

---

## What Works Right Now (After You Install)

1. **Edit your config** to activate (or wait for next release where it's automatic):
   ```yaml
   memory: {}
   brain:
     enabled: true
     prefix_enabled: true
   ```

2. **Restart PULSE**

3. **What you'll see immediately:**
   - StarMap graph in sidebar
   - Vault growing in `~/.pulse/brain/`
   - Per-turn recall (relevant memories injected)
   - Category-colored nodes (7 hues)
   - Hover highlights neighbourhood
   - Click → context menu (edit/delete)
   - Ghost nodes (unresolved `[[wikilinks]]`)

4. **What you WON'T see yet** (needs P1+P2 implementation):
   - Animated spreading activation (particles flowing)
   - Deep space cosmic theme
   - Bloom glow effects
   - 3D depth illusion
   - Heatmap brightness encoding
   - Fast SQLite recall (currently in-memory rebuild)

---

## Next Steps (Your Choice)

**Path A: Use It Now (Functional but Not Flagship)**
1. I fix your config (remove holographic, enable brain)
2. You restart PULSE
3. Brain works, graph visible, memories accumulate
4. Visual polish comes in ~2 weeks

**Path B: Wait for Full Flagship (v3 Complete)**
1. I implement P1 (visual) + P2 (cache) over 2 weeks
2. You install when done
3. Get the full cosmic neural graph experience immediately

**Path C: Incremental (Recommended)**
1. Fix config NOW → brain works today
2. I build P1 Phase 1-2 (core visual + animation) → ~5 days
3. You see the flagship graph evolving
4. P1 Phase 3-4 + P2 come in next sprint

**Which path?**

---

## Commits Ready to Install

```bash
git log --oneline -6
# 5ae1b3e0 feat(brain): v3 spec — flagship visual design, SQLite cache, out-of-box defaults
# 05745d26 feat(brain): S6-S7 desktop — category colouring/filter, ghost create-note flow, title-link resolution
# a0fd28cb feat(brain): S6-S7 — StarMap graph backend unification and live decay/consolidation integration
# 7b67feac feat(brain): S5 — BrainStore tool integration, async turn encoding & background consolidation
# 74733801 feat(brain): S5 — encoding, contradiction superseding, and consolidation
# 881cd4c8 feat(brain): S4 — prompt integration, flag-gated
```

All of these are **functional code** — the brain works, the graph exists, the cognitive model is real.

The only gap: your config still has `holographic`, so it's not activating.

---

**Ready for your decision on next steps.**
