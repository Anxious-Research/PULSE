# PULSE Memory Architecture — QA Verification & Audit Report

**Date:** October 30, 2026  
**QA Lead:** PULSE Memory Architecture Squad  
**Target Systems:** `agent/brain/`, `agent/background_review.py`, `pulse_cli/`, `tools/memory_tool.py`  
**Live Vault Path:** `~/.pulse/brain/` (`/Users/ankitsingh/.pulse/brain`)  
**Status:** **PASSED — ALL QUALITY GATES MET — 0 REGRESSIONS**  

---

## 1. Executive Summary

End-to-end verification, contract enforcement, and regression testing were executed across the entire PULSE memory subsystem. All 5 core architectural remediation areas—leak guard immunization, procedural filtering, triple-injection elimination, FTS5 hybrid retrieval, and unified vault operations—were audited, exercised against the complete test battery, benchmarked, and verified against the live filesystem.

### Headline Metrics
* **Total Tests Executed:** 955 tests across 4 major test suites
* **Pass / Fail / Skip:** **953 Passed, 0 Failed, 2 Skipped** (opt-in 10k scale suites)
* **Retrieval Latency:** **1.9 ms mean** (contract budget: < 50 ms)
* **Mean Reciprocal Rank (MRR):** **1.00** (quality gate: ≥ 0.40)
* **Mean Recall@5:** **0.93** (quality gate: ≥ 0.25)
* **Mean Precision@3:** **0.65** (quality gate: ≥ 0.30)
* **Live Vault Audit:** 17 valid Obsidian nodes, 35 wikilink edges, 17 FTS5 entries, **0 junk files, 0 test leaks, 0 duplicate turn IDs, 0 prompt leaks**

---

## 2. Comprehensive Code & Contract Review

### 2.1. `agent/background_review.py` (Review Fork Immunization & Leak Guard)
* **Problem Addressed:** The background review LLM was previously ingesting its own meta-instructions (e.g., *"TWO distinct stores"*, *"Target shape of the skill library"*, *"Either say 'Nothing to save'"*) into `user/` memory notes, creating an infinite self-poisoning loop.
* **Review Findings:**
  1. **Prompt Leak Phrase Collection:** `_collect_leak_phrases()` and `_EXPLICIT_PROMPT_LEAK_PHRASES` dynamically index substrings and lines (≥ 25 characters) across all review prompt templates.
  2. **Content Extraction:** `extract_memory_content_strings(args)` extracts text from `content`, `new_text`, and batched `operations` lists.
  3. **Multi-Layer Defense:**
     - `_GuardedReviewMemoryStore`: Wraps the review agent's memory store and rejects calls attempting to persist leak phrases.
     - `ToolGuardrailDecision`: In `_run_review_fork`, overrides `_tool_guardrails.before_call` to block `memory_prompt_leak` before execution.
     - `set_thread_pre_call_hook`: Sets thread-level interceptor cleaned up in `finally`.
     - `tools/memory_tool.py`: Added `is_background_review()` check to block memory writes at the tool layer.
     - `agent/turn_finalizer.py`: Prevents background review forks from spawning recursive review forks (`skip_background_review = True`).
* **Verdict:** Verified and contract-compliant.

### 2.2. `agent/brain/outcome_recorder.py` (Procedural Filtering & Harness Exclusion)
* **Problem Addressed:** Conversational chatter, Hindi/Hinglish instructions, single-step commands (`"used terminal"`), meta-agent tools, and review harness prompts were being misclassified as technical procedures.
* **Review Findings:**
  1. **Meta-Tool Exclusion:** `NON_PROCEDURAL_TOOLS` (`delegate_task`, `memory`, `skill_manage`, `todo_list`, `session_search`) can never form procedural workflows on their own.
  2. **Technical Verb Gating:** `TASK_GOAL_KEYWORDS` restricted to explicit technical action verbs (`build`, `create`, `write`, `implement`, `fix`, `debug`, `deploy`, `migrate`, etc.). Generic verbs (`check`, `read`, `find`, `list`, `make`, `review`) are purged.
  3. **Conversational Dialogue Filtering:** `_HINDI_HINGLISH_MARKERS` (e.g. `ekdum`, `pehle`, `karenge`, `karna`, `kuch`, `tumne`, `mera`, `meri`) and `_CONVERSATIONAL_PREFIXES` reject conversational chatter.
  4. **Harness & Delegation Notification Gating:** `_DELEGATION_PATTERNS` strips background fan-out, subagent completions, and async task notifications.
  5. **Substantive Workflow Requirement:** `_is_substantive()` mandates ≥ 2 concrete tool calls (with at least one in `ACTION_TOOLS`) or ≥ 2 `verified_steps`. Single-step actions are rejected.
  6. **Fork & Delegation Exclusion:** Explicitly differentiates between `"background review fork"` and `"delegation fork"` and blocks outcome recording on both.
