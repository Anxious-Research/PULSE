"""Obsidian-Grade Metadata Cache & Unlinked Mentions Engine for PULSE Brain.

Mirrors Obsidian's internal MetadataCache architecture:
- `resolved_links`: Source -> Target link matrix with occurrence counts
- `backlinks`: Fast inverted index of incoming references
- `unlinked_mentions`: Full-text scan discovering unlinked references to any note title/alias
- `headings_cache`: Indexed Markdown headings for [[Note#Heading]] deep links
- `tags_cache`: Hierarchical tag taxonomy
"""

from __future__ import annotations

import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.brain.models import BrainNode
from agent.brain.vault import BrainVault, normalize_node_id

logger = logging.getLogger(__name__)

# Heading regex matching # H1, ## H2, etc.
HEADING_PATTERN = re.compile(r"^(?P<level>#{1,6})\s+(?P<heading>.+)$", re.MULTILINE)

# Wikilink pattern for stripping links before checking unlinked mentions
WIKILINK_STRIP_PATTERN = re.compile(r"\[\[[^\]\n]+\]\]")


@dataclass
class HeadingEntry:
    level: int
    text: str
    line: int


@dataclass
class UnlinkedMention:
    source_id: str
    source_title: str
    matched_term: str
    snippet: str
    line_number: int


class MetadataCache:
    """In-memory metadata cache maintaining link matrices and unlinked mention discovery."""

    def __init__(self, vault: Optional[BrainVault] = None):
        self.vault = vault or BrainVault()
        self.resolved_links: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.backlinks: Dict[str, Dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.headings: Dict[str, List[HeadingEntry]] = defaultdict(list)
        self.tags: Dict[str, Set[str]] = defaultdict(set)  # tag -> set of node_ids
        self._nodes: Dict[str, BrainNode] = {}
        self._name_to_id: Dict[str, str] = {}

    def build_cache(self) -> None:
        """Scan vault and populate all indices."""
        all_nodes = self.vault.list_all_nodes()
        self.resolved_links.clear()
        self.backlinks.clear()
        self.headings.clear()
        self.tags.clear()
        self._nodes.clear()
        self._name_to_id.clear()

        # Step 1: Register all nodes and aliases
        for node in all_nodes:
            self._nodes[node.id] = node
            self._name_to_id[node.id.lower()] = node.id
            self._name_to_id[node.title.lower()] = node.id
            self._name_to_id[node.id.split("/")[-1].lower()] = node.id
            for alias in node.frontmatter.aliases:
                self._name_to_id[alias.lower()] = node.id

            # Cache tags
            for tag in node.frontmatter.tags:
                clean_tag = tag.lstrip("#")
                self.tags[clean_tag].add(node.id)

            # Cache headings
            lines = node.content.splitlines()
            for line_idx, line in enumerate(lines, start=1):
                m = HEADING_PATTERN.match(line)
                if m:
                    self.headings[node.id].append(HeadingEntry(
                        level=len(m.group("level")),
                        text=m.group("heading").strip(),
                        line=line_idx,
                    ))

        # Step 2: Build forward resolvedLinks and inverse backlinks
        for node in all_nodes:
            for link in node.wikilinks:
                target_norm = self._resolve_target(link.target, source_id=node.id)
                if target_norm and target_norm != node.id:
                    self.resolved_links[node.id][target_norm] += 1
                    self.backlinks[target_norm][node.id] += 1

            for rel in node.frontmatter.related:
                target_norm = self._resolve_target(rel, source_id=node.id)
                if target_norm and target_norm != node.id:
                    if target_norm not in self.resolved_links[node.id]:
                        self.resolved_links[node.id][target_norm] += 1
                        self.backlinks[target_norm][node.id] += 1

    def _resolve_target(self, raw_name: str, source_id: Optional[str] = None) -> Optional[str]:
        cleaned = raw_name.strip().rstrip(".md").replace("\\", "/")
        lower = cleaned.lower()

        # Check sibling path if source_id is provided
        if source_id and "/" in source_id:
            parent_dir = source_id.rsplit("/", 1)[0]
            sibling = f"{parent_dir}/{cleaned}"
            if sibling.lower() in self._name_to_id:
                return self._name_to_id[sibling.lower()]

        if lower in self._name_to_id:
            return self._name_to_id[lower]
        norm = normalize_node_id(cleaned)
        if norm.lower() in self._name_to_id:
            return self._name_to_id[norm.lower()]
        return None

    def get_backlinks(self, node_id: str) -> List[Dict[str, Any]]:
        """Get all incoming backlinks for a given note with occurrence counts."""
        self.build_cache()
        target = self._resolve_target(node_id) or normalize_node_id(node_id)
        incoming = self.backlinks.get(target, {})

        results = []
        for src_id, count in sorted(incoming.items(), key=lambda x: -x[1]):
            src_node = self._nodes.get(src_id)
            results.append({
                "source_id": src_id,
                "source_title": src_node.title if src_node else src_id,
                "count": count,
            })
        return results

    def find_unlinked_mentions(self, target_id: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Find mentions of target note's title or aliases in other notes that are NOT linked via [[]]."""
        self.build_cache()
        target_norm = self._resolve_target(target_id) or normalize_node_id(target_id)
        target_node = self._nodes.get(target_norm)
        if not target_node:
            return []

        search_terms = {target_node.title}
        for alias in target_node.frontmatter.aliases:
            if alias.strip():
                search_terms.add(alias.strip())
        leaf = target_norm.split("/")[-1].replace("-", " ")
        if len(leaf) >= 4:
            search_terms.add(leaf.title())

        # Filter out overly generic single-word terms to reduce noise
        valid_terms = [t for t in search_terms if len(t) >= 3]
        if not valid_terms:
            return []

        # Construct regex matching word boundaries
        escaped_terms = [re.escape(t) for t in sorted(valid_terms, key=len, reverse=True)]
        pattern_str = r"\b(" + "|".join(escaped_terms) + r")\b"
        mention_regex = re.compile(pattern_str, re.IGNORECASE)

        unlinked_matches: List[UnlinkedMention] = []

        for src_id, src_node in self._nodes.items():
            if src_id == target_norm:
                continue

            # Strip existing [[wikilinks]] so we don't flag already-linked mentions
            stripped_content = WIKILINK_STRIP_PATTERN.sub("", src_node.content)

            lines = stripped_content.splitlines()
            for line_idx, line in enumerate(lines, start=1):
                match = mention_regex.search(line)
                if match:
                    term = match.group(0)
                    start = max(0, match.start() - 40)
                    end = min(len(line), match.end() + 40)
                    snippet = line[start:end].strip()
                    if start > 0:
                        snippet = f"…{snippet}"
                    if end < len(line):
                        snippet = f"{snippet}…"

                    unlinked_matches.append(UnlinkedMention(
                        source_id=src_id,
                        source_title=src_node.title,
                        matched_term=term,
                        snippet=snippet,
                        line_number=line_idx,
                    ))

                    if len(unlinked_matches) >= limit:
                        break
            if len(unlinked_matches) >= limit:
                break

        return [
            {
                "source_id": m.source_id,
                "source_title": m.source_title,
                "matched_term": m.matched_term,
                "snippet": m.snippet,
                "line_number": m.line_number,
            }
            for m in unlinked_matches
        ]
