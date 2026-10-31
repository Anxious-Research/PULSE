"""Turn-end outcome recording for procedural learning (specs/brain.md §8, §4.3).

This is the production write path that bridges turn completion (``agent/turn_finalizer.py``) to
:func:`~agent.brain.outcomes.record_outcome`. It is the missing half of the outcome→strategy
loop: without it, procedures can be *read* (``_procedure_guidance_block``) but never written, so
nothing the agent learns from experience ever changes what it does next.

Rules this module enforces:

* **Record only with sufficient evidence.** A turn is a recordable *task attempt* only when it is
  a task goal (not a question or chitchat) **and** it actually did substantive work (an action
  tool, or several tool calls). Everything else is left alone rather than guessed at.
* **Observed vs. inferred.** The ``action``/``result`` written are observed facts (which tools
  ran, whether they errored). We never invent a cause; ``failure_mode`` is only set when a tool
  reported an explicit error.
* **Idempotent per turn.** A stable ``event_id`` (session + turn) makes a replayed or retried
  turn a no-op, so a retry cannot inflate a procedure's success count.
* **Fail-open.** Runs after the reply is delivered; any error here is swallowed and logged.

Dependency-free (stdlib + sibling brain modules).
"""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

#: Tools that must never form procedural workflows on their own. Orchestration,
#: memory management, and meta-agent control are not technical procedures.
NON_PROCEDURAL_TOOLS = frozenset({
    "delegate_task", "memory", "skill_manage", "todo_list", "session_search",
})

#: Tools that mutate state or execute something concrete. A multi-step workflow
#: using these may represent a reusable technical procedure.
ACTION_TOOLS = frozenset({
    "terminal", "execute_code", "write_file", "patch", "browser_exec", "computer_use",
    "cronjob_manage", "drive_preview", "desktop_project", "gui_tour", "annotate_preview",
    "apply_layout", "focus_pane", "desktop_preview", "read_window_below",
})

#: Goal keywords that mark a user message as an explicit technical task (vs. a question or chitchat).
#: Observational or chitchat verbs like check, find, list, read, make, review are excluded.
TASK_GOAL_KEYWORDS = frozenset({
    "build", "create", "write", "implement", "add", "fix", "debug", "test", "run",
    "deploy", "setup", "configure", "install", "update", "refactor", "optimize",
    "migrate", "generate", "validate", "extract", "parse", "clean", "remove",
    "rename", "move", "convert", "publish", "commit", "push", "restart",
})

#: Openers that mark a *question*, never a task ("how do I...", "what is...").
_QUESTION_OPENERS = (
    "how ", "what ", "why ", "when ", "where ", "who ", "which ", "is ", "are ", "does ",
    "do ", "can you explain", "could you explain", "explain ", "tell me", "show me how",
)

#: Short acknowledgements that must never read as a task goal.
_ACKNOWLEDGEMENTS = frozenset({
    "ok", "okay", "yes", "yep", "yeah", "no", "nope", "thanks", "thank you", "thx", "got it",
    "cool", "nice", "great", "sure", "continue", "go on", "go ahead", "done", "stop", "wait",
})

#: Hindi / Hinglish markers that indicate conversational dialogue rather than technical goals.
_HINDI_HINGLISH_MARKERS = frozenset({
    "ekdum", "shuru", "pehle", "karenge", "karna", "karo", "karke", "sabse",
    "sabki", "kuch", "tumne", "mera", "meri", "yeh", "woh", "kyun", "kaise",
    "kahan", "hai", "thi", "raha", "rahe", "rahi", "accha",
    "batao", "bataiye", "dekho", "samjhao", "samjhe", "bhai", "yaar", "phir", "toh",
    "dijiye", "kijiye", "chahiye", "bolo", "suno", "nahi", "nahin", "saath", "baad",
    "dhyan", "rakho", "rakhna", "samjh", "apna", "apne", "apni", "hogi", "hoge",
    "shuruaat", "kariye",
})

#: Conversational openers / meta-prompts that disqualify a message from being a procedure goal.
_CONVERSATIONAL_PREFIXES = (
    "perfect", "great", "awesome", "nice", "cool", "alright", "sure", "ok", "okay",
    "thanks", "thank you", "thx", "well ", "so ", "actually", "by the way", "btw",
    "listen", "look ", "hey", "hello", "hi ", "can you", "could you", "would you",
    "will you", "please help", "i want you to", "i need you to", "let's", "lets",
    "as discussed", "like we said", "as mentioned", "moving on", "now that",
)

