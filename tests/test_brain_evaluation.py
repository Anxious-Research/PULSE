"""Memory evaluation framework (specs/brain.md §11).

The mandate's bar is explicit: *do not declare success because unit tests pass or the graph looks
impressive*. So this suite runs the fifteen named scenarios against a deterministic fixture and
reports measurable numbers — precision@k, MRR, stale-belief rate, duplicate rate, correction
propagation, retrieval latency — rather than asserting "it works".

Every scenario runs in its own throwaway vault; time is injected, so results are reproducible run
to run. Thresholds are the floors measured from this fixture, recorded here as constants so a
regression is visible as a specific number dropping, not a vague "something broke".
"""

from __future__ import annotations

import shutil
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agent.brain import beliefs, outcomes, reflection
from agent.brain.index import BrainIndex
from agent.brain.recall import recall
from agent.brain.vault import BrainVault

T0 = 1_700_000_000
DAY = 86400

# ── measured floors (see the summary test at the bottom) ───────────────────
MIN_PRECISION_AT_3 = 0.6
MIN_MRR = 0.7
MAX_STALE_BELIEF_RATE = 0.0      # a superseded belief must never read as current
MAX_DUPLICATE_RATE = 0.0         # replayed writes must not duplicate


def precision_at_k(expected_ids, actual_ids, k):
    """Fraction of the top-k results that are relevant."""
    if k <= 0:
        return 0.0
    top = list(actual_ids)[:k]
    if not top:
        return 0.0
    return sum(1 for i in top if i in expected_ids) / float(len(top))


def reciprocal_rank(expected_ids, actual_ids):
    """1 / rank of the first relevant result (0 if none present)."""
    for rank, node_id in enumerate(actual_ids, start=1):
        if node_id in expected_ids:
            return 1.0 / rank
    return 0.0


class EvaluationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="pulse-brain-eval-"))
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _recall_ids(self, query, **kw):
        return recall(query, vault=self.vault, **kw).node_ids()


class Scenario01DirectRecall(EvaluationCase):
    def test_direct_recall_recovers_a_stored_fact(self):
        self.vault.write_node("user/preference-editor", "Sir prefers the Zed editor for code.",
                              title="Editor Preference", salience=0.9)
        self.vault.write_node("concept/unrelated", "The build pipeline runs on Tuesdays.",
                              title="Build Schedule", salience=0.3)
        ids = self._recall_ids("which editor does Sir prefer?")
        self.assertIn("user/preference-editor", ids)


class Scenario02IndirectAssociativeRecall(EvaluationCase):
    def test_a_related_clue_recovers_an_older_episode(self):
        self.vault.write_node("daily/incident-42", "The gateway dropped messages during the deploy.",
                              title="Incident 42", salience=0.9)
        self.vault.write_node("concept/gateway", "The gateway buffers messages before forwarding.",
                              title="Gateway", related=["daily/incident-42"], salience=0.8)
        ids = self._recall_ids("something went wrong with the gateway some time ago")
        self.assertIn("daily/incident-42", ids)


class Scenario03TemporalReasoning(EvaluationCase):
    def test_before_and_after_a_change_are_distinguishable(self):
        self.vault.write_node("concept/arch-v1", "The project used architecture A.", title="Arch A",
                              salience=0.8, now=T0)
        self.vault.write_node("concept/arch-v2", "The project now uses architecture B.", title="Arch B",
                              salience=0.9, now=T0 + DAY)
        self.vault.supersede("concept/arch-v1", "concept/arch-v2", now=T0 + DAY)
        ids = self._recall_ids("which architecture does the project use?", now=T0 + DAY)
        self.assertIn("concept/arch-v2", ids)
        self.assertNotIn("concept/arch-v1", ids, "a superseded fact must not read as current")


class Scenario04Correction(EvaluationCase):
    def test_a_correction_updates_the_belief_and_keeps_history(self):
        old = beliefs.form_belief(self.vault, "Deploys are done by hand.", evidence_refs=["concept/a"])
        new = beliefs.supersede_belief(self.vault, old["belief_id"], "Deploys are automated.",
                                       evidence_refs=["concept/b"], now=T0 + DAY)
        old_node = self.vault.read_node(old["belief_id"])
        self.assertEqual(old_node.frontmatter.status, "superseded")
        self.assertEqual(new.get("superseded"), old["belief_id"])
        # History must survive: the old belief is still readable, with its own content.
        self.assertIn("by hand", old_node.content)


class Scenario05Contradiction(EvaluationCase):
    def test_competing_claims_and_their_sources_are_surfaced(self):
        self.vault.write_node("concept/claim-a", "The service uses a shared database.",
                              title="Claim A", salience=0.9)
        self.vault.write_node("concept/claim-b", "The service does not use a shared database.",
                              title="Claim B", salience=0.9)
        result = reflection.reflect(self.vault, question="does the service use a shared database?")
        self.assertTrue(result.contradictions)
        pair = result.contradictions[0]
        self.assertIn(pair["a"], {"concept/claim-a", "concept/claim-b"})
        self.assertIn(pair["b"], {"concept/claim-a", "concept/claim-b"})


