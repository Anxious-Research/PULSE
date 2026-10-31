"""Tests for background review prompt leakage immunization and outcome recorder guard."""

from __future__ import annotations

import json
import os
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import agent.background_review as bg
from agent.brain.outcome_recorder import record_turn_outcome
from pulse_cli.plugins import (
    clear_thread_pre_call_hook,
    set_thread_pre_call_hook,
    _dispatch_pre_tool_call_hooks,
)
from tools.memory_tool import memory_tool
from tools.skill_provenance import reset_current_write_origin, set_current_write_origin


class TestLeakPatternMatching:
    def test_explicit_phrases_detected(self):
        targets = [
            "Target shape of the library: CLASS-LEVEL skills",
            "A user-preference lesson lives in exactly ONE place: the skill that governs the task",
            "user-preference lesson lives in exactly one place: USER.md only for cross-cutting",
            "Either say 'Nothing to save', or capture the alternative",
            'Either say "Nothing to save" and stop',
            "If a tool failed because of setup state, capture the FIX",
            "• Always-on rules (standing user preferences, gates that apply to every instance of the task) live in SKILL.md itself, whole",
            "One fact goes to ONE store, never both",
            "TWO distinct stores — pick the right one for each fact",
            "USER.md (memory tool, target='user'): who the user is",
            "MEMORY.md (memory tool, target='memory'): facts about the ENVIRONMENT",
            "What a skill IS: the instructions for doing a class of task",
            "Environment-dependent failures: missing binaries, fresh-install errors",
            "Procedure first: the steps in the order they are done",
            "A pitfall is a generalizable rule + one clause of WHY",
            "Review the full conversation above, including the assistant's completed answer",
        ]
        for text in targets:
            assert bg.is_review_prompt_leak(text), f"Failed to detect leak in: {text!r}"

    def test_benign_user_facts_not_blocked(self):
        benign = [
            "User prefers Python 3.14 for development.",
            "User communicates in Hindi / Hinglish.",
            "Database host is postgres.internal:5432.",
            "Use dark mode for frontend UI components.",
            "The user's favorite editor is Neovim.",
            "Deploy script lives at /scripts/deploy.sh.",
        ]
        for text in benign:
            assert not bg.is_review_prompt_leak(text), f"False positive leak detection on: {text!r}"


class TestCheckMemoryPromptLeak:
    def test_single_op_add_leak(self):
        args = {
            "action": "add",
            "target": "user",
            "content": "A user-preference lesson lives in exactly ONE place",
        }
        err = bg.check_memory_prompt_leak(args)
        assert err is not None
        assert "matches background review prompt instructions" in err

    def test_single_op_replace_leak(self):
        args = {
            "action": "replace",
            "target": "memory",
            "old_text": "something",
            "new_text": "Target shape of the library: CLASS-LEVEL skills",
        }
        err = bg.check_memory_prompt_leak(args)
        assert err is not None
        assert "matches background review prompt instructions" in err

    def test_batch_op_leak(self):
        args = {
            "action": "batch",
            "target": "user",
            "operations": [
                {"action": "add", "content": "Always-on rules live in SKILL.md itself, whole"}
            ],
        }
        err = bg.check_memory_prompt_leak(args)
        assert err is not None
        assert "matches background review prompt instructions" in err

    def test_benign_memory_allowed(self):
        args = {
            "action": "add",
            "target": "user",
            "content": "User prefers pytest for testing.",
        }
        assert bg.check_memory_prompt_leak(args) is None


class TestGuardedMemoryStore:
    def test_add_rejects_prompt_leak(self):
        real_store = MagicMock()
        guarded = bg._GuardedReviewMemoryStore(real_store)

        res = guarded.add("user", "A user-preference lesson lives in exactly ONE place")
        assert res["success"] is False
        assert "matches review prompt instructions" in res["error"]
        real_store.add.assert_not_called()

    def test_add_allows_clean_content(self):
        real_store = MagicMock()
        real_store.add.return_value = {"success": True}
        guarded = bg._GuardedReviewMemoryStore(real_store)

        res = guarded.add("user", "User prefers dark mode.")
        assert res["success"] is True
        real_store.add.assert_called_once_with("user", "User prefers dark mode.")

    def test_replace_rejects_prompt_leak(self):
        real_store = MagicMock()
        guarded = bg._GuardedReviewMemoryStore(real_store)

        res = guarded.replace("memory", "old", "CLASS-LEVEL skills")
        assert res["success"] is False
        assert "matches review prompt instructions" in res["error"]
        real_store.replace.assert_not_called()

    def test_batch_rejects_prompt_leak(self):
        real_store = MagicMock()
        guarded = bg._GuardedReviewMemoryStore(real_store)

        ops = [
            {"action": "add", "content": "If a tool failed because of setup state, capture the FIX"}
        ]
        res = guarded.batch("memory", ops)
        assert res["success"] is False
        assert "matches review prompt instructions" in res["error"]
        real_store.batch.assert_not_called()


