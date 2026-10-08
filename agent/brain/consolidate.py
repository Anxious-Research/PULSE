"""Consolidation — episodic to semantic memory promotion and vault hygiene (specs/brain.md §3.2).

A background pass (after a turn, debounced; never in the live response path) does what sleep does:

- **Promote**: cluster recent episodic daily entries by shared entities/topics. When a fact
  recurs across turns or carries lasting significance, promote it into a semantic node
  (``concept/<slug>.md``, ``user/``, ``project/``).
- **Merge**: detect near-duplicate notes (lexical + embedding similarity) and merge or supersede
  the weaker one, preventing the vault from accumulating duplicate knowledge.
- **Re-link**: when a node mentions an entity that has an existing note in the vault, ensure
  bi-directional ``[[wikilinks]]`` are present.

This is why the vault grows *logically* over time instead of as an append-only dump.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Set, Tuple

from .encoding import category_for, title_for
from .models import BrainNode, NodeCategory, NodeStatus
from .parser import extract_wikilinks, slugify
from .similarity import is_near_duplicate, similarity, tokenize

logger = logging.getLogger(__name__)

#: Minimum occurrences across turns/days before an episodic fact is promoted to semantic.
DEFAULT_PROMOTION_MIN_RECURRENCE = 2

#: Similarity threshold for near-duplicate merging in consolidation.
DEFAULT_MERGE_SIMILARITY = 0.86

#: Line regex matching daily episodic entries: "- HH:MM <text> (turn <turn_id>)" or "- <text>"
_DAILY_ENTRY_RE = re.compile(
    r"^(?:-\s+)?(?:(?:\d{1,2}:\d{2})\s+)?(.+?)(?:\s+\(turn\s+([^)]+)\))?$",
    re.IGNORECASE,
)


@dataclass
class PromotionCandidate:
    """An episodic fact identified for promotion to semantic memory."""

    text: str
    category: str
    title: str
    occurrences: int
    source_turns: List[str] = field(default_factory=list)
    source_daily_nodes: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "text": self.text,
            "category": self.category,
            "title": self.title,
            "occurrences": self.occurrences,
            "source_turns": list(self.source_turns),
            "source_daily_nodes": list(self.source_daily_nodes),
        }


def parse_daily_entries(content: str) -> List[Tuple[str, Optional[str]]]:
    """Extract ``(statement_text, turn_id)`` pairs from a daily note body."""
    entries: List[Tuple[str, Optional[str]]] = []
    for raw_line in (content or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        m = _DAILY_ENTRY_RE.match(line)
        if m:
            text = m.group(1).strip()
            turn = m.group(2)
            if text:
                entries.append((text, turn))
    return entries


def find_promotion_candidates(
    vault: Any,
    *,
    min_recurrence: int = DEFAULT_PROMOTION_MIN_RECURRENCE,
    embedder: Any = None,
) -> List[PromotionCandidate]:
    """Scan daily notes and find statements that recur often enough to be promoted."""
    if vault is None:
        return []

    daily_nodes = [
        node for node in vault.list_all_nodes()
        if node.frontmatter.category == NodeCategory.DAILY.value and node.content
    ]
    if not daily_nodes:
        return []

    existing_semantic = [
        node for node in vault.list_all_nodes()
        if node.frontmatter.category != NodeCategory.DAILY.value and node.frontmatter.status == NodeStatus.ACTIVE.value
    ]

    # Collect all episodic statements with provenance
    raw_statements: List[Dict[str, Any]] = []
    for dnode in daily_nodes:
        for stmt, turn in parse_daily_entries(dnode.content):
            raw_statements.append({
                "text": stmt,
                "turn": turn,
                "daily_node": dnode.id,
            })

    if not raw_statements:
        return []

    # Cluster statements by text similarity
    clusters: List[List[Dict[str, Any]]] = []
    for item in raw_statements:
        matched_cluster = None
        for cluster in clusters:
            rep = cluster[0]["text"]
            if is_near_duplicate(item["text"], rep, embedder=embedder, threshold=0.75):
                matched_cluster = cluster
                break
        if matched_cluster is not None:
            matched_cluster.append(item)
        else:
            clusters.append([item])

    candidates: List[PromotionCandidate] = []
    for cluster in clusters:
        if len(cluster) < min_recurrence:
            continue

        # Choose the most descriptive (longest) text as the canonical statement
        cluster_sorted = sorted(cluster, key=lambda x: len(x["text"]), reverse=True)
        canonical_text = cluster_sorted[0]["text"]

        # Check if already covered by an existing active semantic node
        already_covered = False
        for sem in existing_semantic:
            if is_near_duplicate(canonical_text, sem.content or "", embedder=embedder, threshold=0.85):
                already_covered = True
                break
        if already_covered:
            continue

        turns = sorted({item["turn"] for item in cluster if item["turn"]})
        daily_ids = sorted({item["daily_node"] for item in cluster})

        candidates.append(
            PromotionCandidate(
                text=canonical_text,
                category=category_for(canonical_text),
                title=title_for(canonical_text),
                occurrences=len(cluster),
                source_turns=turns,
                source_daily_nodes=daily_ids,
            )
        )

    return candidates


def merge_near_duplicates(
    vault: Any,
    *,
    threshold: float = DEFAULT_MERGE_SIMILARITY,
    embedder: Any = None,
    dry_run: bool = False,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Find near-duplicate active semantic nodes and merge them by superseding."""
    if vault is None:
        return []

    active_nodes = [
        node for node in vault.list_all_nodes()
        if node.frontmatter.status == NodeStatus.ACTIVE.value
        and node.frontmatter.category != NodeCategory.DAILY.value
        and (node.content or "").strip()
    ]

    merged: List[Dict[str, Any]] = []
    processed: Set[str] = set()

    for i in range(len(active_nodes)):
        node_a = active_nodes[i]
        if node_a.id in processed:
            continue

        for j in range(i + 1, len(active_nodes)):
            node_b = active_nodes[j]
            if node_b.id in processed:
                continue

            # Near-duplicate check
            sim = similarity(node_a.content, node_b.content, embedder=embedder)
            if sim >= threshold:
                # Pick stronger note: higher access_count/stability, or longer content
                score_a = (node_a.frontmatter.access_count * 2.0) + len(node_a.content)
                score_b = (node_b.frontmatter.access_count * 2.0) + len(node_b.content)

                primary, weaker = (node_a, node_b) if score_a >= score_b else (node_b, node_a)

                record = {
                    "primary_id": primary.id,
                    "superseded_id": weaker.id,
                    "similarity": round(sim, 4),
                }
                merged.append(record)
                processed.add(weaker.id)

                if not dry_run:
                    vault.supersede(weaker.id, primary.id, now=now)

    return merged


