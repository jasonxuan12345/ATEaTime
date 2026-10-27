"""Fixed Cgm workbook from saved selected statistics; no measurement rescanning."""
from __future__ import annotations

from datetime import date
from io import BytesIO
from deepyield.compute.release_export_workbook import WORKBOOK_OPTIONS, DiagnosticJSON
import json
import math
from pathlib import Path

import xlsxwriter
from xlsxwriter.utility import xl_col_to_name

from deepyield.compute.release_cpk_excel import _identifier, _report_filename, _final_diagnostics
from deepyield.compute.release_control import report_progress
from deepyield.compute.release_numeric import cg_number, require_current_cg_policy
from deepyield.compute.release_remarks import diagnose_item
from deepyield.rules.conditions import as_float

_CONFIG = Path(__file__).resolve().parents[2] / "config" / "release_cgm_excel.json"


def _items(report, metadata):
    catalog = report.get("test_catalog") or (metadata or {}).get("test_catalog") or []
    order = {test["column"]: index for index, test in enumerate(catalog)}
    tests = {test["column"]: test for test in catalog}
    items = list(report.get("items") or [])
    for missing in report.get("unexecuted_tests") or []:
        test = tests.get(missing["column"], {})
        items.append({**missing, "test_num": test.get("test_num"), "test_name": test.get("name"),
                      "unit": test.get("unit"), "effective_lsl": test.get("low_limit"),
                      "effective_usl": test.get("high_limit"), "conditions": {}, "sites": []})
    # Stable sorting keeps separate specification/condition variants intact.
    return sorted(items, key=lambda item: order.get(item["column"], len(order)))


def _cg_value(value):
    """Keep explicit non-finite Cg values distinct from unavailable statistics."""
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if math.isnan(number):
        return "NaN"
    if number == math.inf:
        return "Infinity"
    return number if math.isfinite(number) else None


def _site_values(item, site):
    n = as_float(site.get("n"))
    if n == 0:
        return [None] * 7
    if n is not None and (n < 0 or not n.is_integer()):
        raise ValueError("Site ExecCount 必须是非负整数")
    mean, std, smallest, largest = [as_float(site.get(key)) for key in ("mean", "std", "min", "max")]
    if std is not None and std < 0:
        raise ValueError("已保存的 Site 统计无效，请重新计算")
    fail = as_float(site.get("fail_count"))
    if fail is not None and (not fail.is_integer() or fail < 0 or (n is not None and fail > n)):
        raise ValueError("Site FailCount 必须是 0 到 ExecCount 之间的整数")
    low, high = [as_float(item.get(key)) for key in ("effective_lsl", "effective_usl")]
    cg = _cg_value(site.get("cg"))
    if cg != "NaN" and std == 0 and low is not None and high is not None:
        cg = "NaN" if high == low else "Infinity"
    return [mean, std, smallest, largest, cg, int(n) if n is not None else None, int(fail) if fail is not None else None]


def _cg_state(value, thresholds):
    if value == "Infinity":
        return "Pass"
    numeric = as_float(value)
    if numeric is None:
        return None
    return ("Pass" if numeric >= thresholds["cg_pass"] else
            "Need to check" if numeric >= thresholds["cg_check"] else "Fail")


def _summary(cgs, cpk, thresholds):
    values = [_cg_value(value) for value in cgs]
    valid = [value for value in values if cg_number(value) is not None]
    minimum = "NaN" if "NaN" in values else min(valid, key=cg_number) if valid else None
    state = _cg_state(minimum, thresholds)
    if state == "Pass" or (state is not None and cpk is not None and cpk >= thresholds["cpk_accept"]):
        result = "Pass"
    else:
        result = state or "Need to check"
    return minimum, state, result


def _cpk_state(value, thresholds):
    numeric = as_float(value)
    if numeric is None:
        return None
    return ("Pass" if numeric >= thresholds["cpk_accept"] else
            "Need to check" if numeric >= thresholds["cpk_check"] else "Fail")


