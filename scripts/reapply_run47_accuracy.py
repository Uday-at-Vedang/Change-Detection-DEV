"""Re-detect run 47 from stored overlay PNGs with updated engine."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env", override=True)

    from app.database import SessionLocal, DATA_DIR
    from app.models import DetectionRun
    from app.dda.detect_service import _serialize_regions, _filter_weak_other_regions
    from app.detection_engine import run_detection

    db = SessionLocal()
    try:
        run = db.query(DetectionRun).filter(DetectionRun.id == 47).first()
        if not run:
            print("Run 47 not found")
            return 1
        before = Image.open(DATA_DIR / run.before_full_path).convert("RGB")
        after = Image.open(DATA_DIR / run.after_full_path).convert("RGB")
        print(f"Input {before.size}")

        def _prog(pct, stage):
            print(f"  [{pct:3d}%] {stage}", flush=True)

        _mask, result_image, stats, change_regions = run_detection(
            before,
            after,
            method=run.method or "AI-Based Deep Learning",
            enable_registration=True,
            enable_normalization=True,
            detection_sensitivity=0.5,
            max_size=max(before.size),
            on_progress=_prog,
        )
        regions = _filter_weak_other_regions(_serialize_regions(change_regions))
        print(
            f"change%={stats.get('change_percentage')} regions={len(regions)} "
            f"windowed={stats.get('params', {}).get('windowed')}"
        )
        # Save artifacts
        out = ROOT / "runs" / "accuracy_improve_20260721"
        out.mkdir(parents=True, exist_ok=True)
        overlay_img = (
            Image.fromarray(result_image)
            if isinstance(result_image, np.ndarray)
            else result_image
        )
        overlay_img.save(out / "overlay.png")
        if _mask is not None:
            Image.fromarray(_mask).save(out / "change_mask.png")
        (out / "summary.json").write_text(
            json.dumps(
                {
                    "change_percentage": stats.get("change_percentage"),
                    "n_regions": len(regions),
                    "regions": regions[:30],
                    "params": stats.get("params"),
                    "threshold_debug": stats.get("threshold_debug"),
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        # Update DB run 47
        run.change_percentage = float(stats.get("change_percentage") or 0)
        run.regions_count = len(regions)
        run.regions_json = json.dumps(regions)
        # overwrite overlay
        overlay_abs = DATA_DIR / run.overlay_path
        overlay_img.save(overlay_abs)
        db.commit()
        print(f"Updated run 47 -> {overlay_abs}")
        print(f"Artifacts -> {out}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
