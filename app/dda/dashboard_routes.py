"""Dashboard aggregation API.

Backs the app's home dashboard (Change Detection Overview, region/village
breakdowns, location comparisons, trend, recent reports). Scoped to the
requesting user the same way /api/history and reports.js are — own runs
plus any auto-scheduled runs everyone can see — so two different users see
different dashboards, exactly like they already see different Reports lists.
"""
from __future__ import annotations

import json
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

# Fixed taxonomy every region is already classified into — see
# app/dda/change_type_map.py::map_to_dda_change_type. Kept here (not
# imported) purely to pin a stable display order across every chart/table
# that breaks a count down by change type.
CHANGE_TYPE_ORDER = ["New Construction", "Demolition", "Extension", "Vegetation Change", "Other"]

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


def _confirmed_rate(confirmed: int, false_positive: int) -> Optional[float]:
    """'Accuracy' for a detection: of the regions a human has actually
    adjudicated as real-or-not, what share were real? None (not 0%) when
    nothing's been reviewed yet, so the UI can show "not yet reviewed"
    instead of a misleading 0%."""
    denom = confirmed + false_positive
    if denom == 0:
        return None
    return round(100 * confirmed / denom, 1)


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
        "submitted": row["submitted"],
        "confirmedRate": _confirmed_rate(row["confirmed"], row["falsePositive"]),
        "changeTypes": row["changeTypes"],
        "createdAt": _isoformat_ist(run.created_at),
        **_run_thumbs(run),
    }


def _image_counts_by_location(db: Session) -> tuple[dict[str, int], dict[tuple[str, str], int]]:
    """Image Library counts grouped by zone name and by (zone, village) name
    — joined through the same DdaZone/DdaVillage masters the dashboard's
    zone/village strings already come from, so the names line up exactly
    with what dashboard_regions()/dashboard_villages() group by."""
    from sqlalchemy import func as sa_func

    from .models import DdaVillage, DdaZone
    from .tree.models import ImageLibrary

    by_zone: dict[str, int] = defaultdict(int)
    by_village: dict[tuple[str, str], int] = defaultdict(int)

    zone_rows = (
        db.query(DdaZone.name, sa_func.count(ImageLibrary.id))
        .join(ImageLibrary, ImageLibrary.zone_id == DdaZone.id)
        .group_by(DdaZone.name)
        .all()
    )
    for name, cnt in zone_rows:
        by_zone[name] = cnt

    village_rows = (
        db.query(DdaZone.name, DdaVillage.name, sa_func.count(ImageLibrary.id))
        .join(DdaVillage, DdaVillage.zone_id == DdaZone.id)
        .join(ImageLibrary, ImageLibrary.village_id == DdaVillage.id)
        .group_by(DdaZone.name, DdaVillage.name)
        .all()
    )
    for zone_name, village_name, cnt in village_rows:
        by_village[(zone_name, village_name)] = cnt

    return by_zone, by_village


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


def _parse_date_bound(value: Optional[str], *, end_of_day: bool = False) -> Optional[datetime]:
    """Parse a 'YYYY-MM-DD' filter bound (as sent by an <input type=date>) as
    an IST calendar day — every timestamp in this app is shown in IST, so a
    user picking "7 Sep" means the whole IST day, not UTC midnight — then
    convert to UTC since DetectionRun.created_at is stored in UTC."""
    if not value or not value.strip():
        return None
    try:
        d = datetime.strptime(value.strip(), "%Y-%m-%d")
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date '{value}', expected YYYY-MM-DD")
    if end_of_day:
        d = d + timedelta(days=1, microseconds=-1)
    return d.replace(tzinfo=_IST).astimezone(timezone.utc)


