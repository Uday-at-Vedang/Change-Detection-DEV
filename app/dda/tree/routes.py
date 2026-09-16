"""FastAPI routes for unlimited-depth tree library."""
from __future__ import annotations

import csv
import io
import json
from typing import List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ...database import get_db
from ...models import User
from ..config import IS_DDA_MODE, get_storage_root
from ..dda_auth import current_dda_user, get_user_role
from ..rbac.permissions import require_module_permission
from .bulk_upload_service import (
    BulkDefaults,
    build_bulk_upload_template,
    bulk_upload_images,
    parse_bulk_manifest,
    zone_village_names,
)
from .image_service import (
    IMAGE_TYPE_EXTENSIONS,
    IMAGE_TYPES,
    delete_library_image,
    export_library_rows,
    image_to_dict,
    list_all_images,
    list_images_for_node,
    location_names,
    upload_image,
)
from .tree_service import (
    build_tree,
    create_node,
    delete_node,
    get_node_or_404,
    get_or_create_node,
    move_node,
    rename_node,
)

router = APIRouter()


def _require_dda():
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


def _optional_int(raw: str, field: str) -> Optional[int]:
    """Multipart form fields arrive as strings; '' means 'not selected'."""
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{field} must be a number")


class NodeCreateBody(BaseModel):
    parent_id: Optional[int] = None
    node_name: str = Field(..., min_length=1, max_length=500)
    node_type: str = Field(default="Folder", max_length=100)


class NodeRenameBody(BaseModel):
    node_name: str = Field(..., min_length=1, max_length=500)
    rename_physical: bool = False


class NodeMoveBody(BaseModel):
    parent_id: Optional[int] = None


class NodeDeleteBody(BaseModel):
    delete_files: bool = False


@router.get("/me")
def tree_me(user: User = Depends(current_dda_user), db: Session = Depends(get_db)):
    _require_dda()
    return {"userId": user.id, "role": get_user_role(db, user), "email": user.email}


@router.get("/tree")
def get_tree(db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    _require_dda()
    return {
        "tree": build_tree(db),
        "imageTypes": sorted(IMAGE_TYPES),
        "imageTypeExtensions": {k: sorted(v) for k, v in IMAGE_TYPE_EXTENSIONS.items()},
    }


@router.post("/tree/nodes")
def api_create_node(
    body: NodeCreateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "create")),
):
    _require_dda()
    node = create_node(
        db,
        parent_id=body.parent_id,
        node_name=body.node_name,
        node_type=body.node_type,
        created_by=user.email or str(user.id),
    )
    return {"status": True, "message": "Node Created Successfully", "node": {
        "id": node.id, "name": node.node_name, "nodePath": node.node_path,
    }}


@router.post("/tree/nodes/ensure")
def api_ensure_node(
    body: NodeCreateBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "create")),
):
    """Create a folder if missing, otherwise return the existing sibling."""
    _require_dda()
    node, created = get_or_create_node(
        db,
        parent_id=body.parent_id,
        node_name=body.node_name,
        node_type=body.node_type,
        created_by=user.email or str(user.id),
    )
    return {
        "status": True,
        "created": created,
        "message": "Node Created Successfully" if created else "Node already exists",
        "node": {
            "id": node.id,
            "name": node.node_name,
            "nodePath": node.node_path,
            "parentId": node.parent_id,
        },
    }


@router.put("/tree/nodes/{node_id}/rename")
def api_rename_node(
    node_id: int,
    body: NodeRenameBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "edit")),
):
    _require_dda()
    node = rename_node(
        db, node_id, body.node_name,
        action_by=user.email or str(user.id),
        rename_physical=body.rename_physical,
    )
    return {"status": True, "message": "Node Renamed Successfully", "node": {
        "id": node.id, "name": node.node_name, "nodePath": node.node_path,
    }}


@router.post("/tree/nodes/{node_id}/move")
def api_move_node(
    node_id: int,
    body: NodeMoveBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "edit")),
):
    _require_dda()
    node = move_node(db, node_id, body.parent_id, action_by=user.email or str(user.id))
    return {"status": True, "message": "Node Moved Successfully", "node": {
        "id": node.id, "parentId": node.parent_id, "nodePath": node.node_path,
    }}


