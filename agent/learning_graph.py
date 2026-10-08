"""Assemble the "learning made visible" graph for desktop.

Scoped to what a user actually learns over time: non-base, learned/profile
skills (agent-created or used) plus ``MEMORY.md`` / ``USER.md`` chunks as
first-class nodes. Skill links come from declared ``related_skills``;
memory→skill links are derived from lexical overlap.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from pulse_constants import get_pulse_home

_SKIP_PARTS = {".archive", ".hub", ".locks", "node_modules", ".git"}
_USAGE_TS_KEYS = ("last_activity_at", "last_used_at", "last_viewed_at", "last_patched_at", "created_at")


@dataclass
class SkillNode:
    name: str
    category: str
    source: str = "profile"
    timestamp: Optional[int] = None
    use_count: int = 0
    state: str = "active"
    created_by: Optional[str] = None
    pinned: bool = False
    related: list[str] = field(default_factory=list)


def _fm_field(fm: dict[str, Any], key: str) -> Any:
    """Top-level ``key`` or ``metadata.pulse.<key>``; tolerant of the string-valued
    frontmatter that ``parse_frontmatter``'s malformed-YAML fallback produces."""
    if fm.get(key):
        return fm[key]
    meta = fm.get("metadata")
    pulse = meta.get("pulse") if isinstance(meta, dict) else None
    return pulse.get(key) if isinstance(pulse, dict) else None


def _related(fm: dict[str, Any]) -> list[str]:
    raw = _fm_field(fm, "related_skills")
    raw = raw.strip("[]").split(",") if isinstance(raw, str) else raw
    return [str(r).strip() for r in raw if str(r).strip()] if isinstance(raw, list) else []


def _load_usage() -> dict[str, dict[str, Any]]:
    try:
        from tools.skill_usage import load_usage
        return load_usage()
    except Exception:
        try:
            return json.loads((get_pulse_home() / "skills" / ".usage.json").read_text(encoding="utf-8-sig"))
        except Exception:
            return {}


def _to_int_ts(value: Any) -> Optional[int]:
    """Epoch seconds from a number, numeric string, or ISO timestamp; None otherwise."""
    try:
        if value is None or not (s := str(value).strip()):
            return None
        if isinstance(value, (int, float)):
            return int(value)
        try:
            return int(float(s))
        except ValueError:
            parsed = datetime.fromisoformat(s.replace("Z", "+00:00"))
            return int((parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)).timestamp())
    except Exception:
        return None


def build_skill_nodes(skill_roots: list[tuple[str, Path]]) -> dict[str, SkillNode]:
    usage = _load_usage()
    nodes: dict[str, SkillNode] = {}
    # Tag skills mounted from skills.external_dirs so the journey graph can keep them out of
    # learning milestones (#108032): a mount is configured, not learned. Path-based (the common
    # symlink into the profile tree resolves to the external root), so a local copy of the same
    # name still classifies as its own source.
    try:
        from agent.skill_utils import is_external_skill_path
    except Exception:
        is_external_skill_path = None  # type: ignore[assignment]
    for source, root in skill_roots:
        for skill_md in root.rglob("SKILL.md") if root.exists() else ():
            if _SKIP_PARTS.intersection(skill_md.parts):
                continue
            try:
                text = skill_md.read_text(encoding="utf-8-sig")[:4000]
            except OSError:
                continue
            try:
                from agent.skill_utils import parse_frontmatter
                fm = parse_frontmatter(text)[0] or {}
            except Exception:
                fm = {}
            name = str(fm.get("name") or skill_md.parent.name).strip()
            if not name or name in nodes:
                continue
            rec, cat, parts = usage.get(name, {}), _fm_field(fm, "category"), skill_md.parts  # …/skills/<category>/<skill>/SKILL.md
            usage_ts = next((ts for ts in (_to_int_ts(rec.get(k)) for k in _USAGE_TS_KEYS) if ts is not None), None)
            # Local variable: never overwrite `source` — the loop variable must keep its
            # per-root value for the NEXT skill in the same root, or every skill yielded
            # after the first external mount inherits "external" (ext4 hash order can
            # interleave a symlinked mount before a local skill in one root).
            node_source = "external" if (is_external_skill_path is not None and is_external_skill_path(skill_md)) else source
            nodes[name] = SkillNode(
                name=name, category=str(cat) if cat else parts[-3] if len(parts) >= 3 else "general", source=node_source,
                timestamp=usage_ts or _to_int_ts(skill_md.stat().st_mtime),
                use_count=int(rec.get("use_count", 0) or 0), state=str(rec.get("state", "active") or "active"),
                created_by=rec.get("created_by"), pinned=bool(rec.get("pinned", False)), related=_related(fm),
            )
    return nodes


