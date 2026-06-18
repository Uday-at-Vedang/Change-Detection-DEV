"""Filesystem-safe slugs for zone/folder library paths."""
from __future__ import annotations

import re
import unicodedata
from typing import Iterable, Optional, Set

RESERVED_SLUGS = frozenset({"_unassigned", "legacy", "thumbs", "cache"})


def slugify(name: str, *, fallback: str = "item") -> str:
    """Convert display name to a safe path segment."""
    text = unicodedata.normalize("NFKD", (name or "").strip())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "_", text).strip("_").lower()
    if not text:
        text = fallback
    if text in RESERVED_SLUGS:
        text = f"{text}_1"
    return text[:64]


def unique_slug(base: str, existing: Set[str]) -> str:
    """Return base or base-2, base-3, … if base is taken."""
    slug = slugify(base)
    if slug not in existing:
        return slug
    n = 2
    while True:
        candidate = f"{slug}-{n}"[:64]
        if candidate not in existing:
            return candidate
        n += 1


def validate_path_segment(segment: str) -> bool:
    if not segment or segment in (".", ".."):
        return False
    if ".." in segment.split("/"):
        return False
    if not re.match(r"^[a-z0-9][a-z0-9_-]*$", segment, re.IGNORECASE):
        return False
    return True


def zone_folder_path(zone_slug: str, folder_slug: str, year: int) -> str:
    return f"{zone_slug}/{folder_slug}/{year}"
