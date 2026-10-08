"""Brain event bus — the cognitive state change stream (specs/brain.md §16).

The graph is the *observable* state of the Brain, so it must react to real mutations, not to a
timer. Every event on this bus corresponds to one actual change in the vault: a node written, a
recall recorded, a correction applied. Nothing emits an event "to make the graph move" — an event
that is not backed by a mutation is a lie about cognition, and the whole point of the graph is
that it does not lie.

Delivery is **process-local and synchronous**: subscribers are called inline by the mutating
thread, so an emit can never be lost to a queue that never drains and can never reorder relative
to the mutation that caused it. The only subscriber that must cross a thread boundary (the web
gateway's async broadcast) hands off with ``loop.call_soon_threadsafe`` itself — that complexity
belongs to the transport, not to the bus.

A bounded ring buffer keeps the recent history so a client that connects *after* the interesting
moment can still catch up (the desktop reconnects, the panel is opened late) without a full vault
re-read.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Deque, Dict, List, Optional

logger = logging.getLogger("agent.brain.events")

#: Every event kind originates from a real vault mutation (specs/brain.md §16). Keep this list
#: closed: adding a kind here is a promise that something actually emits it.
MEMORY_CREATED = "MEMORY_CREATED"
MEMORY_UPDATED = "MEMORY_UPDATED"
MEMORY_ACTIVATED = "MEMORY_ACTIVATED"
MEMORY_REINFORCED = "MEMORY_REINFORCED"
MEMORY_DECAYED = "MEMORY_DECAYED"
MEMORY_REACTIVATED = "MEMORY_REACTIVATED"
MEMORY_SUPERSEDED = "MEMORY_SUPERSEDED"
MEMORY_ARCHIVED = "MEMORY_ARCHIVED"
MEMORY_DELETED = "MEMORY_DELETED"
RELATIONSHIP_CREATED = "RELATIONSHIP_CREATED"
RELATIONSHIP_UPDATED = "RELATIONSHIP_UPDATED"
RELATIONSHIP_REMOVED = "RELATIONSHIP_REMOVED"
CONSOLIDATION_COMPLETED = "CONSOLIDATION_COMPLETED"

#: How many events a late subscriber can replay. Comfortably more than a burst of encodes
#: produces, cheap to hold, and bounded so a long-lived process cannot leak.
DEFAULT_HISTORY = 256


@dataclass(frozen=True)
class BrainEvent:
    """One real change in the Brain, shaped for the graph to consume directly."""

    kind: str
    node_id: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    seq: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kind": self.kind,
            "node_id": self.node_id,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
            "seq": self.seq,
        }


class BrainEventBus:
    """Thread-safe synchronous fan-out with a bounded replay buffer."""

    def __init__(self, *, history: int = DEFAULT_HISTORY) -> None:
        self._subscribers: List[Callable[[BrainEvent], None]] = []
        self._history: Deque[BrainEvent] = deque(maxlen=max(1, int(history)))
        self._lock = threading.RLock()
        self._seq = 0

    # -- subscription -------------------------------------------------------

    def subscribe(self, callback: Callable[[BrainEvent], None]) -> Callable[[], None]:
        """Register *callback*; returns an unsubscribe thunk.

        A failing subscriber is logged and dropped from the fan-out for that event only — one
        broken listener must never stop the others from seeing real state changes.
        """
        with self._lock:
            self._subscribers.append(callback)

        def _unsubscribe() -> None:
            with self._lock:
                try:
                    self._subscribers.remove(callback)
                except ValueError:
                    pass

        return _unsubscribe

    # -- emit ---------------------------------------------------------------

    def emit(self, kind: str, node_id: str = "", **payload: Any) -> BrainEvent:
        """Publish one event synchronously to every subscriber."""
        with self._lock:
            self._seq += 1
            event = BrainEvent(kind=kind, node_id=node_id, payload=payload, seq=self._seq)
            self._history.append(event)
            subscribers = list(self._subscribers)
        for callback in subscribers:
            try:
                callback(event)
            except Exception:
                logger.debug("brain event subscriber failed for %s", kind, exc_info=True)
        return event

    # -- replay -------------------------------------------------------------

    def history(self, *, since_seq: int = 0) -> List[BrainEvent]:
        """Events with ``seq > since_seq``, oldest first — the catch-up for a late subscriber."""
        with self._lock:
            return [e for e in self._history if e.seq > since_seq]

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq


_BUS: Optional[BrainEventBus] = None
_BUS_LOCK = threading.Lock()


def get_brain_event_bus() -> BrainEventBus:
    """The process-wide bus. One process, one Brain, one event stream."""
    global _BUS
    if _BUS is None:
        with _BUS_LOCK:
            if _BUS is None:
                _BUS = BrainEventBus()
    return _BUS


def emit_brain_event(kind: str, node_id: str = "", **payload: Any) -> None:
    """Emit on the global bus, never raising into a vault write.

    A mutation must not fail because nobody is listening, so every emit is best-effort at the
    call site — the vault write is the operation that matters; the event is how the world finds
    out about it.
    """
    try:
        get_brain_event_bus().emit(kind, node_id, **payload)
    except Exception:
        logger.debug("brain event emit failed for %s", kind, exc_info=True)


__all__ = [
    "BrainEvent",
    "BrainEventBus",
    "get_brain_event_bus",
    "emit_brain_event",
    "MEMORY_CREATED",
    "MEMORY_UPDATED",
    "MEMORY_ACTIVATED",
    "MEMORY_REINFORCED",
    "MEMORY_DECAYED",
    "MEMORY_REACTIVATED",
    "MEMORY_SUPERSEDED",
    "MEMORY_ARCHIVED",
    "MEMORY_DELETED",
    "RELATIONSHIP_CREATED",
    "RELATIONSHIP_UPDATED",
    "RELATIONSHIP_REMOVED",
    "CONSOLIDATION_COMPLETED",
]
