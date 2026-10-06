"""PULSE Cognitive Associative Consciousness & Multi-Hop Memory Retrieval Engine.

Implements human-like associative memory recall:
1. Multi-token & Entity Semantic Spreading Activation.
2. 2-Hop Graph Traversal across [[wikilinks]] and backlinks.
3. Belief Coherence & Misconception Filter (prevents outdated memory recall).
4. Native Consciousness Stream context generation for seamless, memory-grounded responses.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.brain.models import BeliefStatus, BrainNode, NodeCategory
from agent.brain.vault import BrainVault
from agent.brain.graph import BrainGraph
from agent.brain.cache import MetadataCache

logger = logging.getLogger(__name__)

# Stopwords to filter out conversational noise
STOPWORDS = {
    "the", "and", "for", "with", "this", "that", "from", "have", "what", "where",
    "when", "how", "why", "who", "which", "will", "would", "could", "should",
    "can", "about", "into", "over", "after", "before", "between", "under", "again",
    "there", "their", "then", "them", "these", "those", "does", "done", "been"
}


class ConsciousnessEngine:
    """Orchestrates human-like associative memory recall and cognitive framing."""

    def __init__(self, vault: Optional[BrainVault] = None, graph: Optional[BrainGraph] = None):
        self.vault = vault or BrainVault()
        self.graph = graph or BrainGraph(self.vault)
        self.cache = MetadataCache(self.vault)

    def extract_associative_tokens(self, prompt: str) -> Set[str]:
        """Extract high-signal semantic tokens and bigrams from user prompt."""
        cleaned = re.sub(r"[^\w\s-]", " ", prompt.lower())
        words = [w.strip() for w in cleaned.split() if len(w.strip()) >= 3 and w.strip() not in STOPWORDS]

        tokens: Set[str] = set(words)
        # Add bigrams (e.g. "sqlite wal", "graph view")
        for i in range(len(words) - 1):
            tokens.add(f"{words[i]} {words[i+1]}")

        return tokens

    def activate_memory_network(self, prompt: str, top_k: int = 5, max_hops: int = 2) -> Dict[str, Any]:
        """Activate the knowledge graph via associative spreading activation.
        
        - Finds 1st-degree resonance nodes from prompt keywords.
        - Spreads activation across [[wikilinks]] and backlinks to find 2nd-degree connected beliefs/user preferences.
        - Filters out superseded misconceptions.
        """
        self.graph.rebuild_index()
        self.cache.build_cache()

        tokens = self.extract_associative_tokens(prompt)
        if not tokens:
            return {"primary_nodes": [], "associative_nodes": [], "active_beliefs": [], "resolved_misconceptions": []}

        # Step 1: Score 1st-degree resonance nodes
        scored_primary: List[Tuple[float, BrainNode]] = []
        for node in self.graph._nodes.values():
            if node.category == NodeCategory.SELF.value:
                continue

            score = 0.0
            title_lower = node.title.lower()
            body_lower = node.content.lower()

            for t in tokens:
                if t == title_lower:
                    score += 15.0
                elif t in title_lower:
                    score += 8.0
                if t in node.frontmatter.tags:
                    score += 6.0
                if t in body_lower:
                    score += min(body_lower.count(t) * 1.5, 6.0)

            # Boost active beliefs and user preferences slightly for human alignment
            if node.category == NodeCategory.USER.value and score > 0:
                score *= 1.3
            if node.category == NodeCategory.BELIEF.value and node.frontmatter.status == BeliefStatus.ACTIVE.value:
                score *= 1.2

            if score >= 3.0:
                scored_primary.append((score, node))

        scored_primary.sort(key=lambda x: x[0], reverse=True)
        primary_nodes = [node for _, node in scored_primary[:top_k]]

        # Step 2: 2-Hop Spreading Activation (traverse outgoing [[wikilinks]] & incoming backlinks)
        visited_ids: Set[str] = {n.id for n in primary_nodes}
        associative_nodes: List[BrainNode] = []
        active_beliefs: List[BrainNode] = []
        resolved_misconceptions: List[Dict[str, Any]] = []

        for p_node in primary_nodes:
            # Outgoing links
            for link in p_node.wikilinks:
                target_node = self.graph.get_node(link.target)
                if target_node and target_node.id not in visited_ids:
                    visited_ids.add(target_node.id)
                    if target_node.category == NodeCategory.BELIEF.value:
                        if target_node.frontmatter.status == BeliefStatus.ACTIVE.value:
                            active_beliefs.append(target_node)
                        elif target_node.frontmatter.status == BeliefStatus.SUPERSEDED.value:
                            resolved_misconceptions.append({
                                "old": target_node.title,
                                "superseded_by": target_node.frontmatter.superseded_by or target_node.frontmatter.related,
                            })
                    else:
                        associative_nodes.append(target_node)

            # Incoming backlinks
            for backlink_id in p_node.backlinks:
                b_node = self.graph.get_node(backlink_id)
                if b_node and b_node.id not in visited_ids:
                    visited_ids.add(b_node.id)
                    if b_node.category == NodeCategory.BELIEF.value:
                        if b_node.frontmatter.status == BeliefStatus.ACTIVE.value:
                            active_beliefs.append(b_node)
                    else:
                        associative_nodes.append(b_node)

        # Also extract superseded misconceptions linked from active beliefs
        for act_b in active_beliefs:
            if act_b.frontmatter.supersedes:
                old_node = self.graph.get_node(act_b.frontmatter.supersedes)
                if old_node:
                    resolved_misconceptions.append({
                        "old": old_node.title,
                        "superseded_by": act_b.title,
                    })

        return {
            "primary_nodes": primary_nodes,
            "associative_nodes": associative_nodes[:3],
            "active_beliefs": active_beliefs[:2],
            "resolved_misconceptions": resolved_misconceptions[:2],
        }

    def format_consciousness_stream(self, prompt: str) -> Optional[str]:
        """Format clean, quiet, compact memory context without theatrical noise or filler."""
        activated = self.activate_memory_network(prompt)
        primary = activated["primary_nodes"]
        beliefs = activated["active_beliefs"]

        # Only inject if there are strong relevant matches (filter out low-signal noise)
        if not primary and not beliefs:
            return None

        lines: List[str] = ["# Relevant Memory & Context"]
        for node in primary[:3]:
            # Extract first non-header, non-frontmatter line as clean snippet
            snippet = ""
            for l in node.content.strip().splitlines():
                cl = l.strip()
                if cl and not cl.startswith("#") and not cl.startswith("---") and not cl.startswith("title:"):
                    snippet = cl
                    break
            if not snippet:
                snippet = node.title
            lines.append(f"- [[{node.id}]]: {snippet}")

        for b in beliefs[:2]:
            lines.append(f"- [[{b.id}]] (Active): {b.title}")

        return "\n".join(lines)
