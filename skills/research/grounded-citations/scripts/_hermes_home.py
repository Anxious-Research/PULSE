"""Resolve PULSE_HOME for standalone skill scripts.

Skill scripts may run outside the PULSE process (system Python, nix env,
CI) where ``pulse_constants`` is not importable.  This module provides the
same ``get_pulse_home()`` contract without requiring it on ``sys.path``.

When ``pulse_constants`` IS available it is used directly so profile
resolution and any future enhancements are picked up automatically.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from pulse_constants import get_pulse_home as get_pulse_home
except (ModuleNotFoundError, ImportError):

    def get_pulse_home() -> Path:
        """Return the PULSE home directory (default: ``~/.pulse``)."""
        val = os.environ.get("PULSE_HOME", "").strip()
        return Path(val) if val else Path.home() / ".pulse"
