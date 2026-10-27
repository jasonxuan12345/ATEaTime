"""Outlier diagnostics and bounded in-memory export snapshots."""
from copy import deepcopy
import json
from uuid import uuid4

import xlsxwriter

from app import release_check_service as release
from deepyield.compute.release_control import checkpoint, report_progress, timed
from deepyield.compute.release_export_workbook import WORKBOOK_OPTIONS, DiagnosticJSON
from deepyield.compute.release_outliers import build_release_outlier_report, _TRACE_COLUMNS, _trace, _buckets
from deepyield.compute.release_cpk import _group_events
from deepyield.compute.release_knowledge import load_knowledge
from deepyield.compute.release_run_cache import dataset_computation, invalidate_indexes
from deepyield.compute.release_sample_batch import invalidate_store_arrays
from deepyield.compute.release_remarks import diagnose_outlier


@dataset_computation
def run_outlier_check(dataset_id: str, options: dict) -> dict:
    dataset = release.get_dataset(dataset_id)
    report = build_release_outlier_report(dataset.frame, dataset.metadata, **options)
    catalog = load_knowledge()
    for baseline in report["baselines"]:
        baseline["diagnosis"] = diagnose_outlier(baseline, catalog=catalog,
            source=report["source"], selection={"report": "outlier"}, _share_provenance=True)
    report["remark_knowledge"] = {key: catalog[key] for key in ("version", "hash", "source")}
    checkpoint()
    report_id = uuid4().hex
    with release._lock:
        if release._datasets.get(dataset_id) is not dataset:
            invalidate_indexes(dataset.metadata.get('source_sha256'))
            invalidate_store_arrays([e.store for e in dataset.frame.get('__test_records', []) if hasattr(e, 'store')])
            raise KeyError('文件已不在当前会话，请重新导入')
        dataset.outlier_runs[report_id] = report
        while len(dataset.outlier_runs) > 3:
            dataset.outlier_runs.popitem(last=False)
    return {"dataset_id": dataset_id, "report_id": report_id, "report": report}


@timed('export.outlier.xlsx')
def export_outliers_xlsx(dataset_id: str, report_id: str, file_delivery=False):
    dataset = release.get_dataset(dataset_id)
    with release._lock:
        if report_id not in dataset.outlier_runs:
            raise KeyError("Outlier 结果已过期，请重新计算")
        original = dataset.outlier_runs[report_id]
        # Baselines and their diagnoses are immutable after the check completes.
        report = {'baselines': original['baselines']}
    output = dataset.report_files.build(('outliers', report_id, id(original)),
        lambda stream: _write_outliers(report, dataset.filename, stream), file_delivery=file_delivery)
    return output if file_delivery else output['content']


def _write_outliers(report, filename, output):
    diagnostic_json = DiagnosticJSON()
    with xlsxwriter.Workbook(output, WORKBOOK_OPTIONS) as book:
        sheet = book.add_worksheet("Outlier diagnostics")
        header = book.add_format({"bold": True, "bg_color": "#DCEEF0", "text_wrap": True})
        wrap = book.add_format({"text_wrap": True, "valign": "top"})
        headers = ["Test ID", "Test name", "Lot", "Wafer", "Head", "Site", "n",
                   "Candidates", "Status", "Remark", "Source", "Knowledge hash"]
        sheet.write_row(0, 0, headers, header)
        for line, item in enumerate(report["baselines"], 1):
            report_progress(f'写入 Outlier Excel：{line} / {len(report["baselines"])}', 90 * (line - 1) / max(1, len(report["baselines"])))
            diagnosis = item["diagnosis"]
            sheet.write_row(line, 0, [item["test_num"], item["test_name"], item["lot"], item["wafer"],
                item["head"], item["site"], item["n"], item["candidate_count"], item["status"],
                diagnosis["remark"], filename, diagnosis["knowledge_hash"]], wrap)
            sheet.write_comment(line, 9, diagnostic_json(diagnosis))
        sheet.freeze_panes(1, 2)
        sheet.autofilter(0, 0, len(report["baselines"]), len(headers)-1)
        sheet.set_column(0, 8, 16)
        sheet.set_column(1, 1, 36)
        sheet.set_column(9, 9, 85)
        sheet.set_column(10, 11, 36)
        report_progress('正在封装 Outlier Excel', 95)
    report_progress('Outlier Excel 已生成', 100)
    return {'filename': 'release-outlier-diagnostics.xlsx'}


def outlier_view(dataset_id: str, report_id: str, group_id: str) -> dict:
    """Read real die positions and the exact selected test population from memory."""
    dataset = release.get_dataset(dataset_id)
    with release._lock:
        if report_id not in dataset.outlier_runs:
            raise KeyError('Outlier 结果已过期，请重新计算')
        report = dataset.outlier_runs[report_id]
    baselines = [b for b in report['baselines'] if b['group_id'] == group_id]
    if not baselines:
        raise KeyError('当前 Outlier 报告中没有这个测项分组')
    selected = dataset.frame.loc[dataset.frame['__part_index'].isin(report['selection']['selected_part_indices'])]
    traces = {int(row['__part_index']): _trace(row) for row in selected[
        [column for column in _TRACE_COLUMNS if column in selected.columns]].to_dict('records')}
    unique_dies = {}
    for trace in traces.values():
        if trace['wafer'] is not None and trace['x'] is not None and trace['y'] is not None:
            key = (trace['lot'], trace['wafer'], trace['x'], trace['y'])
            unique_dies[key] = {name: trace[name] for name in ('lot', 'wafer', 'x', 'y')}
    group = _group_events(selected, report['source'], {baselines[0]['column']}).get(group_id)
    points = []
    if group:
        by_channel = {(b['lot'], b['wafer'], b['head'], b['site']): b for b in baselines}
        for key, bucket in _buckets(group, traces).items():
            baseline = by_channel.get(key)
            if baseline is None:
                continue
            for trace, event, value in bucket['rows']:
                points.append({**trace, 'record_sequence': int(event['record_sequence']),
                    'value': value, 'baseline_id': baseline['baseline_id']})
    points.sort(key=lambda point: (point['part_index'], point['record_sequence']))
    total = len(points)
    if total > 20000:
        # Candidate points remain complete in the report and are drawn on top.
        points = [points[index * (total - 1) // 19999] for index in range(20000)]
    return {'dataset_id': dataset_id, 'report_id': report_id, 'group_id': group_id,
            'dies': list(unique_dies.values()), 'points': points,
            'total_points': total, 'sampled': total > len(points)}
