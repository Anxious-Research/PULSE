"""P4 inspector: full evidence provenance for a brain node, read from the canonical vault model.

Proves the inspector surfaces, for the three node kinds, exactly what the UI promises on select:
memory content, type, source evidence, timestamps, confidence justification, provenance, and
revision/supersession history — all from agent.brain.inspect, nothing invented. Uses an isolated
temporary vault; the user's real vault is never touched.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from unittest import TestCase

from agent.brain.session import brain_encode_turn, brain_consolidate, brain_reflect
from agent.brain.beliefs import list_beliefs
from agent.brain.inspect import inspect_node
from agent.brain.outcomes import record_outcome
from agent.brain.vault import BrainVault


class _MockAgent:
    def __init__(self, vault):
        self._brain_vault = vault
        self._brain_settings = None
        self._agent_config = None


class InspectNodeTest(TestCase):
    def setUp(self):
        self.temp_home = tempfile.mkdtemp(prefix="pulse-p4-")
        self.old_home = os.environ.get("PULSE_HOME")
        os.environ["PULSE_HOME"] = self.temp_home
        self.vault = BrainVault(vault_dir=Path(self.temp_home) / "brain")
        self.vault.ensure_vault_structure()
        self.agent = _MockAgent(self.vault)
        self.now = int(time.time())

    def tearDown(self):
        if self.old_home is None:
            os.environ.pop("PULSE_HOME", None)
        else:
            os.environ["PULSE_HOME"] = self.old_home

    def test_missing_node_is_not_an_error(self):
        res = inspect_node(self.vault, "concept/does-not-exist")
        self.assertFalse(res["ok"])
        self.assertIn("not found", res["message"])

    def test_memory_node_carries_content_type_and_timestamps(self):
        brain_encode_turn(self.agent, "The cache layer uses Redis", "Noted.", session_id="s1")
        node_id = next(n.id for n in self.vault.list_all_nodes() if n.frontmatter.category == "concept")
        d = inspect_node(self.vault, node_id)
        self.assertTrue(d["ok"])
        self.assertEqual(d["kind"], "memory")
        self.assertIn("redis", d["content"].lower())
        self.assertEqual(d["category"], "concept")
        self.assertIsNotNone(d["timestamps"]["created_at"])

    def test_belief_carries_evidence_and_confidence_justification(self):
        # Number-anchored paraphrases cluster into one belief (proven in the P2 suite).
        brain_encode_turn(self.agent, "The gateway service runs on port 8791", "I've noted that.", session_id="s1")
        brain_encode_turn(self.agent, "Remember the gateway listens on 8791", "Got it.", session_id="s2")
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="What port does the gateway use?")
        beliefs = list_beliefs(self.vault)
        self.assertEqual(len(beliefs), 1)
        d = inspect_node(self.vault, beliefs[0].id)
        self.assertEqual(d["kind"], "belief")
        self.assertGreaterEqual(len(d["evidence"]), 2, "belief should expose its supporting evidence")
        self.assertTrue(all("label" in e for e in d["evidence"]))
        self.assertIn("inferred from", d["confidence_justification"])
        self.assertIn("belief_status", d)

    def test_belief_counter_evidence_and_revisions_are_exposed(self):
        brain_encode_turn(self.agent, "The service was migrated to GCP", "Understood.", session_id="s1")
        brain_encode_turn(self.agent, "The service has been migrated to GCP", "Noted.", session_id="s2")
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="Where is the service hosted?")
        belief_id = list_beliefs(self.vault)[0].id
        brain_encode_turn(self.agent, "The service was never migrated to GCP in the end", "Noted.", session_id="s3")
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="Was the service migrated to GCP?")
        d = inspect_node(self.vault, belief_id)
        self.assertGreater(len(d["counter_evidence"]), 0, "counter-evidence not exposed")
        reasons = {r.get("reason") for r in d["revisions"]}
        self.assertIn("contradiction", reasons, "revision history not exposed")

    def test_procedure_carries_outcomes_applicability_and_limitations(self):
        # A failed attempt records the failure mode; a later success proves the fixed method —
        # so the procedure carries both its known limitation and its supporting experience.
        record_outcome(
            self.vault,
            goal="Deploy the web service",
            action="in-place restart",
            result="in-place restart causes 502s",
            verdict="failure",
            now=self.now,
        )
        record_outcome(
            self.vault,
            goal="Deploy the web service",
            action="blue-green deploy via the pipeline",
            result="deployment succeeded with zero downtime",
            verdict="success",
            now=self.now + 10,
        )
        proc_id = next(n.id for n in self.vault.list_all_nodes() if n.frontmatter.category == "procedure")
        d = inspect_node(self.vault, proc_id)
        self.assertEqual(d["kind"], "procedure")
        self.assertEqual(d["goal"], "Deploy the web service")
        self.assertIn("in-place restart causes 502s", d["failure_modes"])  # known limitations
        self.assertIn("in-place restart", d["discouraged"])          # action not to repeat
        self.assertIn("preconditions", d)                            # applicability conditions key present
        self.assertGreaterEqual(len(d["outcomes"]), 2)               # supporting experience
        self.assertIn("success rate", d["confidence_justification"])

    def test_superseded_memory_links_to_its_replacement(self):
        brain_encode_turn(self.agent, "The endpoint is /v1/users", "Understood.", session_id="s1")
        brain_encode_turn(self.agent, "The users endpoint is /v1/users", "Noted.", session_id="s2")
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="What is the users endpoint?")
        old_id = list_beliefs(self.vault)[0].id
        brain_encode_turn(self.agent, "Actually, the endpoint changed to /v2/users, not /v1/users",
                          "Correcting that.", session_id="s3")
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="Users endpoint?")
        d = inspect_node(self.vault, old_id)
        # Superseded node keeps its history and points at what replaced it.
        self.assertEqual(d["status"], "superseded")
        self.assertIn("superseded_by", d)
        self.assertIn("label", d["superseded_by"])


if __name__ == "__main__":
    import unittest
    unittest.main()