def build_edges(nodes: dict[str, SkillNode]) -> list[tuple[str, str]]:
    """Undirected related_skills edges where BOTH endpoints exist (deduped, first-seen order)."""
    return list(dict.fromkeys(
        (min(node.name, target), max(node.name, target)) for node in nodes.values() for target in node.related if target in nodes and target != node.name
    ))


def density_stats(nodes: dict[str, SkillNode], edges: list[tuple[str, str]]) -> dict[str, Any]:
    linked, cats, n = {x for edge in edges for x in edge}, Counter(x.category for x in nodes.values()), len(nodes) or 1
    return {
        "nodes": len(nodes), "related_edges": len(edges), "edges_per_node": round(len(edges) / n, 3),
        "linked_nodes": len(linked), "isolated_pct": round(100 * (n - len(linked)) / n, 1), "categories": len(cats),
        "agent_created": sum(1 for x in nodes.values() if x.created_by == "agent"),
        "used": sum(1 for x in nodes.values() if x.use_count > 0),
        "top_categories": sorted(cats.items(), key=lambda kv: -kv[1])[:8],
    }


def memory_fingerprint(entry: str) -> str:
    """Short stable digest of a memory entry's TEXT, carried in the node id.

    A journey card is identified by what it says, not by where it sat: an earlier entry can be
    removed (an agent ``memory_tool`` remove mid-turn, a Journey delete without a refetch)
    between the graph being drawn and the user submitting an edit, and a bare index then names
    somebody else's card (#119668).
    Cards and the mutation path both read entries through ``MemoryStore._read_file``, so the
    same entry digests the same on both sides (a BOM'd file included).
    """
    return hashlib.sha256(entry.strip().encode("utf-8")).hexdigest()[:12]


def memory_node_id(card: dict[str, Any], index: int) -> str:
    """``memory:<source>:<index>:<fingerprint>`` — position for the occurrence, text for identity."""
    return f"memory:{card['source']}:{index}:{card['fingerprint']}"


def _memory_cards() -> list[dict[str, Any]]:
    """Vault nodes as memory cards (active nodes from self/, user/, concept/, project/, belief/).
    Falls back to MEMORY.md / USER.md if vault is empty.
    
    §9 fast path: when the SQLite cache is available and fresh, load from there (O(1) query)
    instead of parsing every .md file (O(vault_size)).
    """
    cards: list[dict[str, Any]] = []
    try:
        from agent.brain.vault import BrainVault
        from agent.brain.cache import BrainCache
        from agent.brain.models import NodeStatus

        vault = BrainVault()
        if not vault.vault_dir.exists():
            raise FileNotFoundError("vault missing")
        
        # Try cache first (fast path)
        cache = BrainCache(vault)
        if not cache.is_stale():
            cache_nodes = cache.all_nodes()
            if cache_nodes:
                for chunk_idx, entry in enumerate(cache_nodes):
                    fm = entry.get("frontmatter", {})
                    if fm.get("status") != NodeStatus.ACTIVE.value:
                        continue
                    content = entry.get("content", "").strip()
                    if not content:
                        continue
                    source = "profile" if entry["category"] == "user" else "memory"
                    first = entry["title"].strip().lstrip("# ").strip()
                    cards.append({
                        "source": source,
                        "timestamp": int(fm.get("updated_at") or fm.get("created_at") or 0) + chunk_idx,
                        "title": (first[:80] + "…") if len(first) > 80 else first,
                        "body": content[:1200],
                        "content": content,
                        "fingerprint": memory_fingerprint(content),
                        "node_id": entry["id"],
                        "category": entry["category"],
                        "tags": fm.get("tags") or [],
                        "stability": fm.get("stability", 1.0),
                        "confidence": fm.get("confidence", 0.8),
                    })
                if cards:
                    return cards
        
        # Cache miss or stale — fall back to vault O(N) scan
        nodes = [
            n for n in vault.list_all_nodes()
            if n.frontmatter.status == NodeStatus.ACTIVE.value and (n.content or "").strip()
        ]
        nodes.sort(key=lambda n: (n.timestamp or 0, n.id))
        for chunk_idx, node in enumerate(nodes):
            source = "profile" if node.frontmatter.category == "user" else "memory"
            first = (node.title or (node.content.splitlines()[0] if node.content else "")).strip().lstrip("# ").strip()
            cards.append({
                "source": source,
                "timestamp": int(node.timestamp or 0) + chunk_idx if node.timestamp else None,
                "title": (first[:80] + "…") if len(first) > 80 else first,
                "body": node.content[:1200],
                "content": node.content,
                "fingerprint": memory_fingerprint(node.content),
                "node_id": node.id,
                "category": node.frontmatter.category,
                "tags": node.frontmatter.tags,
                "stability": node.frontmatter.stability,
                "confidence": node.frontmatter.confidence,
            })
        if cards:
            return cards
    except Exception:
        pass

    from tools.memory_tool import MemoryStore

    base = get_pulse_home() / "memories"
    for fname, source in (("MEMORY.md", "memory"), ("USER.md", "profile")):
        path = base / fname
        try:
            file_ts = _to_int_ts(path.stat().st_mtime)
        except OSError:
            continue
        for chunk_idx, chunk in enumerate(MemoryStore._read_file(path)):
            first = chunk.splitlines()[0].strip().lstrip("# ").strip()
            cards.append({
                "source": source, "timestamp": file_ts + chunk_idx if file_ts is not None else None,
                "title": (first[:80] + "…") if len(first) > 80 else first, "body": chunk[:1200],
                "fingerprint": memory_fingerprint(chunk),
            })
    return cards


