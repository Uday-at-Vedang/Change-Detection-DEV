"""Binary change-mask evaluation metrics.

All functions take boolean / 0-or-255 / 0-or-1 masks of identical shape and
return plain Python floats so results are easy to log and serialize. These are
the standard pixel-wise change-detection metrics: IoU, Dice/F1, precision,
recall, pixel accuracy and the false-positive / false-negative rates.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np


def _to_bool(mask: np.ndarray) -> np.ndarray:
    arr = np.asarray(mask)
    if arr.dtype == bool:
        return arr
    return arr > (127 if arr.max() > 1 else 0)


@dataclass
class ConfusionCounts:
    tp: int
    fp: int
    fn: int
    tn: int


def confusion_counts(pred: np.ndarray, gt: np.ndarray) -> ConfusionCounts:
    """Pixel-wise true/false positive/negative counts for two binary masks."""
    p = _to_bool(pred)
    g = _to_bool(gt)
    if p.shape != g.shape:
        raise ValueError(f"shape mismatch: pred {p.shape} vs gt {g.shape}")
    tp = int(np.sum(p & g))
    fp = int(np.sum(p & ~g))
    fn = int(np.sum(~p & g))
    tn = int(np.sum(~p & ~g))
    return ConfusionCounts(tp=tp, fp=fp, fn=fn, tn=tn)


def _safe_div(num: float, den: float) -> float:
    return float(num) / float(den) if den else 0.0


def binary_metrics(pred: np.ndarray, gt: np.ndarray) -> dict:
    """Return IoU, Dice/F1, precision, recall, accuracy, FPR and FNR.

    Edge case: when ground truth has no positive pixels and the prediction is
    also empty, IoU/Dice/precision/recall are defined as 1.0 (perfect).
    """
    c = confusion_counts(pred, gt)
    tp, fp, fn, tn = c.tp, c.fp, c.fn, c.tn

    if (tp + fp + fn) == 0:
        precision = recall = iou = dice = 1.0
    else:
        precision = _safe_div(tp, tp + fp)
        recall = _safe_div(tp, tp + fn)
        iou = _safe_div(tp, tp + fp + fn)
        dice = _safe_div(2 * tp, 2 * tp + fp + fn)

    f1 = _safe_div(2 * precision * recall, precision + recall) if (precision + recall) else dice
    accuracy = _safe_div(tp + tn, tp + fp + fn + tn)
    fpr = _safe_div(fp, fp + tn)
    fnr = _safe_div(fn, fn + tp)

    # Cohen's kappa: agreement beyond chance (standard CD-benchmark metric)
    total = tp + fp + fn + tn
    if total:
        p_obs = (tp + tn) / total
        p_yes = ((tp + fp) / total) * ((tp + fn) / total)
        p_no = ((fn + tn) / total) * ((fp + tn) / total)
        p_exp = p_yes + p_no
        kappa = _safe_div(p_obs - p_exp, 1.0 - p_exp) if p_exp < 1.0 else 1.0
    else:
        kappa = 0.0

    return {
        "iou": round(iou, 4),
        "dice": round(dice, 4),
        "f1": round(f1, 4),
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "pixelAccuracy": round(accuracy, 4),
        "kappa": round(kappa, 4),
        "falsePositiveRate": round(fpr, 4),
        "falseNegativeRate": round(fnr, 4),
        "counts": asdict(c),
    }
