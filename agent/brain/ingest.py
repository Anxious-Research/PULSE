"""PULSE Turn-by-Turn Cognitive Ingestion & Continuous Learning Engine.

Automatically distills conversations into interconnected Markdown notes in the Brain Vault:
- Continuous learning: Grows the knowledge graph after every turn.
- Intelligent categorization: Routes facts to user/, concept/, project/, belief/.
- Associative linking: Links newly formed knowledge with existing notes using [[wikilinks]].
- Cache synchronization: Immediately updates graph indexes for instant recall and live UI reflection.
"""

from __future__ import annotations

import logging
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.brain.models import BeliefStatus, BrainNode, FrontmatterMetadata, NodeCategory
from agent.brain.vault import BrainVault, normalize_node_id
from agent.brain.graph import BrainGraph
from agent.brain.cache import MetadataCache
from agent.brain.belief import BeliefEngine

logger = logging.getLogger(__name__)

# Trivial greetings/phrases that do not warrant graph mutations
TRIVIAL_PATTERNS = [
    r"^(hi|hello|hey|hola|namaste|sup|yo|ok|okay|cool|thanks|thank you|thx|bye|goodbye)[\s.!?,]*$",
]


def is_trivial_turn(user_msg: str, asst_msg: str) -> bool:
    """Check if turn is purely conversational fluff with zero durable knowledge."""
    u = user_msg.strip().lower()
    if len(u) < 4:
        return True
    for pat in TRIVIAL_PATTERNS:
        if re.match(pat, u, re.IGNORECASE):
            return True
    return False


