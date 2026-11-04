"""Map commit authors to GitHub logins for release notes and attribution checks.

``AUTHOR_MAP`` merges the frozen ``LEGACY_AUTHOR_MAP`` (authors_legacy.py) with
entries from ``contributors/contributors.json`` (and any legacy entries under
``contributors/emails/`` if present); contributors.json wins on duplicates.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from scripts.releases.authors_legacy import LEGACY_AUTHOR_MAP

REPO_ROOT = Path(__file__).resolve().parents[2]

CONTRIBUTORS_JSON = REPO_ROOT / "contributors" / "contributors.json"
CONTRIBUTORS_EMAILS_DIR = REPO_ROOT / "contributors" / "emails"


def _load_contributors_json(path: Path | None = None) -> dict[str, str]:
    """Load contributor email → GitHub login mappings from contributors/contributors.json."""
    path = path or CONTRIBUTORS_JSON
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        mapping = {}
        for email, entry in data.items():
            if isinstance(entry, dict):
                login = entry.get("login")
            else:
                login = entry
            if login:
                mapping[email] = str(login).lstrip("@")
        return mapping
    except (OSError, json.JSONDecodeError):
        return {}


def _load_contributor_dir(directory: Path | None = None) -> dict[str, str]:
    """Load one-file-per-email mappings from contributors/emails/ (legacy compatibility).

    Filename = commit-author email, first non-comment line = GitHub login.
    """
    directory = directory or CONTRIBUTORS_EMAILS_DIR
    mapping = {}
    if not directory.is_dir():
        return mapping
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        try:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    mapping[path.name] = line.lstrip("@")
                    break
        except OSError:
            continue
    return mapping


# Effective map: frozen legacy dict + legacy directory entries + contributors.json (contributors.json wins).
AUTHOR_MAP = {**LEGACY_AUTHOR_MAP, **_load_contributor_dir(), **_load_contributors_json()}


def resolve_author(name: str, email: str) -> str:
    """Resolve a git author to a GitHub @mention."""
    # Try email lookup first
    gh_user = AUTHOR_MAP.get(email)
    if gh_user:
        return f"@{gh_user}"

    # Try noreply pattern
    noreply_match = re.match(r"(\d+)\+(.+)@users\.noreply\.github\.com", email)
    if noreply_match:
        return f"@{noreply_match.group(2)}"

    # Try username@users.noreply.github.com
    noreply_match2 = re.match(r"(.+)@users\.noreply\.github\.com", email)
    if noreply_match2:
        return f"@{noreply_match2.group(1)}"

    # Fallback to git name
    return name