def _tokenize(text: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", text.lower()) if len(t) >= 3}


def _memory_skill_edges(memory_cards: list[dict[str, Any]], skills: list[SkillNode]) -> list[tuple[str, str]]:
    """Top-4 lexically overlapping skills per memory card (name hit weighs 6)."""
    edges: list[tuple[str, str]] = []
    skill_meta = [(s.name, _tokenize(s.name), s.name.lower()) for s in skills]
    for idx, card in enumerate(memory_cards):
        text = f"{card.get('title', '')}\n{card.get('body', '')}".lower()
        text_tokens = _tokenize(text)
        scored = sorted(
            ((score, name) for name, tokens, name_lower in skill_meta if (score := (6 if name_lower in text else 0) + len(tokens & text_tokens)) > 0),
            key=lambda x: (-x[0], x[1]),
        )
        edges.extend((memory_node_id(card, idx), name) for _, name in scored[:4])
    return edges


def _vault_link_edges(memory_cards: list[dict[str, Any]]) -> tuple[list[tuple[str, str]], list[dict[str, Any]]]:
    """Vault edges as ``(source, target)`` pairs, plus ghost nodes for dangling links.

    §7 of the spec: *edges = resolved ``[[wikilinks]]``; directional arrows; unresolved links
    render as ghost nodes*. This is the part that makes the map the brain instead of a
    lexical guess — a link the user (or consolidation) actually wrote is knowledge; the
    memory↔skill overlap edges are only a hint. Directions are preserved because "A links to
    B" is not the same fact as "B links to A".
    
    §9 fast path: when the cache is fresh, read edges from SQLite instead of rebuilding the index.
    """
    graph_id_by_node = {
        card["node_id"]: memory_node_id(card, index)
        for index, card in enumerate(memory_cards)
        if card.get("node_id")
    }
    if not graph_id_by_node:
        return [], []
    try:
        from agent.brain.index import BrainIndex
        from agent.brain.vault import BrainVault
        from agent.brain.cache import BrainCache

        vault = BrainVault()
        cache = BrainCache(vault)
        
        # Try cache edges (O(1) query)
        if not cache.is_stale():
            cache_edges = cache.all_edges()
            if cache_edges:
                # Build resolved/unresolved from cache data
                edges: list[tuple[str, str]] = []
                unresolved: dict[str, set[str]] = {}
                for source, target, kind in cache_edges:
                    source_graph = graph_id_by_node.get(source)
                    target_graph = graph_id_by_node.get(target)
                    if source_graph and target_graph and source_graph != target_graph:
                        edges.append((source_graph, target_graph))
                    elif source_graph and not target_graph:
                        unresolved.setdefault(target, set()).add(source)
                
                ghosts: dict[str, dict[str, Any]] = {}
                for target, source_ids in unresolved.items():
                    ghost_id = f"ghost:{target}"
                    ghosts.setdefault(ghost_id, {
                        "id": ghost_id, "label": target, "kind": "ghost", "category": "ghost",
                        "useCount": 0, "state": "unresolved", "createdBy": None, "pinned": False,
                        "timestamp": None,
                    })
                    for source_id in source_ids:
                        source_graph = graph_id_by_node.get(source_id)
                        if source_graph:
                            edges.append((source_graph, ghost_id))
                
                return list(dict.fromkeys(edges)), list(ghosts.values())
        
        # Cache miss/stale — fall back to full index rebuild
        index = BrainIndex(vault).rebuild()
    except Exception:
        # A graph that cannot read the vault is still a graph of skills; never raise here.
        return [], []

    edges: list[tuple[str, str]] = []
    for source_id, targets in index.forward.items():
        source = graph_id_by_node.get(source_id)
        if source is None:
            continue
        for target in targets:
            resolved = graph_id_by_node.get(target)
            if resolved is not None and resolved != source:
                edges.append((source, resolved))

    ghosts: dict[str, dict[str, Any]] = {}
    for target, source_ids in index.unresolved.items():
        ghost_id = f"ghost:{target}"
        ghosts.setdefault(ghost_id, {
            "id": ghost_id, "label": target, "kind": "ghost", "category": "ghost",
            "useCount": 0, "state": "unresolved", "createdBy": None, "pinned": False,
            "timestamp": None,
        })
        for source_id in source_ids:
            source = graph_id_by_node.get(source_id)
            if source is not None:
                edges.append((source, ghost_id))
    return list(dict.fromkeys(edges)), list(ghosts.values())


def _has_learning_signal(node: SkillNode) -> bool:
    """Graph-worthy: agent-created, user-taught (/learn), or actually used.

    ``created_by="learn"`` is a learning-signal marker only — curator management stays keyed
    strictly on ``"agent"`` (see ``tools.skill_usage._is_curator_managed_record``). External
    mounts are never a learning milestone: they were configured by the user, not learned, so
    even a used external skill stays out of the journey graph (#108032).
    """
    return node.source != "external" and (node.created_by in {"agent", "learn"} or node.use_count > 0)


def build_learning_graph() -> dict[str, Any]:
    """Full payload for the desktop learning panel: non-base skills with real
    learning signal (agent-created or used) plus memory chunks as graph nodes."""
    roots = [("base", Path(__file__).resolve().parent.parent / "skills"), ("profile", get_pulse_home() / "skills")]
    learned_skills = {
        name: node for name, node in build_skill_nodes(roots).items()
        if node.source != "base" and _has_learning_signal(node)
    }
    skill_edges, memory_cards = build_edges(learned_skills), _memory_cards()
    memory_edges = _memory_skill_edges(memory_cards, list(learned_skills.values()))
    # §7: the vault's own links are the graph's real edges — resolved [[wikilinks]] between
    # notes, plus ghost nodes for links whose target does not exist yet.
    vault_edges, ghost_nodes = _vault_link_edges(memory_cards)
    clusters = Counter(node.category for node in learned_skills.values())
    clusters.update(card.get("category") or "memory" for card in memory_cards)

    graph_nodes = [
        {
            "id": n.name, "label": n.name, "kind": "skill", "timestamp": n.timestamp, "category": n.category,
            "useCount": n.use_count, "state": n.state, "createdBy": n.created_by, "pinned": n.pinned,
        }
        for n in learned_skills.values()
    ] + [
        {
            "id": memory_node_id(card, i), "label": card["title"], "kind": "memory",
            "memorySource": card["source"], "timestamp": card.get("timestamp"),
            # §7: colour and filter by the vault's own category (self/user/concept/project/
            # belief/daily). A legacy flat-file card has none, so it keeps the old "memory".
            "category": card.get("category") or "memory",
            "confidence": card.get("confidence"),
            "tags": card.get("tags") or [],
            "vaultId": card.get("node_id"),
            "useCount": 0, "state": "active", "createdBy": "memory", "pinned": False,
        }
        for i, card in enumerate(memory_cards)
    ] + ghost_nodes
    return {
        "nodes": graph_nodes,
        "edges": [
            {"source": a, "target": b, "kind": kind}
            for kind, pairs in (("related", skill_edges), ("memory-skill", memory_edges), ("wikilink", vault_edges))
            for a, b in pairs
        ],
        "clusters": [{"category": c, "count": n} for c, n in sorted(clusters.items(), key=lambda kv: -kv[1])],
        "memory": memory_cards,
        "stats": {
            **density_stats(learned_skills, skill_edges),
            "memory_nodes": len(memory_cards), "memory_skill_edges": len(memory_edges), "learned_skills": len(learned_skills),
            "vault_edges": len(vault_edges), "ghost_nodes": len(ghost_nodes),
        },
    }
