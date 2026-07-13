"""
Build and validate the Delhi evaluation manifest (docs/delhi_eval/manifest.json)
that drives real-imagery accuracy measurement (Day 1 of the accuracy sprint).

Usage:
    python scripts/build_delhi_manifest.py --init
    python scripts/build_delhi_manifest.py --scan
    python scripts/build_delhi_manifest.py --add \\
        --before library_sources/2024/site_a.tif --after library_sources/2026/site_a.tif \\
        --zone "South Delhi" --gsd 0.3 --change-types building,road \\
        --date-before 2024-03-10 --date-after 2026-02-18
    python scripts/build_delhi_manifest.py --list
    python scripts/build_delhi_manifest.py --validate

See docs/delhi_eval/README.md for the manifest schema.
"""
import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "docs" / "delhi_eval" / "manifest.json"
LABELS_DIR = ROOT / "docs" / "delhi_eval" / "labels"
LIBRARY_ROOT = ROOT / "library_sources"

IMAGE_EXTS = {".tif", ".tiff", ".png", ".jpg", ".jpeg"}
CHANGE_TYPES = {"building", "road", "vegetation", "mixed_gsd", "other"}
REQUIRED_COVERAGE = {"building", "road", "vegetation", "mixed_gsd"}
MIN_PAIRS = 30


def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        return {"pairs": []}
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def save_manifest(data: dict) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST_PATH.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _sync_gt_masks(data: dict) -> None:
    """Fill in gt_mask for any pair whose labels/<pair_id>.png now exists."""
    for pair in data["pairs"]:
        mask_path = LABELS_DIR / f"{pair['pair_id']}.png"
        pair["gt_mask"] = str(mask_path.relative_to(ROOT)) if mask_path.exists() else None


def cmd_init(_args) -> None:
    MANIFEST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LABELS_DIR.mkdir(parents=True, exist_ok=True)
    gitkeep = LABELS_DIR / ".gitkeep"
    if not gitkeep.exists():
        gitkeep.touch()
    if MANIFEST_PATH.exists():
        print(f"Already exists: {MANIFEST_PATH.relative_to(ROOT)}")
        return
    save_manifest({"pairs": []})
    print(f"Created {MANIFEST_PATH.relative_to(ROOT)} and {LABELS_DIR.relative_to(ROOT)}/")


def cmd_scan(_args) -> None:
    if not LIBRARY_ROOT.exists():
        print(f"No library_sources/ found at {LIBRARY_ROOT}")
        return
    found = False
    for year_dir in sorted(p for p in LIBRARY_ROOT.iterdir() if p.is_dir()):
        images = sorted(
            p for p in year_dir.rglob("*")
            if p.is_file() and p.suffix.lower() in IMAGE_EXTS
        )
        if not images:
            continue
        found = True
        print(f"\n{year_dir.name}/  ({len(images)} image(s))")
        for img in images:
            size_mb = img.stat().st_size / (1024 * 1024)
            print(f"  {img.relative_to(ROOT)}  ({size_mb:.1f} MB)")
    if not found:
        print(
            "No images found under library_sources/<year>/. Copy Delhi before/after "
            "imagery in first (see library_sources/README.md), then re-run --scan."
        )


def cmd_add(args) -> None:
    if not args.before or not args.after:
        sys.exit("--add requires --before and --after")
    if not args.change_types:
        sys.exit("--add requires --change-types (comma list from: " + ", ".join(sorted(CHANGE_TYPES)) + ")")

    change_types = [t.strip() for t in args.change_types.split(",") if t.strip()]
    bad = set(change_types) - CHANGE_TYPES
    if bad:
        sys.exit(f"Unknown change type(s): {sorted(bad)}. Allowed: {sorted(CHANGE_TYPES)}")

    before_path = Path(args.before)
    after_path = Path(args.after)
    before_abs = before_path if before_path.is_absolute() else ROOT / before_path
    after_abs = after_path if after_path.is_absolute() else ROOT / after_path
    if not before_abs.exists():
        sys.exit(f"--before path does not exist: {before_abs}")
    if not after_abs.exists():
        sys.exit(f"--after path does not exist: {after_abs}")

    data = load_manifest()
    before_str = str(before_path)
    after_str = str(after_path)
    for pair in data["pairs"]:
        if pair["before_path"] == before_str and pair["after_path"] == after_str:
            sys.exit(f"Pair already logged as {pair['pair_id']}")

    next_id = len(data["pairs"]) + 1
    pair_id = f"delhi_{next_id:04d}"
    data["pairs"].append({
        "pair_id": pair_id,
        "before_path": before_str,
        "after_path": after_str,
        "date_before": args.date_before or None,
        "date_after": args.date_after or None,
        "gsd": args.gsd,
        "zone": args.zone or None,
        "change_types": change_types,
        "gt_mask": None,
        "notes": args.notes or "",
    })
    save_manifest(data)
    print(f"Added {pair_id}: {before_str} -> {after_str}  tags={change_types}")
    print(f"Total pairs: {len(data['pairs'])}")


