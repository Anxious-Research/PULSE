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
            "associative_nodes": associative_nodes[:4],
            "active_beliefs": active_beliefs[:4],
            "resolved_misconceptions": resolved_misconceptions[:3],
        }

    def format_consciousness_stream(self, prompt: str) -> Optional[str]:
        """Format an evocative, highly structured cognitive consciousness stream."""
        activated = self.activate_memory_network(prompt)
        primary = activated["primary_nodes"]
        associative = activated["associative_nodes"]
        beliefs = activated["active_beliefs"]
        misconceptions = activated["resolved_misconceptions"]

        if not primary and not beliefs and not associative:
            return None

        stream_lines: List[str] = [
            "<pulse_consciousness_stream>",
            "# Internal Cognitive Resonance & Lived Memory",
            "The user's input has activated the following interconnected memory network in your brain:\n"
        ]

        # 1. Primary Activated Memories
        if primary:
            stream_lines.append("## Core Activated Memories")
            for node in primary:
                snippet = node.content.strip().splitlines()[0] if node.content.strip() else ""
                conf_str = f" [{int(node.frontmatter.confidence * 100)}% conf]" if node.frontmatter.confidence is not None else ""
                stream_lines.append(f"- **[[{node.id}|{node.title}]]** ({node.category}){conf_str}: {snippet}")
                if len(node.wikilinks) > 0:
                    stream_lines.append(f"  *Linked*: {[w.target for w in node.wikilinks[:3]]}")
            stream_lines.append("")

        # 2. Associative Knowledge (Connected 1-hop / 2-hop concepts)
        if associative:
            stream_lines.append("## Associative Context (Linked Network)")
            for node in associative:
                snippet = node.content.strip().splitlines()[0] if node.content.strip() else ""
                stream_lines.append(f"- **[[{node.id}|{node.title}]]** ({node.category}): {snippet}")
            stream_lines.append("")

        # 3. Active Beliefs & Mental Models
        if beliefs:
            stream_lines.append("## Active Working Beliefs")
            for b in beliefs:
                snippet = b.content.strip().splitlines()[0] if b.content.strip() else ""
                stream_lines.append(f"- **[[{b.id}|{b.title}]]** (Verified Belief): {snippet}")
            stream_lines.append("")

        # 4. Resolved Misconceptions Alert
        if misconceptions:
            stream_lines.append("## Historical Misconception Alert (Do NOT revert to these)")
            for m in misconceptions:
                stream_lines.append(f"- *Superseded premise*: {m['old']} (Replaced by newer understanding).")
            stream_lines.append("")

        stream_lines.extend([
            "## Cognitive Integration Guidance",
            "- Speak naturally from these internalized memories as a human answers from consciousness.",
            "- Do not quote this block verbatim; integrate the insights seamlessly into your thoughts and actions.",
            "</pulse_consciousness_stream>"
        ])

        return "\n".join(stream_lines)
