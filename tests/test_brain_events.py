"""Brain event pipeline tests (specs/brain.md §16).

The graph must react to real state changes, so these tests pin the whole path from a vault
mutation to a frame arriving on the gateway channel transport:

    vault.write_node() → emit_brain_event() → BrainEventBus → bridge → /api/events channel

Nothing here asserts on a hand-built event: every event under test is produced by performing the
mutation that is supposed to cause it.
"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from agent.brain.events import (
    MEMORY_CREATED,
    MEMORY_DELETED,
    MEMORY_REINFORCED,
    MEMORY_SUPERSEDED,
    MEMORY_UPDATED,
    RELATIONSHIP_CREATED,
    BrainEventBus,
    get_brain_event_bus,
)
from agent.brain.vault import BrainVault


class TestBrainEventBus(unittest.TestCase):
    def test_emit_reaches_subscribers_in_order(self):
        bus = BrainEventBus()
        seen = []
        bus.subscribe(lambda e: seen.append((e.seq, e.kind)))
        bus.emit(MEMORY_CREATED, "concept/a")
        bus.emit(MEMORY_UPDATED, "concept/a")
        self.assertEqual(seen, [(1, MEMORY_CREATED), (2, MEMORY_UPDATED)])

    def test_unsubscribe_stops_delivery(self):
        bus = BrainEventBus()
        seen = []
        unsub = bus.subscribe(lambda e: seen.append(e.kind))
        bus.emit(MEMORY_CREATED, "concept/a")
        unsub()
        bus.emit(MEMORY_CREATED, "concept/b")
        self.assertEqual(seen, [MEMORY_CREATED])

    def test_one_failing_subscriber_does_not_block_others(self):
        bus = BrainEventBus()
        seen = []

        def boom(_event):
            raise RuntimeError("subscriber exploded")

        bus.subscribe(boom)
        bus.subscribe(lambda e: seen.append(e.kind))
        bus.emit(MEMORY_CREATED, "concept/a")
        self.assertEqual(seen, [MEMORY_CREATED], "a broken listener must not stop the fan-out")

    def test_history_replays_from_a_seq(self):
        bus = BrainEventBus()
        bus.emit(MEMORY_CREATED, "a")
        bus.emit(MEMORY_CREATED, "b")
        bus.emit(MEMORY_CREATED, "c")
        replay = bus.history(since_seq=1)
        self.assertEqual([e.node_id for e in replay], ["b", "c"])

    def test_history_is_bounded(self):
        bus = BrainEventBus(history=3)
        for i in range(10):
            bus.emit(MEMORY_CREATED, f"n{i}")
        self.assertEqual([e.node_id for e in bus.history()], ["n7", "n8", "n9"])


class TestVaultEmitsRealEvents(unittest.TestCase):
    """Every event is caused by an actual mutation — no synthetic events in the suite."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.vault = BrainVault(vault_dir=self.tmp)
        self.vault.ensure_vault_structure()
        self.bus = get_brain_event_bus()
        self.seen = []
        self.unsub = self.bus.subscribe(self.seen.append)

    def tearDown(self):
        self.unsub()

    def _kinds(self):
        return [e.kind for e in self.seen]

    def test_create_then_update(self):
        self.vault.write_node("concept/alpha", "Alpha is a thing.")
        self.vault.write_node("concept/alpha", "Alpha is a thing, revised.")
        self.assertEqual(self._kinds()[:2], [MEMORY_CREATED, MEMORY_UPDATED])

    def test_wikilink_emits_relationship(self):
        self.vault.write_node("concept/alpha", "Alpha links to [[concept/beta]].")
        rel = [e for e in self.seen if e.kind == RELATIONSHIP_CREATED]
        self.assertEqual(len(rel), 1)
        self.assertEqual(rel[0].payload["target"], "concept/beta")

    def test_recall_emits_reinforced(self):
        self.vault.write_node("concept/alpha", "Alpha is a thing.")
        self.seen.clear()
        self.vault.record_access(["concept/alpha"])
        self.assertIn(MEMORY_REINFORCED, self._kinds())

    def test_supersede_and_delete(self):
        self.vault.write_node("concept/old", "Old claim.")
        self.vault.write_node("concept/new", "New claim.")
        self.seen.clear()
        self.vault.supersede("concept/old", "concept/new")
        self.vault.delete_node("concept/new")
        self.assertEqual(self._kinds(), [MEMORY_SUPERSEDED, MEMORY_DELETED])

    def test_event_payload_carries_category_and_salience(self):
        self.vault.write_node("user/pref", "I prefer Hinglish.", category="user", salience=0.7)
        created = next(e for e in self.seen if e.kind == MEMORY_CREATED)
        self.assertEqual(created.payload["category"], "user")
        self.assertEqual(created.payload["salience"], 0.7)


