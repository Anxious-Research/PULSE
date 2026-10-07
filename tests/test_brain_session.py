"""Tests for the agent bridge (specs/brain.md §6, §8).

This module is the one place the vault is coupled to a live agent, so the failure modes here
are the ones that previously cost the user their memory. The tests are therefore weighted
towards the guardrails, not the happy path:

- **Fail-open.** A broken vault, a broken index, a bug in recall, malformed config — every one
  must degrade to "no memory this turn" and must never raise into agent init or a turn.
- **Read path does not write.** Building the context block must not create the vault on disk.
- **Absent by default.** An empty vault yields "", so installing this cannot change behaviour.
- **No double fence.** The block is returned unfenced; the pipeline owns the fence.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.brain import session as session_mod
from agent.brain.session import (
    BrainSettings,
    brain_stable_prefix,
    brain_status,
    brain_turn_context,
    get_brain_config,
    resolve_settings,
    reset_agent_cache,
    vault_fingerprint,
)
from agent.brain.vault import BrainVault

T0 = 1_800_000_000


class BridgeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-session-"))
        self.vault_dir = self.tmp / "brain"
        self.vault = BrainVault(vault_dir=self.vault_dir)
        self.vault.ensure_vault_structure()
        self._orig_vault_cls = session_mod.BrainVault
        session_mod.BrainVault = lambda *a, **kw: self.vault  # hermetic: never the real $PULSE_HOME
        self.agent = SimpleNamespace()

    def tearDown(self):
        session_mod.BrainVault = self._orig_vault_cls
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed(self):
        self.vault.write_node(
            "concept/forgetting-curve",
            "Retrievability decays as exp(-delta over stability).",
            title="Forgetting Curve",
            tags=["memory"],
            salience=0.9,
            now=T0,
        )
        return self.vault


class TestConfig(unittest.TestCase):
    def test_missing_section_means_defaults_not_disabled(self):
        settings = BrainSettings.from_config({})
        self.assertTrue(settings.enabled)
        self.assertTrue(settings.recall_enabled)
        self.assertEqual(settings.recall_max_tokens, 600)

    def test_malformed_section_is_ignored(self):
        self.assertEqual(get_brain_config({"brain": "not-a-dict"}), {})
        self.assertTrue(BrainSettings.from_config({"brain": "not-a-dict"}).enabled)

    def test_string_and_numeric_booleans_are_accepted(self):
        self.assertFalse(BrainSettings.from_config({"brain": {"enabled": "false"}}).enabled)
        self.assertFalse(BrainSettings.from_config({"brain": {"recall_enabled": 0}}).recall_enabled)
        self.assertTrue(BrainSettings.from_config({"brain": {"recall_enabled": "yes"}}).recall_enabled)

    def test_values_are_clamped(self):
        settings = BrainSettings.from_config(
            {"brain": {"recall_max_tokens": 10**9, "recall_limit": -5, "recall_hops": 99, "recall_min_score": 7.5}}
        )
        self.assertLessEqual(settings.recall_max_tokens, 8000)
        self.assertGreaterEqual(settings.recall_limit, 1)
        self.assertLessEqual(settings.recall_hops, 4)
        self.assertLessEqual(settings.recall_min_score, 1.0)

    def test_garbage_numbers_fall_back_to_defaults(self):
        settings = BrainSettings.from_config(
            {"brain": {"recall_max_tokens": "lots", "recall_min_score": "high", "recall_hops": None}}
        )
        self.assertEqual(settings.recall_max_tokens, 600)
        self.assertEqual(settings.recall_min_score, 0.10)
        self.assertEqual(settings.recall_hops, 2)

    def test_prefix_is_off_by_default(self):
        """Layer 1 edits the cached prefix, so it must be opt-in."""
        self.assertFalse(BrainSettings.from_config({}).prefix_enabled)

    def test_resolve_settings_prefers_agent_config(self):
        agent = SimpleNamespace(_agent_config={"brain": {"recall_limit": 3}})
        self.assertEqual(resolve_settings(agent).recall_limit, 3)


class TestFailOpen(BridgeTestCase):
    def test_empty_vault_returns_nothing(self):
        self.assertEqual(brain_turn_context(self.agent, "what about the forgetting curve"), "")

    def test_vault_unavailable_returns_nothing(self):
        session_mod.BrainVault = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("no vault"))
        self.assertEqual(brain_turn_context(self.agent, "what about the forgetting curve"), "")

    def test_missing_vault_directory_returns_nothing(self):
        self.vault.vault_dir = self.tmp / "does-not-exist"
        self.assertEqual(brain_turn_context(self.agent, "what about the forgetting curve"), "")

    def test_recall_exception_is_swallowed(self):
        self.seed()
        original = session_mod.recall
        session_mod.recall = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("recall exploded"))
        try:
            self.assertEqual(brain_turn_context(self.agent, "what about the forgetting curve"), "")
        finally:
            session_mod.recall = original

    def test_index_failure_is_swallowed(self):
        self.seed()
        from agent.brain import index as index_mod

        original = index_mod.BrainIndex.rebuild
        index_mod.BrainIndex.rebuild = lambda self: (_ for _ in ()).throw(RuntimeError("rebuild exploded"))
        try:
            self.assertEqual(brain_turn_context(self.agent, "what about the forgetting curve"), "")
        finally:
            index_mod.BrainIndex.rebuild = original

    def test_disabled_brain_returns_nothing(self):
        self.seed()
        settings = BrainSettings(enabled=False)
        self.assertEqual(brain_turn_context(self.agent, "forgetting curve", settings=settings), "")

    def test_recall_disabled_returns_nothing(self):
        self.seed()
        settings = BrainSettings(recall_enabled=False)
        self.assertEqual(brain_turn_context(self.agent, "forgetting curve", settings=settings), "")

    def test_prefix_failure_is_swallowed(self):
        self.seed()
        original = session_mod.prefix_mod.compile_stable_prefix
        session_mod.prefix_mod.compile_stable_prefix = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nope"))
        try:
            self.assertEqual(brain_stable_prefix(self.agent, settings=BrainSettings(prefix_enabled=True)), "")
        finally:
            session_mod.prefix_mod.compile_stable_prefix = original

    def test_read_path_does_not_create_the_vault(self):
        """A turn with no memory must not touch disk."""
        fresh = self.tmp / "never-created"
        self.vault.vault_dir = fresh
        brain_turn_context(self.agent, "forgetting curve")
        self.assertFalse(fresh.exists(), "the read path created the vault")


class TestTrivialAndEmptyQueries(BridgeTestCase):
    def test_empty_query_returns_nothing(self):
        self.seed()
        self.assertEqual(brain_turn_context(self.agent, ""), "")
        self.assertEqual(brain_turn_context(self.agent, "   "), "")

    def test_bare_greeting_is_skipped(self):
        self.seed()
        self.assertEqual(brain_turn_context(self.agent, "hi"), "")
        self.assertEqual(brain_turn_context(self.agent, "thanks!"), "")

    def test_slash_command_is_skipped(self):
        self.seed()
        self.assertEqual(brain_turn_context(self.agent, "/help"), "")

    def test_non_string_non_list_returns_nothing(self):
        self.seed()
        self.assertEqual(brain_turn_context(self.agent, None), "")
        self.assertEqual(brain_turn_context(self.agent, 12345), "")

    def test_multimodal_text_parts_are_used(self):
        self.seed()
        message = [{"type": "text", "text": "tell me about the forgetting curve"}, {"type": "image", "data": "x"}]
        self.assertIn("Forgetting Curve", brain_turn_context(self.agent, message))

    def test_image_only_turn_returns_nothing(self):
        self.seed()
        self.assertEqual(brain_turn_context(self.agent, [{"type": "image", "data": "x"}]), "")


class TestRecallInjection(BridgeTestCase):
    def test_relevant_turn_returns_a_block(self):
        self.seed()
        block = brain_turn_context(self.agent, "how does the forgetting curve work")
        self.assertTrue(block)
        self.assertIn("Forgetting Curve", block)

    def test_irrelevant_turn_returns_nothing(self):
        self.seed()
        self.assertEqual(brain_turn_context(self.agent, "kubernetes ingress tls termination"), "")

    def test_block_is_returned_unfenced(self):
        """The pipeline owns the fence; a second one would be a malformed prompt."""
        self.seed()
        block = brain_turn_context(self.agent, "how does the forgetting curve work")
        self.assertNotIn("<memory-context>", block)

    def test_pipeline_fence_wraps_the_block_exactly_once(self):
        from agent.memory_manager import build_memory_context_block

        self.seed()
        block = brain_turn_context(self.agent, "how does the forgetting curve work")
        fenced = build_memory_context_block(block)
        self.assertEqual(fenced.count("<memory-context>"), 1)
        self.assertEqual(fenced.count("</memory-context>"), 1)
        self.assertIn("Forgetting Curve", fenced)

    def test_render_respects_the_configured_token_budget(self):
        for i in range(15):
            self.vault.write_node(
                f"concept/topic-{i}",
                " ".join(["memory"] + [f"word{j}" for j in range(80)]),
                title=f"Memory Topic {i}",
                now=T0,
            )
        settings = BrainSettings(recall_max_tokens=80, recall_limit=15)
        block = brain_turn_context(self.agent, "memory topic", settings=settings)
        self.assertTrue(block)
        # render() budgets max(120, tokens * CHARS_PER_TOKEN) characters.
        self.assertLessEqual(len(block), max(120, 80 * 4))


def _frontmatter(vault, node_id):
    """Read a node's frontmatter, failing loudly when the node is missing."""
    node = vault.read_node(node_id)
    assert node is not None, f"expected node {node_id!r} to exist"
    return node.frontmatter


