"""Native cognitive memory ("brain") for PULSE.

The brain is an *organ* of the PULSE entity, not a separate application and not a layer
on top of the agent. It stores memory as a plain-Markdown Obsidian-style vault under
``$PULSE_HOME/brain/`` and implements human-like memory behaviour over it:

    encoding        -> salience-gated capture of what is worth remembering
    consolidation   -> episodic (daily/) promoted into semantic (concept/, user/, ...)
    decay           -> forgetting curve; retrievability falls, nothing is deleted
    recall          -> cue-based spreading activation over the wikilink graph
    reconsolidation -> recall strengthens a node (spacing effect)
    interference    -> contradicting knowledge supersedes, never orphans

See ``specs/brain.md`` for the full design.

Import safety: this package must import cleanly with **zero third-party dependencies**.
Nothing here may raise at import time, and no optional backend is imported eagerly — every
name below is resolved lazily through ``__getattr__``.

Naming note: the submodules ``agent.brain.recall`` and ``agent.brain.similarity`` share their
names with the main function inside each. A package attribute can be only one of the two, so
the package re-exports those functions under non-colliding aliases (``recall_memories``,
``text_similarity``) and leaves the submodule names intact. Re-exporting the bare names here
would shadow the submodules and break ``import agent.brain.recall`` /
``import agent.brain.similarity`` — which is exactly what happened the first time this file
was written, and is now pinned by a test.
"""

from __future__ import annotations

__all__ = [
    # storage
    "BrainVault",
    "BrainNode",
    "Frontmatter",
    "NodeCategory",
    "NodeStatus",
    "get_brain_vault_dir",
    # codec
    "compose_markdown",
    "extract_wikilinks",
    "load_frontmatter",
    "normalize_node_id",
    "parse_frontmatter_and_body",
    "rewrite_link_target",
    "slugify",
    # graph
    "BrainIndex",
    # forgetting / consolidation
    "clamp_stability",
    "is_dormant",
    "on_recall",
    "rank_score",
    "retrievability",
    # recall (aliased: see module docstring)
    "RecallHit",
    "RecallResult",
    "extract_cues",
    "recall_memories",
    "recall_block",
    # migration
    "migrate_legacy_memory",
    "needs_migration",
    "plan_migration",
    # similarity (aliased: see module docstring)
    "get_embedder",
    "is_near_duplicate",
    "overlap_coefficient",
    "text_similarity",
    "tokenize",
]

# Public name -> (submodule, attribute). Only names that do NOT collide with a submodule.
_LAZY: dict = {
    "BrainVault": ("vault", "BrainVault"),
    "get_brain_vault_dir": ("vault", "get_brain_vault_dir"),
    "BrainNode": ("models", "BrainNode"),
    "Frontmatter": ("models", "Frontmatter"),
    "NodeCategory": ("models", "NodeCategory"),
    "NodeStatus": ("models", "NodeStatus"),
    "compose_markdown": ("parser", "compose_markdown"),
    "extract_wikilinks": ("parser", "extract_wikilinks"),
    "load_frontmatter": ("parser", "load_frontmatter"),
    "normalize_node_id": ("parser", "normalize_node_id"),
    "parse_frontmatter_and_body": ("parser", "parse_frontmatter_and_body"),
    "rewrite_link_target": ("parser", "rewrite_link_target"),
    "slugify": ("parser", "slugify"),
    "BrainIndex": ("index", "BrainIndex"),
    "clamp_stability": ("decay", "clamp_stability"),
    "is_dormant": ("decay", "is_dormant"),
    "on_recall": ("decay", "on_recall"),
    "rank_score": ("decay", "rank_score"),
    "retrievability": ("decay", "retrievability"),
    "RecallHit": ("recall", "RecallHit"),
    "RecallResult": ("recall", "RecallResult"),
    "extract_cues": ("recall", "extract_cues"),
    "recall_memories": ("recall", "recall"),
    "recall_block": ("recall", "recall_block"),
    "migrate_legacy_memory": ("migrate", "migrate_legacy_memory"),
    "needs_migration": ("migrate", "needs_migration"),
    "plan_migration": ("migrate", "plan_migration"),
    "get_embedder": ("similarity", "get_embedder"),
    "is_near_duplicate": ("similarity", "is_near_duplicate"),
    "overlap_coefficient": ("similarity", "overlap_coefficient"),
    "text_similarity": ("similarity", "similarity"),
    "tokenize": ("similarity", "tokenize"),
}


def __getattr__(name: str):
    """Resolve public names lazily so importing ``agent.brain`` stays cheap and dep-free.

    Deliberately does NOT write results into ``globals()``: caching here is what previously
    shadowed the ``similarity`` and ``recall`` submodules.
    """
    target = _LAZY.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    from importlib import import_module

    return getattr(import_module(f".{target[0]}", __name__), target[1])


def __dir__():
    return sorted(set(globals()) | set(__all__))
