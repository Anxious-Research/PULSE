"""Verification semantics tests — tool success is not objective success (v4 §7.2, §7.5-D).

The claim under test: when every tool call returns success but no independent verifier
confirmed the user's objective, the recorded outcome must be explicitly unverified — never
a verified success. And when a caller DOES supply a verifier verdict, that verdict is what
the record carries.

These exercise the production entry point (``record_turn_outcome`` → ``record_outcome`` →
the ``procedure/`` node), not internal helpers.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

from agent.brain.outcome_recorder import record_turn_outcome
from agent.brain.outcomes import (
    VERIFICATION_TOOL_SUCCESS,
    VERIFICATION_UNVERIFIED,
    VERIFICATION_VERIFIED,
    find_procedure,
)
from agent.brain.parser import decode_record
from agent.brain.vault import BrainVault


class _MockAgent:
    """The minimal surface record_turn_outcome + resolve_settings touch."""

    def __init__(self, vault):
        self._brain_vault = vault
        self._brain_settings = None


def _tool_msg(name: str, failed: bool = False):
    if failed:
        content = '{"exit_code": 1, "error": "boom"}' if name == "terminal" else '{"success": false, "error": "boom"}'
    else:
        content = '{"exit_code": 0, "output": "ok"}' if name == "terminal" else '{"success": true, "output": "ok"}'
    return {"role": "tool", "tool_name": name, "name": name, "content": content}


def _user_msg(text: str):
    return {"role": "user", "content": text}


def _assistant_msg(text: str = ""):
    return {"role": "assistant", "content": text}


def _outcomes_of(vault, goal: str):
    proc = find_procedure(vault, goal)
    assert proc is not None, "procedure must exist"
    node = vault.read_node(proc.node_id if hasattr(proc, "node_id") else proc.id)
    extra = node.frontmatter.extra or {}
    return [decode_record(t) for t in (extra.get("outcomes") or [])], extra


class VerificationSemanticsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.mkdtemp(prefix="brain-verification-")
        self.vault = BrainVault(vault_dir=Path(self.temp) / "brain")
        self.vault.ensure_vault_structure()
        self.agent = _MockAgent(self.vault)

    def tearDown(self):
        shutil.rmtree(self.temp, ignore_errors=True)

    def _record_green_turn(self, *, goal, verified=None):
        messages = [
            _user_msg(goal),
            _assistant_msg("working"),
            _tool_msg("write_file"),
            _tool_msg("terminal"),
        ]
        return record_turn_outcome(
            self.agent,
            original_user_message=goal,
            final_response="Done.",
            interrupted=False,
            failed=False,
            turn_exit_reason="text_response(complete)",
            session_id="sess-v",
            turn_id="t1",
            messages=messages,
            verified=verified,
        )

    # ── §7.5 Experiment D — objective failure despite successful tools ────────

    def test_all_green_turn_is_not_a_verified_success(self):
        """Every tool succeeded; no verifier ran → the record must NOT claim verified success.

        This is Experiment D: a turn can complete with all tools green while the user's actual
        objective is not achieved. The recorder cannot know the objective outcome, so it must
        record the tool-level truth and leave verification explicitly unverified.
        """
        report = self._record_green_turn(goal="Deploy the payments service to production")
        self.assertTrue(report.get("recorded"), report)
        self.assertEqual(report.get("verdict"), "success")
        self.assertEqual(report.get("verification"), VERIFICATION_TOOL_SUCCESS)
        self.assertNotEqual(report.get("verification"), VERIFICATION_VERIFIED)

        outcomes, extra = _outcomes_of(self.vault, "Deploy the payments service to production")
        self.assertEqual(outcomes[-1].get("verification"), VERIFICATION_TOOL_SUCCESS)
        # The verified-success counter must stay empty — no verifier ran.
        self.assertFalse(extra.get("verified_success_count"))

    def test_supplied_verifier_marks_verified(self):
        """When a caller ran an independent verifier and it passed, the record says verified."""
        report = self._record_green_turn(goal="Fix the failing unit tests", verified=True)
        self.assertTrue(report.get("recorded"), report)
        self.assertEqual(report.get("verification"), VERIFICATION_VERIFIED)

        outcomes, extra = _outcomes_of(self.vault, "Fix the failing unit tests")
        self.assertEqual(outcomes[-1].get("verification"), VERIFICATION_VERIFIED)
        self.assertEqual(int(extra.get("verified_success_count") or 0), 1)

    def test_supplied_failing_verifier_marks_unverified(self):
        """A verifier that ran and did NOT pass leaves the record unverified, not success-trusted."""
        report = self._record_green_turn(goal="Migrate the production database", verified=False)
        self.assertTrue(report.get("recorded"), report)
        self.assertEqual(report.get("verification"), VERIFICATION_UNVERIFIED)

    def test_failed_tools_are_unverified(self):
        """Tool errors → partial verdict, unverified (nothing established the objective)."""
        messages = [
            _user_msg("Refactor the auth module"),
            _assistant_msg("working"),
            _tool_msg("write_file"),
            _tool_msg("terminal", failed=True),
        ]
        report = record_turn_outcome(
            self.agent,
            original_user_message="Refactor the auth module",
            final_response="Had trouble.",
            interrupted=False, failed=False,
            turn_exit_reason="text_response(complete)",
            session_id="sess-f", turn_id="t1", messages=messages,
        )
        self.assertTrue(report.get("recorded"), report)
        self.assertEqual(report.get("verification"), VERIFICATION_UNVERIFIED)

    def test_verification_roundtrips_the_record(self):
        """The verification field survives the vault's frontmatter round trip (decode_record)."""
        self._record_green_turn(goal="Run the weekly backup job", verified=True)
        outcomes, _ = _outcomes_of(self.vault, "Run the weekly backup job")
        self.assertEqual(outcomes[-1].get("verification"), VERIFICATION_VERIFIED)

    def test_default_verification_is_unverified(self):
        """record_outcome's default must be unverified — a bare call cannot claim verification."""
        from agent.brain.outcomes import record_outcome

        record_outcome(self.vault, goal="Standalone verification default check",
                       action="used terminal", result="all 1 tool call(s) succeeded",
                       verdict="success")
        outcomes, _ = _outcomes_of(self.vault, "Standalone verification default check")
        self.assertEqual(outcomes[-1].get("verification"), VERIFICATION_UNVERIFIED)


if __name__ == "__main__":
    unittest.main()
