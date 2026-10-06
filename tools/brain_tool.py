"""PULSE Brain Tool — Native Cognitive Knowledge Base and Graph Engine.

Provides agentic operations over the persistent Brain Vault:
- read: Read note content, frontmatter, [[wikilinks]], and incoming backlinks
- write: Create or update a note with YAML frontmatter, tags, category, and confidence
- patch: Targeted string replacement in a note
- delete: Remove a note
- search: Keyword search with category, tag, or status filters
- explore: Traverse local graph neighborhood around a node (backlinks + forward links)
- evolve_belief: Evolve a belief/hypothesis, resolving misconceptions with audit trail
- graph_stats: Get global knowledge graph density, cluster distribution, and counts
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from agent.brain.vault import BrainVault
from agent.brain.graph import BrainGraph
from agent.brain.belief import BeliefEngine
from agent.brain.models import NodeCategory, BeliefStatus
from tools.registry import registry, tool_error

logger = logging.getLogger(__name__)

BRAIN_SCHEMA = {
    "name": "brain",
    "description": (
        "Interact with PULSE's native cognitive Brain and Knowledge Graph. "
        "Unlike flat memory files, the Brain stores interconnected Markdown nodes "
        "with [[wikilinks]], backlinks, structured frontmatter properties, confidence scores, "
        "and evolving belief states across Self (anatomy/tools), User (profile/preferences), "
        "Concepts (domain knowledge), Beliefs (hypotheses/misconceptions), and Projects."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "patch", "delete", "search", "explore", "evolve_belief", "graph_stats", "unlinked_mentions", "rename", "create_daily", "link_mention"],
                "description": "Brain action to execute.",
            },
            "node_id": {
                "type": "string",
                "description": "Node identifier or path (e.g. 'self/identity', 'user/preferences', 'concepts/python-async', 'beliefs/architecture-model').",
            },
            "title": {
                "type": "string",
                "description": "Display title of the note.",
            },
            "category": {
                "type": "string",
                "enum": ["self", "user", "concept", "belief", "project"],
                "description": "Cognitive category of the note.",
            },
            "content": {
                "type": "string",
                "description": "Markdown body content (supports [[wikilinks]] like [[self/identity]] or [[Note Title|alias]]).",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "List of tags associated with the note (e.g. ['python', 'backend', 'p1']).",
            },
            "confidence": {
                "type": "number",
                "description": "Confidence score from 0.0 (unverified hypothesis) to 1.0 (verified fact).",
            },
            "status": {
                "type": "string",
                "enum": ["active", "provisional", "superseded", "disproved"],
                "description": "Status of the belief or note.",
            },
            "query": {
                "type": "string",
                "description": "Search query for 'search' action.",
            },
            "depth": {
                "type": "integer",
                "description": "Graph traversal depth for 'explore' action (default: 1).",
            },
            "old_string": {
                "type": "string",
                "description": "Target string to replace for 'patch' action.",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement string for 'patch' action.",
            },
            "reason": {
                "type": "string",
                "description": "Reason for revision when evolving beliefs or resolving misconceptions.",
            },
            "aliases": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Alternative titles/aliases for the note.",
            },
            "related": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Explicit related node IDs or titles.",
            },
        },
        "required": ["action"],
    },
}


def check_brain_requirements() -> bool:
    """Brain tool is built-in and always available."""
    return True


def brain_tool(
    action: str,
    node_id: Optional[str] = None,
    title: Optional[str] = None,
    category: Optional[str] = None,
    content: Optional[str] = None,
    tags: Optional[List[str]] = None,
    confidence: float = 1.0,
    status: str = "active",
    query: Optional[str] = None,
    depth: int = 1,
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    reason: Optional[str] = None,
    aliases: Optional[List[str]] = None,
    related: Optional[List[str]] = None,
    vault: Optional[BrainVault] = None,
    **kwargs: Any,
) -> Any:
    """Execute requested brain action."""
    vault_inst = vault or BrainVault()
    graph_inst = BrainGraph(vault_inst)
    belief_engine = BeliefEngine(vault_inst, graph_inst)

    action = (action or "").strip().lower()

    try:
        if action == "read":
            if not node_id:
                return tool_error("Missing required parameter 'node_id' for action='read'")
            node = graph_inst.get_node(node_id)
            if not node:
                return json.dumps({"found": False, "message": f"Brain node '{node_id}' not found."}, indent=2, ensure_ascii=False)
            return json.dumps({"found": True, "node": node.to_full_dict()}, indent=2, ensure_ascii=False)

        elif action == "write":
            if not node_id:
                return tool_error("Missing required parameter 'node_id' for action='write'")
            if content is None:
                return tool_error("Missing required parameter 'content' for action='write'")

            node = vault_inst.write_node(
                node_id=node_id,
                content=content,
                title=title,
                category=category,
                tags=tags,
                confidence=confidence,
                status=status,
                aliases=aliases,
                related=related,
            )
            return json.dumps({"success": True, "node": node.to_summary_dict()}, indent=2, ensure_ascii=False)

        elif action == "patch":
            if not node_id:
                return tool_error("Missing required parameter 'node_id' for action='patch'")
            if old_string is None or new_string is None:
                return tool_error("Missing 'old_string' or 'new_string' for action='patch'")

            patched_node = vault_inst.patch_node(node_id, old_string, new_string)
            if not patched_node:
                return tool_error(f"Brain node '{node_id}' not found.")
            return json.dumps({"success": True, "node": patched_node.to_summary_dict()}, indent=2, ensure_ascii=False)

        elif action == "delete":
            if not node_id:
                return tool_error("Missing required parameter 'node_id' for action='delete'")
            deleted = vault_inst.delete_node(node_id)
            return json.dumps({"success": deleted, "node_id": node_id}, indent=2, ensure_ascii=False)

        elif action == "search":
            results = graph_inst.search_nodes(
                query=query or "",
                category=category,
                status=status,
                limit=kwargs.get("limit", 20),
            )
            return json.dumps({"total_matches": len(results), "results": results}, indent=2, ensure_ascii=False)

        elif action == "explore":
            if not node_id:
                return tool_error("Missing required parameter 'node_id' for action='explore'")
            subgraph = graph_inst.get_neighbors(node_id, depth=max(1, min(depth, 3)))
            return json.dumps(subgraph, indent=2, ensure_ascii=False)

        elif action == "evolve_belief":
            if not node_id:
                return tool_error("Missing required parameter 'node_id' (old belief ID) for evolve_belief")
            if not title or not content:
                return tool_error("Missing 'title' or 'content' for the new evolved belief")

            new_node, old_node = belief_engine.evolve_belief(
                old_belief_id=node_id,
                new_title=title,
                new_content=content,
                reason=reason or "Updated based on new evidence",
                new_confidence=confidence,
                new_tags=tags,
            )
            return json.dumps({
                "success": True,
                "new_belief": new_node.to_summary_dict(),
                "superseded_belief": old_node.to_summary_dict() if old_node else None,
            }, indent=2, ensure_ascii=False)

        elif action == "graph_stats":
            return json.dumps(graph_inst.get_full_graph_payload(), indent=2, ensure_ascii=False)

        elif action == "unlinked_mentions":
            if not node_id:
                return tool_error("Missing required parameter 'node_id' for action='unlinked_mentions'")
            from agent.brain.cache import MetadataCache
            cache = MetadataCache(vault_inst)
            mentions = cache.find_unlinked_mentions(node_id, limit=kwargs.get("limit", 30))
            return json.dumps({"node_id": node_id, "total_unlinked": len(mentions), "mentions": mentions}, indent=2, ensure_ascii=False)

        elif action == "rename":
            if not node_id or not new_string:
                return tool_error("Missing required parameter 'node_id' (old ID) and 'new_string' (new ID) for rename")
            ok = vault_inst.rename_node(node_id, new_string)
            return json.dumps({"success": ok, "old_id": node_id, "new_id": new_string}, indent=2, ensure_ascii=False)

        elif action == "create_daily":
            daily_node = vault_inst.get_or_create_daily_note(date_str=query)
            return json.dumps({"success": True, "node": daily_node.to_summary_dict()}, indent=2, ensure_ascii=False)

        elif action == "link_mention":
            if not node_id or not title:
                return tool_error("Missing required parameter 'node_id' (source note) and 'title' (target note ID or term) for link_mention")
            term = kwargs.get("term", title.split("/")[-1])
            ok = vault_inst.linkify_mention(node_id, title, term)
            return json.dumps({"success": ok, "source_id": node_id, "target_id": title, "term": term}, indent=2, ensure_ascii=False)

        else:
            return tool_error(f"Unknown brain action: '{action}'. Supported actions: read, write, patch, delete, search, explore, evolve_belief, graph_stats, unlinked_mentions, rename, create_daily, link_mention")

    except Exception as e:
        logger.exception("Error executing brain tool action %s: %s", action, e)
        return tool_error(f"Brain tool error: {str(e)}")


registry.register(
    name="brain",
    toolset="brain",
    schema=BRAIN_SCHEMA,
    handler=lambda args, **kw: brain_tool(
        action=args.get("action", ""),
        node_id=args.get("node_id"),
        title=args.get("title"),
        category=args.get("category"),
        content=args.get("content"),
        tags=args.get("tags"),
        confidence=float(args.get("confidence", 1.0)),
        status=args.get("status", "active"),
        query=args.get("query"),
        depth=int(args.get("depth", 1)),
        old_string=args.get("old_string"),
        new_string=args.get("new_string"),
        reason=args.get("reason"),
        aliases=args.get("aliases"),
        related=args.get("related"),
        **kw,
    ),
    check_fn=check_brain_requirements,
    emoji="🧠",
)
