"""Procedural memory — lessons learned from outcomes (specs/brain.md §3.4, §4.3, §8).

This is the layer the mandate calls out as the one that must *change future behaviour*. Memory
creation, summarisation and passing tests are not learning; learning is when what happened before
changes what PULSE does next. So an outcome is not just another log line — it is folded into a
``procedure/`` node that later retrieval surfaces *first* for a matching goal.

    procedure/<slug>     "to achieve <goal>: do <steps>", with the outcomes that justify it

The rules this module enforces, each with a test:

* **A single lucky result never becomes a procedure.** :func:`procedure_confidence` scales the
  evidence score by the observed success *ratio*, so one success is ``unproven`` and one success
  after one failure is ``situational``, not ``reliable`` (§4.3 "Do not promote a single lucky
  outcome into a universally reliable procedure").
* **Failures are recorded, not discarded.** A failed attempt appends a failure mode; a procedure
  whose attempts mostly fail becomes a marked ``avoid`` record — the lesson "don't do X" is
  knowledge too, and it stops PULSE repeating a known-bad approach (§8 "When PULSE fails").
* **Idempotent.** Recording the same outcome twice (same goal/action/result) is a no-op, so a
  retried write cannot inflate a success count.
* **Reversible and auditable.** Every outcome is kept in the node's ``outcomes`` list with its
  timestamp and evidence, and the node carries ``reconsider_when`` — the conditions under which the
  procedure should be re-examined (§4.3) — so a learned lesson can be revisited, never silently
  trusted forever.

Dependency-free (stdlib + sibling brain modules); writes only through the vault's public API.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence

from .models import NodeCategory, NodeStatus
from .parser import decode_record, encode_record, slugify
from .relations import Relation, RelationType, Provenance, dump_relations, merge_into, parse_relations
from .similarity import similarity

# ── confidence model for a procedure (documented anchors, not invented precision) ───────────
PROCEDURE_CEILING = 0.9      #: a method, however often it worked, is never a certainty
PROCEDURE_BASE = 0.5         #: one observation: promising, not proven
PROCEDURE_GAIN = 0.5         #: each additional observation closes half the remaining gap
RELIABLE_THRESHOLD = 0.6     #: at/above this, with a majority of successes, it is recommended
AVOID_THRESHOLD = 0.35       #: success ratio below this (with ≥2 tries) marks a known-bad approach

#: Similarity at which a new goal is treated as *the same procedure* rather than a new one.
SAME_GOAL_SIMILARITY = 0.6

PROCEDURE_CATEGORY = NodeCategory.PROCEDURE.value

#: Outcome verdicts we understand. ``partial`` counts as evidence but not as a success.
VERDICT_SUCCESS = "success"
VERDICT_FAILURE = "failure"
VERDICT_PARTIAL = "partial"


def _clamp(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def procedure_confidence(successes: int, failures: int, partials: int = 0) -> float:
    """Confidence in a procedure, in ``[0, 1]``, from its observed outcomes.

    The evidence term (how much we have seen) is multiplied by the success *ratio*, so volume
    alone never manufactures trust: twelve attempts split six/six are less reliable than three
    wins from three. Partial outcomes pull the ratio toward even rather than counting as wins.
    """
    successes = max(0, int(successes))
    failures = max(0, int(failures))
    partials = max(0, int(partials))
    total = successes + failures + partials
    if total <= 0:
        return 0.0
    # Partial outcomes count as half a success for the ratio — evidence of utility, not proof.
    ratio = (successes + 0.5 * partials) / float(total)
    evidence = min(PROCEDURE_CEILING, PROCEDURE_BASE + (PROCEDURE_CEILING - PROCEDURE_BASE)
                   * (1.0 - (1.0 - PROCEDURE_GAIN) ** (total - 1)))
    return round(_clamp(evidence * ratio), 4)


def success_rate_of(successes: int, failures: int, partials: int = 0) -> float:
    """Success ratio with partial outcomes counting half — the number a procedure is judged on."""
    total = successes + failures + partials
    if total <= 0:
        return 0.0
    return round((successes + 0.5 * partials) / float(total), 4)


def procedure_status(successes: int, failures: int, partials: int = 0) -> str:
    """``reliable`` | ``situational`` | ``avoid`` | ``unproven`` — a pure function of the record."""
    total = successes + failures + partials
    if total == 0:
        return "unproven"
    if total == 1:
        return "unproven"
    ratio = (successes + 0.5 * partials) / float(total)
    if failures >= 1 and ratio < AVOID_THRESHOLD:
        return "avoid"
    # "reliable" needs *both* a success majority and enough corroborating evidence to clear the
    # same bar a belief does — two wins and a failure (ratio .67, confidence .53) is situational,
    # not proven, and the mandate is explicit that a thin record must not read as a guarantee.
    if ratio >= RELIABLE_THRESHOLD and procedure_confidence(successes, failures, partials) >= RELIABLE_THRESHOLD:
        return "reliable"
    return "situational"


# ── view over a procedure node ─────────────────────────────────────────────


@dataclass
class Procedure:
    """A read-only view of one procedure node, for retrieval and the UI."""

    id: str = ""
    goal: str = ""
    steps: List[str] = field(default_factory=list)
    failure_modes: List[str] = field(default_factory=list)
    preconditions: List[str] = field(default_factory=list)
    outcomes: List[Dict[str, Any]] = field(default_factory=list)
    evidence_refs: List[str] = field(default_factory=list)
    success_count: int = 0
    failure_count: int = 0
    partial_count: int = 0
    confidence: float = 0.0
    status: str = "unproven"
    last_verified: Optional[int] = None
    reconsider_when: List[str] = field(default_factory=list)

    @property
    def success_rate(self) -> float:
        return success_rate_of(self.success_count, self.failure_count, self.partial_count)

    @property
    def discouraged(self) -> List[str]:
        """The actions that failed for this goal — what not to reach for again (§4.3).

        Derived from the outcome log rather than stored, so an older node needs no migration. A
        failure *mode* ("502s during the restart") describes a symptom; the *action* that produced
        it ("in-place restart") is the part an agent can act on, and it was previously not exposed
        anywhere in the procedure view.
        """
        seen: List[str] = []
        for outcome in self.outcomes:
            if str(outcome.get("verdict") or "") != VERDICT_FAILURE:
                continue
            action = str(outcome.get("action") or "").strip()
            if action and action not in seen:
                seen.append(action)
        return seen

    @classmethod
    def from_node(cls, node: Any) -> "Procedure":
        fm = node.frontmatter
        extra = fm.extra or {}
        return cls(
            id=node.id,
            goal=str(extra.get("goal") or node.title or (node.content or "").strip()),
            steps=[str(s) for s in (extra.get("steps") or [])],
            failure_modes=[str(s) for s in (extra.get("failure_modes") or [])],
            preconditions=[str(s) for s in (extra.get("preconditions") or [])],
            outcomes=list(decode_record(t) for t in (extra.get("outcomes") or [])),
            evidence_refs=[str(s) for s in (extra.get("evidence_refs") or [])],
            success_count=int(extra.get("success_count") or 0),
            failure_count=int(extra.get("failure_count") or 0),
            partial_count=int(extra.get("partial_count") or 0),
            confidence=float(fm.confidence),
            status=str(extra.get("procedure_status") or "unproven"),
            last_verified=extra.get("last_verified"),
            reconsider_when=[str(s) for s in (extra.get("reconsider_when") or [])],
        )

    def as_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "goal": self.goal,
            "steps": list(self.steps),
            "discouraged": self.discouraged,
            "failure_modes": list(self.failure_modes),
            "preconditions": list(self.preconditions),
            "outcomes": len(self.outcomes),
            "success_count": self.success_count,
            "failure_count": self.failure_count,
            "partial_count": self.partial_count,
            "success_rate": self.success_rate,
            "confidence": self.confidence,
            "status": self.status,
            "last_verified": self.last_verified,
            "reconsider_when": list(self.reconsider_when),
        }


def list_procedures(vault: Any) -> List[Procedure]:
    """Every procedure node, recommended ones first, then by confidence."""
    if vault is None:
        return []
    procs = [
        Procedure.from_node(node)
        for node in vault.list_all_nodes()
        if node.frontmatter.category == PROCEDURE_CATEGORY
    ]
    procs.sort(key=lambda p: (p.status in ("avoid", "unproven"), -p.confidence, p.id))
    return procs


def find_procedure(vault: Any, goal: str) -> Optional[Any]:
    """The existing procedure node for a similar goal, if any."""
    best: Optional[Any] = None
    best_score = 0.0
    for node in vault.list_all_nodes():
        if node.frontmatter.category != PROCEDURE_CATEGORY:
            continue
        candidate_goal = str((node.frontmatter.extra or {}).get("goal") or node.title or node.content or "")
        score = similarity(goal, candidate_goal)
        if score >= SAME_GOAL_SIMILARITY and score > best_score:
            best, best_score = node, score
    return best


# ── write path ─────────────────────────────────────────────────────────────


def _derive_steps(outcomes: Sequence[Dict[str, Any]]) -> List[str]:
    """The actions that led to success — the best-known way to achieve the goal."""
    steps: List[str] = []
    for out in outcomes:
        if out.get("verdict") == VERDICT_SUCCESS and out.get("action"):
            action = str(out["action"]).strip()
            if action and action not in steps:
                steps.append(action)
    return steps


def _derive_failure_modes(outcomes: Sequence[Dict[str, Any]]) -> List[str]:
    modes: List[str] = []
    for out in outcomes:
        if out.get("verdict") == VERDICT_FAILURE and out.get("result"):
            mode = str(out["result"]).strip()
            if mode and mode not in modes:
                modes.append(mode)
    return modes


def _reconsider_conditions(successes: int, failures: int) -> List[str]:
    conds = ["a recorded execution contradicts the current success rate"]
    if failures:
        conds.append("the known failure modes no longer reproduce")
    if successes:
        conds.append("the environment or tooling the procedure relies on changes")
    return conds


def record_outcome(
    vault: Any,
    *,
    goal: str,
    action: str = "",
    result: str = "",
    verdict: str = VERDICT_SUCCESS,
    evidence_refs: Sequence[str] = (),
    failure_mode: Optional[str] = None,
    now: Optional[float] = None,
    source_turn: Optional[str] = None,
) -> Dict[str, Any]:
    """Record the outcome of an attempt at ``goal``, folding it into the procedure that owns it.

    ``verdict`` is one of ``success`` / ``failure`` / ``partial``. Returns
    ``{ok, procedure_id, created|updated, verdict, success_rate, confidence, status,
    recommended}``. Idempotent per (goal, action, result, verdict) — a replayed event is a no-op.
    """
    goal = (goal or "").strip()
    if not goal or vault is None:
        return {"ok": False, "reason": "empty goal or no vault"}
    verdict = verdict if verdict in (VERDICT_SUCCESS, VERDICT_FAILURE, VERDICT_PARTIAL) else VERDICT_FAILURE
    ts = int(now if now is not None else time.time())
    refs = sorted({str(e) for e in evidence_refs if str(e).strip()})

    node = find_procedure(vault, goal)
    created = node is None
    if created:
        node_id = vault.unique_node_id(PROCEDURE_CATEGORY, slugify(goal[:80]))
        extra: Dict[str, Any] = {
            "goal": goal, "steps": [], "failure_modes": [], "preconditions": [],
            "outcomes": [], "evidence_refs": [], "success_count": 0, "failure_count": 0,
            "partial_count": 0, "reconsider_when": [],
        }
        content = f"Procedure to achieve: {goal}"
        relations: List[Relation] = []
    else:
        node_id = node.id
        extra = dict(node.frontmatter.extra or {})
        content = node.content
        relations = parse_relations(node.frontmatter.relations or [])

    outcomes = [decode_record(t) for t in (extra.get("outcomes") or [])]
    outcome = {"at": ts, "goal": goal, "action": action.strip(), "result": result.strip(),
               "verdict": verdict, "evidence": refs}
    if failure_mode:
        outcome["failure_mode"] = failure_mode.strip()
    # Idempotency: the *same event* replayed is one outcome, not two. Identity is the exact
    # timestamp (a replay carries the original event's time) or an explicit event_id — not a
    # time window, because two genuinely distinct attempts can share a minute.
    if any((outcome.get("event_id") and o.get("event_id") == outcome.get("event_id"))
           or (int(o.get("at", -1)) == ts and o.get("action") == outcome["action"]
               and o.get("result") == outcome["result"] and o.get("verdict") == verdict)
           for o in outcomes):
        return {"ok": True, "procedure_id": node_id, "updated": False, "duplicate": True,
                "confidence": float(node.frontmatter.confidence) if not created else 0.0,
                "status": extra.get("procedure_status", "unproven")}
    outcomes.append(outcome)

    successes = sum(1 for o in outcomes if o.get("verdict") == VERDICT_SUCCESS)
    failures = sum(1 for o in outcomes if o.get("verdict") == VERDICT_FAILURE)
    partials = sum(1 for o in outcomes if o.get("verdict") == VERDICT_PARTIAL)
    confidence = procedure_confidence(successes, failures, partials)
    status = procedure_status(successes, failures, partials)

    extra.update({
        "goal": goal,
        "outcomes": [encode_record(o) for o in outcomes],
        "success_count": successes,
        "failure_count": failures,
        "partial_count": partials,
        "steps": _derive_steps(outcomes),
        "failure_modes": _derive_failure_modes(outcomes),
        "evidence_refs": sorted(set(extra.get("evidence_refs") or []) | set(refs)),
        "procedure_status": status,
        "reconsider_when": _reconsider_conditions(successes, failures),
    })
    if verdict == VERDICT_SUCCESS:
        extra["last_verified"] = ts

    # Each piece of evidence is a real edge: the procedure was learned from these outcomes.
    for ev_id in refs:
        merge_into(
            relations,
            Relation(target=ev_id, rel_type=RelationType.LEARNED_FROM.value,
                     provenance=Provenance.INFERRED_ENTITY.value),
            evidence_text=f"{node_id}|learned_from|{ev_id}", now=ts,
        )

    body = _render_procedure_body(goal, extra)
    vault.write_node(
        node_id, body,
        title=f"Procedure: {goal[:60]}", category=PROCEDURE_CATEGORY, tags=["procedure", status],
        confidence=confidence, status=NodeStatus.ACTIVE.value,
        relations=dump_relations(relations) or None,
        source_turn=source_turn, extra=extra, now=ts,
    )
    return {"ok": True, "procedure_id": node_id, "created": created, "updated": not created,
            "verdict": verdict, "success_rate": success_rate_of(successes, failures, partials),
            "confidence": confidence, "status": status,
            "recommended": status == "reliable"}


def _render_procedure_body(goal: str, extra: Dict[str, Any]) -> str:
    """Human-readable procedure text — the node body, so the vault reads well in Obsidian."""
    lines = [f"Procedure to achieve: {goal}", ""]
    steps = extra.get("steps") or []
    if steps:
        lines.append("Steps (from successful executions):")
        lines.extend(f"{i}. {s}" for i, s in enumerate(steps, 1))
        lines.append("")
    modes = extra.get("failure_modes") or []
    if modes:
        lines.append("Known failure modes:")
        lines.extend(f"- {m}" for m in modes)
        lines.append("")
    lines.append(f"Record: {extra.get('success_count', 0)} success, {extra.get('failure_count', 0)} failure, "
                 f"{extra.get('partial_count', 0)} partial — status: {extra.get('procedure_status', 'unproven')}.")
    return "\n".join(lines).strip()


def procedures_for_goal(vault: Any, goal: str, *, limit: int = 3) -> List[Procedure]:
    """The procedures most relevant to a goal, best first — what retrieval should surface (§8).

    A ``reliable`` procedure outranks a ``situational`` one, which outranks an ``avoid`` record
    (still returned: knowing a method fails is a useful answer). Ties break on goal similarity.
    """
    if vault is None or not (goal or "").strip():
        return []
    ranked = []
    order = {"reliable": 0, "situational": 1, "unproven": 2, "avoid": 3}
    for node in vault.list_all_nodes():
        if node.frontmatter.category != PROCEDURE_CATEGORY:
            continue
        proc = Procedure.from_node(node)
        sim = similarity(goal, proc.goal)
        if sim < 0.35:
            continue
        ranked.append((order.get(proc.status, 2), -sim, -proc.confidence, proc))
    ranked.sort(key=lambda t: (t[0], t[1], t[2]))
    return [p for *_, p in ranked[:max(0, limit)]]
