#!/usr/bin/env python3
"""Memory Tool — Native Brain Vault integration for PULSE.

Replaces legacy flat files (MEMORY.md/USER.md) with first-class Markdown notes in the Brain Vault.
- target='user': Writes to user/preferences.md or dedicated user notes
- target='memory': Writes to concept/ or project/ notes
- Full [[wikilinks]] and backlink support
"""

from __future__ import annotations

import copy
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from agent.brain.vault import BrainVault, normalize_node_id
from agent.brain.graph import BrainGraph
from pulse_constants import get_pulse_home
from tools.registry import no_cache_check_fn, registry, tool_error

logger = logging.getLogger(__name__)

MEMORY_SCHEMA = {
    "name": "memory",
    "description": (
        "Store durable facts into PULSE's native Brain Vault (~/.pulse/brain/). "
        "Knowledge is structured as Markdown notes with [[wikilinks]] across "
        "'user' (profile, preferences) and 'memory' (concepts, lessons, domain facts)."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["add", "replace", "remove"],
                "description": "The action to perform on the memory note.",
            },
            "target": {
                "type": "string",
                "enum": ["memory", "user"],
                "description": "'user' for preferences/profile, 'memory' for domain facts/notes.",
            },
            "content": {
                "type": "string",
                "description": "Content of the memory note (supports [[wikilinks]]).",
            },
            "new_text": {
                "type": "string",
                "description": "Alias for content.",
            },
            "old_text": {
                "type": "string",
                "description": "Substring to locate the memory entry for replace or remove.",
            },
            "operations": {
                "type": "array",
                "description": "Batch of operations ({action, content, old_text}) applied sequentially.",
                "items": {
                    "type": "object",
                    "properties": {
                        "action": {"type": "string", "enum": ["add", "replace", "remove"]},
                        "content": {"type": "string"},
                        "old_text": {"type": "string"},
                        "new_text": {"type": "string"},
                    },
                    "required": ["action"],
                },
            },
        },
        "required": ["target"],
    },
}


def get_memory_dir() -> Path:
    """Return the profile-scoped brain vault directory."""
    return get_pulse_home() / "brain"


@no_cache_check_fn
def check_memory_requirements() -> bool:
    """Brain memory is always available."""
    return True


def memory_tool(
    action: Optional[str] = None,
    target: str = "memory",
    content: Optional[str] = None,
    old_text: Optional[str] = None,
    new_text: Optional[str] = None,
    operations: Optional[List[Dict[str, Any]]] = None,
    vault: Optional[BrainVault] = None,
    **kwargs: Any,
) -> str:
    """Execute native brain memory operation."""
    v = vault or BrainVault()
    v.ensure_vault_structure()

    target = "user" if target == "user" else "memory"
    category = "user" if target == "user" else "concept"
    note_slug = f"{category}/preferences" if target == "user" else f"{category}/memory-notes"
    note_title = "User Preferences & Profile" if target == "user" else "Learned Concepts & Notes"

    try:
        if operations:
            for op in operations:
                act = op.get("action")
                if not act:
                    continue
                cnt = op.get("content") or op.get("new_text")
                old = op.get("old_text")
                _apply_single_memory_op(v, act, note_slug, note_title, category, cnt, old)
            return json.dumps({"success": True, "done": True, "target": target, "note": f"Applied {len(operations)} ops to {note_slug}"})

        if not action:
            return tool_error("Missing required parameter 'action' (add, replace, remove)", success=False)

        cnt = content or new_text
        res = _apply_single_memory_op(v, action, note_slug, note_title, category, cnt, old_text)
        return json.dumps(res, ensure_ascii=False)

    except Exception as e:
        logger.exception("Memory tool execution error: %s", e)
        return tool_error(f"Memory operation error: {str(e)}", success=False)


def _apply_single_memory_op(
    vault: BrainVault,
    action: str,
    note_slug: str,
    note_title: str,
    category: str,
    content: Optional[str],
    old_text: Optional[str],
) -> Dict[str, Any]:
    node = vault.read_node(note_slug)
    body = node.content if node else f"# {note_title}\n\n*Related*: [[self/identity]]\n"

    if action == "add":
        if not content:
            raise ValueError("Content is required for 'add' action")
        clean = content.strip().lstrip("-* ").strip()
        if clean not in body:
            body = f"{body.strip()}\n- {clean}\n"
            vault.write_node(
                node_id=note_slug,
                content=body,
                title=note_title,
                category=category,
                tags=[category, "memory"],
            )
        return {"success": True, "action": "add", "target": note_slug}

    elif action == "replace":
        if not old_text or not content:
            raise ValueError("Both 'old_text' and 'content' are required for 'replace'")
        if old_text not in body:
            raise ValueError(f"Target text '{old_text}' not found in memory")
        body = body.replace(old_text, content.strip(), 1)
        vault.write_node(
            node_id=note_slug,
            content=body,
            title=note_title,
            category=category,
            tags=[category, "memory"],
        )
        return {"success": True, "action": "replace", "target": note_slug}

    elif action == "remove":
        if not old_text:
            raise ValueError("'old_text' is required for 'remove'")
        if old_text not in body:
            raise ValueError(f"Target text '{old_text}' not found in memory")
        lines = [l for l in body.splitlines() if old_text not in l]
        body = "\n".join(lines)
        vault.write_node(
            node_id=note_slug,
            content=body,
            title=note_title,
            category=category,
            tags=[category, "memory"],
        )
        return {"success": True, "action": "remove", "target": note_slug}

    else:
        raise ValueError(f"Unknown memory action '{action}'. Supported: add, replace, remove")


registry.register(
    name="memory",
    toolset="memory",
    schema=MEMORY_SCHEMA,
    handler=lambda args, **kw: memory_tool(
        action=args.get("action"),
        target=args.get("target", "memory"),
        content=args.get("content"),
        old_text=args.get("old_text"),
        new_text=args.get("new_text"),
        operations=args.get("operations"),
        **kw,
    ),
    check_fn=check_memory_requirements,
    emoji="🧠",
)
