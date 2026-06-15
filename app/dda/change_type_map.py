"""Map internal detection_engine labels → DDA report change types (FR-04)."""
from __future__ import annotations

from typing import Optional

DDA_NEW_CONSTRUCTION = "New Construction"
DDA_DEMOLITION = "Demolition"
DDA_EXTENSION = "Extension"
DDA_VEGETATION = "Vegetation Change"
DDA_OTHER = "Other"

_KEYWORDS = [
    (DDA_DEMOLITION, ("demolition", "clearing", "removed", "debris")),
    (DDA_EXTENSION, ("expansion", "widening", "extension", "renovation", "addition")),
    (DDA_VEGETATION, ("vegetation", "tree", "forest", "green", "crop")),
    (DDA_NEW_CONSTRUCTION, (
        "construction", "building", "structure", "road", "pavement",
        "temporary", "roof", "concrete", "foundation",
    )),
    (DDA_OTHER, ("water", "bare", "soil", "land", "unclassified")),
]


def map_to_dda_change_type(internal_type: str) -> str:
    """Return canonical DDA change type for a detection_engine object_type string."""
    if not internal_type:
        return DDA_OTHER
    lower = internal_type.lower()
    for dda_type, keywords in _KEYWORDS:
        if any(kw in lower for kw in keywords):
            return dda_type
    if "new" in lower:
        return DDA_NEW_CONSTRUCTION
    return DDA_OTHER


def enrich_region_for_dda(region: dict, *, dda_change_type: Optional[str] = None) -> dict:
    """Add DDA report fields to a serialized region dict."""
    internal = region.get("objectType") or region.get("object_type") or ""
    out = dict(region)
    out["ddaChangeType"] = dda_change_type or map_to_dda_change_type(internal)
    out["internalObjectType"] = internal
    out["reviewStatus"] = region.get("reviewStatus", "pending")
    return out
