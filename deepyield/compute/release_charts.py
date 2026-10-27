"""Deterministic, full-population statistics for the six Release charts."""
from __future__ import annotations

import numpy as np

from deepyield.rules.conditions import as_float
from deepyield.compute.release_numeric import cg_number


def moments(values: list[float]) -> dict:
    return {'n': len(values), 'mean': float(np.mean(values)) if values else None,
            'std': as_float(np.std(values, ddof=1)) if len(values) > 1 else None}


def _box(points: list[dict]) -> dict:
    values = np.asarray([p['value'] for p in points], dtype=float)
    if not len(values):
        return {'q1': None, 'median': None, 'q3': None, 'low': None, 'high': None,
                'outlier_count': 0, 'outliers': []}
    q1, median, q3 = np.quantile(values, [.25, .5, .75], method='linear')
    iqr = q3 - q1
    inside = values[(values >= q1 - 1.5 * iqr) & (values <= q3 + 1.5 * iqr)]
    outliers = [p for p in points if p['value'] < q1 - 1.5 * iqr or p['value'] > q3 + 1.5 * iqr]
    return {'q1': float(q1), 'median': float(median), 'q3': float(q3),
            'low': float(inside.min()) if len(inside) else float(q1),
            'high': float(inside.max()) if len(inside) else float(q3),
            'outlier_count': len(outliers), 'outliers': thin_points(outliers, 200)}


def thin_points(points: list[dict], limit: int) -> list[dict]:
    if len(points) <= limit:
        return points
    return [points[i] for i in np.linspace(0, len(points) - 1, limit, dtype=int)]


def build_chart_statistics(points: list[dict], item: dict, *, bins: int = 24,
                           point_limit: int = 5000, bin_width: float | None = None,
                           thresholds: dict | None = None) -> dict:
    if not isinstance(bins, int) or isinstance(bins, bool) or not 1 <= bins <= 200:
        raise ValueError('分箱数必须为 1–200 的整数')
    if not isinstance(point_limit, int) or isinstance(point_limit, bool) or not 10 <= point_limit <= 10000:
        raise ValueError('显示点数必须为 10–10000 的整数')
    values = [p['value'] for p in points]
    stats = moments(values)
    low, high = as_float(item.get('effective_lsl')), as_float(item.get('effective_usl'))
    valid_spec = low is not None and high is not None and low < high
    stats['cp'] = (high - low) / (6 * stats['std']) if valid_spec and stats['std'] else None
    stats['cpk'] = as_float(item.get('cpk'))
    stats['lsl'], stats['usl'] = low, high
    stats['spec_valid'] = not (low is not None and high is not None and low >= high)
    stats['unit'] = item.get('unit')
    if values and min(values) == max(values):
        width = abs(values[0]) * .01 if values[0] else .5
        edges = [values[0] - width, values[0] + width]
    else:
        edges = np.histogram_bin_edges(values, bins=bins).tolist() if values else []
    if bin_width is not None:
        if as_float(bin_width) is None or bin_width <= 0:
            raise ValueError('分箱宽度必须为正有限数')
        if values:
            count = max(1, int(np.ceil((max(values) - min(values)) / bin_width)))
            if count > 200:
                raise ValueError('分箱宽度过小，最多支持 200 个分箱')
            start = min(values) if max(values) != min(values) else min(values) - bin_width / 2
            edges = [start + i * bin_width for i in range(count + 1)]
            edges[-1] = max(edges[-1], max(values))
            if any(a >= b for a, b in zip(edges, edges[1:])):
                raise ValueError('分箱宽度小于当前数值的浮点分辨率，请增大宽度')
    by_site: dict[float | None, list[dict]] = {}
    for point in points:
        by_site.setdefault(point['site'], []).append(point)
    report_sites = {s['site']: s for s in item.get('sites', [])}
    site_keys = sorted(set(by_site) | set(report_sites), key=lambda s: (s is None, s or 0))
    sites = []
    for site in site_keys:
        group = by_site.get(site, [])
        site_values = [p['value'] for p in group]
        reported = report_sites.get(site, {})
        site_stats = moments(site_values)
        cg = reported.get('cg') if cg_number(reported.get('cg')) is not None else None
        sites.append({**site_stats, 'site': site, 'cg': cg, 'status': reported.get('status'),
            'reason': reported.get('reason'), 'box': _box(group),
            'histogram': np.histogram(site_values, bins=edges)[0].tolist() if edges else [],
            'cg_status': _cg_status(cg, thresholds or {}),
            'heads': sorted({p['head'] for p in group if p['head'] is not None})})
    sigma = [s['std'] for s in sites if s['std'] is not None]
    std_factor = (thresholds or {}).get('var_red', 2.5)
    std_threshold = float(np.median(sigma)) * std_factor if sigma else None
    sampled = thin_points(points, point_limit)
    return {'statistics': stats, 'sites': sites, 'histogram': {'edges': edges},
            'points': sampled, 'total_points': len(points), 'displayed_points': len(sampled),
            'sampled': len(sampled) < len(points), 'std_threshold': std_threshold,
            'thresholds': thresholds or {}, 'cpk_override_applied': item.get('cpk_override_applied', False),
            'status': item['status'], 'reason': item.get('reason'),
            'excluded_counts': item.get('excluded_counts', {}),
            'empty_reason': '功能测试不适用连续参数图' if item.get('record_type') == 'FTR'
                else '没有入选有效测量值' if not points else None}


def _cg_status(value: float | None, thresholds: dict) -> str:
    value = cg_number(value)
    if value is None:
        return 'NOT_CALCULABLE'
    if value < thresholds.get('cg_fail', 1.):
        return 'FAIL'
    return 'RISK' if value < thresholds.get('cg_pass', 1.33) else 'PASS'


def select_chart_samples(points: list[dict], selection: dict) -> list[dict]:
    """Selection changes only the displayed evidence, never the report population."""
    ids = None if selection.get('ids') is None else set(selection['ids'])
    sites = None if selection.get('sites') is None else set(selection['sites'])
    chosen = []
    for point in points:
        if ids is not None and point['id'] not in ids:
            continue
        if sites is not None and point['site'] not in sites:
            continue
        if selection.get('wafer_key') is not None and (point['lot'], point['wafer']) != tuple(selection['wafer_key']):
            continue
        if any(selection.get(k) is not None and (point[field] is None or point[field] < selection[k])
               for k, field in [('value_min', 'value'), ('sequence_min', 'record_sequence'), ('x_min', 'x'), ('y_min', 'y')]):
            continue
        if any(selection.get(k) is not None and (point[field] is None or point[field] > selection[k])
               for k, field in [('value_max', 'value'), ('sequence_max', 'record_sequence'), ('x_max', 'x'), ('y_max', 'y')]):
            continue
        if selection.get('value_max_exclusive') and selection.get('value_max') is not None and point['value'] == selection['value_max']:
            continue
        chosen.append(point)
    return chosen
