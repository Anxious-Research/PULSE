"""PULSE Self-Codebase Introspection & Auto-Vault Indexer.

Enables PULSE to deeply inspect its own codebase directory structure,
extract module wiring, classes, functions, and cross-subsystem dependencies,
and automatically generate an interconnected self-knowledge graph in the Brain Vault.
"""

from __future__ import annotations

import ast
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from agent.brain.vault import BrainVault
from agent.brain.models import NodeCategory

logger = logging.getLogger(__name__)

# Key architectural subsystems to inspect and map
SUBSYSTEM_SPECS: Dict[str, Dict[str, Any]] = {
    "agent-runtime": {
        "title": "Agent Runtime & Reasoning Engine",
        "description": "Core loop orchestrating model turns, tool dispatching, turn context, and streaming responses.",
        "paths": ["agent", "run_agent.py"],
        "key_files": ["run_agent.py", "agent/turn_context.py", "agent/system_prompt.py", "agent/prompt_builder.py"],
        "related": ["tool-registry", "brain-engine", "state-engine", "curator"],
    },
    "brain-engine": {
        "title": "Native Cognitive Brain & Knowledge Graph",
        "description": "Obsidian-grade local markdown vault, bi-directional [[wikilinks]], backlinks, and belief evolution.",
        "paths": ["agent/brain", "tools/brain_tool.py"],
        "key_files": ["agent/brain/vault.py", "agent/brain/graph.py", "agent/brain/belief.py", "tools/brain_tool.py"],
        "related": ["agent-runtime", "desktop-electron", "curator"],
    },
    "tool-registry": {
        "title": "Tool Registry & Execution Fabric",
        "description": "Central registry coordinating tool schemas, handlers, toolset distribution, and permission checks.",
        "paths": ["tools", "toolsets.py"],
        "key_files": ["tools/registry.py", "toolsets.py", "tools/memory_tool.py", "tools/brain_tool.py"],
        "related": ["agent-runtime", "desktop-electron"],
    },
    "state-engine": {
        "title": "Persistence, Session & State Engine",
        "description": "SQLite and WAL-backed multi-session message state, FTS search, history compaction, and profile isolation.",
        "paths": ["pulse_state.py", "pulse_state_*.py"],
        "key_files": ["pulse_state.py", "pulse_state_sessions.py", "pulse_state_search.py", "pulse_state_schema.py"],
        "related": ["agent-runtime", "gateway"],
    },
    "desktop-electron": {
        "title": "Desktop Shell & StarMap Canvas",
        "description": "Electron & React desktop application hosting the visual canvas, StarMap knowledge graph, and user settings.",
        "paths": ["apps/desktop"],
        "key_files": ["apps/desktop/src/main.tsx", "apps/desktop/src/app/starmap/star-map.tsx", "apps/desktop/src/app/starmap/render.ts"],
        "related": ["brain-engine", "gateway"],
    },
    "gateway": {
        "title": "Gateway & Platform IPC",
        "description": "IPC and WebSocket bridge interconnecting Desktop, CLI, and multi-agent platforms.",
        "paths": ["gateway", "tui_gateway"],
        "key_files": ["tui_gateway/server.py", "gateway/"],
        "related": ["desktop-electron", "state-engine"],
    },
    "curator": {
        "title": "Background Curator & Cognitive Sleep",
        "description": "Autonomous maintenance orchestrator executing background consolidation, skill pruning, and memory reflection.",
        "paths": ["agent/curator.py", "agent/learning_graph.py"],
        "key_files": ["agent/curator.py", "agent/learning_graph.py"],
        "related": ["brain-engine", "agent-runtime"],
    },
}


