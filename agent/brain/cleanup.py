"""Brain vault semantic cleanup — remove junk nodes created before encoding v3 fix.

Criteria (all must be true to mark junk):
  1. Short content (≤30 words)
  2. Salience below 0.52 (spec prose scored ~0.48-0.50, outcomes ≥0.42)
  3. access_count = 0 (never recalled → not valuable)
  4. Matches junk patterns (structural fragments, incomplete sentences, spec artifacts)

Action: mark status=archived (preserve history, exclude from active recall).
Idempotent: can run repeatedly without side effects.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .models import NodeStatus
from .vault import BrainVault

logger = logging.getLogger(__name__)

# Junk patterns: structural fragments, incomplete phrases, spec prose markers
_JUNK_PATTERNS = [
    re.compile(r"^[│├└─•\-*>#]{1,4}\s"),  # tree diagram / bullet prefix
    re.compile(r"(correction|supersession)$", re.I),  # isolated spec headings
    re.compile(r"^(never|always|do not|must|should)\s", re.I),  # imperative rule fragments
    re.compile(r"(or what|of what|of the|because of|rather than|merely because)$", re.I),  # incomplete tail
    re.compile(r"^(pretend you remember|fix the skill|encode the correction)$", re.I),  # spec instruction fragments
    re.compile(r"^[\[\]\(\)]+$"),  # only brackets
    re.compile(r"^[0-9\.\-]+$"),  # only numbers/punctuation
]

_MEANINGLESS_TITLES = {
    "pretend you remember", "correction supersession", "never create a node merely because",
    "never silently pretend they agree", "never convert uncertainty into confidence",
    "never update documentation to claim future functionality",
    "but do not preserve fundamentally wrong architecture",
    "but visual polish must never substitute for",
}


def is_junk_node(node_id: str, content: str, frontmatter: Any) -> tuple[bool, str]:
    """Semantic junk detector. Returns (is_junk, reason)."""
    
    # Already archived/superseded → skip (don't re-archive superseded nodes)
    if frontmatter.status in (NodeStatus.ARCHIVED.value, NodeStatus.SUPERSEDED.value):
        return False, "already non-active"
    
    # Self/user category → never junk (core identity/preferences)
    if frontmatter.category in ("self", "user"):
        return False, "core category"
    
    # High access count → valuable regardless of content
    if frontmatter.access_count >= 3:
        return False, f"access_count={frontmatter.access_count}"
    
    # High salience → likely outcome/decision (keep)
    if frontmatter.salience >= 0.52:
        return False, f"salience={frontmatter.salience}"
    
    text = content.strip()
    words = len(text.split())
    
    # Short + low-salience + never accessed → suspect
    if words > 30 or frontmatter.access_count > 0:
        return False, "content length or usage"
    
    # Title is a known junk phrase
    title_norm = frontmatter.title.strip().lower()
    if title_norm in _MEANINGLESS_TITLES:
        return True, f"meaningless title: {title_norm[:40]}"
    
    # Content matches structural junk patterns
    for pattern in _JUNK_PATTERNS:
        if pattern.search(text):
            return True, f"junk pattern: {pattern.pattern[:40]}"
    
    # Title ends with incomplete phrase marker
    if re.search(r"(or what|of what|of the|because of|merely because|rather than|what the)$", title_norm):
        return True, f"incomplete phrase: {title_norm[-30:]}"
    
    # Node ID suggests it's a spec heading fragment
    id_tail = node_id.split("/")[-1]
    if re.match(r"^(correction|supersession|never-|always-|do-not-|must-|should-)", id_tail):
        return True, f"spec directive id: {id_tail[:40]}"
    
    return False, "passes all checks"


def cleanup_junk_nodes(
    vault: Optional[BrainVault] = None,
    *,
    dry_run: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Archive semantic junk nodes created before encoder v3 fix.
    
    Returns:
        {
            "inspected": int,
            "archived": int,
            "preserved": int,
            "junk_nodes": [{"id", "reason", "title", "words"}],
            "dry_run": bool,
        }
    """
    vault = vault or BrainVault()
    nodes = vault.list_all_nodes()
    
    junk_candidates: List[Dict[str, Any]] = []
    preserved = 0
    
    for node in nodes:
        is_junk, reason = is_junk_node(node.id, node.content, node.frontmatter)
        if is_junk:
            junk_candidates.append({
                "id": node.id,
                "reason": reason,
                "title": node.title,
                "words": len(node.content.split()),
                "salience": node.frontmatter.salience,
                "access_count": node.frontmatter.access_count,
            })
        else:
            preserved += 1
    
    archived = 0
    if not dry_run:
        for entry in junk_candidates:
            node = vault.read_node(entry["id"])
            if node is None:
                continue
            vault.write_node(
                entry["id"],
                node.content,
                title=node.title,
                category=node.category,
                tags=node.tags,
                status=NodeStatus.ARCHIVED.value,
                salience=node.frontmatter.salience,
                confidence=node.frontmatter.confidence,
                supersedes=node.frontmatter.supersedes,
                aliases=node.frontmatter.aliases,
                related=node.frontmatter.related,
                source_turn=node.frontmatter.source_turn,
                extra=node.frontmatter.extra,
                now=now,
            )
            archived += 1
            logger.info("archived junk node: %s (%s)", entry["id"], entry["reason"])
    
    return {
        "inspected": len(nodes),
        "archived": archived if not dry_run else 0,
        "preserved": preserved,
        "junk_nodes": junk_candidates,
        "dry_run": dry_run,
    }


