"""Pair library images that cover the same place.

Two images are compared on every piece of metadata the library actually holds:
administrative location (zone / village), folder path, capture date, filename,
and geographic overlap. Each signal contributes to a score, and a few of them
act as hard gates that rule a pairing out entirely.

Automatic pairing then picks the best-scoring pairs greedily, so a pair always
holds exactly two images and no image is used by more than one pair. The full
same-place cluster is still reported as ``candidates`` so an operator can swap
in a different image by hand.
"""
from __future__ import annotations

import logging
import math
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, NamedTuple, Optional

from ..geo_regions import parse_bounds

logger = logging.getLogger(__name__)

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

# Candidate generation is blocked so a large library stays near-linear: only
# images that already share a filename key, a village, or a map cell are ever
# compared. These caps bound the work inside one oversized block.
_SPATIAL_CELL = 0.02          # ~2 km at Delhi's latitude
_SPATIAL_SUBCELL = 0.002      # ~200 m, used to split a crowded cell
_BLOCK_FULL_LIMIT = 64        # compare every combination up to this block size
_BLOCK_WINDOW = 12            # past it, only compare near-in-time neighbours
_MAX_CANDIDATES = 500_000

# A pair needs both a "same place" signal (see _score_pair) and enough total
# agreement to be worth proposing.
_MIN_PAIR_SCORE = 24


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


def overlap_stats(a: Any, b: Any) -> Optional[dict]:
    """IoU plus how much of the smaller box is covered, or None if disjoint.

    ``small`` and ``ratio`` matter because a wide orthomosaic legitimately
    contains a smaller tile of the same place: IoU alone would call that a
    mismatch.
    """
    ba, bb = _bbox(a), _bbox(b)
    if not ba or not bb:
        return None
    inter = _intersection(ba, bb)
    if not inter:
        return None
    aa, ab, ai = _area(ba), _area(bb), _area(inter)
    if min(aa, ab) <= 0:
        return None
    return {
        "iou": ai / max(aa + ab - ai, 1e-12),
        "small": ai / min(aa, ab),
        "ratio": min(aa, ab) / max(aa, ab),
    }


def bounds_same_area(a: Any, b: Any) -> bool:
    """True when two WGS84 boxes cover essentially the same place (not a tiny crop in a huge ORI)."""
    stats = overlap_stats(a, b)
    if not stats:
        return False
    return stats["iou"] >= 0.25 or (stats["small"] >= 0.55 and stats["ratio"] >= 0.15)


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
    """Oldest first. Within one day the before/after in the name decides,
    because a same-day upload order says nothing about which came first."""
    return sorted(
        images,
        key=lambda img: (
            sort_datetime(img).date(),
            _name_rank(img.get("filename") or ""),
            sort_datetime(img),
            (img.get("filename") or "").lower(),
            img.get("id") or 0,
        ),
    )


_HARD_KEY_PREFIXES = ("seq:", "grid:", "sheet:")


def _hard_keys(keys: set[str]) -> set[str]:
    return {k for k in keys if k.startswith(_HARD_KEY_PREFIXES)}


def distinct_places(keys_a: set[str], keys_b: set[str]) -> bool:
    """True when both images are already identified as different sites.

    They may still be the same place under two naming schemes (a grid number
    and a survey sheet id), so this only raises the bar: such a pairing needs
    strong geographic proof rather than being ruled out outright.
    """
    ha, hb = _hard_keys(keys_a), _hard_keys(keys_b)
    return bool(ha and hb and ha.isdisjoint(hb))


def conflicting_keys(keys_a: set[str], keys_b: set[str]) -> bool:
    """True when both names use the same vocabulary but disagree within it.

    ``before3`` vs ``before7`` are two different before/after sets, and
    ``grid 54`` vs ``grid 60`` are two different grids — no amount of map
    overlap makes those one pair. A grid number against a sheet id is not a
    conflict, because neither vocabulary can contradict the other.
    """
    for prefix in _HARD_KEY_PREFIXES:
        a = {k for k in keys_a if k.startswith(prefix)}
        b = {k for k in keys_b if k.startswith(prefix)}
        if a and b and a.isdisjoint(b):
            return True
    return False


def _key_text(key: str) -> str:
    kind, _, value = key.partition(":")
    if kind == "grid":
        return f"grid {value}"
    if kind == "sheet":
        return f"sheet {value.upper()}"
    if kind == "seq":
        return f"before/after set {value}"
    return "filename"