def cmd_list(_args) -> None:
    data = load_manifest()
    _sync_gt_masks(data)
    save_manifest(data)
    if not data["pairs"]:
        print("No pairs logged yet. Use --add to log one, or --init first.")
        return
    for pair in data["pairs"]:
        labeled = "labeled" if pair["gt_mask"] else "unlabeled"
        print(f"  {pair['pair_id']:12s} {pair['zone'] or '-':20s} "
              f"{','.join(pair['change_types']):30s} {labeled}")
    print(f"\nTotal: {len(data['pairs'])} pairs")


def cmd_validate(_args) -> None:
    data = load_manifest()
    _sync_gt_masks(data)
    save_manifest(data)
    pairs = data["pairs"]
    n = len(pairs)
    print(f"Pairs logged: {n} (target: >= {MIN_PAIRS})")
    if n < MIN_PAIRS:
        print(f"  SHORT by {MIN_PAIRS - n} pair(s)")

    tag_counts = Counter(t for pair in pairs for t in pair["change_types"])
    print("\nChange-type coverage:")
    for t in sorted(REQUIRED_COVERAGE):
        count = tag_counts.get(t, 0)
        flag = "OK" if count > 0 else "MISSING"
        print(f"  {t:12s} {count:3d} pair(s)  [{flag}]")

    missing_files = [
        pair["pair_id"] for pair in pairs
        if not (ROOT / pair["before_path"]).exists() or not (ROOT / pair["after_path"]).exists()
    ]
    if missing_files:
        print(f"\nBroken paths (image no longer on disk): {missing_files}")

    unlabeled = [pair["pair_id"] for pair in pairs if not pair["gt_mask"]]
    if unlabeled:
        print(f"\nStill need GT masks ({len(unlabeled)}): {unlabeled[:10]}"
              f"{' ...' if len(unlabeled) > 10 else ''}")

    ok = n >= MIN_PAIRS and not (REQUIRED_COVERAGE - tag_counts.keys()) and not missing_files
    print(f"\n{'PASS' if ok else 'NOT READY'} for baseline report.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--init", action="store_true", help="create manifest.json + labels/")
    parser.add_argument("--scan", action="store_true", help="list images under library_sources/")
    parser.add_argument("--add", action="store_true", help="log a new pair")
    parser.add_argument("--list", action="store_true", help="list logged pairs")
    parser.add_argument("--validate", action="store_true", help="check coverage / readiness")
    parser.add_argument("--before", default="", help="path to before image (for --add)")
    parser.add_argument("--after", default="", help="path to after image (for --add)")
    parser.add_argument("--date-before", default="")
    parser.add_argument("--date-after", default="")
    parser.add_argument("--gsd", type=float, default=None)
    parser.add_argument("--zone", default="")
    parser.add_argument("--change-types", default="",
                        help="comma list from: " + ", ".join(sorted(CHANGE_TYPES)))
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    actions = {
        "init": cmd_init, "scan": cmd_scan, "add": cmd_add,
        "list": cmd_list, "validate": cmd_validate,
    }
    ran = False
    for name, fn in actions.items():
        if getattr(args, name):
            fn(args)
            ran = True
    if not ran:
        parser.print_help()


if __name__ == "__main__":
    main()