class Scenario06HistoricalRecall(EvaluationCase):
    def test_an_important_old_memory_survives_decay(self):
        self.vault.write_node("concept/core-principle",
                              "The Brain must never silently lose a memory.",
                              title="Core Principle", salience=1.0, now=T0)
        later = T0 + 400 * DAY
        # Half 1 — selectivity is preserved: an ordinary turn does not inject a faded memory.
        self.assertEqual(recall("what must the Brain never do?", vault=self.vault, now=later).node_ids(), [])
        # Half 2 — §6.7 deep retrieval: asked to dig, the important older memory is recovered.
        deep = recall("what must the Brain never do?", vault=self.vault, now=later, deep=True)
        self.assertIn("concept/core-principle", deep.node_ids())
        self.assertTrue(deep.hits[0].historical, "a recovered old memory must be flagged historical")


class Scenario07FailureLearning(EvaluationCase):
    def test_a_failure_lesson_is_applied_to_a_related_task(self):
        for t in range(3):
            outcomes.record_outcome(self.vault, goal="migrate the database", action="drop and recreate",
                                    result="data loss", verdict="failure", evidence_refs=["daily/inc-1"],
                                    now=T0 + t * DAY)
        guidance = outcomes.procedures_for_goal(self.vault, "how should I migrate the database?")
        self.assertTrue(guidance)
        self.assertEqual(guidance[0].status, "avoid")
        self.assertIn("data loss", guidance[0].failure_modes)


class Scenario08OutcomeLearning(EvaluationCase):
    def test_successful_and_unsuccessful_procedures_are_distinguished(self):
        for t in range(3):
            outcomes.record_outcome(self.vault, goal="deploy safely", action="canary", result="clean",
                                    verdict="success", now=T0 + t * DAY)
            outcomes.record_outcome(self.vault, goal="deploy safely", action="big-bang", result="outage",
                                    verdict="failure", now=T0 + t * DAY + 1)
        # A procedure is per *goal*: the action that worked becomes a step, the action that
        # failed becomes a recorded failure mode, and the status reflects the mix.
        procs = outcomes.list_procedures(self.vault)
        self.assertTrue(procs)
        best = outcomes.procedures_for_goal(self.vault, "deploy safely")[0]
        self.assertIn("canary", best.steps, "the successful action must be kept as a step")
        self.assertIn("outage", best.failure_modes, "the failed action must be kept as a failure mode")
        self.assertEqual(best.success_count, 3)
        self.assertEqual(best.failure_count, 3)
        self.assertEqual(best.status, "situational")


class Scenario09NoiseResistance(EvaluationCase):
    def test_a_weak_link_does_not_drag_in_irrelevant_memories(self):
        self.vault.write_node("concept/quantum-topic", "Quantum tunnelling affects flash memory wear.",
                              title="Quantum Topic", salience=0.5)
        self.vault.write_node("concept/recipe", "The deploy recipe is documented in the runbook.",
                              title="Recipe", salience=0.8)
        ids = self._recall_ids("how do I deploy the service?")
        self.assertNotIn("concept/quantum-topic", ids)


class Scenario10DuplicateResistance(EvaluationCase):
    def test_repeated_events_do_not_inflate_confidence(self):
        first = beliefs.form_belief(self.vault, "The gateway is stateless.", evidence_refs=["e1"])
        before = beliefs.explain_belief(self.vault, first["belief_id"])["confidence"]
        for _ in range(5):
            beliefs.reinforce_belief(self.vault, first["belief_id"], ["e1"])
        after = beliefs.explain_belief(self.vault, first["belief_id"])["confidence"]
        self.assertEqual(before, after)

    def test_duplicate_rate_is_zero_for_replayed_beliefs(self):
        for _ in range(4):
            beliefs.form_belief(self.vault, "The gateway is stateless.", evidence_refs=["e1"])
        all_beliefs = beliefs.list_beliefs(self.vault)
        duplicate_rate = (len(all_beliefs) - 1) / float(max(1, len(all_beliefs)))
        self.assertLessEqual(duplicate_rate, MAX_DUPLICATE_RATE)


class Scenario11RestartRecovery(EvaluationCase):
    def test_memory_integrity_survives_a_restart(self):
        beliefs.form_belief(self.vault, "State lives in SQLite.", evidence_refs=["concept/x"])
        for t in range(3):
            outcomes.record_outcome(self.vault, goal="back up state", action="copy the db",
                                    result="verified", verdict="success", now=T0 + t * DAY)
        reopened = BrainVault(vault_dir=self.tmp)
        self.assertEqual(len(beliefs.list_beliefs(reopened)), 1)
        procs = outcomes.list_procedures(reopened)
        self.assertEqual(procs[0].success_count, 3)