def _live_locations_for(db: Session, run_ids: list[int]) -> dict[int, tuple[str, str]]:
    """Zone/village for runs that still have a blank one, re-derived on every
    call from the run's original before/after image paths (kept on its
    DetectionJob) and those images' *current* zone/village. This is what
    makes assigning a location to an image in the Image Library immediately
    reflected on the dashboard for every past run that used it — no separate
    backfill step, no stale copy to re-sync."""
    if not run_ids:
        return {}
    from .tree.image_service import resolve_run_location

    jobs = db.query(DetectionJob).filter(DetectionJob.run_id.in_(run_ids)).all()
    out: dict[int, tuple[str, str]] = {}
    for job in jobs:
        try:
            params = json.loads(job.params_json or "{}")
        except json.JSONDecodeError:
            continue
        base_path = params.get("base_path", "")
        comparison_path = params.get("comparison_path", "")
        if not base_path and not comparison_path:
            continue
        out[job.run_id] = resolve_run_location(db, base_path, comparison_path)
    return out


def _run_change_type_counts(run: DetectionRun) -> dict[str, int]:
    """How many of this run's regions fall into each fixed change-type
    category (New Construction/Demolition/Extension/Vegetation Change/
    Other) — already computed per region at detection time (see
    change_type_map.py) and stored in regions_json; this just tallies it."""
    try:
        regions = json.loads(run.regions_json or "[]")
    except json.JSONDecodeError:
        return {}
    counts: dict[str, int] = defaultdict(int)
    for region in regions:
        if region.get("suppressed"):
            continue
        ctype = region.get("ddaChangeType") or "Other"
        counts[ctype] += 1
    return dict(counts)


def _merge_change_type_counts(items: list[dict]) -> dict[str, int]:
    """Sum per-run changeTypes dicts into one, ordered per CHANGE_TYPE_ORDER
    with any unrecognized label appended at the end rather than dropped."""
    total: dict[str, int] = defaultdict(int)
    for i in items:
        for ctype, cnt in i["changeTypes"].items():
            total[ctype] += cnt
    ordered = {k: total[k] for k in CHANGE_TYPE_ORDER if k in total}
    ordered.update({k: v for k, v in total.items() if k not in ordered})
    return ordered


def _load_dataset(
    db: Session,
    user: User,
    *,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
) -> list[dict]:
    """One query for every accessible run + one grouped query for
    classification counts; every endpoint below aggregates from this in
    Python — mirrors the simple query style already used by
    admin_routes.admin_status(). Runs with a blank zone/village are bucketed
    under "Unassigned" so totals reconcile, after first trying to live-derive
    one from the source images (see _live_locations_for). ``date_from``/
    ``date_to`` are optional 'YYYY-MM-DD' bounds on created_at (inclusive on
    both ends).
    """
    from_dt = _parse_date_bound(date_from)
    to_dt = _parse_date_bound(date_to, end_of_day=True)

    query = db.query(DetectionRun).filter(_accessible_run_filter(db, user))
    if from_dt:
        query = query.filter(DetectionRun.created_at >= from_dt)
    if to_dt:
        query = query.filter(DetectionRun.created_at <= to_dt)
    runs = query.order_by(DetectionRun.created_at.asc()).all()
    counts = bulk_classification_counts(db)
    # Same role resolution as dda_auth.get_user_role (raw User.role string,
    # falling back to "analyst") but bulk-loaded to avoid a query per run.
    roles_by_user = {uid: (role or DEFAULT_ROLE) for uid, role in db.query(User.id, User.role).all()}
    blank_ids = [r.id for r in runs if not (r.zone or "").strip() or not (r.village or "").strip()]
    live = _live_locations_for(db, blank_ids)
    rows = []
    for run in runs:
        zone = (run.zone or "").strip()
        village = (run.village or "").strip()
        if not zone or not village:
            auto_zone, auto_village = live.get(run.id, ("", ""))
            zone = zone or auto_zone
            village = village or auto_village
        c = counts.get(run.id, {})
        classified = c.get("confirmed", 0) + c.get("false_positive", 0) + c.get("submitted", 0)
        unclassified = max(0, (run.regions_count or 0) - classified)
        rows.append({
            "run": run,
            "zone": zone or UNASSIGNED,
            "village": village or UNASSIGNED,
            "role": roles_by_user.get(run.user_id, DEFAULT_ROLE),
            "confirmed": c.get("confirmed", 0),
            "falsePositive": c.get("false_positive", 0),
            "submitted": c.get("submitted", 0),
            "unclassified": unclassified,
            "changeTypes": _run_change_type_counts(run),
        })
    return rows