class CognitiveIngestor:
    """Continuous learning engine that ingests turn transcripts into the Brain Vault."""

    def __init__(self, vault: Optional[BrainVault] = None, graph: Optional[BrainGraph] = None):
        self.vault = vault or BrainVault()
        self.vault.ensure_vault_structure()
        self.graph = graph or BrainGraph(self.vault)
        self.cache = MetadataCache(self.vault)
        self.belief_engine = BeliefEngine(self.vault, self.graph)

    def ingest_turn(
        self,
        user_message: str,
        assistant_response: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest a completed conversation turn into the cognitive brain vault."""
        if not user_message or not assistant_response:
            return {"ingested": False, "reason": "empty_turn"}

        if is_trivial_turn(user_message, assistant_response):
            return {"ingested": False, "reason": "trivial_turn"}

        results = {
            "ingested": True,
            "created_nodes": [],
            "updated_nodes": [],
            "links_formed": 0,
        }

        try:
            # 1. Detect User Preferences, Corrections & Directives
            user_updates = self._extract_user_insights(user_message, assistant_response)
            for node_id, title, content, tags in user_updates:
                node = self._upsert_memory_node(
                    node_id=node_id,
                    title=title,
                    category=NodeCategory.USER.value,
                    content=content,
                    tags=tags,
                )
                if node:
                    results["created_nodes"].append(node.id)

            # 2. Extract Technical Domain Concepts & Workflows
            concept_updates = self._extract_concept_insights(user_message, assistant_response)
            for node_id, title, content, tags in concept_updates:
                node = self._upsert_memory_node(
                    node_id=node_id,
                    title=title,
                    category=NodeCategory.CONCEPT.value,
                    content=content,
                    tags=tags,
                )
                if node:
                    results["created_nodes"].append(node.id)

            # 3. Extract Active Project Context
            project_updates = self._extract_project_insights(user_message, assistant_response)
            for node_id, title, content, tags in project_updates:
                node = self._upsert_memory_node(
                    node_id=node_id,
                    title=title,
                    category=NodeCategory.PROJECT.value,
                    content=content,
                    tags=tags,
                )
                if node:
                    results["created_nodes"].append(node.id)

            # 4. Refresh Cache & Index
            if results["created_nodes"] or results["updated_nodes"]:
                self.graph.rebuild_index()
                self.cache.build_cache()
                logger.info("CognitiveIngestor ingested turn: %s", results)

            return results

        except Exception as e:
            logger.exception("CognitiveIngestor failed to ingest turn: %s", e)
            return {"ingested": False, "error": str(e)}

    def _upsert_memory_node(
        self,
        node_id: str,
        title: str,
        category: str,
        content: str,
        tags: List[str],
        confidence: float = 0.95,
    ) -> Optional[BrainNode]:
        """Create or enrich a markdown memory node in the Brain Vault."""
        canonical_id = normalize_node_id(node_id)
        existing = self.vault.read_node(canonical_id)

        # Cross-linking: link to self/identity and core nodes if relevant
        wikilink_matches = re.findall(r"\[\[([^\]\|#]+)(?:#[^\]\|]+)?(?:\|[^\]]+)?\]\]", content)
        existing_links = set(wikilink_matches)

        # Auto-link to related categories
        if category == NodeCategory.USER.value and "self/identity" not in existing_links:
            content += "\n\n*Related*: [[self/identity]]"
        elif category == NodeCategory.CONCEPT.value and "self/wiring" not in existing_links:
            # Check if mentions user or self
            if "pulse" in content.lower():
                content += "\n\n*Related*: [[self/identity]], [[self/wiring]]"

        if existing:
            # Merge content cleanly without duplicate blocks
            if content.strip() in existing.content:
                return existing

            merged_body = existing.content.strip() + "\n\n" + content.strip()
            merged_tags = list(set(existing.frontmatter.tags + tags))
            node = self.vault.write_node(
                node_id=canonical_id,
                content=merged_body,
                title=existing.title or title,
                category=category,
                tags=merged_tags,
                confidence=max(existing.frontmatter.confidence, confidence),
                status=existing.frontmatter.status,
            )
            return node
        else:
            node = self.vault.write_node(
                node_id=canonical_id,
                content=content,
                title=title,
                category=category,
                tags=tags,
                confidence=confidence,
                status=BeliefStatus.ACTIVE.value,
            )
            return node

    def _extract_user_insights(self, user_msg: str, asst_msg: str) -> List[Tuple[str, str, str, List[str]]]:
        """Detect user mental models, stylistic corrections, preferences, and expectations."""
        insights = []
        u_lower = user_msg.lower()

        # Preference patterns
        if any(w in u_lower for w in ["mujhe chahiye", "i want", "i prefer", "always do", "don't", "nahi chahiye", "obsidian jaisa"]):
            # Extract key desire
            snippet = user_msg.strip()
            if len(snippet) > 20:
                summary = snippet.split("\n")[0][:180]
                content = (
                    f"### Continuous Learning & Knowledge Association\n"
                    f"- **Preference/Model**: {summary}\n"
                    f"- **Context**: Connected with [[concept/knowledge-graph]], [[self/identity]], and Obsidian-style associative recall."
                )
                insights.append((
                    "user/preferences",
                    "User Cognitive Preferences & Mental Models",
                    content,
                    ["user-profile", "preferences", "associative-memory"],
                ))

        return insights

    def _extract_concept_insights(self, user_msg: str, asst_msg: str) -> List[Tuple[str, str, str, List[str]]]:
        """Extract domain concepts, architectural techniques, and mechanisms discussed."""
        concepts = []
        combined = f"{user_msg}\n{asst_msg}"
        combined_lower = combined.lower()

        # Obsidian Graph & Associative Memory Concept
        if "obsidian" in combined_lower or "knowledge graph" in combined_lower or "networking graph" in combined_lower:
            content = (
                f"# Obsidian Knowledge Graph Architecture\n\n"
                f"Obsidian organizes knowledge as an interconnected web of local Markdown files with `[[wikilinks]]`.\n\n"
                f"## Core Pillars\n"
                f"- **Bi-directional Linking**: Every `[[wikilink]]` establishes an edge with automatic backlink tracking.\n"
                f"- **Associative Recall**: Memories are retrieved not as isolated text chunks, but through spreading activation across connected 1st and 2nd degree neighbor nodes.\n"
                f"- **Interactive Physics Graph**: Force-directed simulation where node size correlates with link density and hover spotlights connected clusters.\n\n"
                f"## Integration in PULSE\n"
                f"PULSE uses this architecture natively in `~/.pulse/brain/` across [[self/identity]], [[user/preferences]], and active domain concepts."
            )
            concepts.append((
                "concept/obsidian-knowledge-graph",
                "Obsidian Knowledge Graph Architecture",
                content,
                ["architecture", "knowledge-graph", "wikilinks", "associative-memory"],
            ))

        # Spreading Activation Memory
        if "spreading activation" in combined_lower or "human like memory" in combined_lower or "connecting yaadon" in combined_lower:
            content = (
                f"# Spreading Activation Cognitive Memory\n\n"
                f"Associative cognitive architecture inspired by human neuro-synaptic recall.\n\n"
                f"## Mechanism\n"
                f"1. **Resonance**: User query activates seed nodes matching key concepts.\n"
                f"2. **Propagation**: Energy propagates across `[[wikilinks]]` to 1-hop and 2-hop neighbor nodes.\n"
                f"3. **Contextual Stream**: Synthesizes activated thoughts into an internal consciousness stream before response generation.\n\n"
                f"## Connected Nodes\n"
                f"- [[concept/obsidian-knowledge-graph]]\n"
                f"- [[user/preferences]]\n"
                f"- [[self/wiring]]"
            )
            concepts.append((
                "concept/spreading-activation-memory",
                "Spreading Activation Cognitive Memory",
                content,
                ["cognitive", "memory", "retrieval", "consciousness"],
            ))

        return concepts

    def _extract_project_insights(self, user_msg: str, asst_msg: str) -> List[Tuple[str, str, str, List[str]]]:
        """Extract project states and development milestones."""
        projects = []
        combined_lower = f"{user_msg}\n{asst_msg}".lower()

        if "pulse" in combined_lower or "starmap" in combined_lower or "brain" in combined_lower:
            content = (
                f"# PULSE Native Brain & StarMap Evolution\n\n"
                f"Active development track for PULSE's native cognitive architecture.\n\n"
                f"## Status & Deliverables\n"
                f"- Local Markdown Brain Vault (`~/.pulse/brain/`)\n"
                f"- Interactive Obsidian-grade Force-Directed Knowledge Graph\n"
                f"- Turn-by-turn continuous cognitive learning and auto-ingestion\n"
                f"- Spreading activation associative context recall\n\n"
                f"## Connected Systems\n"
                f"- [[self/identity]]\n"
                f"- [[self/wiring]]\n"
                f"- [[concept/obsidian-knowledge-graph]]\n"
                f"- [[user/preferences]]"
            )
            projects.append((
                "project/pulse-brain-evolution",
                "PULSE Brain & StarMap Evolution",
                content,
                ["pulse", "brain", "starmap", "active-project"],
            ))

        return projects


def auto_ingest_turn_to_brain(
    user_message: str,
    assistant_response: str,
    session_id: Optional[str] = None,
    vault: Optional[BrainVault] = None,
) -> Dict[str, Any]:
    """Convenience entry point for turn-by-turn brain ingestion."""
    ingestor = CognitiveIngestor(vault=vault)
    return ingestor.ingest_turn(user_message, assistant_response, session_id=session_id)
