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
    cleaned = raw_id.strip().replace("\\", "/")
    if cleaned.endswith(".md"):
        cleaned = cleaned[:-3]
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
        "daily": "daily",
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
        try:
            from agent.brain.self_knowledge import populate_self_knowledge
            populate_self_knowledge(self)
        except Exception as e:
            logger.debug("Failed to seed self knowledge: %s", e)

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

    def rename_node(self, old_node_id: str, new_node_id: str) -> bool:
        """Rename a note and cascade [[wikilink]] updates across every note in the vault."""
        with _VAULT_LOCK:
            old_norm = normalize_node_id(old_node_id)
            new_norm = normalize_node_id(new_node_id)
            if old_norm == new_norm:
                return True

            old_file = self.vault_dir / f"{old_norm}.md"
            new_file = self.vault_dir / f"{new_norm}.md"

            if not old_file.exists():
                return False

            new_file.parent.mkdir(parents=True, exist_ok=True)
            old_node = self.read_node(old_norm)
            old_title = old_node.title if old_node else old_norm.split("/")[-1]
            new_title = new_norm.split("/")[-1].replace("-", " ").title()

            # Move the file
            old_file.rename(new_file)

            # Update the renamed note's internal title if matching old slug
            renamed_node = self.read_node(new_norm)
            if renamed_node:
                self.write_node(
                    node_id=new_norm,
                    content=renamed_node.content,
                    title=new_title,
                    category=new_norm.split("/")[0],
                    tags=renamed_node.frontmatter.tags,
                    confidence=renamed_node.frontmatter.confidence,
                    status=renamed_node.frontmatter.status,
                )

            # Cascade refactoring across all vault markdown notes
            old_targets = [old_norm, old_title, old_norm.split("/")[-1]]
            for md_path in self.vault_dir.rglob("*.md"):
                try:
                    text = md_path.read_text(encoding="utf-8")
                    changed = False
                    for tgt in old_targets:
                        # Regex replacing [[tgt]] or [[tgt|alias]] or [[tgt#heading]]
                        pat = re.compile(rf"\[\[{re.escape(tgt)}(\|[^\]\n]+|#[^\]\n]+)?\]\]", re.IGNORECASE)
                        if pat.search(text):
                            def _repl(m: re.Match) -> str:
                                suffix = m.group(1) or ""
                                return f"[[{new_norm}{suffix}]]"
                            text = pat.sub(_repl, text)
                            changed = True

                    if changed:
                        md_path.write_text(text, encoding="utf-8")
                except Exception as e:
                    logger.error("Failed to cascade rename into %s: %s", md_path, e)

            return True

    def linkify_mention(self, source_node_id: str, target_node_id: str, term: str) -> bool:
        """Convert a plain-text unlinked mention into an active [[wikilink]]."""
        with _VAULT_LOCK:
            source_norm = normalize_node_id(source_node_id)
            target_norm = normalize_node_id(target_node_id)

            node = self.read_node(source_norm)
            if not node or not term:
                return False

            # Replace first unlinked occurrence in body (outside of existing [[...]])
            pattern = re.compile(rf"(?<!\[\[)\b{re.escape(term)}\b(?!\]\])", re.IGNORECASE)
            if not pattern.search(node.content):
                return False

            replacement = f"[[{target_norm}|{term}]]" if target_norm.split("/")[-1] != term.lower() else f"[[{target_norm}]]"
            new_content = pattern.sub(replacement, node.content, count=1)

            self.write_node(
                node_id=source_norm,
                content=new_content,
                title=node.title,
                category=node.category,
                tags=node.frontmatter.tags,
                confidence=node.frontmatter.confidence,
                status=node.frontmatter.status,
            )
            return True

    def get_or_create_daily_note(self, date_str: Optional[str] = None) -> BrainNode:
        """Create or retrieve today's chronological daily note (Obsidian Daily Notes)."""
        if not date_str:
            date_str = time.strftime("%Y-%m-%d")

        daily_id = f"daily/{date_str}"
        node = self.read_node(daily_id)
        if node:
            return node

        initial_content = f"""# Daily Journal — {date_str}

## Notes & Sessions
- Daily journal and cognitive reflections for {date_str}.

*Related*: [[self/identity]]
"""
        return self.write_node(
            node_id=daily_id,
            content=initial_content.strip(),
            title=f"Daily Note {date_str}",
            category="daily",
            tags=["daily-note", "journal"],
            confidence=1.0,
            status="active",
        )

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
