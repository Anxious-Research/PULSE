#!/usr/bin/env python3
"""End-to-end proof for the brain's prompt integration (specs/brain.md §6, stage S4).

Runs the *production* call path — ``agent.turn_context._brain_recall_block`` and
``compose_user_api_content`` — so what it prints is the bytes a real turn would send, not a
reimplementation that can drift from it.

    PYTHONPATH=. python evals/brain_memory_demo.py

Everything happens in a throwaway temp directory; ``$PULSE_BRAIN_DIR`` points the vault there,
so no real profile is touched. Covers:

1. legacy ``MEMORY.md``/``USER.md`` -> vault, additive and idempotent
2. a relevant cue, and the exact fenced block appended to the user message
3. selectivity — off-topic turns and greetings inject nothing
4. association — a note reached only through a wikilink
5. prompt-cache safety — the Layer-1 prefix is byte-stable; recall does not reword it
6. fail-open — a raising vault degrades to no memory, never a broken turn
7. reconsolidation — recall strengthens what it recalled
"""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from types import SimpleNamespace

TMP = Path(tempfile.mkdtemp(prefix="s4-demo-"))
VAULT = TMP / "brain"
LEGACY = TMP / "memories"
os.environ["PULSE_BRAIN_DIR"] = str(VAULT)

from agent.brain.migrate import ENTRY_DELIMITER, migrate_legacy_memory  # noqa: E402
from agent.brain.session import brain_status, reset_agent_cache  # noqa: E402
from agent.brain.vault import BrainVault  # noqa: E402

LEGACY.mkdir(parents=True, exist_ok=True)
# Real legacy stores delimit entries with ENTRY_DELIMITER, not blank lines.
DELIM = ENTRY_DELIMITER
(LEGACY / "MEMORY.md").write_text(
    DELIM.join(
        [
            "PULSE forgets exponentially: retrievability decays as exp(-delta/stability). Nothing is ever deleted.",
            "The brain vault is plain Markdown under ~/.pulse/brain; the index is rebuildable, never authoritative.",
            "Recall is selective: a weak cue injects nothing at all, so stale memory cannot derail a turn.",
        ]
    ),
    encoding="utf-8",
)
(LEGACY / "USER.md").write_text(
    DELIM.join(
        [
            "User prefers Hinglish replies and wants real logic, no shortcuts, no jugaad.",
            "User requires verified output and never accepts a claim without it.",
        ]
    ),
    encoding="utf-8",
)

print("=" * 78)
print("1. ENSURE VAULT + MIGRATE LEGACY MEMORY (additive, idempotent, dry-run first)")
print("=" * 78)
vault = BrainVault(vault_dir=VAULT)
vault.ensure_vault_structure()
run1 = migrate_legacy_memory(source_dir=LEGACY, vault=vault, dry_run=False)
run2 = migrate_legacy_memory(source_dir=LEGACY, vault=vault, dry_run=False)
print(f"created: {run1['created']}   second run created: {run2['created']} (idempotent)")
for node in sorted(vault.list_all_nodes(), key=lambda n: n.id):
    print(f"   {node.id}")

# Add the stable Layer-1 material, as the encoding stage (S5) will.
vault.write_node("self/identity", "PULSE is one entity; memory is an organ of it, not an app.", title="Identity")
vault.write_node("user/preferences", "Prefers Hinglish replies; wants real logic, no shortcuts.", title="Preferences")

agent = SimpleNamespace(
    _agent_config={"brain": {"prefix_enabled": True}},
    # _memory_parts reads these before it ever reaches the brain prefix; a real agent always
    # has them set by _init_memory.
    _memory_store=None,
    _memory_enabled=False,
    _user_profile_enabled=False,
    _memory_manager=None,
)

print()
print("=" * 78)
print("2. TURN 1 — RELEVANT CUE: what the model is actually sent")
print("=" * 78)
from agent.turn_context import _brain_recall_block, compose_user_api_content  # noqa: E402

QUESTION = "how does the forgetting curve actually work?"
block = _brain_recall_block(agent, QUESTION)
if not block:
    print("   (nothing injected)")
else:
    print(f"recall block ({len(block)} chars, budget 600 tokens = 2400 chars):")
    print(block)
    sent = compose_user_api_content(QUESTION, block, "")
    print()
    print("--- exact bytes appended to the user message ---")
    print(repr(sent[len(QUESTION):]) if sent else "(none)")
    print(f"fences in the payload: {sent.count('<memory-context>')}")

print()
print("=" * 78)
print("3. TURN 2 — OFF-TOPIC CUE: nothing is injected (selectivity)")
print("=" * 78)
for query in ["kubernetes ingress tls termination certificate", "hi", "thanks!", "/help"]:
    out = _brain_recall_block(agent, query)
    print(f"   {query!r:50} -> {('INJECTED ' + str(len(out)) + ' chars') if out else 'nothing injected'}")

print()
print("=" * 78)
print("4. TURN 3 — ASSOCIATION: a cue that only a LINK connects")
print("=" * 78)
vault.write_node(
    "concept/associative-recall",
    "Activation spreads across links to the forgetting curve and to user preferences.",
    title="Associative Recall",
    tags=["memory"],
)
vault.write_node("self/wiring", "Memory lives in [[concept/associative-recall]].", title="Wiring")
reset_agent_cache(agent)  # a real write invalidates the memo, as _reload_memory_from_disk does
out = _brain_recall_block(agent, "explain associative recall and spreading activation")
print(out or "   (nothing injected)")

print()
print("=" * 78)
print("5. PROMPT-CACHE SAFETY: system prompt must be byte-stable across calls")
print("=" * 78)
from agent.system_prompt import _memory_parts  # noqa: E402

first = "\n\n".join(_memory_parts(agent))
second = "\n\n".join(_memory_parts(agent))
print(f"Layer-1 prefix chars: {len(first)}   stable across calls: {first == second}")
print("--- prefix as it would appear in the system prompt ---")
print(first)

print()
print("=" * 78)
print("6. FAIL-OPEN: the brain cannot break a turn")
print("=" * 78)
import agent.brain.session as session_mod  # noqa: E402

orig = session_mod.BrainVault
reset_agent_cache(agent)  # drop the memoized vault, or the patch below never gets consulted
session_mod.BrainVault = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("vault on fire"))  # type: ignore[assignment]
try:
    print(f"vault raising          -> recall block = {_brain_recall_block(agent, QUESTION)!r}")
    print(f"vault raising          -> prefix       = {'(empty)' if not ''.join(_memory_parts(agent)) else 'PRESENT'}")
finally:
    session_mod.BrainVault = orig

print()
print("=" * 78)
print("7. RECONSOLIDATION: recall strengthens what it recalled")
print("=" * 78)
before = vault.read_node("concept/associative-recall")
reset_agent_cache(agent)
_brain_recall_block(agent, "explain associative recall and spreading activation")
after = vault.read_node("concept/associative-recall")
print(f"access_count {before.frontmatter.access_count} -> {after.frontmatter.access_count}")
print(f"stability    {before.frontmatter.stability:.4f} -> {after.frontmatter.stability:.4f}")

print()
print("=" * 78)
print("8. VAULT STATUS")
print("=" * 78)
status = brain_status(agent)
print(f"dir: {status['vault']['dir']}")
print(f"stats: {status['vault']['stats']}")

shutil.rmtree(TMP, ignore_errors=True)
print("\n(demo workspace removed)")
