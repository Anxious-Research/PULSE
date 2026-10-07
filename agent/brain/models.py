"""Data model for brain vault nodes.

A node is one Markdown file plus its frontmatter. The frontmatter *is* the memory
metadata — this is what separates a memory from a plain note:

    stability      how strongly the memory resists forgetting (grows on recall)
    salience       how important it was at encoding time
    last_accessed  when it was last recalled (drives the forgetting curve)
    access_count   how often it has been recalled (spacing effect)
    status         active | superseded | archived | dormant
    supersedes / superseded_by   the correction chain

No third-party imports: ``Enum``/``dataclass`` from the standard library only.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class NodeCategory(str, Enum):
    """Vault folder / node kind. Values are the on-disk directory names."""

    SELF = "self"
    USER = "user"
    CONCEPT = "concept"
    PROJECT = "project"
    BELIEF = "belief"
    DAILY = "daily"

    @classmethod
    def all_values(cls) -> List[str]:
        return [c.value for c in cls]

    @classmethod
    def coerce(cls, value: Any) -> "NodeCategory":
        """Best-effort category from a string; unknown values fall back to CONCEPT."""
        if isinstance(value, NodeCategory):
            return value
        text = str(value or "").strip().lower()
        for c in cls:
            if c.value == text:
                return c
        return cls.CONCEPT


class NodeStatus(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    ARCHIVED = "archived"
    DORMANT = "dormant"


class BeliefStatus(str, Enum):
    ACTIVE = "active"
    UNCERTAIN = "uncertain"
    REFUTED = "refuted"


DEFAULT_STABILITY = 1.0
DEFAULT_SALIENCE = 0.5
DEFAULT_CONFIDENCE = 0.8


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_list(value: Any) -> List[str]:
    """Normalise a frontmatter list field (inline list, block list, or scalar)."""
    if value is None or value == "":
        return []
    if isinstance(value, (list, tuple)):
        return [str(v).strip() for v in value if str(v).strip()]
    return [str(value).strip()] if str(value).strip() else []


@dataclass
class Frontmatter:
    """Typed view of a node's frontmatter block."""

    title: str = ""
    category: str = NodeCategory.CONCEPT.value
    tags: List[str] = field(default_factory=list)
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    last_accessed: Optional[int] = None
    access_count: int = 0
    stability: float = DEFAULT_STABILITY
    salience: float = DEFAULT_SALIENCE
    confidence: float = DEFAULT_CONFIDENCE
    status: str = NodeStatus.ACTIVE.value
    supersedes: List[str] = field(default_factory=list)
    superseded_by: Optional[str] = None
    source_turn: Optional[str] = None
    aliases: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    # -- construction -------------------------------------------------------

    @classmethod
    def from_dict(cls, raw: Optional[Dict[str, Any]], *, node_id: str = "") -> "Frontmatter":
        raw = dict(raw or {})
        category = NodeCategory.coerce(raw.get("category") or (node_id.split("/")[0] if "/" in node_id else ""))
        known = {
            "title", "category", "tags", "created_at", "updated_at", "last_accessed",
            "access_count", "stability", "salience", "confidence", "status",
            "supersedes", "superseded_by", "source_turn", "aliases", "related",
        }
        fallback_title = node_id.split("/")[-1].replace("-", " ").replace("_", " ").strip() or node_id
        return cls(
            title=str(raw.get("title") or fallback_title),
            category=category.value,
            tags=_as_list(raw.get("tags")),
            created_at=_as_int(raw.get("created_at")),
            updated_at=_as_int(raw.get("updated_at")),
            last_accessed=_as_int(raw.get("last_accessed")),
            access_count=_as_int(raw.get("access_count"), 0) or 0,
            stability=_as_float(raw.get("stability"), DEFAULT_STABILITY),
            salience=_as_float(raw.get("salience"), DEFAULT_SALIENCE),
            confidence=_as_float(raw.get("confidence"), DEFAULT_CONFIDENCE),
            status=str(raw.get("status") or NodeStatus.ACTIVE.value),
            supersedes=_as_list(raw.get("supersedes")),
            superseded_by=(str(raw["superseded_by"]) if raw.get("superseded_by") else None),
            source_turn=(str(raw["source_turn"]) if raw.get("source_turn") else None),
            aliases=_as_list(raw.get("aliases")),
            related=_as_list(raw.get("related")),
            extra={k: v for k, v in raw.items() if k not in known},
        )

    def to_dict(self, *, include_empty: bool = True) -> Dict[str, Any]:
        """Frontmatter as an ordered dict, ready to serialise."""
        out: Dict[str, Any] = {
            "title": self.title,
            "category": self.category,
            "tags": list(self.tags),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "last_accessed": self.last_accessed,
            "access_count": self.access_count,
            "stability": round(float(self.stability), 4),
            "salience": round(float(self.salience), 4),
            "confidence": round(float(self.confidence), 4),
            "status": self.status,
        }
        if self.supersedes:
            out["supersedes"] = list(self.supersedes)
        if self.superseded_by:
            out["superseded_by"] = self.superseded_by
        if self.source_turn:
            out["source_turn"] = self.source_turn
        if self.aliases:
            out["aliases"] = list(self.aliases)
        if self.related:
            out["related"] = list(self.related)
        out.update(self.extra)
        if not include_empty:
            out = {k: v for k, v in out.items() if v not in (None, [], "")}
        return out

    # -- lifecycle ----------------------------------------------------------

    def touch_created(self, now: Optional[int] = None) -> int:
        ts = int(now if now is not None else time.time())
        if self.created_at is None:
            self.created_at = ts
        self.updated_at = ts
        return ts


@dataclass
class BrainNode:
    """One memory: an id, its frontmatter, its body, and its outgoing links."""

    id: str
    path: Any  # pathlib.Path — kept loose so this module stays import-trivial
    frontmatter: Frontmatter = field(default_factory=Frontmatter)
    content: str = ""
    raw_markdown: str = ""
    wikilinks: List[Any] = field(default_factory=list)   # parser.WikiLink
    backlinks: List[str] = field(default_factory=list)   # filled by the index

    # -- convenience --------------------------------------------------------

    @property
    def title(self) -> str:
        return self.frontmatter.title or self.id

    @property
    def category(self) -> str:
        return self.frontmatter.category

    @property
    def timestamp(self) -> Optional[int]:
        return self.frontmatter.updated_at or self.frontmatter.created_at

    @property
    def tags(self) -> List[str]:
        return self.frontmatter.tags

    def link_targets(self) -> List[str]:
        return [w.target for w in self.wikilinks]

    def to_summary_dict(self) -> Dict[str, Any]:
        """Compact representation for the graph payload / UI."""
        return {
            "id": self.id,
            "label": self.title,
            "kind": "brain",
            "category": self.category,
            "timestamp": self.timestamp,
            "state": self.frontmatter.status,
            "confidence": self.frontmatter.confidence,
            "salience": self.frontmatter.salience,
            "stability": self.frontmatter.stability,
            "accessCount": self.frontmatter.access_count,
            "tags": list(self.tags),
            "wikilinksCount": len(self.wikilinks),
            "backlinksCount": len(self.backlinks),
        }
