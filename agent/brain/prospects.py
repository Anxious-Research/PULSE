"""Prospective memory — intentions, commitments and triggers for the future.

specs/brain.md §3.6 prospective memory: remembering *to do* something at a future time
or when a cue appears, as distinct from recalling that something happened (episodic) or
how to do something (procedural). PULSE previously had no representation for this: a
"remind me to X on Friday" encoded as an ordinary episodic note — recallable only if the
user asks again in similar words, with no due-time tracking and no completion state.

Design (docs/brain-evolution.md §5, "prospective"):

- **Typed intention records** in a dedicated ``prospect/`` vault category with a lifecycle
  owned by code, not by prose: ``captured -> active -> completed | cancelled | expired``.
  (PIS-style typed intention store: the *lifecycle* is code; the *content* is a memory node,
  so it stays in the one canonical store and inherits relations, recall, inspector, events.)
- **Two trigger kinds** (the standard PM distinction, cf. Einstein & McDaniel 2005):
  ``time`` — a due timestamp; ``event`` — a cue phrase that a future user message may
  contain. A condition with no parseable time ("when the CI is green") is stored as an
  unresolved *condition* trigger, surfaced only by cue match, never by due-ness.
- **Idempotent capture**: the same source event re-encoded replays to the same intention.
- **No silent state inference**: completion and cancellation are explicit operations, never
  inferred from inactivity. Overdue items surface as overdue — an intention that passes its
  deadline keeps saying so until the user or an agent closes it explicitly.
- **Fail-soft**: every public function returns a plain value and never raises to its caller,
  matching the session-layer contract.

Everything here is stdlib-only and imports nothing from ``agent.*``.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from .models import NodeCategory, NodeStatus
from .parser import decode_record, encode_record, slugify, normalize_node_id
from .relations import Relation, RelationType, Provenance, dump_relations, merge_into, parse_relations
from .similarity import token_set

logger = logging.getLogger("agent.brain.prospects")

__all__ = [
    "PROSPECT_CATEGORY",
    "STATUS_CAPTURED", "STATUS_ACTIVE", "STATUS_COMPLETED", "STATUS_CANCELLED", "STATUS_EXPIRED",
    "TRIGGER_TIME", "TRIGGER_EVENT", "TRIGGER_CONDITION", "TRIGGER_NONE",
    "Intention",
    "capture_intention", "list_intentions", "find_intention", "open_intentions",
    "due_intentions", "overdue_intentions", "cue_intentions",
    "complete_intention", "cancel_intention", "reopen_intention",
    "guidance_block", "extract_due_phrase",
]

PROSPECT_CATEGORY = NodeCategory.PROSPECT.value

# Lifecycle (closed vocabulary, enforced by _transition):
STATUS_CAPTURED = "captured"    # stated, details possibly unresolved — not yet acted on
STATUS_ACTIVE = "active"        # clarified: has a usable trigger (due time or cue)
STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_EXPIRED = "expired"      # closed by policy, never by silent inference

OPEN_STATUSES = (STATUS_CAPTURED, STATUS_ACTIVE)

TRIGGER_TIME = "time"
TRIGGER_EVENT = "event"
TRIGGER_CONDITION = "condition"  # natural-language condition; not machine-checkable
TRIGGER_NONE = "none"

#: How long an overdue intention keeps its full-attention rendering before the block marks
#: it clearly stale (surfacing continues either way — expiry is explicit, never inferred).
OVERDUE_STALE_AFTER_S = 14 * 86400

# ── capture heuristics (deterministic, high-precision) ─────────────────────

#: Explicit commitment markers. Deliberately narrow: a question ("can you remind me?")
#: or a bare future mention ("we should maybe look at X") must NOT auto-become a
#: commitment (§10: never convert casual statements; keep the unresolved state).
_INTENT_PATTERNS = (
    re.compile(r"\bremind me to\s+(.{3,})$", re.IGNORECASE),
    re.compile(r"\bremember to\s+(.{3,})$", re.IGNORECASE),
)


# ── trigger extraction ─────────────────────────────────────────────────────

_WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")

_DUE_PATTERNS: Sequence[tuple] = (
    # ISO date: "by 2026-10-20" / "on 2026-10-20"
    (re.compile(r"\b(\d{4}-\d{2}-\d{2})\b", re.IGNORECASE), "iso"),
    # "in N days/weeks"
    (re.compile(r"\bin\s+(\d+)\s+(day|week)s?\b", re.IGNORECASE), "in"),
    # "tomorrow" / "today" / "tonight"
    (re.compile(r"\b(tomorrow|tonight|today)\b", re.IGNORECASE), "day"),
    # weekday names: "by Friday", "on monday", "next friday"
    (re.compile(r"\b(?:on\s+|next\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", re.IGNORECASE), "weekday"),
)


def extract_due_phrase(text: str) -> tuple[str, Optional[int]]:
    """Best-effort due time from *text*. Returns ``(matched_phrase, epoch_seconds)``.

    Deterministic and deliberately narrow: it recognises ISO dates, "in N days/weeks",
    "tomorrow/today/tonight", and weekday names (forward-looking to the next occurrence).
    Times of day ("at 3pm") are recognised only attached to a recognised day phrase.
    Unknown -> ``("", None)``, which the caller stores as an unresolved condition trigger
    rather than inventing a deadline (§10: preserve the unresolved state).
    """
    text = str(text or "").strip()
    if not text:
        return "", None
    now = time.time()
    local = time.localtime(now)
    for pattern, kind in _DUE_PATTERNS:
        m = pattern.search(text)
        if not m:
            continue
        phrase = m.group(0).strip()
        due: Optional[int] = None
        try:
            if kind == "iso":
                parsed = time.strptime(m.group(1), "%Y-%m-%d")
                due = int(time.mktime((parsed.tm_year, parsed.tm_mon, parsed.tm_mday, 9, 0, 0, 0, 0, -1)))
            elif kind == "in":
                n = int(m.group(1))
                days = n * 7 if m.group(2).lower() == "week" else n
                due = int(now + days * 86400)
            elif kind == "day":
                word = m.group(1).lower()
                if word == "today":
                    due = int(now + 2 * 3600)
                elif word == "tonight":
                    due = int(now + 6 * 3600)
                else:
                    due = int(now + 86400)
            elif kind == "weekday":
                name = m.group(1).lower()
                target = _WEEKDAYS.index(name)
                delta = (target - local.tm_wday) % 7
                if delta == 0:
                    delta = 7  # "on Friday" said on a Friday means NEXT Friday
                due = int(now + delta * 86400)
        except (ValueError, OverflowError):
            continue
        return phrase, due
    return "", None


# ── the record ─────────────────────────────────────────────────────────────

@dataclass
class Intention:
    """One prospective-memory record, decoded from its prospect/ node."""

    node_id: str
    text: str                      # the intention itself ("send the rent reminder")
    status: str                    # captured | active | completed | cancelled | expired
    trigger_kind: str              # time | event | condition | none
    due: Optional[int]             # epoch seconds, time triggers only
    cue: str                       # cue phrase for event/condition triggers
    source_event: str
    completed_at: Optional[int]
    completion_evidence: str
    cancel_reason: str
    created_at: Optional[int]
    node_id_full: str = ""
    history: List[Dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.history is None:
            self.history = []

    @property
    def is_open(self) -> bool:
        return self.status in OPEN_STATUSES

    def due_state(self, now: Optional[float] = None) -> str:
        """``due`` | ``overdue`` | ``due-soon`` | ``upcoming`` | ``none`` — derived, never stored."""
        if self.trigger_kind != TRIGGER_TIME or not self.due:
            return "none"
        now_i = int(now if now is not None else time.time())
        if now_i >= self.due:
            if now_i - self.due > OVERDUE_STALE_AFTER_S:
                return "overdue"
            return "due"
        if now_i >= self.due - 86400:
            return "due-soon"
        return "upcoming"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "node_id": self.node_id or self.node_id_full,
            "text": self.text, "status": self.status,
            "trigger": self.trigger_kind, "due": self.due, "cue": self.cue,
        }


def _intention_from_node(node: Any) -> Optional[Intention]:
    extra = node.frontmatter.extra or {}
    trigger = str(extra.get("trigger_kind") or TRIGGER_NONE)
    due_raw = extra.get("due")
    return Intention(
        node_id=node.id,
        text=str(extra.get("intention") or node.title or node.content or "").strip(),
        status=str(extra.get("prospect_status") or STATUS_CAPTURED),
        trigger_kind=trigger,
        due=int(due_raw) if due_raw else None,
        cue=str(extra.get("cue") or ""),
        source_event=str(extra.get("source_event") or ""),
        completed_at=int(extra["completed_at"]) if extra.get("completed_at") else None,
        completion_evidence=str(extra.get("completion_evidence") or ""),
        cancel_reason=str(extra.get("cancel_reason") or ""),
        created_at=int(node.frontmatter.created_at) if node.frontmatter.created_at else None,
        history=[decode_record(r) for r in (extra.get("history") or [])],
    )


# ── deterministic capture detection (high-precision only) ──────────────────

def detect_intention(user_text: str) -> Optional[Dict[str, Any]]:
    """Detect an *explicit* commitment in a user message, or ``None``.

    Deliberately narrow (§10: never convert casual statements into commitments):
    only unambiguous directives — "remind me to X", "remember to X" — are captured,
    and only from user text. A question ("can you remind me to...?") is a request to
    the assistant in the moment, not necessarily a stored commitment, and is still
    captured with status ``captured`` (unresolved) rather than silently dropped — the
    distinction lives in the status, not in discarding the user's words.
    """
    text = str(user_text or "").strip()
    if not text:
        return None
    for pattern in _INTENT_PATTERNS:
        m = pattern.search(text)
        if m:
            body = m.group(1).strip().rstrip(".!?")
            if len(body) < 3:
                return None
            phrase, due = extract_due_phrase(body)
            cue = body
            if phrase:
                cue = body.lower().replace(phrase.lower(), "").strip(" ,.;:-")
            return {"intention": body, "due": due, "cue": cue}
    return None


# ── turn surfacing ─────────────────────────────────────────────────────────

def _fmt_due(due: Optional[int], now_f: float) -> str:
    if not due:
        return ""
    delta = int(due) - int(now_f)
    if delta <= -OVERDUE_STALE_AFTER_S:
        return f"overdue by ~{max(1, (int(now_f) - int(due)) // 86400)}d"
    if delta <= 0:
        return "due now"
    if delta < 3600:
        return f"due in {max(1, delta // 60)}m"
    if delta < 86400:
        return f"due in ~{delta // 3600}h"
    return f"due in ~{delta // 86400}d"


def guidance_block(vault: Any, query: str, *, now: Optional[float] = None, limit: int = 3) -> str:
    """Rendered prospective-memory lines for a turn, or ``""``.

    Mirrors ``_procedure_guidance_block``: ordinary recall ranks by lexical overlap with
    the whole message, so a commitment that is *due* but shares no words with the current
    question would never surface — which is precisely the failure prospective memory
    exists to prevent (Einstein & McDaniel: prospective recall is cue-driven, and a due
    time is itself a cue). Due/overdue items surface regardless of wording; event-cue
    items surface on token overlap with the message; unresolved conditions surface only
    on cue match. Bounded to *limit* lines, like every other injected block.
    """
    if vault is None:
        return ""
    now_f = now if now is not None else time.time()
    lines: List[str] = []
    try:
        due = due_intentions(vault, now=now_f)
    except Exception:
        return ""
    # Due/overdue first — the whole point of time-triggered prospective memory.
    due.sort(key=lambda it: it.due or 0)
    for item in due[:max(1, int(limit))]:
        state = item.due_state(now=now_f)
        lines.append(f"- [{state}] {item.text} (prospect/{item.node_id.split('/', 1)[-1]})")
    if len(lines) < int(limit):
        for item in cue_intentions(vault, query, limit=limit - len(lines)):
            if any(item.node_id == d.node_id for d in due):
                continue
            lines.append(f"- [when {item.cue}] {item.text}" if item.trigger_kind != TRIGGER_EVENT
                         else f"- [cue: {item.cue}] {item.text}")
    if not lines:
        return ""
    return "Open intentions (prospective memory):\n" + "\n".join(lines)


# ── vault queries ──────────────────────────────────────────────────────────

def _iter_prospects(vault: Any) -> List[Any]:
    """All prospect/ nodes, oldest first. Fail-soft: unreadable entries are skipped.

    ``vault.iter_nodes()`` yields ``(node_id, BrainNode)`` pairs; tolerate both that and a
    bare-node iterable so a future API change fails safe rather than silently empty.
    """
    try:
        entries = list(vault.iter_nodes() or [])
    except Exception:
        return []
    nodes: List[Any] = []
    for entry in entries:
        node = entry[1] if isinstance(entry, tuple) and len(entry) == 2 else entry
        if node is None or not str(getattr(node, "id", "")).startswith(PROSPECT_CATEGORY + "/"):
            continue
        nodes.append(node)
    nodes.sort(key=lambda n: n.frontmatter.created_at or 0)
    return nodes


def list_intentions(vault: Any, *, include_closed: bool = False) -> List[Intention]:
    if vault is None:
        return []
    out: List[Intention] = []
    for node in _iter_prospects(vault):
        try:
            item = _intention_from_node(node)
        except Exception:
            continue
        if item is None:
            continue
        if not item.is_open and not include_closed:
            continue
        out.append(item)
    return out


def find_intention(vault: Any, node_id: str) -> Optional[Intention]:
    if vault is None or not str(node_id or "").strip():
        return None
    try:
        node = vault.read_node(node_id)
    except Exception:
        node = None
    if node is None or not str(node.id).startswith(PROSPECT_CATEGORY + "/"):
        return None
    return _intention_from_node(node)


def open_intentions(vault: Any) -> List[Intention]:
    return list_intentions(vault, include_closed=False)


def due_intentions(vault: Any, *, now: Optional[float] = None) -> List[Intention]:
    """Open intentions whose due time has arrived (due now or overdue)."""
    now_f = now if now is not None else time.time()
    return [
        it for it in open_intentions(vault)
        if it.trigger_kind == TRIGGER_TIME and it.due is not None and int(now_f) >= it.due
    ]


def overdue_intentions(vault: Any, *, now: Optional[float] = None) -> List[Intention]:
    now_f = now if now is not None else time.time()
    return [i for i in due_intentions(vault, now=now_f)
            if i.due_state(now=now_f) == "overdue"]


def cue_intentions(vault: Any, text: str, *, limit: int = 3) -> List[Intention]:
    """Open intentions whose cue/condition overlaps the current message.

    Deterministic token overlap over the trigger phrase — the same lexical basis as
    procedure matching, not a semantic claim. An intention with no cue never matches.
    """
    query_tokens = token_set(str(text or ""))
    if not query_tokens:
        return []
    out: List[tuple] = []
    for item in open_intentions(vault):
        if not item.cue:
            continue
        cue_tokens = token_set(item.cue)
        if not cue_tokens:
            continue
        overlap = len(query_tokens & cue_tokens) / max(1, len(cue_tokens))
        if overlap >= 0.4:
            out.append((overlap, item))
    out.sort(key=lambda pair: (-pair[0], pair[1].node_id))
    return [item for _, item in out[:max(1, int(limit))]]


# ── lifecycle operations ───────────────────────────────────────────────────

def capture_intention(
    vault: Any,
    text: str,
    *,
    due: Optional[int] = None,
    cue: str = "",
    source_event: str = "",
    source_turn: str = "",
    project: str = "",
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Record an intention. Idempotent per ``source_event``; never raises.

    The trigger is derived from an explicit *due* (epoch seconds) or a *cue* phrase; with
    neither, the record is kept as an unresolved condition — visible in the prospect list,
    never surfacing as due. Status starts at ``captured``; it becomes ``active`` only once
    the record has a resolved trigger.
    """
    if vault is None:
        return {"ok": False, "reason": "no vault"}
    text = str(text or "").strip()
    if not text:
        return {"ok": False, "reason": "empty intention"}
    ts = int(now if now is not None else time.time())

    # Idempotency: the same source event must not create two intentions.
    if source_event:
        for node in _iter_prospects(vault):
            extra = node.frontmatter.extra or {}
            if str(extra.get("source_event") or "") == str(source_event):
                return {"ok": True, "intention_id": node.id, "created": False,
                        "duplicate": True, "status": str(extra.get("prospect_status") or STATUS_CAPTURED)}

    trigger_kind = TRIGGER_TIME if due else (TRIGGER_EVENT if cue else TRIGGER_CONDITION)
    status = STATUS_ACTIVE if trigger_kind in (TRIGGER_TIME, TRIGGER_EVENT) else STATUS_CAPTURED
    node_id = vault.unique_node_id(PROSPECT_CATEGORY, slugify(text[:70]))
    history = [{"at": ts, "kind": "captured", "source_event": source_event}]
    extra: Dict[str, Any] = {
        "intention": text,
        "prospect_status": status,
        "trigger_kind": trigger_kind,
        "cue": str(cue or ""),
        "source_event": str(source_event or ""),
        "history": [encode_record(history[0])],
    }
    if due:
        extra["due"] = int(due)
    relations: List[Relation] = []
    if project:
        project_id = normalize_node_id(project)
        relation = Relation(target=project_id, rel_type=RelationType.MENTIONS.value,
                            provenance=Provenance.INFERRED_ENTITY.value)
        relations.append(relation)
        relations = merge_into(relations, relation, evidence_text=f"{node_id}|mentions|{project_id}", now=ts)
    content = f"Intention: {text}"
    vault.write_node(
        node_id, content,
        title=f"Intention: {text[:60]}", category=PROSPECT_CATEGORY,
        tags=["prospect", trigger_kind, status],
        confidence=1.0,
        status=NodeStatus.ACTIVE.value,
        salience=0.75,
        relations=dump_relations(relations) or None,
        source_turn=source_turn, extra=extra, now=ts,
    )
    # §6.2: a time-triggered intention must reach the scheduler, not merely sit in the
    # vault awaiting the next user message. Fail-soft: a scheduler problem never fails
    # the capture — the commitment is durable either way.
    _sync_scheduler(vault, node_id, reason="captured")
    return {"ok": True, "intention_id": node_id, "created": True, "status": status,
            "trigger": trigger_kind, "due": due or None, "cue": cue or ""}


