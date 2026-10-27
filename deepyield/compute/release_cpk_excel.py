"""Fixed four-sheet CPK workbook; consume saved statistics and shared remarks."""
from __future__ import annotations

from datetime import date
from io import BytesIO
from deepyield.compute.release_export_workbook import WORKBOOK_OPTIONS, DiagnosticJSON
import json
from pathlib import Path
import re

import xlsxwriter

from deepyield.compute.release_remarks import diagnose_item
from deepyield.rules.conditions import as_float, band_matches, parse_band
from deepyield.compute.release_control import report_progress

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "release_cpk_excel.json"


def _layout():
    data = json.loads(_CONFIG.read_text(encoding="utf-8"))
    if data.get("version") != "1.0":
        raise ValueError("不支持的 CPK Excel 格式版本")
    return data


def _bands(report):
    bands = (report.get("rules") or {}).get("bands") or []
    if {b.get("rule_id") for b in bands} != {"CPK-001", "CPK-002", "CPK-003"} or len(bands) != 3:
        raise ValueError("CPK 报告缺少完整的已保存 CPK 区间规则，请重新计算")
    for band in bands:
        parse_band(band["condition"])
        if band.get("status") not in {"PASS", "FAIL", "RISK"}:
            raise ValueError("CPK 报告包含不支持的规则状态")
    return bands


def _band_status(value, bands):
    value = as_float(value)
    if value is None:
        return "UNAVAILABLE"
    matches = [band["status"] for band in bands if band_matches(band["condition"], value)]
    if len(matches) != 1:
        raise ValueError(f"CPK={value} 无唯一规则区间，拒绝导出")
    return matches[0]


def _display_column(row, module="cpk"):
    column = row.get("column") or ""
    source_id = (row.get(module) or {}).get("dataset_id")
    prefix = f"{source_id}:" if source_id else ""
    return column[len(prefix):] if prefix and column.startswith(prefix) else column


def _rows(report, metadata, result):
    items = [dict(item) for item in report["items"]]
    tests = {test["column"]: test for test in (metadata or {}).get("test_catalog", [])}
    display = {_display_column(row): row for row in (result or {}).get("rows", [])
               if row.get("cpk") and not row["cpk"].get("group_id")}
    for original in report.get("unexecuted_tests", []):
        item = dict(original)
        test, row = tests.get(item["column"], {}), display.get(item["column"], {})
        item.update(test_num=test.get("test_num", row.get("number")),
                    test_name=test.get("name", row.get("name")),
                    unit=test.get("unit", row.get("unit")),
                    effective_lsl=test.get("low_limit"), effective_usl=test.get("high_limit"),
                    group_id=f"unexecuted:{item['column']}", conditions={})
        if item["test_num"] is None or not item["test_name"]:
            raise ValueError("未执行测项缺少 Test Nr./Test Name；请使用完整页面 JSON、附 metadata.test_catalog 或改用 Release Parquet")
        items.append(item)
    return items


def _final_diagnostics(result, module="cpk"):
    diagnostics = {}
    for row in (result or {}).get("rows", []):
        cell = row.get(module)
        if cell and isinstance(cell.get("diagnosis"), dict):
            group_id = cell.get("group_id") or f"unexecuted:{_display_column(row, module)}"
            if group_id in diagnostics:
                raise ValueError(f"Release 结果包含重复 {module.upper()} 分组说明")
            diagnostics[group_id] = cell["diagnosis"]
    return diagnostics


def _filename_part(value, label):
    text = str(value).strip()
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", text).strip(" .")
    return safe if safe not in {".", ".."} else ""


def _identifier(items, metadata, key, explicit, label):
    if explicit is not None:
        return _filename_part(explicit, label)
    values = {str((item.get("conditions") or {}).get(key)).strip() for item in items
              if (item.get("conditions") or {}).get(key) is not None
              and str(item["conditions"][key]).strip()}
    mir = ((metadata or {}).get("stdf_metadata") or {}).get("mir") or {}
    raw_key = {"part_type": "PART_TYP", "test_temperature": "TST_TEMP", "test_code": "TEST_COD"}.get(key, key)
    recorded = mir.get(key, mir.get(raw_key))
    if not values and recorded is not None and str(recorded).strip():
        values.add(str(recorded).strip())
    # Descriptive metadata is optional. Never guess one device from mixed data.
    return _filename_part(next(iter(values)), label) if len(values) == 1 else ""


def _report_filename(day, *parts):
    return "_".join([f"{day:%Y%m%d}", *(str(part) for part in parts if part)])


def _values(item):
    test_num = as_float(item.get("test_num"))
    if test_num is not None and (not test_num.is_integer() or test_num < 0):
        raise ValueError("Test Nr. 必须是非负整数")
    nr = int(test_num) if test_num is not None else None
    name = str(item.get("test_name") or "")
    unit = str(item.get("unit") or "").strip()
    identifier = " ".join(part for part in (f"{nr:06d}" if nr is not None else "", name, unit) if part)
    return [identifier, nr, name, unit, *[
        as_float(item.get(key)) for key in ("n", "fail_count", "effective_lsl", "effective_usl",
                                            "mean", "std", "cpk_cp", "cpk")]]