#: Patterns identifying internal gateway, delegation, and harness notifications.
_DELEGATION_PATTERNS = (
    r"^\s*\[ASYNC",
    r"^\s*\[CONTEXT",
    r"^\s*\[PRIOR CONTEXT",
    r"^\s*\[Your active task list",
    r"^\s*\[IMPORTANT: Background process",
    r"^\s*\[OUT-OF-BAND USER MESSAGE",
    r"^\s*\[TASK",
    r"^\s*A background fan-out",
    r"^\s*A background subagent",
)


def _user_text(original_user_message: Any) -> str:
    """Best-effort plain text of the user's message (str or multimodal list of parts)."""
    if original_user_message is None:
        return ""
    if isinstance(original_user_message, str):
        return original_user_message
    if isinstance(original_user_message, dict):
        content = original_user_message.get("content")
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return " ".join(
                str(p.get("text") or "") for p in content
                if isinstance(p, dict) and p.get("type") in (None, "text", "input_text")
            )
        return ""
    if isinstance(original_user_message, list):
        return " ".join(
            str(p.get("text") or "") for p in original_user_message
            if isinstance(p, dict) and p.get("type") in (None, "text", "input_text")
        )
    return str(original_user_message)


def _is_task_goal(text: str) -> bool:
    """True when the message reads as an explicit technical task, not a question, chitchat, or delegation."""
    cleaned = (text or "").strip()
    if not cleaned:
        return False
    # Drop harness notifications / delegations
    for pat in _DELEGATION_PATTERNS:
        if re.search(pat, cleaned, re.IGNORECASE):
            return False
    lower = cleaned.lower()
    bare = lower.lstrip("/@#* \t\n").strip()
    if not bare or bare in _ACKNOWLEDGEMENTS:
        return False
    if any(bare.startswith(op) for op in _QUESTION_OPENERS) or bare.endswith("?"):
        return False
    for prefix in _CONVERSATIONAL_PREFIXES:
        if bare.startswith(prefix):
            return False
    words = re.findall(r"\b[a-z]+\b", lower)
    if not words or len(words) < 2:
        return False
    # Conversational Hindi / Hinglish instructions are not technical procedures
    if any(w in _HINDI_HINGLISH_MARKERS for w in words):
        return False
    # Check the first line of bare text
    first_line = bare.split("\n")[0].strip()
    for prefix in _CONVERSATIONAL_PREFIXES:
        if first_line.startswith(prefix):
            return False
    line_words = re.findall(r"\b[a-z]+\b", first_line)
    if not line_words:
        return False
    target_idx = 0
    if line_words[0] in ("please", "safely", "carefully") and len(line_words) > 1:
        target_idx = 1
    return line_words[target_idx] in TASK_GOAL_KEYWORDS


def _extract_goal(text: str, max_len: int = 120) -> str:
    """The user's task goal as one line, dropping fences/attachments, truncated if long."""
    text = (text or "").strip()
    if not text:
        return ""
    lines = [
        ln.strip() for ln in text.split("\n")
        if ln.strip() and not ln.strip().startswith("```") and not ln.strip().startswith("[")
    ]
    if not lines:
        return ""
    first = lines[0]
    # Strip leading formatting / slash-command token so the goal is the human intent.
    first = first.lstrip("/@#* \t\n").strip()
    if len(first) > max_len:
        first = first[: max_len - 1] + "…"
    return first.strip()


def _tool_outcomes_from_messages(messages: Any) -> List[Tuple[str, bool]]:
    """This turn's ``[(tool_name, is_error), ...]`` read from the committed transcript.

    The observability harness resets its per-round buffer each round, so it only holds the last
    round at turn end. The transcript is cumulative for the turn, so this is the reliable source.
    Scans from just after the last user message to the end.
    """
    if not messages or not isinstance(messages, (list, tuple)):
        return []
    start = 0
    for i in range(len(messages) - 1, -1, -1):
        msg = messages[i]
        if isinstance(msg, dict) and msg.get("role") == "user":
            start = i + 1
            break
    outcomes: List[Tuple[str, bool]] = []
    try:
        from agent.display import _detect_tool_failure
    except Exception:  # pragma: no cover - defensive
        _detect_tool_failure = None  # type: ignore
    for msg in messages[start:]:
        if not isinstance(msg, dict) or msg.get("role") != "tool":
            continue
        name = str(msg.get("tool_name") or msg.get("name") or "")
        content = msg.get("content")
        failed = False
        if _detect_tool_failure is not None:
            try:
                failed, _ = _detect_tool_failure(name, content)
            except Exception:
                failed = False
        outcomes.append((name, bool(failed)))
    return outcomes


