"""Unit & Integration Tests for PULSE Autonomous Enterprise Operating System."""

import tempfile
import unittest
from pathlib import Path
from agent.enterprise.talent_ops import TalentLedger
from agent.enterprise.gatekeeper import EnterpriseGatekeeper, ReviewVerdict
from agent.enterprise.orchestrator import EnterpriseOrchestrator
from agent.enterprise.squad_spawner import SquadManager
from agent.enterprise.channels import EnterpriseChannels
from agent.enterprise.rfc_system import RFCEngine, RFCStatus
from agent.enterprise.incident_manager import IncidentManager, Severity, IncidentStatus
from agent.enterprise.cfo_budget import CFOBudgetEngine


class TestTalentLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmp.name) / "test_ledger.json"
        self.ledger = TalentLedger(self.ledger_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_new_bot_registration(self):
        rec = self.ledger.get_or_register("vp-core-systems", role="lead", department="engineering")
        self.assertEqual(rec.profile, "vp-core-systems")
        self.assertEqual(rec.status, "good_standing")
        self.assertEqual(rec.performance_score, 100.0)

    def test_pip_trigger_on_consecutive_failures(self):
        self.ledger.get_or_register("worker-bot-1")
        res1 = self.ledger.record_task_outcome("worker-bot-1", "task-1", success=False, reason="Syntax Error")
        self.assertEqual(res1["status"], "good_standing")
        self.assertEqual(res1["consecutive_failures"], 1)

        res2 = self.ledger.record_task_outcome("worker-bot-1", "task-2", success=False, reason="Test Timeout")
        self.assertEqual(res2["status"], "pip")
        self.assertLess(res2["score"], 80.0)

    def test_quarantine_on_repeated_pip_failures(self):
        self.ledger.get_or_register("worker-bot-bad")
        self.ledger.record_task_outcome("worker-bot-bad", "task-1", success=False)
        self.ledger.record_task_outcome("worker-bot-bad", "task-2", success=False)
        self.assertEqual(self.ledger.records["worker-bot-bad"].status, "pip")

        self.ledger.record_task_outcome("worker-bot-bad", "task-3", success=False)
        res_final = self.ledger.record_task_outcome("worker-bot-bad", "task-4", success=False)
        self.assertEqual(res_final["status"], "quarantined")

    def test_pip_rehabilitation(self):
        self.ledger.get_or_register("worker-bot-rehabil")
        self.ledger.record_task_outcome("worker-bot-rehabil", "t1", success=False)
        self.ledger.record_task_outcome("worker-bot-rehabil", "t2", success=False)
        self.assertEqual(self.ledger.records["worker-bot-rehabil"].status, "pip")

        self.ledger.record_task_outcome("worker-bot-rehabil", "t3", success=True)
        self.ledger.record_task_outcome("worker-bot-rehabil", "t4", success=True)
        res = self.ledger.record_task_outcome("worker-bot-rehabil", "t5", success=True)
        self.assertEqual(res["status"], "good_standing")

    def test_stack_ranking(self):
        self.ledger.record_task_outcome("top-star", "t1", success=True)
        self.ledger.record_task_outcome("top-star", "t2", success=True)
        self.ledger.record_task_outcome("top-star", "t3", success=True)

        self.ledger.record_task_outcome("slacker", "t1", success=False)
        self.ledger.record_task_outcome("slacker", "t2", success=False)

        ranking = self.ledger.stack_ranking()
        self.assertTrue(any(b["profile"] == "top-star" for b in ranking["top_performers"]))
        self.assertTrue(any(b["profile"] == "slacker" for b in ranking["at_risk_or_pip"]))


class TestEnterpriseGatekeeper(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gatekeeper = EnterpriseGatekeeper(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_gate_lifecycle_rejection_then_approval(self):
        gate = self.gatekeeper.submit_for_review(
            "task-101",
            author_profile="vp-ml-platform",
            diff_summary="Add float32 vector indexing",
            target_files=[],
        )
        self.assertFalse(gate.merged)

        # Mock QA Pass & Security Pass
        self.gatekeeper.gates["task-101"].qa_status = "passed"
        self.gatekeeper.gates["task-101"].security_status = "passed"

        result = self.gatekeeper.sign_off("task-101", "pulse-cto", ReviewVerdict.APPROVED, "Architecture solid.")
        self.assertTrue(result["can_merge"])
        self.assertTrue(self.gatekeeper.gates["task-101"].merged)


class TestSquadSpawner(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.manager = SquadManager(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_spawn_and_disband_squad(self):
        specs = [
            {"role_title": "Senior Systems Engineer", "level": "IC-5", "specialization": "Async Concurrency"},
            {"role_title": "Compiler Specialist", "level": "IC-4", "specialization": "Bytecode"},
        ]
        squad = self.manager.spawn_squad(
            lead_profile="vp-core-systems",
            squad_name="async-runtime-squad",
            mission_goal="Optimize event loop latency",
            worker_specs=specs,
        )
        self.assertEqual(len(squad.workers), 2)
        self.assertEqual(squad.status, "active")

        # Disband
        disbanded = self.manager.disband_squad(squad.squad_id)
        self.assertIsNotNone(disbanded)
        self.assertEqual(disbanded.status, "disbanded")


class TestEnterpriseChannels(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.channels = EnterpriseChannels(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_channel_posting_and_retrieval(self):
        msg = self.channels.post_message(
            channel="c-suite-boardroom",
            sender_profile="pulse-ceo",
            content="Directives issued to @pulse-cto for Q4 Roadmap.",
        )
        self.assertIn("@pulse-cto", msg.mentions)

        history = self.channels.get_messages("c-suite-boardroom", limit=10)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0].sender_profile, "pulse-ceo")


class TestRFCEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = RFCEngine(storage_dir=Path(self.tmp.name), docs_dir=Path(self.tmp.name) / "docs")

    def tearDown(self):
        self.tmp.cleanup()

    def test_rfc_creation_and_voting(self):
        rfc = self.engine.create_rfc(
            title="Native SQLite Vector Search",
            author="vp-ml-platform",
            summary="Embed SQLite float vector cosine extension.",
            motivation="Zero dependency local fast vector retrieval.",
            technical_design="Use binary blob embeddings with CosineDistance in C.",
            security_considerations="No prompt injections in embeddings.",
        )
        self.assertEqual(rfc.status, RFCStatus.UNDER_REVIEW.value)

        # Vote 1 (CTO)
        self.engine.submit_vote(rfc.rfc_id, "pulse-cto", approved=True, rationale="Solid performance.")
        # Vote 2 (CISO) -> Consensus reached
        res = self.engine.submit_vote(rfc.rfc_id, "pulse-ciso", approved=True, rationale="Zero leakage verified.")
        self.assertEqual(res["status"], RFCStatus.APPROVED.value)


class TestIncidentManager(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.mgr = IncidentManager(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_sev0_incident_lifecycle(self):
        inc = self.mgr.trigger_incident(
            severity=Severity.SEV0,
            title="Database Memory Leak",
            description="Process memory spiked to 98% in production.",
            component="infrastructure",
        )
        self.assertEqual(inc.assigned_vp, "vp-cloud-sre")
        self.assertEqual(inc.status, IncidentStatus.TRIGGERED.value)

        # Acknowledge
        self.mgr.acknowledge_incident(inc.incident_id, "vp-cloud-sre")
        self.assertEqual(self.mgr.incidents[inc.incident_id].status, IncidentStatus.ACKNOWLEDGED.value)

        # Resolve
        pm = self.mgr.resolve_incident(
            inc.incident_id,
            responder_profile="vp-cloud-sre",
            root_cause="Unclosed cursor in metrics collector.",
            mitigation_notes="Patched with context manager and restarted daemon.",
        )
        self.assertIn("Blameless Post-Mortem", pm)
        self.assertEqual(self.mgr.incidents[inc.incident_id].status, IncidentStatus.RESOLVED.value)


class TestCFOBudgetEngine(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.cfo = CFOBudgetEngine(Path(self.tmp.name))

    def tearDown(self):
        self.tmp.cleanup()

    def test_token_tracking_and_freeze(self):
        res = self.cfo.record_usage("engineering", "vp-core-systems", tokens=1_000_000)
        self.assertFalse(res["is_frozen"])
        self.assertEqual(res["used_tokens"], 1_000_000)

        # Exceed quota (25M tokens default for engineering)
        res_huge = self.cfo.record_usage("engineering", "worker-rogue", tokens=25_000_000)
        self.assertTrue(res_huge["is_frozen"])
        self.assertGreaterEqual(res_huge["utilization_pct"], 100.0)

        # CFO Extension
        extended = self.cfo.grant_budget_extension("engineering", additional_tokens=10_000_000)
        self.assertFalse(extended.is_frozen)


class TestEnterpriseOrchestrator(unittest.TestCase):
    def test_executive_briefing_generation(self):
        orch = EnterpriseOrchestrator()
        briefing = orch.generate_executive_briefing()
        self.assertIn("SOVEREIGN ENTERPRISE EXECUTIVE BRIEFING", briefing)
        self.assertIn("Ankit Singh", briefing)
        self.assertIn("pulse-ceo", briefing)


class TestEnterpriseToolHandler(unittest.TestCase):
    def test_tool_actions(self):
        from tools.enterprise_tool import _handle_enterprise_manage
        import json

        # Post and get channels
        res = _handle_enterprise_manage({
            "action": "post_channel",
            "channel": "eng-architecture",
            "sender": "vp-core-systems",
            "content": "RFC ready for review @pulse-cto",
        })
        self.assertIn("posted", res)

        msgs = _handle_enterprise_manage({
            "action": "get_channel_messages",
            "channel": "eng-architecture",
            "limit": 5,
        })
        data = json.loads(msgs)
        self.assertTrue(len(data) >= 1)

        # Executive briefing
        briefing = _handle_enterprise_manage({"action": "executive_briefing"})
        self.assertIn("SOVEREIGN ENTERPRISE EXECUTIVE BRIEFING", briefing)


if __name__ == "__main__":
    unittest.main()
