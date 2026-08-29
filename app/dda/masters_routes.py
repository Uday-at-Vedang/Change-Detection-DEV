"""Zone / District and Village / Location master data."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..database import get_db
from ..models import User
from .models import DdaVillage, DdaZone, ImageAsset
from .rbac.permissions import require_module_permission
from .seed import seed_delhi_hierarchy

router = APIRouter()


def _require_dda():
    from .config import IS_DDA_MODE
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled")


class ZoneBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    mode: str = Field("admin", max_length=32)


class VillageBody(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    zone_id: int


def _zone_dict(zone: DdaZone, village_count: int = 0) -> dict:
    return {
        "id": zone.id,
        "name": zone.name,
        "mode": zone.mode or "admin",
        "villageCount": village_count,
        "createdAt": zone.created_at.isoformat() if zone.created_at else None,
    }


def _village_dict(village: DdaVillage, zone: Optional[DdaZone] = None) -> dict:
    z = zone or village.zone
    return {
        "id": village.id,
        "name": village.name,
        "zoneId": village.zone_id,
        "zoneName": z.name if z else "",
        "createdAt": village.created_at.isoformat() if village.created_at else None,
    }


@router.get("/masters/zones")
def list_zones(
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("masters", "view")),
):
    _require_dda()
    seed_delhi_hierarchy(db)
    zones = db.query(DdaZone).order_by(DdaZone.name).all()
    counts = {}
    for v in db.query(DdaVillage).all():
        counts[v.zone_id] = counts.get(v.zone_id, 0) + 1
    return {"zones": [_zone_dict(z, counts.get(z.id, 0)) for z in zones]}


@router.post("/masters/zones")
def create_zone(
    body: ZoneBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("masters", "create")),
):
    _require_dda()
    name = body.name.strip()
    if db.query(DdaZone).filter(DdaZone.name == name).first():
        raise HTTPException(status_code=409, detail="A zone with this name already exists.")
    zone = DdaZone(name=name, mode=(body.mode or "admin").strip() or "admin")
    db.add(zone)
    db.commit()
    db.refresh(zone)
    return {"ok": True, "zone": _zone_dict(zone, 0)}


@router.put("/masters/zones/{zone_id}")
def update_zone(
    zone_id: int,
    body: ZoneBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("masters", "edit")),
):
    _require_dda()
    zone = db.query(DdaZone).filter(DdaZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    name = body.name.strip()
    clash = db.query(DdaZone).filter(DdaZone.name == name, DdaZone.id != zone_id).first()
    if clash:
        raise HTTPException(status_code=409, detail="A zone with this name already exists.")
    zone.name = name
    zone.mode = (body.mode or zone.mode or "admin").strip()
    db.commit()
    count = db.query(DdaVillage).filter(DdaVillage.zone_id == zone.id).count()
    return {"ok": True, "zone": _zone_dict(zone, count)}


@router.delete("/masters/zones/{zone_id}")
def delete_zone(
    zone_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("masters", "delete")),
):
    _require_dda()
    zone = db.query(DdaZone).filter(DdaZone.id == zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    in_use = db.query(ImageAsset).filter(ImageAsset.zone_id == zone_id).count()
    if in_use:
        raise HTTPException(status_code=409, detail="This zone is used by library images and cannot be deleted.")
    db.query(DdaVillage).filter(DdaVillage.zone_id == zone_id).delete(synchronize_session=False)
    db.delete(zone)
    db.commit()
    return {"ok": True}


@router.get("/masters/villages")
def list_villages(
    zone_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("masters", "view")),
):
    _require_dda()
    seed_delhi_hierarchy(db)
    q = db.query(DdaVillage).order_by(DdaVillage.name)
    if zone_id:
        q = q.filter(DdaVillage.zone_id == zone_id)
    villages = q.all()
    zones = {z.id: z for z in db.query(DdaZone).all()}
    return {
        "villages": [_village_dict(v, zones.get(v.zone_id)) for v in villages],
        "zones": [_zone_dict(z) for z in sorted(zones.values(), key=lambda z: z.name)],
    }


@router.post("/masters/villages")
def create_village(
    body: VillageBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("masters", "create")),
):
    _require_dda()
    zone = db.query(DdaZone).filter(DdaZone.id == body.zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    name = body.name.strip()
    clash = (
        db.query(DdaVillage)
        .filter(DdaVillage.zone_id == zone.id, DdaVillage.name == name)
        .first()
    )
    if clash:
        raise HTTPException(status_code=409, detail="That location already exists in this zone.")
    village = DdaVillage(zone_id=zone.id, name=name)
    db.add(village)
    db.commit()
    db.refresh(village)
    return {"ok": True, "village": _village_dict(village, zone)}


@router.put("/masters/villages/{village_id}")
def update_village(
    village_id: int,
    body: VillageBody,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("masters", "edit")),
):
    _require_dda()
    village = db.query(DdaVillage).filter(DdaVillage.id == village_id).first()
    if not village:
        raise HTTPException(status_code=404, detail="Location not found")
    zone = db.query(DdaZone).filter(DdaZone.id == body.zone_id).first()
    if not zone:
        raise HTTPException(status_code=404, detail="Zone not found")
    name = body.name.strip()
    clash = (
        db.query(DdaVillage)
        .filter(
            DdaVillage.zone_id == zone.id,
            DdaVillage.name == name,
            DdaVillage.id != village_id,
        )
        .first()
    )
    if clash:
        raise HTTPException(status_code=409, detail="That location already exists in this zone.")
    village.name = name
    village.zone_id = zone.id
    db.commit()
    return {"ok": True, "village": _village_dict(village, zone)}


@router.delete("/masters/villages/{village_id}")
def delete_village(
    village_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(require_module_permission("masters", "delete")),
):
    _require_dda()
    village = db.query(DdaVillage).filter(DdaVillage.id == village_id).first()
    if not village:
        raise HTTPException(status_code=404, detail="Location not found")
    in_use = db.query(ImageAsset).filter(ImageAsset.village_id == village_id).count()
    if in_use:
        raise HTTPException(status_code=409, detail="This location is used by library images and cannot be deleted.")
    db.delete(village)
    db.commit()
    return {"ok": True}
