"""Restore a DDA report by re-running detection on the original GeoTIFF paths.

Uses the same windowed GeoTIFF path as the UI job (not PNG re-detect).

  python scripts/restore_run_from_geotiff.py --run-id 47 ^
    --before data/library_sources/central_delhi/Images/Grid_54.tif ^
    --after data/library_sources/central_delhi/Images/H43X2E1.tif
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", type=int, required=True)
    ap.add_argument("--before", type=str, required=True)
    ap.add_argument("--after", type=str, required=True)
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)

    before = Path(args.before)
    after = Path(args.after)
    if not before.is_file() or not after.is_file():
        print("Missing GeoTIFF paths")
        return 1

    from app.database import SessionLocal, DATA_DIR
    from app.models import DetectionRun
    from app.detection_config import get_load_max_side
    from app.dda.geotiff_io import load_rgb_pil
    from app.dda.detect_service import (
        _serialize_regions,
        _filter_weak_other_regions,
    )
    from app.dda.geo_regions import enrich_regions_geo, resolve_geo_context
    from app.detection_engine import run_detection

    db = SessionLocal()
    try:
        run = db.query(DetectionRun).filter(DetectionRun.id == args.run_id).first()
        if not run:
            print(f"Run {args.run_id} not found")
            return 1

        max_side = get_load_max_side(str(before), str(after)) or 5120
        print(f"Loading GeoTIFF pair (cap={max_side}) for classical/preview...")
        before_pil = load_rgb_pil(before, max_side=max_side)
        after_pil = load_rgb_pil(after, max_side=max_side)
        if before_pil.size != after_pil.size:
            after_pil = after_pil.resize(before_pil.size, Image.Resampling.LANCZOS)
        print(f"  preview {before_pil.size}")

        def _prog(pct, stage):
            print(f"  [{pct:3d}%] {stage}", flush=True)

        print("Running windowed GeoTIFF detection (same path as UI job)...")
        _mask, result_image, stats, change_regions = run_detection(
            before_pil,
            after_pil,
            method=run.method or "AI-Based Deep Learning",
            enable_registration=True,
            enable_normalization=True,
            detection_sensitivity=0.5,
            max_size=max_side,
            on_progress=_prog,
            before_path=str(before),
            after_path=str(after),
        )
        params = stats.get("params") or {}
        print(
            f"  engine regions={len(change_regions)} "
            f"change%={stats.get('change_percentage')} "
            f"windowed={params.get('windowed')}"
        )

        serial = _serialize_regions(change_regions)
        det_w = int(stats.get("image_width") or before_pil.size[0])
        det_h = int(stats.get("image_height") or before_pil.size[1])
        geo_ctx = resolve_geo_context(
            db, "central_delhi/Images/" + before.name, before)
        serial = enrich_regions_geo(
            serial, img_width=det_w, img_height=det_h,
            bounds=geo_ctx.bounds, geo=geo_ctx,
        )
        before_n = len(serial)
        serial = _filter_weak_other_regions(serial)
        print(f"  report regions: {before_n} -> {len(serial)} (must match)")

        overlay_path = DATA_DIR / run.overlay_path
        Image.fromarray(result_image).save(overlay_path)
        if run.before_full_path:
            before_pil.save(DATA_DIR / run.before_full_path)
        if run.after_full_path:
            after_pil.save(DATA_DIR / run.after_full_path)

        run.regions_json = json.dumps(serial)
        run.regions_count = len(serial)
        run.change_percentage = float(stats.get("change_percentage") or 0)
        run.changed_pixels = int(stats.get("changed_pixels") or 0)
        run.total_pixels = int(stats.get("total_pixels") or 0)
        db.commit()

        print(
            f"Restored run {run.id}: regions_count={run.regions_count} "
            f"change%={run.change_percentage:.4f}"
        )
        for r in serial:
            print(
                f"  #{r.get('id')} {r.get('ddaChangeType')}/"
                f"{r.get('objectType')} conf={float(r.get('confidence') or 0):.2f} "
                f"area={r.get('area')}"
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
