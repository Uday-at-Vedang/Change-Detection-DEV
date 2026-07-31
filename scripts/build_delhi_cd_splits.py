"""
Day 4 (Uday): materialize 70/15/15 train/val/test splits under data/delhi_cd/.

Writes lightweight manifests that point at Priyanka's Delhi pairs (no GeoTIFF
copies). Same seed / fractions as scripts/finetune_adaptformer.py so smoke
training and the on-disk split stay aligned.

Usage:
    python scripts/build_delhi_cd_splits.py
    python scripts/build_delhi_cd_splits.py --manifest docs/delhi_eval/manifest.json \\
        --out data/delhi_cd --seed 0
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.evaluation.delhi_eval import DelhiEvalNotReady, load_manifest  # noqa: E402


def _training_use_ok(pair_id: str) -> bool:
    """False only when the pack's meta.json explicitly opts out (``training_use: false``).

    Evidence 2026-07-31: the 9 un-orthorectified drone pairs (NCC 0.07-0.61)
    measurably degraded a held-out fine-tune (frozen test F1 0.589 without
    them vs 0.487 with) — see docs/ACCURACY_IMPROVE_STATUS.md. Their meta.json
    now records ``training_use: false`` so a plain rerun of this script can't
    silently re-include them; pass ``--include-low-quality`` to override.
    """
    meta_path = ROOT / "docs" / "delhi_eval" / "dda_labeling" / pair_id / "meta.json"
    if not meta_path.is_file():
        return True
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return True
    return meta.get("training_use", True) is not False


def _labeled_pairs(manifest: Path, min_change_frac: float = 0.0,
                   include_low_quality: bool = False) -> list[dict]:
    import numpy as np
    from PIL import Image

    data = load_manifest(manifest, required=True)
    pairs = []
    dropped = []
    excluded_low_quality = []
    for pair in data.get("pairs", []):
        pair_id = pair.get("pair_id") or pair.get("id")
        before = pair.get("before_path") or pair.get("before")
        after = pair.get("after_path") or pair.get("after")
        gt = pair.get("gt_mask")
        if not (pair_id and before and after):
            continue
        if not include_low_quality and not _training_use_ok(pair_id):
            excluded_low_quality.append(pair_id)
            continue
        if not gt:
            auto = ROOT / "docs" / "delhi_eval" / "labels" / f"{pair_id}.png"
            gt = str(auto.relative_to(ROOT)).replace("\\", "/") if auto.is_file() else None
        if not gt:
            continue
        if not (ROOT / before).is_file() or not (ROOT / after).is_file():
            continue
        if not (ROOT / gt).is_file():
            continue
        arr = np.array(Image.open(ROOT / gt).convert("L"))
        frac = float((arr > 127).mean())
        if min_change_frac > 0 and frac < min_change_frac:
            dropped.append(pair_id)
            continue
        pairs.append({
            "pair_id": pair_id,
            "before_path": before.replace("\\", "/"),
            "after_path": after.replace("\\", "/"),
            "gt_mask": gt.replace("\\", "/"),
            "change_types": pair.get("change_types") or [],
            "notes": pair.get("notes") or "",
            "change_frac": frac,
        })
    if excluded_low_quality:
        print(f"Auto-excluded {len(excluded_low_quality)} training_use=false pair(s): "
              f"{excluded_low_quality}")
    if dropped:
        print(f"Dropped {len(dropped)} empty/near-empty GT pairs: {dropped}")
    return pairs


def split_pairs(pairs: list[dict], seed: int = 0,
                train_frac: float = 0.70, val_frac: float = 0.15,
                stratify: bool = False, frozen_test_ids: list[str] | None = None):
    """70/15/15 split. With stratify=True, balance change density across splits.

    ``frozen_test_ids``, when given, pins those pair_ids to the test split every
    time regardless of what else is added to/removed from the labeled pool, so a
    frozen model/threshold's test F1 stays comparable across days. Any frozen id
    not currently in ``pairs`` (label missing this run) is skipped with a
    warning rather than silently producing a smaller/different test set.
    """
    n = len(pairs)
    if n < 3:
        raise SystemExit(f"Need at least 3 labeled pairs for 70/15/15; got {n}")

    if frozen_test_ids:
        by_id = {p["pair_id"]: p for p in pairs}
        forced_test = [by_id[pid] for pid in frozen_test_ids if pid in by_id]
        missing = [pid for pid in frozen_test_ids if pid not in by_id]
        if missing:
            print(f"WARNING: frozen test id(s) not in labeled pool this run: {missing}")
        remaining = [p for p in pairs if p["pair_id"] not in set(frozen_test_ids)]
        # Two-way train/val split of the remaining pool (no test carve-out here —
        # the frozen ids above are the entire test set, always, every run).
        rng = random.Random(seed)
        if stratify:
            ordered = sorted(remaining, key=lambda p: p.get("change_frac", 0.0))
        else:
            ordered = list(remaining)
            rng.shuffle(ordered)
        n_val = max(1, int(round(len(remaining) * val_frac / (train_frac + val_frac)))) if remaining else 0
        if stratify:
            # Take every Nth pair across the density-sorted order for val, so val
            # still spans easy/medium/hard rather than clustering at one end.
            step = max(1, len(ordered) // max(1, n_val))
            val_idx = set(range(0, len(ordered), step)[:n_val])
            val = [p for i, p in enumerate(ordered) if i in val_idx]
            train = [p for i, p in enumerate(ordered) if i not in val_idx]
        else:
            val = ordered[:n_val]
            train = ordered[n_val:]
        return train, val, forced_test

    rng = random.Random(seed)
    n_test = max(1, int(round(n * (1.0 - train_frac - val_frac))))
    n_val = max(1, int(round(n * val_frac)))
    if n_test + n_val >= n:
        n_test = max(1, n // 5)
        n_val = max(1, n // 5)

    if not stratify:
        idx = list(range(n))
        rng.shuffle(idx)
        test_idx = set(idx[:n_test])
        val_idx = set(idx[n_test:n_test + n_val])
        train = [pairs[i] for i in range(n) if i not in test_idx and i not in val_idx]
        val = [pairs[i] for i in range(n) if i in val_idx]
        test = [pairs[i] for i in range(n) if i in test_idx]
        return train, val, test

    # Stratified by change_frac tertiles so val/test are not all "easy dense" scenes
    ordered = sorted(pairs, key=lambda p: p.get("change_frac", 0.0))
    buckets: list[list[dict]] = [[], [], []]
    for i, p in enumerate(ordered):
        buckets[min(2, (i * 3) // max(n, 1))].append(p)

    train, val, test = [], [], []
    n_train_target = n - n_test - n_val
    for bucket in buckets:
        rng.shuffle(bucket)
        nb = len(bucket)
        # Proportional take from each density band
        bt = max(0, int(round(nb * n_test / n)))
        bv = max(0, int(round(nb * n_val / n)))
        # Ensure at least one val/test from a band when the band is large enough
        if nb >= 3:
            bt = max(1, bt)
            bv = max(1, bv)
        if bt + bv > nb:
            bt = min(bt, max(0, nb - 1))
            bv = min(bv, max(0, nb - bt))
        test.extend(bucket[:bt])
        val.extend(bucket[bt:bt + bv])
        train.extend(bucket[bt + bv:])

    # Repair global counts without fully reshuffling (preserve density mix)
    def _steal(src: list, dst: list, k: int):
        for _ in range(k):
            if not src:
                break
            dst.append(src.pop())

    _steal(train, test, n_test - len(test))
    _steal(test, train, len(test) - n_test)
    _steal(train, val, n_val - len(val))
    _steal(val, train, len(val) - n_val)
    # Final size clamp
    while len(test) > n_test and test:
        train.append(test.pop())
    while len(val) > n_val and val:
        train.append(val.pop())
    while len(train) > n_train_target and train:
        if len(val) < n_val:
            val.append(train.pop())
        elif len(test) < n_test:
            test.append(train.pop())
        else:
            break

    print(
        "Stratified by change density: "
        f"train_mean%={100 * sum(p['change_frac'] for p in train) / max(len(train), 1):.2f} "
        f"val_mean%={100 * sum(p['change_frac'] for p in val) / max(len(val), 1):.2f} "
        f"test_mean%={100 * sum(p['change_frac'] for p in test) / max(len(test), 1):.2f}"
    )
    return train, val, test


def _existing_hard_negatives(out_dir: Path, name: str) -> list[dict]:
    """Hard-negative entries already in ``<out_dir>/<name>/manifest.json``, if any.

    ``mine_hard_negatives.py`` appends tiles directly to a split's
    ``train/manifest.json`` — outside the ``docs/delhi_eval/manifest.json``
    source this script regenerates from. Without this, every rerun of
    ``build_delhi_cd_splits.py`` silently wipes them (bug found 2026-07-31:
    cost wed_retrain's 5 mined tiles from both the official and an ablation
    split, understating the ablation's F1 vs wed_retrain until restored).
    """
    path = out_dir / name / "manifest.json"
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return [p for p in data.get("pairs", [])
            if "hard_negative" in (p.get("change_types") or [])]


def _write_split_dir(out_dir: Path, name: str, pairs: list[dict],
                     preserve_hard_negatives: bool = False) -> Path:
    split_dir = out_dir / name
    split_dir.mkdir(parents=True, exist_ok=True)
    if preserve_hard_negatives:
        existing_ids = {p["pair_id"] for p in pairs}
        carried = [hn for hn in _existing_hard_negatives(out_dir, name)
                  if hn.get("pair_id") not in existing_ids]
        if carried:
            print(f"Preserved {len(carried)} existing hard-negative tile(s) in {name}: "
                  f"{[p['pair_id'] for p in carried]}")
            pairs = pairs + carried
    manifest = {
        "version": 1,
        "split": name,
        "n_pairs": len(pairs),
        "pairs": pairs,
    }
    path = split_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    ids_path = split_dir / "pair_ids.txt"
    ids_path.write_text("\n".join(p["pair_id"] for p in pairs) + "\n", encoding="utf-8")
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--manifest", default="docs/delhi_eval/manifest.json")
    parser.add_argument("--out", default="data/delhi_cd")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--min-change-frac", type=float, default=0.001,
                        help="drop GT masks with change fraction below this (empty labels)")
    parser.add_argument("--stratify", action="store_true",
                        help="balance change density across train/val/test (fixes Val>>Test gap)")
    parser.add_argument(
        "--freeze-test-ids", default="data/delhi_cd/frozen_test_ids.json",
        help=(
            "JSON file listing pair_ids to always assign to test, so a frozen "
            "model/threshold's test F1 stays comparable across days even as "
            "labeled pairs are added/removed. Pass '' to disable and let the "
            "splitter pick test freely (NOT recommended once a model/threshold "
            "has been calibrated against a specific test set)."
        ),
    )
    parser.add_argument(
        "--exclude-prefix", action="append", default=[],
        help=(
            "Drop any pair whose pair_id starts with this prefix (repeatable). "
            "Use for ablation splits, e.g. --exclude-prefix dda_before --exclude-prefix "
            "dda_after --exclude-prefix dda_1_2 to exclude the un-orthorectified drone "
            "pairs and isolate their effect on F1. Combine with a distinct --out so this "
            "never overwrites the official split."
        ),
    )
    parser.add_argument(
        "--include-low-quality", action="store_true",
        help=(
            "Include pairs whose meta.json sets training_use=false (currently the 9 "
            "un-orthorectified drone pairs, confirmed to hurt F1 — see "
            "docs/ACCURACY_IMPROVE_STATUS.md). Off by default; only pass this "
            "deliberately, e.g. to re-test after orthomosaics are available."
        ),
    )
    args = parser.parse_args()

    manifest = (ROOT / args.manifest).resolve() if not Path(args.manifest).is_absolute() else Path(args.manifest)
    try:
        pairs = _labeled_pairs(manifest, min_change_frac=args.min_change_frac,
                               include_low_quality=args.include_low_quality)
    except DelhiEvalNotReady as exc:
        raise SystemExit(str(exc)) from exc

    if args.exclude_prefix:
        before_n = len(pairs)
        pairs = [p for p in pairs if not any(
            (p.get("pair_id") or "").startswith(pfx) for pfx in args.exclude_prefix)]
        print(f"Excluded {before_n - len(pairs)} pair(s) matching prefixes {args.exclude_prefix}")

    if not pairs:
        raise SystemExit("No labeled Delhi pairs found — cannot build splits.")

    frozen_test_ids = None
    if args.freeze_test_ids:
        freeze_path = ROOT / args.freeze_test_ids
        if freeze_path.is_file():
            frozen_test_ids = json.loads(freeze_path.read_text(encoding="utf-8"))["test_pair_ids"]
            print(f"Freezing test split to {len(frozen_test_ids)} pair(s) from {freeze_path.name}")
        else:
            print(f"NOTE: {freeze_path} not found — test split will NOT be frozen this run.")

    train, val, test = split_pairs(
        pairs, seed=args.seed, stratify=args.stratify, frozen_test_ids=frozen_test_ids)
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_split_dir(out_dir, "train", train, preserve_hard_negatives=True)
    _write_split_dir(out_dir, "val", val)
    _write_split_dir(out_dir, "test", test)

    def _mean_frac(ps):
        return round(sum(p.get("change_frac", 0) for p in ps) / max(len(ps), 1), 6)

    summary = {
        "version": 1,
        "source_manifest": str(manifest.relative_to(ROOT)).replace("\\", "/"),
        "seed": args.seed,
        "split": "70/15/15",
        "stratified": bool(args.stratify),
        "min_change_frac": args.min_change_frac,
        "n_labeled": len(pairs),
        "n_train": len(train),
        "n_val": len(val),
        "n_test": len(test),
        "change_frac_mean": {
            "train": _mean_frac(train),
            "val": _mean_frac(val),
            "test": _mean_frac(test),
        },
        "train": [p["pair_id"] for p in train],
        "val": [p["pair_id"] for p in val],
        "test": [p["pair_id"] for p in test],
    }
    (out_dir / "split.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    (out_dir / "README.md").write_text(
        "# Delhi CD splits (Day 4)\n\n"
        "70/15/15 train/val/test over labeled pairs from `docs/delhi_eval/`.\n"
        "Manifests store repo-relative paths (no GeoTIFF copies).\n\n"
        f"- seed={args.seed}\n"
        f"- train={len(train)} val={len(val)} test={len(test)}\n"
        "- Built by `scripts/build_delhi_cd_splits.py`\n"
        "- Consumed by `scripts/finetune_adaptformer.py --delhi-cd data/delhi_cd`\n",
        encoding="utf-8",
    )

    print(f"Labeled pairs: {len(pairs)}")
    print(f"train={len(train)} val={len(val)} test={len(test)} (seed={args.seed})")
    print(f"Wrote {out_dir / 'split.json'}")
    for name in ("train", "val", "test"):
        print(f"  {out_dir / name / 'manifest.json'}")


if __name__ == "__main__":
    main()