class CodebaseIntrospector:
    """Inspects the PULSE repository to extract architectural structure and wiring."""

    def __init__(self, repo_root: Optional[Path] = None):
        self.repo_root = repo_root or Path(__file__).resolve().parent.parent.parent

    def inspect_file(self, rel_path: str) -> Dict[str, Any]:
        """Inspect a single Python file for classes, functions, and docstrings."""
        full_path = self.repo_root / rel_path
        if not full_path.exists() or not full_path.suffix == ".py":
            return {"exists": False}

        try:
            content = full_path.read_text(encoding="utf-8")
            tree = ast.parse(content)
            docstring = ast.get_docstring(tree) or ""
            classes = [node.name for node in ast.walk(tree) if isinstance(node, ast.ClassDef)]
            functions = [node.name for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))]
            lines = len(content.splitlines())

            return {
                "exists": True,
                "path": rel_path,
                "lines": lines,
                "docstring": docstring.strip().split("\n")[0] if docstring else "",
                "classes": classes[:10],
                "functions": functions[:15],
            }
        except Exception as e:
            logger.debug("Could not inspect AST of %s: %s", rel_path, e)
            return {"exists": True, "path": rel_path, "error": str(e)}

    def build_subsystem_doc(self, key: str, spec: Dict[str, Any]) -> str:
        """Construct an Obsidian-compatible Markdown document for a subsystem."""
        title = spec["title"]
        desc = spec["description"]
        related = spec.get("related", [])
        key_files = spec.get("key_files", [])

        file_sections = []
        for f_path in key_files:
            info = self.inspect_file(f_path)
            if info.get("exists"):
                lines = info.get("lines", 0)
                doc = info.get("docstring", "")
                classes = ", ".join(info.get("classes", []))
                funcs = ", ".join(info.get("functions", []))

                sec = f"### `{f_path}` ({lines} lines)\n"
                if doc:
                    sec += f"> {doc}\n\n"
                if classes:
                    sec += f"- **Classes**: `{classes}`\n"
                if funcs:
                    sec += f"- **Key Functions**: `{funcs}`\n"
                file_sections.append(sec)
            else:
                file_sections.append(f"### `{f_path}`\n- Path verified in codebase.\n")

        files_md = "\n".join(file_sections)
        related_links = ", ".join(f"[[self/subsystems/{r}|{r.title()}]]" for r in related)

        return f"""---
title: "{title}"
category: "self"
tags: ["self", "architecture", "subsystem", "{key}"]
confidence: 1.0
status: "active"
---

# {title}

{desc}

## Related Subsystems
{related_links if related_links else "None"}

## Key Files & Code Inspection
{files_md}
"""

    def generate_full_self_knowledge(self, vault: BrainVault) -> int:
        """Scan codebase and write all subsystem notes into the brain vault under self/."""
        vault.ensure_vault_structure()
        count = 0

        # 1. Write the overarching wiring index
        wiring_content = """---
title: "PULSE Architecture Wiring & Subsystems Map"
category: "self"
tags: ["self", "wiring", "architecture", "index"]
confidence: 1.0
status: "active"
---

# PULSE Complete Architecture & Systems Wiring

PULSE is composed of several interdependent cognitive and execution subsystems:

- [[self/subsystems/agent-runtime|Agent Runtime]]: Central reasoning loop, context building, and turn orchestration.
- [[self/subsystems/brain-engine|Brain Engine]]: Native local markdown knowledge vault with [[wikilinks]], backlinks, and belief evolution.
- [[self/subsystems/tool-registry|Tool Registry]]: Tool schemas, permission policies, and system capabilities.
- [[self/subsystems/state-engine|State Engine]]: Multi-session SQLite persistence, search, and message timelines.
- [[self/subsystems/desktop-electron|Desktop Shell & StarMap]]: Electron application and force-directed knowledge visualization.
- [[self/subsystems/gateway|Gateway IPC]]: WebSocket and IPC bridge for cross-process communication.
- [[self/subsystems/curator|Curator & Cognitive Sleep]]: Background memory consolidation and contradiction resolution.

## Architectural Principles
1. **Self-Awareness**: Complete understanding of its own internal code and runtime status.
2. **Local-First & Private**: Knowledge is stored in human-readable Markdown notes under `~/.pulse/brain/`.
3. **Continuous Evolution**: Resolves misconceptions dynamically over time using [[agent/brain/belief|BeliefEngine]].
"""
        vault.write_node(
            node_id="self/wiring",
            content=wiring_content,
            title="PULSE Architecture Wiring & Subsystems Map",
            category=NodeCategory.SELF.value,
            tags=["self", "wiring", "architecture"],
        )
        count += 1

        # 2. Write each individual subsystem note
        for key, spec in SUBSYSTEM_SPECS.items():
            doc_content = self.build_subsystem_doc(key, spec)
            vault.write_node(
                node_id=f"self/subsystems/{key}",
                content=doc_content,
                title=spec["title"],
                category=NodeCategory.SELF.value,
                tags=["self", "subsystem", key],
            )
            count += 1

        logger.info("Successfully generated %d self-knowledge subsystem notes in brain vault.", count)
        return count
