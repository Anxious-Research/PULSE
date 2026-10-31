"""Integration tests for outcome-driven procedural learning (specs/brain.md §8, §4.3).

These exercise the REAL production entry point — ``agent.brain.outcome_recorder.record_turn_outcome``
— with the same arguments ``agent/turn_finalizer.py`` passes at turn end, proving:

* a completed task turn is recorded and creates a procedure;
* the procedure is retrievable on a later, similarly-worded goal;
* failures/partials are classified from the transcript, not guessed;
* replays are idempotent (a retry cannot inflate the success count);
* turns with insufficient evidence are left alone.

The transcript messages are shaped exactly as the executor writes them (``role: "tool"`` with a
``tool_name``), so the extraction path under test is the one that runs in production.
"""

import tempfile
from pathlib import Path
from unittest import TestCase

from agent.brain.outcomes import Procedure, procedures_for_goal
from agent.brain.outcome_recorder import record_turn_outcome
from agent.brain.vault import BrainVault


class _MockAgent:
    """The minimal surface ``record_turn_outcome`` + ``resolve_settings`` touch."""

    def __init__(self, vault):
        self._brain_vault = vault
        self._brain_settings = None


def _tool_msg(name: str, failed: bool = False):
    """A tool-result message shaped as the executor commits it.

    ``terminal`` failures are exit-code failures (the canonical signal), so the failed payload
    carries ``exit_code``; other tools report ``success``/``error``.
    """
    if failed:
        content = '{"exit_code": 1, "error": "boom"}' if name == "terminal" else '{"success": false, "error": "boom"}'
    else:
        content = '{"exit_code": 0, "output": "ok"}' if name == "terminal" else '{"success": true, "output": "ok"}'
    return {"role": "tool", "tool_name": name, "name": name, "content": content}


def _user_msg(text: str):
    return {"role": "user", "content": text}


def _assistant_msg(text: str = ""):
    return {"role": "assistant", "content": text}