def _transition(vault: Any, node_id: str, *, to_status: str, now: Optional[float] = None,
                evidence: str = "", reason: str = "") -> Dict[str, Any]:
    """Shared explicit state transition. Never re-opens a terminal record implicitly."""
    item = find_intention(vault, node_id)
    if item is None:
        return {"ok": False, "reason": "intention not found"}
    if not item.is_open:
        return {"ok": False, "reason": f"intention is {item.status} (explicit reopen required)"}
    ts = int(now if now is not None else time.time())
    try:
        node = vault.read_node(item.node_id)
        extra = dict(node.frontmatter.extra or {})
    except Exception:
        return {"ok": False, "reason": "node unreadable"}
    extra["prospect_status"] = to_status
    history = [decode_record(r) for r in (extra.get("history") or [])]
    history.append({"at": ts, "from": item.status, "to": to_status})
    extra["history"] = [encode_record(h) for h in history]
    if evidence:
        extra["completion_evidence"] = str(evidence)
        extra["completed_at"] = ts
    if to_status == STATUS_CANCELLED and reason:
        extra["cancel_reason"] = str(reason)
    content = node.content
    if to_status == STATUS_COMPLETED:
        extra["completed_at"] = ts
        content = (node.content or "").rstrip() + f"\n\nCompleted {ts}."
    elif to_status == STATUS_CANCELLED:
        extra["cancel_reason"] = str(reason or "")
        content = (node.content or "").rstrip() + f"\n\nCancelled {ts}: {reason or 'no reason recorded'}."
    vault.write_node(item.node_id, content,
                     extra=extra, now=ts)
    return {"ok": True, "intention_id": item.node_id, "status": to_status,
            "from": item.status, "closed": to_status in (STATUS_COMPLETED, STATUS_CANCELLED, STATUS_EXPIRED)}


