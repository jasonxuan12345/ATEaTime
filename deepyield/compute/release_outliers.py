"""Single imported Release frame: within-site outliers and exact execution trace."""
from collections import Counter
import hashlib
import json

import pandas as pd

from deepyield.compute.release_cpk import _flag, _group_events, _ids
from deepyield.compute.release_control import checkpoint, count, measure, stage, timed
from deepyield.compute.release_outlier_stats import robust_outlier_statistics, robust_outlier_statistics_many
from deepyield.compute.release_outlier_batch import batch_buckets
from deepyield.compute.release_sample_batch import prepared_groups
from deepyield.compute.release_run_cache import computation
from deepyield.compute.release_stdf import COMPUTE_SCHEMAS
from deepyield.io.stdf_parquet_writer import _classify_part_flag
from deepyield.rules.conditions import as_float

DEFAULT_SIGMA_MULTIPLIER = 6.0
DEFAULT_MINIMUM_SAMPLES = 30
_TRACE_COLUMNS = ('__part_index', '__source_record_sequence', 'HEAD_NUM', 'SITE',
                  'STDF_PART_ID', 'LOT_ID', 'WAFER_ID', 'DIE_X', 'DIE_Y')


def _integer(value):
    number = as_float(value)
    return int(number) if number is not None and number.is_integer() else None


def _text(value):
    return None if value is None or pd.isna(value) else str(value).strip() or None


def _select(frame, metadata, part_indices, sites, test_columns):
    if not isinstance(frame, pd.DataFrame) or metadata.get('schema_version') not in COMPUTE_SCHEMAS:
        raise ValueError('需要 Release 导入后的内存数据及内置元数据')
    required = {'__part_index', 'HEAD_NUM', 'SITE', 'SBIN', 'PART_FLAG',
                '__conditions_json', '__test_records'}
    if not required.issubset(frame.columns):
        raise ValueError(f'Release 数据缺少字段：{sorted(required - set(frame.columns))}')
    indices = frame['__part_index']
    if any(isinstance(value, bool) or _integer(value) is None or _integer(value) < 0 for value in indices):
        raise ValueError('测试记录编号必须是非负整数')
    if indices.duplicated().any():
        raise ValueError('测试记录编号重复，无法定位芯片')
    parts, chosen_sites = _ids(part_indices, 'part_indices'), _ids(sites, 'sites')
    if parts is not None and not parts.issubset(set(indices.astype(int))):
        raise ValueError('所选测试记录不存在')
    known_sites = {_flag(site) for site in frame['SITE']} - {None}
    if chosen_sites is not None and not chosen_sites.issubset(known_sites):
        raise ValueError('所选 site 不存在')
    catalog = metadata.get('test_catalog')
    if not isinstance(catalog, list) or any(not isinstance(t, dict) or 'column' not in t for t in catalog):
        raise ValueError('Release 测项目录缺失或无效')
    if test_columns is not None and (not isinstance(test_columns, (list, tuple)) or
                                    any(not isinstance(column, str) for column in test_columns)):
        raise ValueError('test_columns 必须是测项列名列表')
    known_tests = {str(test['column']) for test in catalog}
    chosen = None if test_columns is None else set(test_columns)
    if chosen is not None and not chosen.issubset(known_tests):
        raise ValueError('所选测项不存在')
    selected = frame
    if parts is not None:
        selected = selected.loc[selected['__part_index'].isin(parts)]
    if chosen_sites is not None:
        selected = selected.loc[selected['SITE'].isin(chosen_sites)]
    selection = dict(part_indices=None if parts is None else sorted(parts),
        sites=None if chosen_sites is None else sorted(chosen_sites),
        test_columns=None if chosen is None else sorted(chosen),
        selected_part_indices=[int(index) for index in selected['__part_index']],
        selected_record_count=len(selected))
    return selected, chosen, known_tests, selection


def _trace(row):
    return dict(part_index=int(row['__part_index']),
        prr_record_sequence=_integer(row.get('__source_record_sequence')),
        part_id=_text(row.get('STDF_PART_ID')), lot=_text(row.get('LOT_ID')),
        wafer=_text(row.get('WAFER_ID')), x=_integer(row.get('DIE_X')),
        y=_integer(row.get('DIE_Y')), head=_flag(row.get('HEAD_NUM')), site=_flag(row.get('SITE')))


