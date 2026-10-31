"""Enterprise Autonomous Worker Squad Spawning & Delegation Engine.

Enables VPs and C-Suite to provision on-demand autonomous worker squads (IC-3 to IC-6):
- Dynamic task decomposition into worker subagents.
- Scoped mission goals, deliverables, and execution tracking.
- Automated decommissioning and performance logging once mission concludes.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.squad_spawner")

SQUADS_FILENAME = "squads.json"


@dataclass
class SquadWorker:
    worker_id: str
    role_title: str
    level: str  # IC-3 (Junior), IC-4 (Mid), IC-5 (Senior), IC-6 (Staff)
    specialization: str
    tasks_completed: int = 0
    status: str = "idle"  # idle | active | completed | failed


@dataclass
class AutonomousSquad:
    squad_id: str
    lead_profile: str
    squad_name: str
    mission_goal: str
    workers: List[SquadWorker] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    closed_at: Optional[float] = None
    status: str = "active"  # active | disbanded
    artifacts: List[str] = field(default_factory=list)


class SquadManager:
    """Manages dynamic provisioning and lifecycle of worker squads."""

    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            self.dir = get_pulse_home() / "enterprise"
            self.dir.mkdir(parents=True, exist_ok=True)
        else:
            self.dir = Path(storage_dir)
            self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / SQUADS_FILENAME
        self.squads: Dict[str, AutonomousSquad] = {}
        self.load()

    def load(self) -> None:
        if not self.file.exists():
            return
        try:
            raw = json.loads(self.file.read_text(encoding="utf-8"))
            for sid, sdata in raw.items():
                workers = [SquadWorker(**w) for w in sdata.pop("workers", [])]
                self.squads[sid] = AutonomousSquad(workers=workers, **sdata)
        except Exception as exc:
            logger.warning("Could not load squads: %s", exc)

    def save(self) -> None:
        try:
            data = {}
            for sid, sq in self.squads.items():
                sdict = asdict(sq)
                data[sid] = sdict
            self.file.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save squads: %s", exc)

    def spawn_squad(
        self,
        lead_profile: str,
        squad_name: str,
        mission_goal: str,
        worker_specs: List[Dict[str, str]],
    ) -> AutonomousSquad:
        """Spawn an on-demand workforce squad under a VP or C-Suite leader."""
        squad_id = f"squad_{uuid.uuid4().hex[:8]}"
        workers = []
        for i, spec in enumerate(worker_specs):
            wid = f"{squad_id}_w{i+1}"
            workers.append(
                SquadWorker(
                    worker_id=wid,
                    role_title=spec.get("role_title", "Software Engineer"),
                    level=spec.get("level", "IC-5"),
                    specialization=spec.get("specialization", "General Engineering"),
                )
            )

        squad = AutonomousSquad(
            squad_id=squad_id,
            lead_profile=lead_profile,
            squad_name=squad_name,
            mission_goal=mission_goal,
            workers=workers,
        )
        self.squads[squad_id] = squad
        self.save()
        logger.info("Squad '%s' (%s) spawned by @%s with %d workers.", squad_name, squad_id, lead_profile, len(workers))
        return squad

    def record_squad_artifact(self, squad_id: str, artifact_path_or_description: str) -> None:
        if squad_id in self.squads:
            self.squads[squad_id].artifacts.append(artifact_path_or_description)
            self.save()

    def disband_squad(self, squad_id: str) -> Optional[AutonomousSquad]:
        """Disband an ephemeral worker squad upon mission completion."""
        if squad_id not in self.squads:
            return None
        squad = self.squads[squad_id]
        squad.status = "disbanded"
        squad.closed_at = time.time()
        for w in squad.workers:
            w.status = "completed"
        self.save()
        logger.info("Squad '%s' (%s) gracefully disbanded.", squad.squad_name, squad_id)
        return squad

    def list_active_squads(self) -> List[AutonomousSquad]:
        return [s for s in self.squads.values() if s.status == "active"]
