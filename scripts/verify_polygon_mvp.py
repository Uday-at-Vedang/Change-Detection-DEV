"""Verify the polygon MVP (serialization, WGS84 projection, GeoJSON export).

CPU-only, no server and no GPU needed. Covers today's three CPU-track tasks
plus the backward-compatibility guarantee that regions without a polygon
behave exactly as they did before.

Run:  venv\\Scripts\\python scripts\\verify_polygon_mvp.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.dda.detect_service import _serialize_polygon, _serialize_regions
from app.dda.geo_regions import enrich_regions_geo, polygon_to_lat_lng
from app.dda.reports_routes import _region_geometry

_fails = []


def check(name, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        _fails.append(name)


def engine_region(**over):
    """A region dict shaped like the engine emits (tuples, snake_case)."""
    base = {
        "id": 1, "area": 400, "center": (50, 50), "bbox": (0, 0, 100, 100),
        "object_type": "New Construction/Building", "confidence": 0.92,
        "sub_type": None, "sub_type_confidence": None, "estimated_stories": None,
        "estimated_height_m": None, "construction_stage": None,
    }
    base.update(over)
    return base


SQUARE = [[0, 0], [100, 0], [100, 100], [0, 100]]
BOUNDS = (77.0, 28.0, 77.1, 28.1)  # west, south, east, north

print("=" * 64)
print("TASK 1a - Polygon serialization")
print("=" * 64)

s = _serialize_regions([engine_region(polygon=SQUARE)])[0]
check("polygon passes through serialization", s.get("polygon") == SQUARE, str(s.get("polygon")))

plain = _serialize_regions([engine_region()])[0]
check("no polygon -> key absent (backward compatible)", "polygon" not in plain)

big = _serialize_regions([engine_region(polygon=[[i, i * 2] for i in range(500)])])[0]
check("500 vertices capped to 100", len(big["polygon"]) == 100, f"{len(big['polygon'])} vertices")

check("degenerate ring (<3 points) rejected", _serialize_polygon([[1, 2], [3, 4]]) is None)
check("non-numeric coords rejected", _serialize_polygon([["a", "b"], [1, 2], [3, 4]]) is None)
check("empty/None rejected", _serialize_polygon(None) is None)

print("=" * 64)
print("TASK 1b - WGS84 projection (polygonGeo)")
print("=" * 64)

ring = polygon_to_lat_lng(SQUARE, 100, 100, BOUNDS)
nw, se = ring[0], ring[2]
check("NW corner projects correctly",
      abs(nw["lng"] - 77.0) < 1e-6 and abs(nw["lat"] - 28.1) < 1e-6, str(nw))
check("SE corner projects correctly",
      abs(se["lng"] - 77.1) < 1e-6 and abs(se["lat"] - 28.0) < 1e-6, str(se))

enr = enrich_regions_geo(_serialize_regions([engine_region(polygon=SQUARE)]),
                         img_width=100, img_height=100, bounds=BOUNDS)[0]
check("polygonGeo attached alongside latLng",
      "polygonGeo" in enr and "latLng" in enr,
      f"{len(enr.get('polygonGeo', []))} vertices")
check("polygon agrees with region (same transform as bbox centre)",
      enr["latLng"] == {"lat": 28.05, "lng": 77.05}, str(enr["latLng"]))

enr_plain = enrich_regions_geo(_serialize_regions([engine_region()]),
                               img_width=100, img_height=100, bounds=BOUNDS)[0]
check("no polygon -> no polygonGeo (backward compatible)", "polygonGeo" not in enr_plain)

enr_nogeo = enrich_regions_geo(_serialize_regions([engine_region(polygon=SQUARE)]),
                               img_width=100, img_height=100, bounds=None)[0]
check("ungeoreferenced run handled without crashing", "polygonGeo" not in enr_nogeo)

print("=" * 64)
print("TASK 3 - GeoJSON export")
print("=" * 64)

geom = _region_geometry(enr)
closed = bool(geom and geom["coordinates"][0][0] == geom["coordinates"][0][-1])
check("polygon region -> GeoJSON Polygon", geom and geom["type"] == "Polygon")
check("ring is closed (GeoJSON requirement)", closed)
check("coordinates are [lng, lat] order",
      geom and abs(geom["coordinates"][0][0][0] - 77.0) < 1e-6,
      str(geom["coordinates"][0][0]) if geom else "")

pt = _region_geometry({"latLng": {"lat": 28.05, "lng": 77.05}})
check("no polygon -> Point fallback (old runs still export)",
      pt and pt["type"] == "Point", str(pt))
check("no geo at all -> skipped, not a broken feature",
      _region_geometry({}) is None)

print("=" * 64)
print("END-TO-END - engine dict -> serialize -> enrich -> GeoJSON")
print("=" * 64)

chain = _region_geometry(
    enrich_regions_geo(_serialize_regions([engine_region(polygon=SQUARE)]),
                       img_width=100, img_height=100, bounds=BOUNDS)[0])
check("full chain produces a valid Polygon feature",
      chain and chain["type"] == "Polygon" and len(chain["coordinates"][0]) == 5)

chain_plain = _region_geometry(
    enrich_regions_geo(_serialize_regions([engine_region()]),
                       img_width=100, img_height=100, bounds=BOUNDS)[0])
check("full chain without polygon degrades to Point (no regression)",
      chain_plain and chain_plain["type"] == "Point")

print("=" * 64)
if _fails:
    print(f"RESULT: {len(_fails)} CHECK(S) FAILED -> {', '.join(_fails)}")
    sys.exit(1)
print("RESULT: ALL CHECKS PASSED")
