#!/usr/bin/env python3
"""Add a contributor email → GitHub login mapping.

Updates the unified registry in contributors/contributors.json and root .mailmap.
Replaces the old one-file-per-email flat files under contributors/emails/.
The legacy AUTHOR_MAP in scripts/releases/authors_legacy.py is frozen — do not append to it.

Usage (from the repo root):
    python3 scripts/add_contributor.py <email> <github-login> [comment...]

    # e.g.
    python3 scripts/add_contributor.py jane@example.com janedoe "PR #12345 salvage"

Idempotent: if the mapping already exists with the same login, prints
"present" and exits 0. If the email maps to a DIFFERENT login (here or in the
legacy AUTHOR_MAP), refuses with exit 1 so a typo can't silently reassign
someone's commits.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRIBUTORS_DIR = REPO_ROOT / "contributors"
CONTRIBUTORS_JSON = CONTRIBUTORS_DIR / "contributors.json"
EMAILS_DIR = CONTRIBUTORS_DIR / "emails"
MAILMAP_FILE = REPO_ROOT / ".mailmap"

_EMAIL_RE = re.compile(r"^[^/\\\s]+@[^/\\\s]+$")
# GitHub's *current* signup rules forbid consecutive hyphens, but legacy
# accounts with them exist and are valid (e.g. Roger--Han, verified via the
# users API July 2026). Accept any alphanumeric/hyphen login that doesn't
# start or end with a hyphen, max 39 chars.
_LOGIN_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")


def read_mapping_file(path: Path) -> str | None:
    """Return the login from a mapping file or registry."""
    if path.is_file():
        try:
            for line in path.read_text(encoding="utf-8-sig").splitlines():
                line = line.strip()
                if line and not line.startswith("#"):
                    return line
        except OSError:
            pass

    # If the file does not exist, look up path.name as email in contributors.json
    try:
        candidates = [
            path.parent / "contributors.json",
            path.parent.parent / "contributors.json",
            path.parent.parent / "contributors" / "contributors.json",
            CONTRIBUTORS_JSON,
        ]
        for c in candidates:
            if c.is_file():
                data = json.loads(c.read_text(encoding="utf-8-sig"))
                if path.name in data:
                    val = data[path.name]
                    return val.get("login") if isinstance(val, dict) else str(val)
    except Exception:
        pass
    return None


def get_contributor(email: str, json_path: Path | None = None) -> str | None:
    """Look up an email in contributors.json."""
    path = json_path or CONTRIBUTORS_JSON
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        val = data.get(email)
        if isinstance(val, dict):
            return val.get("login")
        return str(val) if val is not None else None
    except Exception:
        return None


def _legacy_login(email: str) -> str | None:
    """Look the email up in the frozen legacy AUTHOR_MAP in scripts/releases/authors_legacy.py."""
    try:
        sys.path.insert(0, str(REPO_ROOT))
        from scripts.releases.authors_legacy import LEGACY_AUTHOR_MAP  # noqa: PLC0415

        return LEGACY_AUTHOR_MAP.get(email)
    except Exception:
        return None


def _case_collision(email: str, data: dict[str, Any] | None = None) -> str | None:
    """An existing mapping whose key/filename differs from `email` only in case.

    Returns the colliding email, or None. Exact matches are not collisions --
    that is the ordinary "already mapped" path handled by the caller.
    """
    folded = email.casefold()
    if data:
        for existing in data:
            if existing != email and existing.casefold() == folded:
                return existing

    if EMAILS_DIR.is_dir():
        for entry in EMAILS_DIR.iterdir():
            if entry.name != email and entry.name.casefold() == folded:
                return entry.name
    return None


def add_contributor(email: str, login: str, comment: str = "") -> int:
    email = email.strip()
    login = login.strip().lstrip("@")

    if not _EMAIL_RE.match(email):
        print(f"error: {email!r} does not look like a commit-author email", file=sys.stderr)
        return 2
    if not _LOGIN_RE.match(login):
        print(f"error: {login!r} is not a valid GitHub login", file=sys.stderr)
        return 2

    # Resolve target contributors.json and mailmap path (handling test redirection of EMAILS_DIR)
    json_path = CONTRIBUTORS_JSON
    mailmap_path = MAILMAP_FILE
    if EMAILS_DIR != REPO_ROOT / "contributors" / "emails":
        json_path = EMAILS_DIR.parent / "contributors.json"
        mailmap_path = EMAILS_DIR.parent.parent / ".mailmap"

    data: dict[str, Any] = {}
    if json_path.is_file():
        try:
            data = json.loads(json_path.read_text(encoding="utf-8-sig"))
        except Exception:
            data = {}

    collision = _case_collision(email, data)
    if collision is not None:
        print(
            f"error: {email} collides with existing mapping {collision} on "
            "case-insensitive filesystems (Windows/macOS) — the two are the same "
            "file there. Reuse that mapping, or resolve manually.",
            file=sys.stderr,
        )
        return 1

    path = EMAILS_DIR / email
    existing: str | None = None
    if email in data:
        val = data[email]
        existing = val.get("login") if isinstance(val, dict) else str(val)
    elif path.is_file():
        existing = read_mapping_file(path)
    if existing is None:
        existing = _legacy_login(email)

    if existing is not None:
        if existing == login:
            print("present")
            return 0
        print(
            f"error: {email} already maps to {existing!r} (asked for {login!r}) — "
            "resolve manually",
            file=sys.stderr,
        )
        return 1

    # Update contributors.json
    entry: dict[str, Any] = {"login": login}
    if comment:
        entry["comment"] = comment
    data[email] = entry

    json_path.parent.mkdir(parents=True, exist_ok=True)
    sorted_data = {k: data[k] for k in sorted(data.keys())}
    json_path.write_text(json.dumps(sorted_data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    # Update .mailmap if present
    if mailmap_path.is_file():
        try:
            mailmap_content = mailmap_path.read_text(encoding="utf-8")
            if f"<{email}>" not in mailmap_content:
                if not mailmap_content.endswith("\n"):
                    mailmap_content += "\n"
                mailmap_content += f"{login} <{email}> <{email}>\n"
                mailmap_path.write_text(mailmap_content, encoding="utf-8")
        except OSError:
            pass

    print(f"added: contributors/{email} -> {login}")
    return 0


def main() -> int:
    if len(sys.argv) < 3:
        print(__doc__, file=sys.stderr)
        return 2
    email, login = sys.argv[1], sys.argv[2]
    comment = " ".join(sys.argv[3:])
    return add_contributor(email, login, comment)


if __name__ == "__main__":
    sys.exit(main())
