"""Load supported Release inputs and save the fixed CPK workbook."""
from __future__ import annotations

import json
from pathlib import Path
from deepyield.compute.release_export_session import export_input, export_catalog

from deepyield.compute.release_cpk import build_release_bin1_cpk_report
from deepyield.compute.release_cpk_excel import render_cpk_xlsx
from deepyield.compute.release_stdf import read_release_wide_all
from deepyield.rules.loader import load_analysis_rules


def export_release_cpk_xlsx(input_path, output_dir, *, device_id=None, test_temp=None,
                            part_indices=None, sites=None, test_columns=None, minimum_samples=None,
                            rule_dir=None):
    """Use saved JSON unchanged, or calculate an explicitly selected Release Parquet."""
    path = Path(input_path)
    metadata, result = None, None
    selection = {"part_indices": part_indices, "sites": sites, "test_columns": test_columns,
                 "minimum_samples": minimum_samples}
    if path.suffix.lower() == ".json":
        if any(value is not None for value in selection.values()) or rule_dir is not None:
            raise ValueError("已有 JSON 固定了选样与规则；如需重新选择，请使用 Release Parquet")
        payload = json.loads(path.read_text(encoding="utf-8"))
        report = (payload.get("reports") or {}).get("cpk") if "reports" in payload else payload
        if not isinstance(report, dict):
            raise ValueError("Release JSON 中没有 CPK 报告")
        result = payload.get("result")
        metadata = payload.get("metadata")
    elif path.suffix.lower() == ".parquet":
        frame, metadata = export_input(path, read_release_wide_all)
        report = build_release_bin1_cpk_report(frame, metadata, catalog=export_catalog(rule_dir),
                                                **selection)
    else:
        raise ValueError("请提供 Release JSON 或 Release Parquet；STDF 请先通过已有入口导入")
    workbook = render_cpk_xlsx(report, metadata=metadata, result=result,
                               device_id=device_id, test_temp=test_temp)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / workbook["filename"]
    with output.open("xb") as stream:
        stream.write(workbook["content"])
    return {"path": str(output.resolve()), "filename": workbook["filename"],
            "sheet_rows": workbook["sheet_rows"], "warnings": report.get("warnings", []),
            "source": report.get("source"), "rules": report.get("rules")}
