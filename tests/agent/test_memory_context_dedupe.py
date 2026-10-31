"""A recalled bullet is stated once per ``<memory-context>`` block.

Providers merge several stores, and the manager merges several providers, so one prefetch routinely
surfaces the same fact more than once. The composed block is stamped into the user row's
``api_content`` sidecar and replayed verbatim on every later request, so a byte-identical repeat is
paid once per turn for the life of the row — while telling the model nothing the block has not
already said.

Structure is not touched: headings, prose, blank lines and ``---`` rules survive as written, so a
provider's sections still read the way it wrote them.
"""
from __future__ import annotations

from agent.memory_manager import build_memory_context_block


def _body(block: str) -> str:
    """The provider's own content, without the wrapper and the system note that precedes it."""
    return block.split("]\n\n", 1)[1].rsplit("\n</memory-context>", 1)[0]


def test_a_repeated_bullet_is_kept_once_in_first_position():
    """Within a section a repeat is dropped; a section is delimited by a heading of ANY style.
    RetainDB writes prose headings (``Profile:`` / ``Relevant memories:``); other providers write
    markdown ones. In both shapes a placeholder bullet repeated under the second heading belongs to
    that heading and must stay, or the heading is left claiming nothing."""
    raw = "- alpha\n- beta\n- alpha\n- gamma\n"

    body = _body(build_memory_context_block(raw))

    # First-occurrence order, not a re-sort, and nothing else moved.
    assert body.splitlines() == ["- alpha", "- beta", "- gamma"]

    prose = "Profile:\n- None\nRelevant memories:\n- None\n"
    markdown = "## A\n- (none recorded)\n\n## B\n- (none recorded)\n"

    assert _body(build_memory_context_block(prose)) == prose
    assert _body(build_memory_context_block(markdown)) == markdown


def test_a_bullet_with_continuation_lines_is_never_touched():
    """Two entries can share a headline and differ underneath it. Dropping one would re-parent its
    provenance under the other and invent a record neither provider reported."""
    raw = ("- prefers draft PRs\n  (logged 12 Jan, source: supermemory)\n"
           "- prefers draft PRs\n  (logged 3 Feb, source: builtin)\n")
    nested = "- Project A\n  - status: active\n- Project B\n  - status: active\n"

    assert _body(build_memory_context_block(raw)) == raw
    assert _body(build_memory_context_block(nested)) == nested


def test_user_model_guidance_block_ignores_unrelated_query(tmp_path):
    from agent.brain.vault import BrainVault
    from agent.brain.session import brain_encode_turn, _user_model_guidance_block
    from tests.test_brain_integration_v4 import _make_agent

    vault = BrainVault(vault_dir=tmp_path)
    vault.ensure_vault_structure()
    agent = _make_agent(tmp_path, vault)

    brain_encode_turn(agent, "I prefer concise answers over long explanations", session_id="s1")

    # Unrelated queries must yield empty block (§9.1 context contamination guard)
    assert _user_model_guidance_block(vault, "explain merge sort") == ""
    assert _user_model_guidance_block(vault, "how does binary search work") == ""
    assert _user_model_guidance_block(vault, "kubernetes ingress tls") == ""


def test_user_model_guidance_block_triggers_on_user_signals(tmp_path):
    from agent.brain.vault import BrainVault
    from agent.brain.session import brain_encode_turn, _user_model_guidance_block
    from tests.test_brain_integration_v4 import _make_agent

    vault = BrainVault(vault_dir=tmp_path)
    vault.ensure_vault_structure()
    agent = _make_agent(tmp_path, vault)

    brain_encode_turn(agent, "I prefer concise answers over long explanations", session_id="s1")

    # Queries with user-model signal must trigger
    block = _user_model_guidance_block(vault, "how should I format my replies")
    assert "User model" in block
    assert "concise answers" in block

    block2 = _user_model_guidance_block(vault, "what are my preferences")
    assert "User model" in block2
    assert "concise answers" in block2


def test_user_model_guidance_block_dedupes_against_prefix_node_ids(tmp_path):
    from agent.brain.vault import BrainVault
    from agent.brain.session import brain_encode_turn, _user_model_guidance_block
    from tests.test_brain_integration_v4 import _make_agent

    vault = BrainVault(vault_dir=tmp_path)
    vault.ensure_vault_structure()
    agent = _make_agent(tmp_path, vault)

    brain_encode_turn(agent, "I prefer concise answers over long explanations", session_id="s1")

    nodes = [n for n in vault.list_all_nodes() if n.category == "user"]
    assert len(nodes) >= 1
    prefix_ids = {nodes[0].id}

    # When node is already in prefix, user model block should suppress it
    assert _user_model_guidance_block(vault, "how should I format my replies", prefix_node_ids=prefix_ids) == ""


def test_brain_turn_context_filters_stable_prefix_from_recall_hits(tmp_path):
    from agent.brain.vault import BrainVault
    from agent.brain.session import brain_turn_context, brain_stable_prefix
    from tests.test_brain_integration_v4 import _make_agent

    vault = BrainVault(vault_dir=tmp_path)
    vault.ensure_vault_structure()
    agent = _make_agent(tmp_path, vault)

    # Concept node (should be in Layer 2 recall)
    vault.write_node("concept/quick-sort", "Quicksort has O(n log n) average time complexity.", title="Quicksort", salience=0.9)
    # Self identity node (should be in Layer 1 stable prefix and NOT in Layer 2 recall)
    vault.write_node("self/identity", "PULSE is an autonomous agent framework.", title="Identity", salience=0.9)

    # Stable prefix compiles identity
    prefix = brain_stable_prefix(agent)
    assert "Identity" in prefix

    # Turn context recalling both cues
    ctx = brain_turn_context(agent, "quicksort autonomous agent framework")
    assert "Quicksort" in ctx
    # Identity node must be filtered out from Relevant memories
    assert "self/identity" not in ctx
    assert "[self] Identity" not in ctx

