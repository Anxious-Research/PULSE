"""Enterprise Cross-Functional Channels (Slack / Teams Architecture).

Provides persistent departmental communication channels for autonomous cross-functional debate:
- #c-suite-boardroom (Executive strategy & governance)
- #eng-architecture (Technical RFC review & architectural decisions)
- #product-design (PRDs, UX roadmaps & design tokens)
- #operations-incident-war-room (SEV incident coordination & release trains)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.channels")

STANDARD_CHANNELS = {
    "c-suite-boardroom": {
        "description": "Executive strategy, corporate governance, and founder directives.",
        "members": [
            "pulse-ceo", "pulse-cto", "pulse-cpo", "pulse-ciso",
            "pulse-coo", "pulse-chro", "pulse-cfo", "pulse-general-counsel",
        ],
    },
    "eng-architecture": {
        "description": "Core software architecture, RFC discussions, and technical standards.",
        "members": [
            "pulse-cto", "vp-core-systems", "vp-ai-research",
            "vp-cognitive-memory", "vp-ml-platform", "vp-developer-platform",
        ],
    },
    "product-design": {
        "description": "Product roadmaps, UX wireframes, design tokens, and documentation.",
        "members": [
            "pulse-cpo", "vp-product-pmo", "vp-design-systems", "vp-technical-docs",
        ],
    },
    "operations-incident-war-room": {
        "description": "Production incidents (SEV-0/SEV-1), SRE health, release trains & security alerts.",
        "members": [
            "pulse-coo", "pulse-ciso", "vp-cloud-sre",
            "vp-release-engineering", "vp-qa-verification", "vp-infosec-redteam",
        ],
    },
}


@dataclass
class ChannelMessage:
    id: str
    channel: str
    sender_profile: str
    content: str
    timestamp: float = field(default_factory=time.time)
    mentions: List[str] = field(default_factory=list)


class EnterpriseChannels:
    """Manages virtual departmental channels and inter-agent message buses."""

    def __init__(self, root_dir: Optional[Path] = None):
        if root_dir is None:
            self.dir = get_pulse_home() / "enterprise" / "channels"
        else:
            self.dir = Path(root_dir) / "channels"
        self.dir.mkdir(parents=True, exist_ok=True)
        self._ensure_standard_channels()

    def _ensure_standard_channels(self) -> None:
        for ch_name, meta in STANDARD_CHANNELS.items():
            meta_path = self.dir / f"{ch_name}.meta.json"
            if not meta_path.exists():
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def list_channels(self) -> Dict[str, Dict[str, Any]]:
        channels = {}
        for f in self.dir.glob("*.meta.json"):
            ch_name = f.stem.replace(".meta", "")
            try:
                channels[ch_name] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                channels[ch_name] = {}
        return channels

    def post_message(self, channel: str, sender_profile: str, content: str) -> ChannelMessage:
        """Post a message to a departmental channel."""
        ch_file = self.dir / f"{channel}.jsonl"
        # Parse @mentions
        mentions = [word for word in content.split() if word.startswith("@")]

        msg = ChannelMessage(
            id=f"msg_{int(time.time()*1000)}",
            channel=channel,
            sender_profile=sender_profile,
            content=content,
            mentions=mentions,
        )

        with ch_file.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(msg), ensure_ascii=False) + "\n")

        logger.info("[%s] @%s: %s", channel, sender_profile, content[:60])
        return msg

    def get_messages(self, channel: str, limit: int = 50) -> List[ChannelMessage]:
        """Fetch latest messages from a channel."""
        ch_file = self.dir / f"{channel}.jsonl"
        if not ch_file.exists():
            return []

        lines = ch_file.read_text(encoding="utf-8").strip().splitlines()
        records = []
        for line in lines[-limit:]:
            if line.strip():
                try:
                    records.append(ChannelMessage(**json.loads(line)))
                except Exception:
                    continue
        return records
