"""Cognition wiring tests (§7, §8, Phase D) — the production seam, not just the library.

The belief/outcome/reflection modules are worthless if nothing in the running agent ever calls
them. These tests exercise the actual entry point (`brain_reflect`) that the per-turn background
worker invokes, so "learning happens in the real loop" is verified rather than assumed.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest


class _Agent:
    """Minimal stand-in for the agent object the brain helpers expect."""

    def __init__(self, config=None):
        self._agent_config = config or {}


class CognitionWiringTest(unittest.TestCase):
    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="pulse-brain-cognition-")
        self._prev = os.environ.get("PULSE_HOME")
        os.environ["PULSE_HOME"] = self._home
        from agent.brain import session
        self.session = session
        self.agent = _Agent()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("PULSE_HOME", None)
        else:
            os.environ["PULSE_HOME"] = self._prev
        shutil.rmtree(self._home, ignore_errors=True)

    def _seed(self):
        vault = self.session.get_write_vault(self.agent)
        vault.write_node("concept/cache-a", "The cache layer is shared by every subsystem.",
                         title="Cache A", salience=0.9)
        vault.write_node("concept/cache-b", "A shared cache layer is used by every subsystem.",
                         title="Cache B", salience=0.85)
        return vault

    def test_reflect_forms_beliefs_into_the_vault(self):
        from agent.brain import beliefs
        vault = self._seed()
        report = self.session.brain_reflect(self.agent, text="how is the cache layer shared?")
        self.assertEqual(report.get("reflect"), "ok")
        self.assertGreaterEqual(report.get("beliefs_formed", 0), 1)
        self.assertTrue(beliefs.list_beliefs(vault))

    def test_reflect_respects_the_disable_lever(self):
        from agent.brain import beliefs
        self.agent = _Agent({"brain": {"reflect_enabled": False}})
        vault = self._seed()
        report = self.session.brain_reflect(self.agent, text="how is the cache layer shared?")
        self.assertEqual(report.get("reflect"), "disabled")
        self.assertEqual(beliefs.list_beliefs(vault), [])

    def test_dry_run_writes_nothing(self):
        from agent.brain import beliefs
        vault = self._seed()
        self.session.brain_reflect(self.agent, text="how is the cache layer shared?", dry_run=True)
        self.assertEqual(beliefs.list_beliefs(vault), [])

    def test_reflect_never_raises_on_a_broken_vault(self):
        # A vault whose directory cannot be read must degrade to a report, never an exception —
        # this runs on the background encode thread where an exception would be invisible.
        report = self.session.brain_reflect(self.agent, vault=object(), text="anything")
        self.assertIn(report.get("reflect"), {"failed", "no vault", "ok"})

    def test_settings_expose_the_reflect_levers(self):
        settings = self.session.resolve_settings(_Agent({"brain": {"reflect_every": 3}}))
        self.assertTrue(settings.reflect_enabled)
        self.assertEqual(settings.reflect_every, 3)
        capped = self.session.resolve_settings(_Agent({"brain": {"reflect_every": 0}}))
        self.assertGreaterEqual(capped.reflect_every, 1)


if __name__ == "__main__":
    unittest.main()
