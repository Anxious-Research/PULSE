"""Memory evidence inspector — the full provenance of one brain node for the UI (P4, §13.3/§13.15).

The desktop graph can show *that* a memory exists; selecting it should answer *why PULSE holds it*:
what it says, what kind of memory it is, the evidence behind it, when it was formed and last seen,
how its confidence was justified, how it was established (provenance), its revision and supersession
history, and its meaningful relationships. All of that already lives in the canonical vault data
model — this module reads it and shapes it for the inspector, inventing nothing in the frontend.

One entry point, :func:`inspect_node`, dispatches on the node's category so a belief carries its
supporting/counter evidence, a procedure carries its outcome record and known limitations, and an
ordinary memory carries its corroborations and corrections. Every referenced id is resolved to a
human label so the inspector can render a relationship without a second round-trip. Read-only and
fail-soft: a missing node returns ``{ok: False}``, never an exception into the request handler.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .beliefs import Belief, belief_status, BELIEF_BASE, BELIEF_CEILING, EVIDENCE_GAIN, CONFLICT_PENALTY
from .models import NodeCategory, NodeStatus
from .outcomes import Procedure, PROCEDURE_CATEGORY
from .prospects import PROSPECT_CATEGORY, Intention, _intention_from_node


def _label_of(vault: Any, node_id: str) -> str:
    """Human title for a referenced id, falling back to the id when the node is gone/synthetic."""
    # A corroboration ref like ``concept/x#c1`` points at the base node's n-th restatement.
    base = node_id.split("#", 1)[0]
    try:
        node = vault.read_node(base)
    except Exception:
        node = None
    if node is None:
        return node_id
    suffix = ""
    if "#c" in node_id:
        suffix = f" (restatement {node_id.split('#c', 1)[1]})"
    return f"{node.title}{suffix}"


def _ref(vault: Any, node_id: str, *, kind: str = "", provenance: str = "", confidence: Optional[float] = None) -> Dict[str, Any]:
    base = node_id.split("#", 1)[0]
    try:
        node = vault.read_node(base)
    except Exception:
        node = None
    ref: Dict[str, Any] = {
        "id": node_id,
        "label": _label_of(vault, node_id),
        "missing": node is None,
    }
    if node is not None:
        ref["category"] = node.frontmatter.category
        ref["status"] = node.frontmatter.status
        snippet = (node.content or "").strip().replace("\n", " ")
        ref["snippet"] = snippet[:160]
    if kind:
        ref["kind"] = kind
    if provenance:
        ref["provenance"] = provenance
    if confidence is not None:
        ref["confidence"] = round(float(confidence), 4)
    return ref


def _common(vault: Any, node: Any) -> Dict[str, Any]:
    """The provenance every node carries, independent of category."""
    fm = node.frontmatter
    detail: Dict[str, Any] = {
        "ok": True,
        "id": node.id,
        "label": node.title,
        "category": fm.category,
        "content": node.content or "",
        "status": fm.status,
        "confidence": round(float(fm.confidence), 4),
        "salience": round(float(fm.salience), 4),
        "stability": round(float(fm.stability), 4),
        "timestamps": {
            "created_at": fm.created_at,
            "updated_at": fm.updated_at,
            "last_accessed": fm.last_accessed,
            "access_count": fm.access_count,
        },
        "source_turn": fm.source_turn,
    }

    # Supersession chain — both directions, resolved to labels so "replaced by what / replaces what"
    # is answerable without erasing the history that produced the current claim.
    if fm.superseded_by:
        detail["superseded_by"] = _ref(vault, fm.superseded_by)
    if fm.supersedes:
        detail["supersedes"] = [_ref(vault, sid) for sid in fm.supersedes]

    # Corroborations: a restated claim is folded into this node (not duplicated); each is an
    # independent assertion that justifies the memory's strength.
    try:
        corr = vault.corroborations_of(node)
    except Exception:
        corr = []
    if corr:
        detail["corroborations"] = [
            {"at": c.get("at"), "text": c.get("text", ""), "turn": c.get("turn", "")} for c in corr
        ]

    # In-place corrections (e.g. a temporal fix): the *previous* text is retained, so "what did
    # this say before, and when did it change" stays answerable.
    try:
        corrections = vault.corrections_of(node)
    except Exception:
        corrections = []
    if corrections:
        detail["corrections"] = corrections

    # Meaningful relationships: the typed edges this node owns, resolved to labels. Kept distinct
    # from the raw ``related`` list so the inspector can show type/provenance/trust, not just a line.
    rels: List[Dict[str, Any]] = []
    try:
        for rel in node.typed_relations():
            rels.append(_ref(vault, rel.target, kind=rel.rel_type, provenance=rel.provenance, confidence=rel.confidence))
    except Exception:
        pass
    if rels:
        detail["relationships"] = rels

    return detail


def _confidence_justification(belief: Belief) -> str:
    """Plain-language account of how a belief's confidence number was reached (never mechanical)."""
    if belief.asserted:
        base = "stated directly by the user (asserted, so confidence starts at certainty)"
    else:
        n = len(belief.supporting)
        base = (
            f"inferred from {n} independent piece(s) of evidence; the first sets a baseline of "
            f"{BELIEF_BASE:g}, each additional piece closes {int(EVIDENCE_GAIN * 100)}% of the gap "
            f"toward the inference ceiling of {BELIEF_CEILING:g}"
        )
    if belief.counter:
        base += (
            f", then weakened by {len(belief.counter)} counter-example(s) at "
            f"{int(CONFLICT_PENALTY * 100)}% each"
        )
    return base + f" → {belief.confidence:g} ({belief.status})."


