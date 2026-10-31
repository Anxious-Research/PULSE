# PULSE Memory Architecture — Comprehensive Audit & Overhaul Plan

**Date:** October 30, 2026  
**Auditor:** PULSE Agent (Pair Programming with Creator Ankit Singh)  
**Status:** AUDIT COMPLETE — ACTION PLAN READY  

---

## Executive Summary

A comprehensive architectural and disk-level inspection of PULSE's memory systems revealed severe structural conflicts, broken command surfaces, dead subsystems, and a critical self-poisoning feedback loop. 

Instead of a unified, human-like associative memory, the codebase currently suffers from:
1. **A split-brain coexistence** between the legacy flat store (`MEMORY.md` / `USER.md` with character budgets) and the new Brain Vault (`~/.pulse/brain/`).
2. **Review fork prompt ingestion**, where the background learning model saves its own meta-instructions into the user profile, causing exponential memory pollution and infinite evidence loops.
3. **Triple-injection redundancy**, where the same facts are duplicated across the System Prompt Prefix, Cue Recall, and an unconditional User Model dump.
4. **Degenerate procedural memory**, where arbitrary conversation lines (and review prompts) are classified as "procedures" with trivial single-step actions (`"used memory"` / `"used terminal"`).
5. **Disconnected & dead features**, including an unused SQLite FTS5/graph cache (`cache.py`), an un-wired BM25/RRF hybrid retrieval engine (`hybrid.py`), and completely empty episodic (`daily/`), semantic (`concept/`), belief (`belief/`), and intention (`prospect/`) stores.
6. **Primitive heuristic algorithms**, including 256-bucket blake2b hash projections pretending to be embeddings, 5-letter prefix truncation pretending to be stemming, and regex negation lists failing on multilingual/Hinglish text.

---

## Detailed Audit Findings

### 1. Split-Brain Architecture (Old Flat Store vs Brain Vault)
* **Code Conflict:** `tools/memory_tool_store.py` (544 lines) implements flat file storage with char budgets (`memory_char_limit: 2200`, `user_char_limit: 1375`) and `§` delimiters. `agent/brain/store.py` (`BrainStore`) wraps `BrainVault` to look like `MemoryStore`, but ignores char limits.
* **Broken CLI Reset:** `pulse memory reset` (`pulse_cli/main_agent_cmds.py:22`) only inspects and deletes `~/.pulse/memories/MEMORY.md` and `USER.md`. In reality, `~/.pulse/memories/` is completely empty (`total 0`), while real memory lives in `~/.pulse/brain/`. Running `pulse memory reset` reports *"Nothing to reset"* and leaves the vault untouched.
* **Blind Web Dashboard:** `/api/memory` and `/api/memory/reset` (`pulse_cli/web_routers/ops.py:481, 519`) only check flat files in `~/.pulse/memories/`. The dashboard shows 0 bytes and cannot view or reset the actual brain vault.
* **Prompt Disconnect:** The system prompt instructs the agent: *"IF FULL: an add is rejected with the current entries shown... it has a hard character budget"*, teaching the agent a constraint that no longer exists in `BrainStore`.

### 2. Self-Poisoning Feedback Loop in Background Review
* **The Mechanism:** `agent/turn_finalizer.py` calls `_spawn_background_review` after turns, which runs an LLM fork with `_MEMORY_REVIEW_PROMPT` (`agent/background_review.py`).
* **The Bug:** The review prompt contains instructions like:
  * *"TWO distinct stores — pick the right one for each fact: USER.md..."*
  * *"Target shape of the library: CLASS-LEVEL skills..."*
  * *"If a tool failed because of setup state, capture the FIX..."*
  * *"Either say 'Nothing to save'..."*
  * *"Stop doing X / Don't format like this..."*
  The review LLM hallucinated that these instructions were facts about the user and executed `memory(action="add", target="user", ...)` for them.
* **Disk Evidence in `~/.pulse/brain/user/`:**
  * `a-user-preference-lesson-lives-in-exactly-one-place-the-ski.md` (7.1 KB)
  * `always-on-rules-standing-user-preferences-gates-that-app.md` (6.9 KB)
  * `either-say-nothing-to-save-or-only-if-you-are-independen.md` (6.6 KB)
  * `if-a-tool-failed-because-of-setup-state-capture-the-fix-in.md` (7.0 KB)
  * `stop-doing-x-don-t-format-like-this-i-hate-when-you-y.md` (6.9 KB)
  * `target-shape-of-the-skill-library-class-level-skills-with-a.md` (6.9 KB)
* **Infinite Evidence Reference Loop:** In `a-user-preference-....md`, `evidence_refs` contains the exact same turn ID (`20261029195908091780`) appended **470 times**, blowing the file up to 268 lines.
* **Permanent Poisoning via Reconsolidation:** Because these files contain generic words ("rule", "user", "preference", "memory"), they match almost every cue recall query. Every turn increments `access_count` (now at 277 accesses), inflating `stability` to `3650.0` (10 years!). They will never decay naturally.

