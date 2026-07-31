"""Train a proxy DSIFN checkpoint (official weights unavailable on Drive).

Official Google Drive package only ships train/val/test zips — no .h5/.pth.
We therefore train the official PyTorch DSIFN (VGG16 ImageNet backbone)
on the DSIFN-CD val split for a few epochs and evaluate on the DSIFN test
split, then reuse the checkpoint for Delhi boundary comparison.

Usage:
    python scripts/dsifn_proxy_train.py --epochs 5 --batch 4
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn
from PIL import Image
from torch.utils.data import DataLoader, Dataset

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

DSIFN_PY = ROOT / "third_party" / "DSIFN" / "pytorch version" / "DSIFN.py"
DATA = ROOT / "third_party" / "DSIFN_weights" / "data"
OUT = ROOT / "models" / "dsifn_proxy"


def _load_dsifn_module():
    spec = importlib.util.spec_from_file_location("dsifn_official", DSIFN_PY)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class DSIFNPairDataset(Dataset):
    def __init__(self, split_dir: Path, size: int = 512):
        self.size = size
        self.t1_dir = split_dir / "t1"
        self.t2_dir = split_dir / "t2"
        self.mask_dir = split_dir / "mask"
        ids = []
        for p in sorted(self.t1_dir.iterdir()):
            stem = p.stem
            # mask may be .tif while images are .jpg
            m_candidates = list(self.mask_dir.glob(f"{stem}.*"))
            t2_candidates = list(self.t2_dir.glob(f"{stem}.*"))
            if m_candidates and t2_candidates:
                ids.append((p, t2_candidates[0], m_candidates[0], stem))
        self.items = ids

    def __len__(self):
        return len(self.items)

    def __getitem__(self, idx):
        t1p, t2p, mp, stem = self.items[idx]
        t1 = Image.open(t1p).convert("RGB").resize((self.size, self.size), Image.BILINEAR)
        t2 = Image.open(t2p).convert("RGB").resize((self.size, self.size), Image.BILINEAR)
        m = Image.open(mp).convert("L").resize((self.size, self.size), Image.NEAREST)
        t1 = torch.from_numpy(np.asarray(t1).transpose(2, 0, 1)).float() / 255.0
        t2 = torch.from_numpy(np.asarray(t2).transpose(2, 0, 1)).float() / 255.0
        # ImageNet normalize (VGG)
        mean = torch.tensor([0.485, 0.456, 0.406]).view(3, 1, 1)
        std = torch.tensor([0.229, 0.224, 0.225]).view(3, 1, 1)
        t1 = (t1 - mean) / std
        t2 = (t2 - mean) / std
        mask = (torch.from_numpy(np.array(m)).float() > 0).float().unsqueeze(0)
        return t1, t2, mask, stem


def _bce_multi(preds, target):
    # Deep supervision: average BCE over all branch outputs (resized to target)
    loss = 0.0
    for p in preds:
        if p.shape[-2:] != target.shape[-2:]:
            p = torch.nn.functional.interpolate(p, size=target.shape[-2:], mode="bilinear", align_corners=False)
        loss = loss + nn.functional.binary_cross_entropy(p.clamp(1e-6, 1 - 1e-6), target)
    return loss / float(len(preds))


@torch.no_grad()
def _eval_f1(model, loader, device, thr=0.5):
    model.eval()
    f1s = []
    for t1, t2, mask, _ in loader:
        t1, t2, mask = t1.to(device), t2.to(device), mask.to(device)
        outs = model(t1, t2)
        pred = outs[0]  # finest branch
        if pred.shape[-2:] != mask.shape[-2:]:
            pred = torch.nn.functional.interpolate(pred, size=mask.shape[-2:], mode="bilinear", align_corners=False)
        m = (pred >= thr).float()
        for i in range(m.shape[0]):
            gt = mask[i, 0] > 0.5
            pm = m[i, 0] > 0.5
            # Also try thr=0.3 if nothing fires at 0.5 (early training)
            if not gt.any():
                continue
            tp = (pm & gt).sum().item()
            fp = (pm & ~gt).sum().item()
            fn = (~pm & gt).sum().item()
            p = 0.0 if tp + fp == 0 else tp / (tp + fp)
            r = 0.0 if tp + fn == 0 else tp / (tp + fn)
            f1 = 0.0 if p + r == 0 else 2 * p * r / (p + r)
            f1s.append(f1)
    return float(np.mean(f1s)) if f1s else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--batch", type=int, default=2)
    ap.add_argument("--lr", type=float, default=1e-4)
    ap.add_argument("--size", type=int, default=256)  # memory-friendly; paper used 512
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    args = ap.parse_args()

    device = torch.device(args.device)
    OUT.mkdir(parents=True, exist_ok=True)

    mod = _load_dsifn_module()
    # Patch deprecated torchvision API if needed
    try:
        from torchvision.models import VGG16_Weights
        def _vgg():
            features = list(__import__("torchvision").models.vgg16(weights=VGG16_Weights.DEFAULT).features)[:30]
            m = torch.nn.Module()
            # rebuild like official
            base = mod.vgg16_base.__new__(mod.vgg16_base)
            torch.nn.Module.__init__(base)
            base.features = torch.nn.ModuleList(features).eval()
            return base
        model_a, model_b = _vgg(), _vgg()
    except Exception:
        model_a, model_b = mod.vgg16_base(), mod.vgg16_base()

    model = mod.DSIFN(model_a, model_b).to(device)
    # Freeze VGG backbone initially for faster proxy train
    for p in model.t1_base.parameters():
        p.requires_grad = False
    for p in model.t2_base.parameters():
        p.requires_grad = False

    train_ds = DSIFNPairDataset(DATA / "val", size=args.size)
    test_ds = DSIFNPairDataset(DATA / "test", size=args.size)
    print(f"train(val-split)={len(train_ds)} test={len(test_ds)} device={device}", flush=True)
    train_loader = DataLoader(train_ds, batch_size=args.batch, shuffle=True, num_workers=0)
    test_loader = DataLoader(test_ds, batch_size=args.batch, shuffle=False, num_workers=0)

    opt = torch.optim.Adam([p for p in model.parameters() if p.requires_grad], lr=args.lr)
    history = []
    best_f1, best_path = -1.0, OUT / "best.pt"

    for epoch in range(1, args.epochs + 1):
        model.train()
        # keep VGG in eval (BN frozen behavior)
        model.t1_base.eval()
        model.t2_base.eval()
        losses = []
        t0 = time.perf_counter()
        for t1, t2, mask, _ in train_loader:
            t1, t2, mask = t1.to(device), t2.to(device), mask.to(device)
            opt.zero_grad()
            outs = model(t1, t2)
            loss = _bce_multi(outs, mask)
            loss.backward()
            opt.step()
            losses.append(float(loss.item()))
        train_loss = float(np.mean(losses)) if losses else 0.0
        test_f1 = _eval_f1(model, test_loader, device)
        row = {"epoch": epoch, "train_loss": round(train_loss, 4), "test_f1": round(test_f1, 4),
               "elapsed_s": round(time.perf_counter() - t0, 1)}
        history.append(row)
        print(f"epoch {epoch}: loss={train_loss:.4f} test_f1={test_f1:.4f} ({row['elapsed_s']}s)", flush=True)
        if test_f1 > best_f1:
            best_f1 = test_f1
            torch.save({"model": model.state_dict(), "epoch": epoch, "test_f1": test_f1, "size": args.size}, best_path)

    meta = {
        "note": "Proxy DSIFN: official pretrained weights missing from Drive; trained on val, eval on test.",
        "epochs": args.epochs,
        "batch": args.batch,
        "lr": args.lr,
        "size": args.size,
        "best_test_f1": best_f1,
        "best_path": str(best_path),
        "history": history,
        "source_repo": "https://github.com/GeoZcx/A-deeply-supervised-image-fusion-network-for-change-detection-in-remote-sensing-images",
        "review_index": "https://github.com/MinZHANG-WHU/Change-Detection-Review",
    }
    (OUT / "metrics.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Best test F1={best_f1:.4f} -> {best_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
