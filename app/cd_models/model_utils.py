"""
Shared utilities for model loading, tile processing, and multi-scale detection.
"""
import cv2
import numpy as np


def make_blend_weights(tile_size: int, overlap: int) -> np.ndarray:
    """Raised-cosine (trapezoid) 2D weight for seamless tile stitching.

    Ramps from 0 to 1 across the overlap band on each edge and stays flat in
    the interior, so overlapping tile predictions blend without visible seams.
    """
    overlap = max(0, min(overlap, tile_size // 2))
    if overlap == 0:
        return np.ones((tile_size, tile_size), dtype=np.float32)
    ramp = np.linspace(0.0, 1.0, overlap, dtype=np.float32)
    flat = np.ones(tile_size - 2 * overlap, dtype=np.float32)
    profile = np.concatenate([ramp, flat, ramp[::-1]])
    return np.outer(profile, profile).astype(np.float32)


def tiled_score_map(score_tile_fn, img1, img2, tile_size: int = 256,
                    overlap: int | None = None,
                    score_batch_fn=None, batch: int = 1):
    """Sliding-window tiled scoring with reflect padding + cosine blending.

    ``score_tile_fn(tile1, tile2)`` must return a float32 array in [0, 1] the
    same height/width as the input tile. Returns a float32 change-probability
    map cropped back to the original (h, w). This is the single shared
    implementation used by every deep model so blending stays consistent.

    When ``batch > 1`` and ``score_batch_fn`` is provided, tiles are scored in
    groups (``score_batch_fn(list[(t1, t2)]) -> list[prob]``) so GPUs can run
    several tiles per forward pass.
    """
    h, w = img1.shape[:2]
    if overlap is None:
        overlap = tile_size // 4
    overlap = max(0, min(overlap, tile_size // 2))
    stride = max(1, tile_size - overlap)

    pad_h = (tile_size - h % tile_size) % tile_size
    pad_w = (tile_size - w % tile_size) % tile_size
    # Guarantee at least one full tile even for small inputs
    pad_h = max(pad_h, max(0, tile_size - h))
    pad_w = max(pad_w, max(0, tile_size - w))
    if pad_h or pad_w:
        img1 = np.pad(img1, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")
        img2 = np.pad(img2, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")

    ph, pw = img1.shape[:2]
    score_sum = np.zeros((ph, pw), dtype=np.float32)
    count = np.zeros((ph, pw), dtype=np.float32)
    weight_2d = make_blend_weights(tile_size, overlap)

    ys = list(range(0, ph - tile_size + 1, stride))
    xs = list(range(0, pw - tile_size + 1, stride))
    # Ensure the far edges are always covered
    if ys and ys[-1] != ph - tile_size:
        ys.append(ph - tile_size)
    if xs and xs[-1] != pw - tile_size:
        xs.append(pw - tile_size)

    coords = [(y0, x0) for y0 in ys for x0 in xs]

    def _accumulate(y0, x0, prob):
        if prob.shape != (tile_size, tile_size):
            prob = cv2.resize(prob.astype(np.float32), (tile_size, tile_size),
                              interpolation=cv2.INTER_LINEAR)
        score_sum[y0:y0 + tile_size, x0:x0 + tile_size] += prob * weight_2d
        count[y0:y0 + tile_size, x0:x0 + tile_size] += weight_2d

    use_batch = score_batch_fn is not None and batch > 1
    if use_batch:
        for i in range(0, len(coords), batch):
            chunk = coords[i:i + batch]
            pairs = [
                (np.ascontiguousarray(img1[y:y + tile_size, x:x + tile_size]),
                 np.ascontiguousarray(img2[y:y + tile_size, x:x + tile_size]))
                for (y, x) in chunk
            ]
            probs = score_batch_fn(pairs)
            for (y0, x0), prob in zip(chunk, probs):
                _accumulate(y0, x0, prob)
    else:
        for (y0, x0) in coords:
            t1 = np.ascontiguousarray(img1[y0:y0 + tile_size, x0:x0 + tile_size])
            t2 = np.ascontiguousarray(img2[y0:y0 + tile_size, x0:x0 + tile_size])
            _accumulate(y0, x0, score_tile_fn(t1, t2))

    count = np.maximum(count, 1e-6)
    avg = score_sum / count
    return avg[:h, :w]


def split_into_tiles(img, tile_size=512, overlap=64):
    """
    Split an image into overlapping tiles.
    Returns list of (tile, y_offset, x_offset) tuples.
    """
    h, w = img.shape[:2]
    stride = tile_size - overlap
    tiles = []

    pad_h = (tile_size - h % tile_size) % tile_size if h % tile_size else 0
    pad_w = (tile_size - w % tile_size) % tile_size if w % tile_size else 0
    if pad_h or pad_w:
        img = np.pad(img, ((0, pad_h), (0, pad_w), (0, 0)) if img.ndim == 3
                     else ((0, pad_h), (0, pad_w)), mode="reflect")

    ph, pw = img.shape[:2]
    for y in range(0, ph - tile_size + 1, stride):
        for x in range(0, pw - tile_size + 1, stride):
            tile = img[y:y+tile_size, x:x+tile_size]
            tiles.append((tile, y, x))

    return tiles, (ph, pw), (h, w)


def merge_tile_masks(tile_results, padded_shape, orig_shape, tile_size=512, overlap=64):
    """
    Merge tile-level binary masks back into a single full-resolution mask.
    Uses raised-cosine blending to avoid tile boundary artifacts.
    """
    ph, pw = padded_shape
    h, w = orig_shape

    score_sum = np.zeros((ph, pw), dtype=np.float32)
    count = np.zeros((ph, pw), dtype=np.float32)

    ramp = np.linspace(0, 1, overlap)
    flat = np.ones(tile_size - 2 * overlap)
    profile = np.concatenate([ramp, flat, ramp[::-1]])
    weight_2d = np.outer(profile, profile).astype(np.float32)

    for (mask_tile, y, x) in tile_results:
        score = mask_tile.astype(np.float32) / 255.0 if mask_tile.max() > 1 else mask_tile.astype(np.float32)
        if score.shape != (tile_size, tile_size):
            score = cv2.resize(score, (tile_size, tile_size))
        score_sum[y:y+tile_size, x:x+tile_size] += score * weight_2d
        count[y:y+tile_size, x:x+tile_size] += weight_2d

    count = np.maximum(count, 1e-6)
    merged = score_sum / count
    merged = merged[:h, :w]
    return (merged * 255).astype(np.uint8)


def multiscale_detect(detect_fn, img1, img2, scales=(1.0, 0.5, 0.25)):
    """
    Run a detection function at multiple scales and combine via logical OR.
    detect_fn(img1, img2) -> uint8 mask [0|255].
    Captures small structures at full res and large regions at coarse scales.
    """
    h, w = img1.shape[:2]
    combined = np.zeros((h, w), dtype=np.uint8)

    for scale in scales:
        if scale == 1.0:
            s1, s2 = img1, img2
        else:
            sh, sw = max(64, int(h * scale)), max(64, int(w * scale))
            s1 = cv2.resize(img1, (sw, sh), interpolation=cv2.INTER_AREA)
            s2 = cv2.resize(img2, (sw, sh), interpolation=cv2.INTER_AREA)

        mask = detect_fn(s1, s2)

        if scale != 1.0:
            mask = cv2.resize(mask, (w, h), interpolation=cv2.INTER_NEAREST)

        combined = np.maximum(combined, mask)

    return combined


def build_confidence_map(channels, weights=None):
    """
    Build a [0-1] confidence map from multiple normalized signal channels.
    Each channel should be a float32 array in [0,1].
    If weights is None, uses equal weighting.
    """
    if not channels:
        return None
    if weights is None:
        weights = [1.0 / len(channels)] * len(channels)
    total_w = sum(weights)
    weights = [w / total_w for w in weights]

    shape = channels[0].shape
    conf = np.zeros(shape, dtype=np.float64)
    for ch, w in zip(channels, weights):
        if ch.shape != shape:
            ch = cv2.resize(ch.astype(np.float32), (shape[1], shape[0]))
        conf += w * ch.astype(np.float64)

    return np.clip(conf, 0, 1).astype(np.float32)