def _gap_text(days: int) -> str:
    if days < 45:
        return f"{days} day{'' if days == 1 else 's'} apart"
    if days < 730:
        months = max(1, round(days / 30.4))
        return f"{months} month{'' if months == 1 else 's'} apart"
    years = round(days / 365.25, 1)
    return f"{years:g} years apart"


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


def _spatial_cells(b: tuple[float, float, float, float], cell: float = _SPATIAL_CELL):
    """Every grid cell the box touches, so any two overlapping boxes meet in one."""
    west, south, east, north = b
    x0 = math.floor(west / cell)
    x1 = math.floor((east - 1e-12) / cell)
    y0 = math.floor(south / cell)
    y1 = math.floor((north - 1e-12) / cell)
    # A city-wide orthomosaic can span thousands of cells; indexing it in all
    # of them would cost far more than the recall it buys.
    if (x1 - x0 + 1) * (y1 - y0 + 1) > 256:
        yield (x0, y0)
        yield (x1, y1)
        return
    for x in range(x0, x1 + 1):
        for y in range(y0, y1 + 1):
            yield (x, y)


def _clip_to_cell(b: tuple[float, float, float, float], cell: tuple[int, int], size: float):
    """Box trimmed to one coarse cell, so a re-hash inside it stays bounded."""
    west = max(b[0], cell[0] * size)
    south = max(b[1], cell[1] * size)
    east = min(b[2], (cell[0] + 1) * size)
    north = min(b[3], (cell[1] + 1) * size)
    if east <= west or north <= south:
        return None
    return (west, south, east, north)


def _public_image(img: dict, item: Optional["_Item"] = None) -> dict:
    """Drop heavy fields after pairing so the HTTP payload stays small."""
    out = dict(img)
    out.pop("bounds", None)
    if item is not None:
        out["zoneId"] = item.zone
        out["villageId"] = item.village
        out["zoneName"] = item.zone_name
        out["villageName"] = item.village_name
    return out


class ResolvedLocation(NamedTuple):
    zone_id: Optional[int]
    village_id: Optional[int]
    zone_name: str
    village_name: str
    # True only when a person chose this on upload. A location read off the
    # folder tree is a hint, so it may suggest a pairing but never veto one.
    explicit: bool


# Folder names that stand for "no location yet" — matching them against the
# masters would invent a location the image does not really have.
_PLACEHOLDER_LOCATIONS = frozenset({
    "unassigned", "_unassigned", "unknown", "uncategorized",
    "legacy", "misc", "other", "root", "images", "temp", "tmp",
})


class LocationIndex:
    """Resolves an image's zone / village, falling back to its folder names.

    Uploads carry an explicit choice, but the folder tree has always spelled
    the same thing out ("Central Delhi/Daryaganj"), so images indexed from
    disk still get a location to compare on.
    """

    def __init__(self, zones=(), villages=()):
        self.zone_by_name: dict[str, int] = {}
        self.zone_names: dict[int, str] = {}
        for zone_id, name in zones:
            clean = (name or "").strip()
            if clean:
                self.zone_by_name[clean.lower()] = zone_id
                self.zone_names[zone_id] = clean
        # One village name can exist in several zones, so keep every option.
        self.villages_by_name: dict[str, list] = defaultdict(list)
        self.village_names: dict[int, str] = {}
        for village_id, name, zone_id in villages:
            clean = (name or "").strip()
            if clean:
                self.villages_by_name[clean.lower()].append((zone_id, village_id))
                self.village_names[village_id] = clean

    def __bool__(self) -> bool:
        return bool(self.zone_by_name or self.villages_by_name)

    def resolve(self, img: dict) -> ResolvedLocation:
        zone_id = img.get("zoneId") or None
        village_id = img.get("villageId") or None
        explicit = bool(zone_id or village_id)
        if not (zone_id and village_id) and self:
            segments = [
                s.strip().lower()
                for s in str(img.get("nodePath") or "").replace("\\", "/").split("/")
                if s.strip() and s.strip().lower() not in _PLACEHOLDER_LOCATIONS
            ]
            if not zone_id:
                for segment in segments:
                    if segment in self.zone_by_name:
                        zone_id = self.zone_by_name[segment]
                        break
            if not village_id:
                for segment in segments:
                    options = self.villages_by_name.get(segment) or []
                    if zone_id:
                        options = [o for o in options if o[0] == zone_id] or options
                    # An ambiguous name is left unresolved rather than guessed.
                    if len(options) == 1:
                        zone_id = zone_id or options[0][0]
                        village_id = options[0][1]
                        break
        return ResolvedLocation(
            zone_id,
            village_id,
            img.get("zoneName") or self.zone_names.get(zone_id, ""),
            img.get("villageName") or self.village_names.get(village_id, ""),
            explicit,
        )