def _summary_from_rows(rows: list[dict], db: Session, user: User) -> dict:
    total_detected = sum((r["run"].regions_count or 0) for r in rows)
    pct_values = [r["run"].change_percentage for r in rows if r["run"].change_percentage is not None]
    zones = {r["zone"] for r in rows}
    villages = {(r["zone"], r["village"]) for r in rows}

    from .models import DdaVillage, DdaZone
    zones_total = db.query(DdaZone).count()
    villages_total = db.query(DdaVillage).count()

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
        "zonesTotal": zones_total,
        "villagesTotal": villages_total,
        "changeTypes": _merge_change_type_counts(rows),
        "jobsQueued": db.query(DetectionJob).filter(DetectionJob.status == "queued", DetectionJob.created_by == user.id).count(),
        "jobsRunning": db.query(DetectionJob).filter(DetectionJob.status == "running", DetectionJob.created_by == user.id).count(),
        "latestRun": _run_summary(rows[-1]) if rows else None,
    }


def _regions_from_rows(rows: list[dict], db: Session) -> list[dict]:
    by_zone: dict[str, list] = defaultdict(list)
    for r in rows:
        by_zone[r["zone"]].append(r)
    image_counts_by_zone, _ = _image_counts_by_location(db)

    out = []
    for zone, items in by_zone.items():
        pct_values = [i["run"].change_percentage for i in items if i["run"].change_percentage is not None]
        confirmed = sum(i["confirmed"] for i in items)
        false_positive = sum(i["falsePositive"] for i in items)
        out.append({
            "zone": zone,
            "runsCount": len(items),
            "detectedChanges": sum((i["run"].regions_count or 0) for i in items),
            "unclassified": sum(i["unclassified"] for i in items),
            "confirmed": confirmed,
            "falsePositive": false_positive,
            "submitted": sum(i["submitted"] for i in items),
            "confirmedRate": _confirmed_rate(confirmed, false_positive),
            "avgChangePercentage": round(sum(pct_values) / len(pct_values), 2) if pct_values else None,
            "villagesCount": len({i["village"] for i in items}),
            "imageCount": image_counts_by_zone.get(zone, 0),
            "changeTypes": _merge_change_type_counts(items),
            "lastDetectionAt": _isoformat_ist(max(i["run"].created_at for i in items)),
        })
    out.sort(key=lambda x: x["runsCount"], reverse=True)
    return out


def _roles_from_rows(rows: list[dict]) -> list[dict]:
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
    return out


def _villages_from_rows(rows: list[dict], db: Session, zone: Optional[str] = None) -> list[dict]:
    if zone:
        rows = [r for r in rows if r["zone"] == zone]
    by_village: dict[tuple, list] = defaultdict(list)
    for r in rows:
        by_village[(r["zone"], r["village"])].append(r)
    _, image_counts_by_village = _image_counts_by_location(db)

    out = []
    for (z, v), items in by_village.items():
        pct_values = [i["run"].change_percentage for i in items if i["run"].change_percentage is not None]
        confirmed = sum(i["confirmed"] for i in items)
        false_positive = sum(i["falsePositive"] for i in items)
        out.append({
            "zone": z,
            "village": v,
            "runsCount": len(items),
            "detectedChanges": sum((i["run"].regions_count or 0) for i in items),
            "unclassified": sum(i["unclassified"] for i in items),
            "confirmed": confirmed,
            "falsePositive": false_positive,
            "submitted": sum(i["submitted"] for i in items),
            "confirmedRate": _confirmed_rate(confirmed, false_positive),
            "avgChangePercentage": round(sum(pct_values) / len(pct_values), 2) if pct_values else None,
            "imageCount": image_counts_by_village.get((z, v), 0),
            "changeTypes": _merge_change_type_counts(items),
            "lastDetectionAt": _isoformat_ist(max(i["run"].created_at for i in items)),
        })
    out.sort(key=lambda x: x["runsCount"], reverse=True)
    return out


