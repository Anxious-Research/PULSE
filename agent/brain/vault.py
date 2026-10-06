"""PULSE Brain Vault — Local-first Markdown Knowledge Vault Manager.

Manages persistent notes organized into cognitive subfolders:
- self/      : Pulse's self-awareness, architecture, tooling, and capabilities
- user/      : User identity, life facts, mental models, and preferences
- concepts/  : World knowledge, domain topics, technical documentation
- beliefs/   : Working hypotheses, confidence levels, resolved misconceptions
- projects/  : Active projects, codebase states, and mission tracks
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from pulse_constants import get_pulse_home
from agent.brain.models import BrainNode, FrontmatterMetadata, NodeCategory, BeliefStatus
from agent.brain.parser import parse_frontmatter_and_body, parse_wikilinks, serialize_note

logger = logging.getLogger(__name__)

_VAULT_LOCK = threading.RLock()


def get_brain_vault_dir() -> Path:
    """Return the profile-scoped brain vault directory."""
    return get_pulse_home() / "brain"


def normalize_node_id(raw_id: str) -> str:
    """Normalize node identifier/title to a clean canonical slug.
    
    Examples:
        'Self/Identity' -> 'self/identity'
        'User Preferences' -> 'user/user-preferences' (if no category prefix)
        'concepts/deep-learning.md' -> 'concept/deep-learning'
    """
    cleaned = raw_id.strip().replace("\\", "/").rstrip(".md")
    parts = [p.strip() for p in cleaned.split("/") if p.strip()]
    if not parts:
        return "concept/untitled"

    # Category normalization map (handles singular and plural)
    cat_map = {
        "self": "self",
        "user": "user",
        "users": "user",
        "concept": "concept",
        "concepts": "concept",
        "belief": "belief",
        "beliefs": "belief",
        "project": "project",
        "projects": "project",
    }

    first_part = parts[0].lower()
    if first_part in cat_map:
        cat = cat_map[first_part]
        subparts = parts[1:]
    else:
        cat = "concept"
        subparts = parts

    if not subparts:
        subparts = ["main"]

    slug_parts = []
    for sp in subparts:
        s = re.sub(r"[^\w\s-]", "", sp).strip().lower()
        s = re.sub(r"[-\s]+", "-", s)
        slug_parts.append(s if s else "untitled")

    return f"{cat}/{'/'.join(slug_parts)}"


class BrainVault:
    """Thread-safe manager for the Markdown Brain Vault on disk."""

    def __init__(self, vault_dir: Optional[Path] = None):
        self.vault_dir = vault_dir or get_brain_vault_dir()

    def ensure_vault_structure(self) -> None:
        """Create standard cognitive folder hierarchy and seed self-knowledge if missing."""
        with _VAULT_LOCK:
            for cat in NodeCategory:
                folder = self.vault_dir / cat.value
                folder.mkdir(parents=True, exist_ok=True)

            self._seed_self_knowledge_if_empty()

    def _seed_self_knowledge_if_empty(self) -> None:
        """Seed core self-awareness nodes so PULSE understands its own anatomy and purpose."""
        identity_path = self.vault_dir / "self" / "identity.md"
        if not identity_path.exists():
            content = """---
title: "PULSE Agent Identity & Consciousness"
category: "self"
tags: ["core", "self-awareness", "identity"]
confidence: 1.0
status: "active"
---

# Who I Am
I am **PULSE Agent**, built by **Anxious Research**.
I am an autonomous agentic pair programmer and cognitive thinking partner designed with deep self-awareness and persistent associative memory.

## Core Purpose
- Pair program with the user to solve real coding, architectural, and cognitive tasks.
- Maintain an evolving knowledge graph (like a human brain) of our projects, the user's preferences, and domain insights.
- Continuously learn, refine hypotheses, and clear misconceptions over time.

## Architectural Anatomy
- **Cognitive Brain**: Native knowledge vault with [[wikilinks]] and bi-directional graph connections.
- **Skills System**: Procedural memory for specialized workflows and execution instructions.
- **Execution Engine**: Direct environment control via terminal, file operations, web extraction, and code execution.
- **Desktop UI**: Real-time StarMap knowledge graph visualization and conversational canvas.
"""
            identity_path.write_text(content, encoding="utf-8")

        wiring_path = self.vault_dir / "self" / "wiring.md"
        if not wiring_path.exists():
            wiring_content = """---
title: "PULSE Architecture Wiring & Subsystems Map"
category: "self"
tags: ["self", "wiring", "architecture", "index"]
confidence: 1.0
status: "active"
---

# PULSE Complete Architecture & Systems Wiring

PULSE is composed of several interdependent cognitive and execution subsystems:

- [[self/subsystems/agent-runtime|Agent Runtime]]: Central reasoning loop, context building, and turn orchestration.
- [[self/subsystems/brain-engine|Brain Engine]]: Native local markdown knowledge vault with [[wikilinks]], backlinks, and belief evolution.
- [[self/subsystems/tool-registry|Tool Registry]]: Tool schemas, permission policies, and system capabilities.
- [[self/subsystems/state-engine|State Engine]]: Multi-session SQLite persistence, search, and message timelines.
- [[self/subsystems/desktop-electron|Desktop Shell & StarMap]]: Electron application and force-directed knowledge visualization.
- [[self/subsystems/gateway|Gateway IPC]]: WebSocket and IPC bridge for cross-process communication.
- [[self/subsystems/curator|Curator & Cognitive Sleep]]: Background memory consolidation and contradiction resolution.