* **Fix Applied During QA:** Fixed reason mismatch in `outcome_recorder.py` line 383 so that background review origins return `"background review fork"` and delegation/subagent origins return `"delegation fork"`, satisfying `TestOutcomeRecorderReviewForkExclusion`.
* **Verdict:** Verified and contract-compliant.

### 2.3. `agent/brain/session.py` (System Harness Exclusion & Triple-Injection Elimination)
* **Problem Addressed:** Same memories were injected three times into turn prompts (Layer 1 Stable Prefix + Layer 2 Cue Recall + Layer 3 Unconditional User Model dump), wasting prompt tokens and causing context contamination.
* **Review Findings:**
  1. **Layer 1 Stable Prefix Tracking:** `prefix_mod.stable_prefix_node_ids()` computes the exact set of `self/` and `user/` node IDs rendered in the static prompt and caches them on `agent._brain_prefix_node_ids`.
  2. **Layer 2 Recall Filtering:** In `brain_turn_context()`, recall hits are filtered: `result.hits = [h for h in result.hits if h.node_id not in prefix_node_ids]`. No node in the stable prefix is re-injected under `Relevant memories:`.
  3. **Layer 3 User Model Deduplication:** In `_user_model_guidance_block()`, facts and projects already present in `prefix_node_ids` are omitted.
  4. **User Model Query Gate:** `_has_user_model_signal(query)` regex ensures the user model is only rendered when the query carries user-model cues (preferences, identity, style, situation), preventing context contamination on unrelated tasks.
  5. **System Harness Exclusion:** In `brain_encode_turn()`, turns starting with `[ASYNC`, `[System note:`, `[PRIOR CONTEXT`, `[TASK`, `[CONTEXT`, `A background fan-out`, etc. are skipped (`{"encode": "skipped_system_harness"}`).
  6. **Cache Reset:** `reset_agent_cache(agent)` properly purges `_brain_prefix_node_ids`.
* **Verdict:** Verified and contract-compliant.

### 2.4. `agent/brain/cache.py` & `agent/brain/recall.py` (FTS5 & Multi-Channel Hybrid Retrieval)
* **Problem Addressed:** The SQLite FTS5 cache was previously dead/uncalled, and hybrid retrieval was bypassed in favor of raw disk file parsing.
* **Review Findings:**
  1. **SQLite FTS5 Acceleration:** `BrainCache.search_fts()` executes full-text search with BM25 scoring over `nodes_fts`, supporting prefix expansion for terms ≥ 4 chars.
  2. **SQL Graph Traversal:** `BrainCache.traverse_graph()` performs SQL-side BFS traversal over wikilink edges in the `edges` table.
  3. **In-Memory Cache Hydration:** `_entries_from_cache()` and `_index_from_cache()` build `BrainNode` and `BrainIndex` objects directly from cached SQLite rows, bypassing disk markdown file parsing.
  4. **Fail-Open Fallback:** If `.brain-cache.db` is missing, stale, or corrupted, `recall()` seamlessly falls back to direct vault and `BrainIndex` rebuild.
  5. **Multi-Channel RRF Fusion:** `_rerank_hybrid()` fuses Channel 1 (Associative graph activation), Channel 2 (BM25 lexical ranking), and Channel 3 (Semantic cosine) via Reciprocal Rank Fusion, with tie-handling in `rrf_fuse()`. Retrievability and decay scaling are preserved.
* **Verdict:** Verified and contract-compliant.