def render_cpk_xlsx(report, *, metadata=None, result=None, device_id=None, test_temp=None,
                    report_date=None, output=None):
    """Return filename/bytes/counts without recomputing statistics or mutating the run."""
    if report.get("report_type") != "release_bin1_cpk":
        raise ValueError("需要 Release bin1 CPK 报告")
    layout, bands = _layout(), _bands(report)
    items = _rows(report, metadata, result)
    device = _identifier(items, metadata, "part_type", device_id, "DeviceID")
    if test_temp is None:
        # Prefer actual temperature; use the test stage only when no temperature is recorded.
        has_temperature = any((item.get("conditions") or {}).get("test_temperature") not in (None, "")
                              for item in items)
        mir = ((metadata or {}).get("stdf_metadata") or {}).get("mir") or {}
        key = "test_temperature" if has_temperature or mir.get("test_temperature") not in (None, "") else "test_code"
        temperature = _identifier(items, metadata, key, None, "TestTemp/测试阶段")
    else:
        temperature = _filename_part(test_temp, "TestTemp/测试阶段")
    filename = _report_filename(report_date or date.today(), device, temperature, "Bin1_CPK_Report.xlsx")
    saved = _final_diagnostics(result)
    rows = []
    for item in items:
        values = _values(item)
        state = _band_status(item.get("cpk"), bands)
        rows.append({"item": item, "values": values, "band": state,
                     "color": state if item.get("status") in {"PASS", "FAIL", "RISK"} else "UNAVAILABLE"})
    ordered = sorted(rows, key=lambda row: (
        row["values"][1] is None, row["values"][1] or 0, row["values"][2],
        row["item"].get("group_id") or row["item"].get("column") or ""))
    unit_rows = [row for row in ordered if row["values"][3]]
    threshold = layout["too_good"]["threshold"]
    groups = [
        ordered,
        sorted((row for row in unit_rows if row["band"] in {"FAIL", "RISK"}),
               key=lambda row: row["values"][11]),
        [row for row in unit_rows if row["values"][11] is not None and row["values"][11] > threshold],
        [row for row in ordered if not row["values"][3]],
    ]
    counts = {}
    buffer = output if output is not None else BytesIO()
    diagnostic_json = DiagnosticJSON()
    written, total_rows = 0, sum(map(len, groups))
    report_progress('准备 CPK Excel', 0)
    with xlsxwriter.Workbook(buffer, WORKBOOK_OPTIONS) as book:
        book.set_properties({"title": "Bin1 CPK Report", "comments": json.dumps({
            "source": report.get("source"), "selection": report.get("selection"),
            "rules": report.get("rules"), "remark_knowledge": (result or {}).get("remark_knowledge"),
            "excel_layout_version": layout["version"], "too_good": layout["too_good"]
        }, ensure_ascii=False, allow_nan=False)})
        header = book.add_format({"bold": True, "bg_color": "#DCEEF0", "text_wrap": True})
        text = book.add_format({"valign": "top"})
        numeric = book.add_format({"num_format": "0.0000", "valign": "top"})
        integer = book.add_format({"num_format": "0", "valign": "top"})
        wrap = book.add_format({"text_wrap": True, "valign": "top"})
        colors = {status: book.add_format({"bg_color": color, "num_format": "0.0000", "valign": "top"})
                  for status, color in layout["colors"].items()}
        for index, (name, group) in enumerate(zip(layout["sheets"], groups)):
            sheet = book.add_worksheet(name)
            headers = layout["headers"] + (["Remark"] if index else [])
            sheet.write_row(0, 0, headers, header)
            sheet.freeze_panes(1, 4)
            sheet.autofilter(0, 0, len(group), len(headers) - 1)
            sheet.set_row(0, 30)
            sheet.set_column(0, 0, 56)
            sheet.set_column(1, 1, 12)
            sheet.set_column(2, 2, 44)
            sheet.set_column(3, 3, 12)
            sheet.set_column(4, 11, 16)
            if index:
                sheet.set_column(12, 12, 85)
            for line, row in enumerate(group, 1):
                report_progress(f'写入 CPK Excel：{written + 1} / {total_rows}', 90 * written / max(1, total_rows))
                written += 1
                for column, value in enumerate(row["values"]):
                    style = (colors[row["color"]] if column == 11 else integer if column in {1, 4, 5}
                             else numeric if column >= 6 else text)
                    sheet.write(line, column, value, style)
                item = row["item"]
                if "provenance_json" not in row:
                    provenance = {key: item.get(key) for key in
                                  ("group_id", "column", "conditions", "status", "reason")}
                    provenance["cpk_numeric_band"] = row["band"]
                    provenance["numeric_screen_only"] = True
                    row["provenance_json"] = json.dumps(provenance, ensure_ascii=False)
                sheet.write_comment(line, 0, row["provenance_json"])
                if index == 2:
                    sheet.write_blank(line, 12, None, wrap)
                elif index:
                    diagnostic = saved.get(item.get("group_id")) or item.get("diagnosis") or diagnose_item(
                        "cpk", item, source=report.get("source"), selection={"module": "cpk"})
                    remark = diagnostic.get("remark", diagnostic.get("auto_remark", ""))
                    sheet.write_string(line, 12, remark or "", wrap)
                    sheet.write_comment(line, 12, diagnostic_json(diagnostic))
            counts[name] = len(group)
        report_progress('正在封装 CPK Excel', 95)
    report_progress('CPK Excel 已生成', 100)
    return {"filename": filename, "content": buffer.getvalue() if output is None else None, "sheet_rows": counts}
