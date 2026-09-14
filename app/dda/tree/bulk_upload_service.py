"""Bulk image upload: an Excel manifest + a batch of files, routed through the
same upload_image() pipeline used by the single-image upload (validation,
dedup, thumbnailing, geo-metadata) — see image_service.py."""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from fastapi import HTTPException, UploadFile
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.datavalidation import DataValidation
from sqlalchemy.orm import Session

from ..models import DdaVillage, DdaZone
from .image_service import IMAGE_TYPES, upload_image
from .models import ImageLibrary
from .tree_service import get_or_create_node_by_path

logger = logging.getLogger(__name__)

TEMPLATE_HEADERS = ["File Name", "Folder Path", "Zone", "Village", "Image Type", "Capture Date", "Manual Bounds"]
TEMPLATE_EXAMPLE_ROW = ["example_before.tif", "Central Delhi/Sector 5/2025", "Central Delhi", "Sector 5", "GeoTIFF", "2025-01-15", ""]
# Zone/Village are required, not just conventionally: every image needs a
# location so it shows up correctly in the dashboard's zone/village
# breakdowns instead of piling up as "Unassigned" (a real, recurring problem
# with this exact template before these two columns existed).
_REQUIRED_COLUMNS = ("File Name", "Folder Path", "Zone", "Village", "Image Type")


def _basename(value: str) -> str:
    """A browser file input only ever sends the bare filename (never a full
    path), but it's natural to paste a full path into the sheet (e.g.
    Windows Explorer's "Copy as path", which wraps the result in literal
    quote characters: "C:\\...\\file.png") — accept either by stripping any
    surrounding quotes, then taking just the last path segment, on both
    '/' and '\\' separators."""
    v = (value or "").strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
        v = v[1:-1].strip()
    return Path(v.replace("\\", "/")).name