def build_location_index(db) -> LocationIndex:
    from ..models import DdaVillage, DdaZone

    return LocationIndex(
        db.query(DdaZone.id, DdaZone.name).all(),
        db.query(DdaVillage.id, DdaVillage.name, DdaVillage.zone_id).all(),
    )


class _Item:
    """Everything the scorer needs about one image, computed once."""

    __slots__ = (
        "idx", "img", "path", "filename", "keys", "hard", "bbox", "when",
        "zone", "village", "zone_name", "village_name", "pinned_location",
        "node_id", "folder", "rank",
    )

    def __init__(self, idx: int, img: dict, locations: Optional[LocationIndex] = None):
        self.idx = idx
        self.img = img
        self.path = (img.get("path") or "").replace("\\", "/")
        self.filename = img.get("filename") or img.get("imageName") or self.path
        self.keys = distinctive_keys(self.filename or self.path)
        self.hard = _hard_keys(self.keys)
        self.bbox = _bbox(img.get("bounds"))
        self.when = infer_capture_datetime(img)
        place = (locations or LocationIndex()).resolve(img)
        self.zone = place.zone_id
        self.village = place.village_id
        self.zone_name = place.zone_name
        self.village_name = place.village_name
        self.pinned_location = place.explicit
        self.node_id = img.get("nodeId") or None
        folder = (img.get("nodePath") or "").strip("/")
        if not folder:
            folder = self.path.rsplit("/", 1)[0] if "/" in self.path else ""
        self.folder = folder.lower()
        self.rank = _name_rank(self.filename)


def _folder_score(a: _Item, b: _Item) -> tuple[float, str]:
    if a.node_id and b.node_id and a.node_id == b.node_id:
        return 10.0, "same folder"
    if a.folder and a.folder == b.folder:
        return 10.0, "same folder"
    if not a.folder or not b.folder:
        return 0.0, ""
    seg_a, seg_b = a.folder.split("/"), b.folder.split("/")
    shared = 0
    for x, y in zip(seg_a, seg_b):
        if x != y:
            break
        shared += 1
    if shared:
        return min(6.0, 2.0 * shared), "same folder branch"
    return 0.0, ""


def _date_score(a: _Item, b: _Item) -> tuple[float, str]:
    if not a.when or not b.when:
        return 0.0, ""
    gap = abs((a.when - b.when).days)
    if gap == 0:
        # Change detection needs two moments in time; a same-day pairing only
        # survives when the filenames explicitly say before/after.
        return -4.0, "same capture date"
    if gap >= 180:
        return 14.0, _gap_text(gap)
    if gap >= 30:
        return 12.0, _gap_text(gap)
    return 8.0, _gap_text(gap)


def _score_pair(a: _Item, b: _Item) -> Optional[dict]:
    """Score one candidate pairing, or None when the metadata rules it out."""
    if a.path and a.path == b.path:
        return None
    if conflicting_keys(a.hard, b.hard):
        return None
    # Only a location someone actually picked can veto a pairing; one read off
    # the folder tree just describes how the files happen to be filed.
    if a.pinned_location and b.pinned_location:
        if a.zone and b.zone and a.zone != b.zone:
            return None
        if a.village and b.village and a.village != b.village:
            return None

    stats = overlap_stats(a.bbox, b.bbox) if (a.bbox and b.bbox) else None
    if a.bbox and b.bbox and stats is None:
        # Both are georeferenced and their footprints do not touch.
        return None
    strong_geo = bool(stats) and (stats["small"] >= 0.85 and stats["ratio"] >= 0.30)
    if distinct_places(a.hard, b.hard) and not strong_geo:
        return None

    score = 0.0
    reasons: list[str] = []
    match = "filename"

    geo_ok = False
    if stats:
        geo_ok = stats["iou"] >= 0.25 or (stats["small"] >= 0.55 and stats["ratio"] >= 0.15)
        score += 40.0 * min(1.0, stats["iou"])
        if stats["small"] >= 0.8:
            score += 10.0
        if geo_ok:
            match = "bounds"
            reasons.append(f"{round(stats['iou'] * 100)}% map overlap")

    shared_hard = a.hard & b.hard
    shared_soft = (a.keys & b.keys) - shared_hard
    if shared_hard:
        score += 26.0
        reasons.append(f"matching {_key_text(sorted(shared_hard)[0])}")
    elif shared_soft:
        score += 16.0
        reasons.append("matching filename")

    same_village = bool(a.village) and a.village == b.village
    if same_village:
        score += 20.0
        reasons.append(f"same location ({a.village_name})" if a.village_name else "same location")
        if match == "filename" and not shared_hard and not shared_soft:
            match = "location"
    elif a.zone and a.zone == b.zone:
        score += 12.0
        reasons.append(f"same zone ({a.zone_name})" if a.zone_name else "same zone")

    folder_points, folder_text = _folder_score(a, b)
    score += folder_points
    if folder_text:
        reasons.append(folder_text)

    date_points, date_text = _date_score(a, b)
    score += date_points
    if date_text:
        reasons.append(date_text)

    if {a.rank, b.rank} == {0, 2}:
        score += 12.0
        reasons.append("named before / after")

    # Folder, zone and capture date only say "could be related". When both
    # footprints are known the map is authoritative — a shared name key can
    # still vouch for a partial overlap, but a folder or village cannot.
    if a.bbox and b.bbox:
        same_place = geo_ok or bool(shared_hard)
    else:
        same_place = geo_ok or bool(shared_hard) or bool(shared_soft) or same_village
    if not same_place:
        return None
    if score < _MIN_PAIR_SCORE:
        return None
    return {"score": score, "reasons": reasons, "match": match}


