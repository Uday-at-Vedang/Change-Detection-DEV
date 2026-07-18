"""
Fine-tune AdaptFormer on Delhi change-detection tiles (scaffold).

Day 1 deliverable: end-to-end runnable training loop on placeholder data.
Replace ``--dummy`` pairs with ``docs/delhi_eval`` once Priyanka's manifest
and labels are ready.

Run from change_detection_webapp:
    python scripts/finetune_adaptformer.py --dummy --epochs 2
    python scripts/finetune_adaptformer.py --eval-dir docs/delhi_eval --epochs 20

Outputs checkpoints under ``runs/finetune_adaptformer/<run_id>/``.
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


def _try_torch():
    try:
        import torch
        from torch.utils.data import DataLoader, Dataset
        from transformers import AutoImageProcessor, AutoModel
        return torch, DataLoader, Dataset, AutoImageProcessor, AutoModel
    except ImportError as exc:
        raise SystemExit(
            "PyTorch + transformers required for fine-tuning. "
            f"Import error: {exc}"
        ) from exc


class DelhiTileDataset:
    """Minimal tile dataset — wraps torch Dataset when torch is available."""

    def __init__(self, pairs: list[tuple], crop_size: int = _TILE, train: bool = True):
        torch, _dl, Dataset, _proc, _model = _try_torch()
        self.crop_size = crop_size
        self.train = train
        self.samples: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []

        for before, after, gt, _pair_id in pairs:
            h, w = before.shape[:2]
            if h < crop_size or w < crop_size:
                before = np.array(Image.fromarray(before).resize((crop_size, crop_size)))
                after = np.array(Image.fromarray(after).resize((crop_size, crop_size)))
                gt = np.array(Image.fromarray(gt).resize((crop_size, crop_size)))
                h = w = crop_size

            # Anchor crops at every corner + center instead of a single fixed
            # top-left tile. For images only slightly larger than crop_size
            # (our 300x300 Delhi tiles vs. a 256px crop), a plain stride loop
            # yields exactly one crop anchored at (0,0) — silently dropping any
            # labeled region that falls outside that corner. Fixed-position
            # sampling (not random) keeps runs reproducible.
            y_anchors = sorted({0, max(0, h - crop_size), max(0, (h - crop_size) // 2)})
            x_anchors = sorted({0, max(0, w - crop_size), max(0, (w - crop_size) // 2)})
            for y in y_anchors:
                for x in x_anchors:
                    self.samples.append((
                        before[y:y + crop_size, x:x + crop_size],
                        after[y:y + crop_size, x:x + crop_size],
                        gt[y:y + crop_size, x:x + crop_size],
                    ))

        class _Inner(Dataset):
            def __init__(inner_self, outer):
                inner_self.outer = outer

            def __len__(inner_self):
                return len(inner_self.outer.samples)

            def __getitem__(inner_self, idx):
                b, a, g = inner_self.outer.samples[idx]
                return b, a, (g > 127).astype(np.float32)

        self._dataset = _Inner(self)

    def torch_dataset(self):
        return self._dataset


def _dummy_pairs(n: int = 8, size: int = 256) -> list[tuple]:
    """Synthetic before/after/GT tuples for scaffold runs without Delhi manifest."""
    rng = np.random.default_rng(42)
    out = []
    for i in range(n):
        before = rng.integers(40, 200, (size, size, 3), dtype=np.uint8)
        after = before.copy()
        gt = np.zeros((size, size), dtype=np.uint8)
        x, y, w, h = 40 + i * 10, 50 + i * 5, 48, 36
        if y + h < size and x + w < size:
            after[y:y + h, x:x + w] = [210, 200, 190]
            gt[y:y + h, x:x + w] = 255
        out.append((before, after, gt, f"dummy_{i:02d}"))
    return out


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
    if loaded:
        raise SystemExit(
            f"{len(loaded)} pair(s) on disk but no GT masks yet. "
            "Use --dummy until Priyanka adds labels to docs/delhi_eval/labels/."
        )
    raise SystemExit("No Delhi pairs with images on disk. Use --dummy for scaffold runs.")


def _split_pairs(pairs: list[tuple], seed: int = 0,
                 train_frac: float = 0.70, val_frac: float = 0.15):
    """70/15/15 train/val/test split (Day 3 plan)."""
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


def _compute_pos_weight(train_ds, max_weight: float = 50.0) -> float:
    """Inverse-frequency weight for the changed (positive) class.

    Change-detection masks are heavily imbalanced toward "no change" (our
    Delhi masks run ~2-10% positive pixels). Plain BCELoss on this imbalance
    has a well-known failure mode: predicting all-background is already
    near loss-optimal, so the model never learns real signal -- this is the
    most likely explanation for a val/test F1 stuck near 0. Weighting the
    positive class by (negative_count / positive_count) counteracts that.
    """
    total_pos = 0
    total_px = 0
    for _b, _a, gt in train_ds.samples:
        mask = gt > 127
        total_pos += int(mask.sum())
        total_px += mask.size
    if total_pos == 0:
        return 1.0
    pos_frac = total_pos / total_px
    weight = (1.0 - pos_frac) / pos_frac
    return float(min(max_weight, max(1.0, weight)))


def _weighted_bce(prob, target, pos_weight: float, eps: float = 1e-7):
    """Manual weighted binary cross-entropy on probabilities (not logits) —
    ``BCEWithLogitsLoss``'s ``pos_weight`` only applies to raw logits, but the
    model output here is already passed through ``_logits_to_change_prob``."""
    torch, *_ = _try_torch()
    prob = prob.clamp(eps, 1.0 - eps)
    loss = -(pos_weight * target * torch.log(prob) + (1.0 - target) * torch.log(1.0 - prob))
    return loss.mean()


def _predict_mask(model, processor, device, before, after, threshold=0.5):
    torch, *_ = _try_torch()
    from PIL import Image as PILImage
    from app.model_inference import _logits_to_change_prob

    inputs = processor(
        images=(PILImage.fromarray(before), PILImage.fromarray(after)),
        return_tensors="pt",
    )
    inputs = {k: v.to(device) for k, v in inputs.items()}
    with torch.no_grad():
        outputs = model(**inputs)
    prob = _logits_to_change_prob(outputs.logits, torch)
    score = prob.cpu().numpy().astype(np.float32)
    mask = (score >= threshold).astype(np.uint8) * 255
    return mask, score


def _eval_pairs(model, processor, device, pairs: list[tuple]) -> dict:
    f1s = []
    for before, after, gt, pair_id in pairs:
        mask, _ = _predict_mask(model, processor, device, before, after)
        if mask.shape != gt.shape:
            from cv2 import resize, INTER_NEAREST
            mask = resize(mask, (gt.shape[1], gt.shape[0]), interpolation=INTER_NEAREST)
        f1s.append(binary_metrics(mask, gt)["f1"])
    return {"mean_f1": round(float(np.mean(f1s)), 4) if f1s else 0.0, "n": len(f1s)}


def train(
    eval_dir: Path | None,
    dummy: bool,
    epochs: int,
    batch_size: int,
    lr: float,
    out_root: Path,
) -> Path:
    torch, DataLoader, _Dataset, AutoImageProcessor, AutoModel = _try_torch()

    pairs = _load_pairs(eval_dir, dummy)
    train_pairs, val_pairs, test_pairs = _split_pairs(pairs)
    train_ds = DelhiTileDataset(train_pairs, train=True)
    val_ds = DelhiTileDataset(val_pairs, train=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    split_info = {
        "train": [p[3] for p in train_pairs],
        "val": [p[3] for p in val_pairs],
        "test": [p[3] for p in test_pairs],
        "split": "70/15/15",
    }
    print(f"Device: {device} | split {split_info['split']} | "
          f"train={len(train_pairs)} val={len(val_pairs)} test={len(test_pairs)} | "
          f"tiles train={len(train_ds.samples)} val={len(val_ds.samples)}")

    processor = AutoImageProcessor.from_pretrained(_MODEL_ID, trust_remote_code=True)
    model = AutoModel.from_pretrained(_MODEL_ID, trust_remote_code=True)
    model.to(device)
    model.train()

    train_loader = DataLoader(
        train_ds.torch_dataset(), batch_size=batch_size, shuffle=True, num_workers=0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    pos_weight = _compute_pos_weight(train_ds)
    print(f"Positive-class weight (class-imbalance correction): {pos_weight:.2f}")

    run_id = time.strftime("%Y%m%d_%H%M%S")
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "split.json").write_text(json.dumps(split_info, indent=2), encoding="utf-8")

    history = []
    best_f1 = -1.0
    best_path = run_dir / "best"

    for epoch in range(1, epochs + 1):
        model.train()
        total_loss = 0.0
        n_batches = 0
        from PIL import Image as PILImage
        from app.model_inference import _logits_to_change_prob

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
                labels_t = torch.from_numpy(label).to(device).unsqueeze(0)

                outputs = model(**inputs)
                prob = _logits_to_change_prob(outputs.logits, torch).unsqueeze(0)
                if prob.shape[-2:] != labels_t.shape[-2:]:
                    prob = torch.nn.functional.interpolate(
                        prob.unsqueeze(1), size=labels_t.shape[-2:],
                        mode="bilinear", align_corners=False).squeeze(1)
                batch_loss = batch_loss + _weighted_bce(prob, labels_t, pos_weight)

            batch_loss = batch_loss / max(before_np.shape[0], 1)
            batch_loss.backward()
            optimizer.step()
            total_loss += float(batch_loss.item())
            n_batches += 1

        model.eval()
        val_metrics = _eval_pairs(model, processor, device, val_pairs)
        avg_loss = total_loss / max(n_batches, 1)
        row = {
            "epoch": epoch,
            "train_loss": round(avg_loss, 4),
            "val_mean_f1": val_metrics["mean_f1"],
        }
        history.append(row)
        print(f"  epoch {epoch}/{epochs} loss={avg_loss:.4f} val_F1={val_metrics['mean_f1']:.4f}")
        (run_dir / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")

        if val_metrics["mean_f1"] >= best_f1:
            best_f1 = val_metrics["mean_f1"]
            best_path.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(best_path)
            processor.save_pretrained(best_path)

    # Held-out test eval using best val checkpoint
    test_metrics = {"mean_f1": 0.0, "n": 0}
    if test_pairs and best_path.is_dir():
        best_model = AutoModel.from_pretrained(best_path, trust_remote_code=True)
        best_model.to(device)
        best_processor = AutoImageProcessor.from_pretrained(best_path, trust_remote_code=True)
        best_model.eval()
        test_metrics = _eval_pairs(best_model, best_processor, device, test_pairs)

    meta = {
        "model_id": _MODEL_ID,
        "dummy": dummy,
        "epochs": epochs,
        "device": str(device),
        "split": split_info,
        "train_pairs": len(train_pairs),
        "val_pairs": len(val_pairs),
        "test_pairs": len(test_pairs),
        "train_tiles": len(train_ds.samples),
        "val_tiles": len(val_ds.samples),
        "pos_weight": round(pos_weight, 2),
        "best_val_f1": best_f1,
        "test_mean_f1": test_metrics["mean_f1"],
        "history": history,
    }
    (run_dir / "metrics.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Test F1={test_metrics['mean_f1']:.4f} ({test_metrics['n']} pairs)")
    print(f"Run complete. Artifacts: {run_dir}")
    return run_dir


def finalize_run(
    run_dir: Path,
    eval_dir: Path | None,
    dummy: bool,
    history: list[dict] | None = None,
) -> Path:
    """Run held-out test eval on a finished run dir and write metrics.json."""
    torch, *_rest, AutoImageProcessor, AutoModel = _try_torch()

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

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    best_model = AutoModel.from_pretrained(best_path, trust_remote_code=True)
    best_model.to(device)
    best_processor = AutoImageProcessor.from_pretrained(best_path, trust_remote_code=True)
    best_model.eval()
    test_metrics = _eval_pairs(best_model, best_processor, device, test_pairs)

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
        "split": split_info,
        "train_pairs": len(split_info.get("train", [])),
        "val_pairs": len(split_info.get("val", [])),
        "test_pairs": len(test_pairs),
        "best_val_f1": best_val_f1,
        "test_mean_f1": test_metrics["mean_f1"],
        "history": hist,
        "finalized": True,
    }
    (run_dir / "metrics.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Test F1={test_metrics['mean_f1']:.4f} ({test_metrics['n']} pairs)")
    print(f"Wrote {run_dir / 'metrics.json'}")
    return run_dir


def main():
    parser = argparse.ArgumentParser(description="Fine-tune AdaptFormer on Delhi tiles")
    parser.add_argument("--manifest", type=str, default="docs/delhi_eval/manifest.json",
                        help="Priyanka's Delhi manifest (repo-relative paths)")
    parser.add_argument("--dummy", action="store_true",
                        help="use in-memory synthetic pairs (Day 1 scaffold)")
    parser.add_argument("--epochs", type=int, default=12,
                        help="training epochs (Day 3 smoke test: 10-15)")
    parser.add_argument("--batch-size", type=int, default=2)
    parser.add_argument("--lr", type=float, default=1e-5)
    parser.add_argument("--out", type=str, default="runs/finetune_adaptformer")
    parser.add_argument("--eval-run-dir", type=str, default="",
                        help="skip training; run test eval on an existing run dir and write metrics.json")
    args = parser.parse_args()

    manifest = Path(args.manifest).resolve() if not args.dummy else None
    if args.eval_run_dir:
        finalize_run(Path(args.eval_run_dir).resolve(), manifest, args.dummy)
        return

    train(
        eval_dir=manifest,
        dummy=args.dummy,
        epochs=args.epochs,
        batch_size=args.batch_size,
        lr=args.lr,
        out_root=Path(args.out).resolve(),
    )


if __name__ == "__main__":
    main()