def relink_mentions(
    vault: Any,
    *,
    dry_run: bool = False,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Scan active semantic notes and ensure references to known entities are [[wikilinked]]."""
    if vault is None:
        return []

    active_nodes = [
        node for node in vault.list_all_nodes()
        if node.frontmatter.status == NodeStatus.ACTIVE.value
        and node.frontmatter.category != NodeCategory.DAILY.value
    ]
    if len(active_nodes) < 2:
        return []

    # Map entity terms -> target node_id
    targets: List[Tuple[str, re.Pattern, str]] = []
    for node in active_nodes:
        # Title as entity
        t = (node.title or "").strip()
        if len(t) >= 4 and not re.match(r"^\d+$", t):
            pat = re.compile(r"\b" + re.escape(t) + r"\b", re.IGNORECASE)
            targets.append((node.id, pat, t))

    relinked: List[Dict[str, Any]] = []

    for node in active_nodes:
        existing_related = set(node.frontmatter.related or [])
        content = node.content or ""
        new_related_to_add: List[str] = []

        for target_id, pat, term in targets:
            if target_id == node.id or target_id in existing_related:
                continue

            # Does the content mention this entity?
            if pat.search(content):
                new_related_to_add.append(target_id)

        if new_related_to_add:
            combined_related = sorted(set(existing_related) | set(new_related_to_add))
            relinked.append({
                "node_id": node.id,
                "added_related": new_related_to_add,
            })
            if not dry_run:
                # Append related links block to the node body if not present
                link_refs = " ".join(f"[[{lid}]]" for lid in new_related_to_add)
                updated_body = content
                if "## Related" in updated_body:
                    updated_body = updated_body + f"\n- {link_refs}"
                else:
                    updated_body = updated_body + f"\n\n## Related\n- {link_refs}"

                vault.write_node(
                    node.id,
                    updated_body,
                    title=node.title,
                    category=node.frontmatter.category,
                    tags=node.frontmatter.tags,
                    status=node.frontmatter.status,
                    salience=node.frontmatter.salience,
                    confidence=node.frontmatter.confidence,
                    related=combined_related,
                    now=now,
                )

    return relinked


def consolidate_vault(
    vault: Any,
    *,
    min_recurrence: int = DEFAULT_PROMOTION_MIN_RECURRENCE,
    merge_threshold: float = DEFAULT_MERGE_SIMILARITY,
    embedder: Any = None,
    dry_run: bool = False,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """Execute a complete consolidation pass over the vault.

    Returns a structured report of promotions, merges, and link updates.
    """
    ts = now if now is not None else time.time()
    report: Dict[str, Any] = {
        "promoted": [],
        "merged": [],
        "relinked": [],
        "dry_run": bool(dry_run),
    }

    if vault is None:
        return report

    # 1. Promote recurring episodic facts
    candidates = find_promotion_candidates(
        vault, min_recurrence=min_recurrence, embedder=embedder
    )
    for cand in candidates:
        node_id = vault.unique_node_id(cand.category, slugify(cand.title))
        rec = {"node_id": node_id, **cand.as_dict()}
        report["promoted"].append(rec)
        if not dry_run:
            # Create the promoted semantic note
            vault.write_node(
                node_id,
                cand.text,
                title=cand.title,
                category=cand.category,
                tags=["consolidated", "semantic"],
                salience=0.85,
                confidence=0.9,
                status=NodeStatus.ACTIVE.value,
                extra={
                    "source_turns": cand.source_turns,
                    "source_daily_nodes": cand.source_daily_nodes,
                },
                now=ts,
            )

    # 2. Merge near-duplicates
    merges = merge_near_duplicates(
        vault, threshold=merge_threshold, embedder=embedder, dry_run=dry_run, now=ts
    )
    report["merged"] = merges

    # 3. Re-link mentions
    links = relink_mentions(vault, dry_run=dry_run, now=ts)
    report["relinked"] = links

    return report