def _trend_from_rows(rows: list[dict], months: int = 6) -> list[dict]:
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

    return [
        {
            "period": f"{y}-{m:02d}",
            "label": f"{MONTH_NAMES[m]} {y}",
            "runsCount": counts[(y, m)]["runsCount"],
            "detectedChanges": counts[(y, m)]["detectedChanges"],
        }
        for (y, m) in keys
    ]


@router.get("/dashboard/bootstrap")
def dashboard_bootstrap(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    months: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Single payload for home dashboard — one `_load_dataset` pass."""
    _require_dda()
    rows = _load_dataset(db, user, date_from=date_from, date_to=date_to)
    return {
        "summary": _summary_from_rows(rows, db, user),
        "roles": _roles_from_rows(rows),
        "regions": _regions_from_rows(rows, db),
        "villages": _villages_from_rows(rows, db),
        "trend": {"buckets": _trend_from_rows(rows, months=months)},
    }


@router.get("/dashboard/summary")
def dashboard_summary(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    rows = _load_dataset(db, user, date_from=date_from, date_to=date_to)
    return _summary_from_rows(rows, db, user)


@router.get("/dashboard/regions")
def dashboard_regions(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    rows = _load_dataset(db, user, date_from=date_from, date_to=date_to)
    return {"regions": _regions_from_rows(rows, db)}


@router.get("/dashboard/roles")
def dashboard_roles(
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    """Detection activity grouped by the role of the user who ran it."""
    _require_dda()
    rows = _load_dataset(db, user, date_from=date_from, date_to=date_to)
    return {"roles": _roles_from_rows(rows)}


@router.get("/dashboard/villages")
def dashboard_villages(
    zone: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    rows = _load_dataset(db, user, date_from=date_from, date_to=date_to)
    return {"villages": _villages_from_rows(rows, db, zone=zone)}


@router.get("/dashboard/trend")
def dashboard_trend(
    months: int = Query(6, ge=1, le=24),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    rows = _load_dataset(db, user)
    return {"buckets": _trend_from_rows(rows, months=months)}


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
    summary = None
    if latest and previous:
        change_delta = (
            round(latest["changePercentage"] - previous["changePercentage"], 2)
            if latest["changePercentage"] is not None and previous["changePercentage"] is not None
            else None
        )
        regions_delta = latest["regionsCount"] - previous["regionsCount"]
        rate_delta = (
            round(latest["confirmedRate"] - previous["confirmedRate"], 1)
            if latest["confirmedRate"] is not None and previous["confirmedRate"] is not None
            else None
        )
        comparison = {
            "changePercentageDelta": change_delta,
            "regionsCountDelta": regions_delta,
            "confirmedDelta": latest["confirmed"] - previous["confirmed"],
            "falsePositiveDelta": latest["falsePositive"] - previous["falsePositive"],
            "unclassifiedDelta": latest["unclassified"] - previous["unclassified"],
            "confirmedRateDelta": rate_delta,
        }
        summary = _build_comparison_summary(latest, previous, comparison)

    return {
        "zone": zone,
        "village": village,
        "runsCount": len(items),
        "latest": latest,
        "previous": previous,
        "comparison": comparison,
        "summary": summary,
        "history": list(reversed(history)),
    }


def _build_comparison_summary(latest: dict, previous: dict, comparison: dict) -> str:
    """Plain-English recap of how the latest run compares to the previous
    one at this location — change area, region count, and review accuracy
    (confirmed rate) all in one sentence set, so the dashboard doesn't make
    someone read four numbers to know if things got better or worse."""
    parts = []

    cd = comparison["changePercentageDelta"]
    if cd is not None:
        if abs(cd) < 0.01:
            parts.append("Change area is essentially unchanged from the previous run.")
        else:
            direction = "up" if cd > 0 else "down"
            parts.append(
                f"Change area is {direction} {abs(cd):.2f} percentage points "
                f"({previous['changePercentage']:.2f}% → {latest['changePercentage']:.2f}%)."
            )

    rd = comparison["regionsCountDelta"]
    if rd != 0:
        parts.append(f"{abs(rd)} {'more' if rd > 0 else 'fewer'} region(s) detected than last time.")
    else:
        parts.append("Same number of regions detected as last time.")

    rate_delta = comparison["confirmedRateDelta"]
    if rate_delta is not None:
        if abs(rate_delta) < 0.1:
            parts.append(f"Confirmed rate held steady at {latest['confirmedRate']:.1f}%.")
        else:
            direction = "improved" if rate_delta > 0 else "dropped"
            parts.append(
                f"Confirmed rate {direction} {abs(rate_delta):.1f} points "
                f"({previous['confirmedRate']:.1f}% → {latest['confirmedRate']:.1f}%)."
            )
    elif latest["confirmedRate"] is not None:
        parts.append(f"Confirmed rate is {latest['confirmedRate']:.1f}% (previous run not yet reviewed).")

    fpd = comparison["falsePositiveDelta"]
    if fpd != 0:
        parts.append(f"{abs(fpd)} {'more' if fpd > 0 else 'fewer'} false positive(s) flagged.")

    return " ".join(parts)


@router.get("/dashboard/recent-reports")
def dashboard_recent_reports(
    limit: int = Query(8, ge=1, le=50),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    rows = _load_dataset(db, user, date_from=date_from, date_to=date_to)
    rows.sort(key=lambda r: r["run"].created_at, reverse=True)
    return {"reports": [_run_summary(r) for r in rows[:limit]]}


@router.get("/dashboard/library-stats")
def dashboard_library_stats(db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    """Image Library summary for the dashboard — total images, zone/village
    breakdown, format mix, and how many still have no zone/village assigned.
    Independent of detection history and not scoped per-user: the library
    itself isn't user-scoped anywhere else in the app (matches
    local_routes.py's image listing), so this reflects the whole library as
    it stands right now, for every viewer.
    """
    _require_dda()
    from sqlalchemy import func as sa_func

    from .tree.models import ImageLibrary

    total = db.query(ImageLibrary).count()
    by_zone, by_village = _image_counts_by_location(db)
    unassigned = db.query(ImageLibrary).filter(ImageLibrary.zone_id.is_(None)).count()

    format_rows = (
        db.query(ImageLibrary.format, sa_func.count(ImageLibrary.id))
        .group_by(ImageLibrary.format)
        .all()
    )
    by_format = sorted(
        ({"format": fmt or "Unknown", "count": cnt} for fmt, cnt in format_rows),
        key=lambda x: -x["count"],
    )
    zones_out = sorted(
        ({"zone": z, "imageCount": c} for z, c in by_zone.items()),
        key=lambda x: -x["imageCount"],
    )
    villages_out = sorted(
        ({"zone": z, "village": v, "imageCount": c} for (z, v), c in by_village.items()),
        key=lambda x: -x["imageCount"],
    )

    return {
        "totalImages": total,
        "assignedImages": total - unassigned,
        "unassignedImages": unassigned,
        "zonesCovered": len(by_zone),
        "villagesCovered": len(by_village),
        "byZone": zones_out,
        "byVillage": villages_out,
        "byFormat": by_format,
    }
