"""Autonomous Cross-Bot Channel Watcher & Debate Engine.

Enables continuous, autonomous collaboration between AI bots without human presence:
- Monitors departmental channels (#c-suite-boardroom, #eng-architecture, #product-design, #operations-incident-war-room).
- When a message contains @mentions, it wakes the target bot profile, runs an autonomous turn,
  and posts the response back into the channel.
- If a message in a channel has no mentions and is from a non-lead member, the channel lead is notified.
- Supports multi-turn peer discussions with loop budgets and thread tracking.
"""

from __future__ import annotations

import json
import logging
import re
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

CHANNEL_LEADS = {
    "c-suite-boardroom": "pulse-ceo",
    "eng-architecture": "pulse-cto",
    "product-design": "pulse-cpo",
    "operations-incident-war-room": "pulse-coo",
}


class ChannelWatcher:
    """Autonomous dispatcher that turns passive channel messages into live inter-bot debates."""

    def __init__(self, root_dir: Optional[Path] = None):
        if root_dir is None:
            self.dir = get_pulse_home() / "enterprise"
            self.channels = EnterpriseChannels()
        else:
            self.dir = Path(root_dir)
            self.channels = EnterpriseChannels(self.dir)

        self.dir.mkdir(parents=True, exist_ok=True)
        self.watermarks_file = self.dir / WATERMARKS_FILENAME
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

    def check_and_dispatch_debates(self, max_dispatches_per_pass: int = 2) -> List[Dict[str, Any]]:
        """Inspect all channels for unhandled @mentions and execute autonomous bot turns."""
        all_channels = self.channels.list_channels()
        dispatched_events = []
        count = 0

        for ch_name, meta in all_channels.items():
            if count >= max_dispatches_per_pass:
                break

            messages = self.channels.get_messages(ch_name, limit=25)
            for msg in messages:
                if msg.id in self.processed_ids:
                    continue

                # Determine target bots to notify
                target_bots: List[str] = []
                if msg.mentions:
                    for m in msg.mentions:
                        bot = m.lstrip("@").strip()
                        if bot and bot != msg.sender_profile:
                            target_bots.append(bot)
                else:
                    # If no mentions, route to channel lead if message wasn't from the lead
                    lead = CHANNEL_LEADS.get(ch_name)
                    if lead and msg.sender_profile != lead:
                        target_bots.append(lead)

                if not target_bots:
                    self.processed_ids.add(msg.id)
                    continue

                for target_bot in target_bots:
                    # Verify bot is member of channel
                    members = meta.get("members", [])
                    if members and target_bot not in members:
                        logger.debug("Bot @%s is not a member of #%s, skipping dispatch.", target_bot, ch_name)
                        continue

                    # Symmetrical thread depth guard (A-B and B-A count toward the same budget)
                    pair = "-".join(sorted([msg.sender_profile, target_bot]))
                    thread_key = f"{ch_name}:{pair}"
                    depth = self.thread_depths.get(thread_key, 0)
                    if depth >= MAX_THREAD_DEPTH:
                        logger.info("Thread %s reached max turn budget (%d). Debate concluding.", thread_key, MAX_THREAD_DEPTH)
                        continue

                    # Execute autonomous bot response
                    logger.info("🤖 Auto-dispatching @%s response in #%s to message from @%s", target_bot, ch_name, msg.sender_profile)
                    reply_text = self._execute_bot_turn(ch_name, target_bot, msg, messages)
                    if reply_text:
                        # Post reply back to channel
                        reply_msg = self.channels.post_message(ch_name, target_bot, reply_text)
                        # NOTE: We do not add reply_msg.id to self.processed_ids so its mentions can trigger follow-ups
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
                            break

                self.processed_ids.add(msg.id)
                self.save_watermarks()
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
        context_lines = []
        for m in recent_messages[-6:]:
            context_lines.append(f"[@{m.sender_profile} in #{channel}]: {m.content}")

        context_str = "\n".join(context_lines)

        prompt = (
            f"You are collaborating in an official corporate channel #{channel}.\n\n"
            f"--- RECENT CHANNEL CONVERSATION ---\n{context_str}\n\n"
            f"--- YOUR MESSAGE TO RESPOND TO ---\n"
            f"@{trigger_msg.sender_profile} stated: \"{trigger_msg.content}\"\n\n"
            f"--- INSTRUCTIONS ---\n"
            f"Respond as your corporate persona @{target_bot}.\n"
            f"Provide a direct, conversational response to @{trigger_msg.sender_profile} in 1-3 sentences.\n"
            f"If delegating, reviewing, or asking a question, explicitly @mention the next person (e.g. @pulse-ceo, @pulse-cto, @vp-core-systems).\n"
            f"Do NOT execute multi-step code refactors or long file searches in this channel turn; give your executive/technical directive or feedback directly."
        )

        cmd = ["pulse", "-p", target_bot, "chat", "-q", prompt]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
            output = res.stdout.strip()
            cleaned_reply = self._extract_clean_reply(output)
            return cleaned_reply or f"Acknowledged by @{target_bot}. Action item logged."
        except subprocess.TimeoutExpired:
            logger.error("Bot @%s timed out while responding in #%s", target_bot, channel)
            return None
        except Exception as exc:
            logger.error("Failed to execute bot turn for @%s: %s", target_bot, exc)
            return None

    def _extract_clean_reply(self, raw_output: str) -> str:
        """Extract the agent's actual prose reply from CLI output boxes and strip ANSI."""
        clean_text = re.sub(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", raw_output)
        lines = clean_text.splitlines()
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
        return text if text else clean_text[-500:].strip()
