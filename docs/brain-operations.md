# PULSE Brain — operations: backup, restore, recovery, migration

Companion to `docs/brain-evolution.md`. That document explains *what the Brain is*. This one
explains *how to keep it safe and how to get it back*, and it distinguishes what has been verified
from what has not.

**Legend:** `Verified` · `NOT VERIFIED`.

---

## 1. What is canonical, and what is disposable

The single most important operational fact:

| Path | Role | If lost |
|---|---|---|
| `$PULSE_HOME/brain/**/*.md` | **Canonical.** Every memory, belief and procedure is a Markdown file with YAML frontmatter. | **Real loss.** This is the only thing worth backing up. |
| `$PULSE_HOME/brain/.index/*` (link/ID index) | Derived cache | Rebuilt automatically. No loss. |
| `$PULSE_HOME/state.db` (+ `-wal`, `-shm`) | Derived: sessions, FTS5 search index, task state | Rebuilt from the vault and the session store. Chat history is worth keeping, but a lost index costs rebuild time only. |
| `$PULSE_HOME/sessions/*` | Session transcripts | Archive, not memory. Losing it does not lose a memory. |

A second Brain instance pointed at the same vault directory sees the same memories, beliefs and
procedures, because there is no second store to fall out of sync (`brain-evolution.md` D1).

---

## 2. Backup

Because the vault is plain files, a backup is a copy — there is no export step, no dump command, and
no special tool required.

```bash
# Whole memory, one archive. ~2 KB per node before compression.
tar -czf pulse-brain-$(date +%Y%m%d).tar.gz -C "$PULSE_HOME" brain
```

Two properties make this safe to run at any time:

* **A memory is completed before it is visible.** Each node is written as one file; a node is never
  half-written in place. Anarchic mid-run copies therefore cannot produce a torn memory — at worst
  they miss a turn that had not been written yet.
* **Nothing is stored outside the vault.** There is no accompanying database that must be copied in
  lockstep for the memory to make sense.

What is *not* worth backing up: `.index/`, `state.db*`, `__pycache__`. They are rebuilt from the
vault, and backing them up only creates the risk of restoring a stale derived state over a newer
canonical one.

---

## 3. Restore

```bash
tar -xzf pulse-brain-20261010.tar.gz -C "$PULSE_HOME"
```

Then let the derived layers rebuild by using PULSE normally (or delete `.index/` first to force a
clean rebuild). No import or migration step is required: the restored files are the state.

### Verified

`~/.pulse/cache/scratch/verify_backup_restore.py` performs the real round trip against real vaults
inside isolated `PULSE_HOME` directories (never the live one). Result on this build:

```
A. source vault: nodes=3 beliefs=1 procedures=1
   backup archive: brain.tar.gz (1870 bytes)
B. restored into a fresh PULSE_HOME: nodes=3 beliefs=1 procedures=1
   OK  : restore reproduces the source exactly
C. after deleting derived files: nodes=3 beliefs=1 procedures=1
   OK  : the Markdown is the source of truth; derived files rebuild
D. legacy node reads back: True

RESULT: ALL VERIFIED
```

---

## 4. Recovery

| Symptom | Cause | Action |
|---|---|---|
| Recall returns nothing, vault files are present | Index is stale or missing | Delete `$PULSE_HOME/brain/.index/`; it rebuilds on next use. |
| Graph UI shows fewer nodes than the vault has | Graph reads the derived index | Same as above — rebuild, then reload. |
| A belief looks wrong | Beliefs are evidence-weighted, not authoritative | Record counter-evidence (it moves the confidence down and is preserved); do not hand-edit unless you intend to edit the record itself. |
| A memory is outdated | Supersession keeps history by design | Write the corrected fact. The old node is marked superseded, **not deleted**, so its history survives (`Zep/Graphiti`-style: the prior assertion is kept). |
| A procedure gives bad advice | It is judged by recorded outcomes | Record the failing outcome. `status` degrades toward `avoid`; the procedure is still surfaced, because knowing a method fails is itself useful. |

**Nothing in the belief, procedure or reflection layers deletes a memory.** Contradiction lowers
confidence; supersession adds a pointer. A wrong belief can therefore always be recovered by looking
at `evidence_refs`, `counter_evidence` and `revisions` on the node itself.

### Rollback levers

If learning misbehaves, learning can be stopped without touching memory:

| Setting | Effect |
|---|---|
| `brain.reflect_enabled: false` | Reflection stops forming/updating beliefs. Encoding and recall keep working. |
| `brain.reflect_every: N` | Cadence of the background reflection pass (default 6, clamped 1–1000). |
| `brain.encode_*` | Write-path gate. Turning encoding off makes PULSE read-only for memory. |

These are ordinary config values — no migration, no schema change, and reverting them restores the
prior behaviour exactly, because nothing was rewritten in place.

---

## 5. Migration

### 5.1 Legacy flat memory → the vault (`Covered by unit tests`)

Old PULSE stored memory as `§`-delimited plain text in `$PULSE_HOME/memories/MEMORY.md` and
`USER.md`. `agent/brain/migrate.py` imports those into vault nodes:

| Legacy file | Target category |
|---|---|
| `memories/MEMORY.md` | `concept/` (or `project/`) |
| `memories/USER.md` | `user/` |