def cleanup_duplicates(
    vault: Optional[BrainVault] = None,
    *,
    dry_run: bool = False,
    similarity_threshold: float = 0.90,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Detect near-duplicate nodes (>90% text overlap) and supersede all but the most-used.
    
    Groups by base ID (strips -2, -3, -4 suffix). Within each group, the node with highest
    access_count survives; the rest are marked superseded.
    """
    vault = vault or BrainVault()
    nodes = vault.list_all_nodes()
    
    # Group by base ID
    groups: Dict[str, List[Any]] = {}
    for node in nodes:
        if node.frontmatter.status == NodeStatus.SUPERSEDED.value:
            continue  # already superseded
        base = re.sub(r"-[234]$", "", node.id)
        groups.setdefault(base, []).append(node)
    
    superseded_list: List[Dict[str, Any]] = []
    
    for base, group in groups.items():
        if len(group) <= 1:
            continue
        
        # Check content similarity within group (simple word-set Jaccard)
        texts = [set(n.content.lower().split()) for n in group]
        if len(texts) < 2:
            continue
        
        # All pairs must be >90% similar to be considered duplicates
        is_dup_group = True
        for i in range(len(texts)):
            for j in range(i + 1, len(texts)):
                overlap = len(texts[i] & texts[j])
                union = len(texts[i] | texts[j])
                if union == 0 or overlap / union < similarity_threshold:
                    is_dup_group = False
                    break
            if not is_dup_group:
                break
        
        if not is_dup_group:
            continue
        
        # Sort by (access_count desc, created_at asc) — most-used or oldest survives
        group_sorted = sorted(
            group,
            key=lambda n: (-n.frontmatter.access_count, n.frontmatter.created_at or 9999999999),
        )
        keeper = group_sorted[0]
        dupes = group_sorted[1:]
        
        for dupe in dupes:
            superseded_list.append({
                "id": dupe.id,
                "superseded_by": keeper.id,
                "title": dupe.title,
            })
            if not dry_run:
                vault.supersede(dupe.id, keeper.id, now=now)
                logger.info("superseded duplicate: %s → %s", dupe.id, keeper.id)
    
    return {
        "groups_inspected": len([g for g in groups.values() if len(g) > 1]),
        "superseded": len(superseded_list) if not dry_run else 0,
        "superseded_nodes": superseded_list,
        "dry_run": dry_run,
    }