def build_bulk_upload_template() -> bytes:
    """Generate the standardized .xlsx template for bulk image upload."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Images"
    ws.append(TEMPLATE_HEADERS)
    ws.append(TEMPLATE_EXAMPLE_ROW)
    for col_idx, width in enumerate((30, 45, 18, 18, 16, 16, 22), start=1):
        ws.column_dimensions[ws.cell(row=1, column=col_idx).column_letter].width = width

    dv = DataValidation(type="list", formula1='"' + ",".join(sorted(IMAGE_TYPES)) + '"', allow_blank=False)
    ws.add_data_validation(dv)
    dv.add("E2:E1000")

    instructions = wb.create_sheet("Instructions")
    instructions.append(["Column", "Required", "Notes"])
    instructions.append(["File Name", "Yes", "Must match the name of one of the files you upload alongside this sheet. Pasting a full path (e.g. copied from File Explorer) is fine too — only the filename part is used."])
    instructions.append(["Folder Path", "Yes", "Slash-separated, e.g. Central Delhi/Sector 5/2025 — missing folders are created automatically."])
    instructions.append(["Zone", "Yes", "Must match an existing Zone name exactly (see Zone / District master)."])
    instructions.append(["Village", "Yes", "Must match an existing Village name within that Zone (see Village / Location master)."])
    instructions.append(["Image Type", "Yes", "One of: " + ", ".join(sorted(IMAGE_TYPES))])
    instructions.append(["Capture Date", "No", "YYYY-MM-DD"])
    instructions.append(["Manual Bounds", "No", "West,South,East,North — only used if the image has no embedded georeferencing."])
    instructions.column_dimensions["A"].width = 18
    instructions.column_dimensions["B"].width = 10
    instructions.column_dimensions["C"].width = 80

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


@dataclass
class ManifestRow:
    row_number: int
    file_name: str
    folder_path: str
    zone: str
    village: str
    image_type: str
    capture_date: Optional[str]
    manual_bounds: Optional[str]


def parse_bulk_manifest(xlsx_bytes: bytes) -> tuple[List[ManifestRow], List[dict]]:
    """Parse the uploaded manifest. Returns (valid_rows, row_errors)."""
    try:
        wb = load_workbook(io.BytesIO(xlsx_bytes), data_only=True, read_only=True)
    except Exception:
        raise HTTPException(status_code=400, detail="Could not read the Excel file. Please use the provided template.")
    ws = wb["Images"] if "Images" in wb.sheetnames else wb.worksheets[0]

    rows_iter = ws.iter_rows(values_only=True)
    try:
        header = [str(c or "").strip() for c in next(rows_iter)]
    except StopIteration:
        raise HTTPException(status_code=400, detail="The Excel sheet is empty.")

    idx = {}
    for name in TEMPLATE_HEADERS:
        idx[name] = header.index(name) if name in header else None
    missing = [name for name in _REQUIRED_COLUMNS if idx.get(name) is None]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing required column(s): {', '.join(missing)}. Please use the provided template.")

    valid: List[ManifestRow] = []
    errors: List[dict] = []
    for row_number, raw in enumerate(rows_iter, start=2):
        if raw is None or all(c is None or str(c).strip() == "" for c in raw):
            continue

        def cell(name: str, _raw=raw) -> str:
            i = idx.get(name)
            if i is None or i >= len(_raw) or _raw[i] is None:
                return ""
            return str(_raw[i]).strip()

        file_name = _basename(cell("File Name"))
        folder_path = cell("Folder Path")
        zone = cell("Zone")
        village = cell("Village")
        image_type = cell("Image Type") or "GeoTIFF"
        capture_date = cell("Capture Date") or None
        manual_bounds = cell("Manual Bounds") or None

        if not file_name or not folder_path:
            errors.append({"row": row_number, "file": file_name, "reason": "File Name and Folder Path are required"})
            continue
        if not zone or not village:
            errors.append({"row": row_number, "file": file_name, "reason": "Zone and Village are required"})
            continue
        if image_type not in IMAGE_TYPES:
            errors.append({"row": row_number, "file": file_name, "reason": f"Image Type must be one of: {', '.join(sorted(IMAGE_TYPES))}"})
            continue
        valid.append(ManifestRow(
            row_number=row_number, file_name=file_name, folder_path=folder_path,
            zone=zone, village=village,
            image_type=image_type, capture_date=capture_date, manual_bounds=manual_bounds,
        ))
    return valid, errors


def _resolve_zone_village_by_name(db: Session, zone_name: str, village_name: str) -> tuple[int, int]:
    """Case-insensitive name lookup — the manifest carries human-readable
    Zone/Village names (matching the master data), not ids."""
    zone = db.query(DdaZone).filter(DdaZone.name.ilike(zone_name.strip())).first()
    if not zone:
        raise HTTPException(status_code=400, detail=f"Zone '{zone_name}' not found. Check the Zone / District master.")
    village = (
        db.query(DdaVillage)
        .filter(DdaVillage.zone_id == zone.id, DdaVillage.name.ilike(village_name.strip()))
        .first()
    )
    if not village:
        raise HTTPException(
            status_code=400,
            detail=f"Village '{village_name}' not found under zone '{zone_name}'. Check the Village / Location master.",
        )
    return zone.id, village.id


@dataclass
class BulkUploadResult:
    created: List[dict] = field(default_factory=list)
    duplicates: List[dict] = field(default_factory=list)
    errors: List[dict] = field(default_factory=list)
    unmatched_files: List[dict] = field(default_factory=list)


async def bulk_upload_images(
    db: Session,
    rows: List[ManifestRow],
    files: List[UploadFile],
    *,
    uploaded_by: str,
) -> BulkUploadResult:
    result = BulkUploadResult()
    files_by_name = {(f.filename or "").strip().lower(): f for f in files}
    matched_names = set()

    for row in rows:
        key = row.file_name.strip().lower()
        file = files_by_name.get(key)
        if file is None:
            result.errors.append({"row": row.row_number, "file": row.file_name, "reason": "No uploaded file matches this File Name"})
            continue
        matched_names.add(key)
        try:
            zone_id, village_id = _resolve_zone_village_by_name(db, row.zone, row.village)
            node = get_or_create_node_by_path(db, row.folder_path, created_by=uploaded_by)
            already_existed = (
                db.query(ImageLibrary)
                .filter(ImageLibrary.node_id == node.id, ImageLibrary.image_name == Path(row.file_name).name)
                .count() > 0
            )
            img = await upload_image(
                db, node.id, file,
                image_type=row.image_type,
                capture_date=row.capture_date,
                uploaded_by=uploaded_by,
                manual_bounds=row.manual_bounds,
                zone_id=zone_id,
                village_id=village_id,
            )
            entry = {"row": row.row_number, "file": row.file_name, "folder": node.node_path, "imageId": img.id}
            (result.duplicates if already_existed else result.created).append(entry)
        except HTTPException as exc:
            db.rollback()
            result.errors.append({"row": row.row_number, "file": row.file_name, "reason": exc.detail})
        except Exception:
            db.rollback()
            logger.exception("Bulk upload failed for row %s (%s)", row.row_number, row.file_name)
            result.errors.append({"row": row.row_number, "file": row.file_name, "reason": "Unexpected error while uploading this file"})

    for name, file in files_by_name.items():
        if name not in matched_names:
            result.unmatched_files.append({"file": file.filename})

    return result