class TestMemoryToolBackgroundLeakGuard:
    def test_memory_tool_rejects_leak_in_background_review(self, tmp_path, monkeypatch):
        from tools.memory_tool_store import MemoryStore

        monkeypatch.setenv("PULSE_HOME", str(tmp_path / "home"))
        monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
        store = MemoryStore(memory_char_limit=1000, user_char_limit=1000)
        store.load_from_disk()

        token = set_current_write_origin("background_review")
        try:
            raw = memory_tool(
                action="add",
                target="user",
                content="A user-preference lesson lives in exactly ONE place",
                store=store,
            )
            res = json.loads(raw)
            assert res["success"] is False
            assert "matches background review prompt instructions" in res["error"]
            assert len(store._entries_for("user")) == 0

            # Valid content succeeds
            raw_valid = memory_tool(
                action="add",
                target="user",
                content="User prefers fast responses.",
                store=store,
            )
            res_valid = json.loads(raw_valid)
            assert res_valid["success"] is True
            assert len(store._entries_for("user")) == 1
        finally:
            reset_current_write_origin(token)


class TestPreCallHookBlocking:
    def test_pre_call_hook_blocks_leaked_memory_call(self):
        def _hook(tool_name: str, args: dict):
            if tool_name == "memory":
                return bg.check_memory_prompt_leak(args)
            return None

        set_thread_pre_call_hook(_hook)
        try:
            block_msg, _ = _dispatch_pre_tool_call_hooks(
                "memory",
                {"action": "add", "content": "Target shape of the library: CLASS-LEVEL skills"},
            )
            assert block_msg is not None
            assert "matches background review prompt instructions" in block_msg

            # Benign call passes
            clean_msg, _ = _dispatch_pre_tool_call_hooks(
                "memory",
                {"action": "add", "content": "User prefers pytest"},
            )
            assert clean_msg is None
        finally:
            clear_thread_pre_call_hook()


class TestOutcomeRecorderReviewForkExclusion:
    def _agent(self, **attrs):
        a = SimpleNamespace(
            session_id="s1",
            _pulse_profile="default",
            valid_tool_names=["read_file"],
            _memory_write_origin=attrs.get("_memory_write_origin"),
            _turn_origin=attrs.get("_turn_origin"),
            _memory_write_context=attrs.get("_memory_write_context"),
            skip_background_review=attrs.get("skip_background_review", False),
        )
        return a

    def test_memory_write_origin_skips(self):
        agent = self._agent(_memory_write_origin="background_review")
        res = record_turn_outcome(
            agent,
            original_user_message="build a web crawler",
            final_response="done",
            interrupted=False,
            failed=False,
            turn_exit_reason="completed",
        )
        assert res["recorded"] is False
        assert res["reason"] == "background review fork"

    def test_turn_origin_skips(self):
        agent = self._agent(_turn_origin="background_review")
        res = record_turn_outcome(
            agent,
            original_user_message="build a web crawler",
            final_response="done",
            interrupted=False,
            failed=False,
            turn_exit_reason="completed",
        )
        assert res["recorded"] is False
        assert res["reason"] == "background review fork"

    def test_turn_origin_arg_skips(self):
        agent = self._agent()
        res = record_turn_outcome(
            agent,
            original_user_message="build a web crawler",
            final_response="done",
            interrupted=False,
            failed=False,
            turn_exit_reason="completed",
            turn_origin="background_review",
        )
        assert res["recorded"] is False
        assert res["reason"] == "background review fork"

    def test_contextvar_is_background_review_skips(self):
        agent = self._agent()
        token = set_current_write_origin("background_review")
        try:
            res = record_turn_outcome(
                agent,
                original_user_message="build a web crawler",
                final_response="done",
                interrupted=False,
                failed=False,
                turn_exit_reason="completed",
            )
            assert res["recorded"] is False
            assert res["reason"] == "background review fork"
        finally:
            reset_current_write_origin(token)

    def test_review_harness_prompt_skips(self):
        agent = self._agent()
        res = record_turn_outcome(
            agent,
            original_user_message="Review the full conversation above, including the assistant's completed answer",
            final_response="Nothing to save.",
            interrupted=False,
            failed=False,
            turn_exit_reason="completed",
        )
        assert res["recorded"] is False
        assert res["reason"] == "background review fork"
