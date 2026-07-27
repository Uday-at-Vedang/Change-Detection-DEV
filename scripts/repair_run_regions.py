"""Re-run detection on a saved report pair and rewrite regions_json.

Used to repair reports that lost regions to the aggressive Other filter.
Example:
  python scripts/repair_run_regions.py --run-id 47
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
    args = ap.parse_args()

    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env", override=True)

    from app.database import SessionLocal, DATA_DIR
    from app.models import DetectionRun
    from app.detection_engine import run_detection
    from app.dda.detect_service import _serialize_regions, _filter_weak_other_regions
    from app.dda.geo_regions import enrich_regions_geo
    from app.dda.change_type_map import enrich_region_for_dda

    db = SessionLocal()
    try:
        run = db.query(DetectionRun).filter(DetectionRun.id == args.run_id).first()
        if not run:
            print(f"Run {args.run_id} not found")
            return 1
        before_p = DATA_DIR / run.before_full_path
        after_p = DATA_DIR / run.after_full_path
        if not before_p.is_file() or not after_p.is_file():
            print("Missing before/after overlay PNGs for this run")
            return 1

        before = Image.open(before_p).convert("RGB")
        after = Image.open(after_p).convert("RGB")
        print(f"Re-detecting run {run.id} at {before.size} ...")

        def _prog(pct, stage):
            print(f"  [{pct:3d}%] {stage}", flush=True)

        mask, vis, stats, regions = run_detection(
            before, after,
            method=run.method or "AI-Based Deep Learning",
            enable_registration=False,
            enable_normalization=True,
            detection_sensitivity=0.5,
            max_size=max(before.size),
            on_progress=_prog,
        )
        print(f"  engine regions={len(regions)} change%={stats.get('change_percentage')}")

        serial = _serialize_regions(regions)
        serial = [enrich_region_for_dda(r) for r in serial]
        before_n = len(serial)
        serial = _filter_weak_other_regions(serial)
        print(f"  after Other filter: {before_n} -> {len(serial)}")

        w = int(stats.get("image_width") or before.size[0])
        h = int(stats.get("image_height") or before.size[1])
        serial = enrich_regions_geo(serial, img_width=w, img_height=h, bounds=None, geo=None)

        # Refresh overlay image too so boxes match regions
        out_overlay = DATA_DIR / run.overlay_path
        Image.fromarray(vis).save(out_overlay)

        run.regions_json = json.dumps(serial)
        run.regions_count = len(serial)
        run.change_percentage = float(stats.get("change_percentage") or run.change_percentage)
        run.changed_pixels = int(stats.get("changed_pixels") or run.changed_pixels)
        run.total_pixels = int(stats.get("total_pixels") or run.total_pixels)
        db.commit()
        print(f"Updated run {run.id}: regions_count={run.regions_count}")
        for r in serial[:20]:
            print(
                f"  #{r.get('id')} {r.get('ddaChangeType')} "
                f"conf={float(r.get('confidence') or 0):.2f} area={r.get('area')}"
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
