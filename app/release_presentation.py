"""Small UI snapshots; full evidence stays on the saved, immutable report.

Global lists retain all rows and sparse hits for exact filtering/sorting/matrix
counts. Diagnostic provenance is loaded only when expanded. Legacy API callers
and exported reports retain their complete schemas.
"""
import json
from time import perf_counter

from fastapi.responses import Response

from app import release_check_service as release
from deepyield.compute.release_control import checkpoint, measure, count, request_trace
from deepyield.compute.release_performance_log import emit


def diagnosis_summary(diagnosis, detail_url):
    if not diagnosis:
        return diagnosis
    return {key: diagnosis.get(key) for key in ('remark', 'auto_remark', 'current_remark',
        'category', 'knowledge_version', 'knowledge_hash', 'review_state')} | {
            'knowledge': {'status': (diagnosis.get('knowledge') or {}).get('status', 'none')},
            'related_evidence': bool(diagnosis.get('related_evidence')), 'detail_url': detail_url}


def run_view(result):
    root = f"/datasets/{result['dataset_id']}/runs/{result['id']}"
    output = {key: value for key, value in result.items() if key not in
              {'rows', 'chart_observations', 'observation_history'}}
    output['rows'] = []
    for number, row in enumerate(result['rows']):
        if number % 256 == 0:
            checkpoint()
        copy = dict(row)
        for module in result['modules']:
            if row.get(module):
                copy[module] = dict(row[module])
                copy[module]['diagnosis'] = diagnosis_summary(row[module].get('diagnosis'),
                    f"{root}/items/{row['key']}/diagnosis/{module}")
        output['rows'].append(copy)
    return output


def outlier_view(result):
    output = dict(result)
    report = dict(result['report'])
    root = f"/datasets/{result['dataset_id']}/outliers/{result['report_id']}"
    report['detail_level'] = 'summary'
    report['baselines'] = [{**baseline, 'diagnosis': diagnosis_summary(baseline.get('diagnosis'),
        f"{root}/baselines/{baseline['baseline_id']}/diagnosis")} for baseline in report['baselines']]
    positions = {b['baseline_id']: n for n, b in enumerate(report['baselines'])}
    # A hit's identity, test text and limits already exist on its reference row.
    report['chips'] = [{**chip, 'hits': [[positions[h['baseline_id']], h['record_sequence'],
        h['value'], h['score'], h['direction']] for h in chip['hits']]} for chip in report['chips']]
    output.update(report=report, wire_format='release-ui-v1')
    return output


def response(value, headers):
    started = perf_counter()
    with measure('response.serialize'):
        body = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    count('response.bytes', len(body))
    emit('ui_response', request_id=(request_trace.get() or {}).get('id'),
         elapsed_ms=round((perf_counter() - started) * 1000, 3), response_bytes=len(body))
    return Response(body, media_type='application/json', headers={'Cache-Control': 'no-store',
        **{key: value for key, value in headers.items() if key.lower() == 'server-timing'}})


def run_diagnosis(dataset_id, run_id, key, module):
    result, _ = release.get_run(release.get_dataset(dataset_id), run_id)
    with release._lock:
        row = next((r for r in result['rows'] if r['key'] == key), None)
        if row is None or not row.get(module) or not row[module].get('diagnosis'):
            raise KeyError('当前结果中没有这个测项诊断')
        return dict(row[module]['diagnosis'])


def outlier_report(dataset_id, report_id):
    dataset = release.get_dataset(dataset_id)
    with release._lock:
        report = dataset.outlier_runs.get(report_id)
        if report is None:
            # A bound run retains its exact snapshot beyond the source's
            # three-entry recent list, just like Excel/PDF export does.
            report = next((link['report'] for owner in release._datasets.values()
                for saved in owner.outlier_report_exports.values() for link in saved.get('sources', [])
                if link['dataset_id'] == dataset_id and link['report_id'] == report_id), None)
        if report is None:
            raise KeyError('Outlier 结果已过期，请重新计算')
        return report


def baseline_diagnosis(dataset_id, report_id, baseline_id):
    report = outlier_report(dataset_id, report_id)
    baseline = next((b for b in report['baselines'] if b['baseline_id'] == baseline_id), None)
    if baseline is None:
        raise KeyError('当前结果中没有这个参考组')
    return baseline.get('diagnosis') or {}


def candidate_page(dataset_id, report_id, offset, limit, group_id=None):
    report = outlier_report(dataset_id, report_id)
    chips = report['chips']
    if group_id is not None:
        chips = [chip for chip in chips if any(h['group_id'] == group_id for h in chip['hits'])]
    return {'total': len(chips), 'offset': offset, 'limit': limit,
            'summary': report['summary'], 'rows': [{k: v for k, v in c.items() if k != 'hits'}
                for c in chips[offset:offset + limit]]}


def hit_page(dataset_id, report_id, part_index, offset, limit):
    report = outlier_report(dataset_id, report_id)
    chip = next((c for c in report['chips'] if c['part_index'] == part_index), None)
    if chip is None:
        raise KeyError('当前结果中没有这个异常候选记录')
    return {'total': len(chip['hits']), 'offset': offset, 'limit': limit,
            'chip': {k: v for k, v in chip.items() if k != 'hits'}, 'rows': chip['hits'][offset:offset + limit]}
