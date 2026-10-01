"""Comment-watermark queries in kanban_db.

``list_comments_after`` backs the live worker bridge: it returns only comments
newer than a cursor so a running worker folds in new operator notes without
re-reading the whole thread.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_WORKTREE = Path(__file__).resolve().parents[2]
if str(_WORKTREE) not in sys.path:
    sys.path.insert(0, str(_WORKTREE))

from pulse_cli import kanban_db as kb
from pulse_cli import kanban_db_connect as kbc


@pytest.fixture
def fresh_home(tmp_path, monkeypatch):
    home = tmp_path / "pulse_home"
    home.mkdir()
    monkeypatch.setenv("PULSE_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for var in ("PULSE_KANBAN_DB", "PULSE_KANBAN_WORKSPACES_ROOT", "PULSE_KANBAN_HOME", "PULSE_KANBAN_BOARD"):
        monkeypatch.delenv(var, raising=False)
    try:
        import pulse_constants
        pulse_constants._cached_default_pulse_root = None  # type: ignore[attr-defined]
    except Exception:
        pass
    kb._INITIALIZED_PATHS.clear()
    return home


def test_list_comments_after_cursor(fresh_home):
    conn = kbc.connect()
    try:
        tid = kb.create_task(conn, title="chat")
        c1 = kb.add_comment(conn, tid, author="alice", body="first")
        c2 = kb.add_comment(conn, tid, author="bob", body="second")

        assert [c.id for c in kb.list_comments_after(conn, tid, after_id=0)] == [c1, c2]

        newer = kb.list_comments_after(conn, tid, after_id=c1)
        assert [c.id for c in newer] == [c2]
        assert newer[0].body == "second"

        assert kb.list_comments_after(conn, tid, after_id=c2) == []
    finally:
        conn.close()
