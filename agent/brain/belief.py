"""Belief Evolution & Cognitive Misconception Resolution Engine.

Manages hypotheses, evolving belief networks, confidence calibration,
and automatic resolution of outdated beliefs/misconceptions with full audit trails.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from agent.brain.models import BeliefStatus, BrainNode, NodeCategory
from agent.brain.vault import BrainVault, normalize_node_id
from agent.brain.graph import BrainGraph

logger = logging.getLogger(__name__)


class BeliefEngine:
    """Manages dynamic beliefs, hypothesis tracking, and misconception updates."""

    def __init__(self, vault: Optional[BrainVault] = None, graph: Optional[BrainGraph] = None):
        self.vault = vault or BrainVault()
        self.graph = graph or BrainGraph(self.vault)

    def record_belief(
        self,
        title: str,
        content: str,
        confidence: float = 0.9,
        tags: Optional[List[str]] = None,
        related_nodes: Optional[List[str]] = None,
        node_id: Optional[str] = None,
    ) -> BrainNode:
        """Record a new belief or working hypothesis."""
        slug = node_id or f"beliefs/{title}"
        tags = tags or ["belief"]
        if "belief" not in tags:
            tags.append("belief")

        return self.vault.write_node(
            node_id=slug,
            content=content,
            title=title,
            category=NodeCategory.BELIEF.value,
            tags=tags,
            confidence=confidence,
            status=BeliefStatus.ACTIVE.value,
            related=related_nodes or [],
        )

    def evolve_belief(
        self,
        old_belief_id: str,
        new_title: str,
        new_content: str,
        reason: str,
        new_confidence: float = 1.0,
        new_tags: Optional[List[str]] = None,
    ) -> Tuple[BrainNode, Optional[BrainNode]]:
        """Resolve an outdated belief or misconception with an updated one.
        
        - Marks old belief as SUPERSEDED with link to new belief and explanation.
        - Creates new belief with link to old belief in frontmatter (`supersedes`).
        - Preserves cognitive continuity and learning history.
        """
        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        old_node = self.graph.get_node(old_belief_id)

        # 1. Create the new evolved belief
        new_id = f"beliefs/{new_title}"
        tags = new_tags or (old_node.frontmatter.tags if old_node else ["belief", "evolved"])
        if "evolved" not in tags:
            tags.append("evolved")

        # Include forward link to old belief in body or metadata
        body_with_ref = (
            f"{new_content.strip()}\n\n"
            f"### Evolution History\n"
            f"- **Evolution Date**: {now_iso}\n"
            f"- **Supersedes**: [[{old_node.id if old_node else old_belief_id}]]\n"
            f"- **Reason for Revision**: {reason}\n"
        )

        new_node = self.vault.write_node(
            node_id=new_id,
            content=body_with_ref,
            title=new_title,
            category=NodeCategory.BELIEF.value,
            tags=tags,
            confidence=new_confidence,
            status=BeliefStatus.ACTIVE.value,
            supersedes=old_node.id if old_node else old_belief_id,
            related=[old_node.id] if old_node else [],
        )

        # 2. Update the old belief to mark it superseded / misconception resolved
        updated_old_node: Optional[BrainNode] = None
        if old_node:
            superseded_annotation = (
                f"\n\n> [!WARNING] Superseded Belief / Misconception Resolved\n"
                f"> **Status**: Superseded on {now_iso}\n"
                f"> **Replaced by**: [[{new_node.id}|{new_node.title}]]\n"
                f"> **Resolution Reason**: {reason}\n"
            )
            updated_old_content = old_node.content + superseded_annotation

            updated_old_node = self.vault.write_node(
                node_id=old_node.id,
                content=updated_old_content,
                title=old_node.title,
                category=old_node.category,
                tags=list(set(old_node.frontmatter.tags + ["superseded"])),
                confidence=0.0,
                status=BeliefStatus.SUPERSEDED.value,
                related=list(set(old_node.frontmatter.related + [new_node.id])),
            )

        # Rebuild graph to reflect new connections
        self.graph.rebuild_index()

        return new_node, updated_old_node

    def mark_disproved(
        self,
        belief_id: str,
        counter_evidence: str,
    ) -> Optional[BrainNode]:
        """Mark a belief as verified misconception / disproved."""
        node = self.graph.get_node(belief_id)
        if not node:
            return None

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        annotation = (
            f"\n\n> [!CAUTION] Disproved Misconception\n"
            f"> **Disproved Date**: {now_iso}\n"
            f"> **Counter Evidence**: {counter_evidence}\n"
        )

        return self.vault.write_node(
            node_id=node.id,
            content=node.content + annotation,
            title=node.title,
            category=node.category,
            tags=list(set(node.frontmatter.tags + ["disproved", "misconception"])),
            confidence=0.0,
            status=BeliefStatus.DISPROVED.value,
            related=node.frontmatter.related,
        )

    def list_active_beliefs(self) -> List[BrainNode]:
        """Retrieve all currently active beliefs and mental models."""
        self.graph.rebuild_index()
        return [
            node for node in self.graph._nodes.values()
            if node.category == NodeCategory.BELIEF.value and node.frontmatter.status == BeliefStatus.ACTIVE.value
        ]
