"""Live-loop tests for the four defects found by exercising the real vault path.

Each test here corresponds to a behaviour observed by running the *production* entry points
against a real Markdown vault, not by reading the code — the first two made the brain silently
remember almost nothing, and the next two made the learning layers unreachable from conversation.

1. ``_declarative_signal`` demanded a verb from a narrow closed list, so "The pipeline pushes to
   staging" scored 0 and, carrying only the 0.10 novelty modifier, fell under the 0.4 gate.
2. The entity anchor only accepted PULSE's own vocabulary, so a fact about the user's world
   ("the payments service writes to the ledger") had no anchor even once the verb matched.
3. A restated claim was detected as a near-duplicate and the evidence was discarded — so
   independent corroboration could never accumulate and a belief was unreachable.
4. ``brain_reflect`` never passed a ``goal`` and ``reflect`` only filled ``procedural_guidance``
   when given one, so the outcome→strategy loop stored procedures and never surfaced them.
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


class EncodingCoverageTest(unittest.TestCase):
    """§3.1 — a durable fact from the user's message must actually become memory."""

    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="pulse-brain-live-")
        self._prev = os.environ.get("PULSE_HOME")
        os.environ["PULSE_HOME"] = self._home
        from agent.brain import encoding, vault as vault_mod
        self.encoding = encoding
        self.vault = vault_mod.BrainVault(vault_mod.get_brain_vault_dir())
        self.vault.ensure_vault_structure()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("PULSE_HOME", None)
        else:
            os.environ["PULSE_HOME"] = self._prev
        shutil.rmtree(self._home, ignore_errors=True)

    def test_ordinary_declarative_facts_are_encoded(self):
        # Regression: every one of these scored 0 before the verb list covered them, so a normal
        # conversation contributed almost nothing to memory.
        for statement in (
            "The atlas deploy pipeline pushes to the staging cluster before production.",
            "Our search index rebuilds every night at 2am.",
            "The billing service retries a failed charge three times.",
            "The graph renderer reads activation values from the vault.",
        ):
            with self.subTest(statement=statement):
                self.assertEqual(self.encoding._declarative_signal(statement), 1.0)
                self.assertEqual(len(self.encoding.extract_candidates(statement)), 1)

    def test_a_coded_name_anchors_a_fact_outside_pulses_own_vocabulary(self):
        # "the payments service" is a coded name — the third anchor the module documents — so a
        # durable fact about the user's world is remembered, not only facts about PULSE.
        statement = "The payments service writes to the ledger every night."
        self.assertEqual(self.encoding._declarative_signal(statement), 1.0)
        self.assertEqual(len(self.encoding.extract_candidates(statement)), 1)

    def test_noise_is_still_rejected(self):
        # Broadening the gate must not turn chatter into memory: the trivia/command/fragment
        # filters and the question filter are what keep the vault worth reading.
        for text in (
            "thanks, that is cool",
            "ok",
            "got it",
            "yeah do that",
            "hmm interesting",
            "what time is it",
            "can you check the logs?",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.encoding.extract_candidates(text), [])


class CorroborationTest(unittest.TestCase):
    """§4.4 — a restated claim is corroborating evidence, not a discarded duplicate."""

    CLAIM = "The atlas deploy pipeline pushes to the staging cluster before production."

    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="pulse-brain-corr-")
        self._prev = os.environ.get("PULSE_HOME")
        os.environ["PULSE_HOME"] = self._home
        from agent.brain import encoding, reflection, beliefs, vault as vault_mod
        self.encoding, self.reflection, self.beliefs = encoding, reflection, beliefs
        self.vault = vault_mod.BrainVault(vault_mod.get_brain_vault_dir())
        self.vault.ensure_vault_structure()

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("PULSE_HOME", None)
        else:
            os.environ["PULSE_HOME"] = self._prev
        shutil.rmtree(self._home, ignore_errors=True)

    def _restate(self):
        """Encode one claim, then the same claim again in different words."""
        self.encoding.encode_turn(self.vault, self.CLAIM, turn_id="t1")
        second = self.encoding.encode_turn(
            self.vault,
            "The atlas deploy pipeline always pushes to the staging cluster before production.",
            turn_id="t2",
        )
        return second

    def test_a_verbatim_repeat_is_recorded_as_corroboration(self):
        # The strongest evidence there is — the same claim asserted twice — and it used to be
        # silently dropped: novelty is 0.0 for a repeat, so recurrence alone could not clear the
        # salience gate and the restatement never reached the duplicate handling below it.
        self.encoding.encode_turn(self.vault, self.CLAIM, turn_id="t1")
        report = self.encoding.encode_turn(self.vault, self.CLAIM, turn_id="t2")
        self.assertEqual(len(report["corroborated"]), 1)
        self.assertEqual(report["semantic"], [])
        # A restatement adds evidence, not a node: the vault must not fill with the same sentence.
        self.assertEqual(len(self.vault.list_all_nodes()), 1)

    def test_a_restatement_is_recorded_as_corroboration(self):
        report = self._restate()
        self.assertEqual([s["reason"] for s in report["skipped"]], ["near_duplicate"])
        self.assertEqual(len(report["corroborated"]), 1)
        node = self.vault.read_node(report["corroborated"][0]["node_id"])
        self.assertIsNotNone(node)
        self.assertEqual(len(self.vault.corroborations_of(node)), 1)

    def test_corroboration_is_idempotent(self):
        self._restate()
        # Replaying the identical assertion must not inflate the evidence.
        self.encoding.encode_turn(
            self.vault,
            "The atlas deploy pipeline always pushes to the staging cluster before production.",
            turn_id="t2",
        )
        node = self.vault.list_all_nodes()[0]
        self.assertEqual(len(self.vault.corroborations_of(node)), 1)

    def test_corroborated_claim_supports_a_belief(self):
        # The end-to-end point: state a claim, restate it, and reflection can now hold it — which
        # was structurally impossible when the restatement was thrown away.
        self._restate()
        result = self.reflection.reflect(
            self.vault,
            question="atlas deploy pipeline staging cluster production",
            now=1_700_000_000,
        )
        self.assertGreaterEqual(len(result.updates), 1)
        formed = self.beliefs.list_beliefs(self.vault)
        self.assertEqual(len(formed), 1)
        self.assertEqual(formed[0].status, "active")
        # The belief names both assertions as its evidence.
        self.assertEqual(len(formed[0].supporting), 2)
        self.assertIn("#c1", formed[0].supporting[1])

    def test_an_uncorroborated_claim_forms_no_belief(self):
        # The guard on the other side: one memory is a fact, not a belief.
        self.encoding.encode_turn(
            self.vault,
            "The atlas deploy pipeline pushes to the staging cluster before production.",
            turn_id="t1",
        )
        self.reflection.reflect(
            self.vault, question="atlas deploy pipeline staging cluster production", now=1_700_000_000
        )
        self.assertEqual(self.beliefs.list_beliefs(self.vault), [])


