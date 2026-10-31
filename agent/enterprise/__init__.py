"""PULSE Autonomous Enterprise Operating System.

Provides enterprise-grade governance, accountability, and operational mechanisms:
- talent_ops: Performance tracking, stack ranking, automated PIP, and layoffs.
- gatekeeper: Multi-phase zero-defect code review pipeline (QA, CISO, CTO).
- orchestrator: Enterprise OKR cascade, Kanban dispatching, and executive briefing.
- squad_spawner: Dynamic workforce provisioning (IC-3 to IC-6 worker squads).
- channels: Cross-functional departmental messaging channels (#c-suite, #eng-architecture, etc.).
- rfc_system: Institutional RFC & Architecture Decision Record (ADR) lifecycle.
- incident_manager: Production SEV incident response, on-call paging, and post-mortems.
- cfo_budget: CFO token economics, departmental compute quotas, and financial burn controls.
"""

from .talent_ops import TalentLedger, evaluate_bot_performance, BotPerformanceRecord
from .gatekeeper import EnterpriseGatekeeper, ReviewVerdict
from .orchestrator import EnterpriseOrchestrator
from .squad_spawner import SquadManager, AutonomousSquad, SquadWorker
from .channels import EnterpriseChannels, ChannelMessage
from .rfc_system import RFCEngine, RFCDocument, RFCStatus
from .incident_manager import IncidentManager, IncidentRecord, Severity, IncidentStatus
from .cfo_budget import CFOBudgetEngine, DepartmentBudget

__all__ = [
    "TalentLedger",
    "evaluate_bot_performance",
    "BotPerformanceRecord",
    "EnterpriseGatekeeper",
    "ReviewVerdict",
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
]
