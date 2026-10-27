"""Wafer-map display-frame transforms from device ``wafer_map.display``.

Transforms are applied to aggregate map points after the DuckDB / index query.
``flip_x`` / ``flip_y`` reflect coordinates within each wafer's bounding box.
``origin`` / ``corner`` are product semantics echoed to clients; the configured
flips encode the screen correction for the current devices.
"""

from __future__ import annotations

from typing import Any

from deepyield.device_config.models import WaferMapDisplayConfig


def display_key(display: WaferMapDisplayConfig | None) -> tuple[Any, ...]:
    """Stable equality key for conflict detection."""
    if display is None:
        return ("__none__",)
    return (display.origin, display.corner, display.flip_x, display.flip_y)


def display_to_context(display: WaferMapDisplayConfig | None) -> dict[str, Any] | None:
    if display is None:
        return None
    payload = display.to_dict()
    payload["applied"] = bool(display.flip_x or display.flip_y)
    return payload


def unify_displays(
    displays: list[WaferMapDisplayConfig | None],
) -> WaferMapDisplayConfig | None:
    """Return the single shared display, or raise ``ValueError`` on conflict.

    An empty input yields ``None``. All-``None`` yields ``None``. Mixed
    ``None`` and concrete displays, or two different concrete displays, conflict.
    """
    if not displays:
        return None
    keys = {display_key(item) for item in displays}
    if len(keys) > 1:
        raise ValueError("conflicting wafer_map.display configurations across sources")
    return displays[0]


def apply_display_to_map_points(
    map_points: list[dict[str, Any]],
    display: WaferMapDisplayConfig | None,
) -> list[dict[str, Any]]:
    """Return map points with per-wafer flip transforms applied.

    When ``display`` is ``None`` or both flips are false, points are returned
    unchanged (same list object is acceptable; callers may rely on either).
    """
    if display is None or (not display.flip_x and not display.flip_y):
        return map_points
    if not map_points:
        return map_points

    bounds: dict[tuple[str, str], list[int]] = {}
    for point in map_points:
        key = (str(point.get("lot_id") or ""), str(point.get("wafer_id") or ""))
        x = int(point["x"])
        y = int(point["y"])
        box = bounds.get(key)
        if box is None:
            bounds[key] = [x, x, y, y]
        else:
            if x < box[0]:
                box[0] = x
            if x > box[1]:
                box[1] = x
            if y < box[2]:
                box[2] = y
            if y > box[3]:
                box[3] = y

    transformed: list[dict[str, Any]] = []
    for point in map_points:
        key = (str(point.get("lot_id") or ""), str(point.get("wafer_id") or ""))
        min_x, max_x, min_y, max_y = bounds[key]
        x = int(point["x"])
        y = int(point["y"])
        if display.flip_x:
            x = max_x + min_x - x
        if display.flip_y:
            y = max_y + min_y - y
        updated = dict(point)
        updated["x"] = x
        updated["y"] = y
        transformed.append(updated)
    return transformed