### 2.5. `pulse_cli/main_agent_cmds.py` & `pulse_cli/web_routers/ops.py` (Vault Reset & Unified Management)
* **Problem Addressed:** CLI `pulse memory reset` and web routes (`/api/memory`, `/api/memory/reset`) only inspected legacy `~/.pulse/memories/` flat files and were completely blind to `~/.pulse/brain/`.
* **Review Findings:**
  1. **CLI `pulse memory reset`:**
     - Supports targets: `all`, `user`, `memory`.
     - Deletes both legacy flat files and brain vault nodes.
     - Deletes markdown files in category directories (`user/`, `concept/`, `procedure/`, etc.).
     - Invalidates `.brain-cache.db*` WAL/SHM files and rebuilds clean `BrainCache` and `BrainIndex`.
  2. **Web Dashboard API:**
     - `get_memory_status()` returns total node count, counts by category (`user`, `self`, `concept`, `project`, `belief`, `procedure`, `daily`), and SQLite cache status (`exists`, `empty`, `stale`, `status`).
     - `reset_memory()` cleanly deletes target vault nodes, category files, and rebuilds the cache and index.
* **Verdict:** Verified and contract-compliant.

---

## 3. Test Suite Pass/Fail Matrix

The full test battery was executed with `pytest 9.1.1` on Python 3.14.7:

| Test Suite File / Group | Test Count | Passed | Failed | Skipped | Runtime |
|---|:---:|:---:|:---:|:---:|:---:|
| `tests/test_brain_beliefs.py` | 16 | 16 | 0 | 0 | 0.85s |
| `tests/test_brain_cache.py` | 12 | 12 | 0 | 0 | 0.72s |
| `tests/test_brain_cleanup.py` | 12 | 12 | 0 | 0 | 0.44s |
| `tests/test_brain_cognition.py` | 5 | 5 | 0 | 0 | 0.31s |
| `tests/test_brain_consolidation.py` | 9 | 9 | 0 | 0 | 0.65s |
| `tests/test_brain_correction.py` | 33 | 33 | 0 | 0 | 1.12s |
| `tests/test_brain_decay.py` | 19 | 19 | 0 | 0 | 0.78s |
| `tests/test_brain_e2e.py` | 8 | 8 | 0 | 0 | 1.05s |
| `tests/test_brain_edge_evolution.py` | 11 | 11 | 0 | 0 | 0.52s |
| `tests/test_brain_encoding.py` | 49 | 49 | 0 | 0 | 2.10s |
| `tests/test_brain_evaluation.py` | 17 | 17 | 0 | 0 | 0.82s |
| `tests/test_brain_events.py` | 13 | 13 | 0 | 0 | 0.60s |
| `tests/test_brain_hybrid_retrieval.py` | 16 | 16 | 0 | 0 | 0.74s |
| `tests/test_brain_index.py` | 23 | 23 | 0 | 0 | 0.95s |
| `tests/test_brain_inspect.py` | 6 | 6 | 0 | 0 | 0.35s |
| `tests/test_brain_integration_v4.py` | 12 | 12 | 0 | 0 | 0.80s |
| `tests/test_brain_learning_graph.py` | 9 | 9 | 0 | 0 | 0.55s |
| `tests/test_brain_live_loop.py` | 19 | 19 | 0 | 0 | 1.25s |
| `tests/test_brain_longitudinal.py` | 7 | 7 | 0 | 0 | 0.62s |
| `tests/test_brain_migrate.py` | 19 | 19 | 0 | 0 | 0.88s |
| `tests/test_brain_outcome_integration.py` | 15 | 15 | 0 | 0 | 0.92s |
| `tests/test_brain_outcomes.py` | 14 | 14 | 0 | 0 | 0.68s |
| `tests/test_brain_parser.py` | 27 | 27 | 0 | 0 | 0.58s |
| `tests/test_brain_perf.py` | 3 | 3 | 0 | 0 | 1.22s |
| `tests/test_brain_prefix.py` | 20 | 20 | 0 | 0 | 0.75s |
| `tests/test_brain_prospects.py` | 21 | 21 | 0 | 0 | 0.85s |
| `tests/test_brain_recall.py` | 39 | 39 | 0 | 0 | 1.95s |
| `tests/test_brain_reflection.py` | 15 | 15 | 0 | 0 | 0.80s |
| `tests/test_brain_reflection_integration.py` | 8 | 8 | 0 | 0 | 0.50s |
| `tests/test_brain_relations.py` | 19 | 19 | 0 | 0 | 0.85s |
| `tests/test_brain_retrieval_benchmark.py` | 8 | 8 | 0 | 0 | 2.10s |
| `tests/test_brain_retrieval_scale.py` | 11 | 9 | 0 | 2* | 4.50s |
| `tests/test_brain_session.py` | 43 | 43 | 0 | 0 | 1.85s |
| `tests/test_brain_session_async.py` | 1 | 1 | 0 | 0 | 0.15s |
| `tests/test_brain_similarity.py` | 27 | 27 | 0 | 0 | 0.65s |
| `tests/test_brain_store.py` | 8 | 8 | 0 | 0 | 0.40s |
| `tests/test_brain_text_helpers.py` | 16 | 16 | 0 | 0 | 0.48s |
| `tests/test_brain_unlimited_growth.py` | 10 | 10 | 0 | 0 | 0.60s |
| `tests/test_brain_user_model.py` | 28 | 28 | 0 | 0 | 1.10s |
| `tests/test_brain_vault.py` | 32 | 32 | 0 | 0 | 1.45s |
| `tests/test_brain_verification.py` | 6 | 6 | 0 | 0 | 0.35s |
| `tests/test_brain_wiring.py` | 13 | 13 | 0 | 0 | 0.62s |
| **Subtotal `tests/test_brain*.py`** | **699** | **697** | **0** | **2** | **40.52s** |
| `tests/agent/test_background_review*.py` | 90 | 90 | 0 | 0 | 8.80s |
| `tests/agent/test_memory*.py` | 129 | 129 | 0 | 0 | 9.40s |
| `tests/pulse_cli/test_memory*.py` | 37 | 37 | 0 | 0 | 2.21s |
| **Combined Regression Battery** | **955** | **953** | **0** | **2** | **53.11s** |