@router.delete("/tree/nodes/{node_id}")
def api_delete_node(
    node_id: int,
    body: NodeDeleteBody = NodeDeleteBody(),
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "delete")),
):
    _require_dda()
    result = delete_node(db, node_id, delete_files=body.delete_files, action_by=user.email or str(user.id))
    return {"status": True, "message": "Node Deleted Successfully", **result}


@router.get("/tree/nodes/{node_id}")
def api_get_node(node_id: int, db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    _require_dda()
    node = get_node_or_404(db, node_id)
    return {
        "id": node.id,
        "parentId": node.parent_id,
        "name": node.node_name,
        "nodeType": node.node_type,
        "nodePath": node.node_path,
        "physicalPath": node.physical_path,
    }


@router.get("/tree/nodes/{node_id}/images")
def api_list_node_images(node_id: int, db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    _require_dda()
    return {"images": list_images_for_node(db, node_id)}


@router.get("/tree/images")
def api_list_images(
    node_id: Optional[int] = None,
    q: Optional[str] = None,
    year: Optional[int] = None,
    image_type: Optional[str] = None,
    zone_id: Optional[int] = None,
    village_id: Optional[int] = None,
    capture_date_from: Optional[str] = None,
    capture_date_to: Optional[str] = None,
    source: Optional[str] = None,
    limit: Optional[int] = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    return list_all_images(
        db,
        node_id=node_id,
        query=q,
        year=year,
        image_type=image_type,
        zone_id=zone_id,
        village_id=village_id,
        capture_date_from=capture_date_from,
        capture_date_to=capture_date_to,
        source=source,
        include_bounds=False,
        slim=True,
        limit=limit if limit is not None else 50,
        offset=offset,
    )


@router.get("/tree/images/export")
def api_export_images(
    node_id: Optional[int] = None,
    q: Optional[str] = None,
    year: Optional[int] = None,
    image_type: Optional[str] = None,
    zone_id: Optional[int] = None,
    village_id: Optional[int] = None,
    capture_date_from: Optional[str] = None,
    capture_date_to: Optional[str] = None,
    source: Optional[str] = None,
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "view")),
):
    _require_dda()
    rows = export_library_rows(
        db,
        node_id=node_id,
        query=q,
        year=year,
        image_type=image_type,
        zone_id=zone_id,
        village_id=village_id,
        capture_date_from=capture_date_from,
        capture_date_to=capture_date_to,
        source=source,
    )
    headers = [
        "Image ID", "Server filename", "Image type", "Source", "Format",
        "Capture date", "Year", "Zone", "Village", "File size", "Uploaded on",
    ]
    if format == "xlsx":
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "Library"
        ws.append(headers)
        for row in rows:
            ws.append([row.get(h, "") for h in headers])
        buf = io.BytesIO()
        wb.save(buf)
        buf.seek(0)
        return Response(
            content=buf.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": 'attachment; filename="library_export.xlsx"'},
        )

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=headers, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    data = buf.getvalue().encode("utf-8-sig")
    return Response(
        content=data,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="library_export.csv"'},
    )


@router.post("/tree/nodes/{node_id}/images/upload")
async def api_upload_image(
    node_id: int,
    file: UploadFile = File(...),
    image_type: str = Form("GeoTIFF"),
    capture_date: str = Form(""),
    manual_bounds: str = Form(""),
    zone_id: str = Form(""),
    village_id: str = Form(""),
    year: str = Form(""),
    source: str = Form(""),
    server_filename: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "create")),
):
    _require_dda()
    img = await upload_image(
        db, node_id, file,
        image_type=image_type,
        capture_date=capture_date or None,
        uploaded_by=user.email or str(user.id),
        manual_bounds=manual_bounds or None,
        zone_id=_optional_int(zone_id, "zone_id"),
        village_id=_optional_int(village_id, "village_id"),
        year=year or None,
        source=source or None,
        server_filename=server_filename or None,
    )
    node = get_node_or_404(db, node_id)
    zones, villages = location_names(db)
    return {
        "status": True,
        "message": "Image Uploaded Successfully",
        "image": image_to_dict(img, node, zone_names=zones, village_names=villages),
    }


@router.get("/tree/bulk-upload/template")
def api_bulk_upload_template(
    user: User = Depends(require_module_permission("library", "view")),
):
    _require_dda()
    content = build_bulk_upload_template()
    return Response(
        content=content,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": "attachment; filename=bulk_image_upload_template.xlsx"},
    )


