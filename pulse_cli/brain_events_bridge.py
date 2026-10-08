"""Bridge the Brain event bus onto the gateway's channel transport (specs/brain.md §16).

The Brain emits events in whatever thread performed the vault write; the desktop subscribes
over the gateway's WebSocket channel transport, which lives on the asyncio loop. This module is
the single seam between them: it subscribes to the bus once per app and republishes each event
onto the ``brain`` channel with ``run_coroutine_threadsafe``.

Each connected desktop client gets its catch-up from ``GET /api/brain/events/recent`` (a plain
REST read of the bus history) and its live tail from ``/api/events?channel=brain``. Splitting it
that way keeps replay off the hot fan-out path and reuses the channel transport that already
handles auth, disconnect and backpressure.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, Callable, Optional

_log = logging.getLogger("pulse_cli.brain_events")

#: The channel every Brain event is published on. One Brain per profile, so one channel: a client
#: that connects while a turn is encoding sees the same stream the graph is rendering.
BRAIN_CHANNEL = "brain"


def install_brain_event_bridge(app: Any) -> Optional[Callable[[], None]]:
    """Subscribe *app*'s event transport to the Brain bus. Returns the unsubscribe thunk.

    Must be called from inside the running event loop (the app lifespan): it captures the loop
    the broadcasts must be scheduled on. Best-effort — a failure here means the graph falls back
    to polling, which is degraded, not broken.
    """
    try:
        from agent.brain.events import get_brain_event_bus
    except Exception:
        _log.debug("brain event bus unavailable; graph will poll", exc_info=True)
        return None

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        _log.debug("brain bridge installed outside a running loop; graph will poll")
        return None

    def _on_event(event: Any) -> None:
        # Runs on the mutating thread. Hand the coroutine to the loop that owns the sockets; the
        # bridge must never block a vault write waiting for a broadcast.
        try:
            from pulse_cli.web_routers.chat_ws import _broadcast_event

            payload = json.dumps(event.to_dict(), separators=(",", ":"), default=str)
            asyncio.run_coroutine_threadsafe(_broadcast_event(app, BRAIN_CHANNEL, payload), loop)
        except Exception:
            _log.debug("brain event bridge dropped %s", getattr(event, "kind", "?"), exc_info=True)

    unsubscribe = get_brain_event_bus().subscribe(_on_event)
    _log.debug("brain event bridge installed on channel %r", BRAIN_CHANNEL)
    return unsubscribe


__all__ = ["install_brain_event_bridge", "BRAIN_CHANNEL"]
