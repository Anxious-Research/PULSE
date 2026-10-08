"""End-to-end wiring tests for S4 (specs/brain.md §6).

The unit tests prove the pieces work. These prove the pieces are actually *connected* to the
live turn pipeline, which is the thing that broke last time: a correct store that nothing
called, or a call site that swallowed its own error into an unbound local.

What is asserted here:

1. ``turn_context._brain_recall_block`` returns the block, and returns ``""`` (never raises)
   when the brain is unavailable.
2. The block reaches the **user message** via ``compose_user_api_content`` — the per-turn
   channel — and is fenced exactly once.
3. The **system prompt** gains the Layer-1 prefix only when explicitly enabled.
4. The system prompt is byte-stable across calls for the same vault, which is the prompt-cache
   invariant.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from agent.brain import session as session_mod
from agent.brain.vault import BrainVault

T0 = 1_800_000_000


class WiringTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-wiring-"))
        self.vault = BrainVault(vault_dir=self.tmp / "brain")
        self.vault.ensure_vault_structure()
        self._orig = session_mod.BrainVault
        session_mod.BrainVault = lambda *a, **kw: self.vault
        self.agent = SimpleNamespace(
            _memory_store=None,
            _memory_enabled=False,
            _user_profile_enabled=False,
            _memory_manager=None,
        )

    def tearDown(self):
        session_mod.BrainVault = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed(self):
        self.vault.write_node(
            "concept/forgetting-curve",
            "Retrievability decays as exp(-delta over stability). Nothing is ever deleted.",
            title="Forgetting Curve",
            tags=["memory", "decay"],
            salience=0.9,
            now=T0,
        )


class TestTurnWiring(WiringTestCase):
    def test_recall_block_reaches_the_user_message(self):
        from agent.turn_context import _brain_recall_block, compose_user_api_content

        self.seed()
        block = _brain_recall_block(self.agent, "how does the forgetting curve work")
        self.assertTrue(block)

        api_content = compose_user_api_content("how does the forgetting curve work?", block, "")
        self.assertIsNotNone(api_content)
        assert api_content is not None  # narrow for the type checker; the assert above is the real check
        self.assertTrue(api_content.startswith("how does the forgetting curve work?"))
        self.assertIn("Forgetting Curve", api_content)
        self.assertEqual(api_content.count("<memory-context>"), 1, "exactly one fence")

    def test_nothing_relevant_leaves_the_message_untouched(self):
        from agent.turn_context import _brain_recall_block, compose_user_api_content

        self.seed()
        self.assertEqual(_brain_recall_block(self.agent, "kubernetes ingress tls termination"), "")
        self.assertIsNone(compose_user_api_content("kubernetes ingress tls termination", "", ""))

    def test_missing_vault_does_not_raise_into_the_turn(self):
        from agent.turn_context import _brain_recall_block

        session_mod.BrainVault = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("vault gone"))
        self.assertEqual(_brain_recall_block(self.agent, "how does the forgetting curve work"), "")

    def test_block_rides_alongside_an_external_provider_prefetch(self):
        """A provider block and the brain block coexist in one per-turn injection."""
        from agent.turn_context import _brain_recall_block, compose_user_api_content

        self.seed()
        brain_block = _brain_recall_block(self.agent, "how does the forgetting curve work")
        combined = f"provider context line\n\n{brain_block}"
        api_content = compose_user_api_content("q", combined, "")
        self.assertIsNotNone(api_content)
        assert api_content is not None
        self.assertIn("provider context line", api_content)
        self.assertIn("Forgetting Curve", api_content)


class TestSystemPromptWiring(WiringTestCase):
    def test_prefix_present_in_the_system_prompt_by_default(self):
        """v3: Layer 1 is ON by default (specs/brain.md §11.1) — identity loads out of box."""
        from agent.system_prompt import _memory_parts

        self.vault.write_node("self/identity", "PULSE is a single entity.", title="Identity", now=T0)
        joined = "\n".join(_memory_parts(self.agent))
        self.assertIn("PULSE is a single entity.", joined)

    def test_prefix_absent_when_disabled(self):
        from agent.system_prompt import _memory_parts

        self.agent._agent_config = {"brain": {"prefix_enabled": False}}
        self.vault.write_node("self/identity", "PULSE is a single entity.", title="Identity", now=T0)
        joined = "\n".join(_memory_parts(self.agent))
        self.assertNotIn("PULSE is a single entity.", joined, "Layer 1 must be opt-out")

    def test_prefix_appears_when_enabled(self):
        from agent.system_prompt import _memory_parts

        self.agent._agent_config = {"brain": {"prefix_enabled": True}}
        self.vault.write_node("self/identity", "PULSE is a single entity.", title="Identity", now=T0)
        joined = "\n".join(_memory_parts(self.agent))
        self.assertIn("PULSE is a single entity.", joined)

    def test_system_prompt_is_byte_stable_across_calls(self):
        """The cache invariant: same vault, same bytes, every time."""
        from agent.system_prompt import _memory_parts

        self.agent._agent_config = {"brain": {"prefix_enabled": True}}
        self.vault.write_node("self/identity", "PULSE is a single entity.", title="Identity", now=T0)
        self.vault.write_node("user/preferences", "prefers Hinglish replies", title="Preferences", now=T0)
        self.assertEqual(_memory_parts(self.agent), _memory_parts(self.agent))

    def test_empty_vault_adds_nothing_to_the_system_prompt(self):
        from agent.system_prompt import _memory_parts

        self.agent._agent_config = {"brain": {"prefix_enabled": True}}
        self.assertEqual(_memory_parts(self.agent), [])

    def test_reset_agent_cache_is_callable_on_the_reload_boundary(self):
        from agent.brain.session import reset_agent_cache

        self.agent._agent_config = {"brain": {"prefix_enabled": True}}
        self.vault.write_node("self/identity", "PULSE is a single entity.", title="Identity", now=T0)
        from agent.system_prompt import _memory_parts

        self.assertIn("PULSE is a single entity.", "\n".join(_memory_parts(self.agent)))
        reset_agent_cache(self.agent)
        # Still present: the note did not change, only the memo was dropped.
        self.assertIn("PULSE is a single entity.", "\n".join(_memory_parts(self.agent)))


class TestFailureIsolation(WiringTestCase):
    def test_broken_brain_never_breaks_the_system_prompt(self):
        """Guardrail §8.3: a vault problem degrades to no prefix, never to a broken agent."""
        from agent.system_prompt import _memory_parts

        self.agent._agent_config = {"brain": {"prefix_enabled": True}}
        session_mod.BrainVault = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("vault on fire"))
        self.assertEqual(_memory_parts(self.agent), [])

    def test_broken_config_never_breaks_the_system_prompt(self):
        from agent.system_prompt import _memory_parts

        self.agent._agent_config = {"brain": ["not", "a", "dict"]}
        self.assertEqual(_memory_parts(self.agent), [])

    def test_suppress_misuse_would_leave_a_local_unbound(self):
        """Pin the anti-pattern: the guard must be an explicit default, not a bare suppress.

        The previous breakage was ``with suppress(Exception): x = ...`` followed by a use of
        ``x`` — the import failed, ``x`` was never bound, and the turn died with
        UnboundLocalError far from the cause. Both call sites must assign a default first.
        """
        import inspect

        from agent import system_prompt, turn_context

        for fn in (turn_context._brain_recall_block, system_prompt._memory_parts):
            source = inspect.getsource(fn)
            self.assertNotIn("suppress(", source, f"{fn.__name__} must not use bare suppress()")


if __name__ == "__main__":
    unittest.main()
