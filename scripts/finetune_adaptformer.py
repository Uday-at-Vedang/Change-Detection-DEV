"""
Fine-tune AdaptFormer on Delhi change-detection tiles.

Day 4: 70/15/15 under ``data/delhi_cd/`` + 10–15 epoch smoke test.
Day 5: tune LR / aug / loss from smoke; queue full 20–30 epoch run.

Run from change_detection_webapp:
    python scripts/build_delhi_cd_splits.py
    python scripts/finetune_adaptformer.py --delhi-cd data/delhi_cd --preset day5
    python scripts/finetune_adaptformer.py --dummy --epochs 2   # scaffold only

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

# Day 5 defaults from Day 4 smoke (val F1≈0, loss falling without F1 lift).
_DAY5_PRESET = {
    "epochs": 30,
    "lr": 3e-5,
    "batch_size": 2,
    "augment": True,
    "stride": 128,
    "early_stop_patience": 8,
    "loss": "bce_dice",
}


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


def _augment_triplet(b: np.ndarray, a: np.ndarray, g: np.ndarray, rng: random.Random):
    if rng.random() < 0.5:
        b = np.ascontiguousarray(np.flip(b, axis=1))
        a = np.ascontiguousarray(np.flip(a, axis=1))
        g = np.ascontiguousarray(np.flip(g, axis=1))
    if rng.random() < 0.5:
        b = np.ascontiguousarray(np.flip(b, axis=0))
        a = np.ascontiguousarray(np.flip(a, axis=0))
        g = np.ascontiguousarray(np.flip(g, axis=0))
    return b, a, g


class DelhiTileDataset:
    """Tile dataset with denser stride + optional train-time flips."""

    def __init__(self, pairs: list[tuple], crop_size: int = _TILE, train: bool = True,
                 stride: int | None = None, augment: bool = False, seed: int = 0):
        _torch, _dl, Dataset, _proc, _model = _try_torch()
        self.crop_size = crop_size
        self.train = train
        self.augment = bool(augment and train)
        self._rng = random.Random(seed)
        self.samples: list[tuple[np.ndarray, np.ndarray, np.ndarray]] = []
        use_stride = stride if stride is not None else (crop_size // 2 if train else crop_size)
        use_stride = max(32, min(crop_size, int(use_stride)))

        for before, after, gt, _pair_id in pairs:
            h, w = before.shape[:2]
            if h < crop_size or w < crop_size:
                before = np.array(Image.fromarray(before).resize((crop_size, crop_size)))
                after = np.array(Image.fromarray(after).resize((crop_size, crop_size)))
                gt = np.array(Image.fromarray(gt).resize((crop_size, crop_size)))
                h = w = crop_size
            for y in range(0, h - crop_size + 1, use_stride):
                for x in range(0, w - crop_size + 1, use_stride):
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
                b, a, g = b.copy(), a.copy(), g.copy()
                if inner_self.outer.augment:
                    b, a, g = _augment_triplet(b, a, g, inner_self.outer._rng)
                return b, a, (g > 127).astype(np.float32)

        self._dataset = _Inner(self)

    def torch_dataset(self):
        return self._dataset


def _dice_loss(prob, target, eps: float = 1e-6):
    p = prob.reshape(-1)
    t = target.reshape(-1)
    inter = (p * t).sum()
    return 1.0 - (2.0 * inter + eps) / (p.sum() + t.sum() + eps)


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
    if loaded:
        raise SystemExit(
            f"{len(loaded)} pair(s) on disk but no GT masks yet. "
            "Use --dummy until Priyanka adds labels to docs/delhi_eval/labels/."
        )
    raise SystemExit("No Delhi pairs with images on disk. Use --dummy for scaffold runs.")


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
    for before, after, gt, _pair_id in pairs:
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
    delhi_cd: Path | None = None,
    augment: bool = False,
    stride: int = 128,
    early_stop_patience: int = 0,
    loss_mode: str = "bce",
) -> Path:
    torch, DataLoader, _Dataset, AutoImageProcessor, AutoModel = _try_torch()

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

    train_ds = DelhiTileDataset(
        train_pairs, train=True, stride=stride, augment=augment, seed=0)
    val_ds = DelhiTileDataset(
        val_pairs, train=False, stride=_TILE, augment=False)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print(f"Device: {device} | split {split_info['split']} | "
          f"train={len(train_pairs)} val={len(val_pairs)} test={len(test_pairs)} | "
          f"tiles train={len(train_ds.samples)} val={len(val_ds.samples)} | "
          f"lr={lr} aug={augment} loss={loss_mode} patience={early_stop_patience}")

    processor = AutoImageProcessor.from_pretrained(_MODEL_ID, trust_remote_code=True)
    model = AutoModel.from_pretrained(_MODEL_ID, trust_remote_code=True)
    model.to(device)
    model.train()

    train_loader = DataLoader(
        train_ds.torch_dataset(), batch_size=batch_size, shuffle=True, num_workers=0)
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    bce = torch.nn.BCELoss()

    run_id = time.strftime("%Y%m%d_%H%M%S")
    run_dir = out_root / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "split.json").write_text(json.dumps(split_info, indent=2), encoding="utf-8")
    (run_dir / "config.json").write_text(json.dumps({
        "model_id": _MODEL_ID,
        "epochs": epochs,
        "lr": lr,
        "batch_size": batch_size,
        "augment": augment,
        "stride": stride,
        "early_stop_patience": early_stop_patience,
        "loss": loss_mode,
        "delhi_cd": str(delhi_cd) if delhi_cd else None,
    }, indent=2), encoding="utf-8")

    history = []
    best_f1 = -1.0
    best_path = run_dir / "best"
    stale = 0

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
                sample_loss = bce(prob, labels_t)
                if loss_mode == "bce_dice":
                    sample_loss = 0.5 * sample_loss + 0.5 * _dice_loss(prob, labels_t)
                batch_loss = batch_loss + sample_loss

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
        print(f"  epoch {epoch}/{epochs} loss={avg_loss:.4f} val_F1={val_metrics['mean_f1']:.4f}",
              flush=True)
        (run_dir / "history.json").write_text(json.dumps(history, indent=2), encoding="utf-8")

        improved = val_metrics["mean_f1"] > best_f1 + 1e-6
        if improved or best_f1 < 0:
            best_f1 = val_metrics["mean_f1"]
            stale = 0
            best_path.mkdir(parents=True, exist_ok=True)
            model.save_pretrained(best_path)
            processor.save_pretrained(best_path)
        else:
            stale += 1
            if early_stop_patience and stale >= early_stop_patience:
                print(f"Early stop at epoch {epoch} (no val F1 lift for "
                      f"{early_stop_patience} epochs). Best val F1={best_f1:.4f}",
                      flush=True)
                break

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
        "epochs_ran": len(history),
        "lr": lr,
        "augment": augment,
        "stride": stride,
        "loss": loss_mode,
        "device": str(device),
        "split": split_info,
        "train_pairs": len(train_pairs),
        "val_pairs": len(val_pairs),
        "test_pairs": len(test_pairs),
        "train_tiles": len(train_ds.samples),
        "best_val_f1": best_f1 if best_f1 >= 0 else 0.0,
        "test_mean_f1": test_metrics["mean_f1"],
        "history": history,
        "preset": "day5" if (augment and loss_mode == "bce_dice") else None,
    }
    (run_dir / "metrics.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Test F1={test_metrics['mean_f1']:.4f} ({test_metrics['n']} pairs)", flush=True)
    print(f"Run complete. Artifacts: {run_dir}", flush=True)
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
    parser.add_argument("--manifest", type=str, default="docs/delhi_eval/manifest.json")
    parser.add_argument("--dummy", action="store_true")
    parser.add_argument("--preset", choices=["", "day5"], default="",
                        help="day5 = lr=3e-5, epochs=30, aug, bce_dice, early-stop")
    parser.add_argument("--epochs", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=None)
    parser.add_argument("--lr", type=float, default=None)
    parser.add_argument("--out", type=str, default="runs/finetune_adaptformer")
    parser.add_argument("--delhi-cd", type=str, default="",
                        help="use data/delhi_cd train/val/test manifests")
    parser.add_argument("--augment", action="store_true",
                        help="enable hflip/vflip on train tiles")
    parser.add_argument("--stride", type=int, default=None,
                        help="train tile stride (default 256 smoke / 128 day5)")
    parser.add_argument("--early-stop", type=int, default=None,
                        help="stop after N epochs without val F1 improvement (0=off)")
    parser.add_argument("--loss", choices=["bce", "bce_dice"], default=None)
    parser.add_argument("--eval-run-dir", type=str, default="",
                        help="skip training; finalize test eval on an existing run")
    args = parser.parse_args()

    preset = _DAY5_PRESET if args.preset == "day5" else {}
    epochs = args.epochs if args.epochs is not None else preset.get("epochs", 12)
    batch_size = args.batch_size if args.batch_size is not None else preset.get("batch_size", 2)
    lr = args.lr if args.lr is not None else preset.get("lr", 1e-5)
    augment = True if args.augment or preset.get("augment") else False
    stride = args.stride if args.stride is not None else preset.get("stride", _TILE)
    early_stop = (args.early_stop if args.early_stop is not None
                  else preset.get("early_stop_patience", 0))
    loss_mode = args.loss if args.loss is not None else preset.get("loss", "bce")

    # Day 5 convenience: default to data/delhi_cd when using --preset day5
    delhi_cd_arg = args.delhi_cd
    if not delhi_cd_arg and args.preset == "day5" and not args.dummy:
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
    )


if __name__ == "__main__":
    main()
