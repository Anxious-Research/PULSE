"""Enterprise Role-Based Access Control (RBAC) & Model Specialization Engine.

Applies strict corporate governance to all 22 profiles:
1. Role-Based Tool Permissions (RBAC):
   - Executive / Governance (CFO, CHRO, Legal): Read-only, financial, talent, and compliance tools.
   - Engineering & Systems (CTO, Core Systems, Release Eng): Full coding, compiler, and system execution.
   - Quality & Security (QA, CISO, Red Team): Automated testing, threat simulation, and security scanning.
   - Product & Design (CPO, PMO, Design Systems): PRD authorship, UX design, and documentation.
2. Cognitive Model Specialization:
   - Deep Reasoning (o3 / Sonnet / Thinking models) for Strategic & Architecture leads.
   - High Throughput / Deterministic models for QA, SRE, and Release Engineering.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List
from pulse_constants import get_pulse_home
from pulse_yaml import safe_dump as yaml_dump, safe_load as yaml_load

logger = logging.getLogger("enterprise.rbac")

# Strict Role-Based Toolset Allowlist per Profile
PROFILE_TOOLSET_POLICIES: Dict[str, List[str]] = {
    # ── Executive Office ─────────────────────────────────────────────────────
    "pulse-ceo": [
        "clarify", "delegation", "enterprise", "kanban", "memory",
        "session_search", "skills", "todo", "web"
    ],
    "pulse-cto": [
        "clarify", "code_execution", "delegation", "enterprise", "file",
        "kanban", "memory", "session_search", "skills", "terminal", "todo", "web"
    ],
    "pulse-cpo": [
        "clarify", "delegation", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "todo", "web"
    ],
    "pulse-ciso": [
        "clarify", "delegation", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "terminal", "todo", "web"
    ],
    "pulse-coo": [
        "clarify", "delegation", "enterprise", "kanban", "memory",
        "session_search", "skills", "terminal", "todo", "web"
    ],
    "pulse-chro": [
        "clarify", "enterprise", "kanban", "memory", "session_search",
        "skills", "todo", "web"
    ],
    "pulse-cfo": [
        "clarify", "enterprise", "kanban", "memory", "session_search",
        "skills", "todo", "web"
    ],
    "pulse-general-counsel": [
        "clarify", "enterprise", "file", "kanban", "memory",
        "session_search", "skills", "todo", "web"
    ],

    # ── AI & Core Technology ─────────────────────────────────────────────────
    "vp-ai-research": [
        "clarify", "code_execution", "delegation", "enterprise", "file",
        "kanban", "memory", "session_search", "skills", "terminal", "todo", "web"
    ],
    "vp-core-systems": [
        "clarify", "code_execution", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "terminal", "todo"
    ],
    "vp-cognitive-memory": [
        "clarify", "code_execution", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "terminal", "todo"
    ],
    "vp-ml-platform": [
        "clarify", "code_execution", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "terminal", "todo"
    ],

    # ── Product & Ecosystem ──────────────────────────────────────────────────
    "vp-product-pmo": [
        "clarify", "enterprise", "file", "kanban", "memory",
        "session_search", "skills", "todo", "web"
    ],
    "vp-design-systems": [
        "clarify", "enterprise", "file", "kanban", "memory",
        "session_search", "skills", "todo", "vision", "web"
    ],
    "vp-developer-platform": [
        "clarify", "code_execution", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "terminal", "todo", "web"
    ],
    "vp-technical-docs": [
        "clarify", "enterprise", "file", "kanban", "memory",
        "session_search", "skills", "todo"
    ],

    # ── Operations, QA & Defense ─────────────────────────────────────────────
    "vp-qa-verification": [
        "clarify", "code_execution", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "terminal", "todo"
    ],
    "vp-infosec-redteam": [
        "clarify", "code_execution", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "terminal", "todo", "web"
    ],
    "vp-cloud-sre": [
        "clarify", "code_execution", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "terminal", "todo"
    ],
    "vp-release-engineering": [
        "clarify", "code_execution", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "terminal", "todo"
    ],
    "vp-data-telemetry": [
        "clarify", "code_execution", "enterprise", "file", "kanban",
        "memory", "session_search", "skills", "terminal", "todo"
    ],
    "vp-corporate-strategy": [
        "clarify", "enterprise", "kanban", "memory", "session_search",
        "skills", "todo", "web"
    ],
}

# Cognitive Model Allocations
PROFILE_MODEL_POLICIES: Dict[str, Dict[str, str]] = {
    # Strategic / Heavy Reasoning
    "pulse-ceo": {"reasoning_effort": "high"},
    "pulse-cto": {"reasoning_effort": "high"},
    "vp-ai-research": {"reasoning_effort": "high"},
    "vp-cognitive-memory": {"reasoning_effort": "high"},
    "vp-core-systems": {"reasoning_effort": "high"},

    # High-Throughput / Fast Execution
    "vp-qa-verification": {"reasoning_effort": "medium"},
    "vp-cloud-sre": {"reasoning_effort": "medium"},
    "vp-release-engineering": {"reasoning_effort": "low"},

    # Analytical / Governance
    "pulse-cfo": {"reasoning_effort": "medium"},
    "pulse-ciso": {"reasoning_effort": "high"},
    "pulse-general-counsel": {"reasoning_effort": "medium"},
}


def apply_enterprise_rbac() -> Dict[str, Any]:
    """Apply strict RBAC tool policies and model configs to all 22 profile configs."""
    pulse_home = get_pulse_home()
    results = {}

    for profile, toolsets in PROFILE_TOOLSET_POLICIES.items():
        prof_dir = pulse_home / "profiles" / profile
        if not prof_dir.exists():
            continue

        cfg_file = prof_dir / "config.yaml"
        cfg = {}
        if cfg_file.exists():
            try:
                cfg = yaml_load(cfg_file.read_text(encoding="utf-8")) or {}
            except Exception as exc:
                logger.warning("Could not read %s config: %s", profile, exc)

        # 1. Apply RBAC platform_toolsets.cli
        if "platform_toolsets" not in cfg:
            cfg["platform_toolsets"] = {}
        cfg["platform_toolsets"]["cli"] = toolsets

        # 2. Apply Model Reasoning Policy
        if profile in PROFILE_MODEL_POLICIES:
            policy = PROFILE_MODEL_POLICIES[profile]
            if "reasoning_effort" in policy:
                cfg["reasoning_effort"] = policy["reasoning_effort"]

        # Write sanitized config
        try:
            cfg_file.write_text(yaml_dump(cfg, sort_keys=False), encoding="utf-8")
            results[profile] = {"toolsets_count": len(toolsets), "status": "applied"}
        except Exception as exc:
            results[profile] = {"error": str(exc), "status": "failed"}

    logger.info("Applied enterprise RBAC and model specialization across %d profiles.", len(results))
    return results


if __name__ == "__main__":
    apply_enterprise_rbac()