def _emit_block(members: list[int], items: list[_Item], add) -> None:
    members = list(dict.fromkeys(members))
    if len(members) < 2:
        return
    if len(members) <= _BLOCK_FULL_LIMIT:
        for a in range(len(members)):
            for b in range(a + 1, len(members)):
                add(members[a], members[b])
        return
    # Too crowded to compare exhaustively: order by capture time and only look
    # at each image's nearest neighbours, since a pair needs two dates anyway.
    members.sort(key=lambda i: (items[i].when or datetime.min, items[i].path))
    for a in range(len(members)):
        for b in range(a + 1, min(len(members), a + _BLOCK_WINDOW)):
            add(members[a], members[b])


def _candidate_pairs(items: list[_Item]) -> set[tuple[int, int]]:
    """Index pairings worth scoring: shared filename key, map cell, or village.

    Folder and zone are deliberately not blocking keys. Neither can establish
    on its own that two images show the same place, so blocking on them would
    only add candidates the scorer then throws away.
    """
    pairs: set[tuple[int, int]] = set()
    truncated = False

    def add(i: int, j: int) -> None:
        nonlocal truncated
        if len(pairs) >= _MAX_CANDIDATES:
            truncated = True
            return
        pairs.add((i, j) if i < j else (j, i))

    by_key: dict[str, list[int]] = defaultdict(list)
    for item in items:
        for key in item.keys:
            by_key[key].append(item.idx)
    for members in by_key.values():
        _emit_block(members, items, add)

    by_cell: dict[tuple[int, int], list[int]] = defaultdict(list)
    for item in items:
        if item.bbox:
            for cell in _spatial_cells(item.bbox):
                by_cell[cell].append(item.idx)
    for cell, members in by_cell.items():
        if len(members) <= _BLOCK_FULL_LIMIT:
            _emit_block(members, items, add)
            continue
        # Dense city imagery keeps its geographic precision by re-hashing the
        # crowded cell at a finer resolution instead of giving up on bounds.
        sub: dict[tuple[int, int], list[int]] = defaultdict(list)
        for idx in members:
            clipped = _clip_to_cell(items[idx].bbox, cell, _SPATIAL_CELL)
            if not clipped:
                continue
            for fine in _spatial_cells(clipped, _SPATIAL_SUBCELL):
                sub[fine].append(idx)
        for group in sub.values():
            _emit_block(group, items, add)

    # Village is the only bridge for an image with no map extent; images that
    # do have one are already indexed by cell, so skip those combinations.
    by_village: dict[Any, list[int]] = defaultdict(list)
    for item in items:
        if item.village:
            by_village[item.village].append(item.idx)
    for members in by_village.values():
        floating = [i for i in members if not items[i].bbox]
        if not floating:
            continue
        _emit_block(floating, items, add)
        located = [i for i in members if items[i].bbox]
        if len(located) > _BLOCK_WINDOW:
            located.sort(key=lambda i: (items[i].when or datetime.min, items[i].path))
            located = located[:_BLOCK_WINDOW]
        for i in floating:
            for j in located:
                add(i, j)

    if truncated:
        logger.warning(
            "Same-area pairing hit the %d candidate cap; some weak pairings were skipped.",
            _MAX_CANDIDATES,
        )
    return pairs


