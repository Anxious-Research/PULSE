"""Typed edge model for the brain graph (focus-topic: relationship type, provenance, confidence).

The vault's edges were historically *bare*: ``frontmatter.related`` held a list of target
node-ids with no indication of **why** the link existed, **how** it was established, or **how
much** we trust it. This module makes an edge a first-class, typed, auditable object while
remaining fully backward-compatible with every node already on disk.

Design (all additive, zero-migration for a fresh install or an existing vault):

* ``relations`` is a NEW frontmatter field — a list of pipe-delimited tokens, each one a
  :class:`Relation`. The dependency-free parser (``agent/brain/parser.py``) round-trips these
  as plain strings, so no YAML dependency is introduced.
* ``related`` (the old denormalized list of target ids) is kept and derived from ``relations``,
  so legacy readers (``index.py``, ``learning_graph.py``) keep working untouched.
* A node that has only legacy ``related``/``supersedes`` (no ``relations``) is read through
  :func:`relations_for_node`, which *derives* typed relations on the fly — a correct, defensible
  interpretation of the old data, so nothing on disk has to be rewritten to be understood.

Confidence has a defensible, documented meaning (``0.0``–``1.0``):

* ``ASSERTED`` relationships (an explicit ``[[wikilink]]`` the user or the model wrote, a
  supersedes/correction edge) start at ``1.0`` — a stated fact, not a guess.
* ``INFERRED_ENTITY`` relationships (consolidation noticed node A mentions an entity that *is*
  node B) start at :data:`INFERRED_BASE` and can only rise toward — never reach —
  :data:`INFERRED_CEILING`. Inference never becomes certainty; we do not invent precision.
* Repeating the **same** evidence never raises confidence (idempotent): confidence is a function
  of *distinct* corroborating evidence, tracked by hash. Each new distinct piece of evidence
  moves confidence a fraction of the remaining gap (noisy-OR style), so corroboration helps with
  diminishing returns and the ceiling is never crossed.
* Conflicting evidence (a correction/supersession that touches the relationship) lowers
  confidence rather than deleting the edge outright — see :func:`weaken`.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set


class RelationType(str, Enum):
    """Why two memories are linked. A small, meaningful, closed vocabulary — never free text."""

    WIKILINK = "wikilink"          # an explicit [[link]] in the body
    RELATED_TO = "related_to"      # a general association (default for derived links)
    MENTIONS = "mentions"          # A's text names the entity that B is about
    SUPERSEDES = "supersedes"      # A corrects/replaces B (the correction chain)
    # ── §3.3 relationship semantics beyond plain association ────────────────────────────
    # Each of these asserts a *specific* claim about how A and B relate; they exist so the graph
    # can distinguish support from contradiction from derivation rather than showing one
    # undifferentiated "related" edge for every kind of connection.
    SUPPORTS = "supports"          # A is evidence for B (B is a conclusion A backs)
    CONTRADICTS = "contradicts"    # A asserts the opposite of B (unresolved disagreement)
    DERIVED_FROM = "derived_from"  # A was produced from B (summary/belief from an episode)
    CAUSED_BY = "caused_by"        # A resulted from B — only when causal evidence justifies it
    PRECEDES = "precedes"          # A happened before B (temporal order)
    LEARNED_FROM = "learned_from"  # A (a procedure) was learned from outcome B

    @classmethod
    def coerce(cls, value: Any) -> "RelationType":
        text = str(value or "").strip().lower()
        for r in cls:
            if r.value == text:
                return r
        return cls.RELATED_TO


class Provenance(str, Enum):
    """How the relationship was established — the audit trail for every edge."""

    ASSERTED = "asserted"              # a human/model wrote the link explicitly ([[wikilink]])
    INFERRED_ENTITY = "inferred_entity"  # consolidation derived it from a shared entity mention
    CORRECTION = "correction"          # produced by a correction/supersession

    @classmethod
    def coerce(cls, value: Any) -> "Provenance":
        text = str(value or "").strip().lower()
        for p in cls:
            if p.value == text:
                return p
        return cls.INFERRED_ENTITY


# Confidence anchors (documented meaning, not invented precision).
ASSERTED_CONFIDENCE = 1.0
#: An inferred edge from a single entity mention: more likely than not, but far from certain.
INFERRED_BASE = 0.5
#: Inference, however corroborated, never becomes an asserted certainty.
INFERRED_CEILING = 0.95
#: Fraction of the remaining gap that each NEW distinct piece of evidence closes (noisy-OR).
EVIDENCE_GAIN = 0.5
#: How much a single conflicting signal weakens an edge.
CONFLICT_PENALTY = 0.35

_SEP = "|"
_EVID_SEP = ","


def evidence_hash(text: str) -> str:
    """Stable short digest identifying one piece of corroborating evidence (e.g. a sentence)."""
    return hashlib.sha256((text or "").strip().lower().encode("utf-8")).hexdigest()[:10]


def clamp(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return lo
    return max(lo, min(hi, v))


def _sanitize(token: str) -> str:
    """Keep the pipe/comma delimiters usable: strip them from free-ish fields."""
    return str(token or "").replace(_SEP, "/").replace(_EVID_SEP, " ").replace("\n", " ").strip()


@dataclass
class Relation:
    """One typed, provenanced, confidence-scored directed edge ``source -> target``.

    ``source`` is implicit (the node that owns the relation); only the target and metadata are
    serialized into that node's frontmatter.
    """

    target: str
    rel_type: str = RelationType.RELATED_TO.value
    provenance: str = Provenance.INFERRED_ENTITY.value
    confidence: float = INFERRED_BASE
    evidence: List[str] = field(default_factory=list)  # evidence hashes (distinct corroboration)
    created_at: Optional[int] = None
    updated_at: Optional[int] = None

    # -- normalisation ------------------------------------------------------

    def normalized(self) -> "Relation":
        self.rel_type = RelationType.coerce(self.rel_type).value
        self.provenance = Provenance.coerce(self.provenance).value
        self.confidence = round(clamp(self.confidence), 4)
        # dedupe evidence, preserving order
        self.evidence = list(dict.fromkeys(e for e in (self.evidence or []) if e))
        return self

    # -- serialization (codec-safe pipe token) ------------------------------

    def to_token(self) -> str:
        self.normalized()
        parts = [
            _sanitize(self.target),
            self.rel_type,
            self.provenance,
            f"{self.confidence:.4f}",
            _EVID_SEP.join(_sanitize(e) for e in self.evidence),
            "" if self.created_at is None else str(int(self.created_at)),
            "" if self.updated_at is None else str(int(self.updated_at)),
        ]
        return _SEP.join(parts)

    @classmethod
    def from_token(cls, token: str) -> Optional["Relation"]:
        if not token or not str(token).strip():
            return None
        raw = str(token)
        # A bare target with no pipes is a legacy ``related`` entry: treat as inferred.
        if _SEP not in raw:
            return cls(target=raw.strip()).normalized()
        f = raw.split(_SEP)
        f += [""] * (7 - len(f))
        target = f[0].strip()
        if not target:
            return None
        evid = [e for e in f[4].split(_EVID_SEP) if e.strip()]
        def _int(x: str) -> Optional[int]:
            x = x.strip()
            if not x:
                return None
            try:
                return int(float(x))
            except ValueError:
                return None
        return cls(
            target=target,
            rel_type=f[1].strip() or RelationType.RELATED_TO.value,
            provenance=f[2].strip() or Provenance.INFERRED_ENTITY.value,
            confidence=clamp(f[3] or INFERRED_BASE),
            evidence=[e.strip() for e in evid],
            created_at=_int(f[5]),
            updated_at=_int(f[6]),
        ).normalized()

    def to_dict(self) -> Dict[str, Any]:
        self.normalized()
        return {
            "target": self.target,
            "type": self.rel_type,
            "provenance": self.provenance,
            "confidence": self.confidence,
            "evidence": list(self.evidence),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    # -- confidence evolution ----------------------------------------------

    def ceiling(self) -> float:
        return ASSERTED_CONFIDENCE if self.provenance == Provenance.ASSERTED.value else INFERRED_CEILING

    def add_evidence(self, text: str, *, now: Optional[float] = None) -> bool:
        """Corroborate this edge with one piece of evidence.

        Returns True when the evidence was NEW (and confidence moved); False when the identical
        evidence was already recorded (idempotent — confidence does NOT rise on a repeat).
        """
        h = evidence_hash(text)
        if not h or h in self.evidence:
            return False
        self.evidence.append(h)
        gap = self.ceiling() - self.confidence
        self.confidence = round(clamp(self.confidence + gap * EVIDENCE_GAIN, 0.0, self.ceiling()), 4)
        if now is not None:
            self.updated_at = int(now)
        return True

    def weaken(self, *, now: Optional[float] = None, penalty: float = CONFLICT_PENALTY) -> None:
        """Lower confidence in response to conflicting evidence (do not delete the edge)."""
        self.confidence = round(clamp(self.confidence - penalty, 0.0, 1.0), 4)
        if now is not None:
            self.updated_at = int(now)


# ── collection helpers ──────────────────────────────────────────────────────


def parse_relations(tokens: Optional[Iterable[str]]) -> List[Relation]:
    out: List[Relation] = []
    for tok in tokens or []:
        rel = Relation.from_token(tok)
        if rel is not None:
            out.append(rel)
    return out


def dump_relations(relations: Iterable[Relation]) -> List[str]:
    return [r.to_token() for r in relations]


def merge_into(
    existing: List[Relation],
    incoming: Relation,
    *,
    evidence_text: str = "",
    now: Optional[float] = None,
) -> List[Relation]:
    """Merge ``incoming`` into ``existing`` keyed on (target, type).

    * A new (target, type) is appended.
    * A duplicate corroborates: new distinct evidence raises confidence (bounded by ceiling);
      identical evidence is a no-op, so regenerating the same inference never inflates trust.
    * An ASSERTED incoming upgrades an existing inferred edge's provenance and ceiling.
    """
    incoming.normalized()
    for rel in existing:
        if rel.target == incoming.target and rel.rel_type == incoming.rel_type:
            # Upgrade provenance: an explicit assertion outranks an inference.
            if incoming.provenance == Provenance.ASSERTED.value and rel.provenance != Provenance.ASSERTED.value:
                rel.provenance = Provenance.ASSERTED.value
                rel.confidence = ASSERTED_CONFIDENCE
            if evidence_text:
                rel.add_evidence(evidence_text, now=now)
            elif incoming.evidence:
                for h in incoming.evidence:
                    if h not in rel.evidence:
                        rel.evidence.append(h)
                        gap = rel.ceiling() - rel.confidence
                        rel.confidence = round(clamp(rel.confidence + gap * EVIDENCE_GAIN, 0.0, rel.ceiling()), 4)
            if now is not None:
                rel.updated_at = int(now)
            rel.normalized()
            return existing
    if evidence_text and not incoming.evidence:
        incoming.evidence = [evidence_hash(evidence_text)]
    if now is not None:
        incoming.created_at = incoming.created_at or int(now)
        incoming.updated_at = int(now)
    existing.append(incoming.normalized())
    return existing


def derive_from_frontmatter(
    related: Sequence[str],
    supersedes: Sequence[str],
    wikilink_targets: Sequence[str],
    *,
    created_at: Optional[int] = None,
) -> List[Relation]:
    """Interpret a legacy node (no ``relations`` field) as typed relations.

    This is the zero-migration compatibility path: an old vault is *understood* as typed edges
    without rewriting a single file. Wikilinks and supersedes are asserted; a bare ``related``
    entry is an inferred entity association.
    """
    out: List[Relation] = []
    seen: Set[tuple] = set()

    def _add(target: str, rtype: RelationType, prov: Provenance, conf: float) -> None:
        target = str(target or "").strip()
        if not target:
            return
        key = (target, rtype.value)
        if key in seen:
            return
        seen.add(key)
        out.append(Relation(
            target=target, rel_type=rtype.value, provenance=prov.value,
            confidence=conf, created_at=created_at, updated_at=created_at,
        ).normalized())

    for t in wikilink_targets or []:
        _add(t, RelationType.WIKILINK, Provenance.ASSERTED, ASSERTED_CONFIDENCE)
    for t in supersedes or []:
        _add(t, RelationType.SUPERSEDES, Provenance.CORRECTION, ASSERTED_CONFIDENCE)
    for t in related or []:
        _add(t, RelationType.MENTIONS, Provenance.INFERRED_ENTITY, INFERRED_BASE)
    return out


def validate_targets(relations: Iterable[Relation], existing_ids: Set[str]) -> List[Relation]:
    """Drop edges whose target node does not exist — an edge cannot reference a missing node."""
    return [r for r in relations if r.target in existing_ids]
