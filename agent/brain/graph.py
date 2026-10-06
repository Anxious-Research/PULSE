"""PULSE Brain Graph — Bi-directional Link Index and Knowledge Graph Engine.

Indexes [[wikilinks]], backlinks, tags, frontmatter relations, and provides
force-directed graph payloads for the PULSE Desktop and Agent retrieval context.
"""

from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.brain.models import BrainEdge, BrainNode, NodeCategory
from agent.brain.vault import BrainVault, normalize_node_id

logger = logging.getLogger(__name__)


class BrainGraph:
    """Graph engine maintaining index of all nodes, forward links, and backlinks."""

    def __init__(self, vault: Optional[BrainVault] = None):
        self.vault = vault or BrainVault()
        self._nodes: Dict[str, BrainNode] = {}
        self._title_to_id: Dict[str, str] = {}
        self._alias_to_id: Dict[str, str] = {}
        self._edges: List[BrainEdge] = []
        self._backlinks: Dict[str, List[str]] = defaultdict(list)

    def rebuild_index(self) -> None:
        """Scan all markdown files in vault and construct bi-directional link index."""
        raw_nodes = self.vault.list_all_nodes()
        self._nodes.clear()
        self._title_to_id.clear()
        self._alias_to_id.clear()
        self._edges.clear()
        self._backlinks.clear()

        # Step 1: Register lookup tables for resolution
        for node in raw_nodes:
            self._nodes[node.id] = node
            self._title_to_id[node.title.lower()] = node.id
            self._title_to_id[node.id.split("/")[-1].lower()] = node.id
            for alias in node.frontmatter.aliases:
                self._alias_to_id[alias.lower()] = node.id

        # Step 2: Resolve forward wikilinks and construct edges
        for node in raw_nodes:
            resolved_targets: Set[str] = set()

            for link in node.wikilinks:
                target_id = self._resolve_target(link.target)
                if target_id and target_id != node.id:
                    resolved_targets.add(target_id)
                    self._backlinks[target_id].append(node.id)
                    self._edges.append(BrainEdge(
                        source=node.id,
                        target=target_id,
                        relation_type="link",
                    ))

            # Also add edges for explicit related frontmatter items
            for rel in node.frontmatter.related:
                target_id = self._resolve_target(rel)
                if target_id and target_id != node.id and target_id not in resolved_targets:
                    resolved_targets.add(target_id)
                    self._backlinks[target_id].append(node.id)
                    self._edges.append(BrainEdge(
                        source=node.id,
                        target=target_id,
                        relation_type="related",
                    ))

            # Explicit supersedes relationship
            if node.frontmatter.supersedes:
                target_id = self._resolve_target(node.frontmatter.supersedes)
                if target_id and target_id != node.id:
                    self._edges.append(BrainEdge(
                        source=node.id,
                        target=target_id,
                        relation_type="supersedes",
                    ))

        # Step 3: Populate backlinks on node instances
        for node_id, backlinks in self._backlinks.items():
            if node_id in self._nodes:
                self._nodes[node_id].backlinks = sorted(list(set(backlinks)))

    def _resolve_target(self, raw_target: str) -> Optional[str]:
        """Resolve a link string (e.g. 'Identity', 'self/identity', 'user-prefs') to a canonical node ID."""
        cleaned = raw_target.strip().rstrip(".md").replace("\\", "/")
        lower = cleaned.lower()

        # Direct ID match
        norm = normalize_node_id(cleaned)
        if norm in self._nodes:
            return norm

        # Title or slug match
        if lower in self._title_to_id:
            return self._title_to_id[lower]

        # Alias match
        if lower in self._alias_to_id:
            return self._alias_to_id[lower]

        # Category-agnostic leaf match
        leaf = lower.split("/")[-1]
        if leaf in self._title_to_id:
            return self._title_to_id[leaf]

        return None

    def get_node(self, node_id: str) -> Optional[BrainNode]:
        """Get an indexed node by ID or resolved title."""
        self.rebuild_index()
        resolved = self._resolve_target(node_id) or normalize_node_id(node_id)
        return self._nodes.get(resolved)

    def get_neighbors(self, node_id: str, depth: int = 1) -> Dict[str, Any]:
        """Retrieve local subgraph surrounding a node up to specified depth."""
        self.rebuild_index()
        target_id = self._resolve_target(node_id) or normalize_node_id(node_id)
        if target_id not in self._nodes:
            return {"nodes": [], "edges": []}

        visited_nodes: Set[str] = {target_id}
        current_layer: Set[str] = {target_id}

        for _ in range(depth):
            next_layer: Set[str] = set()
            for n_id in current_layer:
                # Forward links
                node = self._nodes.get(n_id)
                if node:
                    for link in node.wikilinks:
                        t = self._resolve_target(link.target)
                        if t and t not in visited_nodes and t in self._nodes:
                            visited_nodes.add(t)
                            next_layer.add(t)
                # Backward links
                for b_id in self._backlinks.get(n_id, []):
                    if b_id not in visited_nodes and b_id in self._nodes:
                        visited_nodes.add(b_id)
                        next_layer.add(b_id)
            current_layer = next_layer

        subgraph_nodes = [self._nodes[n_id].to_full_dict() for n_id in visited_nodes if n_id in self._nodes]
        subgraph_edges = [
            e.to_dict() for e in self._edges
            if e.source in visited_nodes and e.target in visited_nodes
        ]

        return {
            "root": target_id,
            "nodes": subgraph_nodes,
            "edges": subgraph_edges,
        }

    def search_nodes(
        self,
        query: str,
        category: Optional[str] = None,
        tag: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 20,
    ) -> List[Dict[str, Any]]:
        """Search brain nodes with full-text keyword matching and metadata filters."""
        self.rebuild_index()
        q_lower = query.lower().strip()
        results: List[Tuple[float, BrainNode]] = []

        for node in self._nodes.values():
            if category and node.category != category:
                continue
            if tag and tag.lstrip("#") not in node.frontmatter.tags:
                continue
            if status and node.frontmatter.status != status:
                continue

            score = 0.0
            if q_lower:
                # Title match weighs highest
                if q_lower in node.title.lower():
                    score += 10.0
                if q_lower in node.id.lower():
                    score += 8.0
                # Tag match
                if any(q_lower in t.lower() for t in node.frontmatter.tags):
                    score += 5.0
                # Content match
                if q_lower in node.content.lower():
                    score += 2.0
                    # Additional count score
                    count = node.content.lower().count(q_lower)
                    score += min(count * 0.5, 5.0)

                if score > 0:
                    results.append((score, node))
            else:
                # If query is empty, return all matching filters sorted by timestamp
                results.append((float(node.timestamp or 0), node))

        results.sort(key=lambda x: x[0], reverse=True)
        return [node.to_summary_dict() for _, node in results[:limit]]

    def get_full_graph_payload(self) -> Dict[str, Any]:
        """Construct full serialized graph payload for PULSE Desktop UI / StarMap with unresolved ghost nodes."""
        self.rebuild_index()

        nodes_payload = []
        clusters_counter = Counter()
        unresolved_ghost_targets: Set[str] = set()

        for node in self._nodes.values():
            clusters_counter[node.category] += 1
            nodes_payload.append({
                "id": node.id,
                "label": node.title,
                "kind": "brain_node",
                "category": node.category,
                "path": node.path,
                "timestamp": node.timestamp,
                "confidence": node.frontmatter.confidence,
                "status": node.frontmatter.status,
                "tags": node.frontmatter.tags,
                "wikilinks_count": len(node.wikilinks),
                "backlinks_count": len(node.backlinks),
                "resolved": True,
            })

            # Detect uncreated / ghost wikilink targets
            for link in node.wikilinks:
                t = self._resolve_target(link.target)
                if not t:
                    clean_ghost = normalize_node_id(link.target)
                    unresolved_ghost_targets.add(clean_ghost)

        # Append ghost / unresolved nodes (Obsidian Ghost Nodes)
        for ghost_id in unresolved_ghost_targets:
            leaf_title = ghost_id.split("/")[-1].replace("-", " ").title()
            cat = ghost_id.split("/")[0]
            clusters_counter["unresolved"] += 1
            nodes_payload.append({
                "id": ghost_id,
                "label": leaf_title,
                "kind": "ghost_node",
                "category": cat,
                "path": f"{ghost_id}.md",
                "timestamp": 0,
                "confidence": 0.0,
                "status": "unresolved",
                "tags": ["ghost"],
                "wikilinks_count": 0,
                "backlinks_count": 1,
                "resolved": False,
            })

        edges_payload = [e.to_dict() for e in self._edges]

        # Add edges connecting to ghost nodes
        for node in self._nodes.values():
            for link in node.wikilinks:
                t = self._resolve_target(link.target)
                if not t:
                    clean_ghost = normalize_node_id(link.target)
                    edges_payload.append({
                        "source": node.id,
                        "target": clean_ghost,
                        "relation_type": "ghost_link",
                    })

        stats = {
            "total_nodes": len(nodes_payload),
            "resolved_nodes": len(self._nodes),
            "unresolved_nodes": len(unresolved_ghost_targets),
            "total_edges": len(edges_payload),
            "categories": len(clusters_counter),
            "self_nodes": clusters_counter.get("self", 0),
            "user_nodes": clusters_counter.get("user", 0),
            "concepts_nodes": clusters_counter.get("concept", 0),
            "beliefs_nodes": clusters_counter.get("belief", 0),
            "projects_nodes": clusters_counter.get("project", 0),
            "daily_nodes": clusters_counter.get("daily", 0),
        }

        return {
            "nodes": nodes_payload,
            "edges": edges_payload,
            "clusters": [{"category": cat, "count": count} for cat, count in clusters_counter.items()],
            "stats": stats,
        }
