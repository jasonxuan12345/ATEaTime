"""Save the Cgm workbook from a fixed JSON snapshot or selected Release Parquet."""
from __future__ import annotations

import json
from pathlib import Path
from deepyield.compute.release_export_session import export_input, export_catalog

from deepyield.compute.release_cgm import build_release_cgm_report
from deepyield.compute.release_cgm_excel import render_cgm_xlsx
from deepyield.compute.release_stdf import read_release_wide_all
from deepyield.rules.loader import load_analysis_rules


def export_release_cgm_xlsx(input_path, output_dir, *, device_id=None,
                            part_indices=None, sites=None, test_columns=None,
                            minimum_samples_per_site=None, rule_dir=None,
                            bin1_cpk_report_path=None):
    """Preserve saved selection/CPK support; never overwrite an existing workbook."""
    path = Path(input_path)
    metadata, result = None, None
    selection = {"part_indices": part_indices, "sites": sites, "test_columns": test_columns,
                 "minimum_samples_per_site": minimum_samples_per_site}
    if path.suffix.lower() == ".json":
        if any(value is not None for value in selection.values()) or rule_dir is not None or bin1_cpk_report_path is not None:
            raise ValueError("已有 JSON 固定了选样与 CPK 来源；如需重新选择，请使用 Release Parquet")
        payload = json.loads(path.read_text(encoding="utf-8"))
        report = (payload.get("reports") or {}).get("cgm") if "reports" in payload else payload
        if not isinstance(report, dict):
            raise ValueError("Release JSON 中没有 CGM 报告")
        metadata = payload.get("metadata")
        result = payload.get("result")
    elif path.suffix.lower() == ".parquet":
        frame, metadata = export_input(path, read_release_wide_all)
        if bin1_cpk_report_path is not None:
            payload = json.loads(Path(bin1_cpk_report_path).read_text(encoding="utf-8"))
            cpk = (payload.get("reports") or {}).get("cpk") if "reports" in payload else payload
            if not isinstance(cpk, dict):
                raise ValueError("提供的 JSON 中没有 Bin1 CPK 报告")
            selection.update(bin1_cpk_report=cpk, allow_cross_source_cpk=True)
        report = build_release_cgm_report(frame, metadata, catalog=export_catalog(rule_dir), **selection)
    else:
        raise ValueError("请提供 Release JSON 或 Release Parquet；STDF 请先通过已有入口导入")
    workbook = render_cgm_xlsx(report, metadata=metadata, result=result, device_id=device_id)
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / workbook["filename"]
    with output.open("xb") as stream:
        stream.write(workbook["content"])
    return {"path": str(output.resolve()), "filename": workbook["filename"],
            "sheet_rows": workbook["sheet_rows"], "warnings": report.get("warnings", []),
            "source": report.get("source"), "rules": report.get("rules")}