@router.get("/tree/bulk-upload/config")
def api_bulk_upload_config(
    user: User = Depends(require_module_permission("library", "view")),
):
    """Main folder for this environment — Excel General Folder paths are relative to it."""
    _require_dda()
    root = get_storage_root()
    return {
        "mainFolder": str(root),
        "imageTypes": sorted(IMAGE_TYPES),
        "sources": ["satellite", "drone"],
    }


@router.post("/tree/bulk-upload")
async def api_bulk_upload(
    excel: UploadFile = File(...),
    files: List[UploadFile] = File(...),
    year: str = Form(""),
    source: str = Form(""),
    image_type: str = Form(""),
    zone_id: str = Form(""),
    village_id: str = Form(""),
    filenames: Optional[str] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "create")),
):
    _require_dda()
    xlsx_bytes = await excel.read()
    rows, row_errors = parse_bulk_manifest(xlsx_bytes)

    zid = _optional_int(zone_id, "zone_id")
    vid = _optional_int(village_id, "village_id")
    zone_name, village_name = zone_village_names(db, zid, vid)

    name_map = {}
    if filenames:
        try:
            raw = json.loads(filenames)
            if isinstance(raw, dict):
                name_map = {str(k).strip().lower(): str(v).strip() for k, v in raw.items() if v}
        except Exception:
            raise HTTPException(status_code=400, detail="filenames must be a JSON object of originalName → serverFilename")

    defaults = BulkDefaults(
        year=(year or "").strip() or None,
        source=(source or "").strip().lower() or None,
        image_type=(image_type or "").strip() or None,
        zone=zone_name or None,
        village=village_name or None,
        filenames=name_map,
    )
    # Always run defaults so empty Image Type gets GeoTIFF.
    result = await bulk_upload_images(
        db, rows, files,
        uploaded_by=user.email or str(user.id),
        defaults=defaults,
    )
    result.errors = row_errors + result.errors
    return {
        "status": True,
        "created": result.created,
        "duplicates": result.duplicates,
        "errors": result.errors,
        "unmatchedFiles": result.unmatched_files,
        "summary": {
            "created": len(result.created),
            "duplicates": len(result.duplicates),
            "errors": len(result.errors),
            "unmatchedFiles": len(result.unmatched_files),
        },
        "mainFolder": str(get_storage_root()),
    }


@router.post("/tree/nodes/{node_id}/images/upload-bulk")
async def api_upload_images_bulk(
    node_id: int,
    files: List[UploadFile] = File(...),
    image_type: str = Form("GeoTIFF"),
    capture_date: str = Form(""),
    manual_bounds: str = Form(""),
    zone_id: str = Form(""),
    village_id: str = Form(""),
    year: str = Form(""),
    source: str = Form(""),
    filenames: Optional[List[str]] = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "create")),
):
    """Upload many files in one request; loops the single-file upload path."""
    _require_dda()
    node = get_node_or_404(db, node_id)
    zones, villages = location_names(db)
    uploaded = []
    failed = []
    if isinstance(filenames, str):
        name_list = [filenames]
    else:
        name_list = list(filenames or [])
    for i, file in enumerate(files):
        server_name = name_list[i] if i < len(name_list) else (file.filename or "upload")
        try:
            img = await upload_image(
                db, node_id, file,
                image_type=image_type,
                capture_date=capture_date or None,
                uploaded_by=user.email or str(user.id),
                manual_bounds=manual_bounds or None,
                zone_id=_optional_int(zone_id, "zone_id"),
                village_id=_optional_int(village_id, "village_id"),
                year=year or None,
                source=source or None,
                server_filename=server_name,
            )
            uploaded.append(image_to_dict(img, node, zone_names=zones, village_names=villages))
        except HTTPException as exc:
            failed.append({"filename": server_name, "error": str(exc.detail)})
        except Exception as exc:
            failed.append({"filename": server_name, "error": str(exc)})
    return {
        "status": True,
        "message": f"Uploaded {len(uploaded)} image(s)" + (f", {len(failed)} failed" if failed else ""),
        "uploaded": uploaded,
        "failed": failed,
    }


@router.delete("/tree/images/{image_id}")
def api_delete_image(
    image_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "delete")),
):
    _require_dda()
    result = delete_library_image(
        db, image_id=image_id, action_by=user.email or str(user.id),
    )
    return {"status": True, "message": "Image Deleted Successfully", **result}
