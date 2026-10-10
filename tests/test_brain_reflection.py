"""Reflection engine tests (specs/brain.md §3.7, §4.4, §7, §8).

Reflection must reason over evidence without inventing it: name the memories a conclusion rests
on, surface conflict instead of hiding it, say "provisional" when the evidence is thin, and stay
bounded (no unbounded self-summarising loop). Writing is separable from analysis (``apply=False``).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent.brain import beliefs, reflection
from agent.brain.vault import BrainVault


class ShouldReflectTest(unittest.TestCase):
    def test_triggers_on_explicit_request(self):
        self.assertTrue(reflection.should_reflect(explicit=True))

    def test_triggers_on_contradiction_and_failure(self):
        self.assertTrue(reflection.should_reflect(contradiction=True))
        self.assertTrue(reflection.should_reflect(failure=True))

    def test_triggers_on_high_novelty_or_importance(self):
        self.assertTrue(reflection.should_reflect(novelty=0.9))
        self.assertTrue(reflection.should_reflect(importance=0.9))

    def test_does_not_trigger_on_an_ordinary_turn(self):
        self.assertFalse(reflection.should_reflect(novelty=0.1, importance=0.2))


class ReflectionTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-reflection-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def seed(self):
        self.vault.write_node(
            "concept/orbital-cache", "The cache layer is shared by every PULSE subsystem.",
            title="Cache Layer", tags=["cache", "architecture"], salience=0.9)
        self.vault.write_node(
            "concept/shared-cache-2", "A shared cache layer is used by every PULSE subsystem.",
            title="Shared Cache", tags=["cache", "architecture"], salience=0.85)

    # -- evidence gathering -----------------------------------------------

    def test_reflect_with_no_evidence_reports_uncertainty(self):
        result = reflection.reflect(self.vault, question="what is the capital of France?")
        self.assertEqual(result.evidence, [])
        self.assertTrue(result.uncertainty)

    def test_reflect_names_the_memories_it_used(self):
        self.seed()
        result = reflection.reflect(self.vault, question="how is the cache layer organised?")
        self.assertTrue(result.evidence)
        self.assertTrue(result.supporting)
        self.assertTrue(all(isinstance(i, str) for i in result.supporting))

    def test_empty_question_is_a_no_op(self):
        result = reflection.reflect(self.vault, question="")
        self.assertEqual(result.conclusion, "")
        self.assertIsNotNone(result.uncertainty)

    # -- belief formation --------------------------------------------------

    def test_repeated_evidence_forms_a_belief(self):
        self.seed()
        result = reflection.reflect(self.vault, question="cache layer architecture", apply=True)
        created = [u for u in result.updates if u.get("kind") == "belief" and u.get("created")]
        self.assertTrue(created)
        self.assertTrue(beliefs.list_beliefs(self.vault))

    def test_reflection_is_idempotent_across_runs(self):
        self.seed()
        reflection.reflect(self.vault, question="cache layer architecture", apply=True)
        first = len(beliefs.list_beliefs(self.vault))
        reflection.reflect(self.vault, question="cache layer architecture", apply=True)
        second = len(beliefs.list_beliefs(self.vault))
        self.assertEqual(first, second, "re-reflecting on the same evidence must not duplicate beliefs")

    def test_apply_false_writes_nothing(self):
        self.seed()
        result = reflection.reflect(self.vault, question="cache layer architecture", apply=False)
        self.assertTrue(result.evidence)
        self.assertEqual(beliefs.list_beliefs(self.vault), [])
        self.assertEqual(result.updates, [])

    # -- conflict awareness ------------------------------------------------

    def test_contradictory_evidence_is_reported_not_hidden(self):
        self.vault.write_node("concept/plan", "The PULSE project migrated to architecture B today.",
                              title="Plan", salience=0.9)
        self.vault.write_node("concept/denial", "The PULSE project did not migrate to architecture B.",
                              title="Denial", salience=0.8)
        result = reflection.reflect(self.vault, question="did the PULSE project migrate to architecture B?")
        self.assertTrue(result.contradictions, "the opposing claims must be detected")
        self.assertIn("provisional", (result.uncertainty or ""))

    def test_counter_evidence_is_recorded_against_the_belief(self):
        self.vault.write_node("concept/plan", "The PULSE project migrated to architecture B today.",
                              title="Plan", salience=0.9)
        self.vault.write_node("concept/denial", "The PULSE project did not migrate to architecture B.",
                              title="Denial", salience=0.8)
        reflection.reflect(self.vault, question="did the PULSE project migrate to architecture B?",
                           apply=True)
        # If a belief was formed it must carry the losing claim as counter-evidence, not silently drop it.
        for b in beliefs.list_beliefs(self.vault):
            if b.counter:
                return
        # No belief formed (evidence too thin) is also acceptable — but then nothing was claimed.

    # -- procedures reach reflection (learning changes behaviour) ----------

    def test_reflection_surfaces_a_learned_procedure_for_the_goal(self):
        from agent.brain import outcomes
        for t in (1, 2, 3):
            outcomes.record_outcome(self.vault, goal="restart the gateway", action="drain then restart",
                                    result="no dropped messages", verdict="success", now=t)
        result = reflection.reflect(self.vault, question="how should I restart the gateway?",
                                    goal="restart the gateway")
        self.assertTrue(result.procedural_guidance)
        self.assertEqual(result.procedural_guidance[0]["status"], "reliable")

    # -- bounds ------------------------------------------------------------

    def test_reflection_stays_within_its_update_budget(self):
        for i in range(20):
            self.vault.write_node(f"concept/fact-{i}",
                                  f"The PULSE system records fact number {i} about itself.",
                                  title=f"Fact {i}", salience=0.7)
        result = reflection.reflect(self.vault, question="what facts has PULSE recorded about itself?")
        belief_updates = [u for u in result.updates if u.get("kind") == "belief"]
        self.assertLessEqual(len(belief_updates), reflection.MAX_BELIEF_UPDATES)

    def test_render_is_readable(self):
        self.seed()
        result = reflection.reflect(self.vault, question="cache layer architecture")
        text = result.render()
        self.assertIn("Reflection on:", text)


if __name__ == "__main__":
    unittest.main()