The rules it obeys are the reason a previous attempt at this destroyed the user's memory, and they
are enforced in code:

* **Additive only. Nothing is ever deleted.** The legacy files are left in place.
* **Idempotent.** Each imported entry stores a `source_fingerprint`; a second run imports nothing.
  Re-running after new legacy entries appear imports only the new ones.
* **Dry run by default.** `migrate_legacy_memory(...)` defaults to `dry_run=True`.
* **Never overwrites.** If a target node exists with different content it is skipped and reported.

```python
from agent.brain.migrate import needs_migration, plan_migration, migrate_legacy_memory

needs_migration()                      # is there anything to import?
plan_migration()                       # what exactly would be created / skipped
migrate_legacy_memory()                # dry run (default) — writes nothing
migrate_legacy_memory(dry_run=False)   # apply
```

### 5.2 Version-to-version: no migration required

The belief/procedure work was designed so that **an existing vault needs no migration**:

* Every new frontmatter field is additive and optional. A node written before this work simply has
  no `evidence_refs` / `outcomes` until new evidence produces them.
* Unknown relation types coerce to `related_to`, and unknown categories coerce to `concept`, so a
  vault written by a newer build does not break an older one (and vice versa).
* The new `procedure/` directory is created idempotently by `ensure_vault_structure()`.

**Verified:** a node written with none of the new fields reads back correctly
(`verify_backup_restore.py`, check D).

### 5.3 Rolling back to an older PULSE

Because nothing was rewritten, an older build reads a newer vault, ignoring the fields it does not
understand. Belief and procedure nodes will not *appear* in an older version's UI, but they are
still there and are re-read by a newer build. No data is lost by downgrading.

**NOT VERIFIED:** a full downgrade run against the built desktop app. The compatibility behaviour is
reasoned from the coercion rules in `models.py`/`relations.py` and tested at the parser level, not
exercised end-to-end.

---

## 6. Operations at scale — measured

`~/.pulse/cache/scratch/bench_learning.py` builds vaults of a given size in an isolated
`PULSE_HOME` and measures the production functions. Real numbers, not projections:

| Nodes | build | index rebuild | recall (warm, avg) | recall (cold) | encode_turn (avg of 5) | reflect | procedures_for_goal | RSS |
|---|---|---|---|---|---|---|---|---|
| 500 | 171 ms | 34 ms | 57 ms | 90 ms | 153 ms | 219 ms | 32 ms | 37 MB |
| 1,500 | 487 ms | 100 ms | 172 ms | 261 ms | 481 ms | 526 ms | 97 ms | 60 MB |
| 3,000 | 1,059 ms | 206 ms | 348 ms | 535 ms | 943 ms | 1,023 ms | 198 ms | 90 MB |
| 5,000 | 1,867 ms | 339 ms | 578 ms | 929 ms | 1,605 ms | 1,638 ms | 328 ms | 127 MB |

All figures are post-G16 (the duplicated scan removed — see §7). `build` is the one-off cost of
writing the vault; `index rebuild` happens once at startup; the rest are steady-state per-operation
costs on a populated vault. `beliefs=1` in every run, i.e. the learning layers themselves do not
change the shape of the curve.

**The honest reading of this table.** Every stage grows **linearly** with vault size, and nothing
degrades sharply — the 10× growth from 500 to 5,000 nodes costs ~10× time, which means the
algorithms are O(N) with no hidden quadratic blowup. The consequence at scale is that the per-turn
path is linear:

* **Recall reads are acceptable.** 585 ms for a 5,000-node vault, in the background, is not a user
  visible problem. The live vault is 426 nodes, where recall is ~50 ms.
* **Writes get slow.** `encode_turn` is linear because it compares the new statement against the
  existing corpus twice (novelty/recurrence, then the restatement check). At 5,000 nodes that is
  ~1.6 s, and it runs in the background worker — so it does not block a reply, but it does mean a
  large vault catches up slowly. Memory is not lost, only delayed.

The fix for this is known and deliberately not attempted here: use the existing FTS5 cache to
pre-filter the comparison set, which would turn the linear scan into a lookup. It was left out
because it changes IDF/ranking, and the mandate is explicit that a ranking change must not be made
casually (`brain-evolution.md` §6.4).

---

## 7. Performance regression found and removed in this work

Adding the restatement check created an O(N) similarity scan in `extract_candidates`. But a
second, pre-existing near-duplicate scan already ran further down `encode_turn` — over the same
nodes, at the same 0.85 threshold, with only an extra category restriction, i.e. a strict subset of
what the new check covers. Every surviving candidate was therefore scanned **twice**.

The duplicate block was dead code and was removed, and a cheap length prefilter was added in front of
the remaining scan: since a Jaccard score above 0.85 implies the two token counts are within ~18% of
each other, a memory of a wildly different length cannot be a restatement and need not be tokenized
at all.

Measured `encode_turn` at 3,000 nodes: **1,359 ms → 953 ms** for a single call, and
**1,776 ms → 943 ms** averaged over the five writes in the scale benchmark (the spread comes from
where in the vault the statement lands). At 5,000 nodes the same fix takes it from **2,856 ms →
1,605 ms**. Verbatim-repeat and paraphrase corroboration were both re-verified after the change, and
the node count still stays flat.