class TestBridgeDeliversOverChannel(unittest.TestCase):
    """The bridge republishes bus events onto the gateway's channel transport, unchanged."""

    def test_bus_event_reaches_channel_subscriber(self):
        from fastapi import FastAPI

        from pulse_cli.brain_events_bridge import BRAIN_CHANNEL, install_brain_event_bridge
        from agent.brain.events import emit_brain_event

        received = []

        class FakeSocket:
            async def send_text(self, text):
                received.append(text)

        async def _scenario():
            app = FastAPI()
            app.state.event_channels = {BRAIN_CHANNEL: {FakeSocket()}}
            app.state.event_lock = asyncio.Lock()
            unsub = install_brain_event_bridge(app)
            self.assertIsNotNone(unsub, "bridge failed to install on a running loop")
            try:
                # A real mutation, off the loop thread — exactly how a turn's encode runs.
                await asyncio.to_thread(emit_brain_event, MEMORY_CREATED, "concept/x", title="x")
                for _ in range(50):
                    if received:
                        break
                    await asyncio.sleep(0.01)
            finally:
                unsub()

        asyncio.run(_scenario())
        self.assertTrue(received, "no frame reached the channel subscriber")
        frame = json.loads(received[0])
        self.assertEqual(frame["kind"], MEMORY_CREATED)
        self.assertEqual(frame["node_id"], "concept/x")


class TestRecentEventsEndpoint(unittest.TestCase):
    def test_recent_returns_bus_history(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from agent.brain.events import emit_brain_event
        from pulse_cli.web_routers.status import router

        emit_brain_event(MEMORY_CREATED, "concept/endpoint-probe")
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        res = client.get("/api/brain/events/recent")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertIn("seq", body)
        self.assertTrue(body["events"], "history should include the probe event")
        self.assertIn("concept/endpoint-probe", [e["node_id"] for e in body["events"]])


class TestLiveFrameCrossesTheSocket(unittest.TestCase):
    """End-to-end: a real vault mutation delivers a frame over /api/events?channel=brain.

    This exercises the whole live tail — bus → bridge → gateway channel transport → a WebSocket
    subscriber — not just the bus or the bridge in isolation. The frame under test is produced by
    an actual ``write_node``; nothing here hand-builds an event.
    """

    def test_mutation_frame_reaches_a_channel_subscriber(self):
        import contextlib
        import tempfile
        import threading
        import time

        try:
            from fastapi import FastAPI
            from starlette.testclient import TestClient
        except Exception as exc:  # pragma: no cover - environment guard
            self.skipTest(f"FastAPI/TestClient unavailable: {exc}")

        import pulse_cli.web_routers.chat_ws as chat_ws
        from pulse_cli.brain_events_bridge import BRAIN_CHANNEL, install_brain_event_bridge

        # The sidecar accept-path gates read dashboard state owned by web_server. In-process the
        # TestClient peer is "testclient" (not loopback), so make the gate loopback-permissive —
        # the gate itself is covered by the dashboard auth suite; here we test the event path.
        orig_enabled = chat_ws._DASHBOARD_EMBEDDED_CHAT_ENABLED
        orig_auth = chat_ws._ws_auth_ok
        orig_allowed = chat_ws._ws_request_is_allowed
        chat_ws._DASHBOARD_EMBEDDED_CHAT_ENABLED = True
        chat_ws._ws_auth_ok = lambda ws: True
        chat_ws._ws_request_is_allowed = lambda ws: True

        tmp = Path(tempfile.mkdtemp())
        bridge = {}

        @contextlib.asynccontextmanager
        async def _lifespan(app):
            bridge["unsub"] = install_brain_event_bridge(app)
            try:
                yield
            finally:
                if bridge.get("unsub"):
                    bridge["unsub"]()

        app = FastAPI(lifespan=_lifespan)
        app.include_router(chat_ws.router)

        try:
            with TestClient(app) as client:
                with client.websocket_connect(f"/api/events?channel={BRAIN_CHANNEL}") as sock:
                    time.sleep(0.25)  # let the subscriber register
                    vault = BrainVault(vault_dir=tmp)
                    vault.ensure_vault_structure()
                    # Mutate off the loop thread — exactly how a turn's encode runs.
                    t = threading.Thread(
                        target=lambda: vault.write_node(
                            "concept/wire-probe", "Wire probe is a real memory.",
                            category="concept", salience=0.6))
                    t.start()
                    t.join()
                    frame = json.loads(sock.receive_text())
        finally:
            chat_ws._DASHBOARD_EMBEDDED_CHAT_ENABLED = orig_enabled
            chat_ws._ws_auth_ok = orig_auth
            chat_ws._ws_request_is_allowed = orig_allowed

        self.assertEqual(frame["kind"], MEMORY_CREATED)
        self.assertEqual(frame["node_id"], "concept/wire-probe")


if __name__ == "__main__":
    unittest.main()
