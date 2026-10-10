"""Beliefs and observations — conclusions synthesized from evidence (specs/brain.md §3.5, §4.4).

An *episode* is something that happened. A *fact* is something asserted. A **belief** is a
conclusion PULSE reached by weighing evidence — and it is a different kind of object, so it is a
different kind of node:

    belief/<slug>        a claim, plus the evidence that supports it and the evidence against it

Why a dedicated layer, and not just another ``concept/`` note: a concept is *knowledge* ("the
vault has six folders"); a belief is *a position* — it can be wrong, it can be contradicted, its
confidence is a function of how much independent evidence backs it, and it must be revisable
without losing what it used to claim. Those are properties a plain note does not carry.

The rules this module enforces, each with a test:

* **Confidence is evidence-justified, never mechanical.** ``evidence_confidence`` is a noisy-OR
  over *distinct* pieces of evidence: the first piece puts a claim at :data:`BELIEF_BASE`, each
  additional *independent* piece closes half the remaining gap toward :data:`BELIEF_CEILING`, and
  inference never reaches certainty. Feeding the same evidence twice is a no-op
  (:func:`reinforce_belief`), so a repeated event can never inflate a belief — the §13.10 bar.
* **Counter-evidence weakens, it does not silently delete.** :func:`add_counter_evidence` lowers
  confidence by :data:`CONFLICT_PENALTY` per independent counter-example and can move a belief to
  ``uncertain`` then ``refuted`` — but the belief stays on disk with its full history, because an
  unresolved contradiction is itself knowledge (§2.4 no-silent-loss).
* **Revisions are recorded.** Every confidence change appends ``{at, reason, from, to}`` to the
  node's ``revisions`` list, so "why did PULSE change its mind, and when" is answerable.
* **Provenance is an edge, not a string.** Each piece of supporting evidence is a ``derived_from``
  edge and each counter-example a ``contradicts`` edge, so the graph shows the real structure and
  a belief can be traced back to the episodes that produced it (§13.3, §13.15).

The module is dependency-free (stdlib + sibling brain modules) and writes only through the vault's
public API, so a belief is an ordinary vault file — inspectable in Obsidian like everything else.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from .models import NodeCategory, NodeStatus
from .parser import decode_record, encode_record, slugify
from .relations import Relation, RelationType, Provenance, dump_relations, merge_into, parse_relations
from .similarity import similarity

# ── confidence model ───────────────────────────────────────────────────────
#
# These are documented anchors, not invented precision. A belief asserted outright by the user
# is not the same object as one inferred from two episodes, and the arithmetic makes that
# distinction explicit instead of burying it in a single default.
BELIEF_BASE = 0.5          #: one independent piece of evidence: more likely than not, not certain
BELIEF_CEILING = 0.95      #: inference, however corroborated, never becomes an assertion
BELIEF_ASSERTED = 1.0      #: the user stated it directly — no inference involved
EVIDENCE_GAIN = 0.5        #: fraction of the remaining gap each NEW distinct evidence closes
CONFLICT_PENALTY = 0.35    #: how much each independent counter-example weakens a belief
UNCERTAIN_THRESHOLD = 0.55 #: below this the belief is provisional, not held
REFUTED_THRESHOLD = 0.25   #: below this the evidence is against the claim

#: Similarity at which a new statement is treated as *the same belief* rather than a new one.
#: Deliberately near-verbatim: a lexical score cannot tell "the payments module credits a ledger"
#: from "the search module credits a ledger" (they differ by one token but assert different
#: things), so merging on a loose threshold would fuse unrelated claims. Corroboration across
#: *paraphrases* is handled by the reflection engine, which checks subject and polarity explicitly;
#: here we only fold exact and near-exact restatements so a retried write cannot duplicate a belief.
SAME_BELIEF_SIMILARITY = 0.9

BELIEF_CATEGORY = NodeCategory.BELIEF.value


def clamp(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def evidence_confidence(
    supporting: Iterable[str],
    counter: Iterable[str] = (),
    *,
    asserted: bool = False,
) -> float:
    """Evidence-justified confidence for a belief, in ``[0, 1]``.

    ``supporting`` and ``counter`` are ids of independent pieces of evidence; **duplicates are
    collapsed first**, which is the whole point — five copies of one episode are one episode, and
    confidence must reflect evidence quality and independence, not how many times a write was
    retried (§13.10).
    """
    n_support = len({str(s) for s in supporting if str(s).strip()})
    n_counter = len({str(c) for c in counter if str(c).strip()})
    if asserted:
        base = BELIEF_ASSERTED
    elif n_support <= 0:
        base = 0.0
    else:
        base = min(
            BELIEF_CEILING,
            BELIEF_BASE + (BELIEF_CEILING - BELIEF_BASE) * (1.0 - (1.0 - EVIDENCE_GAIN) ** (n_support - 1)),
        )
    conf = base * ((1.0 - CONFLICT_PENALTY) ** n_counter)
    return round(clamp(conf), 4)


def belief_status(confidence: float, *supporting: Iterable[str]) -> str:
    """``active`` | ``uncertain`` | ``refuted`` from a confidence value.

    Deliberately a pure function of confidence so status can never drift from the number the UI
    shows — there is no second, separately-maintained notion of "is this true".
    """
    if confidence < REFUTED_THRESHOLD:
        return "refuted"
    if confidence < UNCERTAIN_THRESHOLD:
        return "uncertain"
    return "active"


# ── view over a belief node ────────────────────────────────────────────────


@dataclass
class Belief:
    """A read-only view of one belief node, for reflection, retrieval and the UI."""

    id: str
    statement: str
    confidence: float
    status: str
    supporting: List[str] = field(default_factory=list)
    counter: List[str] = field(default_factory=list)
    revisions: List[Dict[str, Any]] = field(default_factory=list)
    first_observed: Optional[int] = None
    last_verified: Optional[int] = None
    valid_from: Optional[int] = None
    valid_to: Optional[int] = None
    asserted: bool = False

    @classmethod
    def from_node(cls, node: Any) -> "Belief":
        fm = node.frontmatter
        extra = fm.extra or {}
        return cls(
            id=node.id,
            statement=(node.content or "").strip(),
            confidence=float(fm.confidence),
            status=str(extra.get("belief_status") or belief_status(float(fm.confidence))),
            supporting=[str(s) for s in (extra.get("evidence_refs") or [])],
            counter=[str(s) for s in (extra.get("counter_evidence") or [])],
            revisions=[decode_record(t) for t in (extra.get("revisions") or [])],
            first_observed=extra.get("first_observed"),
            last_verified=extra.get("last_verified"),
            valid_from=extra.get("valid_from"),
            valid_to=extra.get("valid_to"),
            asserted=bool(extra.get("asserted")),
        )

    def as_dict(self, *, include_history: bool = False) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "id": self.id,
            "statement": self.statement,
            "confidence": self.confidence,
            "status": self.status,
            "supporting": list(self.supporting),
            "counter": list(self.counter),
            "support_count": len(self.supporting),
            "counter_count": len(self.counter),
            "first_observed": self.first_observed,
            "last_verified": self.last_verified,
            "asserted": self.asserted,
        }
        if include_history:
            out["revisions"] = list(self.revisions)
        return out


def list_beliefs(vault: Any) -> List[Belief]:
    """Every belief node in the vault, active ones first, then by confidence."""
    if vault is None:
        return []
    beliefs = [
        Belief.from_node(node)
        for node in vault.list_all_nodes()
        if node.frontmatter.category == BELIEF_CATEGORY
    ]
    beliefs.sort(key=lambda b: (b.status != "active", -b.confidence, b.id))
    return beliefs


def explain_belief(vault: Any, belief_id: str) -> Optional[Dict[str, Any]]:
    """Full provenance for one belief: statement, confidence, evidence, counters, history."""
    if vault is None:
        return None
    node = vault.read_node(belief_id)
    if node is None or node.frontmatter.category != BELIEF_CATEGORY:
        return None
    belief = Belief.from_node(node)
    evidence = []
    for ev_id in belief.supporting:
        ev = vault.read_node(ev_id)
        evidence.append({"id": ev_id, "text": (ev.content if ev else "")[:400], "missing": ev is None})
    counters = []
    for ev_id in belief.counter:
        ev = vault.read_node(ev_id)
        counters.append({"id": ev_id, "text": (ev.content if ev else "")[:400], "missing": ev is None})
    return {
        **belief.as_dict(include_history=True),
        "valid_from": belief.valid_from,
        "valid_to": belief.valid_to,
        "evidence": evidence,
        "counter_evidence": counters,
    }


# ── write path ─────────────────────────────────────────────────────────────


def _find_same_belief(vault: Any, statement: str) -> Optional[Any]:
    """The existing active belief this statement restates, if any (so we reinforce, not duplicate)."""
    for node in vault.list_all_nodes():
        if node.frontmatter.category != BELIEF_CATEGORY:
            continue
        if node.frontmatter.status != NodeStatus.ACTIVE.value:
            continue
        if similarity(statement, node.content or "") >= SAME_BELIEF_SIMILARITY:
            return node
    return None


def _record_revision(fm: Any, *, at: int, reason: str, before: float, after: float) -> None:
    history = list(fm.extra.get("revisions") or [])
    history.append(encode_record({"at": int(at), "reason": reason,
                                  "from": round(before, 4), "to": round(after, 4)}))
    fm.extra["revisions"] = history


def form_belief(
    vault: Any,
    statement: str,
    *,
    evidence_refs: Sequence[str] = (),
    asserted: bool = False,
    now: Optional[float] = None,
    source_turn: Optional[str] = None,
    category: str = BELIEF_CATEGORY,
) -> Dict[str, Any]:
    """Create a belief, or reinforce the existing one it restates.

    Returns ``{created|reinforced, belief_id, confidence, status, added_evidence}``. Idempotent:
    calling it again with the same evidence changes nothing, so a re-run of consolidation is safe.
    """
    statement = (statement or "").strip()
    if not statement or vault is None:
        return {"ok": False, "reason": "empty statement or no vault"}
    ts = int(now if now is not None else time.time())

    existing = _find_same_belief(vault, statement)
    if existing is not None:
        return reinforce_belief(vault, existing.id, evidence_refs, now=ts, reason="corroboration")

    supporting = sorted({str(e) for e in evidence_refs if str(e).strip()})
    confidence = evidence_confidence(supporting, asserted=asserted)
    node_id = vault.unique_node_id(category, slugify(statement[:80]))
    relations: List[Relation] = []
    for ev_id in supporting:
        merge_into(
            relations,
            Relation(target=ev_id, rel_type=RelationType.DERIVED_FROM.value,
                     provenance=Provenance.ASSERTED.value if asserted else Provenance.INFERRED_ENTITY.value),
            evidence_text=f"{node_id}|derived|{ev_id}",
            now=ts,
        )
    vault.write_node(
        node_id,
        statement,
        title=statement[:80],
        category=category,
        tags=["belief"],
        confidence=confidence,
        status=NodeStatus.ACTIVE.value,
        relations=dump_relations(relations) or None,
        source_turn=source_turn,
        extra={
            "evidence_refs": supporting,
            "counter_evidence": [],
            "belief_status": belief_status(confidence),
            "revisions": [],
            "first_observed": ts,
            "last_verified": ts,
            "valid_from": ts,
            "asserted": bool(asserted),
        },
        now=ts,
    )
    return {
        "ok": True,
        "created": True,
        "belief_id": node_id,
        "confidence": confidence,
        "status": belief_status(confidence),
        "evidence": supporting,
    }


def reinforce_belief(
    vault: Any,
    belief_id: str,
    evidence_refs: Sequence[str] = (),
    *,
    now: Optional[float] = None,
    reason: str = "corroboration",
) -> Dict[str, Any]:
    """Add independent supporting evidence to a belief.

    Confidence rises **only** for evidence not already present; passing an id that is already
    recorded is a deliberate no-op, so repeated identical events cannot inflate the belief.
    """
    if vault is None:
        return {"ok": False, "reason": "no vault"}
    node = vault.read_node(belief_id)
    if node is None or node.frontmatter.category != BELIEF_CATEGORY:
        return {"ok": False, "reason": "not a belief"}
    ts = int(now if now is not None else time.time())
    belief = Belief.from_node(node)
    incoming = {str(e) for e in evidence_refs if str(e).strip()}
    added = sorted(incoming - set(belief.supporting))
    if not added:
        # Nothing new: keep the history honest and leave confidence untouched.
        return {"ok": True, "reinforced": False, "belief_id": belief_id,
                "confidence": belief.confidence, "status": belief.status, "added_evidence": []}

    supporting = sorted(set(belief.supporting) | incoming)
    before = belief.confidence
    after = evidence_confidence(supporting, belief.counter, asserted=belief.asserted)

    fm = node.frontmatter
    _record_revision(fm, at=ts, reason=reason, before=before, after=after)
    fm.extra["evidence_refs"] = supporting
    fm.extra["belief_status"] = belief_status(after)
    fm.extra["last_verified"] = ts

    relations = parse_relations(fm.relations or [])
    for ev_id in added:
        merge_into(
            relations,
            Relation(target=ev_id, rel_type=RelationType.DERIVED_FROM.value,
                     provenance=Provenance.ASSERTED.value if belief.asserted else Provenance.INFERRED_ENTITY.value),
            evidence_text=f"{belief_id}|derived|{ev_id}",
            now=ts,
        )
    vault.write_node(
        belief_id, node.content,
        title=node.title, category=fm.category, tags=fm.tags,
        confidence=after, status=fm.status,
        relations=dump_relations(relations),
        extra=dict(fm.extra), now=ts,
    )
    return {"ok": True, "reinforced": bool(added), "belief_id": belief_id,
            "confidence": after, "status": belief_status(after), "added_evidence": added}


def add_counter_evidence(
    vault: Any,
    belief_id: str,
    evidence_refs: Sequence[str] = (),
    *,
    now: Optional[float] = None,
    reason: str = "contradiction",
) -> Dict[str, Any]:
    """Record evidence *against* a belief: it weakens and may be refuted, but is never deleted.

    This is how a contradiction is handled without erasing history — the belief keeps its
    identity, its supporting evidence and its revision log, and its status moves to ``uncertain``
    or ``refuted`` so it stops appearing as current truth (§8, §13.4).
    """
    if vault is None:
        return {"ok": False, "reason": "no vault"}
    node = vault.read_node(belief_id)
    if node is None or node.frontmatter.category != BELIEF_CATEGORY:
        return {"ok": False, "reason": "not a belief"}
    ts = int(now if now is not None else time.time())
    belief = Belief.from_node(node)
    incoming = {str(e) for e in evidence_refs if str(e).strip()}
    added = sorted(incoming - set(belief.counter))
    if not added:
        return {"ok": True, "changed": False, "belief_id": belief_id,
                "confidence": belief.confidence, "status": belief.status, "added_counter": []}

    counter = sorted(set(belief.counter) | incoming)
    before = belief.confidence
    after = evidence_confidence(belief.supporting, counter, asserted=belief.asserted)

    fm = node.frontmatter
    _record_revision(fm, at=ts, reason=reason, before=before, after=after)
    fm.extra["counter_evidence"] = counter
    fm.extra["belief_status"] = belief_status(after)
    fm.extra["last_verified"] = ts

    relations = parse_relations(fm.relations or [])
    for ev_id in added:
        merge_into(
            relations,
            Relation(target=ev_id, rel_type=RelationType.CONTRADICTS.value,
                     provenance=Provenance.ASSERTED.value),
            evidence_text=f"{belief_id}|contradicts|{ev_id}",
            now=ts,
        )
    vault.write_node(
        belief_id, node.content,
        title=node.title, category=fm.category, tags=fm.tags,
        confidence=after, status=fm.status,
        relations=dump_relations(relations),
        extra=dict(fm.extra), now=ts,
    )
    return {"ok": True, "changed": True, "belief_id": belief_id,
            "confidence": after, "status": belief_status(after), "added_counter": added}


def supersede_belief(
    vault: Any,
    old_id: str,
    new_statement: str,
    *,
    evidence_refs: Sequence[str] = (),
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Replace a belief with a revised one, keeping the old belief as history.

    The old node is marked ``superseded`` (excluded from active recall, still on disk and still
    linked), and the new belief carries a ``supersedes`` edge back to it — the correction chain
    the UI renders (§13.4).
    """
    ts = int(now if now is not None else time.time())
    if vault is None:
        return {"ok": False, "reason": "no vault"}
    formed = form_belief(vault, new_statement, evidence_refs=evidence_refs, now=ts)
    if not formed.get("ok"):
        return formed
    new_id = formed["belief_id"]
    if old_id and old_id != new_id and vault.read_node(old_id) is not None:
        node = vault.read_node(new_id)
        relations = parse_relations(node.frontmatter.relations or [])
        merge_into(relations, Relation(target=old_id, rel_type=RelationType.SUPERSEDES.value,
                                       provenance=Provenance.CORRECTION.value),
                   evidence_text=f"{new_id}|supersedes|{old_id}", now=ts)
        vault.write_node(new_id, node.content, title=node.title, category=node.frontmatter.category,
                         tags=node.frontmatter.tags, confidence=node.frontmatter.confidence,
                         status=node.frontmatter.status, relations=dump_relations(relations),
                         extra=dict(node.frontmatter.extra), now=ts)
        vault.supersede(old_id, new_id, now=ts)
        formed["superseded"] = old_id
    return formed