def _is_substantive(
    tool_outcomes: Sequence[Tuple[str, bool]],
    verified_steps: Any = None,
) -> bool:
    """True when the turn did enough to constitute a procedure-forming task attempt.

    A procedure represents a concrete, multi-step repeatable technical workflow.
    Single-call actions ('used terminal', 'used delegate_task') or meta-orchestration
    calls are not procedures.
    """
    if isinstance(verified_steps, (list, tuple)) and len(verified_steps) >= 2:
        return True
    if len(tool_outcomes) < 2:
        return False
    concrete = [name for name, _ in tool_outcomes if name and name not in NON_PROCEDURAL_TOOLS]
    if len(concrete) < 2:
        return False
    if not any(name in ACTION_TOOLS for name in concrete):
        return False
    return True


def _derive_verdict(
    *,
    interrupted: bool,
    failed: bool,
    turn_exit_reason: str,
    tool_outcomes: Sequence[Tuple[str, bool]],
    final_response: Any,
) -> Optional[str]:
    """``success`` / ``failure`` / ``partial`` / ``None`` (cannot determine → don't record)."""
    if interrupted:
        return None  # the user stopped it; what happened is unknown
    if not tool_outcomes:
        return None
    errors = sum(1 for _, err in tool_outcomes if err)
    reason = str(turn_exit_reason or "")
    if failed:
        return "failure"
    if reason.startswith("text_response(") and final_response:
        if errors == 0:
            return "success"
        if errors < len(tool_outcomes):
            return "partial"  # some worked, some did not — the agent still delivered a reply
        return "failure"  # every tool errored, yet a reply was produced
    if "max_iterations_reached" in reason or "budget_exhausted" in reason:
        # Ran out of room before finishing: progress at best, never a clean success.
        if errors == 0:
            return "partial"
        if errors == len(tool_outcomes):
            return "failure"
        return "partial"
    return None


def _derive_verification(
    verdict: str,
    tool_outcomes: Sequence[Tuple[str, bool]],
    verified: Optional[bool],
) -> str:
    """Who established the *objective* outcome (§7.2) — never assumed from tool success.

    ``verified=True`` from a caller who ran an independent verifier → ``verified``.
    Otherwise an all-green turn is recorded as ``tool-success`` (actions worked; the
    objective was not independently checked), and anything else as ``unverified``.
    """
    from agent.brain.outcomes import (
        VERIFICATION_TOOL_SUCCESS,
        VERIFICATION_UNVERIFIED,
        VERIFICATION_VERIFIED,
    )

    if verified is True:
        return VERIFICATION_VERIFIED
    if verified is False:
        return VERIFICATION_UNVERIFIED
    errors = sum(1 for _, err in tool_outcomes if err)
    if verdict == "success" and errors == 0:
        return VERIFICATION_TOOL_SUCCESS
    return VERIFICATION_UNVERIFIED


def _action_summary(tool_outcomes: Sequence[Tuple[str, bool]]) -> str:
    """Observed action: which tools the turn used (deduped, order preserved)."""
    seen: List[str] = []
    for name, _ in tool_outcomes:
        if name and name not in seen:
            seen.append(name)
    if not seen:
        return ""
    if len(seen) == 1:
        return f"used {seen[0]}"
    return f"used {', '.join(seen[:3])}" + ("…" if len(seen) > 3 else "")


def _result_summary(verdict: str, tool_outcomes: Sequence[Tuple[str, bool]]) -> str:
    """Observed result — a fact about what the tools did, never a guessed cause."""
    errors = sum(1 for _, err in tool_outcomes if err)
    if verdict == "success":
        return f"all {len(tool_outcomes)} tool call(s) succeeded"
    if verdict == "failure":
        return f"{errors}/{len(tool_outcomes)} tool call(s) failed"
    return f"partial: {errors}/{len(tool_outcomes)} tool call(s) failed"


def _failure_mode(tool_outcomes: Sequence[Tuple[str, bool]]) -> Optional[str]:
    """A known failure mode, only when a tool actually errored (observed, not inferred)."""
    failed = [name for name, err in tool_outcomes if err and name]
    if not failed:
        return None
    uniq: List[str] = []
    for n in failed:
        if n not in uniq:
            uniq.append(n)
    return "tool error in " + ", ".join(uniq)