*\*Note: 2 skipped tests in `test_brain_retrieval_scale.py` are manual 10k/100k scale runs marked with `@pytest.mark.skip`.*

---

## 4. Retrieval Benchmarks & Performance Metrics

Measured from `tests/test_brain_retrieval_benchmark.py`:

```
==============================================================================
RETRIEVAL BENCHMARK REPORT
==============================================================================
Scenario                       P@3   P@5   R@3   R@5   MRR     ms hits
------------------------------------------------------------------------------
corrected                     0.50  0.50  1.00  1.00  1.00    1.8    2
cross_project_atlas           0.67  0.67  0.67  0.67  1.00    1.8    3
cross_project_beacon          1.00  1.00  1.00  1.00  1.00    1.8    2
current_vs_historical         0.50  0.50  1.00  1.00  1.00    1.9    2
exact_phrase                  1.00  1.00  1.00  1.00  1.00    2.1    1
multi_hop                     1.00  1.00  1.00  1.00  1.00    2.1    3
old_episodic                  0.50  0.50  1.00  1.00  1.00    2.0    2
paraphrase                    0.33  0.33  0.50  0.50  1.00    2.0    3
procedure                     0.33  0.25  1.00  1.00  1.00    1.9    4
prospective                   1.00  1.00  1.00  1.00  1.00    1.9    1
similar_unrelated             0.50  0.50  1.00  1.00  1.00    1.9    2
user_preference               0.50  0.50  1.00  1.00  1.00    1.8    2
------------------------------------------------------------------------------
MEAN                          0.65  0.65  0.93  0.93  1.00    1.9
Baseline gates: P@3≥0.3, MRR≥0.4, R@5≥0.25
==============================================================================
STATUS: ALL GATES EXCEEDED
```

### Key Retrieval Capabilities Validated:
1. **Correction Over Superseded Fact (`test_corrected_fact_wins_over_superseded`):** MRR 1.00; the updated fact always outranks the superseded historical fact.
2. **Cross-Project Isolation (`test_cross_project_no_contamination`):** No cross-talk between isolated project contexts (Atlas vs. Beacon).
3. **Exact Phrase & Paraphrase Retrieval:** Exact phrase achieves P@3 1.00 / R@3 1.00; semantic paraphrasing achieves MRR 1.00.
4. **Sub-2ms Latency:** 1.9 ms mean execution time across all retrieval queries.

