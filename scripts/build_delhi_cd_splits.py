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


def _labeled_pairs(manifest: Path, min_change_frac: float = 0.0) -> list[dict]:
    import numpy as np
    from PIL import Image

    data = load_manifest(manifest, required=True)
    pairs = []
    dropped = []
    for pair in data.get("pairs", []):
        pair_id = pair.get("pair_id") or pair.get("id")
        before = pair.get("before_path") or pair.get("before")
        after = pair.get("after_path") or pair.get("after")
        gt = pair.get("gt_mask")
        if not (pair_id and before and after):
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
    if dropped:
        print(f"Dropped {len(dropped)} empty/near-empty GT pairs: {dropped}")
    return pairs


def split_pairs(pairs: list[dict], seed: int = 0,
                train_frac: float = 0.70, val_frac: float = 0.15,
                stratify: bool = False):
    """70/15/15 split. With stratify=True, balance change density across splits."""
    n = len(pairs)
    if n < 3:
        raise SystemExit(f"Need at least 3 labeled pairs for 70/15/15; got {n}")

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


def _write_split_dir(out_dir: Path, name: str, pairs: list[dict]) -> Path:
    split_dir = out_dir / name
    split_dir.mkdir(parents=True, exist_ok=True)
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
    args = parser.parse_args()

    manifest = (ROOT / args.manifest).resolve() if not Path(args.manifest).is_absolute() else Path(args.manifest)
    try:
        pairs = _labeled_pairs(manifest, min_change_frac=args.min_change_frac)
    except DelhiEvalNotReady as exc:
        raise SystemExit(str(exc)) from exc

    if not pairs:
        raise SystemExit("No labeled Delhi pairs found — cannot build splits.")

    train, val, test = split_pairs(pairs, seed=args.seed, stratify=args.stratify)
    out_dir = ROOT / args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    _write_split_dir(out_dir, "train", train)
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