def record_turn_outcome(
    agent: Any,
    *,
    original_user_message: Any,
    final_response: Any,
    interrupted: bool,
    failed: bool,
    turn_exit_reason: str,
    session_id: str = "",
    turn_id: str = "",
    messages: Any = None,
    tool_outcomes: Optional[Sequence[Tuple[str, bool]]] = None,
    verified: Optional[bool] = None,
    turn_origin: Optional[str] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Record a completed turn's outcome as a procedure when evidence is sufficient.

    ``tool_outcomes`` overrides transcript extraction when supplied (tests / callers that already
    have the signal). ``verified`` carries an *independent* verifier's verdict on the user's
    objective when one ran (None = none ran — the record is then explicitly unverified, §7.2).
    Returns ``{recorded, reason, ...}``. Never raises.
    """
    try:
        from agent.brain.session import get_write_vault, resolve_settings
        from agent.brain.outcomes import record_outcome

        settings = resolve_settings(agent)
        if not settings.enabled or not settings.encode_enabled:
            return {"recorded": False, "reason": "brain disabled"}

        # Review forks, subagents, and non-interactive agents must never record procedures from harness prompts
        is_bg = False
        try:
            from tools.skill_provenance import is_background_review
            is_bg = bool(is_background_review())
        except Exception:
            pass

        if (
            is_bg
            or turn_origin == "background_review"
            or getattr(agent, "_memory_write_origin", None) == "background_review"
            or getattr(agent, "_turn_origin", None) == "background_review"
            or getattr(agent, "_memory_write_context", None) == "background_review"
            or getattr(agent, "skip_background_review", False)
        ):
            return {"recorded": False, "reason": "background review fork"}

        if (
            turn_origin in ("subagent", "delegation")
            or getattr(agent, "_memory_write_origin", None) in ("subagent", "delegation")
            or getattr(agent, "_turn_origin", None) in ("subagent", "delegation")
            or getattr(agent, "_memory_write_context", None) in ("subagent", "delegation")
            or getattr(agent, "_parent_session_id", None) is not None
            or getattr(agent, "_delegate_depth", 0) > 0
        ):
            return {"recorded": False, "reason": "delegation fork"}

        user_text = _user_text(original_user_message)
        if not user_text.strip():
            return {"recorded": False, "reason": "empty user message"}

        # Internal gateway, delegation completion, or background notifications
        for pat in _DELEGATION_PATTERNS:
            if re.search(pat, user_text, re.IGNORECASE):
                return {"recorded": False, "reason": "delegation or harness notification"}

        if user_text.lstrip().startswith((
            "Review the full conversation above",
            "Review the conversation above",
            "Review the turn above",
        )):
            return {"recorded": False, "reason": "background review fork"}

        if not _is_task_goal(user_text):
            return {"recorded": False, "reason": "not a task goal"}

        outcomes = list(tool_outcomes) if tool_outcomes is not None else _tool_outcomes_from_messages(messages)
        verdict = _derive_verdict(
            interrupted=interrupted, failed=failed, turn_exit_reason=turn_exit_reason,
            tool_outcomes=outcomes, final_response=final_response,
        )
        if verdict is None:
            return {"recorded": False, "reason": "outcome indeterminate"}

        verified_steps = kwargs.get("verified_steps")
        if not _is_substantive(outcomes, verified_steps=verified_steps):
            return {"recorded": False, "reason": f"insufficient evidence ({len(outcomes)} tool call(s))"}

        concrete = [name for name, _ in outcomes if name and name not in NON_PROCEDURAL_TOOLS]
        has_verified_steps = isinstance(verified_steps, (list, tuple)) and len(verified_steps) >= 2
        if len(concrete) < 2 and not has_verified_steps:
            return {"recorded": False, "reason": f"insufficient concrete steps ({len(concrete)} concrete call(s))"}

        goal = _extract_goal(user_text)
        if not goal:
            return {"recorded": False, "reason": "goal extraction failed"}

        vault = get_write_vault(agent)
        if vault is None:
            return {"recorded": False, "reason": "no vault"}

        # Never record a procedure if steps only contain a single tool call
        if len(outcomes) <= 1 and not has_verified_steps:
            return {"recorded": False, "reason": "single tool call cannot form procedure"}
        action_summary = _action_summary(outcomes)
        if action_summary.startswith("used ") and "," not in action_summary and len(outcomes) <= 1:
            return {"recorded": False, "reason": "single step tool call cannot form procedure"}

        # Stable per-turn identity so a retry/replay cannot inflate the success count.
        event_id = f"{session_id or 'session'}:{turn_id}" if (session_id or turn_id) else None

        verification = _derive_verification(verdict, outcomes, verified)

        outcome = record_outcome(
            vault,
            goal=goal,
            action=_action_summary(outcomes),
            result=_result_summary(verdict, outcomes),
            verdict=verdict,
            failure_mode=_failure_mode(outcomes),
            evidence_refs=[session_id] if session_id else [],
            now=time.time(),
            source_turn=session_id or None,
            event_id=event_id,
            verification=verification,
        )
        if outcome.get("ok"):
            logger.info(
                "brain outcome: %s %r → %s (procedure %s, %s, %s)",
                verdict, goal[:48], outcome.get("status"), outcome.get("procedure_id"),
                "created" if outcome.get("created") else ("duplicate" if outcome.get("duplicate") else "updated"),
                verification,
            )
        return {"recorded": outcome.get("ok", False), "verdict": verdict,
                "verification": verification, **outcome}

    except Exception:
        logger.debug("brain outcome recording failed", exc_info=True)
        return {"recorded": False, "reason": "exception"}
