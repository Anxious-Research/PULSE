"""Resolve PULSE_HOME for standalone skill scripts.

Skill scripts may run outside the PULSE process (e.g. system Python,
nix env, CI) where ``pulse_constants`` is not importable.  This module
provides the same ``get_pulse_home()`` and ``display_pulse_home()``
contracts as ``pulse_constants`` without requiring it on ``sys.path``.

When ``pulse_constants`` IS available it is used directly so that any
future enhancements (profile resolution, Docker detection, etc.) are
picked up automatically.  The fallback path replicates the core logic
from ``pulse_constants.py`` using only the stdlib.

All scripts under ``google-workspace/scripts/`` should import from here
instead of duplicating the ``PULSE_HOME = Path(os.getenv(...))`` pattern.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from pulse_constants import display_pulse_home as display_pulse_home
    from pulse_constants import get_pulse_home as get_pulse_home
except (ModuleNotFoundError, ImportError):

    def get_pulse_home() -> Path:
        """Return the PULSE home directory (default: ~/.pulse).

        Mirrors ``pulse_constants.get_pulse_home()``."""
        val = os.environ.get("PULSE_HOME", "").strip()
        return Path(val) if val else Path.home() / ".pulse"

    def display_pulse_home() -> str:
        """Return a user-friendly ``~/``-shortened display string.

        Mirrors ``pulse_constants.display_pulse_home()``."""
        home = get_pulse_home()
        try:
            return "~/" + home.relative_to(Path.home()).as_posix()
        except ValueError:
            return str(home)