class OutcomeIntegrationTest(TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="brain-outcome-integration-")
        self.vault = BrainVault(vault_dir=Path(self.temp) / "brain")
        self.vault.ensure_vault_structure()
        self.agent = _MockAgent(self.vault)

    # ── the core path ─────────────────────────────────────────────────────────

    def test_successful_task_creates_retrievable_procedure(self):
        """A successful multi-tool turn creates a procedure retrievable on a similar goal."""
        messages = [
            _user_msg("Create a FastAPI user authentication service"),
            _assistant_msg("working"),
            _tool_msg("write_file"),
            _tool_msg("terminal"),
        ]
        result = record_turn_outcome(
            self.agent,
            original_user_message="Create a FastAPI user authentication service",
            final_response="Service created with JWT auth",
            interrupted=False, failed=False,
            turn_exit_reason="text_response(assistant)",
            session_id="sess-1", turn_id="t1", messages=messages,
        )
        self.assertTrue(result.get("recorded"), result)
        self.assertEqual(result["verdict"], "success")

        proc_id = result["procedure_id"]
        node = self.vault.read_node(proc_id)
        self.assertIsNotNone(node)
        self.assertEqual(node.frontmatter.category, "procedure")

        proc = Procedure.from_node(node)
        self.assertIn("fastapi", proc.goal.lower())
        self.assertEqual(proc.success_count, 1)
        self.assertEqual(proc.failure_count, 0)
        self.assertEqual(proc.status, "unproven")  # one success is unproven, not reliable

        # Retrievable on a later, differently-worded goal in the same task class.
        retrieved = procedures_for_goal(self.vault, "Build a FastAPI authentication API", limit=3)
        self.assertGreater(len(retrieved), 0, "procedure not retrievable on a similar goal")
        self.assertEqual(retrieved[0].id, proc_id)

    def test_shared_action_verb_matches_procedure(self):
        """'deploy the app' retrieves the procedure learned from 'deploy via docker'."""
        record_turn_outcome(
            self.agent,
            original_user_message="Deploy via Docker",
            final_response="Deployed",
            interrupted=False, failed=False,
            turn_exit_reason="text_response(assistant)",
            session_id="s-d", turn_id="t1",
            messages=[_user_msg("Deploy via Docker"), _tool_msg("terminal"), _tool_msg("terminal")],
        )
        procs = procedures_for_goal(self.vault, "Deploy the app", limit=3)
        self.assertEqual(len(procs), 1)
        self.assertIn("docker", procs[0].goal.lower())

    def test_failed_task_records_failure(self):
        """A failed turn records failure evidence on the procedure."""
        goal = "Deploy the app to Heroku"
        messages = [_user_msg(goal), _tool_msg("terminal", failed=True), _tool_msg("terminal", failed=True)]
        result = record_turn_outcome(
            self.agent,
            original_user_message=goal, final_response=None,
            interrupted=False, failed=True, turn_exit_reason="unknown",
            session_id="s2", turn_id="t1", messages=messages,
        )
        self.assertTrue(result.get("recorded"))
        self.assertEqual(result["verdict"], "failure")
        proc = Procedure.from_node(self.vault.read_node(result["procedure_id"]))
        self.assertEqual(proc.failure_count, 1)
        self.assertEqual(proc.success_count, 0)

    def test_partial_outcome_then_success_updates_same_procedure(self):
        """A partial then a success update one procedure; counts stay correct."""
        goal = "Fix the database migration script"
        r1 = record_turn_outcome(
            self.agent,
            original_user_message=goal, final_response="progress",
            interrupted=False, failed=False, turn_exit_reason="text_response(assistant)",
            session_id="s3", turn_id="t1",
            messages=[_user_msg(goal), _tool_msg("read_file"), _tool_msg("write_file"), _tool_msg("terminal", failed=True)],
        )
        self.assertTrue(r1.get("recorded"), r1)
        self.assertEqual(r1["verdict"], "partial")
        self.assertTrue(r1.get("created"))

        r2 = record_turn_outcome(
            self.agent,
            original_user_message=goal, final_response="fixed",
            interrupted=False, failed=False, turn_exit_reason="text_response(assistant)",
            session_id="s3", turn_id="t2",
            messages=[_user_msg(goal), _tool_msg("write_file"), _tool_msg("terminal")],
        )
        self.assertTrue(r2.get("recorded"), r2)
        self.assertTrue(r2.get("updated"))
        self.assertEqual(r2["procedure_id"], r1["procedure_id"])

        proc = Procedure.from_node(self.vault.read_node(r1["procedure_id"]))
        self.assertEqual(proc.partial_count, 1)
        self.assertEqual(proc.success_count, 1)
        self.assertEqual(len(proc.outcomes), 2)

    # ── idempotency ────────────────────────────────────────────────────────────

    def test_replay_of_same_turn_is_idempotent(self):
        """Replaying the same turn (same session+turn id) is a no-op."""
        goal = "Run the test suite"
        messages = [_user_msg(goal), _tool_msg("terminal"), _tool_msg("terminal")]
        kwargs = dict(
            original_user_message=goal, final_response="All tests passed",
            interrupted=False, failed=False, turn_exit_reason="text_response(assistant)",
            session_id="s4", turn_id="turn-42", messages=messages,
        )
        r1 = record_turn_outcome(self.agent, **kwargs)
        self.assertTrue(r1.get("recorded"))
        self.assertTrue(r1.get("created"))

        r2 = record_turn_outcome(self.agent, **kwargs)
        self.assertTrue(r2.get("recorded"))
        self.assertTrue(r2.get("duplicate"), "replay should be a no-op")
        self.assertFalse(r2.get("updated"))

        proc = Procedure.from_node(self.vault.read_node(r1["procedure_id"]))
        self.assertEqual(proc.success_count, 1)
        self.assertEqual(len(proc.outcomes), 1)

    # ── sufficiency gates ──────────────────────────────────────────────────────

    def test_single_action_tool_is_not_recorded(self):
        """A single action tool call (1 step) is not a procedure and is not recorded."""
        goal = "Restart the gateway service"
        result = record_turn_outcome(
            self.agent,
            original_user_message=goal, final_response="restarted",
            interrupted=False, failed=False, turn_exit_reason="text_response(assistant)",
            session_id="s5", turn_id="t1",
            messages=[_user_msg(goal), _tool_msg("terminal")],
        )
        self.assertFalse(result.get("recorded"))
        self.assertIn("insufficient", result.get("reason", ""))

    def test_multi_step_action_is_recorded(self):
        """At least two concrete action tool calls form a procedure."""
        goal = "Restart the gateway service"
        result = record_turn_outcome(
            self.agent,
            original_user_message=goal, final_response="restarted",
            interrupted=False, failed=False, turn_exit_reason="text_response(assistant)",
            session_id="s5-multi", turn_id="t1",
            messages=[_user_msg(goal), _tool_msg("terminal"), _tool_msg("terminal")],
        )
        self.assertTrue(result.get("recorded"), result)

    def test_non_procedural_tools_not_recorded(self):
        """Orchestration/meta tools (delegate_task, memory) do not form procedures."""
        goal = "Clean all poisoned prompt files and procedures"
        result = record_turn_outcome(
            self.agent,
            original_user_message=goal, final_response="delegated",
            interrupted=False, failed=False, turn_exit_reason="text_response(assistant)",
            session_id="s-meta", turn_id="t1",
            messages=[_user_msg(goal), _tool_msg("delegate_task")],
        )
        self.assertFalse(result.get("recorded"))

    def test_conversational_and_hinglish_prompts_not_recorded(self):
        """Conversational prose and Hindi/Hinglish instructions must not create procedures."""
        prompts = [
            "ekdum shuru se ek ek karke sab kuch karenge. sabse pehle ab se tum ek commandar ya fir orchestrator kuch bhi samjh lo",
            "perfect ab complete issues jo tumne find kiye hai sabki list or detail me sari problem samjhao",
            "A background fan-out unit you dispatched earlier — 2 subagent(s) — has finished; its consolidated results are below.",
        ]
        for p in prompts:
            result = record_turn_outcome(
                self.agent,
                original_user_message=p, final_response="done",
                interrupted=False, failed=False, turn_exit_reason="text_response(assistant)",
                session_id="s-conv", turn_id="t1",
                messages=[_user_msg(p), _tool_msg("terminal"), _tool_msg("terminal")],
            )
            self.assertFalse(result.get("recorded"), f"Prompt should not be recorded: {p}")

    def test_single_readonly_call_is_not_recorded(self):
        """A single read-only lookup is not a procedure-forming task."""
        result = record_turn_outcome(
            self.agent,
            original_user_message="Validate the config file",
            final_response="here it is",
            interrupted=False, failed=False, turn_exit_reason="text_response(assistant)",
            session_id="s6", turn_id="t1",
            messages=[_user_msg("Validate the config file"), _tool_msg("read_file")],
        )
        self.assertFalse(result.get("recorded"))
        self.assertIn("insufficient evidence", result.get("reason", ""))

    def test_question_is_not_recorded(self):
        """A question is never a task goal, even when it used tools."""
        result = record_turn_outcome(
            self.agent,
            original_user_message="What is a closure in Python?",
            final_response="A closure is...",
            interrupted=False, failed=False, turn_exit_reason="text_response(assistant)",
            session_id="s7", turn_id="t1",
            messages=[_user_msg("What is a closure in Python?"), _tool_msg("web_search"), _tool_msg("web_search")],
        )
        self.assertFalse(result.get("recorded"))
        self.assertIn("not a task goal", result.get("reason", ""))

    def test_interrupted_turn_is_not_recorded(self):
        result = record_turn_outcome(
            self.agent,
            original_user_message="Build a web scraper",
            final_response=None, interrupted=True, failed=False,
            turn_exit_reason="interrupted",
            session_id="s8", turn_id="t1",
            messages=[_user_msg("Build a web scraper"), _tool_msg("write_file")],
        )
        self.assertFalse(result.get("recorded"))
        self.assertIn("indeterminate", result.get("reason", ""))

    def test_acknowledgement_is_not_a_task(self):
        result = record_turn_outcome(
            self.agent,
            original_user_message="ok thanks",
            final_response="you're welcome", interrupted=False, failed=False,
            turn_exit_reason="text_response(assistant)",
            session_id="s9", turn_id="t1",
            messages=[_user_msg("ok thanks"), _tool_msg("terminal"), _tool_msg("terminal")],
        )
        self.assertFalse(result.get("recorded"))
        self.assertIn("not a task goal", result.get("reason", ""))

    # ── ranking ────────────────────────────────────────────────────────────────

    def test_procedure_retrieval_ranks_reliable_over_avoid(self):
        """Reliable outranks situational outranks avoid; avoid is still returned."""
        # Reliable: 3 successes.
        for i in range(3):
            record_turn_outcome(
                self.agent,
                original_user_message="Deploy via Docker", final_response="ok",
                interrupted=False, failed=False, turn_exit_reason="text_response(assistant)",
                session_id=f"a{i}", turn_id="t",
                messages=[_user_msg("Deploy via Docker"), _tool_msg("terminal"), _tool_msg("terminal")],
            )
        # Avoid: 3 failures.
        for i in range(3):
            record_turn_outcome(
                self.agent,
                original_user_message="Deploy via FTP", final_response=None,
                interrupted=False, failed=True, turn_exit_reason="unknown",
                session_id=f"c{i}", turn_id="t",
                messages=[_user_msg("Deploy via FTP"), _tool_msg("terminal", failed=True), _tool_msg("terminal", failed=True)],
            )
        procs = procedures_for_goal(self.vault, "Deploy the app", limit=10)
        self.assertGreaterEqual(len(procs), 2)
        self.assertEqual(procs[0].status, "reliable")
        self.assertIn("docker", procs[0].goal.lower())
        self.assertEqual(procs[-1].status, "avoid")

    # ── wiring guard ───────────────────────────────────────────────────────────

    def test_production_finalizer_calls_recorder(self):
        """The turn finalizer must actually invoke the recorder (guards deleted wiring)."""
        finalizer = Path(__file__).resolve().parents[1] / "agent" / "turn_finalizer.py"
        source = finalizer.read_text(encoding="utf-8")
        self.assertIn("record_turn_outcome", source, "outcome recorder is not wired into the turn finalizer")
        self.assertIn("from agent.brain.outcome_recorder import record_turn_outcome", source)