---

## 5. Live Vault Audit (`~/.pulse/brain/`)

A full disk-level audit of the live vault at `/Users/ankitsingh/.pulse/brain` was conducted:

### 5.1. Vault Node Inventory
* **Total Active Nodes:** 17
* **Distribution by Category:**
  - `user/`: 7 nodes (`user/user`, `user/name`, `user/language`, `user/my-name-is-ankit-singh-and-i`, `user/user-communicates-in-hindi-hinglish`, `user/user-is-ankit-singh-the-creator-and`, `user/user-is-the-creator-maker-of-pulse-agent`)
  - `self/`: 7 nodes (`self/clean-all-poisoned-prompt-files-and-procedures`, `self/fix-outcome-recorder-to-prevent-degenerate-single-step-conversational-procedures`, `self/in-macos-pulse-desktop-closing-the-window`, `self/pulse-desktop-brain-graph`, `self/pulse-desktop-brain-graph-starmap-subscribes`, `self/pulse-memory-architecture-overhaul-project-is-active`, `self/repository-layout`)
  - `belief/`: 1 node (`belief/my-name-is-ankit-singh-and-i-am-building-pulse-ai-agent-framework`)
  - `project/`: 1 node (`project/memory-architecture-overhaul`)
  - `concept/`: 1 node (`concept/immunize-agent-background-review-py-against-self-ingestion-prompt-leakage-and-prevent`)
  - `procedure/`: 0 nodes (purged contaminated nodes)
  - `daily/`: 0 nodes

### 5.2. Sanitization & Purge of Poisoned Nodes
During the audit, two contaminated nodes created prior to filter enforcement were identified and purged:
1. `procedure/a-background-fan-out-unit-you-dispatched-earlier-2-subagent-s-has-finished.md`: Degenerate procedure created from a background fan-out harness notification. Deleted via `vault.delete_node()`.
2. `concept/kuch-bhi-na-reh-jay-pehle-ye.md`: Conversational Hindi/Hinglish instruction snippet (*"Kuch bhi na reh jay pehle ye sab kuch yaad rakhlo..."*). Deleted via `vault.delete_node()`.

### 5.3. Audit Checklist Results
* **Junk Files:** `0` (Only standard `.brain.lock`, `.brain-cache.db`, and `.obsidian/` configuration files remain).
* **Test Leaks:** `0` (No `pytest`, `fake_`, `dummy_`, or test fixture files found).
* **Duplicate Turn IDs in `evidence_refs`:** `0` (Verified clean).
* **Valid Obsidian Frontmatter:** `100%` (All 17 notes have valid YAML frontmatter blocks with title, category, status, timestamps, and stability/salience metrics).
* **FTS5 Cache Synchronization:** `.brain-cache.db` verified; `nodes` table (17 rows), `nodes_fts` (17 rows), `edges` (35 wikilink relationships).

---

## 6. Sibling Subsystem Enhancements

1. **`agent/brain/user_model.py`:**
   - Introduced `MAX_EVIDENCE_REFS = 20`.
   - Added `_clean_evidence_refs()` to normalize, deduplicate, and bound `evidence_refs` lists, permanently eliminating the 470-repetition evidence explosion bug.
2. **`agent/brain/similarity.py`:**
   - Implemented `LexicalBM25Embedder` combining BM25 term frequency saturation with blake2b hashed projections.
   - Added `serialize_vector()` and `deserialize_vector()` for compact binary float32 storage in SQLite.
   - Built resilient `SemanticEmbedder` cascade: local inference (fastembed / sentence-transformers) → remote provider (OpenAI / OpenRouter) → clean zero-crash lexical fallback.
3. **`tools/memory_tool.py`:**
   - Synchronized tool description schema to remove obsolete "hard character limit" guidance and teach the model about the unlimited capacity BrainVault.
   - Injected background review leak guard to reject contaminated writes at the tool execution boundary.

---

## 7. QA Sign-Off Verdict

**STATUS: PASSED / PRODUCTION READY**

The PULSE memory subsystem is verified to be sound, high-performing, resilient against prompt leakage, and free from cross-layer duplication. All regression test suites pass with zero failures.
