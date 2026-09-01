"""Group library images that cover the same place (oldest → Before, newest → After)."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from ..geo_regions import parse_bounds

YEAR_RE = re.compile(r"^(19|20)\d{2}$")
GRID_RE = re.compile(r"grid\s*(?:no\.?\s*)?(\d+)", re.I)
SHEET_RE = re.compile(r"\b([a-z]\d{2}[a-z]\d[a-z]\d+)\b", re.I)
SEQ_RE = re.compile(r"\b(?:before|after)[\s_-]*(\d{1,3})\b", re.I)
TEST_RE = re.compile(r"\btest[\s_-]*(\d+)\b", re.I)
ISO_DATE_RE = re.compile(r"((?:19|20)\d{2})[-_/](\d{2})[-_/](\d{2})")
YEAR_IN_TEXT_RE = re.compile(r"\b((?:19|20)\d{2})\b")
NOISE_RE = re.compile(
    r"\b(ori|orthomosaic|ortho|after|before|t1|t2|old|new)\b",
    re.I,
)


def _bbox(bounds: Any) -> Optional[tuple[float, float, float, float]]:
    parsed = parse_bounds(bounds)
    if not parsed:
        return None
    west, south, east, north = parsed
    if east <= west or north <= south:
        return None
    return (west, south, east, north)


def _area(b: tuple[float, float, float, float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def _intersection(a: tuple[float, float, float, float], b: tuple[float, float, float, float]):
    west = max(a[0], b[0])
    south = max(a[1], b[1])
    east = min(a[2], b[2])
    north = min(a[3], b[3])
    if east <= west or north <= south:
        return None
    return (west, south, east, north)


def bounds_same_area(a: Any, b: Any) -> bool:
    """True when two WGS84 boxes cover essentially the same place (not a tiny crop in a huge ORI)."""
    ba, bb = _bbox(a), _bbox(b)
    if not ba or not bb:
        return False
    inter = _intersection(ba, bb)
    if not inter:
        return False
    aa, ab, ai = _area(ba), _area(bb), _area(inter)
    if min(aa, ab) <= 0:
        return False
    iou = ai / max(aa + ab - ai, 1e-12)
    overlap_small = ai / min(aa, ab)
    ratio = min(aa, ab) / max(aa, ab)
    return iou >= 0.25 or (overlap_small >= 0.55 and ratio >= 0.15)


def distinctive_keys(filename: str) -> set[str]:
    stem = Path(filename or "").stem.lower()
    name = stem.replace("_", " ")
    keys: set[str] = set()
    m = GRID_RE.search(name)
    if m:
        keys.add(f"grid:{m.group(1)}")
    m = SHEET_RE.search(stem.replace("_", " ").replace("-", " "))
    if m:
        keys.add(f"sheet:{m.group(1).lower()}")
    m = SEQ_RE.search(stem.replace("_", " ").replace("-", " "))
    if m:
        keys.add(f"seq:{m.group(1)}")
    if TEST_RE.search(name):
        keys.add("stem:test")
    cleaned = ISO_DATE_RE.sub(" ", stem)
    cleaned = cleaned.replace("_", " ").replace("-", " ")
    cleaned = NOISE_RE.sub(" ", cleaned)
    cleaned = YEAR_IN_TEXT_RE.sub(" ", cleaned)
    cleaned = re.sub(r"[^a-z0-9]+", "", cleaned)
    if len(cleaned) >= 6:
        keys.add(f"stem:{cleaned}")
    return keys


def _year_from_path(img: dict) -> Optional[int]:
    for part in re.split(r"[/\\]", str(img.get("nodePath") or img.get("path") or "")):
        if YEAR_RE.match(part.strip()):
            return int(part.strip())
    text = f"{img.get('filename') or ''} {img.get('path') or ''}"
    m = YEAR_IN_TEXT_RE.search(text)
    return int(m.group(1)) if m else None


def parse_datetime(raw) -> Optional[datetime]:
    """Parse capture/upload timestamps from ORM, MySQL, or ISO strings."""
    if raw is None:
        return None
    if isinstance(raw, datetime):
        return raw.replace(tzinfo=None) if raw.tzinfo else raw
    text = str(raw).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    # MySQL DATETIME string is "YYYY-MM-DD HH:MM:SS" (no T).
    if "T" not in text and re.match(r"^\d{4}-\d{2}-\d{2} ", text):
        text = text.replace(" ", "T", 1)
    # TIFF DateTime tags are "YYYY:MM:DD HH:MM:SS".
    if re.match(r"^\d{4}:\d{2}:\d{2}", text):
        text = text.replace(":", "-", 2).replace(" ", "T", 1)
    try:
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=None) if dt.tzinfo else dt
    except ValueError:
        return None


def infer_capture_datetime(img: dict) -> Optional[datetime]:
    """Best capture time from DB value, then filename / folder year."""
    parsed = parse_datetime(img.get("captureDate"))
    if parsed:
        return parsed
    text = f"{img.get('filename') or ''} {img.get('path') or ''}"
    m = ISO_DATE_RE.search(text)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            pass
    year = _year_from_path(img)
    if year:
        return datetime(year, 1, 1)
    return parse_datetime(img.get("uploadedOn"))


def sort_datetime(img: dict) -> datetime:
    return infer_capture_datetime(img) or datetime.min


def _name_rank(filename: str) -> int:
    n = (filename or "").lower()
    if "before" in n or re.search(r"(^|[_\-\s])t1([_\-\s]|$)", n) or re.search(r"(^|[_\-\s])old([_\-\s]|$)", n):
        return 0
    if "after" in n or re.search(r"(^|[_\-\s])t2([_\-\s]|$)", n) or re.search(r"(^|[_\-\s])new([_\-\s]|$)", n):
        return 2
    return 1


def _sort_images(images: list[dict]) -> list[dict]:
    return sorted(
        images,
        key=lambda img: (
            sort_datetime(img),
            _name_rank(img.get("filename") or ""),
            (img.get("filename") or "").lower(),
            img.get("id") or 0,
        ),
    )


class _UnionFind:
    def __init__(self, n: int):
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


_HARD_KEY_PREFIXES = ("seq:", "grid:", "sheet:")


def _hard_keys(keys: set[str]) -> set[str]:
    return {k for k in keys if k.startswith(_HARD_KEY_PREFIXES)}


def distinct_places(keys_a: set[str], keys_b: set[str]) -> bool:
    """True when both images are already identified as different sites.

    Bounds overlap is then ignored so a large ORI cannot swallow neighbouring
    pairs (union-find is transitive). Images with no hard key can still join
    via overlap or a shared folder.
    """
    ha, hb = _hard_keys(keys_a), _hard_keys(keys_b)
    return bool(ha and hb and ha.isdisjoint(hb))


def _group_label(images: list[dict], keys: set[str]) -> str:
    base = ""
    for key in sorted(keys):
        if key.startswith("grid:"):
            base = f"Grid {key.split(':', 1)[1]}"
            break
        if key.startswith("sheet:"):
            base = key.split(":", 1)[1].upper()
            break
        if key.startswith("seq:"):
            base = f"Pair {key.split(':', 1)[1]}"
            break
        if key == "stem:test":
            base = "TEST pair"
            break
    if not base:
        paths = [img.get("nodePath") or "" for img in images]
        if paths and all(paths):
            common = paths[0]
            for p in paths[1:]:
                while common and not p.startswith(common):
                    common = common.rsplit("/", 1)[0] if "/" in common else ""
            base = common
    if not base:
        base = images[0].get("filename") or "Same area"
    names = [(img.get("filename") or img.get("path") or "").strip() for img in images]
    names = [n for n in names if n]
    if len(images) == 2 and names:
        detail = f"{names[0]} -> {names[-1]}"
        if base in names or base == (images[0].get("nodePath") or ""):
            return detail
        return f"{base}: {detail}"
    if len(images) > 2:
        return f"{base} · {len(images)} images"
    return base


def _match_reason(images: list[dict]) -> str:
    boxes = [_bbox(img.get("bounds")) for img in images]
    if sum(1 for b in boxes if b) >= 2:
        return "bounds"
    return "filename"


def build_area_groups(images: list[dict]) -> dict:
    """Cluster images that cover the same area; suggest oldest/newest as Before/After."""
    items = list(images or [])
    n = len(items)
    uf = _UnionFind(n)
    keys = [distinctive_keys(img.get("filename") or img.get("path") or "") for img in items]

    for i in range(n):
        for j in range(i + 1, n):
            if keys[i] and keys[j] and (keys[i] & keys[j]):
                uf.union(i, j)
                continue
            if distinct_places(keys[i], keys[j]):
                continue
            if bounds_same_area(items[i].get("bounds"), items[j].get("bounds")):
                uf.union(i, j)

    buckets: dict[int, list[int]] = {}
    for i in range(n):
        buckets.setdefault(uf.find(i), []).append(i)

    groups = []
    unpaired = []
    for idxs in buckets.values():
        members = _sort_images([items[i] for i in idxs])
        if len(members) < 2:
            unpaired.extend(members)
            continue
        shared: set[str] = set()
        for i in idxs:
            if not shared:
                shared = set(keys[i])
            else:
                shared &= keys[i]
        before, after = members[0], members[-1]
        groups.append({
            "id": f"area-{before.get('id')}-{after.get('id')}",
            "label": _group_label(members, shared),
            "match": _match_reason(members),
            "images": members,
            "suggestedBefore": before,
            "suggestedAfter": after,
            "beforePath": before.get("path"),
            "afterPath": after.get("path"),
            "beforeDate": sort_datetime(before).date().isoformat() if sort_datetime(before) != datetime.min else None,
            "afterDate": sort_datetime(after).date().isoformat() if sort_datetime(after) != datetime.min else None,
        })

    groups.sort(key=lambda g: (g["label"].lower(), g["id"]))
    unpaired = _sort_images(unpaired)
    return {
        "groups": groups,
        "unpaired": unpaired,
        "totalImages": n,
        "pairedImages": n - len(unpaired),
    }
