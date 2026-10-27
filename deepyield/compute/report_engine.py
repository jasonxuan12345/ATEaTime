"""Deterministic report builders for compute-layer outputs."""

from __future__ import annotations

from collections.abc import Hashable
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from deepyield.compute.release_numeric import CG_TOLERANCE_FRACTION

_NON_TEST_COLUMNS: frozenset[str] = frozenset(
    {
        "NAME",
        "Name",
        "SBIN",
        "HBIN",
        "DIE_X",
        "DIE_Y",
        "SITE",
        "LOT_ID",
        "Lot_ID",
        "FT_LOT_ID",
        "WAFER_ID",
        "device",
        "part_type",
        "start_time",
        "finish_time",
        "test_program_name",
        "__part_id",
        "__part_index",
        "__part_flag",
        "__pass_fail",
        "__part_txt",
        "__test_time_ms",
        "__source_file",
        "__retest_index",
        "__is_retest",
    }
)
_METADATA_PREFIXES: tuple[str, ...] = (
    "MIR_", "MRR_", "PIR_", "PRR_", "SDR_", "WIR_", "WRR_",
)
_REPORT_METADATA_ROWS: frozenset[str] = frozenset(
    {
        "TEST_ID",
        "TEST_ID SUB_NR",
        "UNIT",
        "LOW_LIMIT",
        "HIGH_LIMIT",
    }
)
_BIN1_CPK_COLUMNS: list[str] = [
    "Test ID",
    "Test Name",
    "Unit",
    "ExecCount",
    "FailCount",
    "Lowlim",
    "Uplim",
    "Mean",
    "Std",
    "cpk/cp",
    "cpk",
]
_CGM_BASE_COLUMNS: list[str] = [
    "Test ID",
    "Test Name",
    "Unit",
    "ExecCount",
    "FailCount",
    "Lowlim",
    "Uplim",
]
_GREEN_FILL = "C6EFCE"
_YELLOW_FILL = "FFEB9C"
_RED_FILL = "FFC7CE"


