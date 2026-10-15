"""Bridge: time-triggered intentions → the existing cron scheduler (specs/brain.md §10).

A stored intention is not a delivered reminder. Until this module existed, a due
commitment could only surface as context on the *next user message* — the time trigger
had no push path. This bridge reuses the scheduler PULSE already ships instead of
adding a parallel one:

    intention (due)  →  one-shot cron job at the due time  →  cron's existing
                        authorized delivery (origin channel / home fallback)

Rules this module enforces:

* **Time triggers only.** An event/condition/cue intention has no wall-clock moment to
  fire at; it keeps surfacing through turn-context cue matching (`prospects.guidance_block`).
  Nothing is scheduled for an intention whose due time is unresolved (§10: preserve the
  unresolved state — never invent a deadline).
* **Idempotent per intention.** The cron job id is stored on the intention node
  (``extra.cron_job_id``), so re-encoding the same turn — or re-running the sync — never
  creates a second reminder. This mirrors ``capture_intention``'s ``source_event`` idempotency.
* **The reminder does not execute the task.** The job's prompt asks the agent to *deliver* the
  reminder, not to perform the action. Reminder delivery and permission to execute are
  separate decisions (§6.2).
* **Lifecycle-synced.** ``sync_intention_job`` reconciles one intention: open + future due →
  ensure a job; completed/cancelled/missing → remove the job; due changed → move the job.
  Cancel or complete therefore stops future delivery structurally, not by hope.
* **Fail-soft.** Every public function returns a report dict and never raises to its caller,
  matching the session layer's contract. A scheduler problem must never break a turn, and a
  commitment that could not be scheduled still exists in the vault and keeps surfacing in
  turn context — scheduling is an accelerator, not the source of truth.

Dependency-free at import time: ``cron.*`` is imported lazily inside each function so
``import agent.brain.prospect_scheduler`` stays cheap and cannot drag the scheduler into
a context that only wanted the data model.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Optional

from . import prospects as prospects_mod

logger = logging.getLogger("agent.brain.prospect_scheduler")

__all__ = [
    "reminder_prompt",
    "schedule_intention",
    "unschedule_intention",
    "sync_intention_job",
]

#: Marker name prefix on scheduled reminder jobs so they are recognizably brain-owned in `pulse cron list`.
JOB_NAME_PREFIX = "Reminder:"


def reminder_prompt(text: str) -> str:
    """The prompt a due reminder job runs. Delivers the reminder — never performs the task."""
    text = " ".join(str(text or "").split())
    return (
        "[Scheduled reminder — you (PULSE) recorded this commitment earlier from the user's own "
        f"words and scheduled it for now.]\nReminder: {text}\n\n"
        "Deliver this reminder to the user in one or two concise sentences. Do not perform the "
        "task itself — this is a reminder, not authorization to act. If the user indicates the "
        "commitment is already handled or no longer wanted, acknowledge that briefly."
    )


def _read_extra(vault: Any, node_id: str) -> Dict[str, Any]:
    try:
        node = vault.read_node(node_id)
        if node is None:
            return {}
        return dict(node.frontmatter.extra or {})
    except Exception:
        return {}


def _store_job_id(vault: Any, node_id: str, job_id: str) -> None:
    """Persist ``extra.cron_job_id`` on the intention node (merge semantics, no data loss)."""
    try:
        vault.write_node(node_id, vault.read_node(node_id).content or "", extra={"cron_job_id": str(job_id)})
    except Exception:
        logger.debug("could not store cron_job_id on %s", node_id, exc_info=True)


def _clear_job_id(vault: Any, node_id: str) -> None:
    try:
        vault.write_node(node_id, vault.read_node(node_id).content or "", extra={"cron_job_id": ""})
    except Exception:
        logger.debug("could not clear cron_job_id on %s", node_id, exc_info=True)


def _create_cron_job(*, prompt: str, run_at_iso: str, name: str) -> Dict[str, Any]:
    """Create the one-shot job through the scheduler's registered entry point.

    Late imports: cron is not a dependency of the brain data model. ``origin`` is the
    creating session's channel when it has one (the same capture the cron tool performs),
    so the reminder lands where the user is; otherwise cron's normal resolution applies.
    """
    from tools.cronjob_job_args import _origin_from_env
    from cron.scheduler import create_job_with_scheduler_registration

    origin = None
    try:
        origin = _origin_from_env(run_at_iso)
    except Exception:
        origin = None

    return create_job_with_scheduler_registration(
        prompt=prompt,
        schedule=run_at_iso,
        name=name[:50],
        repeat=1,
        origin=origin,
    )


def schedule_intention(vault: Any, intention_id: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Ensure a one-shot reminder job exists for a time-triggered intention. Never raises.

    Returns ``{"ok": bool, "action": "created"|"exists"|"skipped"|"failed", ...}``.
    """
    try:
        intention = prospects_mod.find_intention(vault, intention_id)
    except Exception:
        intention = None
    if intention is None:
        return {"ok": False, "action": "skipped", "reason": "no such intention"}
    if not intention.is_open:
        return {"ok": False, "action": "skipped", "reason": f"not open ({intention.status})"}
    if intention.trigger_kind != prospects_mod.TRIGGER_TIME or not intention.due:
        # Event/condition triggers have no fire time; they surface by cue. An unresolved
        # condition must never be scheduled at an invented time (§10).
        return {"ok": False, "action": "skipped", "reason": "no time trigger"}

    now_i = int(now if now is not None else time.time())
    extra = _read_extra(vault, intention_id)
    existing_id = str(extra.get("cron_job_id") or "")

    # Already scheduled? Verify the job still exists and still matches the due time.
    if existing_id:
        try:
            from cron import jobs as cron_jobs_mod

            job = cron_jobs_mod.get_job(existing_id)
            if job is not None:
                current_next = str(job.get("next_run_at") or "")
                want_iso = _due_iso(intention.due)
                if current_next == want_iso:
                    return {"ok": True, "action": "exists", "cron_job_id": existing_id}
                # Due time changed (reschedule): move the job rather than duplicate it.
                updated = cron_jobs_mod.update_job(existing_id, {"schedule": want_iso})
                if updated is not None:
                    return {"ok": True, "action": "updated", "cron_job_id": existing_id}
        except Exception:
            logger.debug("prospect schedule: probe of %s failed", existing_id, exc_info=True)

    run_at_iso = _due_iso(intention.due)
    if intention.due <= now_i:
        # create_job rejects past one-shots outside its grace window; an already-due
        # commitment needs no future job — turn context surfaces it as due/overdue.
        return {"ok": False, "action": "skipped", "reason": "due time already passed"}

    try:
        job = _create_cron_job(
            prompt=reminder_prompt(intention.text),
            run_at_iso=run_at_iso,
            name=f"Reminder: {intention.text}",
        )
    except Exception as exc:
        # Fail-soft and diagnosable: the commitment is intact in the vault and still
        # surfaces in turn context; only the push path is degraded.
        logger.warning("prospect schedule: could not create reminder job for %s: %s", intention_id, exc)
        return {"ok": False, "action": "failed", "reason": str(exc)[:200]}

    job_id = str((job or {}).get("id") or "")
    if job_id:
        _store_job_id(vault, intention_id, job_id)
    logger.info("prospect schedule: reminder job %s at %s for intention %s", job_id, run_at_iso, intention_id)
    return {"ok": True, "action": "created", "cron_job_id": job_id, "run_at": run_at_iso}


