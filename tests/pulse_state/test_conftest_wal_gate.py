"""The conftest WAL gate must agree with pulse_state, and must not import it.

``tests/conftest.py::_wal_is_usable`` duplicates the SQLite WAL-reset version
predicate instead of importing ``pulse_state``. That is deliberate: importing
``pulse_state`` during collection caches ``DEFAULT_DB_PATH`` from the real
``~/.pulse`` before the per-test ``PULSE_HOME`` redirect, which makes tests
read the developer's live production database.

Duplication needs a guard, so these tests pin the two implementations in
agreement across the documented upstream boundaries.
"""

import sqlite3

import pytest

from pulse_state_wal import is_sqlite_wal_reset_vulnerable
from tests.conftest import _wal_is_usable


@pytest.mark.parametrize(
    "version_info",
    [
        (3, 6, 23),   # pre-WAL
        (3, 7, 0),    # first WAL release — vulnerable
        (3, 44, 5),   # below the 3.44 backport
        (3, 44, 6),   # 3.44 backport — fixed
        (3, 45, 0),   # next minor, back to vulnerable
        (3, 50, 4),   # the version that exposed this (repo .venv)
        (3, 50, 6),   # below the 3.50 backport
        (3, 50, 7),   # 3.50 backport — fixed
        (3, 51, 2),   # last vulnerable
        (3, 51, 3),   # fixed upstream
        (3, 53, 1),   # the managed runtime
    ],
)
def test_conftest_gate_agrees_with_pulse_state(version_info, monkeypatch):
    """``_wai_is_usable`` must be the exact inverse of the canonical predicate."""
    monkeypatch.setattr(sqlite3, "sqlite_version_info", version_info)
    assert _wal_is_usable() is not is_sqlite_wal_reset_vulnerable(version_info), (
        f"conftest gate and pulse_state disagree for SQLite {version_info}"
    )


def test_conftest_does_not_import_pulse_state_at_collection():
    """The gate must stay import-free of pulse_state.

    Importing it during collection caches DEFAULT_DB_PATH from the real
    ~/.pulse, so tests read live production sessions instead of a tempdir.
    Reading the source is not an option here (banned), so assert on behavior:
    the gate must work with ``pulse_state`` absent from ``sys.modules`` and
    blocked from being imported.
    """
    import builtins
    import sys

    real_import = builtins.__import__
    blocked: list[str] = []

    def guard(name, *args, **kwargs):
        if name == "pulse_state" or name.startswith("pulse_state."):
            blocked.append(name)
            raise AssertionError(
                "conftest._wal_is_usable imported pulse_state — this caches "
                "DEFAULT_DB_PATH from the real ~/.pulse during collection"
            )
        return real_import(name, *args, **kwargs)

    saved = sys.modules.pop("pulse_state", None)
    builtins.__import__ = guard
    try:
        _wal_is_usable()  # must not raise
    finally:
        builtins.__import__ = real_import
        if saved is not None:
            sys.modules["pulse_state"] = saved
    assert not blocked
