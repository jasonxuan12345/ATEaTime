"""Chart orchestration for transient Release sessions; no persistence."""
from __future__ import annotations

import hashlib
import json
import re
import os
import sys
from time import perf_counter

import yaml

from app import release_check_service as release
from deepyield.compute.release_chart_samples import report_samples, sample_quality
from deepyield.compute.release_charts import build_chart_statistics, select_chart_samples
from deepyield.compute.release_numeric import cg_number
from deepyield.compute.release_chart_wafer import release_wafer_data
from deepyield.rules.conditions import as_float
from deepyield.compute.release_control import lock_wait, stage
from deepyield.configuration import configuration_file
from deepyield.compute.release_memory_cache import ByteCache

_chart_cache = ByteCache(int(os.environ.get('DEEPYIELD_RELEASE_CHART_CACHE_MB', '64')) * 1024**2)


def invalidate_chart_cache(dataset_id, run_id=None):
    _chart_cache.discard(lambda key: (dataset_id is None or key[0] == dataset_id) and (run_id is None or key[1] == run_id))


def _samples(result, module, item, source):
    owner = release.get_dataset(result['dataset_id'])
    key = (result['dataset_id'], result['id'], module, item['group_id'], source.metadata['source_sha256'])
    started = perf_counter()
    samples = _chart_cache.get(key)
    lock_wait('chart_samples', started)
    if samples is not None:
        return samples
    # Resolve outside the lock; a slow build must not serialize waiting charts.
    samples = report_samples(source.frame, item, source.metadata['source_sha256'])
    # Bound derived memory independently of retained reports. An unusually
    # large item is returned without retaining a second full population.
    if len(samples) <= 100000:
        # Conservative accounting: repeated scalar/string objects are charged
        # per occurrence, so shared objects can only make the true size smaller.
        size = sys.getsizeof(samples) + sum(sys.getsizeof(point) + sum(sys.getsizeof(v)
            for v in point.values()) for point in samples)
        _chart_cache.put(key, samples, size)
    return samples


def resolve_item(dataset_id: str, run_id: str, key: str, module: str):
    dataset = release.get_dataset(dataset_id)
    result, reports = release.get_run(dataset, run_id)
    row = next((r for r in result['rows'] if r['key'] == key), None)
    if row is None or module not in reports or not row.get(module):
        raise KeyError('当前模块没有匹配测项结果')
    cell = row[module]
    group_id = cell.get('group_id') or key
    item = next((i for i in reports[module]['items'] if i['group_id'] == group_id), None)
    if item is None:
        raise KeyError('当前测项没有可用执行记录')
    source = release.run_source(dataset, result, module)
    return result, reports[module], row, item, source


def chart_context(result: dict, report: dict, item: dict, source, source_id: str) -> dict:
    return {'dataset_id': source_id,
        'source_files': source.metadata.get('source_files', []),
        'run_id': result['id'], 'filename': source.filename,
        'source_sha256': source.metadata.get('source_sha256'), 'group_id': item['group_id'],
        'column': item['column'], 'record_type': item['record_type'],
        'result_index': item.get('result_index'), 'conditions': item['conditions'],
        'selection': report.get('selection'), 'rules_hash': result['rules_hash'],
        'sample_type': '测试记录', 'selection_policy': 'PRR 内每测项最后一次执行，沿用报告中参与计算的测试记录'}


def thresholds(report: dict, result: dict) -> dict:
    rules = report.get('rules', {}).get('rules', [])
    conditions = {r['rule_id']: r.get('condition') or {} for r in rules}
    # Every run retains its own catalog, including for a CPK-only run.
    from deepyield.rules.loader import load_analysis_rules
    catalog = load_analysis_rules()
    cg_pass = conditions.get('CGM-004') or catalog.rule('CGM-004').spec['condition']
    cg_fail = conditions.get('CGM-002') or catalog.rule('CGM-002').spec['condition']
    mmsa = catalog.document('MMSA-V1') or {}
    return {'cg_pass': as_float(cg_pass.get('threshold')), 'cg_fail': as_float(cg_fail.get('threshold')),
        'var_red': mmsa.get('parameters', {}).get('var_ratio', {}).get('red_ge', 2.5),
        'rules_changed': catalog.catalog_hash != result['rules_hash']}


def chart_data(dataset_id: str, run_id: str, key: str, module: str, bins=24, point_limit=5000, bin_width=None) -> dict:
    result, report, row, item, source = resolve_item(dataset_id, run_id, key, module)
    with stage('samples', '读取图表样本'):
        samples = _samples(result, module, item, source)
    limits = thresholds(report, result)
    if limits['rules_changed']:
        raise ValueError('规则配置已改变，请重新计算后查看图表')
    context = {**chart_context(result, report, item, source, row[module].get('dataset_id', dataset_id)), 'module': module}
    with stage('statistics', '计算图表统计'):
        statistics = build_chart_statistics(samples, item, bins=bins, point_limit=point_limit, bin_width=bin_width, thresholds=limits)
        quality = sample_quality(samples)
    with stage('wafer', '准备晶圆图'):
        wafer = release_wafer_data(samples, item, limit=point_limit)
    return {'context': context, 'row': row, **statistics, 'quality': quality, 'wafer': wafer,
            'sample_page': {'points': samples[:100], 'total': len(samples), 'offset': 0,
                            'limit': 100, 'context': context}}


