import json
import logging
import shutil
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..auth import get_or_create_guest_user
from ..database import get_db
from ..models import User
from .config import (
    ALLOWED_EXTENSIONS,
    IS_DDA_MODE,
    LIBRARY_DIR,
    MAX_GEOTIFF_BYTES,
    PREVIEWS_DIR,
    THUMBS_DIR,
    ensure_library_dirs,
    geotiff_io_available,
)
from .geotiff_io import bounds_to_json, inspect_image, raster_to_preview_png
from .models import DdaVillage, DdaZone, ImageAsset

logger = logging.getLogger(__name__)
router = APIRouter()


def _require_dda():
    if not IS_DDA_MODE:
        raise HTTPException(status_code=404, detail="DDA mode is not enabled on this server")


def _image_to_dict(asset: ImageAsset, zone_name: str = "", village_name: str = "") -> dict:
    return {
        "id": asset.id,
        "uuid": asset.uuid,
        "zoneId": asset.zone_id,
        "zoneName": zone_name,
        "villageId": asset.village_id,
        "villageName": village_name,
        "areaName": asset.area_name or "",
        "year": asset.year,
        "gridId": asset.grid_id or "",
        "captureDate": asset.capture_date.isoformat() if asset.capture_date else None,
        "source": asset.source,
        "format": asset.format,
        "originalFilename": asset.original_filename,
        "hasGeoref": asset.has_georef,
        "crs": asset.crs or "",
        "bounds": json.loads(asset.bounds_json) if asset.bounds_json else None,
        "width": asset.width,
        "height": asset.height,
        "fileSizeBytes": asset.file_size_bytes,
        "thumbUrl": f"/api/dda/images/{asset.id}/thumb" if asset.thumb_path else None,
        "createdAt": asset.created_at.isoformat() if asset.created_at else None,
    }


@router.get("/config")
def dda_config():
    _require_dda()
    return {
        "mode": "dda",
        "maxUploadMb": MAX_GEOTIFF_BYTES // (1024 * 1024),
        "geotiffEnabled": geotiff_io_available(),
        "allowedExtensions": sorted(ALLOWED_EXTENSIONS),
        "hierarchyMode": "admin",
    }


@router.get("/hierarchy")
def get_hierarchy(db: Session = Depends(get_db)):
    _require_dda()
    zones = db.query(DdaZone).order_by(DdaZone.name).all()
    tree = []
    for zone in zones:
        villages = (
            db.query(DdaVillage)
            .filter(DdaVillage.zone_id == zone.id)
            .order_by(DdaVillage.name)
            .all()
        )
        image_counts = {}
        for v in villages:
            cnt = db.query(ImageAsset).filter(ImageAsset.village_id == v.id).count()
            if cnt:
                image_counts[v.id] = cnt
        tree.append({
            "id": zone.id,
            "name": zone.name,
            "mode": zone.mode,
            "villages": [
                {
                    "id": v.id,
                    "name": v.name,
                    "imageCount": image_counts.get(v.id, 0),
                }
                for v in villages
            ],
        })
    return {"zones": tree}


