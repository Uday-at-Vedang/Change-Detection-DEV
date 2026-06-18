"""CRUD for zone/folder library hierarchy."""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from .config import IS_DDA_MODE, get_writable_library_root
from .dda_auth import current_dda_user, get_user_role, require_min_role
from .local_library import count_files_in_folder, count_files_in_zone, scan_tree
from .models import DdaLocalFileIndex, DdaVillage, DdaZone
from .path_slugs import unique_slug, validate_path_segment

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_dda():
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


class ZoneCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class ZonePatchBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class FolderCreateBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class FolderPatchBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)


class YearCreateBody(BaseModel):
    year: int = Field(..., ge=1990, le=2100)


def _zone_dict(zone: DdaZone) -> dict:
    return {"id": zone.id, "name": zone.name, "slug": zone.slug, "mode": zone.mode}


def _folder_dict(folder: DdaVillage) -> dict:
    return {"id": folder.id, "zoneId": folder.zone_id, "name": folder.name, "slug": folder.slug}


def _existing_zone_slugs(db: Session) -> set:
    return {z.slug for z in db.query(DdaZone).filter(DdaZone.slug.isnot(None)).all() if z.slug}


def _existing_folder_slugs(db: Session, zone_id: int) -> set:
    return {
        f.slug for f in db.query(DdaVillage).filter(
            DdaVillage.zone_id == zone_id, DdaVillage.slug.isnot(None)
        ).all() if f.slug
    }


@router.get("/me")
def dda_me(user: User = Depends(current_dda_user), db: Session = Depends(get_db)):
    _require_dda()
    return {"userId": user.id, "role": get_user_role(db, user), "email": user.email}


@router.get("/hierarchy/tree")
def hierarchy_tree(db: Session = Depends(get_db), user: User = Depends(current_dda_user)):
    _require_dda()
    return scan_tree(db)


@router.post("/hierarchy/zones")
def create_zone(
    body: ZoneCreateBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    require_min_role(user, db, "admin")
    name = body.name.strip()
    if db.query(DdaZone).filter(DdaZone.name == name).first():
        raise HTTPException(status_code=400, detail="Zone name already exists")
    slug = unique_slug(name, _existing_zone_slugs(db))
    if not validate_path_segment(slug):
        raise HTTPException(status_code=400, detail="Could not derive a valid folder slug")
    zone = DdaZone(name=name, slug=slug, mode="admin")
    db.add(zone)
    db.commit()
    db.refresh(zone)
    root = get_writable_library_root()
    (root / slug).mkdir(parents=True, exist_ok=True)
    logger.info("Created zone %s (%s)", name, slug)
    return {"zone": _zone_dict(zone)}


@router.patch("/hierarchy/zones/{zone_id}")
def patch_zone(
    zone_id: int,
    body: ZonePatchBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    require_min_role(user, db, "admin")
    zone = db.query(DdaZone).filter(DdaZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    name = body.name.strip()
    other = db.query(DdaZone).filter(DdaZone.name == name, DdaZone.id != zone_id).first()
    if other:
        raise HTTPException(status_code=400, detail="Zone name already exists")
    zone.name = name
    db.commit()
    db.refresh(zone)
    return {"zone": _zone_dict(zone)}


@router.delete("/hierarchy/zones/{zone_id}")
def delete_zone(
    zone_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    require_min_role(user, db, "admin")
    zone = db.query(DdaZone).filter(DdaZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    if not zone.slug:
        raise HTTPException(status_code=400, detail="Zone has no slug")
    root = get_writable_library_root()
    if count_files_in_zone(root, zone.slug) > 0:
        raise HTTPException(status_code=400, detail="Zone folder is not empty")
    indexed = db.query(DdaLocalFileIndex).filter(DdaLocalFileIndex.zone_id == zone_id).count()
    if indexed > 0:
        raise HTTPException(status_code=400, detail="Zone has indexed files")
    zone_dir = root / zone.slug
    db.delete(zone)
    db.commit()
    if zone_dir.exists() and zone_dir.is_dir():
        try:
            zone_dir.rmdir()
        except OSError:
            pass
    return {"ok": True}


@router.post("/hierarchy/zones/{zone_id}/folders")
def create_folder(
    zone_id: int,
    body: FolderCreateBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    require_min_role(user, db, "admin")
    zone = db.query(DdaZone).filter(DdaZone.id == zone_id).first()
    if not zone or not zone.slug:
        raise HTTPException(status_code=404, detail="Zone not found")
    name = body.name.strip()
    existing = db.query(DdaVillage).filter(DdaVillage.zone_id == zone_id, DdaVillage.name == name).first()
    if existing:
        raise HTTPException(status_code=400, detail="Folder name already exists in this zone")
    slug = unique_slug(name, _existing_folder_slugs(db, zone_id))
    folder = DdaVillage(zone_id=zone_id, name=name, slug=slug)
    db.add(folder)
    db.commit()
    db.refresh(folder)
    (get_writable_library_root() / zone.slug / slug).mkdir(parents=True, exist_ok=True)
    return {"folder": _folder_dict(folder)}


@router.patch("/hierarchy/folders/{folder_id}")
def patch_folder(
    folder_id: int,
    body: FolderPatchBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    require_min_role(user, db, "admin")
    folder = db.query(DdaVillage).filter(DdaVillage.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    name = body.name.strip()
    other = db.query(DdaVillage).filter(
        DdaVillage.zone_id == folder.zone_id, DdaVillage.name == name, DdaVillage.id != folder_id
    ).first()
    if other:
        raise HTTPException(status_code=400, detail="Folder name already exists in this zone")
    folder.name = name
    db.commit()
    db.refresh(folder)
    return {"folder": _folder_dict(folder)}


@router.delete("/hierarchy/folders/{folder_id}")
def delete_folder(
    folder_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    require_min_role(user, db, "admin")
    folder = db.query(DdaVillage).filter(DdaVillage.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    zone = db.query(DdaZone).filter(DdaZone.id == folder.zone_id).first()
    if not zone or not zone.slug or not folder.slug:
        raise HTTPException(status_code=400, detail="Folder has no disk path")
    root = get_writable_library_root()
    if count_files_in_folder(root, zone.slug, folder.slug) > 0:
        raise HTTPException(status_code=400, detail="Folder is not empty")
    indexed = db.query(DdaLocalFileIndex).filter(DdaLocalFileIndex.folder_id == folder_id).count()
    if indexed > 0:
        raise HTTPException(status_code=400, detail="Folder has indexed files")
    folder_dir = root / zone.slug / folder.slug
    db.delete(folder)
    db.commit()
    if folder_dir.exists() and folder_dir.is_dir():
        try:
            folder_dir.rmdir()
        except OSError:
            pass
    return {"ok": True}


@router.post("/hierarchy/folders/{folder_id}/years")
def create_year_folder(
    folder_id: int,
    body: YearCreateBody,
    db: Session = Depends(get_db),
    user: User = Depends(current_dda_user),
):
    _require_dda()
    require_min_role(user, db, "uploader")
    folder = db.query(DdaVillage).filter(DdaVillage.id == folder_id).first()
    if not folder:
        raise HTTPException(status_code=404, detail="Folder not found")
    zone = db.query(DdaZone).filter(DdaZone.id == folder.zone_id).first()
    if not zone or not zone.slug or not folder.slug:
        raise HTTPException(status_code=400, detail="Folder has no disk path")
    year_dir = get_writable_library_root() / zone.slug / folder.slug / str(body.year)
    year_dir.mkdir(parents=True, exist_ok=True)
    return {"ok": True, "path": f"{zone.slug}/{folder.slug}/{body.year}"}
