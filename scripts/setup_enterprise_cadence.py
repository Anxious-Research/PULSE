#!/usr/bin/env python3
"""Setup Autonomous Enterprise Operating Cadence.

Configures scheduled enterprise routines in pulse cron:
1. [CEO] Morning Executive Briefing -> delivered to bot-chat:pulse-ceo
2. [QA] Continuous Test Sentinel -> delivered to bot-chat:vp-qa-verification
3. [CISO] Zero-Trust Vault & Threat Audit -> delivered to bot-chat:pulse-ciso
4. [CHRO] Talent Calibration & PIP Review -> delivered to bot-chat:pulse-chro
"""

import subprocess
import logging

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("enterprise.cadence")

ROUTINES = [
    {
        "name": "enterprise-morning-briefing",
        "schedule": "0 9 * * *",  # Daily at 9:00 AM
        "deliver": "bot-chat:pulse-ceo",
        "prompt": (
            "Run your morning enterprise executive briefing: review active tasks on the enterprise-main "
            "kanban board, review talent health and stack ranking, and prepare the executive briefing "
            "for Founder Ankit Singh."
        ),
    },
    {
        "name": "qa-test-sentinel",
        "schedule": "every 2h",
        "deliver": "bot-chat:vp-qa-verification",
        "prompt": (
            "Run automated QA test verification: execute tests/test_brain*.py and tests/agent/test_*.py, "
            "measure regression indicators and latency benchmarks, and record test health in the QA ledger."
        ),
    },
    {
        "name": "ciso-security-audit",
        "schedule": "0 0 * * *",  # Nightly at midnight
        "deliver": "bot-chat:pulse-ciso",
        "prompt": (
            "Execute the nightly Zero-Trust Security Audit: scan ~/.pulse/brain/ for prompt-leakage phrases, "
            "verify memory isolation, check threat pattern monitors, and log security certification."
        ),
    },
    {
        "name": "chro-talent-calibration",
        "schedule": "0 18 * * *",  # Daily at 6:00 PM
        "deliver": "bot-chat:pulse-chro",
        "prompt": (
            "Execute the daily Talent Calibration: compute performance scores for all active bot profiles, "
            "identify any bots breaching failure thresholds, place underperforming bots on PIP, and "
            "generate the daily workforce health report."
        ),
    },
]


def setup_cadence():
    logger.info("Setting up Autonomous Enterprise Cadence in pulse cron...")
    for r in ROUTINES:
        cmd = [
            "pulse", "cron", "create",
            r["schedule"],
            r["prompt"],
            "--name", r["name"],
            "--deliver", r["deliver"],
        ]
        try:
            # Check if job already exists
            list_res = subprocess.run(["pulse", "cron", "list"], capture_output=True, text=True)
            if r["name"] in list_res.stdout:
                logger.info("Routine '%s' already exists in cron, skipping create.", r["name"])
                continue

            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            logger.info("Created enterprise routine '%s' (%s): %s", r["name"], r["schedule"], res.stdout.strip())
        except subprocess.CalledProcessError as exc:
            logger.warning("Could not create cron routine '%s': %s", r["name"], exc.stderr.strip())

    logger.info("Autonomous Enterprise Cadence successfully configured!")


if __name__ == "__main__":
    setup_cadence()