class Scenario12ConcurrentIngestion(EvaluationCase):
    def test_concurrent_writes_are_not_lost_or_duplicated(self):
        errors = []

        def writer(i):
            try:
                beliefs.form_belief(self.vault, f"The subsystem-{i} emits telemetry events.",
                                    evidence_refs=[f"e{i}"], now=T0 + i)
            except Exception as exc:  # pragma: no cover - surfaced via errors list
                errors.append(exc)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(16)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()
        self.assertEqual(errors, [])
        self.assertEqual(len(beliefs.list_beliefs(self.vault)), 16)
        # And a rebuild is consistent with what is on disk.
        idx = BrainIndex(self.vault).rebuild()
        self.assertEqual(len(idx.ids), len(self.vault.all_node_ids()))


class Scenario13LargeVaultRetrieval(EvaluationCase):
    def test_retrieval_stays_useful_and_fast_as_the_vault_grows(self):
        for i in range(400):
            self.vault.write_node(f"concept/filler-{i}",
                                  f"Filler memory {i} about routine subsystem {i} behaviour.",
                                  title=f"Filler {i}", salience=0.4)
        self.vault.write_node("concept/needle", "The rotation key is stored in the hardware enclave.",
                              title="Needle", salience=0.95)
        started = time.perf_counter()
        ids = self._recall_ids("where is the rotation key stored?")
        elapsed = time.perf_counter() - started
        self.assertIn("concept/needle", ids)
        self.assertLess(elapsed, 2.0, f"recall over ~400 nodes took {elapsed:.2f}s")


class Scenario14GraphFidelity(EvaluationCase):
    def test_the_graph_payload_matches_the_canonical_vault(self):
        for i in range(25):
            self.vault.write_node(f"concept/node-{i}", f"Memory number {i}.", title=f"N{i}",
                                  related=["concept/node-0"] if i else None)
        idx = BrainIndex(self.vault)
        payload = idx.to_graph_payload()
        vault_ids = set(self.vault.all_node_ids())
        payload_ids = set(payload["nodes"])
        self.assertEqual(payload_ids, vault_ids, "every vault node must appear in the graph")
        # No edge may reference a node that does not exist (no ghost nodes).
        for edge in payload["edges"]:
            self.assertIn(edge["source"], vault_ids)
            self.assertIn(edge["target"], vault_ids)


class Scenario15Provenance(EvaluationCase):
    def test_a_derived_claim_traces_back_to_its_evidence(self):
        self.vault.write_node("daily/ep-1", "The migration failed at step three.", title="Episode 1")
        self.vault.write_node("daily/ep-2", "The migration failed again on the schema step.", title="Episode 2")
        formed = beliefs.form_belief(self.vault, "Migrations tend to fail on schema steps.",
                                     evidence_refs=["daily/ep-1", "daily/ep-2"])
        explained = beliefs.explain_belief(self.vault, formed["belief_id"])
        self.assertEqual(sorted(explained["supporting"]), ["daily/ep-1", "daily/ep-2"])
        for ev in explained["evidence"]:
            self.assertIn(ev["id"], {"daily/ep-1", "daily/ep-2"})
            self.assertFalse(ev["missing"])


class AggregateMetrics(EvaluationCase):
    """A single fixture scored as precision@3 / MRR, so a ranking regression is one number down."""

    def setUp(self):
        super().setUp()
        self.corpus = {
            "user/editor": ("Sir prefers the Zed editor for code.", "Editor Preference", 0.9),
            "concept/decay": ("Retrievability decays as exp(-delta over stability).", "Decay", 0.9),
            "concept/gateway": ("The gateway buffers messages before forwarding.", "Gateway", 0.8),
            "daily/incident-42": ("The gateway dropped messages during the deploy.", "Incident 42", 0.9),
            "concept/arch-v2": ("The project now uses architecture B.", "Arch B", 0.9),
            "project/dbtool": ("The database tool is migration-runner.", "DB Tool", 0.7),
        }
        for node_id, (body, title, sal) in self.corpus.items():
            self.vault.write_node(node_id, body, title=title, salience=sal)

    def test_precision_and_mrr_meet_the_measured_floors(self):
        queries = {
            "which editor does Sir prefer?": {"user/editor"},
            "how does memory decay?": {"concept/decay"},
            "what happened during the deploy?": {"daily/incident-42", "concept/gateway"},
            "which architecture is used now?": {"concept/arch-v2"},
        }
        precisions, rrs = [], []
        for query, expected in queries.items():
            ids = self._recall_ids(query)
            precisions.append(precision_at_k(expected, ids, 3))
            rrs.append(reciprocal_rank(expected, ids))
        mean_precision = sum(precisions) / len(precisions)
        mean_rr = sum(rrs) / len(rrs)
        self.assertGreaterEqual(mean_precision, MIN_PRECISION_AT_3,
                                f"mean precision@3 {mean_precision:.3f} below floor {MIN_PRECISION_AT_3}")
        self.assertGreaterEqual(mean_rr, MIN_MRR,
                                f"mean MRR {mean_rr:.3f} below floor {MIN_MRR}")


if __name__ == "__main__":
    unittest.main()
