"""Unit tests for agent.enterprise.security_ledger."""

import tempfile
import unittest
from pathlib import Path
from agent.enterprise.security_ledger import (
    SecurityLedger,
    SecurityAuditRecord,
    ThreatScanMetrics,
    MemoryIsolationMetrics,
    ThreatMonitorHealth,
)


class TestSecurityLedger(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger_path = Path(self.tmp.name) / "test_sec_ledger.json"
        self.ledger = SecurityLedger(self.ledger_path)

    def tearDown(self):
        self.tmp.cleanup()

    def test_record_audit_and_load(self):
        rec = SecurityAuditRecord(
            audit_id="TEST-AUDIT-001",
            auditor_profile="pulse-ciso",
            status="passed",
            certified=True,
            threat_metrics=ThreatScanMetrics(
                total_nodes_scanned=23,
                threat_patterns_checked=40,
                prompt_leak_phrases_checked=350,
                threat_findings_count=0,
                prompt_leak_findings_count=0,
            ),
            isolation_metrics=MemoryIsolationMetrics(
                brain_nodes_count=23,
                categories_verified=["user", "self", "concept", "project", "belief", "daily"],
                profiles_isolated_count=22,
                cross_profile_leakages=0,
            ),
            monitor_health=ThreatMonitorHealth(
                threat_patterns_tests_passed=33,
                threat_patterns_tests_total=33,
                leak_guard_tests_passed=15,
                leak_guard_tests_total=15,
                monitors_healthy=True,
            ),
            notes="Zero-trust baseline established.",
        )
        saved = self.ledger.record_audit(rec)
        self.assertTrue(saved.certification_hash)
        self.assertEqual(len(self.ledger.audits), 1)

        # Reload
        reloaded = SecurityLedger(self.ledger_path)
        self.assertEqual(len(reloaded.audits), 1)
        latest = reloaded.get_latest_audit()
        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest.audit_id, "TEST-AUDIT-001")
        self.assertEqual(latest.threat_metrics.total_nodes_scanned, 23)
        self.assertEqual(latest.isolation_metrics.profiles_isolated_count, 22)
        self.assertTrue(latest.certified)

    def test_summary_generation(self):
        rec = SecurityAuditRecord(
            audit_id="TEST-AUDIT-002",
            auditor_profile="pulse-ciso",
            status="passed",
            certified=True,
            threat_metrics=ThreatScanMetrics(total_nodes_scanned=23),
            isolation_metrics=MemoryIsolationMetrics(brain_nodes_count=23),
        )
        self.ledger.record_audit(rec)
        summary = self.ledger.generate_summary()
        self.assertIn("ZERO-TRUST SECURITY AUDIT & CERTIFICATION", summary)
        self.assertIn("pulse-ciso", summary)
        self.assertIn("TEST-AUDIT-002", summary)
        self.assertIn("CERTIFIED", summary)


if __name__ == "__main__":
    unittest.main()
