"""Markdown, YAML Frontmatter, and Wikilink Parser for PULSE Brain.

Handles extraction and serialization of [[wikilinks]], #tags, and YAML properties
compatible with Obsidian and Obsidian Bases format.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple
from pulse_yaml import safe_dump, safe_load
from agent.brain.models import FrontmatterMetadata, WikilinkTarget

# Regex for Obsidian-style [[target|alias]] or [[target#heading|alias]]
# Matches [[something]], [[something|text]], [[something#heading]], [[something#heading|text]]
WIKILINK_PATTERN = re.compile(
    r"\[\[(?P<target>[^\]\|#\n]+)(?:#(?P<heading>[^\]\|\n]+))?(?:\|(?P<alias>[^\]\n]+))?\]\]"
)

# Regex for YAML Frontmatter block at start of markdown
FRONTMATTER_PATTERN = re.compile(r"^---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)

# Regex for inline hashtags (#concept, #p1, #tech/python)
TAG_PATTERN = re.compile(r"(?:^|\s)#[a-zA-Z0-9_\-\/]+(?:\b|$)")


def parse_wikilinks(content: str) -> List[WikilinkTarget]:
    """Extract all [[wikilinks]] from markdown content with target, heading, and alias."""
    links: List[WikilinkTarget] = []
    seen: Set[str] = set()

    for match in WIKILINK_PATTERN.finditer(content):
        target = match.group("target").strip()
        heading = match.group("heading").strip() if match.group("heading") else None
        alias = match.group("alias").strip() if match.group("alias") else None
        raw = match.group(0)

        # Normalize key to deduplicate identical links per note while preserving order
        dedupe_key = f"{target}#{heading or ''}|{alias or ''}"
        if dedupe_key not in seen:
            seen.add(dedupe_key)
            links.append(WikilinkTarget(
                target=target,
                heading=heading,
                alias=alias,
                raw=raw,
            ))

    return links


def parse_inline_tags(content: str) -> List[str]:
    """Extract inline hashtags like #python, #project/ui from markdown content."""
    tags: List[str] = []
    for match in TAG_PATTERN.finditer(content):
        tag_str = match.group(0).strip().lstrip("#")
        if tag_str and tag_str not in tags:
            tags.append(tag_str)
    return tags


def parse_frontmatter_and_body(raw_text: str) -> Tuple[FrontmatterMetadata, str]:
    """Parse raw Markdown string into FrontmatterMetadata object and body content."""
    if not raw_text or not raw_text.strip():
        return FrontmatterMetadata(), ""

    match = FRONTMATTER_PATTERN.match(raw_text)
    if not match:
        # No frontmatter block, entire text is body
        body = raw_text.strip()
        inline_tags = parse_inline_tags(body)
        meta = FrontmatterMetadata(tags=inline_tags)
        return meta, body

    fm_raw = match.group(1)
    body = raw_text[match.end():].strip()

    try:
        data = safe_load(fm_raw) or {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        # Fallback for lenient parsing if yaml is malformed
        data = {}
        for line in fm_raw.splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                data[k.strip()] = v.strip().strip("\"'")

    # Extract tags (support both yaml list and space/comma-separated strings)
    tags_raw = data.get("tags", [])
    if isinstance(tags_raw, str):
        tags = [t.strip().lstrip("#") for t in re.split(r"[\s,]+", tags_raw) if t.strip()]
    elif isinstance(tags_raw, list):
        tags = [str(t).strip().lstrip("#") for t in tags_raw if str(t).strip()]
    else:
        tags = []

    # Merge inline tags
    for it in parse_inline_tags(body):
        if it not in tags:
            tags.append(it)

    # Extract aliases
    aliases_raw = data.get("aliases", [])
    if isinstance(aliases_raw, str):
        aliases = [a.strip() for a in aliases_raw.split(",") if a.strip()]
    elif isinstance(aliases_raw, list):
        aliases = [str(a).strip() for a in aliases_raw if str(a).strip()]
    else:
        aliases = []

    # Extract related
    related_raw = data.get("related", [])
    if isinstance(related_raw, str):
        related = [r.strip() for r in related_raw.split(",") if r.strip()]
    elif isinstance(related_raw, list):
        related = [str(r).strip() for r in related_raw if str(r).strip()]
    else:
        related = []

    # Confidence score (0.0 to 1.0)
    confidence = 1.0
    if "confidence" in data:
        try:
            confidence = max(0.0, min(1.0, float(data["confidence"])))
        except (ValueError, TypeError):
            confidence = 1.0

    meta = FrontmatterMetadata(
        title=str(data.get("title", "")).strip(),
        category=str(data.get("category", "concept")).strip(),
        tags=tags,
        confidence=confidence,
        status=str(data.get("status", "active")).strip(),
        aliases=aliases,
        related=related,
        supersedes=str(data.get("supersedes", "")).strip() or None,
        superseded_by=str(data.get("superseded_by", "")).strip() or None,
        created_at=data.get("created_at"),
        updated_at=data.get("updated_at"),
        extra={k: v for k, v in data.items() if k not in {
            "title", "category", "tags", "confidence", "status",
            "aliases", "related", "supersedes", "superseded_by",
            "created_at", "updated_at"
        }},
    )

    return meta, body


def serialize_note(meta: FrontmatterMetadata, body: str) -> str:
    """Format FrontmatterMetadata and markdown body into standard Obsidian-compatible Markdown."""
    fm_dict = meta.to_dict()
    # Filter out empty or None fields for cleaner markdown
    clean_dict: Dict[str, Any] = {}
    for k, v in fm_dict.items():
        if v is not None and v != "" and v != [] and v != {}:
            clean_dict[k] = v

    if not clean_dict:
        return body.strip() + "\n"

    try:
        dumped = safe_dump(clean_dict, sort_keys=False, allow_unicode=True)
        fm_yaml = (dumped or "").strip()
    except Exception:
        # Fallback formatting
        lines = []
        for k, v in clean_dict.items():
            lines.append(f"{k}: {v}")
        fm_yaml = "\n".join(lines)

    return f"---\n{fm_yaml}\n---\n\n{body.strip()}\n"
