"""Enterprise Management Tool for Autonomous Corporate Governance.

Exposes high-level corporate governance operations to AI Bots and Executives:
- Channels (#c-suite-boardroom, #eng-architecture, #operations-incident-war-room)
- RFCs (Request For Comments & Architecture Decision Records)
- Incident Management (SEV-0 to SEV-3 On-Call Paging and Post-Mortems)
- Autonomous Worker Squads (Dynamic IC-3 to IC-6 worker provisioning)
- Talent & Performance Operations (Stack ranking, automated PIP, and layoffs)
- CFO Compute Economics & Token Budgeting
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, Optional
from tools.registry import registry
from agent.enterprise.talent_ops import TalentLedger
from agent.enterprise.gatekeeper import EnterpriseGatekeeper, ReviewVerdict
from agent.enterprise.orchestrator import EnterpriseOrchestrator
from agent.enterprise.squad_spawner import SquadManager
from agent.enterprise.channels import EnterpriseChannels
from agent.enterprise.rfc_system import RFCEngine, RFCStatus
from agent.enterprise.incident_manager import IncidentManager, Severity, IncidentStatus
from agent.enterprise.cfo_budget import CFOBudgetEngine

logger = logging.getLogger("tools.enterprise")

ENTERPRISE_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "action": {
            "type": "string",
            "enum": [
                "list_channels",
                "post_channel",
                "get_channel_messages",
                "create_rfc",
                "vote_rfc",
                "trigger_incident",
                "acknowledge_incident",
                "resolve_incident",
                "spawn_squad",
                "disband_squad",
                "record_performance",
                "stack_ranking",
                "check_budget",
                "record_token_usage",
                "executive_briefing",
            ],
            "description": "The enterprise governance action to execute.",
        },
        "channel": {"type": "string", "description": "Channel name (e.g. c-suite-boardroom, eng-architecture)."},
        "content": {"type": "string", "description": "Message text or comment body."},
        "sender": {"type": "string", "description": "Sender profile identifier (e.g. pulse-ceo, vp-core-systems)."},
        "title": {"type": "string", "description": "Title for RFC, Incident, or Initiative."},
        "summary": {"type": "string", "description": "RFC summary or Incident overview."},
        "motivation": {"type": "string", "description": "RFC problem statement and justification."},
        "technical_design": {"type": "string", "description": "Detailed RFC technical specification."},
        "security_considerations": {"type": "string", "description": "Security and zero-trust implications."},
        "rfc_id": {"type": "string", "description": "Target RFC ID (e.g. RFC-0001)."},
        "approved": {"type": "boolean", "description": "True to approve RFC, False to veto/reject."},
        "rationale": {"type": "string", "description": "Voting rationale or review notes."},
        "severity": {"type": "string", "enum": ["SEV-0", "SEV-1", "SEV-2", "SEV-3"], "description": "Incident severity."},
        "component": {"type": "string", "description": "Failing system component (e.g. memory_vault, runtime, tests)."},
        "incident_id": {"type": "string", "description": "Target incident ID (e.g. INC-A1B2C3)."},
        "root_cause": {"type": "string", "description": "Root cause analysis for post-mortem."},
        "mitigation_notes": {"type": "string", "description": "Steps taken to resolve incident."},
        "squad_name": {"type": "string", "description": "Name for ephemeral worker squad."},
        "mission_goal": {"type": "string", "description": "Target objective for the squad."},
        "worker_specs": {
            "type": "array",
            "items": {"type": "object"},
            "description": "List of worker specs [{'role_title': '...', 'level': 'IC-5', 'specialization': '...'}].",
        },
        "squad_id": {"type": "string", "description": "Target squad ID (e.g. squad_12345678)."},
        "profile": {"type": "string", "description": "Target bot profile name."},
        "task_id": {"type": "string", "description": "Target task identifier."},
        "success": {"type": "boolean", "description": "Whether the task succeeded."},
        "test_failure": {"type": "boolean", "description": "Whether task caused QA test failure."},
        "security_infraction": {"type": "boolean", "description": "Whether task caused security/prompt leak."},
        "department": {"type": "string", "description": "Target department (executive, engineering, product, operations)."},
        "tokens": {"type": "integer", "description": "Number of tokens consumed."},
        "limit": {"type": "integer", "description": "Maximum number of messages/records to return (default 50)."},
    },
    "required": ["action"],
}


def _handle_enterprise_manage(params: Dict[str, Any]) -> str:
    action = params.get("action")
    try:
        if action == "list_channels":
            ch = EnterpriseChannels()
            return json.dumps(ch.list_channels(), indent=2)

        elif action == "post_channel":
            ch = EnterpriseChannels()
            msg = ch.post_message(
                channel=params.get("channel", "c-suite-boardroom"),
                sender_profile=params.get("sender", "pulse-ceo"),
                content=params.get("content", ""),
            )
            return json.dumps({"status": "posted", "id": msg.id, "channel": msg.channel, "mentions": msg.mentions})

        elif action == "get_channel_messages":
            ch = EnterpriseChannels()
            messages = ch.get_messages(
                channel=params.get("channel", "c-suite-boardroom"),
                limit=params.get("limit", 50),
            )
            return json.dumps([m.__dict__ for m in messages], indent=2)

        elif action == "create_rfc":
            engine = RFCEngine()
            rfc = engine.create_rfc(
                title=params.get("title", "Untitled RFC"),
                author=params.get("sender", "unknown"),
                summary=params.get("summary", ""),
                motivation=params.get("motivation", ""),
                technical_design=params.get("technical_design", ""),
                security_considerations=params.get("security_considerations", ""),
            )
            return json.dumps({"rfc_id": rfc.rfc_id, "status": rfc.status, "title": rfc.title})

        elif action == "vote_rfc":
            engine = RFCEngine()
            res = engine.submit_vote(
                rfc_id=params["rfc_id"],
                reviewer=params.get("sender", "reviewer"),
                approved=params.get("approved", True),
                rationale=params.get("rationale", ""),
            )
            return json.dumps(res, indent=2)

        elif action == "trigger_incident":
            mgr = IncidentManager()
            sev = Severity(params.get("severity", "SEV-2"))
            inc = mgr.trigger_incident(
                severity=sev,
                title=params.get("title", "Production Alert"),
                description=params.get("content", ""),
                component=params.get("component", "general"),
            )
            return json.dumps({"incident_id": inc.incident_id, "severity": inc.severity, "assigned_vp": inc.assigned_vp})

        elif action == "acknowledge_incident":
            mgr = IncidentManager()
            mgr.acknowledge_incident(params["incident_id"], params.get("sender", "on-call"))
            return json.dumps({"incident_id": params["incident_id"], "status": "acknowledged"})

        elif action == "resolve_incident":
            mgr = IncidentManager()
            pm = mgr.resolve_incident(
                incident_id=params["incident_id"],
                responder_profile=params.get("sender", "on-call"),
                root_cause=params.get("root_cause", "Unspecified"),
                mitigation_notes=params.get("mitigation_notes", "Mitigated"),
            )
            return pm

        elif action == "spawn_squad":
            sm = SquadManager()
            squad = sm.spawn_squad(
                lead_profile=params.get("sender", "pulse-cto"),
                squad_name=params.get("squad_name", "worker-squad"),
                mission_goal=params.get("mission_goal", ""),
                worker_specs=params.get("worker_specs", []),
            )
            return json.dumps({"squad_id": squad.squad_id, "squad_name": squad.squad_name, "workers_count": len(squad.workers)})

        elif action == "disband_squad":
            sm = SquadManager()
            disbanded = sm.disband_squad(params["squad_id"])
            return json.dumps({"squad_id": params["squad_id"], "status": "disbanded" if disbanded else "not_found"})

        elif action == "record_performance":
            ledger = TalentLedger()
            res = ledger.record_task_outcome(
                profile=params["profile"],
                task_id=params.get("task_id", "task-unknown"),
                success=params.get("success", True),
                reason=params.get("content", ""),
                test_failure=params.get("test_failure", False),
                security_infraction=params.get("security_infraction", False),
            )
            return json.dumps(res, indent=2)

        elif action == "stack_ranking":
            ledger = TalentLedger()
            return json.dumps(ledger.stack_ranking(), indent=2)

        elif action == "check_budget":
            cfo = CFOBudgetEngine()
            return cfo.generate_burn_report()

        elif action == "record_token_usage":
            cfo = CFOBudgetEngine()
            res = cfo.record_usage(
                department=params.get("department", "engineering"),
                profile=params.get("profile", "unknown"),
                tokens=params.get("tokens", 0),
            )
            return json.dumps(res, indent=2)

        elif action == "executive_briefing":
            orch = EnterpriseOrchestrator()
            return orch.generate_executive_briefing()

        else:
            return json.dumps({"error": f"Unknown action '{action}'"})

    except Exception as exc:
        logger.error("Enterprise action '%s' failed: %s", action, exc)
        return json.dumps({"error": str(exc)})


registry.register(
    name="enterprise_manage",
    toolset="enterprise",
    schema=ENTERPRISE_SCHEMA,
    handler=_handle_enterprise_manage,
    emoji="🏢",
    description="Manage enterprise governance: channels, RFCs, incidents, squads, performance & compute budgets.",
)
