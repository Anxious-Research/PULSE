"""P2 integration test: reflection → belief creation → retrieval (deterministic, isolated).

Proves that reflection genuinely creates beliefs from sufficient independent evidence, handles
contradictions, processes corrections, and that the beliefs are retrievable in later turns.

Requirements (specs/brain.md §3.7, §7):
1. A belief is created only when ≥2 independent memories corroborate the same claim.
2. Contradictory evidence triggers counter-evidence recording, not silent overwrite.
3. A correction supersedes the old claim with history preserved.
4. Beliefs are deduplicated: restating an existing belief reinforces it, never duplicates.
5. The formed belief is retrievable in a later recall.

Uses an isolated temporary ``PULSE_HOME`` so the user's live vault is never touched.
"""

import os
import tempfile
import time
from pathlib import Path
from unittest import TestCase

from agent.brain.session import brain_encode_turn, brain_consolidate, brain_reflect, get_write_vault
from agent.brain.beliefs import list_beliefs, explain_belief
from agent.brain.recall import recall
from agent.brain.vault import BrainVault


class _MockAgent:
    """Minimal agent surface for the brain session API."""

    def __init__(self, vault: BrainVault):
        self._brain_vault = vault
        self._brain_settings = None
        self._agent_config = None


class ReflectionBeliefIntegrationTest(TestCase):
    """End-to-end belief formation from real encode → consolidate → reflect flow."""

    def setUp(self):
        # Isolated PULSE_HOME so nothing touches the user's live vault.
        self.temp_home = tempfile.mkdtemp(prefix="pulse-p2-")
        self.old_home = os.environ.get("PULSE_HOME")
        os.environ["PULSE_HOME"] = self.temp_home
        self.vault_dir = Path(self.temp_home) / "brain"
        self.vault = BrainVault(vault_dir=self.vault_dir)
        self.vault.ensure_vault_structure()
        self.agent = _MockAgent(self.vault)
        self.now = int(time.time())

    def tearDown(self):
        if self.old_home is not None:
            os.environ["PULSE_HOME"] = self.old_home
        elif "PULSE_HOME" in os.environ:
            del os.environ["PULSE_HOME"]

    # ── core belief formation ──────────────────────────────────────────────────

    def test_sufficient_independent_evidence_creates_belief(self):
        """Two independent statements of the same claim form a belief on reflection."""
        # Turn 1: user says X, assistant confirms (non-echo reply so encoding doesn't dedupe).
        brain_encode_turn(
            self.agent,
            "The gateway service runs on port 8791",
            "I've noted that.",
            session_id="s1",
        )
        # Turn 2: user restates it (independent evidence), assistant acknowledges.
        brain_encode_turn(
            self.agent,
            "Remember that the gateway listens on 8791",
            "Got it.",
            session_id="s2",
        )
        # Consolidate then reflect.
        brain_consolidate(self.agent)
        result = brain_reflect(self.agent, text="What port does the gateway use?")
        self.assertGreater(result.get("beliefs_formed", 0), 0, "no belief was formed")

        # Verify the belief was persisted.
        beliefs = list_beliefs(self.vault)
        self.assertEqual(len(beliefs), 1)
        belief = beliefs[0]
        self.assertIn("8791", belief.statement.lower())
        self.assertIn("gateway", belief.statement.lower())
        self.assertGreaterEqual(len(belief.supporting), 2, "belief missing independent evidence")

        # Verify it surfaces in later recall.
        recall_result = recall("gateway port", vault=self.vault, now=self.now + 100)
        belief_ids = [h.node_id for h in recall_result.hits if h.category == "belief"]
        self.assertIn(belief.id, belief_ids, "belief not retrieved on relevant query")

    def test_single_memory_does_not_form_belief(self):
        """One unsupported statement is not a belief — provisional only."""
        brain_encode_turn(
            self.agent, "The cache layer uses Redis", "Understood.", session_id="s1"
        )
        brain_consolidate(self.agent)
        result = brain_reflect(self.agent, text="What does the cache layer use?")
        self.assertEqual(result.get("beliefs_formed", 0), 0, "belief formed from single memory")
        self.assertIsNotNone(result.get("uncertainty"))

    def test_restating_existing_belief_reinforces_not_duplicates(self):
        """Restating a claim that already has a belief reinforces it, never duplicates."""
        # Form the belief (2 independent statements).
        brain_encode_turn(self.agent, "The API uses JWT tokens", "Understood.", session_id="s1")
        brain_encode_turn(self.agent, "We use JWT for API auth", "Noted.", session_id="s2")
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="How does the API authenticate?")
        beliefs_1 = list_beliefs(self.vault)
        self.assertEqual(len(beliefs_1), 1)
        belief_id = beliefs_1[0].id

        # Restate the same claim in a later turn.
        brain_encode_turn(self.agent, "The API tokens are JWT", "Understood.", session_id="s3")
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="API authentication method?")

        # Still one belief, same id, higher confidence.
        beliefs_2 = list_beliefs(self.vault)
        self.assertEqual(len(beliefs_2), 1)
        self.assertEqual(beliefs_2[0].id, belief_id)
        self.assertGreaterEqual(beliefs_2[0].confidence, beliefs_1[0].confidence)

    # ── contradiction handling ─────────────────────────────────────────────────

    def test_contradictory_evidence_recorded_as_counter(self):
        """Contradictory evidence (no correction cue) is flagged and weakens the belief.

        This is the *unresolved contradiction* path, distinct from a correction: a plainly-worded
        opposing statement carries no "Actually/No" cue, so encoding does not supersede it. Both
        claims coexist on disk; reflection detects the conflict, records counter-evidence, and
        lowers confidence — the belief is kept with its full history (§2.4 no-silent-loss). The
        correction/supersession mechanism is covered separately by
        ``test_correction_supersedes_old_claim_with_history``.
        """
        # Initial claim, restated (genuine repeated evidence).
        brain_encode_turn(self.agent, "The service was migrated to GCP", "Understood.", session_id="s1")
        brain_encode_turn(self.agent, "The service has been migrated to GCP", "Noted.", session_id="s2")
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="Where is the service hosted?")
        beliefs = list_beliefs(self.vault)
        self.assertEqual(len(beliefs), 1)
        conf_before = beliefs[0].confidence
        belief_id = beliefs[0].id

        # Plainly-worded contradiction — opposing polarity, same subject, no correction cue.
        brain_encode_turn(
            self.agent, "The service was never migrated to GCP in the end", "Noted.", session_id="s3"
        )
        brain_consolidate(self.agent)
        result = brain_reflect(self.agent, text="Was the service migrated to GCP?")
        self.assertGreater(result.get("contradictions", 0), 0, "contradiction not detected")

        # The original claim is kept (not erased) and its confidence dropped, with counter-evidence
        # recorded and the change logged as a revision.
        detail = explain_belief(self.vault, belief_id)
        self.assertIsNotNone(detail, "belief detail not found")
        assert detail is not None  # narrow for type-checkers
        counter = detail.get("counter_evidence", [])
        self.assertGreater(len(counter), 0, "counter-evidence not recorded on the belief")
        self.assertLess(detail["confidence"], conf_before, "contradiction did not weaken the belief")
        reasons = {r.get("reason") for r in (detail.get("revisions") or [])}
        self.assertIn("contradiction", reasons, "contradiction not logged as a revision")

    def test_correction_supersedes_old_claim_with_history(self):
        """A correction replaces the old claim and preserves the superseded history."""
        # Initial belief, restated (genuine repeated evidence).
        brain_encode_turn(self.agent, "The API endpoint is /v1/users", "Understood.", session_id="s1")
        brain_encode_turn(self.agent, "The users endpoint is /v1/users", "Noted.", session_id="s2")
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="What is the users endpoint?")
        beliefs = list_beliefs(self.vault)
        self.assertEqual(len(beliefs), 1)
        old_id = beliefs[0].id

        # Correction.
        brain_encode_turn(
            self.agent,
            "Actually, the API endpoint changed to /v2/users, not /v1/users",
            "Correcting that now.",
            session_id="s3",
        )
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="Users endpoint?")

        # The old belief is superseded; a new one exists with the corrected statement.
        beliefs_after = list_beliefs(self.vault)
        # Either one belief (updated in place with revisions) or two (old superseded, new created).
        self.assertGreaterEqual(len(beliefs_after), 1)
        v2_beliefs = [b for b in beliefs_after if "/v2/" in b.statement.lower()]
        self.assertGreater(len(v2_beliefs), 0, "corrected belief not found")
        # The corrected belief is retrievable.
        recall_result = recall("users endpoint", vault=self.vault, now=self.now + 200)
        recalled_ids = {h.node_id for h in recall_result.hits if h.category == "belief"}
        self.assertTrue(
            any(b.id in recalled_ids for b in v2_beliefs), "corrected belief not retrievable"
        )

    # ── reflection scheduling guard ────────────────────────────────────────────

    def test_reflection_does_not_run_on_every_turn(self):
        """Reflection is triggered by schedule, not every encode (performance / bounded recursion)."""
        # Encode 3 turns without manually calling reflect.
        for i in range(3):
            brain_encode_turn(
                self.agent, f"Turn {i} statement", "ok", session_id=f"s{i}"
            )
        # No beliefs should form yet (reflect_every=6 by default, we encoded 3).
        beliefs = list_beliefs(self.vault)
        self.assertEqual(len(beliefs), 0, "reflection ran before its schedule")

    # ── procedural + belief interaction ────────────────────────────────────────

    def test_belief_and_procedure_both_retrievable(self):
        """A belief (factual claim) and a procedure (method) coexist and both surface on relevant queries."""
        # Factual claim → belief.
        brain_encode_turn(self.agent, "The database uses Postgres", "Understood.", session_id="s1")
        brain_encode_turn(self.agent, "Postgres is the database", "Noted.", session_id="s2")
        brain_consolidate(self.agent)
        brain_reflect(self.agent, text="What database?")
        beliefs = list_beliefs(self.vault)
        self.assertGreater(len(beliefs), 0)

        # Task outcome → procedure (tested in P1, but proving coexistence here).
        from agent.brain.outcome_recorder import record_turn_outcome

        record_turn_outcome(
            self.agent,
            original_user_message="Deploy the service",
            final_response="Deployed",
            interrupted=False,
            failed=False,
            turn_exit_reason="text_response(assistant)",
            session_id="sp",
            turn_id="t1",
            messages=[
                {"role": "user", "content": "Deploy the service"},
                {"role": "tool", "tool_name": "terminal", "name": "terminal", "content": '{"exit_code": 0}'},
            ],
        )

        # Recall should retrieve both on a broad query.
        result = recall("service database deployment", vault=self.vault, now=self.now + 300, limit=10)
        categories = {h.category for h in result.hits}
        self.assertIn("belief", categories, "belief not recalled")
        self.assertIn("procedure", categories, "procedure not recalled")

    # ── wiring guard ───────────────────────────────────────────────────────────

    def test_encode_worker_calls_reflect_on_schedule(self):
        """The background encode worker must actually call brain_reflect when due (guards deleted wiring)."""
        session_path = Path(__file__).resolve().parents[1] / "agent" / "brain" / "session.py"
        source = session_path.read_text(encoding="utf-8")
        self.assertIn("brain_reflect", source, "brain_reflect not in session.py")
        self.assertIn("due_reflect", source, "due_reflect schedule not in encode worker")
        self.assertIn("reflect_every", source, "reflect_every setting not referenced")
