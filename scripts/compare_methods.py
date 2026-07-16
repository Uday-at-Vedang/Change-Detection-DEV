"""
Compare change-detection methods, sensitivities, and fusion modes on a real
before/after image pair, so tuning is based on measurements instead of
guesswork (Phase 3/4 of the accuracy remediation plan).

Without --gt, reports timing/change%/region-count/alignment per config so you
can eyeball which setting looks right for your imagery. With --gt (a binary
PNG mask of the real changed pixels, white = changed), also reports
IoU/Dice/F1/Precision/Recall per config for an objective ranking.

Usage:
    python scripts/compare_methods.py --before before.png --after after.png
    python scripts/compare_methods.py --before b.tif --after a.tif --gt truth.png
    python scripts/compare_methods.py --before b.png --after a.png --out runs/compare
    python scripts/compare_methods.py --before b.png --after a.png \
        --methods "AI-Based Deep Learning,Feature-Based" --sensitivities 0.3,0.5,0.7 \
        --fusions smart_union,hysteresis

    # Batch mode: run every pair in a Delhi eval manifest (docs/delhi_eval/manifest.json).
    # Uses each pair's gt_mask when labeled, otherwise runs without ground truth.
    python scripts/compare_methods.py --manifest docs/delhi_eval/manifest.json --out runs/manifest_scan
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from app.detection_engine import run_detection  # noqa: E402
from app.evaluation.metrics import binary_metrics  # noqa: E402

ALL_METHODS = ["AI-Based Deep Learning", "Feature-Based", "Hybrid Approach", "Hybrid AI",
              "KPCA (Unsupervised)"]
FUSION_CAPABLE = {"AI-Based Deep Learning", "Hybrid AI"}


def _load_image(path: Path):
    """Load an image the same way the app does — GeoTIFFs go through rasterio's
    decimated read (never materializes the full-res array), so a multi-GB
    satellite raster loads safely instead of PIL trying to decode it whole."""
    if path.suffix.lower() in (".tif", ".tiff"):
        from app.dda.geotiff_io import load_rgb_pil
        return load_rgb_pil(path)
    return Image.open(path).convert("RGB")


def _run_one(before, after, method, sensitivity, fusion, gt, before_path=None, after_path=None):
    if fusion:
        os.environ["DETECTION_FUSION"] = fusion
    t0 = time.time()
    mask, _img, stats, regions = run_detection(
        before, after, method=method,
        enable_registration=True, enable_normalization=True,
        detection_sensitivity=sensitivity,
        before_path=before_path, after_path=after_path,
    )
    elapsed = time.time() - t0
    td = stats.get("threshold_debug", {}) or {}
    tile_skip = td.get("tileSkip") or {}
    row = {
        "method": method,
        "sensitivity": sensitivity,
        "fusion": fusion or td.get("fusionMode", "-"),
        "seconds": round(elapsed, 1),
        "changePct": round(stats["change_percentage"], 3),
        "regions": len(regions),
        "alignmentOk": stats.get("alignment_warning") is None,
        "tilesSkipped": f"{tile_skip.get('skippedTiles', 0)}/{tile_skip.get('totalTiles', 0)}"
        if tile_skip.get("totalTiles") else "-",
    }
    if gt is not None:
        m = binary_metrics(mask, gt)
        row.update({"iou": m["iou"], "f1": m["f1"], "precision": m["precision"], "recall": m["recall"]})
    return row, mask


def run_manifest(manifest_path: Path, methods, sensitivities, fusions, out_dir,
                 save_masks: bool = True):
    """Batch mode: run every pair listed in a Delhi eval manifest.json end-to-end.

    Pairs without a labeled gt_mask still run (change%/regions only) so this can
    be smoke-tested before any masks exist — that's the Day 1 wiring check.
    """
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    pairs = data.get("pairs", [])
    if not pairs:
        print(f"No pairs in {manifest_path} — nothing to run. "
              f"Use scripts/build_delhi_manifest.py --add to log pairs first.")
        return

    print(f"Running {len(methods)} method(s) x {len(sensitivities)} sensitivity(ies) "
          f"across {len(pairs)} manifest pair(s)...\n")

    rows = []
    for pair in pairs:
        pair_id = pair["pair_id"]
        before_path = ROOT / pair["before_path"]
        after_path = ROOT / pair["after_path"]
        if not before_path.exists() or not after_path.exists():
            print(f"  {pair_id}: SKIP (image missing on disk)")
            continue
        is_geotiff = before_path.suffix.lower() in (".tif", ".tiff")
        try:
            before = _load_image(before_path)
            after = _load_image(after_path)
        except Exception as exc:  # noqa: BLE001 - report and keep going
            print(f"  {pair_id}: SKIP (failed to load: {exc})")
            continue

        gt = None
        gt_rel = pair.get("gt_mask")
        if gt_rel and (ROOT / gt_rel).exists():
            gt = np.array(Image.open(ROOT / gt_rel).convert("L"))

        for method in methods:
            use_fusions = fusions if method in FUSION_CAPABLE else [None]
            for sensitivity in sensitivities:
                for fusion in use_fusions:
                    row, mask = _run_one(
                        before, after, method, sensitivity, fusion, gt,
                        before_path=str(before_path) if is_geotiff else None,
                        after_path=str(after_path) if is_geotiff else None,
                    )
                    row["pair_id"] = pair_id
                    rows.append(row)
                    tag = f"{pair_id}_{method}_s{sensitivity}" + (f"_{fusion}" if fusion else "")
                    tag = tag.replace(" ", "_").replace("/", "-")
                    extra = " ".join(
                        f"{k}={v}" for k, v in row.items()
                        if k not in ("method", "sensitivity", "fusion", "pair_id")
                    )
                    print(f"  {tag:60s} {extra}")
                    if out_dir and save_masks:
                        Image.fromarray(mask).save(out_dir / f"{tag}_mask.png")

    if out_dir:
        (out_dir / "manifest_report.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nWrote {len(rows)} row(s) to {out_dir / 'manifest_report.json'}")

    labeled_rows = [r for r in rows if "iou" in r]
    if labeled_rows:
        mean_iou = sum(r["iou"] for r in labeled_rows) / len(labeled_rows)
        mean_f1 = sum(r["f1"] for r in labeled_rows) / len(labeled_rows)
        print(f"\nMean over {len(labeled_rows)} labeled row(s): IoU={mean_iou:.3f} F1={mean_f1:.3f}")
    else:
        print(f"\nNo labeled pairs yet ({len(rows)} row(s) ran without ground truth) — "
              f"add masks to docs/delhi_eval/labels/ to get IoU/F1.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--before", default="", help="path to the 'before' image")
    parser.add_argument("--after", default="", help="path to the 'after' image")
    parser.add_argument("--gt", default="", help="optional ground-truth binary change mask PNG")
    parser.add_argument("--manifest", default="",
                        help="path to a Delhi eval manifest.json — batch-runs every pair instead "
                             "of a single --before/--after image")
    parser.add_argument("--methods", default=",".join(ALL_METHODS))
    parser.add_argument("--sensitivities", default="0.3,0.5,0.7")
    parser.add_argument("--fusions", default="",
                        help="comma list e.g. smart_union,hysteresis (AI methods only); "
                             "blank = use current DETECTION_FUSION env/default only")
    parser.add_argument("--out", default="", help="dir to save per-config mask PNGs + report JSON")
    parser.add_argument("--report-only", action="store_true",
                        help="manifest/batch mode: write JSON report only, skip mask PNGs")
    args = parser.parse_args()

    methods = [m.strip() for m in args.methods.split(",") if m.strip()]
    sensitivities = [float(s) for s in args.sensitivities.split(",") if s.strip()]
    fusions = [f.strip() for f in args.fusions.split(",") if f.strip()] or [None]
    out_dir = Path(args.out).resolve() if args.out else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    if args.manifest:
        run_manifest(Path(args.manifest), methods, sensitivities, fusions, out_dir,
                     save_masks=not args.report_only)
        return

    if not args.before or not args.after:
        sys.exit("Either --manifest, or both --before and --after, are required.")

    before_path = Path(args.before)
    after_path = Path(args.after)
    is_geotiff = before_path.suffix.lower() in (".tif", ".tiff")

    print(f"Loading images (GeoTIFFs are downsampled via rasterio, never fully decoded)...")
    before = _load_image(before_path)
    after = _load_image(after_path)
    gt = np.array(Image.open(args.gt).convert("L")) if args.gt else None

    print(f"Comparing on: before={args.before}  after={args.after}"
          f"{'  gt=' + args.gt if args.gt else '  (no ground truth — compare by eye)'}\n")

    rows = []
    for method in methods:
        use_fusions = fusions if method in FUSION_CAPABLE else [None]
        for sensitivity in sensitivities:
            for fusion in use_fusions:
                row, mask = _run_one(
                    before, after, method, sensitivity, fusion, gt,
                    before_path=str(before_path) if is_geotiff else None,
                    after_path=str(after_path) if is_geotiff else None,
                )
                rows.append(row)
                tag = f"{method}_s{sensitivity}" + (f"_{fusion}" if fusion else "")
                tag = tag.replace(" ", "_").replace("/", "-")
                extra = " ".join(
                    f"{k}={v}" for k, v in row.items()
                    if k not in ("method", "sensitivity", "fusion")
                )
                print(f"  {tag:50s} {extra}")
                if out_dir:
                    Image.fromarray(mask).save(out_dir / f"{tag}_mask.png")

    if out_dir:
        (out_dir / "compare_report.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
        print(f"\nWrote masks + compare_report.json to {out_dir}")

    if gt is not None:
        best = max(rows, key=lambda r: r["iou"])
        print(f"\nBest by IoU: {best}")
    else:
        print("\nNo --gt supplied — pick the config whose change% / region count / overlay "
              "(check --out masks) best matches what you know actually changed on the ground.")


if __name__ == "__main__":
    main()
