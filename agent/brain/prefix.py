"""Layer-1 stable prefix — the small, session-constant slice of memory for the system prompt.

specs/brain.md §6 splits prompt integration into two layers, and the split is the whole point:

- **Layer 1 (this module)** — identity and durable facts. Small, stable, compiled once per
  session into the system prompt.
- **Layer 2 (`session.brain_turn_context`)** — everything else, recalled per turn.

The temptation is to put *all* memory in the system prompt, because that is how the legacy
flat `MEMORY.md` worked. That is what this replaces: a system prompt that grows with the
store (a) has to be char-budgeted, forcing the agent to *forget things to make room*, and
(b) is the cached prefix, so every write invalidates the prompt cache.

**Cache invariant:** for the same vault state this function must return byte-identical text,
in every session, on every platform. So there is a fixed section order, node ids are sorted,
and nothing time- or access-dependent (timestamps, access counts, decay) is rendered. A
faded memory must not silently reword the system prompt.
"""

from __future__ import annotations

from typing import Any, List, Optional

from .models import NodeStatus
from .parser import strip_title_overlap, wikilinks_to_text

# ~1.5k tokens. Small enough to be irrelevant to cost, large enough for identity + profile.
DEFAULT_MAX_CHARS = 6000
# One note cannot crowd out the rest of the prefix.
PER_NODE_MAX_CHARS = 700

# Fixed order: who PULSE is, then who it is talking to.
_SECTIONS = (("self", "Self"), ("user", "User"))

# Superseded (corrected) and archived nodes are history, not present identity.
_EXCLUDED_STATUS = {NodeStatus.SUPERSEDED.value, NodeStatus.ARCHIVED.value}

POINTER = (
    "Long-term memory is a Markdown vault and the notes above are its stable core. "
    "Further notes are recalled automatically when relevant. Write new memories with the "
    "memory tool; nothing is ever deleted, corrections supersede."
)


def _plain(text: str) -> str:
    """Backwards-compatible alias for the shared display renderer."""
    return wikilinks_to_text(text)


def _one_line(text: str, limit: int) -> str:
    """Collapse to a single line and clip on a word boundary."""
    flat = " ".join(_plain(text).split())
    if len(flat) <= limit:
        return flat
    clipped = flat[: limit - 1].rstrip()
    if " " in clipped:
        clipped = clipped.rsplit(" ", 1)[0]
    return clipped + "…"


def compile_stable_prefix(vault: Any, *, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    """Compile the Layer-1 prefix from ``self/`` and ``user/`` nodes.

    Returns ``""`` when the vault is empty, unreadable, or holds no stable notes — the caller
    then injects nothing, so an empty vault cannot add an empty section to the system prompt.
    Never raises: a vault problem must degrade to "no prefix", never to a broken agent.
    """
    try:
        nodes = vault.list_all_nodes()
    except Exception:
        return ""
    if not nodes:
        return ""

    by_category: dict = {}
    for node in nodes:
        try:
            if node.frontmatter.status in _EXCLUDED_STATUS:
                continue
            if node.category not in dict(_SECTIONS):
                continue
            body = (node.content or "").strip()
            if not body:
                continue
            by_category.setdefault(node.category, []).append(node)
        except Exception:
            # One malformed node must not cost the whole prefix.
            continue

    if not by_category:
        return ""

    budget = max(200, int(max_chars))
    sections: List[str] = []
    used = 0

    for category, heading in _SECTIONS:
        entries = sorted(by_category.get(category, []), key=lambda n: n.id)
        if not entries:
            continue
        heading_line = f"## {heading}"
        # Reserve the heading and the blank line joining this section to the previous one
        # BEFORE accepting rows, so the running total can never overshoot the budget.
        reserve = len(heading_line) + 1 + (2 if sections else 0)
        running = used + reserve
        lines: List[str] = []
        for node in entries:
            title = _one_line(node.title or node.id, 120)
            body = strip_title_overlap(_plain(node.content), node.title or "")
            line = f"- {title}: {_one_line(body, PER_NODE_MAX_CHARS)}"
            if running + len(line) + 1 > budget:
                break
            lines.append(line)
            running += len(line) + 1
        if lines:
            sections.append(heading_line + "\n" + "\n".join(lines))
            used = running

    if not sections:
        return ""

    prefix = "\n\n".join(sections)
    if used + 2 + len(POINTER) <= budget:
        prefix = f"{prefix}\n\n{POINTER}"
    return prefix


def has_stable_content(vault: Any, *, max_chars: int = DEFAULT_MAX_CHARS) -> bool:
    """True when a prefix would actually be produced — lets a caller skip the work."""
    return bool(compile_stable_prefix(vault, max_chars=max_chars))
