#!/usr/bin/env python3
"""Execute the full autonomous reporting cycle across all 22 leaders."""

import json
import logging
import sys
import time
from agent.enterprise.reporting_cadence import EnterpriseReportingCadence

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

def main():
    print("======================================================================")
    print("PULSE AUTONOMOUS ENTERPRISE: FULL 22-LEADER VERIFICATION & REPORT ROLLUP")
    print("======================================================================\n")

    cadence = EnterpriseReportingCadence()
    results = cadence.run_full_company_reporting_cycle()

    print("\n======================================================================")
    print(f"REPORT CYCLE FINISHED IN {results['cycle_duration_seconds']}s")
    print(f"Verified Active: {results['verified_operational']} / {results['total_leaders']} Leaders")
    print("======================================================================\n")

    return 0

if __name__ == "__main__":
    sys.exit(main())
