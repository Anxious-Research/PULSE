"""Evolving user model — evidence-backed representation of the person (§9).

The user model is NOT a flat profile that grows forever. It is a set of *dated,
sourced claims* — explicit preferences, inferred patterns, project state, goals,
constraints — each distinguishable by how it was learned (told vs. inferred) and
each revisable without rewriting history.

Design choices made here, each tied to the directive:

* **Explicit vs. inferred.** A preference the user stated ("I prefer local-first AI")
  is a ``UserFact`` with ``source='explicit'`` and ``confidence=1.0``. A pattern PULSE
  inferred from repeated behaviour ("prefers concise responses") has ``source='inferred'``
  and a confidence that rises with evidence — never to 1.0 (§9: "every derived
  interpretation must be distinguishable from an explicit user statement").

* **Temporal validity.** A fact carries ``valid_from`` and optional ``valid_until``.
  A changed preference is superseded, not erased: the old fact stays with
  ``valid_until`` set and ``superseded_by`` pointing to the new one.

* **Project/entity scoping.** Facts can be scoped to a project or domain so a
  preference for one project doesn't leak into an unrelated one (§9.1).

* **No silent state inference.** Abandoning a project is never inferred from
  inactivity alone — it requires evidence or explicit user statement (§9).

* **Snapshot projection.** ``current_model()`` returns a read-only snapshot of the
  user's current state: active preferences, goals, projects, constraints. This
  projection is rebuilt from evidence each time; it is never the source of truth.

Dependency-free (stdlib + sibling brain modules); writes only through the vault's
public API.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .models import NodeCategory, NodeStatus
from .parser import decode_record, encode_record, slugify, normalize_node_id
from .relations import (
    Relation,
    dump_relations, merge_into, parse_relations,
)
from .similarity import similarity, tokenize, token_set

__all__ = [
    "UserFact",
    "UserModelSnapshot",
    "FACT_SOURCE_EXPLICIT",
    "FACT_SOURCE_INFERRED",
    "FACT_SOURCE_CORRECTED",
    "record_user_fact",
    "revise_user_fact",
    "supersede_user_fact",
    "current_model",
    "facts_for_scope",
    "active_projects",
    "user_preferences",
    "user_goals",
    "extract_from_text",
    "render_model_block",
]

USER_CATEGORY = "user"
PROJECT_CATEGORY = NodeCategory.PROJECT.value

# Source types — the §9 distinction between what the user said and what PULSE inferred
FACT_SOURCE_EXPLICIT = "explicit"     # user stated it directly
FACT_SOURCE_INFERRED = "inferred"     # PULSE observed a pattern
FACT_SOURCE_CORRECTED = "corrected"   # user corrected a prior belief

# Confidence anchors
EXPLICIT_CONFIDENCE = 1.0             # user said it — not an inference
INFERRED_BASE = 0.5                   # one observation
INFERRED_CEILING = 0.85               # inferred facts never reach assertion-level
INFERRED_GAIN = 0.4                   # each additional observation closes this fraction of gap
CORRECTION_CONFIDENCE = 1.0           # a correction is as strong as an assertion

# Similarity threshold for treating two facts as about the same thing
SAME_FACT_SIMILARITY = 0.85

# Fact types (semantic categories within the user model)
FACT_TYPE_PREFERENCE = "preference"
FACT_TYPE_GOAL = "goal"
FACT_TYPE_CONSTRAINT = "constraint"
FACT_TYPE_CONTEXT = "context"         # environment, tools, technical setup
FACT_TYPE_PATTERN = "pattern"         # inferred behavioural pattern
FACT_TYPE_IDENTITY = "identity"       # name, role, contact info


@dataclass
class UserFact:
    """One fact about the user: a dated, sourced, possibly-scoped claim."""

    content: str
    fact_type: str = FACT_TYPE_PREFERENCE
    source: str = FACT_SOURCE_EXPLICIT
    confidence: float = EXPLICIT_CONFIDENCE
    scope: str = ""                   # empty = global; else project/domain name
    valid_from: Optional[int] = None
    valid_until: Optional[int] = None
    evidence_ids: List[str] = field(default_factory=list)
    evidence_count: int = 1
    node_id: str = ""                 # vault node id when persisted


def inferred_confidence(evidence_count: int) -> float:
    """Confidence for an inferred fact given the number of independent observations.

    One observation: ``INFERRED_BASE`` (0.5). Each additional observation closes
    ``INFERRED_GAIN`` of the remaining gap toward ``INFERRED_CEILING`` (0.85).
    Never reaches 1.0 — an inferred fact is never an assertion.
    """
    if evidence_count <= 0:
        return 0.0
    if evidence_count == 1:
        return INFERRED_BASE
    conf = INFERRED_BASE
    for _ in range(evidence_count - 1):
        conf += INFERRED_GAIN * (INFERRED_CEILING - conf)
    return round(min(conf, INFERRED_CEILING), 4)


def _find_existing_fact(vault: Any, content: str, scope: str = "") -> Optional[Any]:
    """Find an existing user-model fact that says approximately the same thing."""
    try:
        nodes = list(vault.list_all_nodes())
    except Exception:
        return None
    nodes = [n for n in nodes if getattr(n, "category", "") == USER_CATEGORY]
    for node in nodes:
        node_content = getattr(node, "content", "") or ""
        if not node_content:
            continue
        fm = getattr(node, "frontmatter", None)
        if fm and getattr(fm, "status", "") in ("superseded", "archived"):
            continue
        # Check scope match
        extra = getattr(fm, "extra", {}) if fm else {}
        node_scope = extra.get("scope", "")
        if scope and node_scope and scope != node_scope:
            continue
        sim = similarity(content, node_content)
        if sim >= SAME_FACT_SIMILARITY:
            return node
    return None


#: Markers of an explicit revision ("I now prefer X", "actually...", "no longer..."). Only a
#: statement that reads as a correction may supersede an existing fact — an ordinary new
#: statement never overwrites an unrelated one (§9: changed preferences must not stay current,
#: but casual statements must not destroy unrelated facts either).
_CORRECTION_MARKERS = (
    "actually", "now prefer", "now like", "now use", "no longer", "instead of",
    "changed my mind", "anymore", "from now on", "not anymore",
)

#: Content-token overlap (of the smaller set) between a correction and an existing same-type
#: fact for the correction to count as revising it. Measured conservative: "I now prefer light
#: mode everywhere" vs "I prefer dark mode for all interfaces" shares {prefer, mode} → 0.4 ✓;
#: an unrelated "I prefer vim keybindings" shares only {prefer} → 0.33 and does NOT supersede.
CORRECTION_TOPIC_OVERLAP = 0.4


def _looks_like_correction(text: str) -> bool:
    low = (text or "").lower()
    return any(marker in low for marker in _CORRECTION_MARKERS)


def _find_supersede_target(vault: Any, content: str, fact_type: str, scope: str = "") -> Optional[Any]:
    """The best existing same-type fact this correction most likely revises, or ``None``.

    Deterministic and conservative: same fact_type, compatible scope, not already superseded,
    and a meaningful content-token overlap with the new statement. Ties break on recency.
    """
    try:
        nodes = [n for n in vault.list_all_nodes() if getattr(n, "category", "") == USER_CATEGORY]
    except Exception:
        return None
    tokens = token_set(content)
    if not tokens:
        return None
    best = None
    best_key = (0.0, -1)
    for node in nodes:
        fm = getattr(node, "frontmatter", None)
        if fm is None or getattr(fm, "status", "") in ("superseded", "archived"):
            continue
        extra = getattr(fm, "extra", {}) or {}
        if extra.get("fact_type", FACT_TYPE_PREFERENCE) != fact_type:
            continue
        node_scope = extra.get("scope", "")
        if scope and node_scope and scope != node_scope:
            continue
        node_tokens = token_set(getattr(node, "content", "") or "")
        if not node_tokens:
            continue
        overlap = len(tokens & node_tokens) / max(1, min(len(tokens), len(node_tokens)))
        if overlap < CORRECTION_TOPIC_OVERLAP:
            continue
        recency = int(getattr(fm, "updated_at", 0) or getattr(fm, "created_at", 0) or 0)
        if (overlap, recency) > best_key:
            best, best_key = node, (overlap, recency)
    return best


def record_user_fact(
    vault: Any,
    content: str,
    *,
    fact_type: str = FACT_TYPE_PREFERENCE,
    source: str = FACT_SOURCE_EXPLICIT,
    scope: str = "",
    evidence_id: str = "",
    now: Optional[int] = None,
    _skip_correction: bool = False,
) -> Dict[str, Any]:
    """Record a fact about the user, or reinforce/supersede an existing one.

    Returns a report dict: ``{"action": "created"|"reinforced"|"superseded"|"skipped", ...}``.
    Never raises.
    """
    now_ts = int(now if now is not None else time.time())
    content = (content or "").strip()
    if not content:
        return {"action": "skipped", "reason": "empty content"}

    try:
        existing = _find_existing_fact(vault, content, scope=scope)
    except Exception:
        existing = None

    # An explicit correction that is not a restatement revises the fact it is about: the new
    # statement becomes current and the old one is superseded (history preserved). This is the
    # §9 "a preference that changed must not be retrieved as unconditionally current" rule.
    if existing is None and not _skip_correction and _looks_like_correction(content):
        try:
            target = _find_supersede_target(vault, content, fact_type, scope)
        except Exception:
            target = None
        if target is not None:
            return supersede_user_fact(
                vault,
                target.id,
                content,
                source=FACT_SOURCE_CORRECTED,
                evidence_id=evidence_id,
                now=now_ts,
            )

    if existing is not None:
        # Reinforce the existing fact's evidence count
        try:
            fm = existing.frontmatter
            extra = dict(fm.extra) if fm.extra else {}
            ev_count = int(extra.get("evidence_count", 1)) + 1
            extra["evidence_count"] = ev_count
            existing_refs = extra.get("evidence_refs", [])
            if not isinstance(existing_refs, list):
                existing_refs = [str(existing_refs)] if existing_refs else []
            if evidence_id and evidence_id not in existing_refs:
                existing_refs = list(existing_refs) + [evidence_id]
                extra["evidence_refs"] = existing_refs

            # If this was inferred, update confidence with more evidence
            new_confidence = None
            if extra.get("fact_source") == FACT_SOURCE_INFERRED:
                new_confidence = inferred_confidence(ev_count)
                extra["confidence_score"] = new_confidence

            vault.write_node(
                existing.id,
                existing.content,
                extra=extra,
                confidence=float(extra.get("confidence_score", fm.confidence)),
                now=now_ts,
            )
        except Exception:
            pass
        return {"action": "reinforced", "node_id": existing.id, "evidence_count": ev_count}

    # Create a new fact
    slug = slugify(content[:60])
    node_id = f"{USER_CATEGORY}/{slug}"
    node_id = normalize_node_id(node_id)

    confidence = EXPLICIT_CONFIDENCE if source == FACT_SOURCE_EXPLICIT else inferred_confidence(1)
    if source == FACT_SOURCE_CORRECTED:
        confidence = CORRECTION_CONFIDENCE

    extra: Dict[str, Any] = {
        "fact_type": fact_type,
        "fact_source": source,
        "confidence_score": confidence,
        "evidence_count": 1,
        "valid_from": now_ts,
    }
    if scope:
        extra["scope"] = scope
    if evidence_id:
        extra["evidence_refs"] = [evidence_id]

    tags = [fact_type, source]
    if scope:
        tags.append(f"scope:{scope}")

    try:
        vault.write_node(
            node_id,
            content,
            title=content[:80],
            category=USER_CATEGORY,
            tags=tags,
            salience=0.9 if source == FACT_SOURCE_EXPLICIT else 0.7,
            confidence=confidence,
            source_turn=evidence_id or None,
            extra=extra,
        )
    except Exception:
        return {"action": "skipped", "reason": "write failed"}

    return {"action": "created", "node_id": node_id, "fact_type": fact_type, "source": source}


def supersede_user_fact(
    vault: Any,
    old_node_id: str,
    new_content: str,
    *,
    source: str = FACT_SOURCE_CORRECTED,
    evidence_id: str = "",
    now: Optional[int] = None,
) -> Dict[str, Any]:
    """Supersede an existing user fact with a new one, preserving history.

    The old fact is marked superseded with ``valid_until`` set. The new fact
    carries a ``supersedes`` relation to the old one. Never raises.
    """
    now_ts = int(now if now is not None else time.time())
    try:
        old_node = vault.read_node(old_node_id)
    except Exception:
        return {"action": "skipped", "reason": "old node not found"}
    if old_node is None:
        return {"action": "skipped", "reason": "old node not found"}

    # Determine fact_type and scope from the old node
    old_fm = old_node.frontmatter
    old_extra = dict(old_fm.extra) if old_fm.extra else {}
    fact_type = old_extra.get("fact_type", FACT_TYPE_PREFERENCE)
    scope = old_extra.get("scope", "")

    # Create new fact
    result = record_user_fact(
        vault,
        new_content,
        fact_type=fact_type,
        source=source,
        scope=scope,
        evidence_id=evidence_id,
        now=now_ts,
        _skip_correction=True,  # this call IS the supersede; do not look for another target
    )
    new_node_id = result.get("node_id", "")

    if not new_node_id or result.get("action") == "skipped":
        return result

    # Mark old as superseded
    try:
        old_extra["valid_until"] = now_ts
        old_extra["superseded_by"] = new_node_id
        vault.write_node(
            old_node_id,
            old_node.content,
            status=NodeStatus.SUPERSEDED.value,
            supersedes=[],
            extra=old_extra,
            now=now_ts,
        )
    except Exception:
        pass

    # Add supersedes relation on new node
    try:
        new_node = vault.read_node(new_node_id)
        if new_node:
            rels = list(new_node.typed_relations())
            new_rel = Relation(
                target=old_node_id,
                rel_type="supersedes",
                created_at=now_ts,
            )
            merge_into(rels, new_rel)
            rel_tokens = dump_relations(rels)
            new_supersedes = list(new_node.frontmatter.supersedes or [])
            if old_node_id not in new_supersedes:
                new_supersedes.append(old_node_id)
            vault.write_node(
                new_node_id,
                new_node.content,
                relations=rel_tokens,
                supersedes=new_supersedes,
                now=now_ts,
            )
    except Exception:
        pass

    return {
        "action": "superseded",
        "node_id": new_node_id,
        "old_node_id": old_node_id,
        "new_node_id": new_node_id,
    }


def revise_user_fact(
    vault: Any,
    node_id: str,
    *,
    new_confidence: Optional[float] = None,
    new_scope: Optional[str] = None,
    evidence_id: str = "",
    now: Optional[int] = None,
) -> Dict[str, Any]:
    """Update metadata on an existing user fact without changing its content.

    For content changes, use ``supersede_user_fact`` instead. Never raises.
    """
    now_ts = int(now if now is not None else time.time())
    try:
        node = vault.get_node(node_id)
    except Exception:
        return {"action": "skipped", "reason": "node not found"}
    if node is None:
        return {"action": "skipped", "reason": "node not found"}

    fm = node.frontmatter
    extra = dict(fm.extra) if fm.extra else {}

    if new_confidence is not None:
        extra["confidence_score"] = round(float(new_confidence), 4)
        fm.confidence = round(float(new_confidence), 4)
    if new_scope is not None:
        extra["scope"] = new_scope
    if evidence_id:
        existing_refs = extra.get("evidence_refs", [])
        if not isinstance(existing_refs, list):
            existing_refs = [str(existing_refs)] if existing_refs else []
        if evidence_id not in existing_refs:
            existing_refs = list(existing_refs) + [evidence_id]
            extra["evidence_refs"] = existing_refs
            extra["evidence_count"] = int(extra.get("evidence_count", 1)) + 1

    fm.extra.update(extra)
    fm.updated_at = now_ts

    try:
        vault.write_node(node_id, node.content, frontmatter=fm)
    except Exception:
        return {"action": "skipped", "reason": "write failed"}
    return {"action": "revised", "node_id": node_id}


def facts_for_scope(vault: Any, scope: str = "") -> List[UserFact]:
    """All active user facts, optionally filtered by scope.

    Returns global facts and scope-matching facts (never facts scoped to
    a different project). Never raises.
    """
    try:
        nodes = [n for n in vault.list_all_nodes() if getattr(n, "category", "") == USER_CATEGORY]
    except Exception:
        return []
    facts: List[UserFact] = []
    for node in nodes:
        fm = getattr(node, "frontmatter", None)
        if not fm:
            continue
        if getattr(fm, "status", "active") in ("superseded", "archived"):
            continue
        extra = getattr(fm, "extra", {}) or {}
        node_scope = extra.get("scope", "")
        # Include if: no scope filter, or the fact is global, or scopes match
        if scope and node_scope and scope != node_scope:
            continue
        fact = UserFact(
            content=getattr(node, "content", "") or getattr(fm, "title", ""),
            fact_type=extra.get("fact_type", FACT_TYPE_PREFERENCE),
            source=extra.get("fact_source", FACT_SOURCE_EXPLICIT),
            confidence=float(extra.get("confidence_score", fm.confidence)),
            scope=node_scope,
            valid_from=extra.get("valid_from"),
            valid_until=extra.get("valid_until"),
            evidence_count=int(extra.get("evidence_count", 1)),
            node_id=node.id,
        )
        facts.append(fact)
    return facts


def user_preferences(vault: Any, scope: str = "") -> List[UserFact]:
    """Active preferences, ordered by confidence then recency."""
    return sorted(
        [f for f in facts_for_scope(vault, scope) if f.fact_type == FACT_TYPE_PREFERENCE],
        key=lambda f: (-f.confidence, -(f.valid_from or 0)),
    )


def user_goals(vault: Any, scope: str = "") -> List[UserFact]:
    """Active goals."""
    return sorted(
        [f for f in facts_for_scope(vault, scope) if f.fact_type == FACT_TYPE_GOAL],
        key=lambda f: (-f.confidence, -(f.valid_from or 0)),
    )


def active_projects(vault: Any) -> List[Dict[str, Any]]:
    """List of active project nodes with their status.

    Projects live in the ``project/`` category under the regular vault conventions.
    This function reads them and returns metadata useful for the user-model snapshot.
    """
    try:
        nodes = [n for n in vault.list_all_nodes() if getattr(n, "category", "") == PROJECT_CATEGORY]
    except Exception:
        return []
    projects = []
    for node in nodes:
        fm = getattr(node, "frontmatter", None)
        if not fm:
            continue
        status = getattr(fm, "status", "active")
        if status in ("archived",):
            continue
        extra = getattr(fm, "extra", {}) or {}
        projects.append({
            "node_id": node.id,
            "title": getattr(fm, "title", node.id),
            "status": status,
            "confidence": fm.confidence,
            "tags": list(getattr(fm, "tags", [])),
            "updated_at": fm.updated_at,
            "scope": extra.get("scope", ""),
        })
    projects.sort(key=lambda p: -(p.get("updated_at") or 0))
    return projects


@dataclass
class UserModelSnapshot:
    """Read-only projection of the user's current state.

    Rebuilt from evidence each time — never a cached source of truth.
    """
    preferences: List[UserFact] = field(default_factory=list)
    goals: List[UserFact] = field(default_factory=list)
    constraints: List[UserFact] = field(default_factory=list)
    patterns: List[UserFact] = field(default_factory=list)
    identity: List[UserFact] = field(default_factory=list)
    context: List[UserFact] = field(default_factory=list)
    projects: List[Dict[str, Any]] = field(default_factory=list)
    generated_at: int = 0

    @property
    def explicit_count(self) -> int:
        return sum(1 for f in self.all_facts if f.source == FACT_SOURCE_EXPLICIT)

    @property
    def inferred_count(self) -> int:
        return sum(1 for f in self.all_facts if f.source == FACT_SOURCE_INFERRED)

    @property
    def all_facts(self) -> List[UserFact]:
        return (
            self.preferences + self.goals + self.constraints +
            self.patterns + self.identity + self.context
        )


def current_model(vault: Any, scope: str = "") -> UserModelSnapshot:
    """Build a snapshot of the user's current state from vault evidence.

    This is a projection, not a store — it is rebuilt each time to reflect
    the latest evidence. Scoped facts are included alongside global ones.
    Never raises.
    """
    all_facts = facts_for_scope(vault, scope)

    def _by_type(ft: str) -> List[UserFact]:
        return sorted(
            [f for f in all_facts if f.fact_type == ft],
            key=lambda f: (-f.confidence, -(f.valid_from or 0)),
        )

    return UserModelSnapshot(
        preferences=_by_type(FACT_TYPE_PREFERENCE),
        goals=_by_type(FACT_TYPE_GOAL),
        constraints=_by_type(FACT_TYPE_CONSTRAINT),
        patterns=_by_type(FACT_TYPE_PATTERN),
        identity=_by_type(FACT_TYPE_IDENTITY),
        context=_by_type(FACT_TYPE_CONTEXT),
        projects=active_projects(vault),
        generated_at=int(time.time()),
    )


def render_model_block(snapshot: UserModelSnapshot, *, max_chars: int = 2000) -> str:
    """Render the user model as a text block for context injection.

    Prioritises explicit preferences and identity, then goals, then patterns.
    Respects the character budget. Never raises.
    """
    lines: List[str] = []

    def _add_section(title: str, facts: List[UserFact], prefix: str = "-") -> None:
        if not facts:
            return
        lines.append(f"{title}:")
        for f in facts[:5]:  # cap per section
            marker = "[explicit]" if f.source == FACT_SOURCE_EXPLICIT else "[inferred]"
            scope_tag = f" ({f.scope})" if f.scope else ""
            lines.append(f"  {prefix} {marker} {f.content}{scope_tag}")

    _add_section("Identity", snapshot.identity)
    _add_section("Preferences", snapshot.preferences)
    _add_section("Goals", snapshot.goals)
    _add_section("Constraints", snapshot.constraints)
    _add_section("Patterns", snapshot.patterns)

    if snapshot.projects:
        lines.append("Active projects:")
        for p in snapshot.projects[:5]:
            lines.append(f"  - {p['title']} [{p['status']}]")

    text = "\n".join(lines)
    if len(text) > max_chars:
        text = text[:max_chars - 3] + "..."
    return text


# ── turn-time capture (wired from session.brain_encode_turn) ────────────────

#: Preference-statement markers. Deliberately high-precision: only an explicit
#: statement of liking/wanting/hating/being becomes a user-model fact directly.
#: Tooling facts ("I use vim") are classified as context, not preference — liking
#: and using are different claims.
_PREFERENCE_PATTERNS = (
    re.compile(
        r"\bi (?:(?:really|now|actually|currently|still|just|personally|definitely|only)\s+)*"
        r"(?:prefer|like|love|hate|dislike|favor|favour)\b.{3,}",
        re.IGNORECASE,
    ),
    re.compile(r"\b(?:always|never) (?:use|respond|reply|answer|write)\b.{3,}", re.IGNORECASE),
    re.compile(r"\b(?:please )?(?:always|never)\b.{3,}", re.IGNORECASE),
    re.compile(r"\bi (?:no longer|don'?t|do not) (?:like|prefer|want|use|need)\b.{2,}", re.IGNORECASE),
    re.compile(r"\bmy (?:favorite|preferred)\b.{3,}", re.IGNORECASE),
)

#: Identity statements: who the user is.
_IDENTITY_PATTERNS = (
    re.compile(r"\bi (?:am|work as|'?m) (?:a |an )?[A-Z]", re.IGNORECASE),
    re.compile(r"\bcall me\b.{2,}", re.IGNORECASE),
    re.compile(r"\bmy name is\b.{2,}", re.IGNORECASE),
)

#: Goal markers: an explicitly stated objective.
_GOAL_PATTERNS = (
    re.compile(r"\bi (?:want|need|plan|intend|aim) to\b.{3,}", re.IGNORECASE),
    re.compile(r"\bmy goal is\b.{3,}", re.IGNORECASE),
    re.compile(r"\bi'?m (?:trying|working) to\b.{3,}", re.IGNORECASE),
    re.compile(r"\b(?:my |the )?(?:objective|target|milestone) is\b.{3,}", re.IGNORECASE),
)

#: Constraint markers: stated limitations.
_CONSTRAINT_PATTERNS = (
    re.compile(r"\bi (?:can'?t|cannot|can not|must not|shouldn'?t)\b.{2,}", re.IGNORECASE),
    re.compile(r"\bi (?:only|just) have\b.{2,}", re.IGNORECASE),
    re.compile(r"\b(?:no|without) (?:internet|network|access|budget|account)\b.{0,40}", re.IGNORECASE),
)

#: Context/environment markers: what the user works with.
_CONTEXT_PATTERNS = (
    re.compile(r"\bi (?:use|run|have installed|work on)\b.{3,}", re.IGNORECASE),
    re.compile(r"\bmy (?:machine|laptop|server|stack|setup|environment) (?:is|runs|has)\b.{2,}", re.IGNORECASE),
)

#: Project markers: statements about a named project's state.
_PROJECT_STATE_PATTERNS = (
    re.compile(r"\b(?:the\s+)?(\w[\w.-]{1,40})\s+project\s+(?:is\s+)?(paused|shelved|cancelled|canceled|done|complete|active|back on)\b", re.IGNORECASE),
    re.compile(r"\bwe (?:just )?(?:shipped|launched|deployed|abandoned|dropped)\s+(?:the\s+)?(\w[\w.-]{0,40})\b", re.IGNORECASE),
)


def extract_from_text(text: str) -> List[Dict[str, Any]]:
    """Extract all user-model facts from a message.

    Splits the message into statements (same rules as encoding) and classifies each.
    Returns a list (possibly empty). High-precision: false positives pollute the model.
    """
    text = (text or "").strip()
    if not text:
        return []
    results: List[Dict[str, Any]] = []
    # Use the same statement-splitting rules as the encoder for consistency.
    try:
        from . import encoding as enc_mod
        statements = enc_mod._statements(text)
    except Exception:
        # Fallback: treat the whole text as one candidate statement
        statements = [text] if len(text.split()) >= 4 else []

    for stmt in statements:
        stmt = stmt.strip()
        if not stmt:
            continue
        classified = _classify_one(stmt)
        if classified:
            results.append(classified)
    return results


def _classify_one(text: str) -> Optional[Dict[str, Any]]:
    """Best-effort classification of a statement as a user-model fact.

    Returns ``None`` for statements that carry no user-model signal. Deterministic
    and deliberately high-precision: a misclassified statement pollutes the user
    model, and a missed one is only a missed memory. Priority: constraint > identity
    > goal > preference > context — a constraint outranks a preference because
    "I can't use X" is not a mere dislike, and an identity claim beats a preference
    about the same words.
    """
    text = (text or "").strip()
    if not text:
        return None

    # Identity first: "call me X" / "my name is X" / "I am a <role>"
    for pat in _IDENTITY_PATTERNS:
        if pat.search(text):
            return {"fact_type": FACT_TYPE_IDENTITY, "content": text}

    # Constraint: a stated limitation outranks a preference (it is actionable).
    for pat in _CONSTRAINT_PATTERNS:
        if pat.search(text):
            return {"fact_type": FACT_TYPE_CONSTRAINT, "content": text}

    # Goal: forward-looking intention ("I want to build X").
    for pat in _GOAL_PATTERNS:
        if pat.search(text):
            return {"fact_type": FACT_TYPE_GOAL, "content": text}

    # Preference: stable likes/dislikes/habits.
    for pat in _PREFERENCE_PATTERNS:
        if pat.search(text):
            return {"fact_type": FACT_TYPE_PREFERENCE, "content": text}

    # Context: environment and tooling facts.
    for pat in _CONTEXT_PATTERNS:
        if pat.search(text):
            return {"fact_type": FACT_TYPE_CONTEXT, "content": text}

    # Project-state statements are deliberately NOT user-model facts: they belong
    # to the project layer (the vault's project/ category via ordinary encoding).
    return None


_PROJECT_STATE_RE = re.compile(
    r"\b(?:the\s+)?(\w[\w.-]{1,40})\s+project\s+(?:is\s+)?"
    r"(paused|shelved|cancelled|canceled|completed|done|shipped|abandoned|dropped|active|live)\b",
    re.IGNORECASE,
)
