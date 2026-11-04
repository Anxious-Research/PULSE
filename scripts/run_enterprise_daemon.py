#!/usr/bin/env python3
"""Autonomous Enterprise Corporation Background Daemon.

Runs continuously in the background when the human is not present:
- Executes the Autonomous Enterprise Loop (Kanban dispatch, Channel debates, Gatekeeper reviews).
- Self-healing watchdog with graceful signal handling and PID management.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from pathlib import Path
from pulse_constants import get_pulse_home
from agent.enterprise.autonomous_loop import AutonomousEnterpriseLoop

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("enterprise_daemon")

PID_FILE = get_pulse_home() / "enterprise" / "daemon.pid"
LOG_FILE = get_pulse_home() / "enterprise" / "daemon.log"


def is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start_daemon(interval: int = 20):
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PID_FILE.exists():
        try:
            old_pid = int(PID_FILE.read_text().strip())
            if is_running(old_pid):
                logger.info("Enterprise daemon is already running (PID %d).", old_pid)
                return
        except Exception:
            pass

    pid = os.getpid()
    PID_FILE.write_text(str(pid))
    logger.info("Starting Autonomous Enterprise Daemon (PID %d, interval=%ds)...", pid, interval)

    def handle_exit(signum, frame):
        logger.info("Received termination signal %d. Shutting down daemon...", signum)
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except Exception:
                pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, handle_exit)
    signal.signal(signal.SIGINT, handle_exit)

    loop = AutonomousEnterpriseLoop(board_slug="enterprise-main")
    try:
        loop.run_forever(interval_seconds=interval)
    finally:
        if PID_FILE.exists():
            try:
                PID_FILE.unlink()
            except Exception:
                pass


def stop_daemon():
    if not PID_FILE.exists():
        logger.info("Enterprise daemon is not running (no PID file).")
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        if is_running(pid):
            os.kill(pid, signal.SIGTERM)
            logger.info("Sent SIGTERM to enterprise daemon (PID %d).", pid)
        else:
            logger.info("Daemon process (PID %d) was already stopped.", pid)
        PID_FILE.unlink(missing_ok=True)
    except Exception as exc:
        logger.error("Failed to stop daemon: %s", exc)


def status_daemon():
    if not PID_FILE.exists():
        logger.info("Enterprise daemon status: STOPPED")
        return
    try:
        pid = int(PID_FILE.read_text().strip())
        if is_running(pid):
            logger.info("Enterprise daemon status: RUNNING (PID %d)", pid)
        else:
            logger.info("Enterprise daemon status: STOPPED (stale PID file %d)", pid)
    except Exception as exc:
        logger.error("Error reading daemon status: %s", exc)


def main():
    parser = argparse.ArgumentParser(description="Autonomous Enterprise Daemon Manager")
    parser.add_argument("action", choices=["start", "stop", "status", "run-once"], help="Daemon control action")
    parser.add_argument("--interval", type=int, default=20, help="Tick interval in seconds (default: 20s)")

    args = parser.parse_args()

    if args.action == "start":
        start_daemon(interval=args.interval)
    elif args.action == "stop":
        stop_daemon()
    elif args.action == "status":
        status_daemon()
    elif args.action == "run-once":
        loop = AutonomousEnterpriseLoop(board_slug="enterprise-main")
        res = loop.tick()
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
