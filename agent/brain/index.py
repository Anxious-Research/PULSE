"""Link index over the vault: forward links, backlinks, unresolved targets, tags.

Derived, not authoritative. Everything here is rebuilt from the Markdown files on demand,
so losing the index can never lose memory (specs/brain.md §4).

Also exposes the graph primitive that recall is built on: ``activate()``, a bounded
spreading-activation walk from seed nodes — the mechanism behind cue-based associative
recall (specs/brain.md §3.4).
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from .parser import normalize_node_id
from .vault import BrainVault


class BrainIndex:
    """In-memory view of the vault's link structure."""

    def __init__(self, vault: Optional[BrainVault] = None) -> None:
        self.vault = vault or BrainVault()
        self.forward: Dict[str, List[str]] = {}
        self.backlinks: Dict[str, List[str]] = {}
        self.unresolved: Dict[str, List[str]] = {}
        self.aliases: Dict[str, str] = {}
        self.tags: Dict[str, List[str]] = {}
        self.titles: Dict[str, str] = {}
        self._ids: Set[str] = set()
        self._built = False
        self._node_by_id: Dict[str, Any] = {}
        # Precomputed lookup maps so link resolution is O(1) per link instead of a scan of
        # every id (that scan made ``rebuild()`` O(N^2) — ~7s on a 3000-node vault, paid on
        # every turn whose vault mtime changed). Built in ``_build_resolution_maps``.
        self._lower_to_id: Dict[str, str] = {}
        self._ids_by_basename_lower: Dict[str, List[str]] = {}
        self._ids_by_title_slug: Dict[str, List[str]] = {}
        self._ids_by_basename_slug: Dict[str, List[str]] = {}

    # -- build -------------------------------------------------------------

    def rebuild(self) -> "BrainIndex":
        """Recompute the whole index from disk. Safe to call after any mutation.

        Two passes on purpose: identities (ids, aliases, tags) must ALL exist before any
        link is resolved. Resolving during a single pass made the result depend on directory
        order — a link to an alias defined in a later file failed to resolve.
        """
        self.forward, self.backlinks, self.unresolved = {}, {}, {}
        self.aliases, self.tags, self.titles = {}, {}, {}
        entries = self.vault.iter_nodes()
        self._ids = {node_id for node_id, _ in entries}
        # Keep the parsed nodes so ``to_graph_payload`` does not re-read and re-parse the
        # whole vault (it previously called ``iter_nodes()`` a second time per build).
        self._node_by_id = {node_id: node for node_id, node in entries}

        # Pass 1 — identities.
        for node_id, node in entries:
            self.titles[node_id] = node.title
            for alias in node.frontmatter.aliases:
                if alias:
                    self.aliases.setdefault(str(alias).lower(), node_id)
            for tag in node.frontmatter.tags:
                self.tags.setdefault(str(tag), []).append(node_id)

        # Precompute resolution lookups BEFORE pass 2, so each link resolves in O(1).
        self._build_resolution_maps()

        # Pass 2 — links, now that every target is known.
        for node_id, node in entries:
            targets: List[str] = []
            seen: Set[str] = set()
            # Explicit [[wikilinks]]
            for link in node.wikilinks:
                resolved = self._resolve(link.target)
                key = resolved or link.target
                if key not in seen:
                    seen.add(key)
                    targets.append(key)
            # Derived 'related' edges from consolidation (§5: entity mentions, semantic overlap)
            for related_id in node.frontmatter.related or []:
                resolved = self._resolve(related_id)
                key = resolved or related_id
                if key not in seen:
                    seen.add(key)
                    targets.append(key)
            self.forward[node_id] = targets

            for target in targets:
                if target in self._ids:
                    self.backlinks.setdefault(target, []).append(node_id)
                else:
                    self.unresolved.setdefault(target, []).append(node_id)

        self._built = True
        return self

    def ensure_built(self) -> "BrainIndex":
        if not self._built:
            self.rebuild()
        return self

    def entries(self) -> List[tuple]:
        """The parsed ``[(node_id, node), ...]`` this index was built from.

        Lets a consumer (recall) reuse the nodes the rebuild already read and parsed instead
        of calling ``vault.iter_nodes()`` a second time — one full vault parse per turn rather
        than two. Equivalent to ``vault.iter_nodes()`` for the vault this index belongs to.
        """
        self.ensure_built()
        return list(self._node_by_id.items())

    @property
    def ids(self) -> Set[str]:
        """Every node id known to the index (builds it first if needed)."""
        self.ensure_built()
        return set(self._ids)

    # -- resolution --------------------------------------------------------

    def _build_resolution_maps(self) -> None:
        """Precompute the O(1) link-resolution lookups used by ``_resolve``.

        Ids are walked in **sorted** order so a case-insensitive or ambiguous match always
        resolves to the same node on every run — the previous ``set`` iteration made that
        order hash-dependent (a latent nondeterminism), and each miss cost a full scan of
        every id, which is what made ``rebuild()`` quadratic.
        """
        from .parser import slugify

        self._lower_to_id = {}
        self._ids_by_basename_lower = {}
        self._ids_by_title_slug = {}
        self._ids_by_basename_slug = {}
        for node_id in sorted(self._ids):
            self._lower_to_id.setdefault(node_id.lower(), node_id)
            base = node_id.split("/")[-1]
            self._ids_by_basename_lower.setdefault(base.lower(), []).append(node_id)
            title_slug = slugify(self.titles.get(node_id, ""))
            if title_slug:
                self._ids_by_title_slug.setdefault(title_slug, []).append(node_id)
            base_slug = slugify(base)
            if base_slug:
                self._ids_by_basename_slug.setdefault(base_slug, []).append(node_id)

    def _resolve(self, name: str) -> Optional[str]:
        """Resolve a wikilink to a node id: exact, case-insensitive, alias, then basename.

        Every step is a precomputed dict hit (``_build_resolution_maps``), so resolution is
        O(1) regardless of vault size — the previous per-step scans of ``self._ids`` were
        what made a large ``rebuild()`` quadratic.
        """
        target = normalize_node_id(name)
        if not target:
            return None
        if target in self._ids:
            return target
        lowered = target.lower()
        if lowered in self._lower_to_id:
            return self._lower_to_id[lowered]
        alias_hit = self.aliases.get(lowered)
        if alias_hit:
            return alias_hit
        # Obsidian "shortest path" links: [[preferences]] may mean user/preferences.
        matches = self._ids_by_basename_lower.get(lowered)
        if matches is not None and len(matches) == 1:
            return matches[0]
        # Title links: [[An Unwritten Idea]] names a note by its title, not its file
        # name — ids are slugs, so a link containing spaces can only resolve by title
        # (spec §7: unresolved *title* links become ghosts; here we give them a chance
        # to resolve first). Slug-compare so [[an unwritten idea]] and [[An Unwritten
        # Idea]] both reach concept/an-unwritten-idea.
        from .parser import slugify

        wanted = slugify(target)
        if wanted and wanted != "untitled":
            by_title = self._ids_by_title_slug.get(wanted)
            if by_title is not None and len(by_title) == 1:
                return by_title[0]
            by_basename = self._ids_by_basename_slug.get(wanted)
            if by_basename is not None and len(by_basename) == 1:
                return by_basename[0]
        return None

    def resolve(self, name: str) -> Optional[str]:
        return self._resolve(name)

    # -- queries -----------------------------------------------------------

    def links_of(self, node_id: str) -> List[str]:
        return list(self.ensure_built().forward.get(normalize_node_id(node_id), []))

    def backlinks_of(self, node_id: str) -> List[str]:
        return list(self.ensure_built().backlinks.get(normalize_node_id(node_id), []))

    def degree(self, node_id: str) -> int:
        self.ensure_built()
        key = normalize_node_id(node_id)
        return len(self.forward.get(key, [])) + len(self.backlinks.get(key, []))

    def neighbors(self, node_id: str, *, hops: int = 1) -> List[str]:
        """Undirected neighbourhood up to ``hops``. Links are traversable both ways."""
        self.ensure_built()
        seen = {normalize_node_id(node_id)}
        frontier = set(seen)
        for _ in range(max(1, hops)):
            nxt: Set[str] = set()
            for current in frontier:
                for other in self.forward.get(current, []) + self.backlinks.get(current, []):
                    if other not in seen:
                        nxt.add(other)
            seen |= nxt
            frontier = nxt
            if not frontier:
                break
        seen.discard(normalize_node_id(node_id))
        return sorted(seen)

    def unresolved_targets(self) -> List[str]:
        return sorted(self.ensure_built().unresolved)

    # -- spreading activation (recall primitive) ---------------------------

    def activate(
        self,
        seeds: Dict[str, float],
        *,
        hops: int = 2,
        decay: float = 0.55,
        min_activation: float = 0.02,
    ) -> Dict[str, float]:
        """Spread activation from ``{node_id: seed_weight}`` over the link graph.

        Proper hop-by-hop propagation (not accumulation): each round spreads only the
        *previous* round's activation, so a node reached by many long paths cannot inflate
        without bound. Each hop multiplies by ``decay``, and a node's activation is divided
        by its **degree** before being passed on, so a heavily-linked hub hands each
        neighbour a small share instead of dominating every recall.

        Consequences that hold by construction, and are asserted in the tests:
            total activation <= sum(seeds) / (1 - decay)     (activation is conserved, not amplified)
            activation at hop k+1 <= decay * activation at hop k
        Seeds are retained in the result; ranking applies retrievability on top.

        Note on the divisor: an earlier revision divided by ``log(1 + degree)``. That looks
        like fan-out dampening but does the opposite — ``n / log(1+n)`` *grows* with n, so
        hubs amplified the signal and total activation diverged from the bound. Degree is the
        correct normaliser; a test pins the bound.
        """
        self.ensure_built()
        current: Dict[str, float] = {}
        for node_id, weight in seeds.items():
            resolved = self._resolve(node_id) or normalize_node_id(node_id)
            if not resolved:
                continue
            try:
                w = float(weight)
            except (TypeError, ValueError):
                continue
            if w > 0:
                current[resolved] = current.get(resolved, 0.0) + w

        if not current:
            return {}

        activation: Dict[str, float] = dict(current)

        for _ in range(max(1, int(hops))):
            nxt: Dict[str, float] = {}
            for node_id, value in current.items():
                neighbours = set(self.forward.get(node_id, [])) | set(self.backlinks.get(node_id, []))
                if not neighbours:
                    continue
                share = value * decay / len(neighbours)
                if share < min_activation:
                    continue
                for other in neighbours:
                    nxt[other] = nxt.get(other, 0.0) + share
            if not nxt:
                break
            for node_id, value in nxt.items():
                activation[node_id] = activation.get(node_id, 0.0) + value
            current = nxt

        return {k: v for k, v in activation.items() if v >= min_activation}

    def stats(self) -> Dict[str, Any]:
        self.ensure_built()
        nodes = len(self._ids)
        edges = sum(len(v) for v in self.forward.values())
        linked = sum(1 for n in self._ids if self.forward.get(n) or self.backlinks.get(n))
        return {
            "nodes": nodes,
            "edges": edges,
            "linked_nodes": linked,
            "isolated_pct": round(100.0 * (nodes - linked) / nodes, 1) if nodes else 0.0,
            "unresolved_targets": len(self.unresolved),
            "tags": len(self.tags),
        }

    def to_graph_payload(self) -> Dict[str, Any]:
        """``{nodes, edges}`` ready for the desktop graph (resolved edges only).

        Each edge carries the typed-edge contract — ``source``, ``target`` plus ``kind``,
        ``provenance`` and ``confidence`` — so a consumer can style and trust an edge, not just
        draw a line. The bare ``source``/``target`` keys are kept unchanged for backward
        compatibility; legacy nodes (no ``relations`` field) are read through
        ``BrainNode.typed_relations()``, which derives a defensible type from the old
        ``related``/``supersedes``/wikilink data, so nothing on disk needs migrating.
        """
        self.ensure_built()
        # Map resolved (source, target) -> the strongest typed relation justifying that edge.
        from .relations import Relation
        from .models import NodeStatus

        # Req #9: a superseded or inactive node must not keep MISLEADING active edges — drop any
        # edge whose endpoint is superseded. (Deleted nodes are already gone from the vault.)
        active_ids: Set[str] = set()
        node_by_id: Dict[str, Any] = self._node_by_id
        for node_id, node in node_by_id.items():
            if node_id in self._ids and node.frontmatter.status == NodeStatus.ACTIVE.value:
                active_ids.add(node_id)

        best: Dict[Tuple[str, str], Relation] = {}
        for node_id, node in node_by_id.items():
            if node_id not in active_ids:
                continue
            for rel in node.typed_relations():
                resolved = self._resolve(rel.target) or rel.target
                if resolved not in active_ids or resolved == node_id:
                    continue
                key = (min(node_id, resolved), max(node_id, resolved))
                prior = best.get(key)
                # Keep the most trustworthy relation per undirected pair (assert > inferred;
                # higher confidence wins ties).
                if prior is None or rel.confidence > prior.confidence:
                    best[key] = rel

        edges: List[Dict[str, Any]] = []
        for (a, b), rel in best.items():
            edges.append({
                "source": a,
                "target": b,
                "kind": rel.rel_type,
                "provenance": rel.provenance,
                "confidence": rel.confidence,
            })
        edges.sort(key=lambda e: (e["source"], e["target"]))
        return {
            "nodes": sorted(self._ids),
            "edges": edges,
            "unresolved": {k: sorted(set(v)) for k, v in self.unresolved.items()},
        }
