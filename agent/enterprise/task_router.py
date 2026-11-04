"""Hierarchical Task Routing System for Enterprise Organization.

Enforces proper corporate delegation flow:
1. CEO creates high-level Epic tasks for VPs
2. VP breaks Epic into Stories and assigns to IC-level squad workers
3. IC completes Stories and reports to VP
4. VP consolidates and reports to CEO

Task Assignment Policy:
- CEO: Strategic planning, never touches code
- VPs: Architecture review, task decomposition, never implementation
- ICs (IC-3 to IC-6): Implementation, testing, delivery

Integrates with SquadManager for worker provisioning and EnterpriseOrchestrator
for Kanban task tracking.
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

logger = logging.getLogger("enterprise.task_router")

ROUTING_LEDGER_FILE = "task_routing_ledger.json"


class TaskType(Enum):
    """Task hierarchy levels in the organization."""
    EPIC = "epic"          # CEO-level strategic initiatives
    STORY = "story"        # VP-level decomposed work units
    SUBTASK = "subtask"    # IC-level implementation tasks


class TaskStatus(Enum):
    """Task lifecycle status."""
    CREATED = "created"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    REJECTED = "rejected"


class RoleLevel(Enum):
    """Corporate hierarchy role levels."""
    CEO = "ceo"
    VP = "vp"
    IC = "ic"


@dataclass
class TaskAssignment:
    """Represents a single task assignment in the corporate hierarchy."""
    task_id: str
    task_type: TaskType
    title: str
    description: str
    assignee_profile: str
    assignee_role: RoleLevel
    assigner_profile: str
    parent_task_id: Optional[str] = None
    child_task_ids: List[str] = field(default_factory=list)
    status: TaskStatus = TaskStatus.CREATED
    created_at: float = field(default_factory=time.time)
    assigned_at: Optional[float] = None
    completed_at: Optional[float] = None
    artifacts: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class DelegationReport:
    """Report submitted by subordinate to superior after task completion."""
    report_id: str
    task_id: str
    submitter_profile: str
    recipient_profile: str
    summary: str
    artifacts_produced: List[str]
    child_tasks_completed: List[str]
    timestamp: float = field(default_factory=time.time)
    verification_status: str = "pending"  # pending | approved | rejected


class TaskRouter:
    """Enforces hierarchical task routing and delegation in enterprise org."""

    def __init__(self, storage_dir: Optional[Path] = None):
        if storage_dir is None:
            self.dir = get_pulse_home() / "enterprise"
            self.dir.mkdir(parents=True, exist_ok=True)
        else:
            self.dir = Path(storage_dir)
            self.dir.mkdir(parents=True, exist_ok=True)
        
        self.ledger_file = self.dir / ROUTING_LEDGER_FILE
        self.tasks: Dict[str, TaskAssignment] = {}
        self.reports: Dict[str, DelegationReport] = {}
        self.load()

    def load(self) -> None:
        """Load routing ledger from disk."""
        if not self.ledger_file.exists():
            return
        try:
            data = json.loads(self.ledger_file.read_text(encoding="utf-8"))
            
            # Load tasks
            for tid, tdata in data.get("tasks", {}).items():
                tdata["task_type"] = TaskType(tdata["task_type"])
                tdata["status"] = TaskStatus(tdata["status"])
                tdata["assignee_role"] = RoleLevel(tdata["assignee_role"])
                self.tasks[tid] = TaskAssignment(**tdata)
            
            # Load reports
            for rid, rdata in data.get("reports", {}).items():
                self.reports[rid] = DelegationReport(**rdata)
                
            logger.info("Loaded %d tasks and %d reports from routing ledger.", len(self.tasks), len(self.reports))
        except Exception as exc:
            logger.error("Failed to load routing ledger: %s", exc)

    def save(self) -> None:
        """Persist routing ledger to disk."""
        try:
            data = {
                "tasks": {},
                "reports": {}
            }
            
            for tid, task in self.tasks.items():
                tdict = asdict(task)
                tdict["task_type"] = task.task_type.value
                tdict["status"] = task.status.value
                tdict["assignee_role"] = task.assignee_role.value
                data["tasks"][tid] = tdict
            
            for rid, report in self.reports.items():
                data["reports"][rid] = asdict(report)
            
            self.ledger_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8"
            )
        except Exception as exc:
            logger.error("Failed to save routing ledger: %s", exc)

    def _infer_role_level(self, profile: str) -> RoleLevel:
        """Infer organizational role level from profile name."""
        profile_lower = profile.lower()
        if "ceo" in profile_lower:
            return RoleLevel.CEO
        elif "vp-" in profile_lower or profile_lower.startswith("vp"):
            return RoleLevel.VP
        else:
            return RoleLevel.IC

    def _validate_delegation(
        self,
        assigner_role: RoleLevel,
        assignee_role: RoleLevel,
        task_type: TaskType
    ) -> tuple[bool, str]:
        """Validate that delegation follows proper organizational hierarchy."""
        
        # CEO can only create Epics for VPs
        if assigner_role == RoleLevel.CEO:
            if task_type != TaskType.EPIC:
                return False, "CEO can only create Epic-level tasks"
            if assignee_role != RoleLevel.VP:
                return False, "CEO can only assign Epics to VPs, not ICs directly"
        
        # VP can only create Stories for ICs (never Epics)
        elif assigner_role == RoleLevel.VP:
            if task_type == TaskType.EPIC:
                return False, "VPs cannot create Epic-level tasks (CEO-only)"
            if task_type == TaskType.STORY and assignee_role != RoleLevel.IC:
                return False, "VP can only assign Stories to ICs"
        
        # ICs can create subtasks for other ICs (peer delegation)
        elif assigner_role == RoleLevel.IC:
            if task_type in (TaskType.EPIC, TaskType.STORY):
                return False, "ICs cannot create Epic or Story tasks"
            if assignee_role in (RoleLevel.CEO, RoleLevel.VP):
                return False, "ICs cannot delegate upward to VPs or CEO"
        
        return True, "Delegation valid"

    def create_task(
        self,
        title: str,
        description: str,
        task_type: TaskType,
        assigner_profile: str,
        assignee_profile: str,
        parent_task_id: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None
    ) -> tuple[bool, str, Optional[TaskAssignment]]:
        """
        Create a new task with hierarchy validation.
        
        Returns:
            (success, message, task_assignment or None)
        """
        assigner_role = self._infer_role_level(assigner_profile)
        assignee_role = self._infer_role_level(assignee_profile)
        
        # Validate delegation is proper
        valid, msg = self._validate_delegation(assigner_role, assignee_role, task_type)
        if not valid:
            logger.warning(
                "Invalid delegation blocked: %s (@%s/%s → @%s/%s, %s)",
                msg, assigner_profile, assigner_role.value,
                assignee_profile, assignee_role.value, task_type.value
            )
            return False, f"Delegation policy violation: {msg}", None
        
        # Validate parent task relationship
        if parent_task_id:
            if parent_task_id not in self.tasks:
                return False, f"Parent task {parent_task_id} not found", None
            
            parent = self.tasks[parent_task_id]
            # Epics decompose into Stories, Stories decompose into Subtasks
            if parent.task_type == TaskType.EPIC and task_type != TaskType.STORY:
                return False, "Epics can only be broken into Stories", None
            if parent.task_type == TaskType.STORY and task_type != TaskType.SUBTASK:
                return False, "Stories can only be broken into Subtasks", None
        
        # Create task
        task_id = f"task_{task_type.value}_{uuid.uuid4().hex[:8]}"
        task = TaskAssignment(
            task_id=task_id,
            task_type=task_type,
            title=title,
            description=description,
            assignee_profile=assignee_profile,
            assignee_role=assignee_role,
            assigner_profile=assigner_profile,
            parent_task_id=parent_task_id,
            status=TaskStatus.ASSIGNED,
            assigned_at=time.time(),
            metadata=metadata or {}
        )
        
        self.tasks[task_id] = task
        
        # Link to parent
        if parent_task_id:
            self.tasks[parent_task_id].child_task_ids.append(task_id)
        
        self.save()
        
        logger.info(
            "Task created: %s (%s) @%s → @%s [%s]",
            task_id, task_type.value, assigner_profile, assignee_profile, title
        )
        
        return True, f"Task {task_id} created successfully", task

    def update_task_status(
        self,
        task_id: str,
        new_status: TaskStatus,
        updater_profile: str
    ) -> tuple[bool, str]:
        """Update task status with authorization check."""
        if task_id not in self.tasks:
            return False, f"Task {task_id} not found"
        
        task = self.tasks[task_id]
        
        # Only assignee can update their task
        if task.assignee_profile != updater_profile:
            return False, "Only the task assignee can update status"
        
        old_status = task.status
        task.status = new_status
        
        if new_status == TaskStatus.COMPLETED:
            task.completed_at = time.time()
        
        self.save()
        
        logger.info(
            "Task %s status updated: %s → %s by @%s",
            task_id, old_status.value, new_status.value, updater_profile
        )
        
        return True, f"Task {task_id} status updated to {new_status.value}"

    def submit_completion_report(
        self,
        task_id: str,
        submitter_profile: str,
        summary: str,
        artifacts_produced: List[str]
    ) -> tuple[bool, str, Optional[DelegationReport]]:
        """
        IC/VP submits completion report to superior after finishing task.
        """
        if task_id not in self.tasks:
            return False, f"Task {task_id} not found", None
        
        task = self.tasks[task_id]
        
        # Validate submitter is the assignee
        if task.assignee_profile != submitter_profile:
            return False, "Only task assignee can submit completion report", None
        
        # Validate task is completed
        if task.status != TaskStatus.COMPLETED:
            return False, "Task must be marked completed before submitting report", None
        
        # Determine recipient (superior who assigned the task)
        recipient_profile = task.assigner_profile
        
        # Gather child tasks
        child_tasks_completed = [
            cid for cid in task.child_task_ids
            if cid in self.tasks and self.tasks[cid].status == TaskStatus.COMPLETED
        ]
        
        report_id = f"report_{uuid.uuid4().hex[:8]}"
        report = DelegationReport(
            report_id=report_id,
            task_id=task_id,
            submitter_profile=submitter_profile,
            recipient_profile=recipient_profile,
            summary=summary,
            artifacts_produced=artifacts_produced,
            child_tasks_completed=child_tasks_completed
        )
        
        self.reports[report_id] = report
        self.save()
        
        logger.info(
            "Completion report %s submitted: @%s → @%s for task %s",
            report_id, submitter_profile, recipient_profile, task_id
        )
        
        return True, f"Report {report_id} submitted successfully", report

    def approve_report(
        self,
        report_id: str,
        approver_profile: str
    ) -> tuple[bool, str]:
        """Superior approves subordinate's completion report."""
        if report_id not in self.reports:
            return False, f"Report {report_id} not found"
        
        report = self.reports[report_id]
        
        # Validate approver is the recipient
        if report.recipient_profile != approver_profile:
            return False, "Only report recipient can approve"
        
        report.verification_status = "approved"
        self.save()
        
        logger.info("Report %s approved by @%s", report_id, approver_profile)
        return True, f"Report {report_id} approved"

    def get_tasks_assigned_to(
        self,
        profile: str,
        status_filter: Optional[TaskStatus] = None
    ) -> List[TaskAssignment]:
        """Get all tasks assigned to a specific profile."""
        tasks = [t for t in self.tasks.values() if t.assignee_profile == profile]
        
        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]
        
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def get_tasks_created_by(
        self,
        profile: str,
        status_filter: Optional[TaskStatus] = None
    ) -> List[TaskAssignment]:
        """Get all tasks created/assigned by a specific profile."""
        tasks = [t for t in self.tasks.values() if t.assigner_profile == profile]
        
        if status_filter:
            tasks = [t for t in tasks if t.status == status_filter]
        
        return sorted(tasks, key=lambda t: t.created_at, reverse=True)

    def get_pending_reports_for(self, profile: str) -> List[DelegationReport]:
        """Get all pending completion reports awaiting approval from profile."""
        return [
            r for r in self.reports.values()
            if r.recipient_profile == profile and r.verification_status == "pending"
        ]

    def get_task_hierarchy(self, task_id: str) -> Optional[Dict[str, Any]]:
        """Get full task hierarchy tree (parent and all descendants)."""
        if task_id not in self.tasks:
            return None
        
        def build_tree(tid: str) -> Dict[str, Any]:
            task = self.tasks[tid]
            return {
                "task": asdict(task),
                "children": [build_tree(cid) for cid in task.child_task_ids if cid in self.tasks]
            }
        
        return build_tree(task_id)

    def generate_routing_stats(self) -> Dict[str, Any]:
        """Generate organizational task routing statistics."""
        stats = {
            "total_tasks": len(self.tasks),
            "by_type": {tt.value: 0 for tt in TaskType},
            "by_status": {ts.value: 0 for ts in TaskStatus},
            "by_role": {rl.value: 0 for rl in RoleLevel},
            "total_reports": len(self.reports),
            "pending_reports": len([r for r in self.reports.values() if r.verification_status == "pending"]),
            "approved_reports": len([r for r in self.reports.values() if r.verification_status == "approved"])
        }
        
        for task in self.tasks.values():
            stats["by_type"][task.task_type.value] += 1
            stats["by_status"][task.status.value] += 1
            stats["by_role"][task.assignee_role.value] += 1
        
        return stats


