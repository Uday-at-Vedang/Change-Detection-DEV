"""Periodic automatic detection for identified same-area pairs.

Manual Change Detection is unchanged (user clicks Run). Automatic mode also
keeps Run-now; this module queues the same identified Before/After pairs on a
cadence (default 10 days) and saves reports without anyone opening the UI.

The FastAPI process must stay running for the schedule to fire.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy.orm import Session

from ..database import SessionLocal
from ..models import User
from .models import AutoDetectSettings, AutoPairSchedule, DetectionJob

logger = logging.getLogger(__name__)

AUTO_SOURCE = "auto_schedule"

_scheduler_lock = threading.Lock()
_tick_lock = threading.Lock()
_scheduler_started = False


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


_IST = timezone(timedelta(hours=5, minutes=30))


def parse_run_at(value: str) -> str:
    raw = (value or "02:00").strip()
    try:
        parts = raw.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
    except (TypeError, ValueError):
        hour, minute = 2, 0
    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))
    return f"{hour:02d}:{minute:02d}"


def clamp_interval_days(value: Any, default: int = 10) -> int:
    try:
        days = int(value)
    except (TypeError, ValueError):
        days = default
    return max(1, min(365, days))


def get_or_create_settings(db: Session) -> AutoDetectSettings:
    row = db.query(AutoDetectSettings).filter(AutoDetectSettings.id == 1).first()
    if row:
        return row
    row = AutoDetectSettings(
        id=1,
        running=False,
        interval_days=clamp_interval_days(os.environ.get("AUTO_DETECT_INTERVAL_DAYS", "10")),
        run_at=parse_run_at(os.environ.get("AUTO_DETECT_RUN_AT", "02:00")),
        next_run_at=None,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def scheduler_is_armed() -> bool:
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        return False
    raw = os.environ.get("AUTO_DETECT_ENABLED", "1").strip().lower()
    if raw in ("0", "false", "no", "off"):
        return False
    db = SessionLocal()
    try:
        return bool(get_or_create_settings(db).running)
    except Exception:
        return False
    finally:
        db.close()


def auto_detect_enabled() -> bool:
    """Feature exists in DDA mode unless explicitly disabled in env.

    The background *queue* only runs when the operator clicks Start
    (``scheduler_is_armed``). This flag only means the Automatic tab controls
    are available.
    """
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        return False
    raw = os.environ.get("AUTO_DETECT_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def get_auto_detect_interval_days() -> int:
    db = SessionLocal()
    try:
        return clamp_interval_days(get_or_create_settings(db).interval_days)
    except Exception:
        return clamp_interval_days(os.environ.get("AUTO_DETECT_INTERVAL_DAYS", "10"))
    finally:
        db.close()


def next_run_at_from_clock(run_at: str, now: Optional[datetime] = None) -> datetime:
    """Next future IST clock time for HH:MM. Never returns 'now' (no launch burst)."""
    ist_now = (now or datetime.now(_IST)).astimezone(_IST)
    hhmm = parse_run_at(run_at)
    hour, minute = (int(p) for p in hhmm.split(":"))
    candidate = ist_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= ist_now + timedelta(seconds=45):
        candidate += timedelta(days=1)
    return candidate.astimezone(timezone.utc)


def cancel_queued_auto_jobs(db: Session, reason: str) -> int:
    cancelled = 0
    pending = (
        db.query(DetectionJob)
        .filter(DetectionJob.status == "queued")
        .all()
    )
    for job in pending:
        if not is_auto_schedule_job(job):
            continue
        job.status = "failed"
        job.error_message = reason[:2000]
        job.completed_at = _utcnow()
        cancelled += 1
    if cancelled:
        db.commit()
        logger.info("Cancelled %d queued auto-detect job(s)", cancelled)
    return cancelled


def pair_key_for(before_path: str, after_path: str) -> str:
    a = (before_path or "").replace("\\", "/").strip().lower()
    b = (after_path or "").replace("\\", "/").strip().lower()
    return hashlib.sha256(f"{a}|{b}".encode("utf-8")).hexdigest()


def job_source(job: DetectionJob) -> str:
    try:
        return str(json.loads(job.params_json or "{}").get("source") or "")
    except json.JSONDecodeError:
        return ""


def is_auto_schedule_job(job: Optional[DetectionJob]) -> bool:
    return bool(job) and job_source(job) == AUTO_SOURCE


def is_auto_schedule_run(db: Session, run_id: int) -> bool:
    if not run_id:
        return False
    job = (
        db.query(DetectionJob)
        .filter(DetectionJob.run_id == run_id)
        .order_by(DetectionJob.id.desc())
        .first()
    )
    return is_auto_schedule_job(job)


def user_can_access_run(db: Session, run_id: int, user_id: int) -> bool:
    from ..models import DetectionRun
    run = db.query(DetectionRun).filter(DetectionRun.id == run_id).first()
    if not run:
        return False
    if run.user_id == user_id:
        return True
    return is_auto_schedule_run(db, run_id)


def _norm_path(path: str) -> str:
    return (path or "").replace("\\", "/").strip()


def _paths_match(job: DetectionJob, before: str, after: str) -> bool:
    try:
        params = json.loads(job.params_json or "{}")
    except json.JSONDecodeError:
        return False
    return (
        _norm_path(params.get("base_path", "")) == _norm_path(before)
        and _norm_path(params.get("comparison_path", "")) == _norm_path(after)
    )


def _schedule_owner_id(db: Session) -> int:
    admin = (
        db.query(User)
        .filter(User.role == "admin")
        .order_by(User.id.asc())
        .first()
    )
    if admin:
        return admin.id
    user = (
        db.query(User)
        .filter(User.email.notlike("__guest__%"))
        .order_by(User.id.asc())
        .first()
    )
    if user:
        return user.id
    from ..auth import get_or_create_guest_user
    return get_or_create_guest_user(db).id


def _load_identified_pairs(db: Session) -> list[dict[str, Any]]:
    from .tree.area_groups import build_area_groups
    from .tree.image_service import list_all_images

    payload = build_area_groups(list_all_images(db))
    pairs = []
    for group in payload.get("groups") or []:
        before = group.get("beforePath") or (group.get("suggestedBefore") or {}).get("path")
        after = group.get("afterPath") or (group.get("suggestedAfter") or {}).get("path")
        if not before or not after or _norm_path(before) == _norm_path(after):
            continue
        pairs.append({
            "group_id": group.get("id") or "",
            "label": group.get("label") or Path(before).name,
            "before_path": _norm_path(before),
            "after_path": _norm_path(after),
            "before_date": group.get("beforeDate"),
            "after_date": group.get("afterDate"),
        })
    return pairs


def _sync_schedules(db: Session, pairs: list[dict[str, Any]]) -> None:
    interval = get_auto_detect_interval_days()
    live_keys = set()
    now = _utcnow()
    for pair in pairs:
        key = pair_key_for(pair["before_path"], pair["after_path"])
        live_keys.add(key)
        row = db.query(AutoPairSchedule).filter(AutoPairSchedule.pair_key == key).first()
        if row:
            row.group_label = pair["label"]
            row.before_path = pair["before_path"]
            row.after_path = pair["after_path"]
            row.interval_days = interval
            row.enabled = True
            row.updated_at = now
        else:
            db.add(AutoPairSchedule(
                pair_key=key,
                group_label=pair["label"],
                before_path=pair["before_path"],
                after_path=pair["after_path"],
                interval_days=interval,
                enabled=True,
            ))
    stale = (
        db.query(AutoPairSchedule)
        .filter(AutoPairSchedule.enabled.is_(True))
        .all()
    )
    for row in stale:
        if row.pair_key not in live_keys:
            row.enabled = False
            row.updated_at = now
    db.commit()


def _pending_job_for_paths(db: Session, before: str, after: str) -> Optional[DetectionJob]:
    pending = (
        db.query(DetectionJob)
        .filter(DetectionJob.status.in_(("queued", "running")))
        .order_by(DetectionJob.id.desc())
        .limit(80)
        .all()
    )
    for job in pending:
        if _paths_match(job, before, after):
            return job
    return None


def _latest_success_for_paths(db: Session, before: str, after: str) -> Optional[datetime]:
    done = (
        db.query(DetectionJob)
        .filter(DetectionJob.status == "completed", DetectionJob.completed_at.isnot(None))
        .order_by(DetectionJob.completed_at.desc())
        .limit(80)
        .all()
    )
    for job in done:
        if _paths_match(job, before, after):
            return _aware(job.completed_at)
    return None


def _is_due(row: AutoPairSchedule, last_success: Optional[datetime], now: datetime) -> bool:
    interval = timedelta(days=max(1, int(row.interval_days or get_auto_detect_interval_days())))
    last = _aware(row.last_completed_at) or last_success
    if last and now < last + interval:
        return False
    last_enq = _aware(row.last_enqueued_at)
    if row.last_error and last_enq and now < last_enq + timedelta(hours=12):
        return False
    if last_enq and last and now < last_enq + timedelta(minutes=30):
        return False
    return True


def _enqueue_pair(db: Session, row: AutoPairSchedule) -> Optional[DetectionJob]:
    from .job_runner import create_local_folder_job
    from .local_routes import safe_resolve

    try:
        safe_resolve(row.before_path)
        safe_resolve(row.after_path)
    except Exception as exc:
        row.last_error = f"Library path missing: {exc}"[:2000]
        db.commit()
        logger.warning("Auto-detect skip %s: %s", row.group_label, exc)
        return None

    title = f"[Auto] {row.group_label}: {Path(row.before_path).name} vs {Path(row.after_path).name}"
    job = create_local_folder_job(
        db,
        base_path=row.before_path,
        comparison_path=row.after_path,
        title=title,
        created_by=_schedule_owner_id(db),
        extra_params={
            "source": AUTO_SOURCE,
            "pair_key": row.pair_key,
            "group_label": row.group_label,
        },
    )
    row.last_enqueued_at = _utcnow()
    row.last_job_id = job.id
    row.last_error = ""
    db.commit()
    logger.info(
        "Auto-detect queued job %d for %s (%s → %s)",
        job.id, row.group_label, Path(row.before_path).name, Path(row.after_path).name,
    )
    return job


def tick_auto_detect() -> int:
    """Sync identified pairs and enqueue those that are due. Returns jobs queued."""
    if not scheduler_is_armed():
        return 0
    if not _tick_lock.acquire(blocking=False):
        return 0
    queued = 0
    db = SessionLocal()
    try:
        pairs = _load_identified_pairs(db)
        _sync_schedules(db, pairs)
        now = _utcnow()
        rows = (
            db.query(AutoPairSchedule)
            .filter(AutoPairSchedule.enabled.is_(True))
            .order_by(AutoPairSchedule.id.asc())
            .all()
        )
        for row in rows:
            if _pending_job_for_paths(db, row.before_path, row.after_path):
                continue
            last_success = _latest_success_for_paths(db, row.before_path, row.after_path)
            if last_success and (
                row.last_completed_at is None or _aware(row.last_completed_at) < last_success
            ):
                row.last_completed_at = last_success
                db.commit()
            if not _is_due(row, last_success, now):
                continue
            if _enqueue_pair(db, row):
                queued += 1
        if queued:
            logger.info("Auto-detect tick queued %d pair(s)", queued)
    except Exception:
        logger.exception("Auto-detect tick failed")
        db.rollback()
        queued = 0
    finally:
        db.close()
        _tick_lock.release()
    if queued:
        from .job_runner import start_next_queued_job
        start_next_queued_job()
    return queued


def mark_schedule_from_job(job_id: int, *, status: str, run_id: Optional[int], error: str = "") -> None:
    db = SessionLocal()
    try:
        job = db.query(DetectionJob).filter(DetectionJob.id == job_id).first()
        if not is_auto_schedule_job(job):
            # Manual success for the same paths still postpones the next auto run.
            if status == "completed" and job:
                try:
                    params = json.loads(job.params_json or "{}")
                except json.JSONDecodeError:
                    return
                key = pair_key_for(params.get("base_path", ""), params.get("comparison_path", ""))
                row = db.query(AutoPairSchedule).filter(AutoPairSchedule.pair_key == key).first()
                if row:
                    row.last_completed_at = _utcnow()
                    row.last_job_id = job_id
                    if run_id:
                        row.last_run_id = run_id
                    row.last_error = ""
                    db.commit()
            return
        try:
            params = json.loads(job.params_json or "{}")
        except json.JSONDecodeError:
            params = {}
        key = params.get("pair_key") or pair_key_for(
            params.get("base_path", ""), params.get("comparison_path", ""))
        row = db.query(AutoPairSchedule).filter(AutoPairSchedule.pair_key == key).first()
        if not row:
            return
        row.last_job_id = job_id
        if status == "completed":
            row.last_completed_at = _utcnow()
            row.last_run_id = run_id
            row.last_error = ""
        else:
            row.last_error = (error or "")[:2000]
        db.commit()
    except Exception:
        logger.exception("Could not update auto-detect schedule for job %s", job_id)
        db.rollback()
    finally:
        db.close()


def refresh_schedule_rows() -> None:
    """Create/update schedule rows from identified pairs without enqueueing jobs."""
    db = SessionLocal()
    try:
        pairs = _load_identified_pairs(db)
        _sync_schedules(db, pairs)
    except Exception:
        logger.exception("Auto-detect schedule sync failed")
        db.rollback()
    finally:
        db.close()


def schedule_status_payload(db: Session) -> dict:
    settings = get_or_create_settings(db)
    interval = clamp_interval_days(settings.interval_days)
    feature_on = auto_detect_enabled()
    armed = bool(settings.running) and feature_on
    next_run = _aware(settings.next_run_at)
    rows = db.query(AutoPairSchedule).order_by(AutoPairSchedule.group_label.asc()).all()
    out = []
    for row in rows:
        last = _aware(row.last_completed_at)
        job = db.query(DetectionJob).filter(DetectionJob.id == row.last_job_id).first() if row.last_job_id else None
        out.append({
            "pairKey": row.pair_key,
            "label": row.group_label,
            "beforePath": row.before_path,
            "afterPath": row.after_path,
            "enabled": bool(row.enabled) and armed,
            "intervalDays": interval,
            "lastCompletedAt": last.isoformat() if last else None,
            "nextDueAt": next_run.isoformat() if next_run and armed else None,
            "dueNow": False,
            "lastJobId": row.last_job_id,
            "lastRunId": row.last_run_id,
            "lastJobStatus": job.status if job else None,
            "lastError": row.last_error or "",
        })
    return {
        "enabled": feature_on,
        "running": armed,
        "intervalDays": interval,
        "runAt": parse_run_at(settings.run_at),
        "nextRunAt": next_run.isoformat() if next_run and armed else None,
        "pairs": out,
    }


def start_schedule(*, interval_days: int, run_at: str) -> dict:
    if not auto_detect_enabled():
        raise ValueError("Automatic detection is disabled on the server.")
    db = SessionLocal()
    try:
        refresh_schedule_rows()
        settings = get_or_create_settings(db)
        settings.interval_days = clamp_interval_days(interval_days)
        settings.run_at = parse_run_at(run_at)
        settings.running = True
        settings.next_run_at = next_run_at_from_clock(settings.run_at)
        settings.updated_at = _utcnow()
        db.commit()
        payload = schedule_status_payload(db)
        logger.info(
            "Auto-detect started: interval=%d day(s) at %s IST; first queue %s (not now)",
            settings.interval_days, settings.run_at, settings.next_run_at,
        )
        return payload
    finally:
        db.close()


def stop_schedule() -> dict:
    db = SessionLocal()
    try:
        settings = get_or_create_settings(db)
        settings.running = False
        settings.next_run_at = None
        settings.updated_at = _utcnow()
        db.commit()
        cancel_queued_auto_jobs(
            db,
            "Cancelled because automatic schedule was stopped.",
        )
        logger.info("Auto-detect stopped; queued auto jobs cancelled")
        return schedule_status_payload(db)
    finally:
        db.close()


def _maybe_fire_scheduled_tick() -> None:
    if not scheduler_is_armed():
        return
    db = SessionLocal()
    try:
        settings = get_or_create_settings(db)
        due_at = _aware(settings.next_run_at)
        if not due_at or _utcnow() < due_at:
            return
        slot = due_at
        settings.next_run_at = slot + timedelta(days=clamp_interval_days(settings.interval_days))
        db.commit()
    except Exception:
        logger.exception("Auto-detect schedule clock failed")
        db.rollback()
        return
    finally:
        db.close()
    tick_auto_detect()


def _scheduler_loop() -> None:
    while True:
        threading.Event().wait(20)
        try:
            _maybe_fire_scheduled_tick()
        except Exception:
            logger.exception("Auto-detect waiter failed")


def reset_schedule_on_process_start() -> None:
    """Disarm the queue and cancel leftover auto jobs. Safe to call twice."""
    db = SessionLocal()
    try:
        settings = get_or_create_settings(db)
        settings.running = False
        settings.next_run_at = None
        db.commit()
        cancel_queued_auto_jobs(
            db,
            "Cancelled on server start. Automatic detection does not run until you click Start.",
        )
    except Exception:
        logger.exception("Could not reset auto-detect on launch")
        db.rollback()
    finally:
        db.close()


def start_auto_detect_scheduler() -> None:
    """Start the idle waiter. Never enqueues jobs on launch."""
    global _scheduler_started
    reset_schedule_on_process_start()

    if not auto_detect_enabled():
        logger.info("Automatic pair detection feature disabled")
        return
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
    logger.info(
        "Automatic detection is idle. Set interval/time on the Automatic tab and click Start."
    )
    threading.Thread(target=_scheduler_loop, daemon=True, name="dda-auto-detect").start()
