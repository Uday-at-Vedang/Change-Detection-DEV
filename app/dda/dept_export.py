"""Department export adapter — API or file fallback (FR-08)."""
from __future__ import annotations

import csv
import io
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests

from ..models import DetectionRun
from .geo_regions import region_lat_lng

logger = logging.getLogger(__name__)

DEPT_API_URL = os.environ.get("DEPT_API_URL", "").strip()
DEPT_API_KEY = os.environ.get("DEPT_API_KEY", "").strip()


@dataclass
class ExportResult:
    ok: bool
    mode: str  # api | file
    message: str
    submitted_count: int = 0
    download_path: Optional[str] = None
    detail: Optional[Dict[str, Any]] = None


def regions_to_csv_rows(run: DetectionRun, regions: List[dict]) -> str:
    """Build CSV string for export."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([
        "run_id", "run_title", "zone", "village", "region_id",
        "dda_change_type", "internal_type", "confidence", "area_px",
        "latitude", "longitude", "area_sq_m", "review_status", "notes",
    ])
    for r in regions:
        lat, lng = region_lat_lng(r)
        writer.writerow([
            run.id,
            run.title,
            run.zone or "",
            run.village or "",
            r.get("id", ""),
            r.get("ddaChangeType") or "",
            r.get("internalObjectType") or r.get("objectType") or "",
            f'{(r.get("confidence") or 0):.4f}',
            r.get("area", ""),
            lat if lat is not None else "",
            lng if lng is not None else "",
            r.get("areaSqM") if r.get("areaSqM") is not None else "",
            r.get("reviewStatus") or "pending",
            r.get("reviewNotes") or "",
        ])
    return buf.getvalue()


class FileExporter:
    """Fallback — client downloads CSV via API."""

    def submit(self, run: DetectionRun, regions: List[dict]) -> ExportResult:
        if not regions:
            return ExportResult(
                ok=False,
                mode="file",
                message="No confirmed regions to export.",
                submitted_count=0,
            )
        return ExportResult(
            ok=True,
            mode="file",
            message=f"{len(regions)} confirmed region(s) ready for CSV download.",
            submitted_count=len(regions),
            download_path=f"/api/dda/reports/{run.id}/export.csv?confirmed=1",
        )


class ApiExporter:
    """POST confirmed changes to departmental API."""

    def __init__(self, url: str, api_key: str = ""):
        self.url = url.rstrip("/")
        self.api_key = api_key

    def submit(self, run: DetectionRun, regions: List[dict]) -> ExportResult:
        if not regions:
            return ExportResult(
                ok=False,
                mode="api",
                message="No confirmed regions to submit.",
            )
        payload = {
            "runId": run.id,
            "title": run.title,
            "zone": run.zone or "",
            "village": run.village or "",
            "changePercentage": run.change_percentage,
            "regions": [
                {
                    "id": r.get("id"),
                    "ddaChangeType": r.get("ddaChangeType"),
                    "objectType": r.get("objectType"),
                    "confidence": r.get("confidence"),
                    "area": r.get("area"),
                    "latitude": r.get("latitude") or (r.get("latLng") or {}).get("lat"),
                    "longitude": r.get("longitude") or (r.get("latLng") or {}).get("lng"),
                    "areaSqM": r.get("areaSqM"),
                }
                for r in regions
            ],
        }
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        try:
            resp = requests.post(self.url, json=payload, headers=headers, timeout=60)
            if 200 <= resp.status_code < 300:
                detail = None
                try:
                    detail = resp.json()
                except Exception:
                    detail = {"body": resp.text[:500]}
                return ExportResult(
                    ok=True,
                    mode="api",
                    message=f"Submitted {len(regions)} confirmed region(s) to departmental system.",
                    submitted_count=len(regions),
                    detail=detail,
                )
            return ExportResult(
                ok=False,
                mode="api",
                message=f"Department API returned {resp.status_code}: {resp.text[:200]}",
            )
        except Exception as exc:
            logger.exception("Department API submit failed")
            return ExportResult(
                ok=False,
                mode="api",
                message=f"Department API error: {exc}",
            )


def get_exporter() -> FileExporter | ApiExporter:
    if DEPT_API_URL:
        return ApiExporter(DEPT_API_URL, DEPT_API_KEY)
    return FileExporter()
