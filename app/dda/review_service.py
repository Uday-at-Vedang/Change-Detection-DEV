"""Merge and persist region review state (FR-08)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from ..models import DetectionRun
from .models import RegionReview

VALID_REVIEW_STATUSES = {"pending", "confirmed", "false_positive", "submitted"}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def load_regions(run: DetectionRun) -> List[dict]:
    try:
        return json.loads(run.regions_json or "[]")
    except json.JSONDecodeError:
        return []


def save_regions(db: Session, run: DetectionRun, regions: List[dict]) -> None:
    run.regions_json = json.dumps(regions)
    run.regions_count = len(regions)
    db.commit()


def merge_reviews(db: Session, run_id: int, regions: List[dict]) -> List[dict]:
    """Attach reviewStatus / notes from RegionReview rows onto region dicts."""
    reviews = {
        r.region_id: r
        for r in db.query(RegionReview).filter(RegionReview.run_id == run_id).all()
    }
    out = []
    for region in regions:
        rid = int(region.get("id", 0))
        rev = reviews.get(rid)
        merged = dict(region)
        if rev:
            merged["reviewStatus"] = rev.status
            merged["reviewNotes"] = rev.notes or ""
            merged["reviewedAt"] = rev.reviewed_at.isoformat() if rev.reviewed_at else None
        else:
            merged.setdefault("reviewStatus", region.get("reviewStatus", "pending"))
        out.append(merged)
    return out


def get_or_create_review(db: Session, run_id: int, region_id: int) -> RegionReview:
    row = (
        db.query(RegionReview)
        .filter(RegionReview.run_id == run_id, RegionReview.region_id == region_id)
        .first()
    )
    if row:
        return row
    row = RegionReview(run_id=run_id, region_id=region_id, status="pending")
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def set_region_review(
    db: Session,
    run: DetectionRun,
    region_id: int,
    review_status: str,
    reviewer_id: int,
    notes: str = "",
) -> dict:
    if review_status not in ("confirmed", "false_positive", "pending"):
        raise ValueError(f"Invalid reviewStatus: {review_status}")

    regions = load_regions(run)
    region = next((r for r in regions if int(r.get("id", -1)) == region_id), None)
    if not region:
        raise LookupError("Region not found in run")

    row = get_or_create_review(db, run.id, region_id)
    if row.status == "submitted":
        raise PermissionError("Region already submitted — cannot change review")

    row.status = review_status
    row.reviewer_id = reviewer_id
    row.notes = (notes or "").strip()
    row.reviewed_at = _utcnow()
    db.commit()

    region["reviewStatus"] = review_status
    if notes:
        region["reviewNotes"] = notes
    save_regions(db, run, regions)
    return merge_reviews(db, run.id, [region])[0]


def filter_regions_by_review(regions: List[dict], status: str) -> List[dict]:
    return [r for r in regions if (r.get("reviewStatus") or "pending") == status]


def mark_confirmed_submitted(db: Session, run_id: int, region_ids: List[int]) -> int:
    q = db.query(RegionReview).filter(
        RegionReview.run_id == run_id,
        RegionReview.region_id.in_(region_ids),
        RegionReview.status == "confirmed",
    )
    now = _utcnow()
    count = 0
    for row in q.all():
        row.status = "submitted"
        row.submitted_at = now
        count += 1
    db.commit()
    return count


def review_summary(regions: List[dict]) -> Dict[str, int]:
    summary = {"pending": 0, "confirmed": 0, "false_positive": 0, "submitted": 0}
    for r in regions:
        st = r.get("reviewStatus") or "pending"
        summary[st] = summary.get(st, 0) + 1
    return summary


def bulk_classification_counts(db: Session) -> Dict[int, Dict[str, int]]:
    """Per-run counts of non-pending RegionReview statuses, across every run.

    One grouped query instead of loading regions_json per run — a region with
    no RegionReview row is implicitly "pending" (see merge_reviews above), so
    callers derive unclassified as ``regions_count - sum(these counts)``.
    """
    rows = (
        db.query(RegionReview.run_id, RegionReview.status, func.count(RegionReview.id))
        .filter(RegionReview.status != "pending")
        .group_by(RegionReview.run_id, RegionReview.status)
        .all()
    )
    out: Dict[int, Dict[str, int]] = {}
    for run_id, status, cnt in rows:
        out.setdefault(run_id, {})[status] = cnt
    return out
