"""Filesystem-safe slugs for tree node paths."""
from __future__ import annotations

import re
import unicodedata
from typing import Set

RESERVED = frozenset({"images", "thumbs", "cache", "legacy"})


def slugify(name: str, *, fallback: str = "node") -> str:
    text = unicodedata.normalize("NFKD", (name or "").strip())
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^\w\s-]", "", text, flags=re.UNICODE)
    text = re.sub(r"[\s_-]+", "_", text).strip("_").lower()
    if not text:
        text = fallback
    if text in RESERVED:
        text = f"{text}_1"
    return text[:64]


def unique_slug(base: str, existing: Set[str]) -> str:
    slug = slugify(base)
    if slug not in existing:
        return slug
    n = 2
    while True:
        candidate = f"{slug}-{n}"[:64]
        if candidate not in existing:
            return candidate
        n += 1
