"""Same-area pairing: hard keys must not be merged by overlapping bounds."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dda.tree.area_groups import (
    build_area_groups,
    distinct_places,
    parse_datetime,
)


def test_mysql_datetime_and_tiff_strings_parse():
    assert parse_datetime("2025-03-01 14:30:00") == datetime(2025, 3, 1, 14, 30, 0)
    assert parse_datetime("2025:02:26 00:00:00") == datetime(2025, 2, 26, 0, 0, 0)
    assert parse_datetime("2025-02-26T00:00:00") == datetime(2025, 2, 26)


def test_seq_pairs_stay_separate_despite_overlap():
    bounds = {"west": 77.0, "south": 28.0, "east": 77.2, "north": 28.2}
    images = [
        {"id": 1, "filename": "before3.tif", "path": "a/before3.tif", "captureDate": "2025-07-24", "bounds": bounds},
        {"id": 2, "filename": "after3.tif", "path": "a/after3.tif", "captureDate": "2026-07-31", "bounds": bounds},
        {"id": 3, "filename": "before7.tif", "path": "a/before7.tif", "captureDate": "2026-07-31", "bounds": bounds},
        {"id": 4, "filename": "after7.tif", "path": "a/after7.tif", "captureDate": "2026-07-31", "bounds": bounds},
    ]
    payload = build_area_groups(images)
    labels = sorted(g["label"] for g in payload["groups"])
    assert any("Pair 3" in lab for lab in labels)
    assert any("Pair 7" in lab for lab in labels)
    assert payload["unpaired"] == []
    g3 = next(g for g in payload["groups"] if "Pair 3" in g["label"])
    names = {img["filename"] for img in g3["images"]}
    assert names == {"before3.tif", "after3.tif"}
    assert g3["suggestedBefore"]["filename"] == "before3.tif"
    assert g3["suggestedAfter"]["filename"] == "after3.tif"


def test_filename_iso_date_orders_before_after():
    images = [
        {"id": 1, "filename": "ecw_overlap_after_2025-03-01.tif", "path": "a/ecw_overlap_after_2025-03-01.tif", "captureDate": None, "bounds": None},
        {"id": 2, "filename": "ecw_overlap_before_2025-02-26.tif", "path": "a/ecw_overlap_before_2025-02-26.tif", "captureDate": None, "bounds": None},
    ]
    payload = build_area_groups(images)
    assert len(payload["groups"]) == 1
    g = payload["groups"][0]
    assert g["suggestedBefore"]["filename"].startswith("ecw_overlap_before")
    assert g["suggestedAfter"]["filename"].startswith("ecw_overlap_after")
    assert g["beforeDate"] == "2025-02-26"
    assert g["afterDate"] == "2025-03-01"


def test_distinct_places_grid_vs_sheet():
    assert distinct_places({"grid:54"}, {"sheet:h43x2e1"}) is True
    assert distinct_places({"seq:1"}, {"seq:1", "stem:x"}) is False
    assert distinct_places({"seq:1"}, set()) is False


if __name__ == "__main__":
    test_mysql_datetime_and_tiff_strings_parse()
    test_seq_pairs_stay_separate_despite_overlap()
    test_filename_iso_date_orders_before_after()
    test_distinct_places_grid_vs_sheet()
    print("ok")
