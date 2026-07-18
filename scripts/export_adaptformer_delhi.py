"""
Day 6 (Uday): export the best fine-tuned AdaptFormer checkpoint to
``models/adaptformer_delhi/``.

Copies a HuggingFace ``save_pretrained`` directory (from finetune runs) and
optionally writes ``best.pt`` (state_dict) for the plan deliverable name.

Usage:
    python scripts/export_adaptformer_delhi.py
    python scripts/export_adaptformer_delhi.py --src runs/day5_full/20260716_142643/best
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CANDIDATES = [
    ROOT / "runs" / "finetune_v3",
    ROOT / "runs" / "finetune_v2",
    ROOT / "runs" / "finetune_fix",
    ROOT / "runs" / "day5_full",
    ROOT / "runs" / "finetune_adaptformer",
]


def _find_latest_best() -> Path | None:
    found: list[Path] = []
    for cand in DEFAULT_CANDIDATES:
        if cand.name == "best" and cand.is_dir():
            found.append(cand)
            continue
        if cand.is_dir():
            found.extend([p for p in cand.glob("*/best") if p.is_dir()])
    if not found:
        return None
    return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="", help="path to HF best/ directory")
    parser.add_argument("--out", default="models/adaptformer_delhi")
    args = parser.parse_args()

    src = Path(args.src).resolve() if args.src else _find_latest_best()
    if src is None or not src.is_dir():
        raise SystemExit(
            "No fine-tune checkpoint found. Run Day 5 training first or pass --src."
        )

    out_root = ROOT / args.out
    best_dir = out_root / "best"
    best_dir.mkdir(parents=True, exist_ok=True)

    # Copy HF artifacts
    for name in ("config.json", "model.safetensors", "pytorch_model.bin",
                 "preprocessor_config.json", "tokenizer_config.json",
                 "special_tokens_map.json"):
        f = src / name
        if f.is_file():
            shutil.copy2(f, best_dir / name)
    # Copy any remaining files (custom code modules if present)
    for f in src.iterdir():
        if f.is_file() and not (best_dir / f.name).exists():
            shutil.copy2(f, best_dir / f.name)

    meta = {
        "source": str(src),
        "exported_to": "models/adaptformer_delhi/best",
        "load_via": "ADAPTFORMER_WEIGHTS=models/adaptformer_delhi/best",
    }

    # Optional best.pt state_dict for plan naming
    try:
        import torch
        from transformers import AutoModel
        model = AutoModel.from_pretrained(best_dir, trust_remote_code=True)
        pt_path = out_root / "best.pt"
        torch.save(model.state_dict(), pt_path)
        meta["best_pt"] = str(pt_path.relative_to(ROOT))
        print(f"Wrote {pt_path}")
    except Exception as exc:
        meta["best_pt_error"] = str(exc)
        print(f"Warning: could not write best.pt ({exc}); HF dir still exported.")

    (out_root / "export_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(f"Exported AdaptFormer Delhi weights -> {best_dir}")
    print("Set ADAPTFORMER_WEIGHTS=models/adaptformer_delhi/best to use in the app.")


if __name__ == "__main__":
    main()
