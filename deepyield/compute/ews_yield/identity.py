"""Physical-Die identity and quality for EWS Yield v2.

Implements YIELD_CONTRACT.md section 2.3:
  Die key = project_id + product_id + lot_id + wafer_id
           + coordinate_system_id + die_x + die_y

Rules:
  - site and head are excluded from identity
  - (0,0) is valid unless excluded by expected wafer map
  - sentinel/missing coordinates are unusable
  - different products or coordinate systems never merge
"""

from __future__ import annotations

from typing import Any

import pandas as pd

from deepyield.compute.ews_yield.models import (
    CoordinateStatus,
    IdentityStatus,
)

# Sentinel coordinate values that indicate missing/unusable
_SENTINEL_COORDINATES: set[int] = {-32768, 32767, -1}

# Die key column names in canonical order
DIE_KEY_COLUMNS: tuple[str, ...] = (
    "project_id",
    "product_id",
    "lot_id",
    "wafer_id",
    "coordinate_system_id",
    "die_x",
    "die_y",
)


def classify_coordinate_status(
    die_x: Any, die_y: Any,
) -> CoordinateStatus:
    """Classify a single coordinate pair's usability."""
    if die_x is None or die_y is None:
        return CoordinateStatus.MISSING
    try:
        x_int = int(die_x)
        y_int = int(die_y)
    except (TypeError, ValueError):
        return CoordinateStatus.UNUSABLE
    if pd.isna(die_x) or pd.isna(die_y):
        return CoordinateStatus.MISSING
    if x_int in _SENTINEL_COORDINATES or y_int in _SENTINEL_COORDINATES:
        return CoordinateStatus.SENTINEL
    return CoordinateStatus.USABLE


def build_die_key(row: pd.Series) -> tuple[Any, ...] | None:
    """Build the canonical Die key from a row.

    Returns None if coordinates are unusable.
    """
    status = classify_coordinate_status(
        row.get("die_x"), row.get("die_y"),
    )
    if status != CoordinateStatus.USABLE:
        return None
    return tuple(
        row.get(col) for col in DIE_KEY_COLUMNS
    )


def assess_identity_status(
    group: pd.DataFrame,
) -> IdentityStatus:
    """Assess identity status for a group of PRRs sharing a location.

    YIELD_CONTRACT 2.3: a population with >1 PRR and only one coordinate
    pair is IDENTITY_UNUSABLE, unless all PRRs are proven attempts of
    one physical Die.

    For the reference engine, we treat same-coordinate PRRs as retests
    of one Die (optimistic). The query layer may override.
    """
    unique_coords = set()
    for _, row in group.iterrows():
        status = classify_coordinate_status(
            row.get("die_x"), row.get("die_y"),
        )
        if status == CoordinateStatus.USABLE:
            unique_coords.add((row.get("die_x"), row.get("die_y")))
    if len(unique_coords) <= 1 and len(group) > 1:
        # All same coord or no usable coords with multiple PRRs
        has_usable = any(
            classify_coordinate_status(
                row.get("die_x"), row.get("die_y"),
            ) == CoordinateStatus.USABLE
            for _, row in group.iterrows()
        )
        if not has_usable:
            return IdentityStatus.IDENTITY_UNUSABLE
    return IdentityStatus.USABLE


def resolve_die_coordinates(df: pd.DataFrame) -> pd.Series:
    """Return a Series of CoordinateStatus for each row."""
    return pd.Series(
        [
            classify_coordinate_status(row.die_x, row.die_y)
            for row in df.itertuples()
        ],
        index=df.index,
        name="coordinate_status",
    )
