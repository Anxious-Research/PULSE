"""Autonomous Cross-Bot Channel Watcher & Debate Engine.

Enables continuous, autonomous collaboration between AI bots without human presence:
- Monitors departmental channels (#c-suite-boardroom, #eng-architecture, etc.).
- When a message contains @mentions, it wakes the target bot profile, runs an autonomous turn,
  and posts the response back into the channel.
- Supports multi-turn peer discussions with loop budgets and thread tracking.
"""

from __future__ import annotations

import json
import logging
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from pulse_constants import get_pulse_home
from .channels import EnterpriseChannels, ChannelMessage

logger = logging.getLogger("enterprise.channel_watcher")

WATERMARKS_FILENAME = "channel_watermarks.json"
MAX_THREAD_DEPTH = 6  # Maximum back-and-forth turns per thread to prevent runaway loops


class ChannelWatcher:
    """Autonomous dispatcher that turns passive channel messages into live inter-bot debates."""

    def __init__(self, root_dir: Optional[Path] = None):
        if root_dir is None:
            self.dir = get_pulse_home() / "enterprise"
        else:
            self.dir = Path(root_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.watermarks_file = self.dir / WATERMARKS_FILENAME
        self.channels = EnterpriseChannels(self.dir.parent)
        self.processed_ids: Set[str] = set()
        self.thread_depths: Dict[str, int] = {}
        self.load_watermarks()

    def load_watermarks(self) -> None:
        if not self.watermarks_file.exists():
            return
        try:
            data = json.loads(self.watermarks_file.read_text(encoding="utf-8"))
            self.processed_ids = set(data.get("processed_ids", []))
            self.thread_depths = data.get("thread_depths", {})
        except Exception as exc:
            logger.warning("Could not load channel watermarks: %s", exc)

    def save_watermarks(self) -> None:
        try:
            # Keep only last 1000 message IDs to prevent file bloat
            pruned_ids = list(self.processed_ids)[-1000:]
            data = {"processed_ids": pruned_ids, "thread_depths": self.thread_depths}
            self.watermarks_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save channel watermarks: %s", exc)

    def check_and_dispatch_debates(self, max_dispatches_per_pass: int = 3) -> List[Dict[str, Any]]:
        """Inspect all channels for unhandled @mentions and execute autonomous bot turns."""
        all_channels = self.channels.list_channels()
        dispatched_events = []
        count = 0

        for ch_name, meta in all_channels.items():
            if count >= max_dispatches_per_pass:
                break

            messages = self.channels.get_messages(ch_name, limit=20)
            for msg in messages:
                if msg.id in self.processed_ids:
                    continue

                self.processed_ids.add(msg.id)
                self.save_watermarks()

                # Find mentions
                if not msg.mentions:
                    continue

                for mention in msg.mentions:
                    target_bot = mention.lstrip("@").strip()
                    # Skip if bot is messaging itself
                    if target_bot == msg.sender_profile:
                        continue

                    # Verify bot is member of channel
                    members = meta.get("members", [])
                    if members and target_bot not in members:
                        logger.debug("Bot @%s is not a member of #%s, skipping dispatch.", target_bot, ch_name)
                        continue

                    # Thread depth guard to prevent infinite runaway loops
                    thread_key = f"{ch_name}:{msg.sender_profile}:{target_bot}"
                    depth = self.thread_depths.get(thread_key, 0)
                    if depth >= MAX_THREAD_DEPTH:
                        logger.info("Thread %s reached max turn budget (%d). Pausing debate.", thread_key, MAX_THREAD_DEPTH)
                        continue

                    # Execute autonomous bot response
                    logger.info("🤖 Auto-dispatching @%s response in #%s to message from @%s", target_bot, ch_name, msg.sender_profile)
                    reply_text = self._execute_bot_turn(ch_name, target_bot, msg, messages)
                    if reply_text:
                        # Post reply back to channel
                        reply_msg = self.channels.post_message(ch_name, target_bot, reply_text)
                        self.processed_ids.add(reply_msg.id)
                        self.thread_depths[thread_key] = depth + 1
                        self.save_watermarks()

                        dispatched_events.append({
                            "channel": ch_name,
                            "target_bot": target_bot,
                            "in_response_to": msg.id,
                            "reply_id": reply_msg.id,
                        })
                        count += 1
                        if count >= max_dispatches_per_pass:
                            return dispatched_events

        return dispatched_events

    def _execute_bot_turn(
        self,
        channel: str,
        target_bot: str,
        trigger_msg: ChannelMessage,
        recent_messages: List[ChannelMessage],
    ) -> Optional[str]:
        """Run an autonomous headless agent turn for the target bot with channel context."""
        # Compile recent conversation context (last 5 messages)
        context_lines = []
        for m in recent_messages[-6:]:
            context_lines.append(f"[@{m.sender_profile} in #{channel}]: {m.content}")

        context_str = "\n".join(context_lines)

        prompt = (
            f"You received an official communication in channel #{channel}.\n\n"
            f"--- RECENT CHANNEL HISTORY ---\n{context_str}\n\n"
            f"--- YOUR TASK ---\n"
            f"You were directly mentioned by @{trigger_msg.sender_profile}: \"{trigger_msg.content}\"\n\n"
            f"Respond directly and professionally as your corporate role. "
            f"If you need another executive or VP to take action, @mention them. "
            f"Keep your response concise, actionable, and zero-filler."
        )

        cmd = ["pulse", "-p", target_bot, "chat", "-q", prompt]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
            output = res.stdout.strip()
            # Clean ANSI and CLI banner headers from output if present
            cleaned_reply = self._extract_clean_reply(output)
            return cleaned_reply or f"Acknowledged by @{target_bot}. Proceeding with action."
        except subprocess.TimeoutExpired:
            logger.error("Bot @%s timed out while responding in #%s", target_bot, channel)
            return None
        except Exception as exc:
            logger.error("Failed to execute bot turn for @%s: %s", target_bot, exc)
            return None

    def _extract_clean_reply(self, raw_output: str) -> str:
        """Extract the agent's actual prose reply from CLI output boxes."""
        lines = raw_output.splitlines()
        content_lines = []
        in_content = False

        for line in lines:
            if "╭─ ☤ PULSE" in line or "─────" in line:
                in_content = True
                continue
            if "╰────" in line or "Resume this session with" in line:
                break
            if in_content:
                content_lines.append(line)

        text = "\n".join(content_lines).strip()
        return text if text else raw_output[-500:].strip()