def _diagnostic(item, report, saved, thresholds):
    group_id = item.get("group_id") or f"unexecuted:{item.get('column', '')}"
    return saved.get(group_id) or item.get("diagnosis") or diagnose_item(
        "cgm", {**item, "_cg_pass_threshold": thresholds["cg_pass"]},
        source=report.get("source"), selection={"module": "cgm", **(report.get("selection") or {})})


def _conditional_formats(sheet, site_count, rows, formats, thresholds):
    if not rows:
        return
    summary = 5 + 7 * site_count
    cg_columns = [9 + 7 * index for index in range(site_count)] + [summary]
    for column in cg_columns:
        cell = f"{xl_col_to_name(column)}4"
        tests = {
            "Pass": f'OR({cell}="Infinity",AND(ISNUMBER({cell}),{cell}>={thresholds["cg_pass"]}))',
            "Need to check": f'AND(ISNUMBER({cell}),{cell}>={thresholds["cg_check"]},{cell}<{thresholds["cg_pass"]})',
            "Fail": f'AND(ISNUMBER({cell}),{cell}<{thresholds["cg_check"]})',
        }
        for state, formula in tests.items():
            sheet.conditional_format(3, column, rows + 2, column,
                {"type": "formula", "criteria": "=" + formula, "format": formats[state]})
    cell = f"{xl_col_to_name(summary + 1)}4"
    tests = {
        "Pass": f'AND(ISNUMBER({cell}),{cell}>={thresholds["cpk_accept"]})',
        "Need to check": f'AND(ISNUMBER({cell}),{cell}>={thresholds["cpk_check"]},{cell}<{thresholds["cpk_accept"]})',
        "Fail": f'AND(ISNUMBER({cell}),{cell}<{thresholds["cpk_check"]})',
    }
    for state, formula in tests.items():
        sheet.conditional_format(3, summary + 1, rows + 2, summary + 1,
            {"type": "formula", "criteria": "=" + formula, "format": formats[state]})
    result_column = xl_col_to_name(summary + 2)
    for state in formats:
        sheet.conditional_format(3, summary + 2, rows + 2, summary + 2,
            {"type": "formula", "criteria": f'=${result_column}4="{state}"', "format": formats[state]})


