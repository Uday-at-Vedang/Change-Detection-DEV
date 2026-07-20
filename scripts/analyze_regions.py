"""
Quantify the false-positive / hallucination pattern in a set of detected
regions, WITHOUT re-running the model. Reads a saved region list and reports
counts by type, a confidence-bucket histogram, per-type confidence, and a
single "hallucination fraction" metric (low-confidence unclassified regions
as a share of all regions).

Purpose (DDA Grid_54 vs H43X2E1 reports, Jul 2026): give hard numbers behind
"it's still hallucinating" so the fix can be measured, not guessed. Pairs with
scripts/filter_regions.py — analyze first to pick thresholds, then filter.

Input (pick one):
    --in regions.json     JSON list of region dicts (or {"regions": [...]})
    --run-id N            read regions from the app DB (data/satellite_app.db)

Options:
    --halluc-types "Unclassified Ground Change,Other"   types treated as
                        "junk-prone" for the hallucination metric (substring match)
    --halluc-conf 0.55  confidence below which a junk-prone region counts as a
                        likely hallucination

Example:
    python scripts/analyze_regions.py --in regions.json
    python scripts/analyze_regions.py --run-id 37 --halluc-conf 0.5
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

_BUCKETS = [(0.0, 0.25), (0.25, 0.40), (0.40, 0.50), (0.50, 0.75), (0.75, 1.01)]


def _load(args) -> tuple:
    if args.run_id is not None:
        from app.database import SessionLocal
        from app.models import DetectionRun
        db = SessionLocal()
        run = db.query(DetectionRun).filter(DetectionRun.id == args.run_id).first()
        db.close()
        if run is None:
            raise SystemExit(f"No DetectionRun with id={args.run_id}")
        return json.loads(run.regions_json or "[]"), f"DB run #{args.run_id}"
    data = json.loads(Path(args.in_path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("regions", [])
    return data, args.in_path


def _bucket_label(lo, hi):
    return f"{int(lo*100):>3d}-{int(min(hi,1.0)*100):>3d}%"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--in", dest="in_path", default="")
    src.add_argument("--run-id", type=int, default=None)
    parser.add_argument("--halluc-types", default="Unclassified Ground Change,Other")
    parser.add_argument("--halluc-conf", type=float, default=0.55)
    args = parser.parse_args()

    regions, source = _load(args)
    n = len(regions)
    if n == 0:
        raise SystemExit(f"No regions in {source}")

    halluc_subs = [s.strip().lower() for s in args.halluc_types.split(",") if s.strip()]

    print(f"Source: {source}")
    print(f"Total regions: {n}\n")

    # By type
    by_type = {}
    for r in regions:
        t = r.get("objectType", "unknown")
        by_type.setdefault(t, []).append(float(r.get("confidence", 0.0)))
    print("By type (count | mean confidence):")
    for t, confs in sorted(by_type.items(), key=lambda kv: -len(kv[1])):
        print(f"  {t:32s} {len(confs):4d} | {sum(confs)/len(confs):.2f}")

    # Confidence histogram
    print("\nConfidence distribution:")
    for lo, hi in _BUCKETS:
        c = sum(1 for r in regions if lo <= float(r.get("confidence", 0.0)) < hi)
        bar = "#" * c
        print(f"  {_bucket_label(lo, hi)}  {c:4d}  {bar}")

    # Area stats
    areas = sorted(float(r.get("area", 0)) for r in regions)
    if areas:
        med = areas[len(areas) // 2]
        print(f"\nArea (px): min={areas[0]:.0f}  median={med:.0f}  max={areas[-1]:.0f}")

    # Hallucination metric
    halluc = [
        r for r in regions
        if any(s in str(r.get("objectType", "")).lower() for s in halluc_subs)
        and float(r.get("confidence", 0.0)) < args.halluc_conf
    ]
    frac = len(halluc) / n
    print(f"\nHallucination metric:")
    print(f"  junk-prone types: {args.halluc_types}")
    print(f"  low-confidence threshold: <{args.halluc_conf}")
    print(f"  likely hallucinations: {len(halluc)}/{n} = {frac*100:.1f}% of all regions")
    print(f"  -> filtering these leaves {n - len(halluc)} confident region(s).")
    if frac > 0.3:
        print("  ** Over 30% of regions are low-confidence junk — strong FP signal;"
              " apply scripts/filter_regions.py before reporting.")


if __name__ == "__main__":
    main()