def _excluded_reason(part, event, group, trace):
    if trace['site'] is None or trace['head'] is None:
        return 'MISSING_OR_INVALID_CHANNEL'
    if group['record_type'] not in {'PTR', 'MPR'}:
        return 'NON_PARAMETRIC_TEST'
    if as_float(part.get('SBIN')) != 1:
        return 'NOT_BIN1'
    flag = _flag(part.get('PART_FLAG'))
    if flag is None or _classify_part_flag(flag)[0] != 'PASS':
        return 'INVALID_OR_CONFLICTING_PART_RESULT'
    tf, pf = _flag(event.get('test_flag')), _flag(event.get('param_flag'))
    value = as_float(event.get('result'))
    if (tf is None or pf is None or tf & 0x3F or pf & 0x27 or
            event.get('result_valid') is not True or value is None):
        return 'INVALID_TEST_RESULT_OR_ALTERNATE_LIMITS'
    if not tf & 0x40 and tf & 0x80:
        return 'EXPLICIT_FAILED_TEST'
    low, high = as_float(group['spec']['low_limit']), as_float(group['spec']['high_limit'])
    if low is not None and high is not None and low >= high:
        return 'INVALID_LIMIT_ORDER'
    if (low is not None and value < low) or (high is not None and value > high):
        return 'OUTSIDE_SPECIFICATION'
    return None


def _buckets(group, traces, prepared=None):
    compact = batch_buckets(group, traces, _flag, {} if prepared is None else prepared)
    if compact is not None:
        return compact
    buckets = {}
    for index, (part, event) in enumerate(group['events']):
        if index % 4096 == 0:
            checkpoint()
        trace = traces[int(part['__part_index'])]
        key = (trace['lot'], trace['wafer'], trace['head'], trace['site'])
        if key not in buckets:
            buckets[key] = dict(rows=[], excluded=Counter(), executions=0)
        bucket = buckets[key]
        bucket['executions'] += 1
        reason = _excluded_reason(part, event, group, trace)
        if reason:
            bucket['excluded'][reason] += 1
        else:
            bucket['rows'].append((trace, event, as_float(event['result'])))
    return buckets


def _baseline(group, key, bucket, sigma_multiplier, minimum_samples, computed=None):
    with measure('outliers.statistics'):
        stats, hits = computed if computed is not None else robust_outlier_statistics(
            (bucket['rows'].values if hasattr(bucket['rows'], 'values') else
             [row[2] for row in bucket['rows']]), sigma_multiplier, minimum_samples)
    if group['record_type'] not in {'PTR', 'MPR'}:
        stats.update(status='NOT_APPLICABLE', reason='NON_PARAMETRIC_TEST')
    elif key[2] is None or key[3] is None:
        stats.update(status='NOT_CALCULABLE', reason='MISSING_OR_INVALID_CHANNEL')
    elif bucket['excluded'].get('INVALID_LIMIT_ORDER'):
        stats.update(status='NOT_CALCULABLE', reason='INVALID_LIMIT_ORDER')
    identity = [group['group_id'], *key]
    baseline_id = hashlib.sha256(json.dumps(identity, ensure_ascii=False).encode()).hexdigest()[:24]
    baseline = {name: group[name] for name in ('group_id', 'column', 'test_num', 'test_name',
                    'record_type', 'result_index', 'conditions')}
    baseline.update(baseline_id=baseline_id, lot=key[0], wafer=key[1], head=key[2], site=key[3],
        unit=group['spec']['unit'], lsl=as_float(group['spec']['low_limit']),
        usl=as_float(group['spec']['high_limit']), selected_execution_count=bucket['executions'],
        excluded_counts=dict(bucket['excluded']), **stats)
    return baseline, hits


def _collect_hits(chips, baseline, rows, hits):
    for position, (index, score) in enumerate(hits):
        if position % 4096 == 0:
            checkpoint()
        trace, event, value = rows[index]
        if trace['part_index'] not in chips:
            chips[trace['part_index']] = dict(trace, status='OUTLIER_CANDIDATE',
                hit_count=0, max_score=0., hits=[])
        chip = chips[trace['part_index']]
        hit = {name: baseline[name] for name in ('baseline_id', 'group_id', 'column', 'test_num',
            'test_name', 'record_type', 'result_index', 'unit', 'median', 'robust_sigma',
            'lower_limit', 'upper_limit')}
        hit.update(record_sequence=int(event['record_sequence']), value=value, score=score,
                   direction='HIGH' if value > baseline['median'] else 'LOW')
        chip['hits'].append(hit)
        chip['hit_count'] += 1
        chip['max_score'] = max(chip['max_score'], score)