def render_cgm_xlsx(report, *, metadata=None, result=None, device_id=None, report_date=None, output=None):
    """Return the exact single-sheet report without changing analysis decisions."""
    if report.get("report_type") != "release_cgm":
        raise ValueError("需要 Release CGM 报告")
    require_current_cg_policy(report)
    layout = json.loads(_CONFIG.read_text(encoding="utf-8"))
    if layout.get("version") != "1.0":
        raise ValueError("不支持的 Cgm Excel 格式版本")
    items = _items(report, metadata)
    device = _identifier(items, metadata, "part_type", device_id, "DeviceID")
    raw_sites = set(report.get("sites") or [])
    raw_sites.update(site["site"] for item in items for site in item.get("sites", []) if site.get("site") is not None)
    if any(isinstance(site, bool) or as_float(site) is None or not as_float(site).is_integer()
           or not 0 <= as_float(site) <= 255 for site in raw_sites):
        raise ValueError("SITE_NUM 必须是 0 到 255 之间的整数")
    sites = sorted(int(site) for site in raw_sites)
    if 5 + 7 * len(sites) + 4 > 16384 or len(items) + 3 > 1048576:
        raise ValueError("Cgm 报告超出 Excel 行列限制")
    thresholds = layout["thresholds"]
    saved = _final_diagnostics(result, "cgm")
    report_progress('准备 CGM Excel', 0)
    buffer = output if output is not None else BytesIO()
    diagnostic_json = DiagnosticJSON()
    with xlsxwriter.Workbook(buffer, WORKBOOK_OPTIONS) as book:
        book.set_properties({"title": "Cgm Report", "comments": json.dumps({
            "source": report.get("source"), "selection": report.get("selection"),
            "bin1_cpk_source": report.get("bin1_cpk_source"),
            "bin1_cpk_selection": report.get("bin1_cpk_selection"),
            "remark_knowledge": (result or {}).get("remark_knowledge"),
            "excel_layout": layout}, ensure_ascii=False, allow_nan=False)})
        sheet = book.add_worksheet(layout["sheet"])
        header = book.add_format({"bold": True, "bg_color": "#DCEEF0", "text_wrap": True})
        text = book.add_format({"valign": "top"})
        numeric = book.add_format({"num_format": "General", "valign": "top"})
        integer = book.add_format({"num_format": "0", "valign": "top"})
        wrap = book.add_format({"text_wrap": True, "valign": "top"})
        colors = {state: book.add_format({"bg_color": color, "num_format": "General"})
                  for state, color in layout["colors"].items()}
        for index, site in enumerate(sites):
            start = 5 + index * 7
            sheet.write_string(0, start, "Site", header)
        for index, site in enumerate(sites):
            sheet.write_number(1, 5 + index * 7, site, header)
        sheet.write_row(2, 0, layout["headers"], header)
        for index, site in enumerate(sites):
            sheet.write_row(2, 5 + index * 7, layout["site_headers"], header)
        summary = 5 + 7 * len(sites)
        sheet.write_row(2, summary, layout["summary_headers"], header)
        sheet.freeze_panes(3, 5)
        sheet.autofilter(2, 0, len(items) + 2, summary + 3)
        sheet.set_column(0, 0, 12)
        sheet.set_column(1, 1, 48)
        sheet.set_column(2, 2, 14)
        sheet.set_column(3, summary + 1, 20)
        sheet.set_column(summary + 2, summary + 2, 20)
        sheet.set_column(summary + 3, summary + 3, 85)
        sheet.set_row(2, 32)
        for line, item in enumerate(items, 3):
            report_progress(f'写入 CGM Excel：{line - 2} / {len(items)}', 90 * (line - 3) / max(1, len(items)))
            nr = as_float(item.get("test_num"))
            if nr is not None and (nr < 0 or not nr.is_integer()):
                raise ValueError("Test Nr. 必须是非负整数")
            sheet.write(line, 0, int(nr) if nr is not None else None, integer)
            sheet.write(line, 1, str(item['test_name']) if item.get('test_name') else None, text)
            unit = str(item.get("unit") or "").strip()
            sheet.write(line, 2, unit or None, text)
            sheet.write(line, 3, as_float(item.get("effective_lsl")), numeric)
            sheet.write(line, 4, as_float(item.get("effective_usl")), numeric)
            by_site = {site["site"]: site for site in item.get("sites", []) if site.get("site") is not None}
            if len(by_site) != sum(site.get("site") is not None for site in item.get("sites", [])):
                raise ValueError("测项包含重复 Site 统计")
            cgs = []
            for index, site in enumerate(sites):
                values = _site_values(item, by_site[site]) if site in by_site else [None] * 7
                cgs.append(values[4])
                for offset, value in enumerate(values):
                    state = _cg_state(value, thresholds) if offset == 4 else None
                    style = colors[state] if state else integer if offset in {5, 6} else numeric
                    sheet.write(line, 5 + index * 7 + offset, value, style)
            cpk = as_float(item.get("supported_by_bin1_cpk"))
            minimum, state, result = _summary(cgs, cpk, thresholds)
            sheet.write(line, summary, minimum, colors[state] if state else numeric)
            cpk_state = _cpk_state(cpk, thresholds)
            sheet.write(line, summary + 1, cpk, colors[cpk_state] if cpk_state else numeric)
            sheet.write_string(line, summary + 2, result, colors[result])
            diagnostic = _diagnostic(item, report, saved, thresholds)
            remark = diagnostic.get("remark", diagnostic.get("auto_remark", ""))
            sheet.write_string(line, summary + 3, remark or "", wrap)
            sheet.write_comment(line, summary + 3, diagnostic_json(diagnostic))
            sheet.write_comment(line, 0, json.dumps({key: item.get(key) for key in
                ("group_id", "column", "conditions", "status", "reason")}, ensure_ascii=False))
        _conditional_formats(sheet, len(sites), len(items), colors, thresholds)
        report_progress('正在封装 CGM Excel', 95)
    report_progress('CGM Excel 已生成', 100)
    filename = _report_filename(report_date or date.today(), device, 'Cgm_Report.xlsx')
    return {"filename": filename, "content": buffer.getvalue() if output is None else None, "sheet_rows": {"raw": len(items)}}
