"""Procedural / outcome memory tests (specs/brain.md §3.4, §4.3, §8 — Phase D).

The claim under test is the mandate's central one: *learning must change future behaviour*. A
procedure is only worth anything if a past outcome changes what PULSE reaches for next time. So
these tests assert both halves — that outcomes are recorded into a lesson honestly (a single win
is not a procedure; failures are kept; duplicates do not inflate) and that the lesson is what
retrieval surfaces for a related goal.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent.brain import outcomes
from agent.brain.relations import RelationType
from agent.brain.vault import BrainVault


class OutcomeTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-outcomes-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()
        self.ev = self.vault.write_node("concept/deploy-incident",
                                        "A big-bang deploy caused a production regression.", title="Incident")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- confidence shape --------------------------------------------------

    def test_a_single_success_is_unproven_not_reliable(self):
        self.assertEqual(outcomes.procedure_status(1, 0), "unproven")

    def test_recurring_success_becomes_reliable(self):
        report = outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                         result="zero downtime", verdict="success", now=1)
        report = outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                         result="zero downtime", verdict="success", now=2)
        report = outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                         result="zero downtime", verdict="success", now=3)
        self.assertEqual(report["status"], "reliable")
        self.assertTrue(report["recommended"])

    def test_two_successes_one_failure_is_situational(self):
        for t in (1, 2):
            outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                    result="zero downtime", verdict="success", now=t)
        report = outcomes.record_outcome(self.vault, goal="deploy safely", action="big-bang",
                                         result="regression", verdict="failure", now=3)
        self.assertEqual(report["status"], "situational")
        self.assertFalse(report["recommended"])

    def test_mostly_failures_marks_the_approach_as_avoid(self):
        for t in (1, 2, 3):
            outcomes.record_outcome(self.vault, goal="deploy safely", action="big-bang",
                                    result="regression", verdict="failure", now=t)
        procs = outcomes.list_procedures(self.vault)
        self.assertEqual(procs[0].status, "avoid")

    # -- record keeping ----------------------------------------------------

    def test_success_steps_are_derived_from_successful_actions(self):
        outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green switch",
                                result="zero downtime", verdict="success", now=1)
        outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green switch",
                                result="zero downtime", verdict="success", now=2)
        proc = outcomes.list_procedures(self.vault)[0]
        self.assertIn("blue-green switch", proc.steps)

    def test_failure_modes_are_kept(self):
        outcomes.record_outcome(self.vault, goal="deploy safely", action="big-bang",
                                result="prod regression", verdict="failure", now=1)
        outcomes.record_outcome(self.vault, goal="deploy safely", action="big-bang",
                                result="prod regression", verdict="failure", now=2)
        proc = outcomes.list_procedures(self.vault)[0]
        self.assertIn("prod regression", proc.failure_modes)

    def test_duplicate_outcome_is_not_double_counted(self):
        outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                result="zero downtime", verdict="success", now=1000)
        dup = outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                      result="zero downtime", verdict="success", now=1000)
        self.assertTrue(dup.get("duplicate"))
        proc = outcomes.list_procedures(self.vault)[0]
        self.assertEqual(proc.success_count, 1)

    def test_distinct_events_at_the_same_time_are_separate_outcomes(self):
        outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                result="zero downtime", verdict="success", now=1000)
        outcomes.record_outcome(self.vault, goal="deploy safely", action="canary",
                                result="caught a bad build", verdict="success", now=1000)
        proc = outcomes.list_procedures(self.vault)[0]
        self.assertEqual(proc.success_count, 2)

    def test_procedure_carries_reconsider_conditions(self):
        outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                result="zero downtime", verdict="success", now=1)
        outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                result="zero downtime", verdict="success", now=2)
        proc = outcomes.list_procedures(self.vault)[0]
        self.assertTrue(proc.reconsider_when)

    def test_learned_from_edges_point_at_evidence(self):
        outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                result="zero downtime", verdict="success",
                                evidence_refs=[self.ev.id], now=1)
        node = self.vault.list_all_nodes()
        proc = [n for n in node if n.frontmatter.category == "procedure"][0]
        rels = outcomes.parse_relations(proc.frontmatter.relations)
        self.assertTrue(any(r.rel_type == RelationType.LEARNED_FROM.value and r.target == self.ev.id
                            for r in rels))

    # -- the point: learning changes future retrieval ----------------------

    def test_a_learned_procedure_is_surfaced_for_a_related_goal(self):
        for t in (1, 2, 3):
            outcomes.record_outcome(self.vault, goal="deploy the service safely",
                                    action="blue-green switch with instant rollback",
                                    result="zero downtime", verdict="success", now=t)
        guidance = outcomes.procedures_for_goal(self.vault, "how should I deploy safely?")
        self.assertTrue(guidance)
        self.assertEqual(guidance[0].status, "reliable")
        self.assertIn("blue-green switch with instant rollback", guidance[0].steps)

    def test_an_avoid_procedure_is_still_returned_but_ranked_last(self):
        for t in (1, 2, 3):
            outcomes.record_outcome(self.vault, goal="deploy the service safely",
                                    action="big-bang deploy", result="regression", verdict="failure", now=t)
        guidance = outcomes.procedures_for_goal(self.vault, "how should I deploy safely?")
        self.assertTrue(guidance)          # knowing a method fails is a useful answer
        self.assertEqual(guidance[0].status, "avoid")

    # -- persistence -------------------------------------------------------

    def test_outcome_history_and_counts_survive_a_restart(self):
        for t in (1, 2, 3):
            outcomes.record_outcome(self.vault, goal="deploy safely", action="blue-green",
                                    result="zero downtime", verdict="success", now=t)
        reopened = BrainVault(vault_dir=self.tmp)
        proc = outcomes.list_procedures(reopened)[0]
        self.assertEqual(proc.success_count, 3)
        self.assertEqual(len(proc.outcomes), 3)
        self.assertIsInstance(proc.outcomes[0], dict)
        self.assertEqual(proc.outcomes[0]["verdict"], "success")
        self.assertEqual(proc.outcomes[0]["action"], "blue-green")

    def test_no_capacity_ceiling_on_outcomes(self):
        for i in range(80):
            outcomes.record_outcome(self.vault, goal="deploy safely", action=f"attempt {i}",
                                    result="ok", verdict="success", now=1_700_000_000 + i)
        proc = outcomes.list_procedures(self.vault)[0]
        self.assertEqual(proc.success_count, 80)


if __name__ == "__main__":
    unittest.main()