def complete_intention(vault: Any, node_id: str, *, evidence_ref: str = "", now: Optional[float] = None) -> Dict[str, Any]:
    """Mark an intention completed. Explicit only — never inferred from inactivity.

    Any pending reminder job is removed, so a completed commitment cannot fire later.
    """
    result = _transition(vault, node_id, to_status=STATUS_COMPLETED, now=now, evidence=evidence_ref)
    if result.get("ok"):
        _sync_scheduler(vault, node_id, reason="completed")
    return result


def cancel_intention(vault: Any, node_id: str, *, reason: str = "", now: Optional[float] = None) -> Dict[str, Any]:
    """Cancel an intention. The record and its history are preserved, never deleted.

    Any pending reminder job is removed, so a cancelled commitment cannot fire later.
    """
    result = _transition(vault, node_id, to_status=STATUS_CANCELLED, now=now, reason=reason)
    if result.get("ok"):
        _sync_scheduler(vault, node_id, reason="cancelled")
    return result


def reopen_intention(vault: Any, node_id: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Explicitly reopen a completed/cancelled intention as ``captured`` (history preserved)."""
    item = find_intention(vault, node_id)
    if item is None:
        return {"ok": False, "reason": "not found"}
    if item.is_open:
        return {"ok": False, "reason": f"already {item.status}"}
    ts = int(now if now is not None else time.time())
    node = vault.read_node(item.node_id)
    extra = dict(node.frontmatter.extra or {})
    history = [decode_record(r) for r in (extra.get("history") or [])]
    history.append({"at": ts, "from": item.status, "to": STATUS_CAPTURED, "kind": "reopen"})
    extra["prospect_status"] = STATUS_CAPTURED
    extra["completed_at"] = None
    extra["completion_evidence"] = ""
    extra["history"] = [encode_record(h) for h in history]
    vault.write_node(item.node_id, node.content, extra=extra, now=ts)
    # Reopen may restore a schedulable trigger (e.g. a due time still recorded); reconcile
    # the reminder job to the reopened state.
    _sync_scheduler(vault, node_id, reason="reopened")
    return {"ok": True, "intention_id": item.node_id, "status": STATUS_CAPTURED}


def _sync_scheduler(vault: Any, node_id: str, *, reason: str = "") -> None:
    """Reconcile the reminder job after a lifecycle change. Fail-soft by design: a
    scheduler problem must never corrupt a lifecycle transition that already succeeded."""
    try:
        from .prospect_scheduler import sync_intention_job

        report = sync_intention_job(vault, node_id)
        if report.get("action") in ("failed",):
            logger.debug("prospect scheduler sync after %s: %s", reason, report)
    except Exception:
        logger.debug("prospect scheduler sync failed for %s", node_id, exc_info=True)