class TestReconsolidation(BridgeTestCase):
    def test_recall_strengthens_the_recalled_notes(self):
        self.seed()
        before = _frontmatter(self.vault, "concept/forgetting-curve").access_count
        brain_turn_context(self.agent, "how does the forgetting curve work")
        after = _frontmatter(self.vault, "concept/forgetting-curve").access_count
        self.assertEqual(after, before + 1, "recall must strengthen what it recalled")

    def test_reconsolidation_can_be_disabled(self):
        self.seed()
        before = _frontmatter(self.vault, "concept/forgetting-curve").access_count
        brain_turn_context(
            self.agent, "how does the forgetting curve work", settings=BrainSettings(reconsolidate=False)
        )
        after = _frontmatter(self.vault, "concept/forgetting-curve").access_count
        self.assertEqual(after, before, "access must not be recorded when reconsolidation is off")

    def test_nothing_recalled_means_nothing_strengthened(self):
        self.seed()
        before = _frontmatter(self.vault, "concept/forgetting-curve").access_count
        brain_turn_context(self.agent, "kubernetes ingress tls termination")
        after = _frontmatter(self.vault, "concept/forgetting-curve").access_count
        self.assertEqual(after, before)


class TestStablePrefix(BridgeTestCase):
    def test_off_by_default(self):
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        self.assertEqual(brain_stable_prefix(self.agent), "")

    def test_enabled_returns_the_prefix(self):
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        self.assertIn("PULSE is a single entity", brain_stable_prefix(self.agent, settings=BrainSettings(prefix_enabled=True)))

    def test_empty_vault_yields_empty_even_when_enabled(self):
        self.assertEqual(brain_stable_prefix(self.agent, settings=BrainSettings(prefix_enabled=True)), "")

    def test_prefix_is_memoized_for_the_session(self):
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        settings = BrainSettings(prefix_enabled=True)
        first = brain_stable_prefix(self.agent, settings=settings)
        self.vault.write_node("user/tone", "prefers Hinglish", title="Tone", now=T0)
        self.assertEqual(
            brain_stable_prefix(self.agent, settings=settings), first, "a mid-session write must not reword the prefix"
        )

    def test_reset_clears_the_memoized_prefix(self):
        self.vault.write_node("self/identity", "PULSE is a single entity", title="Identity", now=T0)
        settings = BrainSettings(prefix_enabled=True)
        first = brain_stable_prefix(self.agent, settings=settings)
        self.vault.write_node("user/tone", "prefers Hinglish", title="Tone", now=T0)
        reset_agent_cache(self.agent)
        self.assertNotEqual(brain_stable_prefix(self.agent, settings=settings), first)


