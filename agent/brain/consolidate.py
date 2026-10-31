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


def _match_theme(text_a: str, text_b: str, embedder: Any = None) -> bool:
    """True when two episodic statements cluster under the same entity or topic."""
    if is_near_duplicate(text_a, text_b, embedder=embedder, threshold=0.75):
        return True

    sim = similarity(text_a, text_b, embedder=embedder)
    if sim >= 0.60:
        return True

    # Entity-based clustering: check if both statements share non-generic entities
    try:
        from .entities import extract_entity_candidates, is_generic_entity
        e_a = {e.lower() for e in extract_entity_candidates(text_a) if not is_generic_entity(e)}
        e_b = {e.lower() for e in extract_entity_candidates(text_b) if not is_generic_entity(e)}
        if e_a and e_b and (e_a & e_b):
            tokens_a = set(tokenize(text_a))
            tokens_b = set(tokenize(text_b))
            if len(tokens_a & tokens_b) >= 2 or sim >= 0.20:
                return True
    except Exception:
        pass

    # Keyword / topic overlap clustering for technical statements
    tokens_a = set(tokenize(text_a))
    tokens_b = set(tokenize(text_b))
    stopwords = {"a", "an", "the", "and", "or", "is", "are", "was", "were", "to", "in", "on", "for", "with", "by", "at", "from", "of", "it", "its"}
    content_overlap = (tokens_a & tokens_b) - stopwords
    if len(content_overlap) >= 3 or (len(content_overlap) >= 2 and sim >= 0.35):
        return True

    return False


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

    # Cluster statements by entity and topic similarity
    clusters: List[List[Dict[str, Any]]] = []
    for item in raw_statements:
        matched_cluster = None
        for cluster in clusters:
            rep = cluster[0]["text"]
            if _match_theme(item["text"], rep, embedder=embedder):
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
                    # A near-duplicate is not merely a redundant copy: it is a *second independent
                    # assertion* of the same claim. Recording it as corroboration on the primary is
                    # what lets reflection hold a belief (≥2 independent pieces of evidence) instead
                    # of a provisional guess. Before this, the merge discarded the recurrence signal
                    # along with the duplicate node, so a corroborated claim could never become a
                    # belief — the reason a real vault produced zero beliefs.
                    try:
                        vault.record_corroboration(
                            primary.id, text=(weaker.content or "")[:400], now=now,
                        )
                    except Exception:
                        pass
                    vault.supersede(weaker.id, primary.id, now=now)

    return merged