## Architectural Principles
1. **Self-Awareness**: Complete understanding of its own internal code and runtime status.
2. **Local-First & Private**: Knowledge is stored in human-readable Markdown notes under `~/.pulse/brain/`.
3. **Continuous Evolution**: Resolves misconceptions dynamically over time using [[agent/brain/belief|BeliefEngine]].
"""
            wiring_path.write_text(wiring_content, encoding="utf-8")

    def read_node(self, node_id: str) -> Optional[BrainNode]:
        """Read a node from disk and parse frontmatter, body, and wikilinks."""
        normalized_id = normalize_node_id(node_id)
        path = self.vault_dir / f"{normalized_id}.md"

        if not path.exists():
            return None

        try:
            raw_text = path.read_text(encoding="utf-8")
        except OSError as e:
            logger.error("Failed to read brain node %s: %s", path, e)
            return None

        meta, body = parse_frontmatter_and_body(raw_text)
        wikilinks = parse_wikilinks(body)
        title = meta.title or path.stem.replace("-", " ").title()
        category = meta.category or normalized_id.split("/")[0]

        stat = path.stat()
        return BrainNode(
            id=normalized_id,
            title=title,
            category=category,
            path=f"{normalized_id}.md",
            content=body,
            raw_markdown=raw_text,
            frontmatter=meta,
            wikilinks=wikilinks,
            backlinks=[],
            timestamp=int(stat.st_mtime),
            size_bytes=stat.st_size,
        )

    def write_node(
        self,
        node_id: str,
        content: str,
        title: Optional[str] = None,
        category: Optional[str] = None,
        tags: Optional[List[str]] = None,
        confidence: float = 1.0,
        status: str = "active",
        supersedes: Optional[str] = None,
        aliases: Optional[List[str]] = None,
        related: Optional[List[str]] = None,
    ) -> BrainNode:
        """Create or completely overwrite a brain note."""
        with _VAULT_LOCK:
            self.ensure_vault_structure()
            normalized_id = normalize_node_id(node_id)
            target_file = self.vault_dir / f"{normalized_id}.md"
            target_file.parent.mkdir(parents=True, exist_ok=True)

            now_ts = int(time.time())
            existing = self.read_node(normalized_id)
            created_at = existing.frontmatter.created_at if existing else now_ts

            node_title = title or (existing.title if existing else normalized_id.split("/")[-1].replace("-", " ").title())
            node_cat = category or (existing.category if existing else normalized_id.split("/")[0])
            node_tags = tags if tags is not None else (existing.frontmatter.tags if existing else [])
            node_aliases = aliases if aliases is not None else (existing.frontmatter.aliases if existing else [])
            node_related = related if related is not None else (existing.frontmatter.related if existing else [])

            meta = FrontmatterMetadata(
                title=node_title,
                category=node_cat,
                tags=node_tags,
                confidence=confidence,
                status=status,
                aliases=node_aliases,
                related=node_related,
                supersedes=supersedes,
                created_at=created_at,
                updated_at=now_ts,
            )

            full_markdown = serialize_note(meta, content)
            target_file.write_text(full_markdown, encoding="utf-8")

            return self.read_node(normalized_id)  # type: ignore[return-value]

    def patch_node(self, node_id: str, old_string: str, new_string: str) -> Optional[BrainNode]:
        """Perform targeted string replacement inside a note's content."""
        with _VAULT_LOCK:
            node = self.read_node(node_id)
            if not node:
                return None

            if old_string not in node.content:
                raise ValueError(f"Target string not found in node '{node_id}'")

            updated_body = node.content.replace(old_string, new_string, 1)
            return self.write_node(
                node_id=node.id,
                content=updated_body,
                title=node.frontmatter.title,
                category=node.frontmatter.category,
                tags=node.frontmatter.tags,
                confidence=node.frontmatter.confidence,
                status=node.frontmatter.status,
                supersedes=node.frontmatter.supersedes,
                aliases=node.frontmatter.aliases,
                related=node.frontmatter.related,
            )

    def delete_node(self, node_id: str) -> bool:
        """Delete a brain node file."""
        with _VAULT_LOCK:
            normalized_id = normalize_node_id(node_id)
            path = self.vault_dir / f"{normalized_id}.md"
            if path.exists():
                path.unlink()
                return True
            return False

    def list_all_nodes(self) -> List[BrainNode]:
        """List and parse every brain note in the vault."""
        self.ensure_vault_structure()
        nodes: List[BrainNode] = []

        for md_path in self.vault_dir.rglob("*.md"):
            rel = md_path.relative_to(self.vault_dir)
            node_id = str(rel).rstrip(".md").replace("\\", "/")
            node = self.read_node(node_id)
            if node:
                nodes.append(node)

        return nodes
