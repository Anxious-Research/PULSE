"""PULSE Grounded Cognitive Ingestion Engine.

Captures real user preferences, explicit instructions, corrections, and verified project facts
into the Brain Vault without generating artificial boilerplate or hallucinated notes.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from agent.brain.models import BeliefStatus, BrainNode, NodeCategory
from agent.brain.vault import BrainVault, normalize_node_id
from agent.brain.graph import BrainGraph
from agent.brain.cache import MetadataCache

logger = logging.getLogger(__name__)


def is_trivial_turn(user_msg: str, asst_msg: str) -> bool:
    """Check if turn is purely conversational fluff with zero durable knowledge."""
    u = user_msg.strip().lower()
    return len(u) < 8


class CognitiveIngestor:
    """Ingests genuine user preferences, corrections, and explicit instructions into the Brain Vault."""

    def __init__(self, vault: Optional[BrainVault] = None, graph: Optional[BrainGraph] = None):
        self.vault = vault or BrainVault()
        self.vault.ensure_vault_structure()
        self.graph = graph or BrainGraph(self.vault)
        self.cache = MetadataCache(self.vault)

    def ingest_turn(
        self,
        user_message: str,
        assistant_response: str,
        session_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Ingest real user preferences and corrections into the brain vault."""
        if not user_message or not assistant_response:
            return {"ingested": False, "reason": "empty_turn"}

        if is_trivial_turn(user_message, assistant_response):
            return {"ingested": False, "reason": "trivial_turn"}

        results = {
            "ingested": True,
            "created_nodes": [],
            "updated_nodes": [],
        }

        try:
            user_updates = self._extract_user_preferences(user_message)
            for node_id, title, bullet_text in user_updates:
                node = self._append_user_preference(node_id, title, bullet_text)
                if node:
                    results["updated_nodes"].append(node.id)

            if results["updated_nodes"]:
                self.graph.rebuild_index()
                self.cache.build_cache()

            return results

        except Exception as e:
            logger.exception("CognitiveIngestor failed: %s", e)
            return {"ingested": False, "error": str(e)}

    def _append_user_preference(self, node_id: str, title: str, bullet_text: str) -> Optional[BrainNode]:
        """Append a clean preference bullet to user/preferences.md."""
        canonical_id = normalize_node_id(node_id)
        existing = self.vault.read_node(canonical_id)

        clean_bullet = bullet_text.strip().lstrip("-* ").strip()
        if not clean_bullet:
            return None

        if existing:
            if clean_bullet in existing.content:
                return existing

            updated_content = existing.content.strip() + f"\n- {clean_bullet}"
            return self.vault.write_node(
                node_id=canonical_id,
                content=updated_content,
                title=existing.title or title,
                category=NodeCategory.USER.value,
                tags=existing.frontmatter.tags or ["user-preferences"],
                confidence=1.0,
            )
        else:
            initial_content = f"# User Preferences & Working Style\n\n- {clean_bullet}\n\n*Related*: [[self/identity]]"
            return self.vault.write_node(
                node_id=canonical_id,
                content=initial_content,
                title=title,
                category=NodeCategory.USER.value,
                tags=["user-preferences"],
                confidence=1.0,
            )

    def _extract_user_preferences(self, user_msg: str) -> List[Tuple[str, str, str]]:
        """Extract high-signal user rules, desires, and corrections (filtering out questions and conversational rants)."""
        updates = []
        u_lower = user_msg.lower()

        # Reject questions or meta queries
        if "?" in user_msg or "kyun" in u_lower or "why" in u_lower:
            return []

        # Explicit preference markers
        explicit_triggers = [
            "remember:", "remember that", "always use", "never use", "my preference is",
            "from now on", "rule:"
        ]

        for trig in explicit_triggers:
            if trig in u_lower:
                idx = u_lower.find(trig)
                statement = user_msg[idx + len(trig):].strip().split("\n")[0].strip()
                if len(statement) >= 5:
                    updates.append((
                        "user/preferences",
                        "User Preferences & Expectations",
                        statement,
                    ))
                break

        return updates


def auto_ingest_turn_to_brain(
    user_message: str,
    assistant_response: str,
    session_id: Optional[str] = None,
    vault: Optional[BrainVault] = None,
) -> Dict[str, Any]:
    """Convenience entry point for turn ingestion."""
    ingestor = CognitiveIngestor(vault=vault)
    return ingestor.ingest_turn(user_message, assistant_response, session_id=session_id)
