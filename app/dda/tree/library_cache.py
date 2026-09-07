"""In-process caches for library pairing (invalidated on upload/delete/sync)."""
from __future__ import annotations

from typing import Any, Optional

_area_fp: Optional[tuple] = None
_area_payload: Optional[dict[str, Any]] = None


def invalidate_library_caches() -> None:
    global _area_fp, _area_payload
    _area_fp = None
    _area_payload = None


def get_area_groups_cache(fingerprint: tuple) -> Optional[dict[str, Any]]:
    if _area_fp == fingerprint:
        return _area_payload
    return None


def set_area_groups_cache(fingerprint: tuple, payload: dict[str, Any]) -> None:
    global _area_fp, _area_payload
    _area_fp = fingerprint
    _area_payload = payload