def sample_page(dataset_id: str, run_id: str, key: str, module: str, selection: dict,
                offset=0, limit=100) -> dict:
    result, report, row, item, source = resolve_item(dataset_id, run_id, key, module)
    with stage('samples', '读取样本明细'):
        samples = _samples(result, module, item, source)
    filtered = any(value is not None for key, value in selection.items() if key != 'value_max_exclusive')
    selected = select_chart_samples(samples, selection) if filtered else samples
    return {'points': selected[offset:offset + limit], 'total': len(selected), 'offset': offset,
            'limit': limit, 'context': {**chart_context(result, report, item, source, row[module].get('dataset_id', dataset_id)), 'module': module}}


def _family(name: str, config: dict) -> tuple[str, str]:
    clean = re.sub(r'^P\d+_', '', name)
    for family in config['families']:
        if any(clean.startswith(prefix) for prefix in family['prefixes']):
            return family['name'], '前缀匹配：' + ', '.join(family['prefixes'])
    return '未分组', '没有匹配的显式前缀配置'


def matrix_data(dataset_id: str, run_id: str, offset=0, limit=100, group=None, scope_keys=None, sort='report') -> dict:
    dataset = release.get_dataset(dataset_id)
    result, reports = release.get_run(dataset, run_id)
    cgm_items = {i['group_id']: i for i in reports.get('cgm', {}).get('items', [])}
    config = yaml.safe_load(configuration_file('release_chart_families.yaml').read_text(encoding='utf-8'))
    limits = thresholds(reports.get('cgm', {}), result)
    if limits['rules_changed']:
        raise ValueError('规则配置已改变，请重新计算')
    rows, groups = [], {}
    # Selection may contain thousands of test/record IDs. Hash this shared
    # prefix once per family/condition, preserving the exact original JSON hash.
    selection_json = json.dumps(reports.get('cgm', {}).get('selection'), sort_keys=True, ensure_ascii=False)
    condition_json, prefix_hashes = {}, {}
    for row in result['rows']:
        if scope_keys is not None and row['key'] not in scope_keys:
            continue
        cell = row.get('cgm') or {}
        item = cgm_items.get(cell.get('group_id') or row['key'])
        family, basis = _family(row['name'], config)
        rule = (row.get('mmsa') or {}).get('rule_id') or cell.get('rule_id') or '无诊断规则'
        sites = item.get('sites', []) if item else []
        bad = [s['site'] for s in sites if cg_number(s.get('cg')) is not None and cg_number(s['cg']) < limits['cg_pass']]
        condition_id = id(row['conditions'])
        if condition_id not in condition_json:
            condition_json[condition_id] = json.dumps(row['conditions'], sort_keys=True, ensure_ascii=False)
        prefix_key = (family, rule, cell.get('dataset_id'), condition_json[condition_id])
        if prefix_key not in prefix_hashes:
            prefix = json.dumps([family, rule, cell.get('dataset_id')], ensure_ascii=False)[:-1]
            prefix += ', ' + condition_json[condition_id] + ', ' + selection_json + ', '
            prefix_hashes[prefix_key] = hashlib.sha256(prefix.encode())
        digest = prefix_hashes[prefix_key].copy()
        tail = [row['unit'], bad] + ([row['key']] if family == '未分组' else [])
        digest.update(json.dumps(tail, sort_keys=True, ensure_ascii=False)[1:].encode())
        group_key = digest.hexdigest()[:24]
        groups.setdefault(group_key, {'key': group_key, 'family': family, 'rule': rule,
            'basis': basis, 'bad_sites': bad, 'keys': [], 'count': 0})
        groups[group_key]['keys'].append(row['key'])
        groups[group_key]['count'] += 1
        rows.append({'key': row['key'], 'name': row['name'], 'number': row['number'], 'group': group_key,
            'family': family, 'rule': rule, 'sites': sites, 'unit': row['unit'],
            'status': row['status'], 'filename': cell.get('filename'),
            'cpk_override_applied': item.get('cpk_override_applied', False) if item else False,
            'source_sha256': result['sources'].get('cgm', {}).get('source_sha256')})
    filtered = [r for r in rows if group is None or r['group'] == group]
    if sort == 'cg':
        filtered.sort(key=lambda row: min((cg_number(s['cg']) for s in row['sites'] if cg_number(s.get('cg')) is not None), default=float('inf')))
    return {'rows': filtered[offset:offset + limit], 'groups': list(groups.values()),
            'total': len(filtered), 'offset': offset, 'limit': limit, 'thresholds': limits}