@router.get("/images")
def list_images(
    zone_id: Optional[int] = Query(None),
    village_id: Optional[int] = Query(None),
    year: Optional[int] = Query(None),
    q: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    _require_dda()
    query = db.query(ImageAsset).order_by(ImageAsset.created_at.desc())
    if zone_id:
        query = query.filter(ImageAsset.zone_id == zone_id)
    if village_id:
        query = query.filter(ImageAsset.village_id == village_id)
    if year:
        query = query.filter(ImageAsset.year == year)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter(
            (ImageAsset.area_name.ilike(like))
            | (ImageAsset.original_filename.ilike(like))
            | (ImageAsset.grid_id.ilike(like))
        )
    assets = query.limit(200).all()
    zone_map = {z.id: z.name for z in db.query(DdaZone).all()}
    village_map = {v.id: v.name for v in db.query(DdaVillage).all()}
    return [
        _image_to_dict(
            a,
            zone_name=zone_map.get(a.zone_id, ""),
            village_name=village_map.get(a.village_id, ""),
        )
        for a in assets
    ]


@router.get("/images/{image_id}")
def get_image(image_id: int, db: Session = Depends(get_db)):
    _require_dda()
    asset = db.query(ImageAsset).filter(ImageAsset.id == image_id).first()
    if not asset:
        raise HTTPException(status_code=404, detail="Image not found")
    zone_name = ""
    village_name = ""
    if asset.zone_id:
        z = db.query(DdaZone).filter(DdaZone.id == asset.zone_id).first()
        zone_name = z.name if z else ""
    if asset.village_id:
        v = db.query(DdaVillage).filter(DdaVillage.id == asset.village_id).first()
        village_name = v.name if v else ""
    return _image_to_dict(asset, zone_name=zone_name, village_name=village_name)


@router.get("/images/{image_id}/thumb")
def get_image_thumb(image_id: int, db: Session = Depends(get_db)):
    _require_dda()
    asset = db.query(ImageAsset).filter(ImageAsset.id == image_id).first()
    if not asset or not asset.thumb_path:
        raise HTTPException(status_code=404, detail="Thumbnail not found")
    path = LIBRARY_DIR.parent / asset.thumb_path
    if not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail file missing")
    return FileResponse(path, media_type="image/png")


@router.post("/images/upload")
async def upload_image(
    file: UploadFile = File(...),
    zone_id: int = Form(...),
    village_id: int = Form(...),
    area_name: str = Form(""),
    year: int = Form(...),
    capture_date: str = Form(...),
    source: str = Form("satellite"),
    grid_id: str = Form(""),
    manual_bounds_json: str = Form(""),
    db: Session = Depends(get_db),
):
    _require_dda()
    ensure_library_dirs()
    user = get_or_create_guest_user(db)

    zone = db.query(DdaZone).filter(DdaZone.id == zone_id).first()
    village = db.query(DdaVillage).filter(DdaVillage.id == village_id, DdaVillage.zone_id == zone_id).first()
    if not zone or not village:
        raise HTTPException(status_code=400, detail="Invalid zone or village")

    original = file.filename or "upload"
    ext = Path(original).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format. Allowed: {', '.join(sorted(ALLOWED_EXTENSIONS))}",
        )

    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="File is empty")
    if len(raw) > MAX_GEOTIFF_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"File too large (max {MAX_GEOTIFF_BYTES // (1024 * 1024)} MB)",
        )

    try:
        cap_date = date.fromisoformat(capture_date.strip())
    except ValueError:
        raise HTTPException(status_code=400, detail="capture_date must be YYYY-MM-DD")

    if source not in ("satellite", "drone"):
        raise HTTPException(status_code=400, detail="source must be satellite or drone")

    asset_uuid = uuid.uuid4().hex
    stored_name = f"{asset_uuid}{ext}"
    dest_file = LIBRARY_DIR / str(year) / stored_name
    dest_file.parent.mkdir(parents=True, exist_ok=True)
    dest_file.write_bytes(raw)

    ingest = inspect_image(dest_file)
    has_georef = ingest.has_georef
    manual_location = manual_bounds_json.strip()

    if ext in (".tif", ".tiff") and not has_georef and not manual_location:
        dest_file.unlink(missing_ok=True)
        raise HTTPException(
            status_code=400,
            detail="GeoTIFF has no embedded georeferencing. Provide manual_bounds_json (west,south,east,north in WGS84).",
        )

    thumb_file = THUMBS_DIR / f"{asset_uuid}.png"
    preview_file = PREVIEWS_DIR / f"{asset_uuid}.png"
    try:
        raster_to_preview_png(dest_file, thumb_file, max_side=256)
        raster_to_preview_png(dest_file, preview_file, max_side=1024)
    except Exception as exc:
        dest_file.unlink(missing_ok=True)
        raise HTTPException(status_code=400, detail=f"Could not generate preview: {exc}")

    rel_file = str(dest_file.relative_to(LIBRARY_DIR.parent))
    rel_thumb = str(thumb_file.relative_to(LIBRARY_DIR.parent))
    rel_preview = str(preview_file.relative_to(LIBRARY_DIR.parent))

    bounds_json = bounds_to_json(ingest.bounds_wgs84)
    if not bounds_json and manual_location:
        try:
            parts = [float(x.strip()) for x in manual_location.replace("[", "").replace("]", "").split(",")]
            if len(parts) == 4:
                bounds_json = bounds_to_json(tuple(parts))
        except (ValueError, TypeError):
            pass

    asset = ImageAsset(
        uuid=asset_uuid,
        zone_id=zone_id,
        village_id=village_id,
        area_name=area_name.strip(),
        year=year,
        grid_id=grid_id.strip(),
        capture_date=cap_date,
        source=source,
        format=ingest.format,
        original_filename=original,
        file_path=rel_file,
        thumb_path=rel_thumb,
        preview_path=rel_preview,
        crs=ingest.crs,
        bounds_json=bounds_json,
        has_georef=has_georef or bool(bounds_json),
        manual_location_json=manual_location,
        width=ingest.width,
        height=ingest.height,
        file_size_bytes=len(raw),
        uploaded_by=user.id,
    )
    db.add(asset)
    db.commit()
    db.refresh(asset)

    return {
        "status": "success",
        "image": _image_to_dict(asset, zone_name=zone.name, village_name=village.name),
    }