# Convenience factory function for integration with orchestrator
def create_ceo_epic_for_vp(
    title: str,
    description: str,
    vp_profile: str,
    router: Optional[TaskRouter] = None
) -> tuple[bool, str, Optional[TaskAssignment]]:
    """CEO creates an Epic task for a VP (common entry point)."""
    if router is None:
        router = TaskRouter()
    
    return router.create_task(
        title=title,
        description=description,
        task_type=TaskType.EPIC,
        assigner_profile="pulse-ceo",
        assignee_profile=vp_profile
    )


def vp_decompose_epic_to_stories(
    epic_task_id: str,
    stories: List[Dict[str, Any]],
    vp_profile: str,
    router: Optional[TaskRouter] = None
) -> List[tuple[bool, str, Optional[TaskAssignment]]]:
    """VP breaks down an Epic into multiple Story tasks for ICs."""
    if router is None:
        router = TaskRouter()
    
    results = []
    for story_spec in stories:
        result = router.create_task(
            title=story_spec["title"],
            description=story_spec["description"],
            task_type=TaskType.STORY,
            assigner_profile=vp_profile,
            assignee_profile=story_spec["assignee_ic_profile"],
            parent_task_id=epic_task_id,
            metadata=story_spec.get("metadata")
        )
        results.append(result)
    
    return results
