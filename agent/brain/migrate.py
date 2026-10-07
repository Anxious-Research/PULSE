"""Migration: the legacy flat memory files into brain vault nodes.

Stage S2 of specs/brain.md. The old system stored memory as ``§``-delimited plain text:

    $PULSE_HOME/memories/MEMORY.md    agent's own notes   -> concept/ (or project/)
    $PULSE_HOME/memories/USER.md      user profile        -> user/

Rules this module obeys (specs/brain.md §8.4 — a previous attempt destroyed the user's
memory by replacing the store before the replacement worked):

- **Additive and idempotent.** Nothing is deleted, ever. Each imported entry carries a
  ``source_fingerprint`` in its frontmatter, so a second run is a no-op rather than a
  duplicate. Re-running after new entries appear imports only the new ones.
- **Dry-run first.** ``dry_run=True`` reports exactly what would be created without writing.
- **Never overwrites.** If a target node already exists with different content, the entry is
  skipped and reported, not clobbered.
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from .models import NodeCategory
from .parser import slugify
from .similarity import is_near_duplicate
from .vault import BrainVault, get_brain_vault_dir

logger = logging.getLogger(__name__)

# Same delimiter the legacy store used (tools/memory_tool_store.ENTRY_DELIMITER).
ENTRY_DELIMITER = "\n§\n"

# Legacy file -> (target category, source label)
_LEGACY_FILES: Tuple[Tuple[str, str, str], ...] = (
    ("MEMORY.md", NodeCategory.CONCEPT.value, "memory"),
    ("USER.md", NodeCategory.USER.value, "user"),
)


def legacy_memory_dir() -> Path:
    """``$PULSE_HOME/memories`` — located lazily so this module stays import-safe."""
    try:
        from pulse_constants import get_pulse_home

        return Path(get_pulse_home()) / "memories"
    except Exception:  # pragma: no cover
        import os

        home = os.environ.get("PULSE_HOME")
        base = Path(home).expanduser() if home else Path.home() / ".pulse"
        return base / "memories"


def split_entries(raw: str) -> List[str]:
    """Split a legacy file body into entries (mirrors the old store's parser)."""
    if not raw or not raw.strip():
        return []
    return [entry.strip() for entry in raw.split(ENTRY_DELIMITER) if entry.strip()]


def fingerprint(text: str) -> str:
    """Stable content id used to make import idempotent."""
    return hashlib.blake2b(text.strip().encode("utf-8"), digest_size=10).hexdigest()


_MAX_TITLE_WORDS = 6
_CLAUSE_BOUNDARY_RE = re.compile(r"[;:]\s|\s+[—–]\s+|(?<=[a-zA-Z0-9])\.\s")


def _title_for(entry: str, fallback: str) -> str:
    """Short, human-readable title from an entry; a node id is derived from this.

    Truncating the first *line* is not enough: a one-line entry is a whole sentence, and
    slugifying it produced ids like
    ``concept/pulse-brain-vault-lives-in-pulse-brain-one-markdown-file``. So cut at the first
    clause boundary and cap the word count.
    """
    first = entry.strip().splitlines()[0].strip().lstrip("#-* ").strip()
    if not first:
        return fallback
    head = _CLAUSE_BOUNDARY_RE.split(first, maxsplit=1)[0].strip()
    words = head.split() or first.split()
    title = " ".join(words[:_MAX_TITLE_WORDS]).rstrip(" ,;:-—–")
    return title or fallback


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as exc:
        logger.warning("legacy memory unreadable: %s (%s)", path, exc)
        return ""


def plan_migration(
    *,
    source_dir: Optional[Path] = None,
    vault: Optional[BrainVault] = None,
    embedder: Any = None,
) -> Dict[str, Any]:
    """Work out what a migration would do, without writing anything."""
    base = Path(source_dir) if source_dir is not None else legacy_memory_dir()
    v = vault or BrainVault()
    v.ensure_vault_structure()

    existing = v.list_all_nodes()
    existing_fingerprints = {str(n.frontmatter.extra.get("source_fingerprint") or "") for n in existing}
    # Compare content against content. Including titles here would dilute the score and let
    # a genuine duplicate slip through as "different".
    existing_texts = [n.content for n in existing if n.content]

    to_create: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    seen_fingerprints: set = set()

    for filename, category, source in _LEGACY_FILES:
        path = base / filename
        if not path.is_file():
            continue
        for index, entry in enumerate(split_entries(_read(path))):
            fp = fingerprint(entry)
            if fp in existing_fingerprints or fp in seen_fingerprints:
                skipped.append({"file": filename, "index": index, "reason": "already imported"})
                continue
            # Near-duplicate guard: the vault may already hold this fact under another name.
            if any(is_near_duplicate(entry, text, embedder=embedder) for text in existing_texts):
                skipped.append({"file": filename, "index": index, "reason": "near-duplicate of an existing node"})
                continue
            seen_fingerprints.add(fp)
            title = _title_for(entry, f"{filename} entry {index + 1}")
            to_create.append(
                {
                    "file": filename,
                    "index": index,
                    "source": source,
                    "category": category,
                    "title": title,
                    "node_id": v.unique_node_id(category, title),
                    "fingerprint": fp,
                    "content": entry,
                }
            )

    return {
        "source_dir": str(base),
        "vault_dir": str(v.vault_dir),
        "found_files": [f for f, _, _ in _LEGACY_FILES if (base / f).is_file()],
        "to_create": to_create,
        "skipped": skipped,
        "existing_nodes": len(existing),
    }


def migrate_legacy_memory(
    *,
    source_dir: Optional[Path] = None,
    vault: Optional[BrainVault] = None,
    dry_run: bool = True,
    embedder: Any = None,
) -> Dict[str, Any]:
    """Import legacy flat memory into the vault. **Default is a dry run.**

    Returns a report: ``{created, skipped, dry_run, plan}``. Nothing is deleted, and a
    second run with the same source creates nothing.
    """
    plan = plan_migration(source_dir=source_dir, vault=vault, embedder=embedder)
    v = vault or BrainVault()

    if dry_run:
        return {
            "dry_run": True,
            "created": 0,
            "would_create": len(plan["to_create"]),
            "skipped": len(plan["skipped"]),
            "plan": plan,
        }

    created: List[str] = []
    for item in plan["to_create"]:
        v.write_node(
            item["node_id"],
            item["content"],
            title=item["title"],
            category=item["category"],
            tags=["migrated", item["source"]],
            # Stamped so re-running cannot duplicate this entry.
            extra={
                "source_fingerprint": item["fingerprint"],
                "migrated_from": item["file"],
            },
        )
        created.append(item["node_id"])

    return {
        "dry_run": False,
        "created": len(created),
        "created_ids": created,
        "skipped": len(plan["skipped"]),
        "plan": plan,
    }


def needs_migration(*, source_dir: Optional[Path] = None, vault: Optional[BrainVault] = None) -> bool:
    """True when legacy files hold entries that are not in the vault yet."""
    plan = plan_migration(source_dir=source_dir, vault=vault)
    return bool(plan["to_create"])