def build_area_groups(
    images: list[dict],
    locations: Optional[LocationIndex] = None,
) -> dict:
    """Pair images that cover the same place: two per pair, never reused."""
    items = [_Item(i, img, locations) for i, img in enumerate(images or [])]
    n = len(items)

    candidates = _candidate_pairs(items)
    scored = []
    for i, j in candidates:
        result = _score_pair(items[i], items[j])
        if result:
            scored.append((result["score"], i, j, result))

    # Every image that shares a viable pairing with another belongs to the same
    # cluster; that is what the UI offers as manual alternatives.
    cluster_of: dict[int, int] = {}
    clusters: dict[int, list[int]] = {}
    for _, i, j, _r in scored:
        ci, cj = cluster_of.get(i), cluster_of.get(j)
        if ci is None and cj is None:
            root = min(i, j)
            clusters[root] = [i, j]
            cluster_of[i] = cluster_of[j] = root
        elif ci is None:
            clusters[cj].append(i)
            cluster_of[i] = cj
        elif cj is None:
            clusters[ci].append(j)
            cluster_of[j] = ci
        elif ci != cj:
            clusters[ci].extend(clusters.pop(cj))
            for idx in clusters[ci]:
                cluster_of[idx] = ci

    # Greedy best-first matching: strongest evidence wins, and an image that is
    # already in a pair is skipped, so no pair repeats an image.
    scored.sort(key=lambda row: (-row[0], items[row[1]].path, items[row[2]].path))
    item_of = {id(it.img): it for it in items}

    def public(img: dict) -> dict:
        return _public_image(img, item_of.get(id(img)))

    used: set[int] = set()
    groups = []
    for score, i, j, result in scored:
        if i in used or j in used or i == j:
            continue
        if items[i].path and items[i].path == items[j].path:
            continue
        used.add(i)
        used.add(j)
        before, after = _sort_images([items[i].img, items[j].img])
        shared = items[i].keys & items[j].keys
        cluster = clusters.get(cluster_of.get(i, i), [i, j])
        candidate_imgs = _sort_images([items[k].img for k in dict.fromkeys(cluster)])
        groups.append({
            "id": f"area-{before.get('id')}-{after.get('id')}",
            "label": _group_label([before, after], shared),
            "match": result["match"],
            "reasons": result["reasons"],
            "score": round(score, 1),
            "images": [public(before), public(after)],
            "candidates": [public(img) for img in candidate_imgs],
            "suggestedBefore": public(before),
            "suggestedAfter": public(after),
            "beforePath": before.get("path"),
            "afterPath": after.get("path"),
            "beforeDate": sort_datetime(before).date().isoformat() if sort_datetime(before) != datetime.min else None,
            "afterDate": sort_datetime(after).date().isoformat() if sort_datetime(after) != datetime.min else None,
        })

    groups.sort(key=lambda g: (g["label"].lower(), g["id"]))
    unpaired = [
        public(img)
        for img in _sort_images([it.img for it in items if it.idx not in used])
    ]
    return {
        "groups": groups,
        "unpaired": unpaired,
        "totalImages": n,
        "pairedImages": len(used),
    }


def load_cached_area_groups(db) -> dict:
    """Build same-area groups once per library fingerprint, then reuse in-process."""
    from .image_service import library_fingerprint, list_all_images
    from .library_cache import get_area_groups_cache, set_area_groups_cache

    fp = library_fingerprint(db)
    cached = get_area_groups_cache(fp)
    if cached is not None:
        return cached
    images = list_all_images(db, include_bounds=True, slim=True)
    payload = build_area_groups(images, build_location_index(db))
    set_area_groups_cache(fp, payload)
    return payload


def filter_area_groups_for_node(payload: dict, node) -> dict:
    prefix = (getattr(node, "node_path", None) or "").rstrip("/")
    node_id = getattr(node, "id", None)

    def _in_node(img: dict) -> bool:
        path = (img.get("nodePath") or "").rstrip("/")
        return path == prefix or path.startswith(prefix + "/") or img.get("nodeId") == node_id

    groups = [
        g for g in (payload.get("groups") or [])
        if any(_in_node(img) for img in g.get("images") or [])
    ]
    unpaired = [img for img in (payload.get("unpaired") or []) if _in_node(img)]
    return {
        "groups": groups,
        "unpaired": unpaired,
        "totalImages": payload.get("totalImages", 0),
        "pairedImages": sum(len(g.get("images") or []) for g in groups),
    }