### 3. Triple-Injection Redundancy (Prompt Token Waste)
On every turn, identical memory facts are injected three separate times into context:
1. **Layer 1 (System Prompt Prefix):** `agent/system_prompt.py:531` -> `brain_stable_prefix` loads every node from `self/` and `user/` into the static prompt.
2. **Layer 2 (Cue Recall):** `agent/brain/session.py:384` -> `brain_turn_context` runs lexical cue matching and injects hits into `<memory-context>` under `Relevant memories:`.
3. **Layer 3 (Unconditional User Model Snapshot):** `agent/brain/session.py:355` -> `_user_model_guidance_block`. Although the docstring states it only injects when the query has user-model signal, the code completely ignores `query` and unconditionally dumps the entire User Model snapshot under `User model (current evidence-backed state):`.

### 4. Degenerate Procedural Memory (`outcome_recorder.py`)
* `agent/brain/outcome_recorder.py` contains `TASK_GOAL_KEYWORDS` with 50 common verbs (`check`, `read`, `fix`, `make`, `run`, `review`, `open`, etc.).
* If any message contains one of these words and any action tool ran, it creates a `Procedure` node with the raw message as the goal.
* **Result in `~/.pulse/brain/procedure/`:**
  * `Review the full conversation above, including the assistant's completed answer and observed tool outcomes...` -> Goal: review instructions, Step: `1. used memory`, Record: `11 success, 0 failure`. The background reviewer's prompt became a learned procedure!
  * `push that to main` -> Step: `1. used terminal`.
  * `teesre point ko fix karo...` -> Step: `1 partial`.
* These degenerate procedures are then injected into future turns as guidance.

### 5. Dead Code & Disconnected Subsystems
* **SQLite Cache (`agent/brain/cache.py`):** Implements `.brain-cache.db`, FTS5 full-text search, and SQL-side graph spreading activation. **It is never called by `recall.py`**. `recall.py` still reads markdown files and walks in-memory Python structures. `cache.py` is only used by desktop graph UI and unit tests.
* **Hybrid BM25 + RRF (`agent/brain/hybrid.py`):** Shipped with Okapi BM25 and Reciprocal Rank Fusion, advertised in docs as the core retrieval engine. In reality, `recall.py` only calls it in `_rerank_hybrid` if top associative scores are within `TIE_EPS = 0.02` (almost never). The primary gate remains naive cue token overlap.
* **Episodic Memory & Consolidation (`daily/`):** `~/.pulse/brain/daily/` has 0 files. `encoding.py` sets a 0.40 salience threshold that raw conversational turns never meet unless they have explicit outcomes. Because `daily/` is empty, `consolidate.py` has no episodic data to cluster or promote into concepts.
* **Beliefs, Concepts, Projects, Prospects:** `~/.pulse/brain/belief/`, `concept/`, `project/`, and `prospect/` all contain 0 files. The graph is currently empty except for corrupted `user/` and degenerate `procedure/` nodes.

### 6. Primitive / Non-Professional Algorithmic Shortcuts
* **Blake2b Random Projection Embedder (`similarity.py`):** `LexicalHashEmbedder` hashes tokens into 256 buckets with a sign bit. It has zero semantic comprehension: synonyms, rephrasings, and cross-language translations produce near-zero cosine similarity.
* **5-Letter Prefix Slicing (`recall.py`):** Instead of a stemmer or lemmatizer, `_key(token)` truncates tokens to 5 characters (`term[:5]`). Unrelated words collide ("forget" / "forgery"), while plurals and conjugations in non-English or Hinglish break.
* **Regex Contradiction Detection (`correction.py`):** Relies on hardcoded lists of English and 7 Hinglish words (`nahi`, `mat`, `band`, `galat`). Number differences in sentences ("took 20 minutes" vs "limit is 10") trigger false-positive contradictions if topics overlap.

---

## Master Remediation Roadmap

