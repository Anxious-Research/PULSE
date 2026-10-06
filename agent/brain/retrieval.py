"""Cognitive Retrieval & Subgraph Context Assembly for PULSE Agent.

Provides:
1. `build_static_brain_prompt`: Structured cognitive self-awareness and active beliefs
   injected into the system prompt's volatile band.
2. `retrieve_turn_subgraph`: Dynamic semantic/keyword traversal retrieving relevant
   knowledge nodes and connected wikilink neighbors for the current turn context.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.brain.models import BeliefStatus, BrainNode, NodeCategory
from agent.brain.vault import BrainVault
from agent.brain.graph import BrainGraph

logger = logging.getLogger(__name__)


def build_static_brain_prompt(vault: Optional[BrainVault] = None) -> Optional[str]:
    """Assemble authoritative self-awareness, active beliefs, and core user profile for system prompt."""
    try:
        v = vault or BrainVault()
        v.ensure_vault_structure()

        sections: List[str] = []

        # 1. Self-Awareness Summary
        identity = v.read_node("self/identity")
        wiring = v.read_node("self/wiring")

        self_lines = ["# PULSE Native Cognitive Brain & Self-Knowledge\n"]
        if identity:
            self_lines.append(f"## Self Identity\n{identity.content.strip()}\n")
        if wiring:
            self_lines.append(f"## Subsystems & Wiring\n{wiring.content.strip()}\n")

        sections.append("\n".join(self_lines))

        # 2. Active Beliefs & Hypotheses
        graph = BrainGraph(v)
        graph.rebuild_index()

        active_beliefs: List[BrainNode] = [
            n for n in graph._nodes.values()
            if n.category == NodeCategory.BELIEF.value and n.frontmatter.status == BeliefStatus.ACTIVE.value
        ]

        if active_beliefs:
            belief_lines = ["## Active Working Beliefs & Hypotheses\n"]
            for b in active_beliefs[:10]:
                conf_pct = int(b.frontmatter.confidence * 100)
                supersedes_note = f" (supersedes [[{b.frontmatter.supersedes}]])" if b.frontmatter.supersedes else ""
                first_body_line = b.content.strip().splitlines()[0] if b.content.strip() else ""
                belief_lines.append(f"- **[[{b.id}|{b.title}]]** ({conf_pct}% confidence){supersedes_note}: {first_body_line}")
            sections.append("\n".join(belief_lines))

        # 3. User Knowledge
        user_nodes: List[BrainNode] = [
            n for n in graph._nodes.values()
            if n.category == NodeCategory.USER.value
        ]
        if user_nodes:
            user_lines = ["## User Mental Models & Preferences\n"]
            for u in user_nodes[:8]:
                first_line = u.content.strip().splitlines()[0] if u.content.strip() else ""
                user_lines.append(f"- **[[{u.id}|{u.title}]]**: {first_line}")
            sections.append("\n".join(user_lines))

        return "\n\n".join(sections) if sections else None

    except Exception as e:
        logger.debug("Failed to build static brain prompt: %s", e)
        return None


def retrieve_turn_subgraph(
    user_prompt: str,
    vault: Optional[BrainVault] = None,
    graph: Optional[BrainGraph] = None,
    top_k: int = 3,
) -> Optional[str]:
    """Retrieve relevant brain nodes, associative connections, and active beliefs for the current turn."""
    if not user_prompt or len(user_prompt.strip()) < 4:
        return None

    try:
        from agent.brain.consciousness import ConsciousnessEngine
        engine = ConsciousnessEngine(vault, graph)
        return engine.format_consciousness_stream(user_prompt)
    except Exception as e:
        logger.debug("Error generating cognitive consciousness stream: %s", e)
        return None