def _belief_detail(vault: Any, node: Any) -> Dict[str, Any]:
    detail = _common(vault, node)
    belief = Belief.from_node(node)
    detail["kind"] = "belief"
    detail["belief_status"] = belief.status
    detail["confidence_justification"] = _confidence_justification(belief)
    detail["evidence"] = [_ref(vault, eid, kind="supports") for eid in belief.supporting]
    detail["counter_evidence"] = [_ref(vault, eid, kind="contradicts") for eid in belief.counter]
    detail["revisions"] = list(belief.revisions)
    detail["asserted"] = belief.asserted
    detail["valid_from"] = belief.valid_from
    detail["valid_to"] = belief.valid_to
    return detail


def _procedure_detail(vault: Any, node: Any) -> Dict[str, Any]:
    detail = _common(vault, node)
    proc = Procedure.from_node(node)
    detail["kind"] = "procedure"
    detail["goal"] = proc.goal
    detail["procedure_status"] = proc.status
    detail["success_rate"] = proc.success_rate
    detail["counts"] = {
        "success": proc.success_count,
        "failure": proc.failure_count,
        "partial": proc.partial_count,
    }
    detail["steps"] = list(proc.steps)
    detail["preconditions"] = list(proc.preconditions)      # applicability conditions
    detail["failure_modes"] = list(proc.failure_modes)      # known limitations
    detail["discouraged"] = list(proc.discouraged)
    detail["reconsider_when"] = list(proc.reconsider_when)
    # Supporting experience: the outcome records (observed action + result + verdict) and the
    # experience ids they came from — the distinction between what was observed and what was inferred.
    detail["outcomes"] = list(proc.outcomes)
    detail["experience_refs"] = [_ref(vault, eid, kind="experience") for eid in proc.evidence_refs]
    detail["confidence_justification"] = (
        f"{proc.success_count} success / {proc.failure_count} failure / {proc.partial_count} partial "
        f"outcome(s) → success rate {proc.success_rate:g}, status '{proc.status}'."
    )
    return detail


def _prospect_detail(vault: Any, node: Any) -> Dict[str, Any]:
    """Full provenance for a prospective-memory (prospect/) node."""
    detail = _common(vault, node)
    item = _intention_from_node(node)
    if item is None:
        detail["kind"] = "prospect"
        return detail
    detail["kind"] = "prospect"
    detail["prospect_status"] = item.status
    detail["trigger_kind"] = item.trigger_kind
    detail["due"] = item.due
    detail["cue"] = item.cue
    detail["source_event"] = item.source_event
    detail["completed_at"] = item.completed_at
    detail["completion_evidence"] = item.completion_evidence
    detail["cancel_reason"] = item.cancel_reason
    detail["is_open"] = item.is_open
    if item.due:
        detail["due_state"] = item.due_state()
    # Lifecycle history
    detail["history"] = list(item.history)
    return detail


def _memory_detail(vault: Any, node: Any) -> Dict[str, Any]:
    detail = _common(vault, node)
    detail["kind"] = "memory"
    return detail


def inspect_node(vault: Any, node_id: str) -> Dict[str, Any]:
    """Full provenance for one brain node, shaped for the inspector. ``{ok: False}`` when missing.

    Dispatches on category: beliefs carry supporting/counter evidence + confidence justification +
    revision history; procedures carry outcomes, applicability conditions and known limitations;
    prospects carry trigger, due state and lifecycle history; everything else is an ordinary
    memory with its corroborations, corrections and relationships.
    """
    if vault is None or not str(node_id or "").strip():
        return {"ok": False, "message": "no node id"}
    try:
        node = vault.read_node(node_id)
    except Exception:
        node = None
    if node is None:
        return {"ok": False, "message": f"node not found: {node_id}"}

    category = node.frontmatter.category
    if category == NodeCategory.BELIEF.value:
        return _belief_detail(vault, node)
    if category == PROCEDURE_CATEGORY:
        return _procedure_detail(vault, node)
    if category == PROSPECT_CATEGORY:
        return _prospect_detail(vault, node)
    return _memory_detail(vault, node)
