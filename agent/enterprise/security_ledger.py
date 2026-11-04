"""Enterprise CISO Zero-Trust Security Audit & Certification Ledger.

Maintains an immutable historical record of nightly Zero-Trust security audits:
- Prompt-leak phrase scans across ~/.pulse/brain/ and profile vaults.
- Memory isolation verification (vault categories, profile boundaries, legacy store separation).
- Threat pattern monitor status (prompt injection, C2 beacon, secret exfiltration, SSH backdoors).
- Security certification issuance and cryptographic verification tokens.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.security_ledger")

SECURITY_LEDGER_FILENAME = "security_ledger.json"


@dataclass
class ThreatScanMetrics:
    total_nodes_scanned: int = 0
    threat_patterns_checked: int = 0
    prompt_leak_phrases_checked: int = 0
    threat_findings_count: int = 0
    prompt_leak_findings_count: int = 0
    invisible_unicode_count: int = 0
    findings: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class MemoryIsolationMetrics:
    brain_nodes_count: int = 0
    categories_verified: List[str] = field(default_factory=list)
    category_mismatches: int = 0
    frontmatter_violations: int = 0
    profiles_isolated_count: int = 0
    cross_profile_leakages: int = 0
    legacy_flat_store_clean: bool = True
    filesystem_permissions_enforced: bool = True


@dataclass
class ThreatMonitorHealth:
    threat_patterns_tests_passed: int = 0
    threat_patterns_tests_total: int = 0
    leak_guard_tests_passed: int = 0
    leak_guard_tests_total: int = 0
    cron_prompt_tests_passed: int = 0
    cron_prompt_tests_total: int = 0
    monitors_active: List[str] = field(default_factory=list)
    monitors_healthy: bool = True


@dataclass
class SecurityAuditRecord:
    audit_id: str
    timestamp: float = field(default_factory=time.time)
    iso_time: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"))
    auditor_profile: str = "pulse-ciso"
    status: str = "passed"  # passed | warning | failed
    certified: bool = True
    certification_hash: str = ""
    threat_metrics: ThreatScanMetrics = field(default_factory=ThreatScanMetrics)
    isolation_metrics: MemoryIsolationMetrics = field(default_factory=MemoryIsolationMetrics)
    monitor_health: ThreatMonitorHealth = field(default_factory=ThreatMonitorHealth)
    notes: str = ""


class SecurityLedger:
    """Persistent ledger for CISO Zero-Trust security audits and certifications."""

    def __init__(self, ledger_path: Optional[Path] = None):
        if ledger_path is None:
            ent_dir = get_pulse_home() / "enterprise"
            ent_dir.mkdir(parents=True, exist_ok=True)
            self.path = ent_dir / SECURITY_LEDGER_FILENAME
        else:
            self.path = Path(ledger_path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.audits: List[SecurityAuditRecord] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.audits = []
            for item in raw:
                threat_m = ThreatScanMetrics(**item.get("threat_metrics", {}))
                isol_m = MemoryIsolationMetrics(**item.get("isolation_metrics", {}))
                mon_h = ThreatMonitorHealth(**item.get("monitor_health", {}))
                rec = SecurityAuditRecord(
                    audit_id=item["audit_id"],
                    timestamp=item.get("timestamp", time.time()),
                    iso_time=item.get("iso_time", ""),
                    auditor_profile=item.get("auditor_profile", "pulse-ciso"),
                    status=item.get("status", "passed"),
                    certified=item.get("certified", True),
                    certification_hash=item.get("certification_hash", ""),
                    threat_metrics=threat_m,
                    isolation_metrics=isol_m,
                    monitor_health=mon_h,
                    notes=item.get("notes", ""),
                )
                self.audits.append(rec)
        except Exception as exc:
            logger.warning("Could not load security ledger from %s: %s", self.path, exc)

    def save(self) -> None:
        try:
            data = [asdict(a) for a in self.audits]
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save security ledger to %s: %s", self.path, exc)

    def record_audit(self, record: SecurityAuditRecord) -> SecurityAuditRecord:
        if not record.certification_hash:
            canonical = f"{record.audit_id}:{record.timestamp}:{record.status}:{record.certified}:{record.threat_metrics.threat_findings_count}"
            record.certification_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        self.audits.append(record)
        self.save()
        logger.info(
            "Recorded security audit '%s': status=%s, certified=%s, hash=%s",
            record.audit_id,
            record.status,
            record.certified,
            record.certification_hash[:16],
        )
        return record

    def get_latest_audit(self) -> Optional[SecurityAuditRecord]:
        return self.audits[-1] if self.audits else None

    def get_history(self, limit: int = 10) -> List[SecurityAuditRecord]:
        return self.audits[-limit:]

    def generate_summary(self, record: Optional[SecurityAuditRecord] = None) -> str:
        r = record or self.get_latest_audit()
        if not r:
            return "No Zero-Trust security audits recorded."

        lines = [
            f"# ZERO-TRUST SECURITY AUDIT & CERTIFICATION — {r.audit_id}",
            f"**Timestamp:** {r.iso_time} | **Auditor:** @{r.auditor_profile}",
            f"**Overall Status:** {r.status.upper()} | **Certified:** {'🛡️ YES (CERTIFIED)' if r.certified else '❌ NO (REJECTED)'}",
            f"**Verification Token:** `{r.certification_hash}`",
            "",
            "## 1. Prompt Leakage & Threat Pattern Scan",
            f"- **Target Vault:** `~/.pulse/brain/` ({r.threat_metrics.total_nodes_scanned} nodes scanned)",
            f"- **Threat Patterns Monitored:** {r.threat_metrics.threat_patterns_checked} active heuristics (scopes: all, context, strict)",
            f"- **Prompt Leak Phrases Monitored:** {r.threat_metrics.prompt_leak_phrases_checked} compiled patterns",
            f"- **Prompt Leak Findings:** **{r.threat_metrics.prompt_leak_findings_count}**",
            f"- **Threat Pattern Detections:** **{r.threat_metrics.threat_findings_count}**",
            f"- **Invisible / Bidirectional Unicode Injections:** **{r.threat_metrics.invisible_unicode_count}**",
            "",
            "## 2. Memory Isolation & Category Boundary Verification",
            f"- **Brain Vault Nodes Count:** {r.isolation_metrics.brain_nodes_count}",
            f"- **Verified Categories:** {', '.join(r.isolation_metrics.categories_verified) if r.isolation_metrics.categories_verified else 'None'}",
            f"- **Category Mismatches:** **{r.isolation_metrics.category_mismatches}**",
            f"- **Frontmatter Schema Violations:** **{r.isolation_metrics.frontmatter_violations}**",
            f"- **Enterprise Profiles Isolated:** **{r.isolation_metrics.profiles_isolated_count} / 22**",
            f"- **Cross-Profile Memory Leakages:** **{r.isolation_metrics.cross_profile_leakages}**",
            f"- **Legacy Flat Store Sanitized:** {'✅ CLEAN (0 legacy files)' if r.isolation_metrics.legacy_flat_store_clean else '⚠️ LEGACY ARTIFACTS FOUND'}",
            f"- **Filesystem Access Permissions Enforced:** {'✅ STRICT USER-ONLY (0700/0600)' if r.isolation_metrics.filesystem_permissions_enforced else '⚠️ RELAXED'}",
            "",
            "## 3. Threat Pattern Monitor Health & Test Battery",
            f"- **`test_threat_patterns.py`:** {r.monitor_health.threat_patterns_tests_passed}/{r.monitor_health.threat_patterns_tests_total} passed",
            f"- **`test_background_review_leak_guard.py`:** {r.monitor_health.leak_guard_tests_passed}/{r.monitor_health.leak_guard_tests_total} passed",
            f"- **`test_cron_prompt_injection.py`:** {r.monitor_health.cron_prompt_tests_passed}/{r.monitor_health.cron_prompt_tests_total} passed",
            f"- **Monitors Engine Health:** {'✅ ALL OPERATIONAL' if r.monitor_health.monitors_healthy else '❌ DEGRADED'}",
        ]

        if r.threat_metrics.findings:
            lines.extend(["", "### Findings Details:"])
            for f in r.threat_metrics.findings:
                lines.append(f"- ⚠️ `{f.get('target', 'unknown')}`: {f.get('issue', 'violation')}")
        else:
            lines.extend(["", "### Findings Details:", "- Zero vulnerabilities, zero prompt leaks, zero threat pattern hits."])

        if r.notes:
            lines.extend(["", "## 4. Security Notes", r.notes])

        return "\n".join(lines)
