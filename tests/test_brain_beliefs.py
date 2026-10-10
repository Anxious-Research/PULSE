"""Belief / observation layer tests (specs/brain.md §3.5, §4.4, §8, §13.3, §13.4, §13.10).

What must hold:

* confidence is a function of *independent* evidence — the first piece makes a claim probable,
  each further independent piece raises it, and it never reaches certainty by inference;
* a repeated identical event cannot inflate a belief (duplicate resistance, §13.10);
* counter-evidence weakens and can refute a belief **without deleting it** — history is preserved
  and the revision log explains every confidence change (§13.4);
* provenance is real: a belief derives from its evidence as typed ``derived_from`` edges and can
  be traced back to the exact memories that produced it (§13.3, §13.15);
* beliefs survive a restart (they are ordinary vault files).
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent.brain import beliefs
from agent.brain.relations import RelationType
from agent.brain.vault import BrainVault


class BeliefTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-beliefs-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()
        self.a = self.vault.write_node(
            "concept/blue-green", "Blue-green switching keeps deploys reversible.", title="Blue Green")
        self.b = self.vault.write_node(
            "concept/rollback", "Every deploy can be rolled back instantly.", title="Rollback")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- confidence is evidence-shaped ------------------------------------

    def test_one_piece_of_evidence_is_probable_not_certain(self):
        conf = beliefs.evidence_confidence(["concept/a"])
        self.assertAlmostEqual(conf, beliefs.BELIEF_BASE, places=4)
        self.assertLess(conf, 1.0)

    def test_each_independent_piece_raises_confidence_bounded_by_ceiling(self):
        one = beliefs.evidence_confidence(["e1"])
        two = beliefs.evidence_confidence(["e1", "e2"])
        three = beliefs.evidence_confidence(["e1", "e2", "e3"])
        self.assertLess(one, two)
        self.assertLess(two, three)
        many = beliefs.evidence_confidence([f"e{i}" for i in range(50)])
        self.assertLessEqual(many, beliefs.BELIEF_CEILING)
        self.assertLess(many, 1.0)

    def test_asserted_belief_is_certain_inference_is_not(self):
        self.assertEqual(beliefs.evidence_confidence(["e1"], asserted=True), 1.0)
        self.assertLess(beliefs.evidence_confidence(["e1"]), 1.0)

    def test_duplicate_evidence_is_collapsed_before_scoring(self):
        # The same evidence id passed five times is one piece of evidence, not five.
        self.assertEqual(
            beliefs.evidence_confidence(["e1", "e1", "e1", "e1", "e1"]),
            beliefs.evidence_confidence(["e1"]),
        )

    # -- form / reinforce --------------------------------------------------

    def test_form_creates_an_active_belief_with_derived_edges(self):
        report = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.a.id, self.b.id])
        self.assertTrue(report["ok"] and report["created"])
        self.assertEqual(report["status"], "active")
        node = self.vault.read_node(report["belief_id"])
        self.assertEqual(node.frontmatter.category, "belief")
        targets = {r.target for r in beliefs.parse_relations(node.frontmatter.relations)}
        self.assertEqual(targets, {self.a.id, self.b.id})
        rel_types = {r.rel_type for r in beliefs.parse_relations(node.frontmatter.relations)}
        self.assertEqual(rel_types, {RelationType.DERIVED_FROM.value})

    def test_restating_a_belief_reinforces_the_same_node(self):
        first = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.a.id])
        second = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.b.id])
        self.assertEqual(first["belief_id"], second["belief_id"])
        self.assertFalse(second.get("created"))
        self.assertEqual(len(beliefs.list_beliefs(self.vault)), 1)

    def test_reinforcing_with_the_same_evidence_is_a_no_op(self):
        r = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.a.id])
        before = beliefs.explain_belief(self.vault, r["belief_id"])["confidence"]
        again = beliefs.reinforce_belief(self.vault, r["belief_id"], [self.a.id])
        self.assertFalse(again["reinforced"])
        after = beliefs.explain_belief(self.vault, r["belief_id"])["confidence"]
        self.assertEqual(before, after)

    def test_new_evidence_raises_confidence_and_logs_a_revision(self):
        r = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.a.id])
        beliefs.reinforce_belief(self.vault, r["belief_id"], [self.b.id], now=1_800_000_000)
        view = beliefs.explain_belief(self.vault, r["belief_id"])
        self.assertGreater(view["confidence"], r["confidence"])
        self.assertTrue(view["revisions"])
        self.assertIn("from", view["revisions"][-1])
        self.assertIn("to", view["revisions"][-1])

    # -- contradiction / refutation ---------------------------------------

    def test_counter_evidence_weakens_without_deleting(self):
        r = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.a.id, self.b.id])
        start = beliefs.explain_belief(self.vault, r["belief_id"])["confidence"]
        out = beliefs.add_counter_evidence(self.vault, r["belief_id"], ["concept/incident"], now=1_800_000_000)
        self.assertTrue(out["changed"])
        self.assertLess(out["confidence"], start)
        self.assertEqual(out["status"], "uncertain")
        # The belief is still on disk, with its history, and now carries the contradiction as a claim.
        view = beliefs.explain_belief(self.vault, r["belief_id"])
        self.assertIsNotNone(view)
        self.assertIn("concept/incident", view["counter"])
        self.assertTrue(any(rv["reason"] == "contradiction" for rv in view["revisions"]))

    def test_enough_counter_evidence_refutes(self):
        r = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.a.id])
        beliefs.add_counter_evidence(self.vault, r["belief_id"], ["c1"], now=1)
        out = beliefs.add_counter_evidence(self.vault, r["belief_id"], ["c2"], now=2)
        self.assertEqual(out["status"], "refuted")

    def test_counter_edge_is_typed_contradicts(self):
        r = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.a.id])
        self.vault.write_node("concept/incident", "A deploy lost data last week.", title="Incident")
        beliefs.add_counter_evidence(self.vault, r["belief_id"], ["concept/incident"], now=5)
        node = self.vault.read_node(r["belief_id"])
        rels = beliefs.parse_relations(node.frontmatter.relations)
        self.assertTrue(any(rel.rel_type == RelationType.CONTRADICTS.value
                            and rel.target == "concept/incident" for rel in rels))

    # -- supersession ------------------------------------------------------

    def test_supersede_keeps_the_old_belief_as_history(self):
        old = beliefs.form_belief(self.vault, "Deploys are manual.", evidence_refs=[self.a.id])
        new = beliefs.supersede_belief(self.vault, old["belief_id"], "Deploys are automated.",
                                       evidence_refs=[self.b.id], now=1_800_000_000)
        self.assertEqual(new.get("superseded"), old["belief_id"])
        old_node = self.vault.read_node(old["belief_id"])
        self.assertEqual(old_node.frontmatter.status, "superseded")
        self.assertEqual(old_node.frontmatter.superseded_by, new["belief_id"])

    # -- persistence -------------------------------------------------------

    def test_belief_survives_a_restart(self):
        r = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.a.id, self.b.id])
        reopened = BrainVault(vault_dir=self.tmp)
        view = beliefs.explain_belief(reopened, r["belief_id"])
        self.assertIsNotNone(view)
        self.assertEqual(sorted(view["supporting"]), sorted([self.a.id, self.b.id]))
        self.assertEqual(view["confidence"], r["confidence"])

    def test_revision_history_round_trips_through_frontmatter(self):
        r = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.a.id])
        beliefs.reinforce_belief(self.vault, r["belief_id"], [self.b.id], now=1_800_000_000)
        reopened = BrainVault(vault_dir=self.tmp)
        view = beliefs.explain_belief(reopened, r["belief_id"])
        rev = view["revisions"][0]
        self.assertIsInstance(rev, dict)
        self.assertEqual(rev["at"], 1_800_000_000)
        self.assertEqual(rev["reason"], "corroboration")

    def test_explain_returns_the_evidence_text(self):
        r = beliefs.form_belief(self.vault, "Deploys stay reversible.", evidence_refs=[self.a.id])
        view = beliefs.explain_belief(self.vault, r["belief_id"])
        self.assertEqual(view["evidence"][0]["id"], self.a.id)
        self.assertIn("Blue-green", view["evidence"][0]["text"])

    def test_no_capacity_ceiling_on_beliefs(self):
        # Each statement is about a genuinely different subsystem, so none is a restatement of
        # another — this measures storage growth, not the (correct) merge of duplicate claims.
        # Two lists of coprime length give 60 distinct subject/behaviour pairs.
        subjects = ["payments", "search", "auth", "mail", "maps", "video", "audio",
                    "files", "jobs", "billing", "camera", "sensors"]
        behaviours = ["credits a ledger", "ranks documents", "issues tokens", "queues deliveries",
                      "projects coordinates", "re-encodes frames", "normalises loudness",
                      "deduplicates blobs", "retries failures", "prorates plans",
                      "calibrates exposure", "samples pressure", "rotates encryption keys"]
        for i in range(60):
            statement = f"The {subjects[i % 12]} module {behaviours[i % 13]} when it runs."
            beliefs.form_belief(self.vault, statement, evidence_refs=[self.a.id], now=1_700_000_000 + i)
        self.assertGreaterEqual(len(beliefs.list_beliefs(self.vault)), 60)


if __name__ == "__main__":
    unittest.main()
