"""PULSE Cognitive Sleep & Background Brain Reflection Engine.

Orchestrates background cognitive consolidation during idle cycles:
1. Re-indexes the Brain Vault and updates the MetadataCache.
2. Discovers high-confidence unlinked mentions and strengthens graph edges.
3. Automatically updates self-knowledge if codebase files changed.
4. Identifies obsolete hypotheses and reinforces active beliefs.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

from agent.brain.vault import BrainVault
from agent.brain.graph import BrainGraph
from agent.brain.cache import MetadataCache
from agent.brain.belief import BeliefEngine
from agent.brain.introspect import CodebaseIntrospector
from agent.brain.models import BeliefStatus, NodeCategory

logger = logging.getLogger(__name__)


def reflect_and_consolidate_brain(vault: Optional[BrainVault] = None) -> Dict[str, Any]:
    """Execute a cognitive consolidation pass over the Brain Vault."""
    v = vault or BrainVault()
    v.ensure_vault_structure()

    results: Dict[str, Any] = {
        "self_notes_synced": 0,
        "unlinked_mentions_discovered": 0,
        "beliefs_active": 0,
        "beliefs_superseded": 0,
        "total_nodes": 0,
        "total_edges": 0,
    }

    try:
        # 1. Self-Codebase Knowledge Sync
        introspector = CodebaseIntrospector()
        synced_count = introspector.generate_full_self_knowledge(v)
        results["self_notes_synced"] = synced_count

        # 2. Rebuild Graph & Cache
        graph = BrainGraph(v)
        graph.rebuild_index()

        cache = MetadataCache(v)
        cache.build_cache()

        results["total_nodes"] = len(graph._nodes)
        results["total_edges"] = len(graph._edges)

        # 3. Discover unlinked mentions across all concept and user notes
        unlinked_count = 0
        for node in graph._nodes.values():
            if node.category in (NodeCategory.CONCEPT.value, NodeCategory.USER.value):
                mentions = cache.find_unlinked_mentions(node.id, limit=5)
                unlinked_count += len(mentions)
        results["unlinked_mentions_discovered"] = unlinked_count

        # 4. Audit belief network
        belief_engine = BeliefEngine(v, graph)
        active_beliefs = belief_engine.list_active_beliefs()
        all_beliefs = [n for n in graph._nodes.values() if n.category == NodeCategory.BELIEF.value]

        results["beliefs_active"] = len(active_beliefs)
        results["beliefs_superseded"] = len(all_beliefs) - len(active_beliefs)

        logger.info("Cognitive brain reflection complete: %s", results)
        return results

    except Exception as e:
        logger.exception("Error during cognitive brain reflection: %s", e)
        results["error"] = str(e)
        return results
