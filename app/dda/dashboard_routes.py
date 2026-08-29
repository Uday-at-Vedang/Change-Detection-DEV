"""Dashboard aggregation API.

Backs the app's home dashboard (Change Detection Overview, region/village
breakdowns, location comparisons, trend, recent reports). Scoped to the
requesting user the same way /api/history and reports.js are — own runs
plus any auto-scheduled runs everyone can see — so two different users see
different dashboards, exactly like they already see different Reports lists.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import DetectionRun, User
from .auto_detect import is_auto_schedule_job
from .dda_auth import current_dda_user
from .models import DetectionJob
from .review_service import bulk_classification_counts

router = APIRouter()

UNASSIGNED = "Unassigned"
DEFAULT_ROLE = "analyst"

# Matches DEFAULT_ROLES rank order in app/dda/rbac/seed.py — keeps the role
# breakdown in a stable, meaningful order instead of alphabetical/count order.
ROLE_ORDER = ["viewer", "uploader", "analyst", "admin"]

MONTH_NAMES = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

_IST = timezone(timedelta(hours=5, minutes=30))


def _isoformat_ist(dt):
    """Same conversion as detect_service._isoformat_ist, duplicated locally
    to avoid importing detect_service (which pulls in geo_regions) just for
    date formatting."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(_IST).isoformat()


def _require_dda():
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


def _run_thumbs(run: DetectionRun) -> dict:
    return {
        "overlayUrl": f"/api/overlay/{run.overlay_path}" if run.overlay_path else None,
        "beforeThumbUrl": f"/api/overlay/{run.before_thumb_path}" if (run.before_thumb_path or "").strip() else None,
        "afterThumbUrl": f"/api/overlay/{run.after_thumb_path}" if (run.after_thumb_path or "").strip() else None,
    }


def _run_summary(row: dict) -> dict:
    run = row["run"]
    return {
        "id": run.id,
        "title": run.title,
        "zone": row["zone"],
        "village": row["village"],
        "role": row["role"],
        "method": run.method,
        "changePercentage": run.change_percentage,
        "regionsCount": run.regions_count or 0,
        "unclassified": row["unclassified"],
        "confirmed": row["confirmed"],
        "falsePositive": row["falsePositive"],
        "createdAt": _isoformat_ist(run.created_at),
        **_run_thumbs(run),
    }


def _accessible_run_filter(db: Session, user: User):
    """Same run-visibility rule as GET /api/history in app/main.py: the
    user's own runs, plus any run that came from the auto-schedule (visible
    to everyone, not just whoever happened to be logged in when it fired)."""
    auto_ids = [
        j.run_id for j in (
            db.query(DetectionJob)
            .filter(DetectionJob.run_id.isnot(None))
            .order_by(DetectionJob.id.desc())
            .limit(200)
            .all()
        )
        if is_auto_schedule_job(j)
    ]
    filt = DetectionRun.user_id == user.id
    if auto_ids:
        filt = or_(filt, DetectionRun.id.in_(auto_ids))
    return filt


def _load_dataset(db: Session, user: User) -> list[dict]:
    """One query for every accessible run + one grouped query for
    classification counts; every endpoint below aggregates from this in
    Python — mirrors the simple query style already used by
    admin_routes.admin_status(). Runs with a blank zone/village are bucketed
    under "Unassigned" so totals reconcile.
    """
    runs = (
        db.query(DetectionRun)
        .filter(_accessible_run_filter(db, user))
        .order_by(DetectionRun.created_at.asc())
        .all()
    )
    counts = bulk_classification_counts(db)
    # Same role resolution as dda_auth.get_user_role (raw User.role string,
    # falling back to "analyst") but bulk-loaded to avoid a query per run.
    roles_by_user = {uid: (role or DEFAULT_ROLE) for uid, role in db.query(User.id, User.role).all()}
    rows = []
    for run in runs:
        c = counts.get(run.id, {})
        classified = c.get("confirmed", 0) + c.get("false_positive", 0) + c.get("submitted", 0)
        unclassified = max(0, (run.regions_count or 0) - classified)
        rows.append({
            "run": run,
            "zone": (run.zone or "").strip() or UNASSIGNED,
            "village": (run.village or "").strip() or UNASSIGNED,
            "role": roles_by_user.get(run.user_id, DEFAULT_ROLE),
            "confirmed": c.get("confirmed", 0),
            "falsePositive": c.get("false_positive", 0),
            "submitted": c.get("submitted", 0),
            "unclassified": unclassified,
        })
    return rows


