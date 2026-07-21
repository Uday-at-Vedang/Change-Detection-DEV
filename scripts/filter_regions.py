"""
Post-process detected change regions to cut false-positive "hallucinations"
WITHOUT re-running the model. Operates purely on the saved region list
(objectType / confidence / area), so it is CPU-trivial and safe on any
machine — no imagery load, no deep model.

Motivation (DDA Grid_54 vs H43X2E1 reports, Jul 2026): of 60 detected
regions, ~25-30 were "Unclassified Ground Change" at 24-50% confidence,
clustered in vegetation — these are the visible hallucinations. Dropping
low-confidence unclassified regions removes most of them while keeping the
confident Vegetation / New Construction / Demolition detections.

Input sources (pick one):
    --in regions.json         a JSON list of region dicts (or {"regions": [...]})
    --run-id N                read regions from the app DB (data/satellite_app.db)

Filters (all optional, combine freely):
    --min-confidence 0.0            global confidence floor (0-1)
    --min-area 0                    global minimum area in pixels
    --type-min-conf "Unclassified Ground Change=0.55,Other=0.55"
                                    per-objectType confidence floor — the main
                                    hallucination lever. Type names match the
                                    engine's objectType strings; matching is
                                    case-insensitive substring.
    --drop-types "..."             comma list of objectType substrings to drop entirely

Output:
    --out filtered.json            write the filtered region list (default: print summary only)
    --apply-to-run                 (with --run-id) write filtered regions back to that
                                    DB run. OFF by default — this mutates stored app
                                    data, so it must be requested explicitly.

Examples:
    # Preview what a 55% floor on unclassified regions would remove, from a JSON export
    python scripts/filter_regions.py --in regions.json \\
        --type-min-conf "Unclassified Ground Change=0.55"

    # Same, reading a real run from the DB, writing the cleaned list to a file
    python scripts/filter_regions.py --run-id 37 \\
        --type-min-conf "Unclassified Ground Change=0.55,Other=0.55" \\
        --min-area 1500 --out runs/filtered_run37.json
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _load_from_json(path: Path) -> list:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("regions", [])
    if not isinstance(data, list):
        raise SystemExit(f"{path} does not contain a region list or {{'regions': [...]}}")
    return data


def _load_from_run(run_id: int) -> tuple:
    """Return (regions, run) read from the app DB. Read-only."""
    from app.database import SessionLocal
    from app.models import DetectionRun
    db = SessionLocal()
    run = db.query(DetectionRun).filter(DetectionRun.id == run_id).first()
    if run is None:
        db.close()
        raise SystemExit(f"No DetectionRun with id={run_id} in the database.")
    regions = json.loads(run.regions_json or "[]")
    return regions, run, db


def _parse_type_floors(raw: str) -> list:
    """'A=0.55,B=0.6' -> [('a', 0.55), ('b', 0.6)] (lowercased substrings)."""
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, val = part.rsplit("=", 1)
        out.append((name.strip().lower(), float(val)))
    return out


def _summary_by_type(regions: list) -> dict:
    counts = {}
    for r in regions:
        t = r.get("objectType", "unknown")
        counts[t] = counts.get(t, 0) + 1
    return dict(sorted(counts.items(), key=lambda kv: -kv[1]))


def apply_filters(regions, min_conf, min_area, type_floors, drop_types):
    """Return (kept, removed) region lists. Pure, no side effects."""
    drop_types_l = [d.strip().lower() for d in drop_types if d.strip()]
    kept, removed = [], []
    for r in regions:
        obj = str(r.get("objectType", "")).lower()
        conf = float(r.get("confidence", 0.0))
        area = float(r.get("area", 0))

        reason = None
        if any(d in obj for d in drop_types_l):
            reason = "dropped-type"
        elif conf < min_conf:
            reason = f"conf<{min_conf}"
        elif area < min_area:
            reason = f"area<{min_area}"
        else:
            for name_sub, floor in type_floors:
                if name_sub in obj and conf < floor:
                    reason = f"'{name_sub}' conf<{floor}"
                    break

        if reason:
            rr = dict(r)
            rr["_removedReason"] = reason
            removed.append(rr)
        else:
            kept.append(r)
    return kept, removed


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    src = parser.add_mutually_exclusive_group(required=True)
    src.add_argument("--in", dest="in_path", default="", help="JSON file of regions")
    src.add_argument("--run-id", type=int, default=None, help="read regions from the app DB")
    parser.add_argument("--min-confidence", type=float, default=0.0)
    parser.add_argument("--min-area", type=float, default=0.0)
    parser.add_argument("--type-min-conf", default="",
                        help='per-type confidence floors, e.g. "Unclassified Ground Change=0.55"')
    parser.add_argument("--drop-types", default="",
                        help="comma list of objectType substrings to drop entirely")
    parser.add_argument("--out", default="", help="write filtered regions to this JSON file")
    parser.add_argument("--apply-to-run", action="store_true",
                        help="(with --run-id) write filtered regions back to the DB run")
    args = parser.parse_args()

    db = run = None
    if args.run_id is not None:
        regions, run, db = _load_from_run(args.run_id)
        source = f"DB run #{args.run_id} ({run.title!r})"
    else:
        regions = _load_from_json(Path(args.in_path))
        source = args.in_path

    type_floors = _parse_type_floors(args.type_min_conf)
    drop_types = [d for d in args.drop_types.split(",") if d.strip()]

    kept, removed = apply_filters(
        regions, args.min_confidence, args.min_area, type_floors, drop_types)

    print(f"Source: {source}")
    print(f"Regions in:  {len(regions)}")
    print(f"Regions kept: {len(kept)}   removed: {len(removed)}\n")

    print("By type — before:")
    for t, n in _summary_by_type(regions).items():
        print(f"  {t:32s} {n}")
    print("\nBy type — after:")
    for t, n in _summary_by_type(kept).items():
        print(f"  {t:32s} {n}")

    if removed:
        print(f"\nRemoved {len(removed)} region(s). Reason breakdown:")
        reasons = {}
        for r in removed:
            reasons[r["_removedReason"]] = reasons.get(r["_removedReason"], 0) + 1
        for reason, n in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f"  {reason:32s} {n}")

    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        Path(args.out).write_text(json.dumps(kept, indent=2), encoding="utf-8")
        print(f"\nWrote {len(kept)} kept region(s) to {args.out}")

    if args.apply_to_run:
        if run is None:
            raise SystemExit("--apply-to-run requires --run-id")
        run.regions_json = json.dumps(kept)
        run.regions_count = len(kept)
        db.commit()
        print(f"\nApplied: DB run #{args.run_id} now has {len(kept)} regions "
              f"(was {len(regions)}). Re-generate its report to see the cleaned result.")

    if db is not None:
        db.close()


if __name__ == "__main__":
    main()