```
┌────────────────────────────────────────────────────────────────────────┐
│                        PHASE 1: VAULT SANITIZATION                     │
│  - Purge hallucinated prompt files from ~/.pulse/brain/user/           │
│  - Purge degenerate procedures from ~/.pulse/brain/procedure/         │
│  - Reset stability and access counters on legitimate facts             │
│  - Fix evidence_refs array deduplication in user_model.py              │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                  PHASE 2: BACKGROUND REVIEW LOOP DEFENSE               │
│  - Add prompt-leak filter in review_fork (block review instructions)   │
│  - Sanitize input history fed to review LLM                            │
│  - Validate memory additions against meta-prompt stop-phrases          │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                  PHASE 3: UNIFY STORAGE & FIX INTERFACES               │
│  - Wire `pulse memory reset` directly to BrainVault                   │
│  - Wire `/api/memory` and `/api/memory/reset` to BrainVault            │
│  - Update tool description & prompt to match true unbounded vault      │
│  - Deprecate flat memory store files permanently                       │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│                 PHASE 4: RESOLVE PROMPT TRIPLE-INJECTION               │
│  - Make User Model Snapshot conditional on query relevance             │
│  - Deduplicate Layer 1 (System Prompt) vs Layer 2 (Recall)             │
│  - Filter out procedures that are trivial tool echoes                  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│              PHASE 5: WIRE SQLITE CACHE & BM25 HYBRID RETRIEVAL        │
│  - Make `BrainCache` (FTS5 + SQL join) the primary index in recall.py │
│  - Wire BM25 + Vector + Graph RRF fusion as primary ranking            │
│  - Integrate local ONNX / FastEmbed semantic model for true vectors    │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │
┌───────────────────────────────────▼────────────────────────────────────┐
│            PHASE 6: REAL PROCEDURAL & EPISODIC CONSOLIDATION           │
│  - Lower episodic threshold to capture genuine daily logs              │
│  - Require multi-step tool verification for procedures                 │
│  - Enable scheduled background consolidation pass                      │
└────────────────────────────────────────────────────────────────────────┘
```

---

## Detailed Execution Checklist

- [ ] **Task 1: Clean & Deduplicate Vault Disk State**
  - Delete `~/.pulse/brain/user/a-user-preference-lesson-lives-in-exactly-one-place-the-ski.md`
  - Delete `~/.pulse/brain/user/always-on-rules-standing-user-preferences-gates-that-app.md`
  - Delete `~/.pulse/brain/user/either-say-nothing-to-save-or-only-if-you-are-independen.md`
  - Delete `~/.pulse/brain/user/if-a-tool-failed-because-of-setup-state-capture-the-fix-in.md`
  - Delete `~/.pulse/brain/user/stop-doing-x-don-t-format-like-this-i-hate-when-you-y.md`
  - Delete `~/.pulse/brain/user/target-shape-of-the-skill-library-class-level-skills-with-a.md`
  - Delete `~/.pulse/brain/user/three-kinds-references-topic-md-for-topical-depth-deci.md`
  - Delete `~/.pulse/brain/user/not-a-duplicate-of-what-the-environment-already-teaches-r.md`
  - Delete `~/.pulse/brain/user/one-fact-goes-to-one-store-never-both-writing-it-to-both.md`
  - Delete `~/.pulse/brain/user/references-is-for-depth-that-is-only-needed-sometimes-a-de.md`
  - Delete `~/.pulse/brain/procedure/review-the-full-conversation-above-including-the-assistant-s-completed-answer-a.md`
  - Retain valid facts: `name.md`, `language.md`, `my-name-is-ankit-singh-and-i.md`, `user-is-the-creator-maker-of-pulse-agent.md`, `user-communicates-in-hindi-hinglish.md`.
  - Fix `agent/brain/user_model.py`: ensure `evidence_refs` uses a strict set and does not append duplicates.

- [ ] **Task 2: Review Fork Anti-Self-Ingestion Guard**
  - In `agent/background_review.py`:
    - Add regex / content filters for `_MEMORY_ROUTING_BLOCK`, `_LESSON_LAYER_BLOCK`, and `_DO_NOT_CAPTURE_BLOCK`.
    - If the review model attempts to call `memory(action="add", ...)` with text originating from its own prompt, reject it immediately.
    - Prevent the review fork's own execution turns from ever being recorded by `outcome_recorder.py`.

- [ ] **Task 3: Unify CLI & Dashboard Memory Operations with BrainVault**
  - Update `pulse_cli/main_agent_cmds.py`: `_cmd_memory_reset` should clear `~/.pulse/brain/` categories (`concept`, `procedure`, `daily`, etc.) with user confirmation, preserving core identity.
  - Update `pulse_cli/web_routers/ops.py`: `/api/memory` should return vault node counts and categories; `/api/memory/reset` should wipe the selected vault categories.
  - Update `tools/memory_tool.py`: update tool documentation to remove outdated character budget warnings.

- [ ] **Task 4: Fix Injection Pipeline & Deduplication**
  - In `agent/brain/session.py`:
    - Modify `_user_model_guidance_block` to actually inspect `query` and inject only when relevant (or only facts not already in Layer 1 prefix).
    - Ensure nodes in Layer 1 (`self/` and `user/`) are suppressed from Layer 2 recall if they are already present in the stable prefix.
  - In `agent/brain/outcome_recorder.py`:
    - Disallow single-step procedures (e.g. `"used terminal"`, `"used memory"`).
    - Require verification before creating procedure nodes.

- [ ] **Task 5: Connect SQLite Cache & Hybrid BM25 Retrieval**
  - Update `agent/brain/recall.py`:
    - Use `BrainCache` to accelerate candidate fetching.
    - Wire `agent/brain/hybrid.py` RRF fusion directly into candidate scoring so BM25 and vector scores participate natively in every recall.
