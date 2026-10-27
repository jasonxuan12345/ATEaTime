"""16:9 production capability slides from the saved Bin1 population and rules."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from io import BytesIO
import json
import math
import pickle
from tempfile import TemporaryFile
from pathlib import Path
from deepyield.compute.release_export_session import export_input, export_catalog

import numpy as np
from reportlab.lib.colors import HexColor
from deepyield.compute.release_pdf_canvas import ReportCanvas as Canvas
from scipy.stats import gaussian_kde, norm

from deepyield.compute.release_cgm_pdf import Slides, WIDTH, HEIGHT, BLUE, LIGHT, LANE, COLORS, _fmt
from deepyield.compute.release_cpk_excel import _bands, _band_status, _identifier, _report_filename
from deepyield.compute.release_control import report_progress, checkpoint, timed, stage
from deepyield.compute.release_cpk import _flag
from deepyield.compute.release_numeric import cpk_metrics
from deepyield.compute.release_chart_wafer import release_wafer_data
from deepyield.io.stdf_parquet_writer import _classify_part_flag
from deepyield.compute.ews_yield.identity import classify_coordinate_status
from deepyield.compute.ews_yield.models import CoordinateStatus
from deepyield.rules.conditions import as_float
from deepyield.compute.release_remarks import diagnose_item

STATES = {'PASS': 'Pass', 'RISK': 'Need to check', 'FAIL': 'Fail', 'UNAVAILABLE': None}


def cpk_state(value, fail_count, bands):
    """Numeric bands are saved with the run; out-of-spec results always fail."""
    return 'Fail' if (fail_count or 0) > 0 else STATES[_band_status(value, bands)]


def cpk_display(value, bands):
    text = _fmt(value)
    if as_float(value) is not None and _band_status(float(text), bands) != _band_status(value, bands):
        return str(float(value))  # Never round across a classification boundary.
    return text


def cpk_population(frame, report):
    """Yield denominator is all selected executions, independent of Bin1 samples."""
    indices = (report.get('selection') or {}).get('selected_part_indices')
    if indices is None:
        raise ValueError('CPK 报告缺少已保存的选样，请重新计算')
    selected = frame.loc[frame['__part_index'].isin(indices)]
    if len(selected) != len(indices):
        raise ValueError('CPK 选中的测试记录已不存在，请重新计算')
    total = len(selected)
    passed = sum(as_float(row['SBIN']) == 1 and _flag(row['PART_FLAG']) is not None
                 and _classify_part_flag(_flag(row['PART_FLAG']))[0] == 'PASS'
                 for row in selected[['SBIN', 'PART_FLAG']].to_dict('records'))
    def unique(column):
        if column not in selected:
            return []
        return sorted({str(value).strip() for value in selected[column].dropna() if str(value).strip()})
    sites = sorted({int(value) for value in selected['SITE'] if as_float(value) is not None})
    return {'sites': sites, 'total_parts': total, 'bin1_parts': passed,
            'bin1_yield': 100 * passed / total if total else None,
            'lots': unique('LOT_ID'), 'wafers': unique('WAFER_ID')}


@timed('export.pdf.prepare')
def prepare_cpk_pdf(report, *, sample_provider, population, sample_store=None):
    if report.get('report_type') != 'release_bin1_cpk':
        raise ValueError('需要 Release Bin1 CPK 报告')
    bands = _bands(report)
    sites = sorted(set(population['sites']))
    if not 1 <= len(sites) <= 16:
        raise ValueError('CPK PDF 支持 1 到 16 个活动 Site')
    rows, excluded = [], len(report.get('unexecuted_tests') or [])
    items = report.get('items', [])
    for index, item in enumerate(items):
        report_progress(f'准备 CPK 测项：{index + 1} / {len(items)}', 50 * index / max(1, len(items)))
        low, high = (as_float(item.get(k)) for k in ('effective_lsl', 'effective_usl'))
        if (item.get('record_type') == 'FTR' or (low is None and high is None)
                or (low is not None and high is not None and low >= high)
                or (not item.get('fail_count') and (as_float(item.get('cpk')) is None
                    or item.get('status') not in {'PASS', 'RISK', 'FAIL'}))):
            excluded += 1
            continue
        result = cpk_state(item.get('cpk'), item.get('fail_count'), bands)
        base = {key: value for key, value in item.items() if key != 'sample_refs'}
        diagnosis = item.get('diagnosis') or diagnose_item('cpk', item,
            source=report.get('source'), selection=report.get('selection'))
        base['remark'] = diagnosis.get('remark', diagnosis.get('auto_remark', '')) or ''
        if result == 'Pass':
            rows.append({**base, 'low': low, 'high': high, 'result': result})
            continue
        points = sample_provider(item)
        values = np.asarray([p['value'] for p in points], dtype=float)
        if len(points) != item['n'] or not len(points) or not np.isfinite(values).all():
            raise ValueError('CPK PDF 图形样本与报告不一致，请重新计算')
        if any(p['site'] not in sites for p in points):
            raise ValueError('CPK 样本包含未入选 Site')
        measured = cpk_metrics(values, low, high)
        for key, metric in [('mean', 'Mean'), ('std', 'Std'), ('cpk', 'cpk'), ('fail_count', 'FailCount')]:
            saved, actual = as_float(item.get(key)), as_float(measured[metric])
            if (saved is None) != (actual is None) or (saved is not None and not np.isclose(saved, actual, rtol=1e-8, atol=1e-12)):
                raise ValueError('CPK 原始样本与已保存的统计不一致，请重新计算')
        grouped = {site: [] for site in sites}
        for point in points:
            grouped[point['site']].append(point['value'])
        stats = []
        for site in sites:
            data = np.asarray(grouped[site], dtype=float)
            metrics = cpk_metrics(data, low, high)
            stats.append({'site': site, 'n': len(data), 'data': data,
                          'mean': metrics['Mean'], 'std': metrics['Std'], 'cpk': metrics['cpk'],
                          'fail_count': metrics['FailCount'],
                          'state': cpk_state(metrics['cpk'], metrics['FailCount'], bands)})
        valid = [site for site in stats if site['cpk'] is not None]
        worst = min(valid, key=lambda site: site['cpk']) if valid else None
        row = {**base, 'low': low, 'high': high, 'sites': stats, 'points': points,
                     'values': values, 'result': result, 'worst_site': worst['site'] if worst else None,
                     'min_site_cpk': worst['cpk'] if worst else None}
        if sample_store is not None:
            offset = sample_store.tell()
            pickle.dump(row, sample_store, protocol=pickle.HIGHEST_PROTOCOL)
            row = {key: value for key, value in row.items() if key not in {'values', 'points', 'sites'}}
            row['sites'] = [{key: value for key, value in site.items() if key != 'data'} for site in stats]
            row['_sample_offset'] = offset
        rows.append(row)
    actions = sorted((row for row in rows if row['result'] in {'Fail', 'Need to check'}),
                     key=lambda r: (r['result'] != 'Fail', r['cpk'] if r['cpk'] is not None else -math.inf))
    return {'rows': rows, 'actions': actions, 'sites': sites, 'bands': bands,
            'excluded': excluded, 'population': population,
            'counts': dict(Counter(row['result'] for row in rows))}


def _summary(s, report, data, metadata, device, index, total):
    s.header(' - '.join(part for part in [device, 'CPK Analysis Report (Executive Summary)'] if part), '', index + 1, total)
    bands = data['bands']
    if index == 0:
        mir = (metadata.get('stdf_metadata') or {}).get('mir') or {}
        def field(key, raw):
            values = sorted({str(r.get('conditions', {}).get(key)) for r in report.get('items', [])
                             if r.get('conditions', {}).get(key) not in (None, '')})
            return ', '.join(values) or mir.get(key, mir.get(raw, '--'))
        population = data['population']
        lots = ', '.join(population.get('lots') or []) or mir.get('lot_id', '--')
        wafers = ', '.join(population.get('wafers') or []) or '--'
        s.box(29, 461, 510, 99, LIGHT, 5)
        device_label = f'Device ID: {device} | ' if device else ''
        lines = [f'{device_label}Lot ID / Wafer ID: {lots} / {wafers}',
                 f'Test Temp: {field("test_temperature", "TST_TEMP")} | Tester Name: {field("node_name", "NODE_NAM")}',
                 'Active Sites: ' + ', '.join(f'S{site}' for site in data['sites']),
                 f'Total Parts Executed: {population["total_parts"]} | Bin 1 Yield (%): {_fmt(population["bin1_yield"])}']
        for i, text in enumerate(lines):
            s.text(39, 543 - i * 21, text, 10, width=490)
        for i, (label, state) in enumerate([('Total Evaluated Tests', None), ('PASS', 'Pass'),
                                           ('Need to Check', 'Need to check'), ('FAIL', 'Fail')]):
            x = 553 + i * 145
            s.box(x, 461, 137, 99, COLORS.get(state, LIGHT), 8)
            s.text(x + 8, 536, label, 10.5, bold=True)
            n = len(data['rows']) if state is None else data['counts'].get(state, 0)
            s.text(x + 10, 500, n, 25, bold=True)
            if state:
                s.text(x + 10, 477, f'{100 * n / len(data["rows"]) if data["rows"] else 0:.1f}%', 11)
    chunk = data['actions'][index * 12:(index + 1) * 12]
    top = 437 if index == 0 else 548
    s.text(30, top, 'Part 2 - Action List | Fail first, then Need to check; ascending Overall CPK', 11, bold=True)
    headers = ['Test Nr.', 'Test Name', 'Unit', 'Lowlim', 'Uplim']
    widths = [57, 194, 36, 59, 59]
    small = len(data['sites']) <= 4
    if small:
        headers += [f'S{site} CPK' for site in data['sites']]
        widths += [230 / len(data['sites'])] * len(data['sites'])
    else:
        headers += ['Worst\nSite', 'Min Site\nCPK', 'Overall\nMean', 'Overall\nStdDev']
        widths += [53, 67, 70, 72]
    headers += ['Overall\nCPK', 'Fail\nCount', 'Result']
    widths += [76, 56, 105]
    widths = [width * 1092 / sum(widths) for width in widths]
    rows, fills = [], {}
    for i, row in enumerate(chunk):
        values = [row.get('test_num'), row.get('test_name'), row.get('unit'), row['low'], row['high']]
        if small:
            for site in row['sites']:
                if site['state']:
                    fills[i, len(values)] = COLORS[site['state']]
                values.append(cpk_display(site['cpk'], bands))
        else:
            worst = next((site for site in row['sites'] if site['site'] == row['worst_site']), None)
            if worst and worst['state']:
                fills[i, len(values) + 1] = COLORS[worst['state']]
            values += [f'S{row["worst_site"]}' if row['worst_site'] is not None else '--',
                       cpk_display(row['min_site_cpk'], bands), row['mean'], row['std']]
        overall_state = cpk_state(row['cpk'], 0, bands)
        if overall_state:
            fills[i, len(values)] = COLORS[overall_state]
        values += [cpk_display(row['cpk'], bands), int(row['fail_count'] or 0), row['result']]
        fills[i, len(values) - 1] = COLORS[row['result']]
        rows.append(values)
    s.table(headers, rows, widths, 30, top - 14, 28, fills, 9)
    if not rows:
        s.text(45, top - 80, 'No Fail or Need to check items.', 16, BLUE, True)
    rules = '; '.join(f'{b["status"]}: {b["condition"]}' for b in bands)
    s.text(30, 28, f'Bin1 valid samples | {rules} | Out-of-spec Fail Count > 0: FAIL', 8)
    s.text(30, 15, f'Excluded non-evaluable / status tests: {data["excluded"]} | Yield uses selected executions, including non-Bin1.', 8)


class Plot:
    """Small vector axes with consistent units and no oversized legends."""
    def __init__(self, slide, rect, title, xlim, ylim, xlabel='', ylabel=''):
        self.s, self.rect, self.xlim, self.ylim = slide, rect, xlim, ylim
        x, y, w, h = rect
        slide.text(x, y + h + 15, title, 11, BLUE, True)
        for tick in np.linspace(*ylim, 4):
            py = self.y(tick)
            slide.line(x, py, x + w, py, '#DAE3EA', width=.4)
            slide.text(x - 43, py - 3, tick, 8, width=39)
        slide.line(x, y, x + w, y, BLUE)
        if xlabel:
            for tick in np.linspace(*xlim, 4):
                slide.text(self.x(tick) - 12, y - 14, tick, 8)
            slide.text(x + w / 2 - 25, y - 31, xlabel, 9)
        if ylabel:
            slide.text(x, y + h + 3, ylabel, 8)

    def x(self, value):
        return self.rect[0] + (value - self.xlim[0]) / (self.xlim[1] - self.xlim[0]) * self.rect[2]

    def y(self, value):
        return self.rect[1] + (value - self.ylim[0]) / (self.ylim[1] - self.ylim[0]) * self.rect[3]

    def points(self, xs, ys, color, radius=1.5, alpha=1):
        self.s.c.saveState()
        self.s.c.setFillAlpha(alpha)
        self.s.c.setFillColor(HexColor(color))
        self.s.dots(((self.x(x), self.y(y)) for x, y in zip(xs, ys)), radius, separate=alpha < 1)
        self.s.c.restoreState()

    def curve(self, xs, ys, color, width=1, alpha=1):
        self.s.c.saveState()
        self.s.c.setStrokeAlpha(alpha)
        self.s.c.setStrokeColor(HexColor(color))
        self.s.c.setLineWidth(width)
        path = self.s.c.beginPath()
        for i, (x, y) in enumerate(zip(xs, ys)):
            if i % 2048 == 0:
                checkpoint()
            (path.moveTo if i == 0 else path.lineTo)(self.x(x), self.y(y))
        self.s.c.drawPath(path)
        self.s.c.restoreState()

    def specs(self, row, vertical=False):
        for bound, label in [(row['low'], 'LSL'), (row['high'], 'USL')]:
            if bound is None:
                continue
            x, y, w, h = self.rect
            if vertical:
                self.s.line(self.x(bound), y, self.x(bound), y + h, COLORS['Fail'], [4, 3])
                self.s.text(self.x(bound) + 2, y + h - 10, label, 8, COLORS['Fail'])
            else:
                self.s.line(x, self.y(bound), x + w, self.y(bound), COLORS['Fail'], [4, 3])
                self.s.text(x + w + 3, self.y(bound) + 2, label, 8, COLORS['Fail'])


def _range(values, low=None, high=None):
    bounds = [float(np.min(values)), float(np.max(values))] + [v for v in (low, high) if v is not None]
    lower, upper = min(bounds), max(bounds)
    margin = (upper - lower) * .1 or max(abs(lower) * .02, .5)
    return lower - margin, upper + margin


def _color(site, index):
    return COLORS['Fail'] if site['state'] == 'Fail' else '#D98200' if site['state'] == 'Need to check' else (BLUE if index % 2 == 0 else '#4198CF')


def highlighted_sites(sites):
    return [s['site'] for s in sorted((s for s in sites if s['state'] in {'Fail', 'Need to check'}),
            key=lambda s: (s['cpk'] is not None, s['cpk'] if s['cpk'] is not None else -math.inf))[:3]]


def _distribution_color(site, index, small, highlighted):
    if small:
        return [BLUE, '#4198CF', '#D98200', '#7B61A8'][index]
    if site['site'] in highlighted:
        return ['#EA0000', '#D98200', '#A63B00'][highlighted.index(site['site'])]
    return BLUE


def _sequence(s, row):
    sites = row['sites']
    plot = Plot(s, (58, 334, 650, 193), '1  Measurement Sequence by Site',
                (0, len(sites)), _range(row['values'], row['low'], row['high']), ylabel=row.get('unit') or 'Value')
    x, y, w, h = plot.rect
    for i, site in enumerate(sites):
        if (i + 1) % 2 == 0:
            s.c.saveState()
            s.c.setFillAlpha(.65)
            s.box(plot.x(i), y, w / len(sites), h, LANE)
            s.c.restoreState()
        xx = np.linspace(i + .07, i + .93, site['n'])
        plot.points(xx, site['data'], _color(site, i), radius=1.3)
        if site['mean'] is not None:
            s.line(plot.x(i + .04), plot.y(site['mean']), plot.x(i + .96), plot.y(site['mean']), BLUE, width=1)
        label = f'S{site["site"]}'
        s.text(plot.x(i + .5) - 8, y - 14, label, 8)
        s.text(plot.x(i + .5) - 8, y + h - 10, label, 8)
    plot.specs(row)


def _kde(values, grid):
    if len(values) < 2 or np.ptp(values) == 0:
        return None
    try:
        return gaussian_kde(values)(grid)
    except np.linalg.LinAlgError:
        return None


def _legend(s, x, y, entries):
    for i, (label, color) in enumerate(entries):
        s.line(x + i * 68, y + 3, x + i * 68 + 12, y + 3, color, width=1.8)
        s.text(x + i * 68 + 16, y, label, 8, color)


def _histogram(s, row, highlighted):
    sites, values = row['sites'], row['values']
    xlim = _range(values, row['low'], row['high'])
    edges = np.linspace(*xlim, min(25, max(8, int(math.sqrt(len(values))))) + 1)
    bin_width = edges[1] - edges[0]
    grid = np.linspace(*xlim, 160)
    small = len(sites) <= 4
    curves = []
    hist = np.histogram(values, edges)[0]
    for i, site in enumerate(sites):
        if small or site['state'] == 'Pass' or site['site'] in highlighted:
            density = _kde(site['data'], grid)
            if density is not None:
                curves.append((site, i, density * (site['n'] if small else len(values)) * bin_width))
    ymax = max([float(max(hist))] + [float(max(curve)) for _, _, curve in curves]) * 1.22 or 1
    plot = Plot(s, (58, 62, 285, 190), '2  Histogram + KDE', xlim, (0, ymax), 'Value', 'Count (KDE scaled to count)')
    if small:
        bottom = np.zeros(len(edges) - 1)
        for i, site in enumerate(sites):
            counts = np.histogram(site['data'], edges)[0]
            for j, count in enumerate(counts):
                s.box(plot.x(edges[j]), plot.y(bottom[j]), plot.x(edges[j + 1]) - plot.x(edges[j]) - .4,
                      plot.y(bottom[j] + count) - plot.y(bottom[j]), _distribution_color(site, i, small, highlighted))
            bottom += counts
    else:
        for i, count in enumerate(hist):
            s.box(plot.x(edges[i]), plot.y(0), plot.x(edges[i + 1]) - plot.x(edges[i]) - .4,
                  plot.y(count) - plot.y(0), '#B7D5E8')
    for site, index, curve in sorted(curves, key=lambda entry: small or entry[0]['site'] in highlighted):
        background = not small and site['site'] not in highlighted
        plot.curve(grid, curve, _distribution_color(site, index, small, highlighted),
                   .6 if background else 1.6, .2 if background else 1)
    plot.specs(row, vertical=True)
    entries = [(f'S{site["site"]}', _distribution_color(site, i, small, highlighted)) for i, site in enumerate(sites)
               if small or site['site'] in highlighted]
    _legend(s, 58, 286, entries)


def _qq(s, row, highlighted):
    sites = row['sites']
    small = len(sites) <= 4
    groups = []
    theoretical_by_count = {}
    for i, site in enumerate(sites):
        if not site['n'] or (not small and site['state'] != 'Pass' and site['site'] not in highlighted):
            continue
        values = site.setdefault('sorted_data', np.sort(site['data']))
        if len(values) not in theoretical_by_count:
            theoretical_by_count[len(values)] = norm.ppf((np.arange(len(values)) + .5) / len(values))
        theoretical = theoretical_by_count[len(values)]
        if len(values) > 1:
            slope, intercept = np.polyfit(theoretical, values, 1)
        else:
            slope, intercept = 0., values[0]
        groups.append((site, i, theoretical, values, slope, intercept))
    extent = max([2.5] + [max(abs(x)) for _, _, x, _, _, _ in groups]) * 1.08
    # Include fitted endpoints in the axes so heavy tails cannot clip the lines.
    fitted = [a * t + b for _, _, _, _, a, b in groups for t in (-extent, extent)]
    ylim = _range(np.concatenate([row['values'], np.asarray(fitted)]))
    plot = Plot(s, (416, 62, 257, 190), '3  Normal Q-Q', (-extent, extent), ylim, 'Theoretical Sigma', row.get('unit') or 'Value')
    for site, index, x, y, slope, intercept in sorted(groups, key=lambda entry: small or entry[0]['site'] in highlighted):
        background = not small and site['site'] not in highlighted
        color = _distribution_color(site, index, small, highlighted)
        plot.points(x, y, color, 1 if background else 1.7, .2 if background else 1)
        plot.curve([-extent, extent], [intercept - slope * extent, intercept + slope * extent],
                   color, .5 if background else 1.1, .2 if background else 1)


def _boxplot(s, row, top=286):
    sites = row['sites']
    plot = Plot(s, (769, 62, 347, top - 96), '4  Site Boxplots', (0, len(sites)),
                _range(row['values'], row['low'], row['high']), ylabel=row.get('unit') or 'Value')
    fills = {'Pass': '#DCEBCB', 'Need to check': '#FFF9B8', 'Fail': '#F8CECE'}
    for i, site in enumerate(sites):
        s.text(plot.x(i + .5) - 8, 48, f'S{site["site"]}', 8)
        if not site['n']:
            continue
        data = site.get('sorted_data')
        if data is None:
            data = np.sort(site['data'])
        positions = (len(data) - 1) * np.asarray([.25, .5, .75])
        lower_index, upper_index = np.floor(positions).astype(int), np.ceil(positions).astype(int)
        fractions = positions - lower_index
        q1, median, q3 = data[lower_index] * (1 - fractions) + data[upper_index] * fractions
        iqr = q3 - q1
        inliers = data[(data >= q1 - 1.5 * iqr) & (data <= q3 + 1.5 * iqr)]
        lower, upper = min(inliers), max(inliers)
        cx, left, right = plot.x(i + .5), plot.x(i + .22), plot.x(i + .78)
        s.line(cx, plot.y(lower), cx, plot.y(upper), BLUE)
        for cap in (lower, upper):
            s.line(cx - 4, plot.y(cap), cx + 4, plot.y(cap), BLUE)
        s.box(left, plot.y(q1), right - left, max(.6, plot.y(q3) - plot.y(q1)), fills.get(site['state'], LANE))
        s.line(left, plot.y(median), right, plot.y(median), BLUE, width=1.1)
        mean_y = plot.y(site['mean'])
        path = s.c.beginPath()
        for j, (dx, dy) in enumerate([(0, 3), (3, 0), (0, -3), (-3, 0)]):
            (path.moveTo if j == 0 else path.lineTo)(cx + dx, mean_y + dy)
        path.close()
        s.c.setFillColor(HexColor(BLUE))
        s.c.drawPath(path, fill=1, stroke=0)
        outliers = data[(data < q1 - 1.5 * iqr) | (data > q3 + 1.5 * iqr)]
        plot.points(np.full(len(outliers), i + .5), outliers, COLORS['Fail'], radius=1.6)
    plot.specs(row)
    s.text(769, top, 'Diamond = Mean | Line = Median | Red dots = Outliers', 8)


def _site_tables(s, row, bands, note=None, top=552):
    sites = row['sites']
    split = len(sites) > 8
    s.text(760, top, '5  Site Statistics' + (' (two columns)' if split else ''), 11, BLUE, True)
    chunks = [sites[:8], sites[8:]] if split else [sites]
    table_top = top - 15
    row_h = min(21, max(14, (table_top - 30 - 285) / max(map(len, chunks))))
    for index, chunk in enumerate(chunks):
        widths = [30, 49, 49, 47] if split else [45, 104, 104, 108]
        fills = {(i, 3): COLORS[site['state']] for i, site in enumerate(chunk) if site['state']}
        rows = [[f'S{site["site"]}', site['mean'], site['std'], cpk_display(site['cpk'], bands)] for site in chunk]
        s.table(['Site', 'Mean', 'Std', 'CPK'], rows, widths, 760 + index * 191, table_top, row_h, fills, 8)
    bottom = table_top - 30 - row_h * max(map(len, chunks))
    if note:
        s.text(760, bottom - 14, note, 8, width=367)
        bottom -= 24
    return bottom


def _wafer_or_table(s, row, bands, top=552):
    identities = set()
    for index, point in enumerate(row['points']):
        if index % 2048 == 0:
            checkpoint()
        if point.get('lot') and point.get('wafer') and classify_coordinate_status(point.get('x'), point.get('y')) == CoordinateStatus.USABLE:
            identities.add((point['lot'], point['wafer']))
        if len(identities) > 1:
            break
    if len(identities) != 1:
        reason = ('Multiple wafers; Site statistics shown without merging maps.' if identities else
                  'No unique wafer identity/coordinates; Site statistics shown.')
        bottom = _site_tables(s, row, bands, reason, top)
        return ('site_table_2col' if len(row['sites']) > 8 else 'site_table'), bottom
    wafer = release_wafer_data(row['points'], row, limit=max(1, len(row['points'])))
    if len(wafer['wafers']) != 1:
        reason = 'No unique wafer identity/coordinates; Site statistics shown.'
        if len(wafer['wafers']) > 1:
            reason = 'Multiple wafers; Site statistics shown without merging maps.'
        bottom = _site_tables(s, row, bands, reason, top)
        return ('site_table_2col' if len(row['sites']) > 8 else 'site_table'), bottom
    group = wafer['wafers'][0]
    s.text(760, top, '5  Wafer Map | ' + group['wafer'], 11, BLUE, True, width=365)
    points = group['points']
    xvals, yvals = [p['x'] for p in points], [p['y'] for p in points]
    mx, my = (min(xvals) + max(xvals)) / 2, (min(yvals) + max(yvals)) / 2
    radius_units = max(math.hypot(p['x'] - mx, p['y'] - my) for p in points) + .8
    radius = min(90, (top - 320) / 2)
    cx, cy = 904, top - 32 - radius
    scale = radius / radius_units
    s.c.setStrokeColor(HexColor(BLUE))
    s.c.setLineWidth(.9)
    s.c.circle(cx, cy, radius, stroke=1, fill=0)
    vmin, vmax = min(p['value'] for p in points), max(p['value'] for p in points)
    def color(value):
        fraction = (value - vmin) / (vmax - vmin) if vmax != vmin else .5
        stops = [(0., (0, 86, 145)), (.5, (116, 178, 48)), (1., (234, 0, 0))]
        left, right = (stops[0], stops[1]) if fraction <= .5 else (stops[1], stops[2])
        t = (fraction - left[0]) / (right[0] - left[0])
        rgb = [round(a + (b - a) * t) for a, b in zip(left[1], right[1])]
        return '#' + ''.join(f'{v:02x}' for v in rgb)
    side = min(8., .8 * scale)
    for point in points:
        px, py = cx + (point['x'] - mx) * scale, cy + (point['y'] - my) * scale
        s.box(px - side / 2, py - side / 2, side, side, color(point['value']))
        outside = (row['low'] is not None and point['value'] < row['low']) or (row['high'] is not None and point['value'] > row['high'])
        if outside:
            s.line(px - 3, py - 3, px + 3, py + 3, COLORS['Fail'], width=1.4)
            s.line(px - 3, py + 3, px + 3, py - 3, COLORS['Fail'], width=1.4)
    for i in range(100):
        s.box(1020, cy - radius + i * 2 * radius / 100, 11, 2 * radius / 100 + .1,
              color(vmin + (vmax - vmin) * i / 99))
    for fraction in (0, .5, 1):
        s.text(1038, cy - radius + fraction * 2 * radius, vmin + (vmax - vmin) * fraction, 8)
    bottom = cy - radius
    s.text(812, bottom - 14, f'X / Y (1:1) | X: {min(xvals)}..{max(xvals)}, Y: {min(yvals)}..{max(yvals)}', 8, width=300)
    s.text(760, bottom - 28, f'Lot {group["lot"]} | Dies {group["total_dies"]} | Missing coordinates {wafer["excluded_count"]}', 8, width=367)
    s.text(760, bottom - 40, f'Red x = out of spec | Latest selected execution per die; duplicates {group["duplicate_positions"]}', 8, width=367)
    return 'wafer', bottom - 50


@timed('export.pdf.draw')
def _detail(s, row, bands, page, total):
    worst = f'S{row["worst_site"]} ({cpk_display(row["min_site_cpk"], bands)})' if row['worst_site'] is not None else '--'
    subtitle = (f'Unit: {row.get("unit") or "--"} | LSL: {_fmt(row["low"])} | USL: {_fmt(row["high"])} | '
                f'Total Parts (Bin1): {row["n"]} | Overall CPK: {cpk_display(row["cpk"], bands)} | Worst Site: {worst}')
    s.header(f'Test #{row.get("test_num")} - {row.get("test_name")}', subtitle, page, total, row['result'])
    _sequence(s, row)
    highlighted = highlighted_sites(row['sites'])
    _histogram(s, row, highlighted)
    _qq(s, row, highlighted)
    right_top = s.remark(row['remark'], 760, 367)
    mode, right_bottom = _wafer_or_table(s, row, bands, right_top)
    _boxplot(s, row, min(286, right_bottom - 18))
    s.text(30, 15, 'Part 3 - Action Item Details | Bin1 valid samples | Full selected population; KDE omitted for constant/<2 samples.', 8)
    return mode


def render_cpk_pdf(report, *, sample_provider, population, metadata=None, device_id=None, generated_at=None, output=None):
    with TemporaryFile() as samples:
        return _render_cpk_pdf(report, sample_provider=sample_provider, population=population,
            metadata=metadata, device_id=device_id, generated_at=generated_at, sample_store=samples, output=output)


def _render_cpk_pdf(report, *, sample_provider, population, metadata, device_id, generated_at, sample_store, output):
    report_progress('准备 CPK PDF', 0)
    data = prepare_cpk_pdf(report, sample_provider=sample_provider, population=population, sample_store=sample_store)
    metadata = metadata or {}
    mir = (metadata.get('stdf_metadata') or {}).get('mir') or {}
    device = _identifier(report.get('items', []), metadata, 'part_type', device_id or mir.get('PART_TYP'), 'Device ID')
    moment = generated_at or datetime.now().astimezone()
    summary_pages = max(1, math.ceil(len(data['actions']) / 12))
    total = summary_pages + len(data['actions'])
    buffer = output if output is not None else BytesIO()
    canvas = Canvas(buffer, pagesize=(WIDTH, HEIGHT), pageCompression=1)
    canvas.setTitle(' - '.join(part for part in [device, 'CPK Analysis Report'] if part))
    canvas.setAuthor('Datalog Check')
    canvas.setSubject('Bin1 CPK selected statistics; ' + json.dumps({
        'source': report.get('source'), 'selection': report.get('selection'), 'rules': report.get('rules')}, ensure_ascii=False))
    slides = Slides(canvas, moment.strftime('%Y-%m-%d %H:%M:%S %Z'))
    for index in range(summary_pages):
        report_progress(f'生成 CPK PDF：第 {index + 1} / {total} 页', 50 + 45 * index / total)
        _summary(slides, report, data, metadata, device, index, total)
        canvas.showPage()
    modes = []
    for index, row in enumerate(data['actions']):
        report_progress(f'生成 CPK PDF：第 {summary_pages + index + 1} / {total} 页', 50 + 45 * (summary_pages + index) / total)
        sample_store.seek(row['_sample_offset'])
        detail = pickle.load(sample_store)
        modes.append(_detail(slides, detail, data['bands'], summary_pages + index + 1, total))
        canvas.showPage()
        del detail
    report_progress('正在封装 CPK PDF', 95)
    with stage('export.pdf.package', '正在封装 CPK PDF'):
        canvas.save()
    report_progress('CPK PDF 已生成', 100)
    return {'filename': _report_filename(moment, device, 'Cpk_Report.pdf'), 'content': buffer.getvalue() if output is None else None,
            'page_count': total, 'evaluated': len(data['rows']), 'action_count': len(data['actions']),
            'excluded_count': data['excluded'], 'detail_modes': modes}


def export_release_cpk_pdf(input_path, output_dir, *, device_id=None, rule_dir=None, **selection):
    from deepyield.compute.release_stdf import read_release_wide_all
    from deepyield.compute.release_cpk import build_release_bin1_cpk_report
    from deepyield.compute.release_chart_samples import ReportSampleResolver
    from deepyield.rules.loader import load_analysis_rules
    frame, metadata = export_input(Path(input_path), read_release_wide_all)
    selection = {'part_indices': None, 'sites': None, 'test_columns': None, 'minimum_samples': None, **selection}
    report = build_release_bin1_cpk_report(frame, metadata, catalog=export_catalog(rule_dir), **selection)
    result = render_cpk_pdf(report, metadata=metadata, device_id=device_id, population=cpk_population(frame, report),
        sample_provider=ReportSampleResolver(frame, metadata['source_sha256'], identifiers=False))
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / result['filename']
    with output.open('xb') as stream:
        stream.write(result['content'])
    return {key: value for key, value in result.items() if key != 'content'} | {'path': str(output.resolve())}