def unschedule_intention(vault: Any, intention_id: str, *, reason: str = "") -> Dict[str, Any]:
    """Remove the reminder job for an intention (completion, cancellation). Never raises."""
    extra = _read_extra(vault, intention_id)
    job_id = str(extra.get("cron_job_id") or "")
    if not job_id:
        return {"ok": True, "action": "noop", "reason": "no job recorded"}
    try:
        from cron import jobs as cron_jobs_mod

        removed = cron_jobs_mod.remove_job(job_id)
        _clear_job_id(vault, intention_id)
        logger.info("prospect schedule: removed reminder job %s (%s)", job_id, reason or "unscheduled")
        return {"ok": bool(removed), "action": "removed", "cron_job_id": job_id}
    except Exception as exc:
        logger.warning("prospect schedule: could not remove job %s: %s", job_id, exc)
        return {"ok": False, "action": "failed", "reason": str(exc)[:200]}


def sync_intention_job(vault: Any, intention_id: str, *, now: Optional[float] = None) -> Dict[str, Any]:
    """Reconcile one intention's reminder job against its current state. Never raises.

    The single entry point every lifecycle surface should call:
      * open, time-triggered, future due  → ensure the job (create/update)
      * closed (completed/cancelled/expired) or no time trigger → remove the job
    """
    try:
        intention = prospects_mod.find_intention(vault, intention_id)
    except Exception:
        intention = None
    if intention is None:
        return unschedule_intention(vault, intention_id, reason="intention gone")
    if not intention.is_open:
        return unschedule_intention(vault, intention_id, reason=f"closed ({intention.status})")
    if intention.trigger_kind != prospects_mod.TRIGGER_TIME or not intention.due:
        return unschedule_intention(vault, intention_id, reason="no time trigger")
    return schedule_intention(vault, intention_id, now=now)


def _due_iso(due: int) -> str:
    """Epoch seconds → the ISO timestamp the cron store records (aware, scheduler timezone)."""
    try:
        from pulse_time import now as _pulse_now

        tz = _pulse_now().tzinfo
    except Exception:
        tz = None
    from datetime import datetime

    dt = datetime.fromtimestamp(int(due), tz=tz)
    return dt.isoformat()