@router.get("/dashboard/summary")
def dashboard_summary(db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    _require_dda()
    rows = _load_dataset(db, user)

    total_detected = sum((r["run"].regions_count or 0) for r in rows)
    pct_values = [r["run"].change_percentage for r in rows if r["run"].change_percentage is not None]
    zones = {r["zone"] for r in rows}
    villages = {(r["zone"], r["village"]) for r in rows}

    return {
        "totalRuns": len(rows),
        "totalDetectedChanges": total_detected,
        "totalUnclassified": sum(r["unclassified"] for r in rows),
        "totalConfirmed": sum(r["confirmed"] for r in rows),
        "totalFalsePositive": sum(r["falsePositive"] for r in rows),
        "totalSubmitted": sum(r["submitted"] for r in rows),
        "avgChangePercentage": round(sum(pct_values) / len(pct_values), 2) if pct_values else None,
        "totalRegions": len(zones),
        "totalVillages": len(villages),
        "jobsQueued": db.query(DetectionJob).filter(DetectionJob.status == "queued", DetectionJob.created_by == user.id).count(),
        "jobsRunning": db.query(DetectionJob).filter(DetectionJob.status == "running", DetectionJob.created_by == user.id).count(),
        "latestRun": _run_summary(rows[-1]) if rows else None,
    }


@router.get("/dashboard/regions")
def dashboard_regions(db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    _require_dda()
    rows = _load_dataset(db, user)
    by_zone: dict[str, list] = defaultdict(list)
    for r in rows:
        by_zone[r["zone"]].append(r)

    out = []
    for zone, items in by_zone.items():
        pct_values = [i["run"].change_percentage for i in items if i["run"].change_percentage is not None]
        out.append({
            "zone": zone,
            "runsCount": len(items),
            "detectedChanges": sum((i["run"].regions_count or 0) for i in items),
            "unclassified": sum(i["unclassified"] for i in items),
            "confirmed": sum(i["confirmed"] for i in items),
            "falsePositive": sum(i["falsePositive"] for i in items),
            "avgChangePercentage": round(sum(pct_values) / len(pct_values), 2) if pct_values else None,
            "villagesCount": len({i["village"] for i in items}),
            "lastDetectionAt": _isoformat_ist(max(i["run"].created_at for i in items)),
        })
    out.sort(key=lambda x: x["runsCount"], reverse=True)
    return {"regions": out}


@router.get("/dashboard/roles")
def dashboard_roles(db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    """Detection activity grouped by the role of the user who ran it."""
    _require_dda()
    rows = _load_dataset(db, user)
    by_role: dict[str, list] = defaultdict(list)
    for r in rows:
        by_role[r["role"]].append(r)

    def _rank(role: str) -> int:
        return ROLE_ORDER.index(role) if role in ROLE_ORDER else len(ROLE_ORDER)

    out = []
    for role, items in by_role.items():
        pct_values = [i["run"].change_percentage for i in items if i["run"].change_percentage is not None]
        out.append({
            "role": role,
            "runsCount": len(items),
            "detectedChanges": sum((i["run"].regions_count or 0) for i in items),
            "unclassified": sum(i["unclassified"] for i in items),
            "confirmed": sum(i["confirmed"] for i in items),
            "falsePositive": sum(i["falsePositive"] for i in items),
            "avgChangePercentage": round(sum(pct_values) / len(pct_values), 2) if pct_values else None,
            "usersCount": len({i["run"].user_id for i in items}),
            "lastDetectionAt": _isoformat_ist(max(i["run"].created_at for i in items)),
        })
    out.sort(key=lambda x: (_rank(x["role"]), x["role"]))
    return {"roles": out}


@router.get("/dashboard/villages")
def dashboard_villages(
    zone: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    rows = _load_dataset(db, user)
    if zone:
        rows = [r for r in rows if r["zone"] == zone]
    by_village: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_village[(r["zone"], r["village"])].append(r)

    out = []
    for (z, v), items in by_village.items():
        pct_values = [i["run"].change_percentage for i in items if i["run"].change_percentage is not None]
        out.append({
            "zone": z,
            "village": v,
            "runsCount": len(items),
            "detectedChanges": sum((i["run"].regions_count or 0) for i in items),
            "unclassified": sum(i["unclassified"] for i in items),
            "confirmed": sum(i["confirmed"] for i in items),
            "falsePositive": sum(i["falsePositive"] for i in items),
            "avgChangePercentage": round(sum(pct_values) / len(pct_values), 2) if pct_values else None,
            "lastDetectionAt": _isoformat_ist(max(i["run"].created_at for i in items)),
        })
    out.sort(key=lambda x: x["runsCount"], reverse=True)
    return {"villages": out}


@router.get("/dashboard/trend")
def dashboard_trend(
    months: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    rows = _load_dataset(db, user)

    now = datetime.now(timezone.utc)
    keys = []
    y, m = now.year, now.month
    for _ in range(months):
        keys.append((y, m))
        m -= 1
        if m == 0:
            m, y = 12, y - 1
    keys.reverse()

    counts = {k: {"runsCount": 0, "detectedChanges": 0} for k in keys}
    for r in rows:
        dt = r["run"].created_at
        if dt is None:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        key = (dt.year, dt.month)
        if key in counts:
            counts[key]["runsCount"] += 1
            counts[key]["detectedChanges"] += (r["run"].regions_count or 0)

    buckets = [
        {
            "period": f"{y}-{m:02d}",
            "label": f"{MONTH_NAMES[m]} {y}",
            "runsCount": counts[(y, m)]["runsCount"],
            "detectedChanges": counts[(y, m)]["detectedChanges"],
        }
        for (y, m) in keys
    ]
    return {"buckets": buckets}


@router.get("/dashboard/location")
def dashboard_location(
    zone: str = Query(...),
    village: str = Query(...),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    rows = _load_dataset(db, user)
    items = [r for r in rows if r["zone"] == zone and r["village"] == village]
    items.sort(key=lambda r: r["run"].created_at)

    history = [_run_summary(i) for i in items]
    latest = history[-1] if history else None
    previous = history[-2] if len(history) > 1 else None

    comparison = None
    if latest and previous:
        comparison = {
            "changePercentageDelta": (
                round(latest["changePercentage"] - previous["changePercentage"], 2)
                if latest["changePercentage"] is not None and previous["changePercentage"] is not None
                else None
            ),
            "regionsCountDelta": latest["regionsCount"] - previous["regionsCount"],
        }

    return {
        "zone": zone,
        "village": village,
        "runsCount": len(items),
        "latest": latest,
        "previous": previous,
        "comparison": comparison,
        "history": list(reversed(history)),
    }


@router.get("/dashboard/recent-reports")
def dashboard_recent_reports(
    limit: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    rows = _load_dataset(db, user)
    rows.sort(key=lambda r: r["run"].created_at, reverse=True)
    return {"reports": [_run_summary(r) for r in rows[:limit]]}
