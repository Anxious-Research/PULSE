"""Post-turn Brain encoding must queue concurrent completed turns instead of dropping them."""

from __future__ import annotations

import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from agent.brain import session as session_mod


class BrainEncodeQueueTest(unittest.TestCase):
    def setUp(self):
        with session_mod._encode_lock:
            session_mod._encode_queue.clear()
            session_mod._encode_state.update(running=False, since_consolidate=0, since_reflect=0)

    def tearDown(self):
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            with session_mod._encode_lock:
                if not session_mod._encode_state["running"] and not session_mod._encode_queue:
                    break
            time.sleep(0.01)

        with session_mod._encode_lock:
            session_mod._encode_queue.clear()
            session_mod._encode_state.update(running=False, since_consolidate=0, since_reflect=0)

    def test_completed_turns_are_queued_and_encoded_in_order(self):
        first_started = threading.Event()
        release_first = threading.Event()
        finished = threading.Event()
        encoded = []
        expected = 2

        def encode(agent, user_text, assistant_text, *, session_id):
            encoded.append((user_text, assistant_text, session_id))
            if user_text == "first question":
                first_started.set()
                self.assertTrue(release_first.wait(timeout=2))
            if len(encoded) == expected:
                finished.set()
            return {"encode": "ok"}

        settings = session_mod.BrainSettings(consolidate_every=1000, reflect_every=1000)
        with patch.object(session_mod, "brain_encode_turn", side_effect=encode), \
             patch.object(session_mod, "resolve_settings", return_value=settings):
            self.assertTrue(session_mod.brain_encode_turn_async(
                SimpleNamespace(), "first question", "first answer", session_id="s1"
            ))
            self.assertTrue(first_started.wait(timeout=2))
            self.assertTrue(session_mod.brain_encode_turn_async(
                SimpleNamespace(), "second question", "second answer", session_id="s2"
            ))
            release_first.set()
            self.assertTrue(finished.wait(timeout=2))

        self.assertEqual(encoded, [
            ("first question", "first answer", "s1"),
            ("second question", "second answer", "s2"),
        ])
