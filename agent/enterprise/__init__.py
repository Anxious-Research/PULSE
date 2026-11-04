"""PULSE Autonomous Enterprise Operating System.

Provides enterprise-grade governance, accountability, and operational mechanisms:
- talent_ops: Performance tracking, stack ranking, automated PIP, and layoffs.
- gatekeeper: Multi-phase zero-defect code review pipeline (QA, CISO, CTO).
- git_merger: Real physical Git rebase, fast-forward merge into main, and push.
- workspace_isolator: Isolated Git worktrees per task to eliminate file collisions.
- cost_guard: Hardware-level token circuit breaker protecting against wallet drain.
- verifier: Deterministic proof-of-work verifier rejecting hallucinated completions.
- sla_watchdog: Deadlock detection and automated task succession for fired bots.
- orchestrator: Enterprise OKR cascade, Kanban dispatching, and executive briefing.
- squad_spawner: Dynamic workforce provisioning (IC-3 to IC-6 worker squads).
- channels: Cross-functional departmental messaging channels (#c-suite, #eng-architecture, etc.).
- rfc_system: Institutional RFC & Architecture Decision Record (ADR) lifecycle.
- incident_manager: Production SEV incident response, on-call paging, and post-mortems.
- cfo_budget: CFO token economics, departmental compute quotas, and financial burn controls.
- rbac: Role-based access control, toolset isolation, and cognitive model allocations.
- channel_watcher: Event-driven cross-bot debate listener and autonomous multi-turn discourse.
- autonomous_loop: Master continuous self-managing corporate loop.
"""

from .talent_ops import TalentLedger, evaluate_bot_performance, BotPerformanceRecord
from .gatekeeper import EnterpriseGatekeeper, ReviewVerdict
from .git_merger import GitMerger
from .workspace_isolator import WorkspaceIsolator
from .cost_guard import CostGuard
from .verifier import ArtifactVerifier
from .sla_watchdog import SLAWatchdog
from .orchestrator import EnterpriseOrchestrator
from .squad_spawner import SquadManager, AutonomousSquad, SquadWorker
from .channels import EnterpriseChannels, ChannelMessage
from .rfc_system import RFCEngine, RFCDocument, RFCStatus
from .incident_manager import IncidentManager, IncidentRecord, Severity, IncidentStatus
from .cfo_budget import CFOBudgetEngine, DepartmentBudget
from .qa_ledger import QALedger, QAHealthRecord, TestSuiteMetrics, BenchmarkMetrics
from .security_ledger import SecurityLedger, SecurityAuditRecord, ThreatScanMetrics, MemoryIsolationMetrics, ThreatMonitorHealth
from .rbac import apply_enterprise_rbac, PROFILE_TOOLSET_POLICIES, PROFILE_MODEL_POLICIES
from .channel_watcher import ChannelWatcher
from .autonomous_loop import AutonomousEnterpriseLoop

__all__ = [
    "TalentLedger",
    "evaluate_bot_performance",
    "BotPerformanceRecord",
    "EnterpriseGatekeeper",
    "ReviewVerdict",
    "GitMerger",
    "WorkspaceIsolator",
    "CostGuard",
    "ArtifactVerifier",
    "SLAWatchdog",
    "EnterpriseOrchestrator",
    "SquadManager",
    "AutonomousSquad",
    "SquadWorker",
    "EnterpriseChannels",
    "ChannelMessage",
    "RFCEngine",
    "RFCDocument",
    "RFCStatus",
    "IncidentManager",
    "IncidentRecord",
    "Severity",
    "IncidentStatus",
    "CFOBudgetEngine",
    "DepartmentBudget",
    "QALedger",
    "QAHealthRecord",
    "TestSuiteMetrics",
    "BenchmarkMetrics",
    "SecurityLedger",
    "SecurityAuditRecord",
    "ThreatScanMetrics",
    "MemoryIsolationMetrics",
    "ThreatMonitorHealth",
    "apply_enterprise_rbac",
    "PROFILE_TOOLSET_POLICIES",
    "PROFILE_MODEL_POLICIES",
    "ChannelWatcher",
    "AutonomousEnterpriseLoop",
]
