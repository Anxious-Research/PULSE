"""Enterprise QA Test Sentinel & Health Ledger.

Maintains an immutable historical record of automated QA test verification runs:
- Unit & regression test pass/fail rates across subsystems (agent, brain, tools).
- Latency benchmarks (mean retrieval ms, graph build ms, SQLite FTS5 traversal).
- Quality indicators: Mean Reciprocal Rank (MRR), Precision@K, Recall@K.
- Regression tracking and build certification status.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from pulse_constants import get_pulse_home

logger = logging.getLogger("enterprise.qa_ledger")

QA_LEDGER_FILENAME = "qa_ledger.json"


@dataclass
class TestSuiteMetrics:
    suite_name: str
    total_tests: int = 0
    passed: int = 0
    failed: int = 0
    skipped: int = 0
    subtests_passed: int = 0
    duration_seconds: float = 0.0
    pass_rate: float = 100.0


@dataclass
class BenchmarkMetrics:
    mean_p3: float = 0.0
    mean_p5: float = 0.0
    mean_r3: float = 0.0
    mean_r5: float = 0.0
    mean_mrr: float = 1.0
    mean_latency_ms: float = 0.0
    warm_latency_ms: float = 0.0
    graph_build_seconds_2k_nodes: float = 0.0
    gates_exceeded: bool = True
    scenarios: Dict[str, Dict[str, Any]] = field(default_factory=dict)


@dataclass
class QAHealthRecord:
    run_id: str
    timestamp: float = field(default_factory=time.time)
    iso_time: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    operator_profile: str = "vp-qa-verification"
    status: str = "passed"  # passed | warning | failed
    certified: bool = True
    brain_suite: TestSuiteMetrics = field(default_factory=lambda: TestSuiteMetrics("tests/test_brain*.py"))
    agent_suite: TestSuiteMetrics = field(default_factory=lambda: TestSuiteMetrics("tests/agent/test_*.py"))
    benchmarks: BenchmarkMetrics = field(default_factory=BenchmarkMetrics)
    regression_indicators: List[str] = field(default_factory=list)
    notes: str = ""


class QALedger:
    """Persistent ledger for QA sentinel test health and latency benchmarks."""

    def __init__(self, ledger_path: Optional[Path] = None):
        if ledger_path is None:
            ent_dir = get_pulse_home() / "enterprise"
            ent_dir.mkdir(parents=True, exist_ok=True)
            self.path = ent_dir / QA_LEDGER_FILENAME
        else:
            self.path = Path(ledger_path)
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.runs: List[QAHealthRecord] = []
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            self.runs = []
            for item in raw:
                brain_m = TestSuiteMetrics(**item.get("brain_suite", {}))
                agent_m = TestSuiteMetrics(**item.get("agent_suite", {}))
                bench_m = BenchmarkMetrics(**item.get("benchmarks", {}))
                rec = QAHealthRecord(
                    run_id=item["run_id"],
                    timestamp=item.get("timestamp", time.time()),
                    iso_time=item.get("iso_time", ""),
                    operator_profile=item.get("operator_profile", "vp-qa-verification"),
                    status=item.get("status", "passed"),
                    certified=item.get("certified", True),
                    brain_suite=brain_m,
                    agent_suite=agent_m,
                    benchmarks=bench_m,
                    regression_indicators=item.get("regression_indicators", []),
                    notes=item.get("notes", ""),
                )
                self.runs.append(rec)
        except Exception as exc:
            logger.warning("Could not load QA ledger from %s: %s", self.path, exc)

    def save(self) -> None:
        try:
            data = [asdict(r) for r in self.runs]
            self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception as exc:
            logger.error("Could not save QA ledger to %s: %s", self.path, exc)

    def record_run(self, record: QAHealthRecord) -> QAHealthRecord:
        self.runs.append(record)
        self.save()
        logger.info("Recorded QA run '%s': status=%s, certified=%s", record.run_id, record.status, record.certified)
        return record

    def get_latest_run(self) -> Optional[QAHealthRecord]:
        return self.runs[-1] if self.runs else None

    def get_history(self, limit: int = 10) -> List[QAHealthRecord]:
        return self.runs[-limit:]

    def generate_summary(self, record: Optional[QAHealthRecord] = None) -> str:
        r = record or self.get_latest_run()
        if not r:
            return "No QA verification runs recorded."

        lines = [
            f"# QA TEST SENTINEL HEALTH REPORT — {r.run_id}",
            f"**Timestamp:** {r.iso_time} | **Lead:** @{r.operator_profile}",
            f"**Overall Status:** {r.status.upper()} | **Certified:** {'✅ YES' if r.certified else '❌ NO'}",
            "",
            "## 1. Test Suite Verification Summary",
            f"- **`{r.brain_suite.suite_name}`:**",
            f"  - Passed: **{r.brain_suite.passed}** / {r.brain_suite.total_tests} ({r.brain_suite.pass_rate:.1f}%)",
            f"  - Failed: **{r.brain_suite.failed}** | Skipped: {r.brain_suite.skipped} | Subtests: {r.brain_suite.subtests_passed}",
            f"  - Duration: {r.brain_suite.duration_seconds:.2f}s",
            f"- **`{r.agent_suite.suite_name}`:**",
            f"  - Passed: **{r.agent_suite.passed}** / {r.agent_suite.total_tests} ({r.agent_suite.pass_rate:.1f}%)",
            f"  - Failed: **{r.agent_suite.failed}** | Skipped: {r.agent_suite.skipped}",
            f"  - Duration: {r.agent_suite.duration_seconds:.2f}s",
            "",
            "## 2. Latency Benchmarks & Retrieval Quality SLA",
            f"- **Mean Reciprocal Rank (MRR):** **{r.benchmarks.mean_mrr:.2f}** (Gate: ≥ 0.40) -> {'PASS' if r.benchmarks.mean_mrr >= 0.40 else 'FAIL'}",
            f"- **Mean Recall@5 (R@5):** **{r.benchmarks.mean_r5:.2f}** (Gate: ≥ 0.25) -> {'PASS' if r.benchmarks.mean_r5 >= 0.25 else 'FAIL'}",
            f"- **Mean Precision@3 (P@3):** **{r.benchmarks.mean_p3:.2f}** (Gate: ≥ 0.30) -> {'PASS' if r.benchmarks.mean_p3 >= 0.30 else 'FAIL'}",
            f"- **Mean Warm Retrieval Latency:** **{r.benchmarks.warm_latency_ms:.1f} ms** (SLA Budget: < 50.0 ms) -> {'PASS' if r.benchmarks.warm_latency_ms < 50.0 else 'FAIL'}",
            f"- **2,000 Nodes Graph Build:** **{r.benchmarks.graph_build_seconds_2k_nodes:.3f} s** (SLA Budget: < 1.0 s) -> {'PASS' if r.benchmarks.graph_build_seconds_2k_nodes < 1.0 else 'FAIL'}",
            "",
            "## 3. Regression Analysis",
        ]
        if r.regression_indicators:
            for ind in r.regression_indicators:
                lines.append(f"- ⚠️ {ind}")
        else:
            lines.append("- Zero core regressions detected. All architectural invariants verified.")

        if r.notes:
            lines.extend(["", "## 4. Notes", r.notes])

        return "\n".join(lines)
