"""Forgetting curve and reconsolidation math.

Human memory does not delete — it becomes harder to retrieve. This module is the whole
of that behaviour, as pure functions so it is trivially testable:

    retrievability(stability, last_accessed, now)   ->  0..1, decays with time and disuse
    on_recall(stability, access_count)              ->  strengthened stability (spacing effect)

``stability`` is the memory's resistance to forgetting (roughly, "how many days until it
is half-forgotten"): a freshly encoded memory starts around 1.0 and grows each time it is
recalled, so frequently used knowledge becomes effectively permanent while one-off trivia
fades — without anything ever being removed from the vault.

Deliberately exponential and monotonic: no tuning constants beyond the half-life, no
randomness, no dependency on wall-clock beyond the timestamps passed in.
"""

from __future__ import annotations

import math
import time
from typing import Optional

DAY_SECONDS = 86400.0

# Retrievability at/under which a node is considered dormant (still stored, rarely recalled).
DORMANT_THRESHOLD = 0.05

STABILITY_GROWTH = 0.6      # each recall multiplies stability by (1 + GROWTH * spacing_factor)
MAX_STABILITY = 3650.0      # ~10 years; practical cap so float growth cannot run away
MIN_STABILITY = 0.05
# A retrievability of exactly 0.0 would mean "gone". exp() underflows to 0 for very old
# memories, so floor it: a memory may become unreachable in practice, never nonexistent.
MIN_RETRIEVABILITY = 1e-9
# Used for junk input (missing/NaN/negative stability). Neutral is correct here: treating
# a corrupt value as *almost forgotten* would silently bury a real memory.
NEUTRAL_STABILITY = 1.0


def _now(now: Optional[float] = None) -> float:
    return float(now if now is not None else time.time())


def clamp_stability(stability: float) -> float:
    try:
        value = float(stability)
    except (TypeError, ValueError):
        return NEUTRAL_STABILITY
    if math.isnan(value) or value <= 0:
        return NEUTRAL_STABILITY
    return max(MIN_STABILITY, min(MAX_STABILITY, value))


def retrievability(stability: float, last_accessed: Optional[float], now: Optional[float] = None) -> float:
    """Probability-style recall strength in (0, 1].

    ``stability`` is in days. A node never accessed is treated as accessed at encoding
    time by the caller; a ``None`` timestamp therefore yields 1.0 (no elapsed time known).
    The result is floored at a small positive value — forgetting lowers retrievability but
    must never reach zero, because nothing is ever deleted.
    """
    s = clamp_stability(stability)
    if last_accessed is None:
        return 1.0
    elapsed_days = max(0.0, (_now(now) - float(last_accessed)) / DAY_SECONDS)
    return max(MIN_RETRIEVABILITY, float(math.exp(-elapsed_days / s)))


def is_dormant(stability: float, last_accessed: Optional[float], now: Optional[float] = None) -> bool:
    """True when a memory has faded below the active-recall floor (never a delete signal)."""
    return retrievability(stability, last_accessed, now) < DORMANT_THRESHOLD


def on_recall(
    stability: float,
    access_count: int,
    last_accessed: Optional[float] = None,
    now: Optional[float] = None,
) -> float:
    """New stability after a recall — the spacing effect.

    Recalling something just after seeing it barely helps; recalling it after a long gap
    helps a lot. So growth scales with the gap relative to current stability.
    """
    s = clamp_stability(stability)
    now_ts = _now(now)
    if last_accessed is None:
        spacing_factor = 1.0
    else:
        gap_days = max(0.0, (now_ts - float(last_accessed)) / DAY_SECONDS)
        spacing_factor = 1.0 + min(2.0, gap_days / s)
    bonus = 1.0 + STABILITY_GROWTH * spacing_factor
    # Diminishing returns: keep the first few recalls impactful, later ones marginal.
    decay = 1.0 / (1.0 + 0.05 * max(0, int(access_count)))
    return clamp_stability(s * (1.0 + (bonus - 1.0) * decay))


def decay_multiplier(stability: float, last_accessed: Optional[float], now: Optional[float] = None) -> float:
    """Retrievability used as a ranking multiplier (alias of :func:`retrievability`)."""
    return retrievability(stability, last_accessed, now)


def rank_score(
    activation: float,
    stability: float,
    last_accessed: Optional[float],
    salience: float,
    *,
    now: Optional[float] = None,
) -> float:
    """Final recall ranking: associative activation × retrievability × salience.

    Activation dominates (it is what the cue asked for), but a faded or trivial memory
    cannot outrank a strong, well-consolidated one at equal activation.
    """
    try:
        act = max(0.0, float(activation))
    except (TypeError, ValueError):
        act = 0.0
    try:
        sal = max(0.0, min(1.0, float(salience)))
    except (TypeError, ValueError):
        sal = 0.5
    return act * retrievability(stability, last_accessed, now) * (0.25 + 0.75 * sal)
