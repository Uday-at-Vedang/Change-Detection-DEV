"""Smoke tests for app.dda.pair_align (run: venv/Scripts/python scripts/test_pair_align.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np

from app.dda.pair_align import (
    are_identical, assess_alignment, content_hash,
)


def _checker(size=256, shift=0):
    """A textured RGB checkerboard, optionally translated to simulate misalignment."""
    rng = np.random.default_rng(0)
    base = rng.integers(0, 255, (size + 32, size + 32, 3), dtype=np.uint8)
    return np.ascontiguousarray(base[shift:shift + size, shift:shift + size])


def test_identical_detected():
    img = _checker()
    assert are_identical(img, img.copy())
    assert content_hash(img) == content_hash(img.copy())
    print("PASS identical detection")


def test_different_not_identical():
    assert not are_identical(_checker(shift=0), _checker(shift=4))
    print("PASS non-identical distinction")


def test_alignment_quality():
    img = _checker(shift=0)
    status_same, ncc_same = assess_alignment(img, img.copy())
    status_shift, ncc_shift = assess_alignment(_checker(shift=0), _checker(shift=16))
    assert status_same == "ok" and ncc_same > 0.9, (status_same, ncc_same)
    assert ncc_shift < ncc_same, (ncc_shift, ncc_same)
    print(f"PASS alignment quality (aligned NCC={ncc_same:.2f}, shifted NCC={ncc_shift:.2f})")


if __name__ == "__main__":
    test_identical_detected()
    test_different_not_identical()
    test_alignment_quality()
    print("ALL PASS")