class ProcedureReachesTheAgentTest(unittest.TestCase):
    """§8 — outcome learning must change what the agent does next, through the production seam."""

    def setUp(self):
        self._home = tempfile.mkdtemp(prefix="pulse-brain-proc-")
        self._prev = os.environ.get("PULSE_HOME")
        os.environ["PULSE_HOME"] = self._home
        from agent.brain import session, outcomes, reflection
        self.session, self.outcomes, self.reflection = session, outcomes, reflection
        self.agent = _Agent()
        self.vault = session.get_write_vault(self.agent)
        self.goal = "deploy the atlas service"
        self.question = "How should I deploy the atlas service?"

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("PULSE_HOME", None)
        else:
            os.environ["PULSE_HOME"] = self._prev
        shutil.rmtree(self._home, ignore_errors=True)

    def _record(self):
        for i in range(2):
            self.outcomes.record_outcome(
                self.vault, goal=self.goal, action="blue/green swap",
                result="staging verified then swapped", verdict="success",
                evidence_refs=[f"a{i}"], now=1_700_000_000 + i,
            )
            self.outcomes.record_outcome(
                self.vault, goal=self.goal, action="in-place restart",
                result="502s during the restart window", verdict="failure",
                failure_mode="drops requests while restarting",
                evidence_refs=[f"b{i}"], now=1_700_000_000 + i,
            )

    def test_goal_is_derived_from_a_question(self):
        self.assertEqual(
            self.reflection._goal_from_question("How should I deploy the atlas service?"),
            "deploy the atlas service",
        )
        self.assertEqual(
            self.reflection._goal_from_question("What's the best way to deploy the atlas service"),
            "deploy the atlas service",
        )

    def test_procedure_guidance_is_surfaced_without_an_explicit_goal(self):
        # The production path only ever has the turn's text; requiring a goal made guidance
        # unreachable for every real caller.
        self._record()
        result = self.reflection.reflect(self.vault, question=self.question, now=1_700_000_000)
        self.assertTrue(result.procedural_guidance)
        self.assertEqual(result.procedural_guidance[0]["goal"], self.goal)

    def test_the_next_turn_injects_the_learned_procedure(self):
        before = self.session.brain_turn_context(self.agent, self.question)
        self.assertNotIn("Learned procedures", before)
        self._record()
        after = self.session.brain_turn_context(self.agent, self.question)
        self.assertIn("Learned procedures", after)
        self.assertIn("blue/green swap", after)

    def test_guidance_names_the_action_to_avoid_not_just_its_symptom(self):
        # "avoid: 502s during the restart window" tells the agent what happened; only the action
        # ("in-place restart") tells it what not to reach for, so both must appear.
        self._record()
        context = self.session.brain_turn_context(self.agent, self.question)
        self.assertIn("worked: blue/green swap", context)
        self.assertIn("avoid: in-place restart", context)
        self.assertIn("why: 502s during the restart window", context)

    def test_guidance_survives_a_restart(self):
        self._record()
        fresh = _Agent()
        self.session.reset_agent_cache(fresh)
        self.assertIn("Learned procedures", self.session.brain_turn_context(fresh, self.question))


if __name__ == "__main__":
    unittest.main()
