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
Nothing here may raise at import time, and no optional backend is imported eagerly.
"""

from __future__ import annotations

__all__ = [
    "BrainVault",
    "BrainNode",
    "Frontmatter",
    "NodeCategory",
    "NodeStatus",
    "load_frontmatter",
    "normalize_node_id",
]


def __getattr__(name: str):
    """Lazy re-exports so importing ``agent.brain`` stays dependency-free and cheap."""
    if name in {"BrainVault"}:
        from .vault import BrainVault

        return BrainVault
    if name in {"BrainNode", "Frontmatter", "NodeCategory", "NodeStatus"}:
        from . import models

        return getattr(models, name)
    if name in {"load_frontmatter", "normalize_node_id"}:
        from . import parser

        return getattr(parser, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
