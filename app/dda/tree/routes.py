"""FastAPI routes for unlimited-depth tree library."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from typing import List

from ...database import get_db
from ...models import User
from ..config import IS_DDA_MODE
from ..dda_auth import current_dda_user, get_user_role
from ..rbac.permissions import require_module_permission
from .bulk_upload_service import build_bulk_upload_template, bulk_upload_images, parse_bulk_manifest
from .image_service import (
    IMAGE_TYPES,
    delete_library_image,
    image_to_dict,
    list_all_images,
    list_images_for_node,
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
    return {"tree": build_tree(db), "imageTypes": sorted(IMAGE_TYPES)}


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
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    return list_all_images(db, node_id=node_id, query=q)


@router.post("/tree/nodes/{node_id}/images/upload")
async def api_upload_image(
    node_id: int,
    file: UploadFile = File(...),
    image_type: str = Form("GeoTIFF"),
    capture_date: str = Form(""),
    manual_bounds: str = Form(""),
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
    )
    node = get_node_or_404(db, node_id)
    return {"status": True, "message": "Image Uploaded Successfully", "image": image_to_dict(img, node)}


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


@router.post("/tree/bulk-upload")
async def api_bulk_upload(
    excel: UploadFile = File(...),
    files: List[UploadFile] = File(...),
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("library", "create")),
):
    _require_dda()
    xlsx_bytes = await excel.read()
    rows, row_errors = parse_bulk_manifest(xlsx_bytes)
    result = await bulk_upload_images(db, rows, files, uploaded_by=user.email or str(user.id))
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
