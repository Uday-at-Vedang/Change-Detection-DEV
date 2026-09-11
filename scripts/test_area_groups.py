"""Same-area pairing: metadata comparison, exactly two images, no reuse."""
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dda.tree.area_groups import (
    LocationIndex,
    build_area_groups,
    conflicting_keys,
    distinct_places,
    parse_datetime,
)

DELHI = LocationIndex(
    zones=[(1, "Central Delhi"), (2, "North Delhi")],
    villages=[(5, "Daryaganj", 1), (6, "Paharganj", 1), (7, "Model Town", 1), (8, "Model Town", 2)],
)

BOUNDS = {"west": 77.0, "south": 28.0, "east": 77.2, "north": 28.2}
ELSEWHERE = {"west": 78.0, "south": 29.0, "east": 78.2, "north": 29.2}


def _paths(group):
    return [img["path"] for img in group["images"]]


def test_mysql_datetime_and_tiff_strings_parse():
    assert parse_datetime("2025-03-01 14:30:00") == datetime(2025, 3, 1, 14, 30, 0)
    assert parse_datetime("2025:02:26 00:00:00") == datetime(2025, 2, 26, 0, 0, 0)
    assert parse_datetime("2025-02-26T00:00:00") == datetime(2025, 2, 26)


def test_seq_pairs_stay_separate_despite_overlap():
    images = [
        {"id": 1, "filename": "before3.tif", "path": "a/before3.tif", "captureDate": "2025-07-24", "bounds": BOUNDS},
        {"id": 2, "filename": "after3.tif", "path": "a/after3.tif", "captureDate": "2026-07-31", "bounds": BOUNDS},
        {"id": 3, "filename": "before7.tif", "path": "a/before7.tif", "captureDate": "2026-07-31", "bounds": BOUNDS},
        {"id": 4, "filename": "after7.tif", "path": "a/after7.tif", "captureDate": "2026-07-31", "bounds": BOUNDS},
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


def test_conflicting_keys_only_within_one_vocabulary():
    # Same vocabulary, different value: never the same place.
    assert conflicting_keys({"seq:3"}, {"seq:7"}) is True
    assert conflicting_keys({"grid:54"}, {"grid:60"}) is True
    # Different vocabularies cannot contradict each other.
    assert conflicting_keys({"grid:54"}, {"sheet:h43x2e1"}) is False
    assert conflicting_keys({"grid:54"}, set()) is False


def test_strong_overlap_beats_mismatched_naming_schemes():
    """A grid number and a survey sheet id can name the same place."""
    images = [
        {"id": 1, "filename": "Grid_54.tif", "path": "a/Grid_54.tif", "captureDate": "2025-06-11", "bounds": BOUNDS},
        {"id": 2, "filename": "H43X2E1.tif", "path": "a/H43X2E1.tif", "captureDate": "2025-06-27", "bounds": BOUNDS},
    ]
    payload = build_area_groups(images)
    assert len(payload["groups"]) == 1
    assert payload["groups"][0]["match"] == "bounds"
    assert payload["unpaired"] == []


def test_georeferenced_images_far_apart_never_pair():
    images = [
        {"id": 1, "filename": "grid_9_before.tif", "path": "a/grid_9_before.tif", "captureDate": "2025-01-01", "bounds": BOUNDS},
        {"id": 2, "filename": "grid_9_after.tif", "path": "a/grid_9_after.tif", "captureDate": "2026-01-01", "bounds": ELSEWHERE},
    ]
    payload = build_area_groups(images)
    assert payload["groups"] == []
    assert len(payload["unpaired"]) == 2


def test_shared_location_cannot_rescue_a_weak_overlap():
    """When both footprints are known, the map decides, not the folder."""
    barely = {"west": 77.19, "south": 28.19, "east": 77.40, "north": 28.40}
    images = [
        {"id": 1, "filename": "B2_ORI.tif", "path": "a/B2_ORI.tif", "nodeId": 1,
         "nodePath": "Central Delhi/Daryaganj", "captureDate": "2025-01-01", "bounds": BOUNDS},
        {"id": 2, "filename": "B3_ORI_CLIP.tif", "path": "a/B3_ORI_CLIP.tif", "nodeId": 1,
         "nodePath": "Central Delhi/Daryaganj", "captureDate": "2026-01-01", "bounds": barely},
    ]
    assert build_area_groups(images, DELHI)["groups"] == []


def test_folder_location_cannot_veto_a_clear_overlap():
    """A file simply parked under 'Unassigned' still pairs with its own place."""
    images = [
        {"id": 1, "filename": "H43X2E2.tif", "path": "a/H43X2E2.tif",
         "nodePath": "Central Delhi/Daryaganj", "captureDate": "2025-01-01", "bounds": BOUNDS},
        {"id": 2, "filename": "H43X2E2_ORI.tif", "path": "b/H43X2E2_ORI.tif",
         "nodePath": "Unassigned/Legacy/2025", "captureDate": "2026-01-01", "bounds": BOUNDS},
    ]
    assert len(build_area_groups(images, DELHI)["groups"]) == 1


def test_different_villages_never_pair():
    images = [
        {"id": 1, "filename": "site.tif", "path": "a/site.tif", "captureDate": "2025-01-01",
         "bounds": BOUNDS, "zoneId": 1, "villageId": 5, "villageName": "Karol Bagh"},
        {"id": 2, "filename": "site_new.tif", "path": "a/site_new.tif", "captureDate": "2026-01-01",
         "bounds": BOUNDS, "zoneId": 1, "villageId": 6, "villageName": "Paharganj"},
    ]
    payload = build_area_groups(images)
    assert payload["groups"] == []


def test_village_pairs_images_that_have_no_map_extent():
    images = [
        {"id": 1, "filename": "scan_a.png", "path": "a/scan_a.png", "captureDate": "2025-01-01",
         "bounds": None, "zoneId": 1, "villageId": 5, "villageName": "Karol Bagh", "zoneName": "Central Delhi"},
        {"id": 2, "filename": "scan_b.png", "path": "a/scan_b.png", "captureDate": "2026-01-01",
         "bounds": None, "zoneId": 1, "villageId": 5, "villageName": "Karol Bagh", "zoneName": "Central Delhi"},
    ]
    payload = build_area_groups(images)
    assert len(payload["groups"]) == 1
    g = payload["groups"][0]
    assert g["match"] == "location"
    assert any("Karol Bagh" in reason for reason in g["reasons"])


def test_unrelated_images_in_one_folder_do_not_pair():
    """Folder and capture date alone are not evidence of the same place."""
    images = [
        {"id": 1, "filename": "alpha.png", "path": "a/alpha.png", "nodeId": 1,
         "nodePath": "Delhi", "captureDate": "2025-01-01", "bounds": None},
        {"id": 2, "filename": "omega.png", "path": "a/omega.png", "nodeId": 1,
         "nodePath": "Delhi", "captureDate": "2026-01-01", "bounds": None},
    ]
    payload = build_area_groups(images)
    assert payload["groups"] == []
    assert len(payload["unpaired"]) == 2


def test_pairs_hold_two_images_and_never_reuse_one():
    """Five dates of one place become two pairs plus one leftover."""
    images = [
        {"id": i, "filename": f"Grid_54_{2021 + i}.tif", "path": f"a/Grid_54_{2021 + i}.tif",
         "nodeId": 1, "nodePath": "Delhi", "captureDate": f"{2021 + i}-04-01", "bounds": BOUNDS}
        for i in range(5)
    ]
    payload = build_area_groups(images)
    assert len(payload["groups"]) == 2
    assert payload["pairedImages"] == 4
    assert len(payload["unpaired"]) == 1

    seen = set()
    for group in payload["groups"]:
        assert len(group["images"]) == 2
        for path in _paths(group):
            assert path not in seen, f"{path} was used by two pairs"
            seen.add(path)
    ids = [g["id"] for g in payload["groups"]]
    assert len(ids) == len(set(ids))
    # The whole cluster stays available for a manual swap.
    assert len(payload["groups"][0]["candidates"]) == 5


def test_folder_names_supply_the_location():
    """Images indexed from disk get their zone/village from the folder tree."""
    images = [
        {"id": 1, "filename": "scan_a.png", "path": "a/scan_a.png", "captureDate": "2025-01-01",
         "bounds": None, "nodePath": "Central Delhi/Daryaganj"},
        {"id": 2, "filename": "scan_b.png", "path": "b/scan_b.png", "captureDate": "2026-01-01",
         "bounds": None, "nodePath": "Central Delhi/Daryaganj"},
    ]
    payload = build_area_groups(images, DELHI)
    assert len(payload["groups"]) == 1
    g = payload["groups"][0]
    assert any("Daryaganj" in reason for reason in g["reasons"])
    assert g["images"][0]["villageName"] == "Daryaganj"
    # Without the masters there is no location to compare, so nothing pairs.
    assert build_area_groups(images)["groups"] == []


def test_folder_location_never_crosses_zones():
    images = [
        {"id": 1, "filename": "scan_a.png", "path": "a/scan_a.png", "captureDate": "2025-01-01",
         "bounds": None, "nodePath": "Central Delhi/Daryaganj"},
        {"id": 2, "filename": "scan_b.png", "path": "b/scan_b.png", "captureDate": "2026-01-01",
         "bounds": None, "nodePath": "North Delhi/Model Town"},
    ]
    assert build_area_groups(images, DELHI)["groups"] == []


def test_ambiguous_village_name_is_left_unresolved():
    """'Model Town' exists in two zones, so the folder alone proves nothing."""
    images = [
        {"id": 1, "filename": "scan_a.png", "path": "a/scan_a.png", "captureDate": "2025-01-01",
         "bounds": None, "nodePath": "Model Town"},
        {"id": 2, "filename": "scan_b.png", "path": "b/scan_b.png", "captureDate": "2026-01-01",
         "bounds": None, "nodePath": "Model Town"},
    ]
    assert build_area_groups(images, DELHI)["groups"] == []


def test_named_before_after_wins_over_same_day_timestamps():
    """An 'after' uploaded a minute earlier must not become the Before image."""
    images = [
        {"id": 1, "filename": "after5.tif", "path": "a/after5.tif",
         "captureDate": "2026-07-31T09:00:00", "bounds": BOUNDS},
        {"id": 2, "filename": "before5.tif", "path": "a/before5.tif",
         "captureDate": "2026-07-31T10:00:00", "bounds": BOUNDS},
    ]
    g = build_area_groups(images)["groups"][0]
    assert g["suggestedBefore"]["filename"] == "before5.tif"
    assert g["suggestedAfter"]["filename"] == "after5.tif"


def test_reasons_name_the_metadata_that_matched():
    images = [
        {"id": 1, "filename": "Grid_54.tif", "path": "Delhi/Grid_54.tif", "nodeId": 1,
         "nodePath": "Delhi", "captureDate": "2024-01-01", "bounds": BOUNDS,
         "zoneId": 1, "villageId": 5, "villageName": "Karol Bagh"},
        {"id": 2, "filename": "Grid_54_new.tif", "path": "Delhi/Grid_54_new.tif", "nodeId": 1,
         "nodePath": "Delhi", "captureDate": "2026-01-01", "bounds": BOUNDS,
         "zoneId": 1, "villageId": 5, "villageName": "Karol Bagh"},
    ]
    reasons = build_area_groups(images)["groups"][0]["reasons"]
    joined = " | ".join(reasons)
    assert "map overlap" in joined
    assert "grid 54" in joined
    assert "Karol Bagh" in joined
    assert "same folder" in joined
    assert "apart" in joined


def test_three_images_become_one_pair_and_one_unpaired():
    """Automatic pairing never emits a 3-image group."""
    images = [
        {"id": 1, "filename": "site_2024.png", "path": "a/site_2024.png",
         "captureDate": "2024-01-01", "bounds": None, "zoneId": 1, "villageId": 5},
        {"id": 2, "filename": "site_2025.png", "path": "a/site_2025.png",
         "captureDate": "2025-01-01", "bounds": None, "zoneId": 1, "villageId": 5},
        {"id": 3, "filename": "site_2026.png", "path": "a/site_2026.png",
         "captureDate": "2026-01-01", "bounds": None, "zoneId": 1, "villageId": 5},
    ]
    payload = build_area_groups(images)
    assert len(payload["groups"]) == 1
    assert len(payload["groups"][0]["images"]) == 2
    assert payload["pairedImages"] == 2
    assert len(payload["unpaired"]) == 1
    assert len(set(_paths(payload["groups"][0]))) == 2


if __name__ == "__main__":
    for name, fn in sorted(list(globals().items())):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"ok  {name}")
    print("all ok")
