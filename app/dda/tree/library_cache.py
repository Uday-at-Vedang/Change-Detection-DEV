"""In-process caches for library pairing (invalidated on upload/delete/sync)."""
from __future__ import annotations

from typing import Any, Optional

# fingerprint → { node_id_or_0: payload }
_area_fp: Optional[tuple] = None
_area_by_node: dict[int, dict[str, Any]] = {}


def invalidate_library_caches() -> None:
    global _area_fp, _area_by_node
    _area_fp = None
    _area_by_node = {}


def get_area_groups_cache(fingerprint: tuple, node_id: int = 0) -> Optional[dict[str, Any]]:
    if _area_fp != fingerprint:
        return None
    return _area_by_node.get(int(node_id or 0))


def set_area_groups_cache(fingerprint: tuple, payload: dict[str, Any], node_id: int = 0) -> None:
    global _area_fp, _area_by_node
    if _area_fp != fingerprint:
        _area_by_node = {}
        _area_fp = fingerprint
    _area_by_node[int(node_id or 0)] = payload
