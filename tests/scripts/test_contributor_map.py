"""Tests for the contributor mapping system (.mailmap and contributors.json).

Contributor email → GitHub login mappings live in contributors/contributors.json
and the root .mailmap. The legacy AUTHOR_MAP dict in scripts/releases/authors_legacy.py
is frozen; authors.py merges both at import time with contributors.json winning on duplicates.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS_DIR = REPO_ROOT / "scripts"

sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(SCRIPTS_DIR))

from scripts.releases import authors, authors_legacy  # noqa: E402
from add_contributor import add_contributor, get_contributor, read_mapping_file  # noqa: E402


# ── loader behavior ───────────────────────────────────────────────────


def test_loader_reads_login_from_contributors_json(tmp_path):
    json_path = tmp_path / "contributors.json"
    json_path.write_text(
        json.dumps({
            "jane@example.com": {"login": "janedoe", "comment": "PR #1"},
            "bob@example.com": "@bobuser",
        }),
        encoding="utf-8",
    )
    mapping = authors._load_contributors_json(json_path)
    assert mapping == {
        "jane@example.com": "janedoe",
        "bob@example.com": "bobuser",
    }


def test_loader_reads_legacy_directory(tmp_path):
    d = tmp_path / "emails"
    d.mkdir()
    (d / "jane@example.com").write_text("# salvage PR #1\njanedoe\n# trailing note\n")
    mapping = authors._load_contributor_dir(d)
    assert mapping == {"jane@example.com": "janedoe"}


def test_effective_map_merges_legacy_and_registry():
    # Invariant: every legacy entry survives into the effective map unless
    # shadowed by a registry entry, and the registry contributes on top.
    assert set(authors_legacy.LEGACY_AUTHOR_MAP) <= (
        set(authors.AUTHOR_MAP) | set(authors._load_contributors_json())
    )
    for email, login in authors._load_contributors_json().items():
        assert authors.AUTHOR_MAP[email] == login


# ── add_contributor.py CLI behavior ───────────────────────────────────


@pytest.fixture()
def contrib_env(tmp_path, monkeypatch):
    import add_contributor

    d = tmp_path / "contributors"
    d.mkdir(parents=True, exist_ok=True)
    json_file = d / "contributors.json"
    mailmap_file = tmp_path / ".mailmap"
    mailmap_file.write_text("# .mailmap\n", encoding="utf-8")

    monkeypatch.setattr(add_contributor, "CONTRIBUTORS_DIR", d)
    monkeypatch.setattr(add_contributor, "CONTRIBUTORS_JSON", json_file)
    monkeypatch.setattr(add_contributor, "EMAILS_DIR", d / "emails")
    monkeypatch.setattr(add_contributor, "MAILMAP_FILE", mailmap_file)
    return d, json_file, mailmap_file


def test_add_creates_mapping_in_json_and_mailmap(contrib_env):
    d, json_file, mailmap_file = contrib_env
    rc = add_contributor("new@example.com", "newperson", "PR #999 salvage")
    assert rc == 0
    assert json_file.is_file()
    assert get_contributor("new@example.com", json_file) == "newperson"
    assert read_mapping_file(d / "emails" / "new@example.com") == "newperson"

    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert data["new@example.com"]["login"] == "newperson"
    assert data["new@example.com"]["comment"] == "PR #999 salvage"

    mailmap_text = mailmap_file.read_text(encoding="utf-8")
    assert "newperson <new@example.com> <new@example.com>" in mailmap_text


def test_add_refuses_login_conflicting_with_legacy_map(contrib_env):
    d, json_file, _ = contrib_env
    email, login = next(iter(authors_legacy.LEGACY_AUTHOR_MAP.items()))
    assert add_contributor(email, login + "x") == 1
    assert not json_file.exists() or email not in json.loads(json_file.read_text(encoding="utf-8"))


def test_add_accepts_legacy_consecutive_hyphen_login(contrib_env):
    # Legacy GitHub accounts with consecutive hyphens are real (Roger--Han);
    # current signup rules forbid them but existing logins remain valid.
    d, json_file, _ = contrib_env
    assert add_contributor("roger.hanhong@gmail.com", "Roger--Han") == 0
    assert get_contributor("roger.hanhong@gmail.com", json_file) == "Roger--Han"


def test_add_strips_at_prefix(contrib_env):
    d, json_file, _ = contrib_env
    assert add_contributor("z@z.com", "@zeta") == 0
    assert get_contributor("z@z.com", json_file) == "zeta"


def test_cli_entrypoint_end_to_end(tmp_path):
    # Run the real script in a subprocess against a temp repo layout.
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("add_contributor.py",):
        # Explicit encoding: add_contributor.py contains UTF-8 multi-byte
        # characters (an em dash), so the locale-default read_text() raises
        # UnicodeDecodeError on non-UTF-8 Windows locales (e.g. cp950).
        (scripts / name).write_text(
            (SCRIPTS_DIR / name).read_text(encoding="utf-8"), encoding="utf-8"
        )
    # Minimal stub author map so the legacy lookup import works
    (scripts / "releases").mkdir()
    (scripts / "releases" / "authors_legacy.py").write_text("LEGACY_AUTHOR_MAP = {}\n")
    proc = subprocess.run(
        [sys.executable, str(scripts / "add_contributor.py"),
         "cli@example.com", "cliperson", "via subprocess"],
        cwd=tmp_path, capture_output=True, text=True,
    )
    assert proc.returncode == 0, proc.stderr
    json_path = tmp_path / "contributors" / "contributors.json"
    assert json_path.is_file()
    data = json.loads(json_path.read_text(encoding="utf-8"))
    assert data["cli@example.com"]["login"] == "cliperson"
    assert data["cli@example.com"]["comment"] == "via subprocess"


# ── case-insensitive collisions ───────────────────────────────────────


def test_add_contributor_refuses_case_collision_even_for_same_login(contrib_env, capsys):
    # Same login, different spelling: still refused — the problem is the
    # key pair, not the login. The exact spelling is what's "present".
    d, json_file, _ = contrib_env
    json_file.write_text(json.dumps({"Foo@Example.com": {"login": "foouser"}}), encoding="utf-8")

    assert add_contributor("foo@example.com", "foouser") == 1
    assert "Foo@Example.com" in capsys.readouterr().err
    data = json.loads(json_file.read_text(encoding="utf-8"))
    assert list(data.keys()) == ["Foo@Example.com"]
    # Exact-case re-add is the ordinary idempotent path.
    assert add_contributor("Foo@Example.com", "foouser") == 0


def test_case_collision_uses_casefold(contrib_env):
    # casefold, not lower: matches how macOS/Windows fold non-ASCII (ß ~ ss).
    d, json_file, _ = contrib_env
    json_file.write_text(json.dumps({"strasse@example.com": {"login": "someone"}}), encoding="utf-8")
    assert add_contributor("STRASSE@example.com", "someone") == 1
    assert add_contributor("straße@example.com", "someone") == 1
