"""Enterprise Incident Management & PagerDuty On-Call War Room Engine.

Automates production incident response:
- Severity levels: SEV-0 (Critical outage/security breach), SEV-1 (High regression), SEV-2, SEV-3.
- Automated paging of on-call VPs based on component failure.
- Auto-routing of emergency alerts to #operations-incident-war-room.
- Generation of Blameless Post-Mortems upon resolution.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home
from .channels import EnterpriseChannels

logger = logging.getLogger("enterprise.incident")

INCIDENTS_STORE_FILENAME = "incidents.json"


class Severity(str, Enum):
    SEV0 = "SEV-0"  # Critical: Platform down or security breach
    SEV1 = "SEV-1"  # High: Core regression / test suite failure
    SEV2 = "SEV-2"  # Medium: Degraded performance / partial failure
    SEV3 = "SEV-3"  # Low: Minor bug / non-blocking glitch


class IncidentStatus(str, Enum):
    TRIGGERED = "triggered"
    ACKNOWLEDGED = "acknowledged"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"


@dataclass
class IncidentRecord:
    incident_id: str
    severity: str
    title: str
    description: str
    component: str
    assigned_vp: str
    status: str = IncidentStatus.TRIGGERED.value
    triggered_at: float = field(default_factory=time.time)
    resolved_at: Optional[float] = None
    timeline: List[str] = field(default_factory=list)
    root_cause: str = ""
    mitigation_notes: str = ""


class IncidentManager:
    """Manages active enterprise incidents, war rooms, and on-call escalation."""

    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            self.dir = get_pulse_home() / "enterprise"
        else:
            self.dir = Path(storage_dir)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / INCIDENTS_STORE_FILENAME
        self.incidents: Dict[str, IncidentRecord] = {}
        self.channels = EnterpriseChannels(self.dir.parent)
        self.load()

    def load(self) -> None:
        if not self.file.exists():
            return
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
            for inc_id, idata in raw.items():
                self.incidents[inc_id] = IncidentRecord(**idata)
        except Exception as exc:
            logger.warning("Could not load incidents: %s", exc)

    def save(self) -> None:
        try:
            data = {k: asdict(v) for k, v in self.incidents.items()}
            self.file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save incidents: %s", exc)

    def trigger_incident(
        self,
        severity: Severity,
        title: str,
        description: str,
        component: str,
    ) -> IncidentRecord:
        """Trigger an emergency incident, page the on-call VP, and alert the War Room."""
        inc_id = f"INC-{uuid.uuid4().hex[:6].upper()}"

        # Determine On-Call Leader
        if "security" in component.lower() or "poison" in component.lower():
            assigned = "pulse-ciso"
        elif "infra" in component.lower() or "sre" in component.lower() or "runtime" in component.lower():
            assigned = "vp-cloud-sre"
        elif "test" in component.lower() or "qa" in component.lower():
            assigned = "vp-qa-verification"
        else:
            assigned = "pulse-coo"

        rec = IncidentRecord(
            incident_id=inc_id,
            severity=severity.value,
            title=title,
            description=description,
            component=component,
            assigned_vp=assigned,
        )
        rec.timeline.append(f"[{time.strftime('%H:%M:%S')}] Triggered: {title} (Paging @{assigned})")
        self.incidents[inc_id] = rec
        self.save()

        # Post to War Room channel
        war_room_msg = (
            f"🚨 **{severity.value} INCIDENT TRIGGERED: {inc_id}**\n"
            f"**Title**: {title}\n"
            f"**Component**: `{component}`\n"
            f"**On-Call Lead Paged**: @{assigned}\n"
            f"**Details**: {description}"
        )
        try:
            self.channels.post_message("operations-incident-war-room", "incident-pager", war_room_msg)
        except Exception as exc:
            logger.warning("Could not post to war room channel: %s", exc)

        logger.critical("[%s] %s paged to @%s: %s", severity.value, inc_id, assigned, title)
        return rec

    def acknowledge_incident(self, incident_id: str, responder_profile: str) -> None:
        if incident_id not in self.incidents:
            raise KeyError(f"Incident {incident_id} not found.")
        rec = self.incidents[incident_id]
        rec.status = IncidentStatus.ACKNOWLEDGED.value
        rec.timeline.append(f"[{time.strftime('%H:%M:%S')}] Acknowledged by @{responder_profile}")
        self.save()

    def resolve_incident(
        self,
        incident_id: str,
        responder_profile: str,
        root_cause: str,
        mitigation_notes: str,
    ) -> str:
        """Resolve an active incident and compile a Blameless Post-Mortem."""
        if incident_id not in self.incidents:
            raise KeyError(f"Incident {incident_id} not found.")
        rec = self.incidents[incident_id]
        rec.status = IncidentStatus.RESOLVED.value
        rec.resolved_at = time.time()
        rec.root_cause = root_cause
        rec.mitigation_notes = mitigation_notes
        rec.timeline.append(f"[{time.strftime('%H:%M:%S')}] Resolved by @{responder_profile}: {mitigation_notes}")
        self.save()

        post_mortem = (
            f"# Blameless Post-Mortem: {rec.incident_id}\n"
            f"- **Severity**: {rec.severity}\n"
            f"- **Title**: {rec.title}\n"
            f"- **Lead Responder**: @{responder_profile}\n"
            f"- **Root Cause**: {root_cause}\n"
            f"- **Mitigation**: {mitigation_notes}\n"
            f"- **Timeline**:\n" + "\n".join(f"  • {t}" for t in rec.timeline)
        )
        return post_mortem
