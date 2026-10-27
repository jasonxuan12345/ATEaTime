"""Canonical schema constants for DeepYield wide/long Parquet datasets."""

from __future__ import annotations

from collections.abc import Iterable

WIDE_IDENTITY_COLUMNS = [
    "Name",
    "LOT_ID",
    "FT_LOT_ID",
    "WAFER_ID",
    "SITE",
    "DIE_X",
    "DIE_Y",
    "SOURCE_LOT_ID",
    "SOURCE_WAFER_ID",
    "SOURCE_DIE_X",
    "SOURCE_DIE_Y",
    "HBIN",
    "SBIN",
]

WIDE_STDF_META_COLUMNS = [
    "__part_id",
    "__part_index",
    "__part_flag",
    "__pass_fail",
    "__part_txt",
    "__test_time_ms",
    "__source_file",
]

WIDE_RETEST_COLUMNS = ["__retest_index", "__is_retest", "__is_final_test"]

WIDE_SYNTHETIC_PREFIXES = ("__",)

WIDE_NON_TEST_COLUMNS = [*WIDE_IDENTITY_COLUMNS, *WIDE_STDF_META_COLUMNS, *WIDE_RETEST_COLUMNS]

LONG_IDENTITY_COLUMNS = ["Name", "LOT_ID", "WAFER_ID", "SITE", "DIE_X", "DIE_Y", "HBIN", "SBIN"]

REQUIRED_WIDE_COLUMNS = ("Name", "LOT_ID", "WAFER_ID", "SITE", "DIE_X", "DIE_Y", "HBIN", "SBIN")


class ParquetSchemaError(ValueError):
    """Raised when a Parquet dataset does not satisfy the DeepYield schema."""


def validate_wide_columns(columns: Iterable[object]) -> list[str]:
    """Validate and return wide columns as strings.

    The current UI and compute engines depend on these exact canonical names.
    """
    names = [str(column) for column in columns]
    missing = [column for column in REQUIRED_WIDE_COLUMNS if column not in names]
    if missing:
        raise ParquetSchemaError(
            "wide.parquet is missing required canonical column(s): " + ", ".join(missing)
        )
    return names


def is_wide_test_column(column: object) -> bool:
    name = str(column)
    if name.startswith(WIDE_SYNTHETIC_PREFIXES):
        return False
    return name.upper() not in {item.upper() for item in WIDE_IDENTITY_COLUMNS}


def discover_wide_test_columns(columns: Iterable[object]) -> list[str]:
    """Return dynamic test columns while excluding identity and __ synthetic fields."""
    return [str(column) for column in columns if is_wide_test_column(column)]


def build_asset_schema(columns: Iterable[object]) -> dict[str, object]:
    names = [str(column) for column in columns]
    readiness = {field: field in names for field in ("HBIN", "SBIN", "LOT_ID", "FT_LOT_ID", "WAFER_ID", "SITE", "DIE_X", "DIE_Y", "SOURCE_LOT_ID", "SOURCE_WAFER_ID", "SOURCE_DIE_X", "SOURCE_DIE_Y")}
    bin_column = "HBIN" if readiness["HBIN"] else "SBIN" if readiness["SBIN"] else None
    return {
        "lotColumn": "FT_LOT_ID" if readiness["FT_LOT_ID"] else "LOT_ID" if readiness["LOT_ID"] else None,
        "timeColumn": None,
        "siteColumn": "SITE" if readiness["SITE"] else None,
        "waferColumn": "WAFER_ID" if readiness["WAFER_ID"] else None,
        "dieXColumn": "DIE_X" if readiness["DIE_X"] else None,
        "dieYColumn": "DIE_Y" if readiness["DIE_Y"] else None,
        "binColumn": bin_column,
        "sourceFileColumn": "__source_file",
        "rowIndexColumn": "__row_index",
        "testColumns": discover_wide_test_columns(names),
    }
