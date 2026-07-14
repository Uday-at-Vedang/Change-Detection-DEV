"""
Promote a reviewed candidate mask (docs/delhi_eval/labels/candidates/<id>.png)
to a real ground-truth label (docs/delhi_eval/labels/<id>.png) and update its
gt_mask field in manifest.json. Run this AFTER you've looked at the preview
strip and are satisfied the mask is correct (or after touching it up in an
image editor / QGIS and saving over the candidate file).

Usage:
    python scripts/accept_candidate_mask.py --pair-id delhi_0001
    python scripts/accept_candidate_mask.py --all-candidates   # promote every candidate as-is
    python scripts/accept_candidate_mask.py --reject --pair-id delhi_0001  # discard, mark unusable
"""
import argparse
import json
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST_PATH = ROOT / "docs" / "delhi_eval" / "manifest.json"
LABELS_DIR = ROOT / "docs" / "delhi_eval" / "labels"
CANDIDATES_DIR = LABELS_DIR / "candidates"


def _load():
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _save(data):
    MANIFEST_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def accept(pair_id: str, manifest: dict) -> bool:
    candidate = CANDIDATES_DIR / f"{pair_id}.png"
    if not candidate.exists():
        print(f"  {pair_id}: no candidate found at {candidate.relative_to(ROOT)} — skip")
        return False
    final_path = LABELS_DIR / f"{pair_id}.png"
    shutil.copy(candidate, final_path)
    for pair in manifest["pairs"]:
        if pair["pair_id"] == pair_id:
            pair["gt_mask"] = str(final_path.relative_to(ROOT))
            break
    print(f"  {pair_id}: promoted -> {final_path.relative_to(ROOT)}")
    return True


def reject(pair_id: str, manifest: dict) -> bool:
    for pair in manifest["pairs"]:
        if pair["pair_id"] == pair_id:
            pair["notes"] = (pair.get("notes", "") + " [REJECTED: candidate mask unusable, needs manual labeling]").strip()
            print(f"  {pair_id}: marked rejected in notes")
            return True
    print(f"  {pair_id}: not found in manifest")
    return False


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pair-id", default="")
    parser.add_argument("--all-candidates", action="store_true", help="promote every file in labels/candidates/")
    parser.add_argument("--reject", action="store_true", help="mark as rejected instead of promoting")
    args = parser.parse_args()

    manifest = _load()

    if args.all_candidates:
        ids = sorted(p.stem for p in CANDIDATES_DIR.glob("*.png"))
        if not ids:
            raise SystemExit(f"No candidates found in {CANDIDATES_DIR.relative_to(ROOT)}")
        for pair_id in ids:
            accept(pair_id, manifest)
    elif args.pair_id:
        (reject if args.reject else accept)(args.pair_id, manifest)
    else:
        raise SystemExit("Pass --pair-id <id> or --all-candidates")

    _save(manifest)
    labeled = sum(1 for p in manifest["pairs"] if p.get("gt_mask"))
    print(f"\n{labeled}/{len(manifest['pairs'])} pairs now have a gt_mask.")


if __name__ == "__main__":
    main()