class TestIndexCache(BridgeTestCase):
    def test_index_is_reused_across_turns(self):
        self.seed()
        brain_turn_context(self.agent, "forgetting curve")
        fingerprint, index = self.agent._brain_index_cache
        brain_turn_context(self.agent, "forgetting curve")
        self.assertIs(self.agent._brain_index_cache[1], index, "unchanged vault must reuse the index")

    def test_index_is_rebuilt_when_the_vault_changes(self):
        self.seed()
        brain_turn_context(self.agent, "forgetting curve")
        _, before = self.agent._brain_index_cache
        self.vault.write_node("concept/other", "a brand new note about memory", title="Other", now=T0)
        brain_turn_context(self.agent, "memory topic")
        self.assertIsNot(self.agent._brain_index_cache[1], before, "a changed vault must rebuild the index")

    def test_fingerprint_tracks_count_and_mtime(self):
        first = vault_fingerprint(self.vault)
        self.seed()
        second = vault_fingerprint(self.vault)
        self.assertNotEqual(first, second)

    def test_fingerprint_of_a_broken_dir_is_safe(self):
        self.assertEqual(vault_fingerprint(SimpleNamespace(vault_dir=self.tmp / "nope")), (0, 0.0))


class TestStatus(BridgeTestCase):
    def test_status_without_a_vault(self):
        session_mod.BrainVault = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nope"))
        status = brain_status(self.agent)
        self.assertIsNone(status["vault"])
        self.assertIn("settings", status)

    def test_status_with_a_vault(self):
        self.seed()
        status = brain_status(self.agent)
        self.assertEqual(status["vault"]["stats"]["nodes"], 1)
        self.assertIn("dir", status["vault"])


if __name__ == "__main__":
    unittest.main()