def _to_json_serializable(value: Any) -> Any:
    """Normalize values into JSON-serializable Python primitives.

    This helper converts pandas/numpy scalar values to Python types and replaces
    missing values (e.g., NaN/NA/NaT) with ``None``.
    """
    if value is None:
        return None

    if isinstance(value, dict):
        return {str(key): _to_json_serializable(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_to_json_serializable(item) for item in value]

    if pd.isna(value):
        return None

    if hasattr(value, "item"):
        try:
            return value.item()
        except (TypeError, ValueError):
            pass

    return value


def _validate_summary(summary: Any, field_name: str) -> dict[str, Any]:
    """Validate summary payload type and return a dictionary."""
    if summary is None:
        return {}
    if not isinstance(summary, dict):
        raise TypeError(f"{field_name} must be a dict or None.")
    return summary


def _dataframe_to_records(frame: pd.DataFrame | None) -> list[dict[str, Any]]:
    """Convert a bin distribution DataFrame to a list of JSON-safe records."""
    if frame is None:
        return []

    if not isinstance(frame, pd.DataFrame):
        raise TypeError("bin_distribution must be a pandas DataFrame or None.")

    if frame.empty:
        return []

    records: list[dict[str, Any]] = []
    for row in frame.to_dict(orient="records"):
        records.append({str(key): _to_json_serializable(value) for key, value in row.items()})
    return records


def _normalize_metadata_label(value: object) -> str:
    """Normalize metadata row/column labels for tolerant matching."""
    return " ".join(str(value).strip().upper().split())


def _is_non_test_column(column_name: object) -> bool:
    """Return whether a dataframe column is device metadata rather than a test item."""
    col_str = str(column_name)
    if _normalize_metadata_label(column_name) in _NON_TEST_COLUMNS:
        return True
    # Also check original & lowered casings
    if col_str in _NON_TEST_COLUMNS:
        return True
    if col_str.lower() in _NON_TEST_COLUMNS:
        return True
    if col_str.startswith("__"):
        return True
    if col_str.startswith(_METADATA_PREFIXES):
        return True
    return False


def _coerce_float(value: object) -> float:
    """Convert a scalar to float, returning NaN for non-numeric or missing values."""
    numeric = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(numeric) if pd.notna(numeric) else float("nan")


def _empty_report_metadata() -> pd.DataFrame:
    """Return an empty canonical test metadata dataframe."""
    return pd.DataFrame(columns=["Test ID", "Test Name", "Unit", "Lowlim", "Uplim"])


def _canonicalize_test_metadata(
    data_df: pd.DataFrame,
    test_metadata_df: pd.DataFrame,
) -> pd.DataFrame:
    """Return report metadata in deterministic per-test-column order.

    The compute layer primarily receives MVA-style metadata rows whose first
    column contains labels such as ``TEST_ID``, ``UNIT``, ``LOW_LIMIT``, and
    ``HIGH_LIMIT``. This helper also accepts already-canonical metadata frames
    with test-name and limit columns for easier programmatic use.
    """
    if not isinstance(data_df, pd.DataFrame):
        raise ValueError("data_df must be a pandas DataFrame.")
    if not isinstance(test_metadata_df, pd.DataFrame):
        raise ValueError("test_metadata_df must be a pandas DataFrame.")

    canonical_by_column = {_normalize_metadata_label(column): column for column in test_metadata_df.columns}
    if "TEST NAME" in canonical_by_column or "TEST_NAME" in canonical_by_column:
        name_column = canonical_by_column.get("TEST NAME", canonical_by_column.get("TEST_NAME"))
        low_column = canonical_by_column.get("LOWLIM", canonical_by_column.get("LOW_LIMIT"))
        high_column = canonical_by_column.get("UPLIM", canonical_by_column.get("HIGH_LIMIT"))
        test_id_column = canonical_by_column.get("TEST ID", canonical_by_column.get("TEST_ID"))
        unit_column = canonical_by_column.get("UNIT")
        if name_column is None:
            return _empty_report_metadata()
        names = test_metadata_df[name_column].tolist()
        return pd.DataFrame(
            {
                "Test ID": test_metadata_df[test_id_column].tolist() if test_id_column else names,
                "Test Name": names,
                "Unit": test_metadata_df[unit_column].tolist() if unit_column else [np.nan] * len(names),
                "Lowlim": (
                    pd.to_numeric(test_metadata_df[low_column], errors="coerce").to_numpy(dtype="float64")
                    if low_column
                    else np.full(len(names), np.nan)
                ),
                "Uplim": (
                    pd.to_numeric(test_metadata_df[high_column], errors="coerce").to_numpy(dtype="float64")
                    if high_column
                    else np.full(len(names), np.nan)
                ),
            },
            columns=["Test ID", "Test Name", "Unit", "Lowlim", "Uplim"],
        )

    test_columns = [column for column in data_df.columns if not _is_non_test_column(column)]
    if test_metadata_df.empty or len(test_metadata_df.columns) == 0:
        return pd.DataFrame(
            {
                "Test ID": test_columns,
                "Test Name": test_columns,
                "Unit": [np.nan] * len(test_columns),
                "Lowlim": [np.nan] * len(test_columns),
                "Uplim": [np.nan] * len(test_columns),
            },
            columns=["Test ID", "Test Name", "Unit", "Lowlim", "Uplim"],
        )

    label_column_position = 0
    row_by_label: dict[str, pd.Series] = {}
    for row_tuple in test_metadata_df.itertuples(index=False, name=None):
        normalized = _normalize_metadata_label(row_tuple[label_column_position])
        if normalized in _REPORT_METADATA_ROWS and normalized not in row_by_label:
            row_by_label[normalized] = pd.Series(row_tuple, index=test_metadata_df.columns)

    metadata_columns = [column for column in test_metadata_df.columns if not _is_non_test_column(column)]
    if metadata_columns:
        ordered_test_columns = metadata_columns
    else:
        ordered_test_columns = test_columns

    rows: list[dict[str, object]] = []
    for column in ordered_test_columns:
        test_id_row = row_by_label.get("TEST_ID")
        unit_row = row_by_label.get("UNIT")
        low_row = row_by_label.get("LOW_LIMIT")
        high_row = row_by_label.get("HIGH_LIMIT")
        test_id = test_id_row[column] if test_id_row is not None and column in test_id_row.index else column
        unit = unit_row[column] if unit_row is not None and column in unit_row.index else np.nan
        lowlim = low_row[column] if low_row is not None and column in low_row.index else np.nan
        uplim = high_row[column] if high_row is not None and column in high_row.index else np.nan
        rows.append(
            {
                "Test ID": test_id,
                "Test Name": column,
                "Unit": unit,
                "Lowlim": _coerce_float(lowlim),
                "Uplim": _coerce_float(uplim),
            }
        )

    return pd.DataFrame(rows, columns=["Test ID", "Test Name", "Unit", "Lowlim", "Uplim"])


def _numeric_test_matrix(data_df: pd.DataFrame, test_names: list[Hashable]) -> pd.DataFrame:
    """Build a numeric matrix for requested test columns, adding NaN columns if absent."""
    numeric_columns: dict[int, pd.Series] = {}
    column_positions_by_name: dict[Hashable, list[int]] = {}
    for position, column_name in enumerate(data_df.columns):
        column_positions_by_name.setdefault(column_name, []).append(position)

    matched_counts: dict[Hashable, int] = {}
    for output_position, test_name in enumerate(test_names):
        occurrence = matched_counts.get(test_name, 0)
        matched_counts[test_name] = occurrence + 1
        positions = column_positions_by_name.get(test_name, [])
        if occurrence >= len(positions):
            numeric_columns[output_position] = pd.Series(np.nan, index=data_df.index, dtype="float64")
            continue
        numeric_columns[output_position] = pd.to_numeric(
            data_df.iloc[:, positions[occurrence]],
            errors="coerce",
        )

    matrix = pd.DataFrame(numeric_columns, index=data_df.index)
    matrix.columns = pd.Index(test_names)
    return matrix


def _numeric_bin_mask(series: pd.Series, bin_value: int | str) -> pd.Series:
    """Compare bin values deterministically across numeric/string representations."""
    expected = str(bin_value).strip()
    return series.map(lambda value: str(value).strip()).eq(expected)


def _outside_limits_count(values: pd.DataFrame, lowlims: np.ndarray, uplims: np.ndarray,
                          zero_limit_is_unset: bool = True) -> np.ndarray:
    """Count valid numeric values outside effective per-column limits.

    Default policy is true-fail-limit-v2: a zero or non-finite bound is "not
    set" and cannot produce an out-of-limit count. Callers on the Release path
    pass ``zero_limit_is_unset=False`` because there a written 0.0 is a real
    limit and only a missing (NaN) bound is unset.
    """
    if values.shape[1] == 0:
        return np.array([], dtype="float64")
    low_valid = np.isfinite(lowlims)
    high_valid = np.isfinite(uplims)
    if zero_limit_is_unset:
        low_valid = low_valid & (lowlims != 0.0)
        high_valid = high_valid & (uplims != 0.0)
    low_cmp = np.where(low_valid, lowlims, np.nan)
    high_cmp = np.where(high_valid, uplims, np.nan)
    below = values.lt(low_cmp, axis=1)
    above = values.gt(high_cmp, axis=1)
    valid_values = values.notna()
    counts = ((below | above) & valid_values).sum(axis=0).to_numpy(dtype="float64")
    counts[~(low_valid | high_valid)] = np.nan
    return counts


def _metadata_batches(metadata: pd.DataFrame, batch_size: int | None) -> list[pd.DataFrame]:
    if batch_size is None:
        return [metadata]
    if batch_size <= 0:
        raise ValueError("batch_size must be greater than 0.")
    return [metadata.iloc[start : start + batch_size] for start in range(0, len(metadata), batch_size)]


def _cpk_status_array(cpk_values: np.ndarray) -> np.ndarray:
    """Return a string status array for CPK color rules."""
    result = np.full(len(cpk_values), "N/A", dtype="<U10")
    if len(cpk_values) == 0:
        return result
    arr = np.asarray(cpk_values, dtype="float64")
    green = np.isfinite(arr) & (arr >= 2.0)
    yellow = np.isfinite(arr) & (arr >= 1.67) & (arr < 2.0)
    red = np.isfinite(arr) & (arr < 1.67)
    result[green] = "GREEN"
    result[yellow] = "YELLOW"
    result[red] = "RED"
    return result


def _bin1_cpk_report_for_metadata(
    data_df: pd.DataFrame,
    metadata: pd.DataFrame,
    bin_column: str = "SBIN",
    bin1_value: int | str = 1,
    zero_limit_is_unset: bool = True,
) -> pd.DataFrame:
    bin1_df = data_df.loc[_numeric_bin_mask(data_df[bin_column], bin1_value)]
    test_names = metadata["Test Name"].tolist()
    values = _numeric_test_matrix(bin1_df, test_names)

    exec_counts = values.notna().sum(axis=0).to_numpy(dtype="int64")
    means = values.mean(axis=0, skipna=True).to_numpy(dtype="float64")
    stds = values.std(axis=0, skipna=True, ddof=1).to_numpy(dtype="float64")
    lowlims = metadata["Lowlim"].to_numpy(dtype="float64", copy=False)
    uplims = metadata["Uplim"].to_numpy(dtype="float64", copy=False)
    fail_counts = _outside_limits_count(values, lowlims, uplims,
                                        zero_limit_is_unset=zero_limit_is_unset)

    # CP / CPK with individual limit handling. Default policy treats a zero
    # bound as unset; the Release path (zero_limit_is_unset=False) treats only
    # a missing bound as unset.
    low_valid = np.isfinite(lowlims)
    high_valid = np.isfinite(uplims)
    if zero_limit_is_unset:
        low_valid = low_valid & (lowlims != 0.0)
        high_valid = high_valid & (uplims != 0.0)
    valid_cp = low_valid & high_valid & np.isfinite(stds) & (stds != 0.0)
    cp = np.full(len(metadata), np.nan, dtype="float64")
    cp[valid_cp] = (uplims[valid_cp] - lowlims[valid_cp]) / (6.0 * stds[valid_cp])

    cpu = np.full(len(metadata), np.nan, dtype="float64")
    cpl = np.full(len(metadata), np.nan, dtype="float64")
    valid_cpu = high_valid & np.isfinite(means) & np.isfinite(stds) & (stds != 0.0)
    valid_cpl = low_valid & np.isfinite(means) & np.isfinite(stds) & (stds != 0.0)
    cpu[valid_cpu] = (uplims[valid_cpu] - means[valid_cpu]) / (3.0 * stds[valid_cpu])
    cpl[valid_cpl] = (means[valid_cpl] - lowlims[valid_cpl]) / (3.0 * stds[valid_cpl])

    # CPK = min(CPU, CPL) when both available, else whichever is available
    cpk = np.full(len(metadata), np.nan, dtype="float64")
    both = valid_cpu & valid_cpl
    cpu_only = valid_cpu & ~valid_cpl
    cpl_only = ~valid_cpu & valid_cpl
    cpk[both] = np.minimum(cpu[both], cpl[both])
    cpk[cpu_only] = cpu[cpu_only]
    cpk[cpl_only] = cpl[cpl_only]

    return pd.DataFrame(
        {
            "Test ID": metadata["Test ID"].to_numpy(copy=False),
            "Test Name": metadata["Test Name"].to_numpy(copy=False),
            "Unit": metadata["Unit"].to_numpy(copy=False),
            "ExecCount": exec_counts,
            "FailCount": fail_counts,
            "Lowlim": lowlims,
            "Uplim": uplims,
            "Mean": means,
            "Std": stds,
            "cpk/cp": np.divide(cpk, cp, out=np.full(len(metadata), np.nan, dtype="float64"), where=np.isfinite(cp) & (cp != 0.0)),
            "cpk": cpk,
        },
        columns=_BIN1_CPK_COLUMNS,
    )


def generate_bin1_cpk_report(
    data_df: pd.DataFrame,
    test_metadata_df: pd.DataFrame,
    bin_column: str = "SBIN",
    bin1_value: int | str = 1,
    batch_size: int | None = None,
    zero_limit_is_unset: bool = True,
) -> pd.DataFrame:
    """Generate deterministic BIN1 CPK statistics for each test item.

    Args:
        data_df: Device-level ATE data containing a bin column and test columns.
        test_metadata_df: Test metadata containing test IDs, units, and limits.
        bin_column: Column used to identify BIN1/pass devices.
        bin1_value: Value in ``bin_column`` that represents BIN1/pass devices.
        batch_size: Optional number of test columns to process per batch.
        zero_limit_is_unset: Default true-fail-limit-v2 policy treats a written
            0.0 bound as "not set". The Release path passes ``False`` because
            there only a missing (NaN) bound is unset.

    Returns:
        A dataframe with one row per test item and CPK-related statistics using
        only BIN1 devices. Metrics that cannot be computed are returned as NaN.

    Raises:
        ValueError: If inputs are not pandas dataframes or ``bin_column`` is missing.
    """
    if not isinstance(data_df, pd.DataFrame):
        raise ValueError("data_df must be a pandas DataFrame.")
    if bin_column not in data_df.columns:
        raise ValueError(f"data_df is missing required bin column: {bin_column}.")

    metadata = _canonicalize_test_metadata(data_df, test_metadata_df)
    if metadata.empty:
        return pd.DataFrame(columns=_BIN1_CPK_COLUMNS)

    reports = [
        _bin1_cpk_report_for_metadata(data_df, batch, bin_column=bin_column, bin1_value=bin1_value,
                                      zero_limit_is_unset=zero_limit_is_unset)
        for batch in _metadata_batches(metadata, batch_size)
    ]
    return pd.concat(reports, ignore_index=True) if len(reports) > 1 else reports[0]


def _site_sort_key(value: object) -> tuple[str, str]:
    """Return a stable site ordering key for mixed numeric/string labels."""
    text = str(value).strip()
    numeric = pd.to_numeric(pd.Series([text]), errors="coerce").iloc[0]
    if pd.notna(numeric):
        return ("0", f"{float(numeric):020.10f}")
    return ("1", text)


def _site_label(value: object) -> str:
    """Format a raw site value for report column names."""
    if isinstance(value, (int, np.integer)):
        return str(int(value))
    if isinstance(value, (float, np.floating)) and np.isfinite(value) and float(value).is_integer():
        return str(int(value))
    return str(value).strip()


def _cgm_report_for_metadata(
    data_df: pd.DataFrame,
    metadata: pd.DataFrame,
    site_column: str = "SITE",
    zero_limit_is_unset: bool = True,
) -> pd.DataFrame:
    test_names = metadata["Test Name"].tolist()
    values = _numeric_test_matrix(data_df, test_names)
    lowlims = metadata["Lowlim"].to_numpy(dtype="float64", copy=False)
    uplims = metadata["Uplim"].to_numpy(dtype="float64", copy=False)
    # Cg requires a two-sided effective spec; otherwise the tolerance is
    # undefined and Cg is NOT EVALUATED. Default policy (true-fail-limit-v2)
    # treats a zero bound as unset; the Release path treats only a missing
    # bound as unset.
    two_sided = np.isfinite(lowlims) & np.isfinite(uplims)
    if zero_limit_is_unset:
        two_sided = two_sided & (lowlims != 0.0) & (uplims != 0.0)
    tolerance = np.where(two_sided, uplims - lowlims, np.nan)

    exec_counts = values.notna().sum(axis=0).to_numpy(dtype="int64")
    fail_counts = _outside_limits_count(values, lowlims, uplims,
                                        zero_limit_is_unset=zero_limit_is_unset)

    site_cg_arrays: dict[str, np.ndarray] = {}
    if site_column in data_df.columns:
        raw_sites = data_df[site_column]
        site_values = sorted(raw_sites.dropna().unique().tolist(), key=_site_sort_key)
        if site_values:
            site_keys = raw_sites.map(lambda value: _site_label(value) if pd.notna(value) else np.nan)
            site_stds = values.groupby(site_keys, dropna=True, sort=False).std(ddof=1)
            for site_value in site_values:
                label = _site_label(site_value)
                column_name = f"site{label} cg"
                cg_values = np.full(len(metadata), np.nan, dtype="float64")
                if label in site_stds.index:
                    stds = site_stds.loc[label].to_numpy(dtype="float64", copy=False)
                    valid = np.isfinite(tolerance) & np.isfinite(stds) & (stds != 0.0)
                    cg_values[valid] = CG_TOLERANCE_FRACTION * tolerance[valid] / (6.0 * stds[valid])
                site_cg_arrays[column_name] = cg_values

    output_data: dict[str, object] = {
        "Test ID": metadata["Test ID"].to_numpy(copy=False),
        "Test Name": metadata["Test Name"].to_numpy(copy=False),
        "Unit": metadata["Unit"].to_numpy(copy=False),
        "ExecCount": exec_counts,
        "FailCount": fail_counts,
        "Lowlim": lowlims,
        "Uplim": uplims,
    }
    output_data.update(site_cg_arrays)
    site_cg_matrix = np.column_stack(list(site_cg_arrays.values())) if site_cg_arrays else np.empty((len(metadata), 0))
    if site_cg_matrix.size:
        all_nan = np.all(np.isnan(site_cg_matrix), axis=1)
        safe_matrix = np.where(np.isnan(site_cg_matrix), np.inf, site_cg_matrix)
        min_cg = np.min(safe_matrix, axis=1)
        min_cg[all_nan] = np.nan
    else:
        min_cg = np.full(len(metadata), np.nan, dtype="float64")
    output_data["min_Cg"] = min_cg

    return pd.DataFrame(output_data, columns=_CGM_BASE_COLUMNS + list(site_cg_arrays.keys()) + ["min_Cg"])


def generate_cgm_report(
    data_df: pd.DataFrame,
    test_metadata_df: pd.DataFrame,
    site_column: str = "SITE",
    batch_size: int | None = None,
    zero_limit_is_unset: bool = True,
) -> pd.DataFrame:
    """Generate deterministic CGM/Cg capability statistics by site.

    Args:
        data_df: Device-level ATE data containing test columns and optionally a
            site column.
        test_metadata_df: Test metadata containing test IDs, units, and limits.
        site_column: Column used to group devices by tester site.
        batch_size: Optional number of test columns to process per batch.
        zero_limit_is_unset: Default true-fail-limit-v2 policy treats a written
            0.0 bound as "not set". The Release path passes ``False`` because
            there only a missing (NaN) bound is unset.

    Returns:
        A dataframe with one row per test item, dynamic ``site<label> cg``
        columns, and ``min_Cg``. Metrics that cannot be computed are NaN.

    Raises:
        ValueError: If inputs are not pandas dataframes.
    """
    if not isinstance(data_df, pd.DataFrame):
        raise ValueError("data_df must be a pandas DataFrame.")

    metadata = _canonicalize_test_metadata(data_df, test_metadata_df)
    if metadata.empty:
        return pd.DataFrame(columns=_CGM_BASE_COLUMNS + ["min_Cg"])

    reports = [
        _cgm_report_for_metadata(data_df, batch, site_column=site_column,
                                 zero_limit_is_unset=zero_limit_is_unset)
        for batch in _metadata_batches(metadata, batch_size)
    ]
    return pd.concat(reports, ignore_index=True) if len(reports) > 1 else reports[0]


def _write_report_excel(
    report_df: pd.DataFrame,
    output_path: str,
    styled_columns: list[str],
    color_for_value: Any,
) -> None:
    """Write an Excel report and color configured numeric cells."""
    from openpyxl.styles import PatternFill

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        report_df.to_excel(writer, index=False)
        workbook = writer.book
        worksheet = workbook.active
        worksheet.freeze_panes = "A2"

        fills = {
            "green": PatternFill(fill_type="solid", fgColor=_GREEN_FILL),
            "yellow": PatternFill(fill_type="solid", fgColor=_YELLOW_FILL),
            "red": PatternFill(fill_type="solid", fgColor=_RED_FILL),
        }
        header_to_index = {cell.value: cell.column for cell in worksheet[1]}
        for column_name in styled_columns:
            column_index = header_to_index.get(column_name)
            if column_index is None:
                continue
            values = pd.to_numeric(report_df[column_name], errors="coerce").to_numpy()
            for row_index, numeric_value in enumerate(values, 2):
                cell = worksheet.cell(row=row_index, column=column_index)
                if pd.isna(numeric_value):
                    continue
                color = color_for_value(float(numeric_value))
                if color is not None:
                    cell.fill = fills[color]

        for column_cells in worksheet.columns:
            header = column_cells[0].value
            values = [header, *[cell.value for cell in column_cells[1:]]]
            max_length = max(len(str(value)) for value in values if value is not None) if values else 0
            worksheet.column_dimensions[column_cells[0].column_letter].width = min(max(max_length + 2, 8), 40)



def _bin1_cpk_color(value: float) -> str | None:
    """Return the BIN1 CPK style bucket for a numeric CPK value.

    GREEN:  CPK >= 2.0
    YELLOW: 1.67 < CPK < 2.0
    RED:    CPK < 1.67
    None:   value is None or NaN
    """
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    if value >= 2.0:
        return "green"
    if value > 1.67:
        return "yellow"
    return "red"


def _cgm_color(value: float) -> str | None:
    """Return the CGM style bucket for a numeric CG value."""
    if value > 1.33:
        return "green"
    if 1.0 <= value <= 1.33:
        return "yellow"
    return "red"


def export_bin1_cpk_report_excel(
    report_df: pd.DataFrame,
    output_path: str,
) -> None:
    """Export a BIN1 CPK report dataframe to a styled Excel workbook.

    Args:
        report_df: Report dataframe from :func:`generate_bin1_cpk_report`.
        output_path: Destination ``.xlsx`` path.

    Raises:
        ValueError: If ``report_df`` is not a pandas dataframe.
    """
    if not isinstance(report_df, pd.DataFrame):
        raise ValueError("report_df must be a pandas DataFrame.")
    cpk_column = "cpk" if "cpk" in report_df.columns else "CPK"
    _write_report_excel(report_df, output_path, [cpk_column], _bin1_cpk_color)


def export_cgm_report_excel(
    report_df: pd.DataFrame,
    output_path: str,
) -> None:
    """Export a CGM report dataframe to a styled Excel workbook.

    Args:
        report_df: Report dataframe from :func:`generate_cgm_report`.
        output_path: Destination ``.xlsx`` path.

    Raises:
        ValueError: If ``report_df`` is not a pandas dataframe.
    """
    if not isinstance(report_df, pd.DataFrame):
        raise ValueError("report_df must be a pandas DataFrame.")
    columns = report_df.columns.tolist()
    if "min_Cg" in columns:
        first_site_index = next(
            (
                index
                for index, column in enumerate(columns)
                if column.startswith("site") and column.endswith(" cg")
            ),
            None,
        )
        min_cg_index = columns.index("min_Cg")
        styled_columns = columns[first_site_index : min_cg_index + 1] if first_site_index is not None else ["min_Cg"]
    else:
        styled_columns = []
    _write_report_excel(report_df, output_path, styled_columns, _cgm_color)


def build_yield_report(
    yield_summary: dict[str, Any] | None,
    bin_distribution: pd.DataFrame | None = None,
) -> dict[str, Any]:
    """Build a structured yield report.

    Args:
        yield_summary: Summary dictionary from ``bin_engine.calculate_yield``.
        bin_distribution: Optional DataFrame from
            ``bin_engine.calculate_bin_distribution``.

    Returns:
        A dictionary with ``yield`` and ``bin_distribution`` fields.
    """
    summary = _validate_summary(yield_summary, "yield_summary")

    return {
        "yield": _to_json_serializable(summary),
        "bin_distribution": _dataframe_to_records(bin_distribution),
    }


def build_anomaly_report(anomaly_summary: dict[str, Any] | None) -> dict[str, Any]:
    """Build a structured anomaly report.

    Args:
        anomaly_summary: Summary dictionary from
            ``anomaly_engine.get_anomaly_summary``.

    Returns:
        A dictionary with an ``anomaly`` field.
    """
    summary = _validate_summary(anomaly_summary, "anomaly_summary")
    return {"anomaly": _to_json_serializable(summary)}


def build_compute_report(
    yield_summary: dict[str, Any] | None = None,
    bin_distribution: pd.DataFrame | None = None,
    anomaly_summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a unified compute report from available summary inputs."""
    yield_report = build_yield_report(
        yield_summary=yield_summary,
        bin_distribution=bin_distribution,
    )
    anomaly_report = build_anomaly_report(anomaly_summary=anomaly_summary)

    return {
        "yield": yield_report["yield"],
        "bin_distribution": yield_report["bin_distribution"],
        "anomaly": anomaly_report["anomaly"],
    }
