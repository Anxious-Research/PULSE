"""Data models for PULSE Native Brain & Knowledge Graph Engine.

Represents interconnected knowledge nodes, wikilinks, frontmatter metadata,
and evolving belief states.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set


class BeliefStatus(str, Enum):
    """Lifecycle state of a cognitive belief or hypothesis."""
    ACTIVE = "active"             # Current verified fact / working belief
    PROVISIONAL = "provisional"   # Working hypothesis requiring more evidence
    SUPERSEDED = "superseded"     # Replaced by newer / more accurate knowledge
    DISPROVED = "disproved"       # Verified misconception or false premise


class NodeCategory(str, Enum):
    """Core cognitive categories in PULSE Brain Vault."""
    SELF = "self"           # Pulse self-knowledge: identity, anatomy, tools, capabilities
    USER = "user"           # User knowledge: life, preferences, mental models, style
    CONCEPT = "concept"     # Domain & world concepts, technical architectures
    BELIEF = "belief"       # Evolving beliefs, hypotheses, and resolved misconceptions
    PROJECT = "project"     # Active projects, codebase states, workflows


@dataclass
class FrontmatterMetadata:
    """Parsed YAML frontmatter metadata from a Markdown note."""
    title: str = ""
    category: str = "concept"
    tags: List[str] = field(default_factory=list)
    confidence: float = 1.0          # 0.0 to 1.0 confidence score
    status: str = "active"           # BeliefStatus string
    aliases: List[str] = field(default_factory=list)
    related: List[str] = field(default_factory=list)
    supersedes: Optional[str] = None # Title/ID of older belief this replaces
    superseded_by: Optional[str] = None # Title/ID of newer belief replacing this
    created_at: Optional[int] = None
    updated_at: Optional[int] = None
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "title": self.title,
            "category": self.category,
            "tags": self.tags,
            "confidence": self.confidence,
            "status": self.status,
            "aliases": self.aliases,
            "related": self.related,
        }
        if self.supersedes:
            d["supersedes"] = self.supersedes
        if self.superseded_by:
            d["superseded_by"] = self.superseded_by
        if self.created_at:
            d["created_at"] = self.created_at
        if self.updated_at:
            d["updated_at"] = self.updated_at
        if self.extra:
            d.update(self.extra)
        return d


@dataclass
class WikilinkTarget:
    """Parsed [[target|alias]] representation."""
    target: str                  # Target note title or relative path
    alias: Optional[str] = None  # Custom display text if specified
    heading: Optional[str] = None # #heading if specified
    raw: str = ""                # Exact raw string e.g. "[[User Preferences|prefs]]"


@dataclass
class BrainNode:
    """First-class knowledge node in the PULSE Brain."""
    id: str                                # Unique slug or normalized title (e.g. "self/architecture")
    title: str                             # Display title
    category: str                          # NodeCategory value
    path: str                              # Relative path within brain vault (e.g. "self/architecture.md")
    content: str                           # Markdown body (excluding frontmatter)
    raw_markdown: str                      # Full markdown including YAML frontmatter
    frontmatter: FrontmatterMetadata = field(default_factory=FrontmatterMetadata)
    wikilinks: List[WikilinkTarget] = field(default_factory=list)  # Forward links from this node
    backlinks: List[str] = field(default_factory=list)             # Incoming node IDs pointing here
    timestamp: Optional[int] = None
    size_bytes: int = 0

    def to_summary_dict(self) -> Dict[str, Any]:
        """Compact summary representation for graph rendering and agent lists."""
        return {
            "id": self.id,
            "title": self.title,
            "category": self.category,
            "path": self.path,
            "tags": self.frontmatter.tags,
            "confidence": self.frontmatter.confidence,
            "status": self.frontmatter.status,
            "timestamp": self.timestamp,
            "wikilinks_count": len(self.wikilinks),
            "backlinks_count": len(self.backlinks),
        }

    def to_full_dict(self) -> Dict[str, Any]:
        """Complete representation including content and forward/backward links."""
        return {
            **self.to_summary_dict(),
            "content": self.content,
            "wikilinks": [w.target for w in self.wikilinks],
            "backlinks": self.backlinks,
            "aliases": self.frontmatter.aliases,
            "supersedes": self.frontmatter.supersedes,
            "superseded_by": self.frontmatter.superseded_by,
        }


@dataclass
class BrainEdge:
    """Bi-directional relationship between two brain nodes."""
    source: str                  # Origin node ID
    target: str                  # Destination node ID
    relation_type: str = "link"  # 'link' (wikilink), 'supersedes', 'parent', 'concept'
    weight: float = 1.0
    context: Optional[str] = None # Sentence or snippet surrounding the link

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "target": self.target,
            "relation_type": self.relation_type,
            "weight": self.weight,
        }