@computation
@timed('outliers.total')
def build_release_outlier_report(frame, metadata, *, part_indices=None, sites=None,
        test_columns=None, sigma_multiplier=DEFAULT_SIGMA_MULTIPLIER,
        minimum_samples_per_site=DEFAULT_MINIMUM_SAMPLES):
    """Flag within-site statistical candidates; never change bins or release decisions."""
    sigma_multiplier = as_float(sigma_multiplier)
    if sigma_multiplier is None or sigma_multiplier <= 0:
        raise ValueError('sigma_multiplier 必须是有限正数')
    if (isinstance(minimum_samples_per_site, bool) or not isinstance(minimum_samples_per_site, int)
            or minimum_samples_per_site < 4):
        raise ValueError('minimum_samples_per_site 必须是至少4的整数')
    with stage('outlier_selection', '准备 Outlier 选样'):
        selected, chosen, known_tests, selection = _select(frame, metadata, part_indices, sites, test_columns)
        columns = [column for column in _TRACE_COLUMNS if column in selected.columns]
        traces = {int(row['__part_index']): _trace(row) for row in selected[columns].to_dict('records')}
    source = dict(source_file=metadata.get('source_file'), source_sha256=metadata.get('source_sha256'))
    with stage('outlier_groups', '准备 Outlier 测项分组'):
        groups = _group_events(selected, source, chosen)
    baselines, chips, prepared = [], {}, {}
    with stage('outlier_statistics', '计算各 site Outlier'):
        with prepared_groups(sorted(groups.items())) as entries:
            for group_id, group in entries:
                checkpoint()
                with measure('outliers.samples'):
                    buckets = _buckets(group, traces, prepared)
                with measure('outliers.statistics_batch'):
                    statistics = robust_outlier_statistics_many([
                        b['rows'].values if hasattr(b['rows'], 'values') else [r[2] for r in b['rows']]
                        for b in buckets.values()], sigma_multiplier, minimum_samples_per_site)
                for (key, bucket), computed in zip(buckets.items(), statistics):
                    checkpoint()
                    baseline, hits = _baseline(group, key, bucket, sigma_multiplier, minimum_samples_per_site, computed)
                    baselines.append(baseline)
                    with measure('outliers.collect'):
                        _collect_hits(chips, baseline, bucket['rows'], hits)
    for chip in chips.values():
        chip['hits'].sort(key=lambda hit: (-hit['score'], hit['group_id'], hit['record_sequence']))
    baselines.sort(key=lambda baseline: baseline['baseline_id'])
    candidate_chips = sorted(chips.values(), key=lambda chip: (-chip['max_score'], chip['part_index']))
    evaluated = sum(baseline['status'] in {'NO_OUTLIER_FOUND', 'OUTLIER_CANDIDATES'} for baseline in baselines)
    exclusions = Counter()
    for baseline in baselines:
        exclusions.update(baseline['excluded_counts'])
    requested = known_tests if chosen is None else chosen
    unexecuted = sorted(requested - {group['column'] for group in groups.values()})
    count('outliers.baselines', len(baselines))
    count('outliers.candidate_records', len(candidate_chips))
    warnings = ['仅筛选异常候选记录，不证明芯片缺陷或修改放行结果。',
                '使用当前选择的有效 Bin1、规格内记录作为参考样本；偏态、多峰和离散测项需另行确认适用性。',
                '各 site 独立筛选；site 整体偏移由 Site Diff 检查，不通过本报告自动判定。']
    if evaluated < len(baselines):
        warnings.append('部分组的参考记录数不足或无法计算筛选范围；没有异常候选记录不代表所有芯片通过。')
    return dict(report_type='release_outliers', source=source, selection=selection,
        algorithm=dict(name='per_site_robust_iqr', version='1', center='median',
            scale='(Q3-Q1)/1.35', percentile_method='linear', comparison='score > sigma_multiplier',
            sigma_multiplier=sigma_multiplier, minimum_samples_per_site=minimum_samples_per_site),
        policy=dict(population='selected_valid_bin1_pass_within_specification',
            test_execution_policy='last_per_test_in_selected_record_no_invalid_fallback',
            reference_groups=['test_identity', 'conditions', 'specification', 'lot', 'wafer', 'head', 'site'],
            identity_scope='source_and_part_index', retest_policy='keep_separate_execution_records',
            site_shift_assessment='USE_RELEASE_SITE_DIFF', distribution_shape_check='NOT_IMPLEMENTED'),
        status='EMPTY' if not baselines else 'COMPLETE', baselines=baselines, chips=candidate_chips,
        unexecuted_test_columns=unexecuted,
        summary=dict(selected_record_count=len(selected), baseline_count=len(baselines),
            evaluated_baseline_count=evaluated, unevaluated_baseline_count=len(baselines) - evaluated,
            candidate_part_count=len(candidate_chips), candidate_hit_count=sum(chip['hit_count'] for chip in candidate_chips),
            status_counts=dict(Counter(baseline['status'] for baseline in baselines)), excluded_counts=dict(exclusions)),
        warnings=warnings, release_decision='ENGINEER_CONFIRMATION_REQUIRED')