def relink_mentions(
    vault: Any,
    *,
    dry_run: bool = False,
    now: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Scan active semantic notes and create 'related' edges from entity mentions.
    
    Uses entity extraction (capitalized phrases, technical terms, proper nouns) instead of
    full-title matching, so "PULSE has a cognitive Brain" → entities ["PULSE", "Brain"] →
    links to nodes about those entities.
    """
    if vault is None:
        return []

    active_nodes = [
        node for node in vault.list_all_nodes()
        if node.frontmatter.status == NodeStatus.ACTIVE.value
        and node.frontmatter.category != NodeCategory.DAILY.value
    ]
    if len(active_nodes) < 2:
        return []

    # Build entity index from all active nodes
    from .entities import build_entity_index, find_entity_mentions
    from .relations import Relation, RelationType, Provenance, parse_relations, merge_into, dump_relations
    entity_index = build_entity_index(active_nodes)

    relinked: List[Dict[str, Any]] = []

    for node in active_nodes:
        existing_related = set(node.frontmatter.related or [])
        content = node.content or ""
        
        # Find entities mentioned in this node's content
        mentioned = find_entity_mentions(content, entity_index)
        new_related_to_add: List[str] = []
        # (target_id -> the entity string that justified the link) is the edge's evidence.
        evidence_by_target: Dict[str, str] = {}

        for target_id, matched_entity in mentioned:
            if target_id == node.id:
                continue
            evidence_by_target.setdefault(target_id, str(matched_entity))
            if target_id in existing_related:
                continue
            new_related_to_add.append(target_id)

        # Build/update the TYPED relations for this node: each entity-mention edge is an
        # INFERRED_ENTITY relation whose confidence is corroborated by the matched entity string.
        # Re-running consolidation over the same text contributes the same evidence hash, so
        # confidence does NOT inflate on repeat (idempotent by construction).
        typed = parse_relations(node.frontmatter.relations or [])
        changed_relations = False
        for target_id, entity_text in evidence_by_target.items():
            if target_id == node.id:
                continue
            before = next((r for r in typed if r.target == target_id
                           and r.rel_type == RelationType.MENTIONS.value), None)
            before_conf = before.confidence if before else None
            before_ev = len(before.evidence) if before else -1
            merge_into(
                typed,
                Relation(target=target_id, rel_type=RelationType.MENTIONS.value,
                         provenance=Provenance.INFERRED_ENTITY.value),
                evidence_text=f"{node.id}|{entity_text.lower()}",
                now=now,
            )
            after = next((r for r in typed if r.target == target_id
                          and r.rel_type == RelationType.MENTIONS.value), None)
            if after and (after.confidence != before_conf or len(after.evidence) != before_ev):
                changed_relations = True

        if new_related_to_add or changed_relations:
            combined_related = sorted(set(existing_related) | set(new_related_to_add))
            relinked.append({
                "node_id": node.id,
                "added_related": new_related_to_add,
            })
            if not dry_run:
                # Append related links block to the node body if not present
                updated_body = content
                if new_related_to_add:
                    link_refs = " ".join(f"[[{lid}]]" for lid in new_related_to_add)
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
                    relations=dump_relations(typed),
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
            # Build content with bidirectional [[wikilinks]] to source daily nodes
            source_links = " ".join(f"[[{d}]]" for d in cand.source_daily_nodes)
            content_with_links = cand.text
            if source_links:
                if "## Related" in content_with_links:
                    content_with_links = f"{content_with_links}\n- {source_links}"
                else:
                    content_with_links = f"{content_with_links}\n\n## Related\n- {source_links}"

            # Create the promoted semantic note
            vault.write_node(
                node_id,
                content_with_links,
                title=cand.title,
                category=cand.category,
                tags=["consolidated", "semantic"],
                salience=0.85,
                confidence=0.9,
                status=NodeStatus.ACTIVE.value,
                related=list(cand.source_daily_nodes),
                extra={
                    "source_turns": cand.source_turns,
                    "source_daily_nodes": cand.source_daily_nodes,
                },
                now=ts,
            )

            # Bidirectional linking: update each source daily note to link back to the promoted concept
            for dnode_id in cand.source_daily_nodes:
                try:
                    dnode = vault.read_node(dnode_id)
                    if dnode is not None:
                        d_content = dnode.content or ""
                        link_ref = f"[[{node_id}]]"
                        if link_ref not in d_content:
                            if "## Related" in d_content:
                                updated_dcontent = f"{d_content}\n- {link_ref}"
                            else:
                                updated_dcontent = f"{d_content}\n\n## Related\n- {link_ref}"
                            updated_related = sorted(set(dnode.frontmatter.related or []) | {node_id})
                            vault.write_node(
                                dnode.id,
                                updated_dcontent,
                                title=dnode.title,
                                category=dnode.frontmatter.category,
                                tags=dnode.frontmatter.tags,
                                salience=dnode.frontmatter.salience,
                                confidence=dnode.frontmatter.confidence,
                                status=dnode.frontmatter.status,
                                related=updated_related,
                                extra=dnode.frontmatter.extra,
                                now=ts,
                            )
                except Exception:
                    logger.debug(f"Failed to update daily node {dnode_id} with link to {node_id}", exc_info=True)

    # 2. Merge near-duplicates
    merges = merge_near_duplicates(
        vault, threshold=merge_threshold, embedder=embedder, dry_run=dry_run, now=ts
    )
    report["merged"] = merges

    # 3. Re-link mentions
    links = relink_mentions(vault, dry_run=dry_run, now=ts)
    report["relinked"] = links

    return report
