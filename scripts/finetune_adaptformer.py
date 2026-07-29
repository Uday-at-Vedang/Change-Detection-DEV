"""
Fine-tune AdaptFormer on Delhi change-detection tiles.

Priority improvements (v2):
  1. Validate logit→prob (2-class softmax, change = channel 1) + print stats
  2. Auto threshold search (fixed grid + score quantiles); freeze thr with best ckpt
  3. Positive-tile oversampling + Focal+Dice / CE+Dice losses
  4. Lazy tile index (pair_idx, x, y) — no duplicated arrays in RAM
  5. Track Precision / Recall / IoU (not just F1)
  6. Save Before/After/GT/Pred/Prob panels each epoch
  7. Stronger aug + ReduceLROnPlateau

Run:
    python scripts/build_delhi_cd_splits.py --min-change-frac 0.001 --stratify
    python scripts/finetune_adaptformer.py --delhi-cd data/delhi_cd --preset v2
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.evaluation.delhi_eval import DelhiEvalNotReady, dummy_delhi_pairs, iter_delhi_pairs  # noqa: E402
from app.evaluation.metrics import binary_metrics  # noqa: E402

_MODEL_ID = "deepang/adaptformer-LEVIR-CD"
_TILE = 256

_DAY5_PRESET = {
    "epochs": 30,
    "lr": 3e-5,
    "batch_size": 2,
    "augment": True,
    "stride": 128,
    "early_stop_patience": 8,
    "loss": "bce_dice",
    "exclude_empty": False,
    "full_resize": False,
    "pos_oversample": 1,
    "visualize": False,
}

_FIX_PRESET = {
    "epochs": 25,
    "lr": 1e-4,
    "batch_size": 2,
    "augment": True,
    "stride": 64,
    "early_stop_patience": 6,
    "loss": "ce",
    "exclude_empty": True,
    "full_resize": True,
    "min_change_frac": 0.001,
    "pos_oversample": 1,
    "visualize": False,
}

# Claude priority plan — target Val F1 0.5+ and closer Val/Test gap
_V2_PRESET = {
    "epochs": 20,
    "lr": 5e-5,
    "batch_size": 2,
    "augment": True,
    "stride": 64,
    "early_stop_patience": 7,
    "loss": "focal_dice",
    "exclude_empty": True,
    "full_resize": True,
    "min_change_frac": 0.001,
    "pos_oversample": 3,
    "min_tile_change": 0.005,
    "visualize": True,
    "scheduler": True,
}

# Recall / FN-focused follow-up (Test F1>0.50, R>0.45 target)
_V3_PRESET = {
    "epochs": 20,
    "lr": 3e-5,
    "batch_size": 2,
    "augment": True,
    "stride": 64,
    "early_stop_patience": 6,
    "loss": "tversky",
    "exclude_empty": True,
    "full_resize": True,
    "min_change_frac": 0.001,
    "pos_oversample": 4,
    "min_tile_change": 0.01,
    "change_centered": True,
    "visualize": False,  # enable with --visualize; keeps CPU train faster
    "scheduler": True,
    "thr_min": 0.2,
    "thr_max": 0.7,
    "thr_objective": "fbeta",  # F_beta=1.5 favors recall on val thr pick
    "warm_start": "runs/finetune_v2/20260716_210208/best",
}

# v4: ONE change vs frozen v3 — stronger positive-tile sampling only (loss/aug unchanged)
_V4_PRESET = {
    "epochs": 12,
    "lr": 2e-5,
    "batch_size": 2,
    "augment": True,
    "stride": 64,
    "early_stop_patience": 5,
    "loss": "tversky",          # unchanged from v3
    "exclude_empty": True,
    "full_resize": True,
    "min_change_frac": 0.001,
    "pos_oversample": 6,        # ↑ from 4
    "min_tile_change": 0.02,    # ↑ from 0.01 — stricter positive tiles
    "change_centered": True,
    "pos_only": True,           # NEW: train batches from change tiles only
    "visualize": False,
    "scheduler": True,
    "thr_min": 0.10,            # match recommended ops sweep window
    "thr_max": 0.40,
    "thr_objective": "fbeta",
    "warm_start": "models/adaptformer_delhi/v3_frozen",
}

# Wednesday plan: training_failure_diagnosis fixes + hard-neg retention
# Target: test F1 > 0.60
_WED_PRESET = {
    "epochs": 20,
    "lr": 5e-5,
    "batch_size": 2,
    "augment": True,
    "stride": 64,
    "early_stop_patience": 7,
    "loss": "ce",                 # CE + pos_weight (diagnosis #2)
    "exclude_empty": True,        # drop empty real GT (diagnosis #1)
    "keep_hard_neg": True,        # but keep mined hn_* empty tiles
    "full_resize": True,          # full-image 256 resize (diagnosis #4)
    "min_change_frac": 0.001,
    "pos_oversample": 4,          # oversample change tiles
    "min_tile_change": 0.005,
    "change_centered": True,
    "visualize": False,
    "scheduler": True,
    "thr_min": 0.05,
    "thr_max": 0.50,
    "thr_objective": "f1",        # val-calibrate + freeze threshold
    "warm_start": "models/adaptformer_delhi/v3_frozen",
}


def _try_torch():
    try:
        import torch
        from torch.utils.data import DataLoader, Dataset, WeightedRandomSampler
        from transformers import AutoImageProcessor, AutoModel
        return torch, DataLoader, Dataset, WeightedRandomSampler, AutoImageProcessor, AutoModel
    except ImportError as exc:
        raise SystemExit(
            "PyTorch + transformers required for fine-tuning. "
            f"Import error: {exc}"
        ) from exc


def _augment_triplet(b: np.ndarray, a: np.ndarray, g: np.ndarray, rng: random.Random):
    """H/V flips, 90° rotations, mild brightness/contrast (same transform on both dates)."""
    if rng.random() < 0.5:
        b = np.ascontiguousarray(np.flip(b, axis=1))
        a = np.ascontiguousarray(np.flip(a, axis=1))
        g = np.ascontiguousarray(np.flip(g, axis=1))
    if rng.random() < 0.5:
        b = np.ascontiguousarray(np.flip(b, axis=0))
        a = np.ascontiguousarray(np.flip(a, axis=0))
        g = np.ascontiguousarray(np.flip(g, axis=0))
    if rng.random() < 0.5:
        k = rng.randint(1, 3)
        b = np.ascontiguousarray(np.rot90(b, k))
        a = np.ascontiguousarray(np.rot90(a, k))
        g = np.ascontiguousarray(np.rot90(g, k))
    if rng.random() < 0.5:
        # Shared photometric jitter so relative change is preserved
        alpha = 1.0 + rng.uniform(-0.15, 0.15)  # contrast
        beta = rng.uniform(-12.0, 12.0)          # brightness
        b = np.clip(b.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)
        a = np.clip(a.astype(np.float32) * alpha + beta, 0, 255).astype(np.uint8)
    return b, a, g


class DelhiTileDataset:
    """Lazy tile dataset: stores (pair_index, kind, x, y) instead of cropped arrays."""

    KIND_FULL = 0
    KIND_CROP = 1
    KIND_CENTER = 2

    def __init__(
        self,
        pairs: list[tuple],
        crop_size: int = _TILE,
        train: bool = True,
        stride: int | None = None,
        augment: bool = False,
        seed: int = 0,
        full_resize: bool = True,
        min_tile_change: float = 0.0,
        pos_oversample: int = 1,
        change_centered: bool = False,
        drop_empty_tiles: bool = False,
        pos_only: bool = False,
    ):
        _torch, _dl, Dataset, _wrs, _proc, _model = _try_torch()
        self.pairs = pairs  # keep source images once
        self.crop_size = crop_size
        self.train = train
        self.augment = bool(augment and train)
        self._rng = random.Random(seed)
        self.min_tile_change = float(min_tile_change)
        self.pos_oversample = max(1, int(pos_oversample))
        self.change_centered = bool(change_centered and train)
        self.pos_only = bool(pos_only and train)
        # index entries: (pair_i, kind, x, y, is_positive)
        self.index: list[tuple[int, int, int, int, bool]] = []
        use_stride = stride if stride is not None else (crop_size // 2 if train else crop_size)
        use_stride = max(16, min(crop_size, int(use_stride)))

        n_pos = n_neg = n_center = 0
        for pi, (before, after, gt, pair_id) in enumerate(pairs):
            h, w = before.shape[:2]
            is_hard_neg = str(pair_id).startswith("hn_")
            if full_resize or h < crop_size or w < crop_size:
                gr = np.array(Image.fromarray(gt).resize(
                    (crop_size, crop_size), resample=Image.NEAREST))
                frac = float((gr > 127).mean())
                # Empty GT (hard negatives) must count as neg even when min_tile_change=0
                is_pos = frac > 0.0 and frac >= self.min_tile_change
                # Hard-neg empty tiles are kept even when drop_empty_tiles is on
                if not (train and drop_empty_tiles and not is_pos and not is_hard_neg):
                    self.index.append((pi, self.KIND_FULL, 0, 0, is_pos))
                    n_pos += int(is_pos)
                    n_neg += int(not is_pos)

            if h >= crop_size and w >= crop_size:
                coords = set()
                for y in range(0, h - crop_size + 1, use_stride):
                    for x in range(0, w - crop_size + 1, use_stride):
                        coords.add((x, y))
                coords.add((max(0, w - crop_size), max(0, h - crop_size)))
                for x, y in coords:
                    tile_gt = gt[y:y + crop_size, x:x + crop_size]
                    frac = float((tile_gt > 127).mean())
                    is_pos = frac > 0.0 and frac >= self.min_tile_change
                    # Drop empty / near-empty crops when exclude_empty path is active
                    # (but keep hard-negative empty tiles so FP patterns are learned)
                    if train and drop_empty_tiles and not is_pos and not is_hard_neg:
                        continue
                    self.index.append((pi, self.KIND_CROP, x, y, is_pos))
                    n_pos += int(is_pos)
                    n_neg += int(not is_pos)

                # Change-centered crops (priority: more tiles on actual buildings)
                if self.change_centered:
                    for cx, cy in _gt_change_centers(gt, max_centers=16 if self.pos_only else 10):
                        x0 = int(np.clip(cx - crop_size // 2, 0, max(0, w - crop_size)))
                        y0 = int(np.clip(cy - crop_size // 2, 0, max(0, h - crop_size)))
                        # Small jitter for diversity
                        if train:
                            x0 = int(np.clip(x0 + self._rng.randint(-24, 24), 0, max(0, w - crop_size)))
                            y0 = int(np.clip(y0 + self._rng.randint(-24, 24), 0, max(0, h - crop_size)))
                        tile_gt = gt[y0:y0 + crop_size, x0:x0 + crop_size]
                        frac = float((tile_gt > 127).mean())
                        if frac < max(self.min_tile_change, 0.002):
                            continue
                        self.index.append((pi, self.KIND_CENTER, x0, y0, True))
                        n_pos += 1
                        n_center += 1

        # Drop negatives entirely when pos_only (no-change tiles cannot dominate)
        if train and self.pos_only:
            before_n = len(self.index)
            self.index = [e for e in self.index if e[4]]
            print(f"  pos_only: kept {len(self.index)}/{before_n} positive tiles", flush=True)

        # Expand positive indices for oversampling (simple list multiply)
        if train and self.pos_oversample > 1:
            extras = [e for e in self.index if e[4]]
            for _ in range(self.pos_oversample - 1):
                self.index.extend(extras)

        # Soft cap so CPU runs stay tractable while keeping centered crops
        max_tiles = 160 if train else None
        if max_tiles and len(self.index) > max_tiles:
            centered = [e for e in self.index if e[1] == self.KIND_CENTER]
            rest = [e for e in self.index if e[1] != self.KIND_CENTER]
            self._rng.shuffle(centered)
            self._rng.shuffle(rest)
            # Prefer more centered when pos_only
            center_frac = 0.85 if self.pos_only else 0.7
            n_center_keep = min(len(centered), max(1, int(max_tiles * center_frac)))
            n_rest_keep = min(len(rest), max_tiles - n_center_keep)
            keep = centered[:n_center_keep] + rest[:n_rest_keep]
            self._rng.shuffle(keep)
            self.index = keep
            print(f"  Capped train tiles to {len(self.index)} "
                  f"(centered={n_center_keep}, other={n_rest_keep})",
                  flush=True)

        self.n_pos_unique = n_pos
        self.n_neg_unique = n_neg
        print(f"  Dataset({'train' if train else 'eval'}): index={len(self.index)} "
              f"(pos~{n_pos}, neg~{n_neg}, centered~{n_center}, "
              f"oversamplex{self.pos_oversample})",
              flush=True)

        outer = self

        class _Inner(Dataset):
            def __len__(inner_self):
                return len(outer.index)

            def __getitem__(inner_self, idx):
                pi, kind, x, y, _is_pos = outer.index[idx]
                before, after, gt, _ = outer.pairs[pi]
                cs = outer.crop_size
                if kind == outer.KIND_FULL:
                    b = np.array(Image.fromarray(before).resize((cs, cs)))
                    a = np.array(Image.fromarray(after).resize((cs, cs)))
                    g = np.array(Image.fromarray(gt).resize((cs, cs), resample=Image.NEAREST))
                else:
                    b = before[y:y + cs, x:x + cs].copy()
                    a = after[y:y + cs, x:x + cs].copy()
                    g = gt[y:y + cs, x:x + cs].copy()
                if outer.augment:
                    b, a, g = _augment_triplet(b, a, g, outer._rng)
                return b, a, (g > 127).astype(np.float32)

        self._dataset = _Inner()

    @property
    def samples(self):
        """Backward-compat length alias."""
        return self.index

    def torch_dataset(self):
        return self._dataset

    def sampler_weights(self) -> list[float]:
        """Per-index weights for WeightedRandomSampler (positives / centered heavier)."""
        w = []
        for _pi, kind, _x, _y, is_pos in self.index:
            base = float(self.pos_oversample) if is_pos else 1.0
            if kind == self.KIND_CENTER:
                base *= 2.0
            w.append(base)
        return w


def _gt_change_centers(gt: np.ndarray, max_centers: int = 10) -> list[tuple[int, int]]:
    """Centroids of GT change blobs — used for positive-focused crops."""
    import cv2
    binary = (gt > 127).astype(np.uint8)
    n, _lab, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    centers = []
    for i in range(1, n):
        area = int(stats[i, cv2.CC_STAT_AREA])
        if area < 8:
            continue
        cx, cy = int(centroids[i][0]), int(centroids[i][1])
        centers.append((area, cx, cy))
    centers.sort(key=lambda t: -t[0])
    return [(cx, cy) for _a, cx, cy in centers[:max_centers]]


def _dice_loss(prob, target, eps: float = 1e-6):
    p = prob.reshape(-1)
    t = target.reshape(-1)
    inter = (p * t).sum()
    return 1.0 - (2.0 * inter + eps) / (p.sum() + t.sum() + eps)


def _focal_loss(prob, target, gamma: float = 2.0, alpha: float = 0.75, eps: float = 1e-6):
    p = prob.clamp(eps, 1.0 - eps)
    pt = p * target + (1.0 - p) * (1.0 - target)
    w = alpha * target + (1.0 - alpha) * (1.0 - target)
    return (-(w * (1.0 - pt).pow(gamma) * pt.log())).mean()


def _tversky_loss(prob, target, alpha: float = 0.3, beta: float = 0.7, eps: float = 1e-6):
    """Tversky: beta>alpha penalizes false negatives more (recall-oriented)."""
    p = prob.reshape(-1)
    t = target.reshape(-1)
    tp = (p * t).sum()
    fp = (p * (1.0 - t)).sum()
    fn = ((1.0 - p) * t).sum()
    return 1.0 - (tp + eps) / (tp + alpha * fp + beta * fn + eps)


def _change_prob_from_logits(logits, torch):
    """Convert AdaptFormer logits → change probability.

    Confirmed on deepang/adaptformer-LEVIR-CD: logits are (N, 2, H, W) where
    channel 0 = no-change, channel 1 = change. Softmax last channel is correct.
    """
    from app.model_inference import _logits_to_change_prob
    return _logits_to_change_prob(logits, torch)


def _probe_output_scale(model, processor, device, pairs: list[tuple], n: int = 2) -> dict:
    """Print / return logit→prob stats to validate conversion (priority #1)."""
    torch, *_ = _try_torch()
    from PIL import Image as PILImage

    rows = []
    for before, after, gt, pair_id in pairs[:n]:
        if before.shape[0] != _TILE or before.shape[1] != _TILE:
            before_r = np.array(Image.fromarray(before).resize((_TILE, _TILE)))
            after_r = np.array(Image.fromarray(after).resize((_TILE, _TILE)))
            gt_r = np.array(Image.fromarray(gt).resize((_TILE, _TILE), Image.NEAREST))
        else:
            before_r, after_r, gt_r = before, after, gt
        inputs = processor(
            images=(PILImage.fromarray(before_r), PILImage.fromarray(after_r)),
            return_tensors="pt",
        )
        inputs = {k: v.to(device) for k, v in inputs.items()}
        with torch.no_grad():
            logits = model(**inputs).logits
        if logits.dim() == 3:
            logits = logits.unsqueeze(0)
        g = (gt_r > 127)
        gt_pos = float(g.mean())
        row = {
            "pair_id": pair_id,
            "logits_shape": list(logits.shape),
            "logits_min": float(logits.min()),
            "logits_max": float(logits.max()),
            "logits_mean": float(logits.mean()),
            "gt_pos_frac": gt_pos,
        }
        if logits.shape[1] >= 2:
            sm = torch.softmax(logits, dim=1)
            p0 = sm[0, 0].cpu().numpy()
            p1 = sm[0, 1].cpu().numpy()
            row.update({
                "ch0_mean": float(p0.mean()),
                "ch1_mean": float(p1.mean()),
                "ch1_min": float(p1.min()),
                "ch1_max": float(p1.max()),
                "ch1_on_gt": float(p1[g].mean()) if g.any() else None,
                "ch1_on_bg": float(p1[~g].mean()) if (~g).any() else None,
                "ch0_on_gt": float(p0[g].mean()) if g.any() else None,
                "change_channel": 1 if (
                    (p1[g].mean() if g.any() else 0) >= (p0[g].mean() if g.any() else 0)
                ) else 0,
            })
        prob = _change_prob_from_logits(logits, torch).cpu().numpy()
        row.update({
            "prob_min": float(prob.min()),
            "prob_max": float(prob.max()),
            "prob_mean": float(prob.mean()),
            "prob_on_gt": float(prob[g].mean()) if g.any() else None,
            "prob_on_bg": float(prob[~g].mean()) if (~g).any() else None,
            "pred_pos_at_0.5": float((prob >= 0.5).mean()),
        })
        rows.append(row)
        print(
            f"  [probe] {pair_id}: logits[{row['logits_min']:.2f},{row['logits_max']:.2f}] "
            f"prob[{row['prob_min']:.2e},{row['prob_max']:.4f}] mean={row['prob_mean']:.4f} "
            f"GT%={gt_pos:.3f} p@GT={row['prob_on_gt']} p@bg={row['prob_on_bg']} "
            f"pred%@0.5={row['pred_pos_at_0.5']:.3f}",
            flush=True,
        )
    return {"n": len(rows), "pairs": rows}


def _filter_empty(
    pairs: list[tuple],
    min_change_frac: float,
    *,
    keep_hard_neg: bool = True,
) -> list[tuple]:
    kept, dropped = [], []
    for before, after, gt, pair_id in pairs:
        frac = float((gt > 127).mean()) if gt is not None else 0.0
        is_hn = keep_hard_neg and str(pair_id).startswith("hn_")
        if frac >= min_change_frac or is_hn:
            kept.append((before, after, gt, pair_id))
        else:
            dropped.append(pair_id)
    if dropped:
        print(f"  Excluded {len(dropped)} empty/near-empty GT pairs: {dropped}")
    hn_kept = sum(1 for *_, pid in kept if str(pid).startswith("hn_"))
    if hn_kept:
        print(f"  Kept {hn_kept} hard-negative (empty-GT) tiles for FP suppression")
    return kept


def _class_balance_report(pairs: list[tuple], name: str) -> dict:
    fracs = [float((g > 127).mean()) for _, _, g, _ in pairs]
    report = {
        "split": name,
        "n_pairs": len(pairs),
        "change_frac_mean": round(float(np.mean(fracs)), 6) if fracs else 0.0,
        "change_frac_min": round(float(np.min(fracs)), 6) if fracs else 0.0,
        "change_frac_max": round(float(np.max(fracs)), 6) if fracs else 0.0,
        "bg_to_change_ratio": round(
            (1.0 - float(np.mean(fracs))) / max(float(np.mean(fracs)), 1e-6), 2
        ) if fracs else None,
    }
    print(
        f"  Imbalance[{name}]: change%={report['change_frac_mean']*100:.2f} "
        f"(min={report['change_frac_min']*100:.2f} max={report['change_frac_max']*100:.2f}) "
        f"bg:change~{report['bg_to_change_ratio']}:1",
        flush=True,
    )
    return report


def _load_rgb_pair(before_rel: str, after_rel: str, gt_rel: str, pair_id: str) -> tuple:
    from app.evaluation.delhi_eval import _load_label, _load_rgb
    before = _load_rgb(ROOT / before_rel)
    after = _load_rgb(ROOT / after_rel)
    gt = _load_label(ROOT / gt_rel)
    return before, after, gt, pair_id


def _load_pairs_from_delhi_cd(delhi_cd: Path) -> tuple[list[tuple], list[tuple], list[tuple], dict]:
    split_path = delhi_cd / "split.json"
    if not split_path.is_file():
        raise SystemExit(
            f"Missing {split_path}. Run: python scripts/build_delhi_cd_splits.py"
        )
    summary = json.loads(split_path.read_text(encoding="utf-8"))
    loaded = {}
    for name in ("train", "val", "test"):
        man = delhi_cd / name / "manifest.json"
        if not man.is_file():
            raise SystemExit(f"Missing {man}")
        rows = json.loads(man.read_text(encoding="utf-8")).get("pairs", [])
        loaded[name] = [
            _load_rgb_pair(p["before_path"], p["after_path"], p["gt_mask"], p["pair_id"])
            for p in rows
        ]
    split_info = {
        "train": [p[3] for p in loaded["train"]],
        "val": [p[3] for p in loaded["val"]],
        "test": [p[3] for p in loaded["test"]],
        "split": summary.get("split", "70/15/15"),
        "seed": summary.get("seed", 0),
        "source": str(delhi_cd),
        "stratified": summary.get("stratified", False),
    }
    return loaded["train"], loaded["val"], loaded["test"], split_info


def _load_pairs(manifest: Path | None, dummy: bool) -> list[tuple]:
    if dummy:
        return [(b, a, g, pid) for b, a, g, pid, _, _ in dummy_delhi_pairs()]
    try:
        loaded = list(iter_delhi_pairs(manifest))
    except DelhiEvalNotReady as exc:
        raise SystemExit(str(exc)) from exc
    labeled = [(b, a, g, pid) for b, a, g, pid, _, _ in loaded if g is not None]
    if labeled:
        return labeled
    raise SystemExit("No Delhi pairs with GT masks. Use --dummy for scaffold runs.")


def _split_pairs(pairs: list[tuple], seed: int = 0,
                 train_frac: float = 0.70, val_frac: float = 0.15):
    n = len(pairs)
    if n < 3:
        return pairs[: max(1, n - 1)], pairs[-1:], []

    rng = random.Random(seed)
    idx = list(range(n))
    rng.shuffle(idx)
    n_test = max(1, int(round(n * (1.0 - train_frac - val_frac))))
    n_val = max(1, int(round(n * val_frac)))
    if n_test + n_val >= n:
        n_test = max(1, n // 5)
        n_val = max(1, n // 5)
    test_idx = set(idx[:n_test])
    val_idx = set(idx[n_test:n_test + n_val])
    train = [pairs[i] for i in range(n) if i not in test_idx and i not in val_idx]
    val = [pairs[i] for i in range(n) if i in val_idx]
    test = [pairs[i] for i in range(n) if i in test_idx]
    return train, val, test


def _predict_mask(model, processor, device, before, after, threshold=0.5):
    torch, *_ = _try_torch()
    from PIL import Image as PILImage

    if before.shape[0] != _TILE or before.shape[1] != _TILE:
        before = np.array(Image.fromarray(before).resize((_TILE, _TILE)))
        after = np.array(Image.fromarray(after).resize((_TILE, _TILE)))

    inputs = processor(
        images=(PILImage.fromarray(before), PILImage.fromarray(after)),
        return_tensors="pt",
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs)
    score = _change_prob_from_logits(outputs.logits, torch).cpu().numpy().astype(np.float32)
    mask = (score >= threshold).astype(np.uint8) * 255
    return mask, score


def _resize_to_gt(arr: np.ndarray, gt: np.ndarray, nearest: bool = False) -> np.ndarray:
    if arr.shape[:2] == gt.shape[:2]:
        return arr
    from cv2 import resize, INTER_NEAREST, INTER_LINEAR
    return resize(
        arr, (gt.shape[1], gt.shape[0]),
        interpolation=INTER_NEAREST if nearest else INTER_LINEAR,
    )


def _eval_pairs(model, processor, device, pairs: list[tuple],
                threshold: float = 0.5) -> dict:
    f1s, precs, recs, ious = [], [], [], []
    scores_all = []
    for before, after, gt, _pair_id in pairs:
        mask, score = _predict_mask(model, processor, device, before, after, threshold)
        score = _resize_to_gt(score, gt, nearest=False)
        mask = _resize_to_gt(mask, gt, nearest=True)
        scores_all.append(score)
        m = binary_metrics(mask, gt)
        f1s.append(m["f1"])
        precs.append(m["precision"])
        recs.append(m["recall"])
        ious.append(m["iou"])
    return {
        "mean_f1": round(float(np.mean(f1s)), 4) if f1s else 0.0,
        "mean_precision": round(float(np.mean(precs)), 4) if precs else 0.0,
        "mean_recall": round(float(np.mean(recs)), 4) if recs else 0.0,
        "mean_iou": round(float(np.mean(ious)), 4) if ious else 0.0,
        "n": len(f1s),
        "threshold": threshold,
        "mean_prob": round(float(np.mean([s.mean() for s in scores_all])), 6) if scores_all else 0.0,
        "max_prob": round(float(np.max([s.max() for s in scores_all])), 6) if scores_all else 0.0,
    }


def _calibrate_threshold(
    model, processor, device, pairs: list[tuple],
    thr_min: float = 0.2,
    thr_max: float = 0.7,
    objective: str = "f1",
) -> tuple[float, float, dict]:
    """Sweep thresholds on val only; return (best_thr, best_f1, detail).

    Default search window 0.2–0.7 (Claude recall plan). objective:
      - f1: maximize mean F1
      - fbeta: maximize F_1.5 (recall-oriented) then report F1 at that thr
    """
    if not pairs:
        return 0.5, 0.0, {}
    scores, gts = [], []
    for before, after, gt, _ in pairs:
        _mask, score = _predict_mask(model, processor, device, before, after, 0.5)
        score = _resize_to_gt(score, gt, nearest=False)
        scores.append(score.astype(np.float32))
        gts.append(gt > 127)

    # Dense grid inside [thr_min, thr_max] plus baseline 0.5
    grid = list(np.linspace(thr_min, thr_max, 26))
    if 0.5 not in grid:
        grid.append(0.5)
    candidates = sorted({round(float(t), 6) for t in grid if thr_min - 1e-9 <= t <= thr_max + 1e-9})

    best_thr, best_score, best_f1 = 0.5, -1.0, -1.0
    best_row = {}
    beta = 1.5
    sweep = []
    for thr in candidates:
        f1s, precs, recs = [], [], []
        for score, gt in zip(scores, gts):
            if not gt.any():
                continue
            m = score >= thr
            tp = int((m & gt).sum())
            fp = int((m & ~gt).sum())
            fn = int((~m & gt).sum())
            p = 0.0 if (tp + fp) == 0 else tp / (tp + fp)
            r = 0.0 if (tp + fn) == 0 else tp / (tp + fn)
            f1 = 0.0 if (p + r) == 0 else 2 * p * r / (p + r)
            f1s.append(f1)
            precs.append(p)
            recs.append(r)
        if not f1s:
            continue
        mean_f1 = float(np.mean(f1s))
        mean_p = float(np.mean(precs))
        mean_r = float(np.mean(recs))
        if objective == "fbeta":
            # F_beta with beta>1 weights recall higher
            b2 = beta * beta
            mean_obj = (
                0.0 if (mean_p + mean_r) == 0
                else (1 + b2) * mean_p * mean_r / (b2 * mean_p + mean_r)
            )
        else:
            mean_obj = mean_f1
        row = {"thr": thr, "f1": mean_f1, "precision": mean_p, "recall": mean_r, "obj": mean_obj}
        sweep.append(row)
        # Prefer higher obj; tie-break toward higher recall, then higher F1
        better = (
            mean_obj > best_score + 1e-9
            or (abs(mean_obj - best_score) < 1e-9 and mean_r > best_row.get("recall", -1) + 1e-9)
        )
        if better:
            best_score, best_thr, best_f1 = mean_obj, thr, mean_f1
            best_row = row

    print(
        f"  Calibrated threshold={best_thr:.4f} (val F1={best_f1:.4f} "
        f"P={best_row.get('precision', 0):.3f} R={best_row.get('recall', 0):.3f} "
        f"obj={objective}, window=[{thr_min},{thr_max}], {len(candidates)} candidates)",
        flush=True,
    )
    return best_thr, best_f1, {"sweep": sweep, "selected": best_row, "objective": objective}


def _save_epoch_visuals(model, processor, device, pairs: list[tuple],
                        thr: float, out_dir: Path, epoch: int, max_pairs: int = 4):
    """Save Before | After | GT | Pred | Prob panels (priority #6)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    for before, after, gt, pair_id in pairs[:max_pairs]:
        mask, score = _predict_mask(model, processor, device, before, after, thr)
        score = _resize_to_gt(score, gt, nearest=False)
        mask = _resize_to_gt(mask, gt, nearest=True)
        h, w = gt.shape[:2]
        b = np.array(Image.fromarray(before).resize((w, h)))
        a = np.array(Image.fromarray(after).resize((w, h)))
        gt_rgb = np.stack([gt, gt, gt], axis=-1)
        pred_rgb = np.stack([mask, mask, mask], axis=-1)
        # Probability heatmap (grayscale → red tint)
        p_u8 = (np.clip(score, 0, 1) * 255).astype(np.uint8)
        prob_rgb = np.stack([p_u8, (p_u8 * 0.3).astype(np.uint8), (p_u8 * 0.3).astype(np.uint8)], -1)
        # Labels strip
        panel = np.concatenate([b, a, gt_rgb, pred_rgb, prob_rgb], axis=1)
        Image.fromarray(panel).save(out_dir / f"ep{epoch:02d}_{pair_id}.png")


def _compute_loss(logits, labels_t, loss_mode: str, ce_weight, torch, F, bce):
    if logits.dim() == 3:
        logits = logits.unsqueeze(0)
    target = labels_t if labels_t.dim() == 3 else labels_t.unsqueeze(0)
    if logits.shape[-2:] != target.shape[-2:]:
        target = F.interpolate(
            target.unsqueeze(1).float(), size=logits.shape[-2:],
            mode="nearest").squeeze(1)

    if loss_mode == "ce":
        return F.cross_entropy(logits, target.long(), weight=ce_weight)

    prob = _change_prob_from_logits(logits, torch).unsqueeze(0)
    if prob.shape[-2:] != target.shape[-2:]:
        prob = F.interpolate(
            prob.unsqueeze(1), size=target.shape[-2:],
            mode="bilinear", align_corners=False).squeeze(1)
    labels_b = target.float()

    if loss_mode == "bce":
        return bce(prob, labels_b)
    if loss_mode == "bce_dice":
        return 0.5 * bce(prob, labels_b) + 0.5 * _dice_loss(prob, labels_b)
    if loss_mode == "focal_dice":
        return 0.5 * _focal_loss(prob, labels_b) + 0.5 * _dice_loss(prob, labels_b)
    if loss_mode == "tversky":
        # beta=0.7 > alpha=0.3 → penalize FN (recall-oriented)
        return 0.6 * _tversky_loss(prob, labels_b, alpha=0.3, beta=0.7) + 0.4 * _focal_loss(
            prob, labels_b, alpha=0.75)
    if loss_mode == "tversky_dice":
        return 0.5 * _tversky_loss(prob, labels_b, alpha=0.3, beta=0.7) + 0.5 * _dice_loss(
            prob, labels_b)
    if loss_mode == "ce_dice":
        ce = F.cross_entropy(logits, target.long(), weight=ce_weight)
        return 0.5 * ce + 0.5 * _dice_loss(prob, labels_b)
    # default
    return 0.5 * _focal_loss(prob, labels_b) + 0.5 * _dice_loss(prob, labels_b)


def train(
    eval_dir: Path | None,
    dummy: bool,
    epochs: int,
    batch_size: int,
    lr: float,
    out_root: Path,
    delhi_cd: Path | None = None,
    augment: bool = False,
    stride: int = 128,
    early_stop_patience: int = 0,
    loss_mode: str = "focal_dice",
    exclude_empty: bool = True,
    full_resize: bool = True,
    min_change_frac: float = 0.001,
    pos_oversample: int = 3,
    min_tile_change: float = 0.005,
    visualize: bool = True,
    use_scheduler: bool = True,
    preset_name: str | None = None,
    change_centered: bool = False,
    thr_min: float = 0.2,
    thr_max: float = 0.7,
    thr_objective: str = "f1",
    warm_start: str | None = None,
    pos_only: bool = False,
    keep_hard_neg: bool = True,
) -> Path:
    torch, DataLoader, _Dataset, WeightedRandomSampler, AutoImageProcessor, AutoModel = _try_torch()
    import torch.nn.functional as F

    if delhi_cd is not None and not dummy:
        train_pairs, val_pairs, test_pairs, split_info = _load_pairs_from_delhi_cd(delhi_cd)
    else:
        pairs = _load_pairs(eval_dir, dummy)
        train_pairs, val_pairs, test_pairs = _split_pairs(pairs)
        split_info = {
            "train": [p[3] for p in train_pairs],
            "val": [p[3] for p in val_pairs],
            "test": [p[3] for p in test_pairs],
            "split": "70/15/15",
        }

    if exclude_empty and not dummy:
        train_pairs = _filter_empty(
            train_pairs, min_change_frac, keep_hard_neg=keep_hard_neg)
        val_pairs = _filter_empty(
            val_pairs, min_change_frac, keep_hard_neg=False)
        test_change = _filter_empty(
            test_pairs, min_change_frac, keep_hard_neg=False)
        if not train_pairs:
            raise SystemExit("No train pairs left after excluding empty GT.")
        if not val_pairs:
            val_pairs = train_pairs[-1:]
        test_pairs = test_change or test_pairs
        split_info["excluded_empty"] = True
        split_info["min_change_frac"] = min_change_frac
        split_info["train"] = [p[3] for p in train_pairs]
        split_info["val"] = [p[3] for p in val_pairs]
        split_info["test"] = [p[3] for p in test_pairs]

    balance = {
        "train": _class_balance_report(train_pairs, "train"),
        "val": _class_balance_report(val_pairs, "val"),
        "test": _class_balance_report(test_pairs, "test"),
    }

    # keep-empty / exclude_empty=False must retain hard-negative (all-zero GT) tiles
    drop_empty_tiles = bool(exclude_empty) and not pos_only
    train_ds = DelhiTileDataset(
        train_pairs, train=True, stride=stride, augment=augment, seed=0,
        full_resize=full_resize, min_tile_change=min_tile_change,
        pos_oversample=pos_oversample, change_centered=change_centered,
        drop_empty_tiles=drop_empty_tiles, pos_only=pos_only)
    val_ds = DelhiTileDataset(
        val_pairs, train=False, stride=_TILE, augment=False, full_resize=full_resize,
        min_tile_change=0.0, pos_oversample=1, change_centered=False)

    pos_frac = float(np.mean([(g > 127).mean() for _, _, g, _ in train_pairs]))
    pos_frac = max(pos_frac, 1e-3)
    pos_weight = (1.0 - pos_frac) / pos_frac
    pos_weight = float(min(50.0, max(2.0, pos_weight)))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device} | split {split_info['split']} | "
          f"train={len(train_pairs)} val={len(val_pairs)} test={len(test_pairs)} | "
          f"tiles train={len(train_ds.index)} val={len(val_ds.index)} | "
          f"lr={lr} aug={augment} loss={loss_mode} pos_w={pos_weight:.1f} "
          f"pos_oversamplex{pos_oversample} scheduler={use_scheduler}", flush=True)

    processor = AutoImageProcessor.from_pretrained(_MODEL_ID, trust_remote_code=True)
    warm_path = Path(warm_start).resolve() if warm_start else None
    if warm_path and warm_path.is_dir():
        print(f"Warm-start from {warm_path}", flush=True)
        model = AutoModel.from_pretrained(warm_path, trust_remote_code=True)
        try:
            processor = AutoImageProcessor.from_pretrained(warm_path, trust_remote_code=True)
        except Exception:
            pass
    else:
        if warm_start:
            print(f"Warm-start path missing ({warm_start}); loading hub weights", flush=True)
        model = AutoModel.from_pretrained(_MODEL_ID, trust_remote_code=True)
    model.to(device)
    model.eval()
    print("Validating logit->prob conversion...", flush=True)
    probe = _probe_output_scale(model, processor, device, train_pairs, n=2)
    model.train()

    weights = train_ds.sampler_weights()
    sampler = WeightedRandomSampler(
        weights=weights, num_samples=len(weights), replacement=True)
    train_loader = DataLoader(
        train_ds.torch_dataset(), batch_size=batch_size, sampler=sampler, num_workers=0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr, weight_decay=1e-4)
    scheduler = None
    if use_scheduler:
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, mode="max", factor=0.5, patience=2, min_lr=1e-6)
    bce = torch.nn.BCELoss()
    ce_weight = torch.tensor([1.0, pos_weight], dtype=torch.float32, device=device)

    run_id = time.strftime("%Y%m%d_%H%M%S")
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    vis_dir = run_dir / "visuals"
    (run_dir / "split.json").write_text(json.dumps(split_info, indent=2), encoding="utf-8")
    (run_dir / "class_balance.json").write_text(json.dumps(balance, indent=2), encoding="utf-8")
    (run_dir / "output_probe.json").write_text(json.dumps(probe, indent=2), encoding="utf-8")
    (run_dir / "config.json").write_text(json.dumps({
        "model_id": _MODEL_ID,
        "epochs": epochs,
        "lr": lr,
        "batch_size": batch_size,
        "augment": augment,
        "stride": stride,
        "early_stop_patience": early_stop_patience,
        "loss": loss_mode,
        "pos_weight": pos_weight,
        "pos_oversample": pos_oversample,
        "min_tile_change": min_tile_change,
        "change_centered": change_centered,
        "pos_only": pos_only,
        "exclude_empty": exclude_empty,
        "full_resize": full_resize,
        "min_change_frac": min_change_frac,
        "scheduler": use_scheduler,
        "visualize": visualize,
        "thr_min": thr_min,
        "thr_max": thr_max,
        "thr_objective": thr_objective,
        "warm_start": warm_start,
        "delhi_cd": str(delhi_cd) if delhi_cd else None,
        "preset": preset_name,
        "change_channel": "softmax_last (ch1)",
    }, indent=2), encoding="utf-8")

    history = []
    best_f1 = -1.0
    best_path = run_dir / "best"
    best_thr = 0.5
    stale = 0

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        n_batches = 0
        from PIL import Image as PILImage

        for batch in train_loader:
            before_np, after_np, gt_np = batch
            optimizer.zero_grad()
            batch_loss = 0.0
            for i in range(before_np.shape[0]):
                b = before_np[i].numpy().astype(np.uint8)
                a = after_np[i].numpy().astype(np.uint8)
                label = gt_np[i].numpy()
                inputs = processor(
                    images=(PILImage.fromarray(b), PILImage.fromarray(a)),
                    return_tensors="pt",
                )
                inputs = {k: v.to(device) for k, v in inputs.items()}
                labels_t = torch.from_numpy(label).to(device)
                outputs = model(**inputs)
                sample_loss = _compute_loss(
                    outputs.logits, labels_t, loss_mode, ce_weight, torch, F, bce)
                batch_loss = batch_loss + sample_loss

            batch_loss = batch_loss / max(before_np.shape[0], 1)
            batch_loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
            total_loss += float(batch_loss.item())
            n_batches += 1

        model.eval()
        thr, thr_f1, thr_detail = _calibrate_threshold(
            model, processor, device, val_pairs,
            thr_min=thr_min, thr_max=thr_max, objective=thr_objective)
        val_metrics = _eval_pairs(model, processor, device, val_pairs, threshold=thr)
        avg_loss = total_loss / max(n_batches, 1)
        cur_lr = float(optimizer.param_groups[0]["lr"])
        row = {
            "epoch": epoch,
            "train_loss": round(avg_loss, 4),
            "val_mean_f1": val_metrics["mean_f1"],
            "val_precision": val_metrics["mean_precision"],
            "val_recall": val_metrics["mean_recall"],
            "val_iou": val_metrics["mean_iou"],
            "threshold": thr,
            "calibrate_f1": round(thr_f1, 4),
            "calibrate_selected": thr_detail.get("selected"),
            "val_mean_prob": val_metrics["mean_prob"],
            "val_max_prob": val_metrics["max_prob"],
            "lr": cur_lr,
        }
        history.append(row)
        print(
            f"  epoch {epoch}/{epochs} loss={avg_loss:.4f} "
            f"val_F1={val_metrics['mean_f1']:.4f} P={val_metrics['mean_precision']:.3f} "
            f"R={val_metrics['mean_recall']:.3f} IoU={val_metrics['mean_iou']:.3f} "
            f"thr={thr:.6g} max_p={val_metrics['max_prob']:.4f} lr={cur_lr:.2e}",
            flush=True,
        )
        (run_dir / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")

        if visualize:
            _save_epoch_visuals(
                model, processor, device, val_pairs, thr, vis_dir, epoch)

        if scheduler is not None:
            scheduler.step(val_metrics["mean_f1"])

        improved = val_metrics["mean_f1"] > best_f1 + 1e-6
        if improved or best_f1 < 0:
            best_f1 = val_metrics["mean_f1"]
            best_thr = thr
            stale = 0
            best_path.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(best_path)
            processor.save_pretrained(best_path)
            (best_path / "threshold.json").write_text(
                json.dumps({
                    "threshold": best_thr,
                    "val_f1": best_f1,
                    "val_precision": val_metrics["mean_precision"],
                    "val_recall": val_metrics["mean_recall"],
                    "val_iou": val_metrics["mean_iou"],
                    "epoch": epoch,
                }, indent=2),
                encoding="utf-8")
        else:
            stale += 1
            if early_stop_patience and stale >= early_stop_patience:
                print(f"Early stop at epoch {epoch}. Best val F1={best_f1:.4f} thr={best_thr}",
                      flush=True)
                break

    # Frozen threshold from best checkpoint for methodologically sound test F1
    test_metrics = {
        "mean_f1": 0.0, "mean_precision": 0.0, "mean_recall": 0.0,
        "mean_iou": 0.0, "n": 0, "threshold": best_thr,
    }
    if test_pairs and best_path.is_dir():
        best_model = AutoModel.from_pretrained(best_path, trust_remote_code=True)
        best_model.to(device)
        best_processor = AutoImageProcessor.from_pretrained(best_path, trust_remote_code=True)
        best_model.eval()
        thr_path = best_path / "threshold.json"
        if thr_path.is_file():
            best_thr = float(json.loads(thr_path.read_text()).get("threshold", best_thr))
        print(f"Test eval with FROZEN threshold={best_thr} from best checkpoint", flush=True)
        test_metrics = _eval_pairs(
            best_model, best_processor, device, test_pairs, threshold=best_thr)
        _save_epoch_visuals(
            best_model, best_processor, device, test_pairs, best_thr,
            run_dir / "visuals_test", epoch=0, max_pairs=len(test_pairs))

    meta = {
        "model_id": _MODEL_ID,
        "dummy": dummy,
        "epochs": epochs,
        "epochs_ran": len(history),
        "lr": lr,
        "augment": augment,
        "stride": stride,
        "loss": loss_mode,
        "pos_weight": pos_weight,
        "pos_oversample": pos_oversample,
        "exclude_empty": exclude_empty,
        "full_resize": full_resize,
        "threshold": best_thr,
        "device": str(device),
        "split": split_info,
        "class_balance": balance,
        "train_pairs": len(train_pairs),
        "val_pairs": len(val_pairs),
        "test_pairs": len(test_pairs),
        "train_tiles": len(train_ds.index),
        "best_val_f1": best_f1 if best_f1 >= 0 else 0.0,
        "test_mean_f1": test_metrics["mean_f1"],
        "test_precision": test_metrics.get("mean_precision", 0.0),
        "test_recall": test_metrics.get("mean_recall", 0.0),
        "test_iou": test_metrics.get("mean_iou", 0.0),
        "history": history,
        "preset": preset_name,
        "output_probe": probe,
    }
    (run_dir / "metrics.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(
        f"Test F1={test_metrics['mean_f1']:.4f} P={test_metrics.get('mean_precision', 0):.3f} "
        f"R={test_metrics.get('mean_recall', 0):.3f} IoU={test_metrics.get('mean_iou', 0):.3f} "
        f"@ thr={best_thr} ({test_metrics['n']} pairs)",
        flush=True,
    )
    print(f"Run complete. Artifacts: {run_dir}", flush=True)
    return run_dir


def finalize_run(
    run_dir: Path,
    eval_dir: Path | None,
    dummy: bool,
    history: list[dict] | None = None,
) -> Path:
    torch, *_rest = _try_torch()
    AutoImageProcessor = _rest[-2]
    AutoModel = _rest[-1]

    split_path = run_dir / "split.json"
    best_path = run_dir / "best"
    if not split_path.is_file():
        raise SystemExit(f"Missing {split_path}")
    if not best_path.is_dir():
        raise SystemExit(f"Missing checkpoint at {best_path}")

    split_info = json.loads(split_path.read_text(encoding="utf-8"))
    pairs = _load_pairs(eval_dir, dummy)
    by_id = {p[3]: p for p in pairs}
    test_pairs = [by_id[pid] for pid in split_info.get("test", []) if pid in by_id]
    thr = 0.5
    thr_path = best_path / "threshold.json"
    if thr_path.is_file():
        thr = float(json.loads(thr_path.read_text()).get("threshold", 0.5))

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    best_model = AutoModel.from_pretrained(best_path, trust_remote_code=True)
    best_model.to(device)
    best_processor = AutoImageProcessor.from_pretrained(best_path, trust_remote_code=True)
    best_model.eval()
    test_metrics = _eval_pairs(best_model, best_processor, device, test_pairs, threshold=thr)

    hist = history or []
    hist_path = run_dir / "history.json"
    if not hist and hist_path.is_file():
        hist = json.loads(hist_path.read_text(encoding="utf-8"))
    best_val_f1 = max((row.get("val_mean_f1", 0.0) for row in hist), default=0.0)
    meta = {
        "model_id": _MODEL_ID,
        "dummy": dummy,
        "epochs": len(hist) or None,
        "device": str(device),
        "threshold": thr,
        "split": split_info,
        "train_pairs": len(split_info.get("train", [])),
        "val_pairs": len(split_info.get("val", [])),
        "test_pairs": len(test_pairs),
        "best_val_f1": best_val_f1,
        "test_mean_f1": test_metrics["mean_f1"],
        "test_precision": test_metrics.get("mean_precision", 0.0),
        "test_recall": test_metrics.get("mean_recall", 0.0),
        "test_iou": test_metrics.get("mean_iou", 0.0),
        "history": hist,
        "finalized": True,
    }
    (run_dir / "metrics.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Test F1={test_metrics['mean_f1']:.4f} ({test_metrics['n']} pairs) thr={thr}")
    return run_dir


def main():
    parser = argparse.ArgumentParser(description="Fine-tune AdaptFormer on Delhi tiles")
    parser.add_argument("--manifest", type=str, default="docs/delhi_eval/manifest.json")
    parser.add_argument("--dummy", action="store_true")
    parser.add_argument("--preset", choices=["", "day5", "fix", "v2", "v3", "v4", "wed"], default="",
                        help="wed = diagnosis CE+pos_weight + hard-neg retention (target test F1>0.60)")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--out", type=str, default="runs/finetune_adaptformer")
    parser.add_argument("--delhi-cd", type=str, default="")
    parser.add_argument("--augment", action="store_true")
    parser.add_argument("--stride", type=int, default=None)
    parser.add_argument("--early-stop", type=int, default=None)
    parser.add_argument(
        "--loss",
        choices=["bce", "bce_dice", "ce", "focal_dice", "ce_dice", "tversky", "tversky_dice"],
        default=None,
    )
    parser.add_argument("--exclude-empty", action="store_true", default=None)
    parser.add_argument("--keep-empty", action="store_true",
                        help="do not exclude empty GT (overrides preset)")
    parser.add_argument("--no-hard-neg", action="store_true",
                        help="drop mined hn_* hard-negatives even if preset keeps them")
    parser.add_argument("--full-resize", action="store_true", default=None)
    parser.add_argument("--min-change-frac", type=float, default=None)
    parser.add_argument("--pos-oversample", type=int, default=None)
    parser.add_argument("--min-tile-change", type=float, default=None)
    parser.add_argument("--change-centered", action="store_true")
    parser.add_argument("--no-change-centered", action="store_true")
    parser.add_argument("--pos-only", action="store_true",
                        help="train only on tiles that contain change pixels")
    parser.add_argument("--thr-min", type=float, default=None)
    parser.add_argument("--thr-max", type=float, default=None)
    parser.add_argument("--thr-objective", choices=["f1", "fbeta"], default=None)
    parser.add_argument("--warm-start", type=str, default="")
    parser.add_argument("--visualize", action="store_true")
    parser.add_argument("--no-visualize", action="store_true")
    parser.add_argument("--scheduler", action="store_true")
    parser.add_argument("--no-scheduler", action="store_true")
    parser.add_argument("--eval-run-dir", type=str, default="")
    args = parser.parse_args()

    if args.preset == "wed":
        preset = _WED_PRESET
        preset_name = "wed"
    elif args.preset == "v4":
        preset = _V4_PRESET
        preset_name = "v4"
    elif args.preset == "v3":
        preset = _V3_PRESET
        preset_name = "v3"
    elif args.preset == "v2":
        preset = _V2_PRESET
        preset_name = "v2"
    elif args.preset == "fix":
        preset = _FIX_PRESET
        preset_name = "fix"
    elif args.preset == "day5":
        preset = _DAY5_PRESET
        preset_name = "day5"
    else:
        preset = {}
        preset_name = None

    epochs = args.epochs if args.epochs is not None else preset.get("epochs", 12)
    batch_size = args.batch_size if args.batch_size is not None else preset.get("batch_size", 2)
    lr = args.lr if args.lr is not None else preset.get("lr", 1e-5)
    augment = True if args.augment or preset.get("augment") else False
    stride = args.stride if args.stride is not None else preset.get("stride", _TILE)
    early_stop = (args.early_stop if args.early_stop is not None
                  else preset.get("early_stop_patience", 0))
    loss_mode = args.loss if args.loss is not None else preset.get("loss", "focal_dice")
    exclude_empty = preset.get("exclude_empty", True)
    if args.keep_empty:
        exclude_empty = False
    elif args.exclude_empty:
        exclude_empty = True
    full_resize = preset.get("full_resize", True)
    if args.full_resize:
        full_resize = True
    min_change_frac = (args.min_change_frac if args.min_change_frac is not None
                       else preset.get("min_change_frac", 0.001))
    pos_oversample = (args.pos_oversample if args.pos_oversample is not None
                      else preset.get("pos_oversample", 1))
    min_tile_change = (args.min_tile_change if args.min_tile_change is not None
                       else preset.get("min_tile_change", 0.0))
    change_centered = bool(preset.get("change_centered", False))
    if args.change_centered:
        change_centered = True
    if args.no_change_centered:
        change_centered = False
    pos_only = bool(preset.get("pos_only", False) or args.pos_only)
    thr_min = args.thr_min if args.thr_min is not None else float(preset.get("thr_min", 0.2))
    thr_max = args.thr_max if args.thr_max is not None else float(preset.get("thr_max", 0.7))
    thr_objective = args.thr_objective or preset.get("thr_objective", "f1")
    warm_start = args.warm_start or preset.get("warm_start") or None
    keep_hard_neg = bool(preset.get("keep_hard_neg", True)) and not args.no_hard_neg
    visualize = preset.get("visualize", False)
    if args.visualize:
        visualize = True
    if args.no_visualize:
        visualize = False
    use_scheduler = preset.get("scheduler", False)
    if args.scheduler:
        use_scheduler = True
    if args.no_scheduler:
        use_scheduler = False

    delhi_cd_arg = args.delhi_cd
    if not delhi_cd_arg and args.preset in ("day5", "fix", "v2", "v3", "v4", "wed") and not args.dummy:
        delhi_cd_arg = "data/delhi_cd"

    manifest = Path(args.manifest).resolve() if not args.dummy else None
    delhi_cd = Path(delhi_cd_arg).resolve() if delhi_cd_arg else None
    if args.eval_run_dir:
        finalize_run(Path(args.eval_run_dir).resolve(), manifest, args.dummy)
        return

    train(
        eval_dir=manifest,
        dummy=args.dummy,
        epochs=epochs,
        batch_size=batch_size,
        lr=lr,
        out_root=Path(args.out).resolve(),
        delhi_cd=delhi_cd,
        augment=augment,
        stride=stride,
        early_stop_patience=early_stop,
        loss_mode=loss_mode,
        exclude_empty=exclude_empty,
        full_resize=full_resize,
        min_change_frac=min_change_frac,
        pos_oversample=pos_oversample,
        min_tile_change=min_tile_change,
        visualize=visualize,
        use_scheduler=use_scheduler,
        preset_name=preset_name,
        change_centered=change_centered,
        thr_min=thr_min,
        thr_max=thr_max,
        thr_objective=thr_objective,
        warm_start=warm_start,
        pos_only=pos_only,
        keep_hard_neg=keep_hard_neg,
    )


if __name__ == "__main__":
    main()
