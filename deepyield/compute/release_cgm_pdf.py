"""Vector, 16:9 CGM slides from saved statistics and exact selected samples."""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from io import BytesIO
import json
import math
from pathlib import Path
from deepyield.compute.release_export_session import export_input, export_catalog
from xml.sax.saxutils import escape

import numpy as np
from reportlab.lib.colors import HexColor
from deepyield.compute.release_pdf_canvas import ReportCanvas as Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.lib.styles import ParagraphStyle
from reportlab.platypus import Paragraph

from deepyield.compute.release_cgm_excel import _cg_state, _summary, _site_values, _cpk_state, _diagnostic, _CONFIG
from deepyield.compute.release_cpk_excel import _identifier, _report_filename
from deepyield.compute.release_control import report_progress, checkpoint, timed, stage
from deepyield.compute.release_numeric import cg_number, require_current_cg_policy
from deepyield.compute.release_cgm_mr import moving_range_metrics, TOLERANCE_FRACTION
from deepyield.rules.conditions import as_float

WIDTH, HEIGHT = 1152, 648  # 16 x 9 inches, vector PDF (150 dpi -> 2400 x 1350).
BLUE, LIGHT, LANE = '#005691', '#F4F8FB', '#F1F5F9'
COLORS = {'Pass': '#74B230', 'Need to check': '#F0EA00', 'Fail': '#EA0000'}
TEXT = '#193449'
PDF_LAYOUT_VERSION = 'cgm-mr-20pct-1'
SHIFT_COLOR = '#76528B'


def prepare_cgm_pdf(report):
    """Use the shared Excel policy, preserving Infinity and propagating NaN."""
    if report.get('report_type') != 'release_cgm':
        raise ValueError('需要 Release CGM 报告')
    require_current_cg_policy(report)
    policy = json.loads(_CONFIG.read_text(encoding='utf-8'))['thresholds']
    sites = sorted(set(report.get('sites') or []) | {
        s['site'] for i in report.get('items', []) for s in i.get('sites', [])})
    if not 1 <= len(sites) <= 16:
        raise ValueError('CGM PDF 支持 1 到 16 个活动 Site')
    rows, excluded = [], 0
    for item in report.get('items', []):
        low, high = (as_float(item.get(k)) for k in ('effective_lsl', 'effective_usl'))
        stats = item.get('sites') or []
        if (low is None or high is None or low >= high or item.get('record_type') == 'FTR'
                or item.get('status', '').startswith(('NOT_', 'INSUFFICIENT'))):
            excluded += 1
            continue
        normalized = []
        for site in stats:
            cg = _site_values(item, site)[4]
            if site.get('n', 0) >= 2 and (cg_number(cg) is not None or cg == 'NaN'):
                normalized.append({**site, 'cg': cg, 'state': _cg_state(cg, policy)})
        if not normalized or any(as_float(s.get('std')) is None or s['std'] < 0 for s in normalized):
            excluded += 1
            continue
        cgs = [s['cg'] for s in normalized]
        cpk = as_float(item.get('supported_by_bin1_cpk'))
        minimum, state, result = _summary(cgs, cpk, policy)
        def order(site):
            value = cg_number(site['cg'])
            return value if value is not None else -math.inf
        worst = min(normalized, key=order)
        valid = [cg for cg in cgs if cg_number(cg) is not None]
        diagnostic = _diagnostic(item, report, {}, policy)
        rows.append({**item, 'sites': normalized, 'statistics_sites': [dict(site) for site in stats], 'minimum': minimum,
                     'minimum_state': state,
                     'maximum': max(valid, key=cg_number) if valid else 'NaN', 'worst_site': worst['site'],
                     'max_std': max(s['std'] for s in normalized), 'result': result,
                     'cpk': cpk, 'cpk_state': _cpk_state(cpk, policy), 'low': low, 'high': high,
                     'remark': diagnostic.get('remark', diagnostic.get('auto_remark', '')) or ''})
    actions = sorted((r for r in rows if r['result'] != 'Pass'),
                     key=lambda r: (r['result'] != 'Fail', cg_number(r['minimum']) is None,
                                    cg_number(r['minimum']) or 0))
    return {'sites': sites, 'rows': rows, 'actions': actions, 'excluded': excluded,
            'counts': dict(Counter(r['result'] for r in rows))}


def _fmt(value):
    if value is None:
        return '--'
    if value == 'Infinity':
        return 'Infinity'
    if isinstance(value, (float, np.floating)):
        return f'{value:.4g}'
    return str(value)


def _cg_display(value):
    text = _fmt(value)
    numeric = as_float(value)
    if numeric is not None:
        # Do not print a red 0.99999997 as "1", or a yellow 1.3299999 as "1.33".
        for boundary in (1., 1.33):
            if numeric < boundary <= float(text):
                return f'<{boundary:.2f}'
    return text


class Slides:
    def __init__(self, canvas, stamp):
        self.c = canvas
        self.stamp = stamp
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))

    def box(self, x, y, w, h, color, radius=0):
        self.c.setFillColor(HexColor(color))
        self.c.roundRect(x, y, w, h, radius, stroke=0, fill=1)

    def text(self, x, y, value, size=10, color=TEXT, bold=False, width=None):
        value = _fmt(value)
        font = 'STSong-Light' if any(ord(c) > 255 for c in value) else 'Helvetica-Bold' if bold else 'Helvetica'
        # Wrap table cells; elsewhere shorten exceptionally long identifiers.
        if width:
            if pdfmetrics.stringWidth(value, font, size) > width:
                low, high = 0, max(0, len(value) - 1)
                while low < high:
                    middle = (low + high + 1) // 2
                    if pdfmetrics.stringWidth(value[:middle] + '...', font, size) <= width:
                        low = middle
                    else:
                        high = middle - 1
                value = value[:low] + '...'
        self.c.setFont(font, size)
        self.c.setFillColor(HexColor(color))
        self.c.drawString(x, y, value)

    def line(self, x1, y1, x2, y2, color, dash=None, width=.7):
        self.c.setStrokeColor(HexColor(color))
        self.c.setLineWidth(width)
        self.c.setDash(dash or [])
        self.c.line(x1, y1, x2, y2)
        self.c.setDash([])

    def dots(self, points, radius, separate=False):
        # One fill per bounded batch, preserving every circle and its exact radius.
        path = self.c.beginPath()
        count = 0
        for index, (x, y) in enumerate(points):
            if index % 2048 == 0:
                checkpoint()
            if separate:
                self.c.circle(x, y, radius, stroke=0, fill=1)
                continue
            path.circle(x, y, radius)
            count += 1
            if count == 2048:
                self.c.drawPath(path, stroke=0, fill=1, fillMode=1)
                path, count = self.c.beginPath(), 0
        if count:
            self.c.drawPath(path, stroke=0, fill=1, fillMode=1)

    def header(self, title, stamp, page, total, badge=None):
        bottom = .89 if badge else .90
        self.box(0, HEIGHT * bottom, WIDTH, HEIGHT * (1 - bottom), BLUE)
        self.text(30, 617, title, 19, '#FFFFFF', True, width=865 if badge is None else 850)
        self.text(30, 592, stamp if badge else 'Part 1 - Global Summary', 9, '#FFFFFF', width=970)
        self.text(925, 596 if badge else 617, self.stamp, 8, '#FFFFFF', width=205)
        self.text(1015, 586, f'{page} / {total}', 10, '#FFFFFF')
        if badge:
            self.box(909, 607, 222, 27, COLORS[badge], 6)
            self.text(920, 616, 'RESULT: ' + badge.upper(), 11,
                      '#FFFFFF' if badge == 'Fail' else TEXT, True)

    def remark(self, value, x, width, top=560, max_height=120):
        """Bound the note below Result; leave the remaining column for statistics."""
        value = value or '--'
        font = 'STSong-Light' if any(ord(char) > 255 for char in value) else 'Helvetica'
        style = ParagraphStyle('Report remark', fontName=font, fontSize=9, leading=12,
                               textColor=HexColor(TEXT), splitLongWords=True)
        paragraph = Paragraph(escape(value).replace('\n', '<br/>'), style)
        text_width, available = width - 16, max_height - 36
        _, height = paragraph.wrap(text_width, available)
        overflow = height > available
        if overflow:
            parts = paragraph.split(text_width, available)
            paragraph = parts[0] if parts else None
            height = paragraph.wrap(text_width, available)[1] if paragraph else 0
        bottom = top - 20 - height - (17 if overflow else 8)
        self.box(x, bottom, width, top - bottom + 5, LIGHT, 4)
        self.text(x + 8, top - 9, 'Remark', 10, BLUE, True)
        if paragraph:
            paragraph.drawOn(self.c, x + 8, top - 20 - height)
        if overflow:
            self.text(x + 8, bottom + 5, 'Full remark in PDF annotation.', 8)
            self.c.textAnnotation(value, Rect=(x + width - 20, bottom + 3, x + width - 4, bottom + 19),
                                  Name='Comment', Color=(0, .34, .57))
        return bottom - 18

    def table(self, headers, rows, widths, x, top, row_h, fills=None, font=9, wrap_columns=(1,)):
        # Header height is independent so two-line labels cannot overlap rows.
        header_h = 30
        positions = np.cumsum([x] + widths[:-1]).tolist()
        for pos, width, label in zip(positions, widths, headers):
            self.box(pos, top - header_h, width, header_h, BLUE)
            for j, line in enumerate(label.split('\n')):
                self.text(pos + 4, top - 12 - j * 10, line, font, '#FFFFFF', True, width=width - 8)
        for i, values in enumerate(rows):
            y = top - header_h - (i + 1) * row_h
            for j, (pos, width, value) in enumerate(zip(positions, widths, values)):
                fill = (fills or {}).get((i, j), LIGHT if i % 2 == 0 else '#FFFFFF')
                self.box(pos, y, width, row_h, fill)
                # Two lines for test names without shrinking the required font.
                text = _fmt(value)
                if '\n' in text:
                    lines = [line.strip() for line in text.splitlines() if line.strip()]
                    if j in wrap_columns and row_h >= 27 and len(lines) > 1:
                        self.text(pos + 4, y + row_h / 2 + 2, lines[0], font, width=width - 8)
                        self.text(pos + 4, y + row_h / 2 - 9,
                                  lines[1] + (' ...' if len(lines) > 2 else ''), font, width=width - 8)
                        continue
                    text = ' '.join(lines)
                if j in wrap_columns and pdfmetrics.stringWidth(text, 'Helvetica', font) > width - 8 and row_h >= 27:
                    cut = max(1, int(len(text) * (width - 8) / max(1, pdfmetrics.stringWidth(text, 'Helvetica', font))))
                    self.text(pos + 4, y + row_h / 2 + 2, text[:cut], font, width=width - 8)
                    self.text(pos + 4, y + row_h / 2 - 9, text[cut:], font, width=width - 8)
                else:
                    self.text(pos + 4, y + row_h / 2 - 3, text, font, width=width - 8)


def _summary_slide(s, data, metadata, device, stamp, index, total):
    title = ' - '.join(part for part in [device, 'CGM Analysis Report (Executive Summary)'] if part)
    s.header(title, stamp, index + 1, total)
    if index == 0:
        mir = (metadata.get('stdf_metadata') or {}).get('mir') or {}
        def field(key, raw):
            values = sorted({str(r.get('conditions', {}).get(key)) for r in data['rows']
                             if r.get('conditions', {}).get(key) is not None})
            return ', '.join(values) or mir.get(key, mir.get(raw, '--'))
        runs = sorted({int(site['n']) for r in data['rows'] for site in r['sites']})
        s.box(29, 461, 510, 99, LIGHT, 5)
        device_label = f'Device ID: {device}   |   ' if device else ''
        lines = [f'{device_label}Lot ID: {field("lot_id", "LOT_ID")}',
                 f'Test Temp: {field("test_temperature", "TST_TEMP")}   |   Tester Name: {field("node_name", "NODE_NAM")}',
                 'Active Sites: ' + ', '.join(f'S{x}' for x in data['sites']),
                 f'Runs per Site: {", ".join(map(str, runs)) or "--"}   |   Total Quantitative Tests: {len(data["rows"])}']
        for j, line in enumerate(lines):
            s.text(39, 543 - j * 21, line, 10, width=490)
        counts, count = data['counts'], len(data['rows'])
        for j, (label, state) in enumerate([('Total Evaluated', None), ('PASS', 'Pass'),
                                           ('Need to Check', 'Need to check'), ('FAIL', 'Fail')]):
            x = 553 + j * 145
            s.box(x, 461, 137, 99, COLORS.get(state, LIGHT), 8)
            s.text(x + 10, 536, label, 11, bold=True)
            n = count if state is None else counts.get(state, 0)
            s.text(x + 10, 500, n, 25, bold=True)
            if state:
                s.text(x + 10, 477, f'{n / count * 100 if count else 0:.1f}%', 11)
    chunk = data['actions'][index * 12:(index + 1) * 12]
    top = 437 if index == 0 else 548
    s.text(30, top, 'Part 2 - Action List | Fail first, then Need to check; ascending min Cg', 11, bold=True)
    small = len(data['sites']) <= 4
    headers = ['Test Nr.', 'Test Name', 'Unit', 'Lowlim', 'Uplim']
    widths = [58, 210 if small else 210, 42, 65, 65]
    if small:
        headers += [f'S{x} cg*' for x in data['sites']]
        widths += [200 / len(data['sites'])] * len(data['sites'])
    else:
        headers += ['Worst Site', 'Max StdDev', 'min Cg', 'Max Cg']
        widths += [70, 84, 70, 70]
    headers += (['min Cg'] if small else []) + ['Bin1 CPK', 'Result', 'Remark']
    widths += ([75] if small else []) + [80, 112, 240]
    widths = [w * 1092 / sum(widths) for w in widths]
    rows, fills = [], {}
    for i, row in enumerate(chunk):
        values = [row.get('test_num'), row.get('test_name'), row.get('unit'), row['low'], row['high']]
        if small:
            lookup = {site['site']: site for site in row['sites']}
            for site in data['sites']:
                entry = lookup.get(site)
                if entry and entry['state']:
                    fills[i, len(values)] = COLORS[entry['state']]
                values.append(_cg_display(entry['cg']) if entry else None)
        else:
            values += [f'S{row["worst_site"]}', row['max_std']]
            if row['minimum_state']:
                fills[i, len(values)] = COLORS[row['minimum_state']]
            values += [_cg_display(row['minimum']), _cg_display(row['maximum'])]
        if small:
            if row['minimum_state']:
                fills[i, len(values)] = COLORS[row['minimum_state']]
            values.append(_cg_display(row['minimum']))
        if row['cpk_state']:
            fills[i, len(values)] = COLORS[row['cpk_state']]
        values += [row['cpk'], row['result'], row['remark']]
        fills[i, len(values) - 2] = COLORS[row['result']]
        rows.append(values)
    s.table(headers, rows, widths, 30, top - 14, 28, fills, 9, wrap_columns=(1, len(headers) - 1))
    if not chunk:
        s.text(45, top - 80, 'No Fail or Need to check items.', 16, BLUE, True)
    s.text(30, 18, f'Part 1 - Global Summary | Excluded non-evaluable / status tests: {data["excluded"]}   |   Actions: {len(data["actions"])}', 9)


def _plot(s, rect, title, sites, values, kind, row, *, grand_mean=None, mr_data=None):
    x, y, w, h = rect
    s.text(x, y + h + 7, title, 11, BLUE, True)
    n = len(sites)
    tol = row['high'] - row['low']
    if kind == 'mr':
        lower = 0
        upper = max([mr_data[0]['spike_ucl']] +
                    [float(np.nanmax(m['values'][1:])) for m in mr_data if len(m['values']) > 1]) * 1.22
    elif kind == 'raw':
        lower = min([row['low']] + [min(v) for v in values if len(v)])
        upper = max([row['high']] + [max(v) for v in values if len(v)])
        pad = (upper - lower) * .1 or 1
        lower, upper = lower - pad, upper + pad
    else:
        offsets = [abs(site['mean'] - grand_mean) for site in sites
                   if site['n'] and site.get('mean') is not None and grand_mean is not None]
        extent = max([.12 * tol] + offsets +
                     [max(abs(v - site['mean'])) for site, v in zip(sites, values) if len(v)]) * 1.15
        lower, upper = -extent, extent
    def yy(value):
        return y + (value - lower) / (upper - lower) * h
    for i in range(n):
        if (i + 1) % 2 == 0:
            s.box(x + i * w / n, y, w / n, h, LANE)
    if kind == 'centered':
        yellow, green = TOLERANCE_FRACTION / 2 * tol, TOLERANCE_FRACTION / 2 * tol / 1.33
        # Disjoint bands keep every fill at alpha .15, including the green center.
        for low, high, color in ((-yellow, -green, '#f1c40f'), (green, yellow, '#f1c40f'),
                                 (-green, green, '#2ecc71')):
            s.c.saveState()
            s.c.setFillColor(HexColor(color))
            s.c.setFillAlpha(.15)
            s.c.rect(x, yy(low), w, yy(high) - yy(low), stroke=0, fill=1)
            s.c.restoreState()
        s.line(x, yy(0), x + w, yy(0), BLUE, [2, 2])
    for tick in np.linspace(lower, upper, 4):
        s.line(x, yy(tick), x + w, yy(tick), '#DAE3EA', width=.4)
        s.text(x - 45, yy(tick) - 3, tick, 8)
    if kind == 'raw':
        for bound, label in ((row['low'], 'LSL'), (row['high'], 'USL')):
            s.line(x, yy(bound), x + w, yy(bound), COLORS['Fail'], [4, 3])
            s.text(x + w + 4, yy(bound) + 3, label, 8, COLORS['Fail'])
    for i, (site, data) in enumerate(zip(sites, values)):
        start, lane = x + i * w / n, w / n
        s.text(start + lane / 2 - 8, y - 14, f'S{site["site"]}', 8)
        color = '#34495e' if kind == 'mr' else (COLORS['Fail'] if site['state'] == 'Fail'
            else '#D98200' if site['state'] == 'Need to check' else (BLUE if i % 2 == 0 else '#4198CF'))
        data = (mr_data[i]['values'] if kind == 'mr' else
                data - site['mean'] if kind == 'centered' else data)
        xs = np.linspace(start + lane * .08, start + lane * .92, len(data))
        s.c.setStrokeColor(HexColor(color))
        s.c.setLineWidth(.65)
        s.c.setDash([])
        path = s.c.beginPath()
        connected = False
        for j, (px, val) in enumerate(zip(xs, data)):
            if j % 2048 == 0:
                checkpoint()
            if not np.isfinite(val):
                connected = False
                continue
            (path.lineTo if connected else path.moveTo)(px, yy(val))
            connected = True
        s.c.drawPath(path)
        s.c.setFillColor(HexColor(color))
        s.dots(((px, yy(val)) for px, val in zip(xs, data) if np.isfinite(val)), 1.25)
        if kind == 'centered' and site['n'] and grand_mean is not None:
            shift = yy(site['mean'] - grand_mean)
            s.line(start, shift, start + lane, shift, SHIFT_COLOR, [5, 3], width=1.5)
        if kind == 'raw' and site['n']:
            s.line(start + lane * .05, yy(site['mean']), start + lane * .95, yy(site['mean']), BLUE, width=1.2)
            s.text(start + lane / 2 - 8, y + h - 9, f'S{site["site"]}', 8)
        if kind == 'mr' and mr_data[i]['average'] is not None:
            mean_y = yy(mr_data[i]['average'])
            s.line(start + lane * .05, mean_y, start + lane * .95, mean_y, '#34495e', width=1.5)
    if kind == 'mr':
        for limit, label, color in ((mr_data[0]['target_limit'], 'MR Avg Limit (Cg=1.33)', '#218c4a'),
                                     (mr_data[0]['spike_ucl'], 'Spike UCL (3.27*Limit)', '#c0392b')):
            s.line(x, yy(limit), x + w, yy(limit), color, [5, 3], width=1.2)
            label_width = pdfmetrics.stringWidth(label, 'Helvetica', 8)
            s.box(x + w - label_width - 7, yy(limit) + 1, label_width + 7, 10, '#FFFFFF')
            s.text(x + w - label_width - 3, yy(limit) + 3, label, 8, color)
    s.line(x, y, x + w, y, BLUE)


def _mean_shift(mean, shift):
    """Use matching decimal places, with an explicit sign on the shift."""
    if mean is None:
        return None, None
    scientific = any(value and (abs(value) >= 1e5 or abs(value) < 1e-3) for value in (mean, shift))
    return (f'{mean:.4e}', f'{shift:+.4e}') if scientific else (f'{mean:.4f}', f'{shift:+.4f}')


def _detail_samples(row, samples, active_sites):
    normalized = {site['site']: site for site in row['sites']}
    lookup = {site['site']: {**site, 'cg': None, 'state': None}
              for site in row.get('statistics_sites', row['sites'])}
    lookup.update(normalized)
    sites = [lookup.get(site, {'site': site, 'n': 0, 'mean': 0, 'std': None,
                              'cg': None, 'state': None}) for site in active_sites]
    grouped = {}
    for index, point in enumerate(samples):
        if index % 2048 == 0:
            checkpoint()
        if point['site'] not in active_sites:
            raise ValueError('PDF 样本包含未选择的 Site，请重新计算')
        grouped.setdefault(point['site'], []).append(point)
    values = []
    for site in sites:
        points = grouped.get(site['site'], [])
        # Production resolvers supply the full record order. Simple external
        # providers without sequence fields retain their supplied order.
        has_sequence = [p.get('record_sequence') is not None for p in points]
        if any(has_sequence):
            if not all(has_sequence):
                raise ValueError('PDF 样本缺少测量顺序，无法计算 MR，请重新计算')
            points = sorted(points, key=lambda p: (p['record_sequence'], p.get('part_index', 0)))
        data = np.asarray([p['value'] for p in points], dtype=float)
        if len(data) != site['n'] or not np.isfinite(data).all():
            raise ValueError('PDF 图形样本与已保存的 Site 统计不一致，请重新计算')
        values.append(data)
    return sites, values


@timed('export.pdf.draw')
def _detail_slide(s, row, samples, stamp, page, total, active_sites):
    states = Counter(site['state'] for site in row['sites'])
    subtitle = (f'Unit: {row.get("unit") or "--"}  |  LSL: {_fmt(row["low"])}  |  USL: {_fmt(row["high"])}  |  '
                f'Tolerance: {_fmt(row["high"] - row["low"])}  |  min Cg: {_cg_display(row["minimum"])}  |  '
                f'Bin1 CPK: {_fmt(row["cpk"])}  |  Sites Pass/IR/Fail: {states["Pass"]}/{states["Need to check"]}/{states["Fail"]}')
    s.header(f'Test #{row.get("test_num")} - {row.get("test_name")}', subtitle, page, total, row['result'])
    sites, values = _detail_samples(row, samples, active_sites)
    all_values = np.concatenate(values)
    grand_mean = float(all_values.mean())
    mr_data = [moving_range_metrics(data, row['high'] - row['low']) for data in values]
    _plot(s, (58, 402, 640, 134), '1  Measurement Sequence by Site', sites, values, 'raw', row)
    _plot(s, (58, 227, 640, 131), '2  Within-Site Deviation + Site Shift', sites, values, 'centered', row, grand_mean=grand_mean)
    s.line(58, 202, 80, 202, SHIFT_COLOR, [5, 3], width=1.5)
    s.text(85, 199, 'Site Shift (Mean - Grand Mean)', 8, SHIFT_COLOR)
    s.text(285, 199, 'Dots: Value - Site Mean; bands: +/-0.10 Tol, /1.33.', 8)
    _plot(s, (58, 45, 640, 131), '3. Moving Range |x[i] - x[i-1]| by Site (Short-Term Noise & Spike Check)',
          sites, values, 'mr', row, mr_data=mr_data)
    stats_top = s.remark(row['remark'], 729, 400)
    s.text(729, stats_top, '4  Site Statistics', 11, BLUE, True)
    rows, fills = [], {}
    for i, (site, mr) in enumerate(zip(sites, mr_data)):
        mean, shift = _mean_shift(site.get('mean') if site['n'] else None,
                                  site['mean'] - grand_mean if site['n'] else None)
        rows.append([f'S{site["site"]}', site['n'], mean, shift, site['std'], _cg_display(site['cg']),
                     _cg_display(mr['cg']),
                     {'Pass': 'Pass', 'Need to check': 'IR', 'Fail': 'Fail'}.get(site['state'], '--')])
        if site['state']:
            fills[i, 5] = fills[i, 7] = COLORS[site['state']]
    i = len(rows)
    mr_cgs = [m['cg'] for m in mr_data if m['cg'] is not None]
    min_mr_cg = min(mr_cgs, key=cg_number) if mr_cgs else None
    mean, shift = _mean_shift(grand_mean, 0.)
    rows.append(['Min/All', len(all_values), mean, shift, float(all_values.std(ddof=1)),
                 _cg_display(row['minimum']), _cg_display(min_mr_cg),
                 {'Need to check': 'IR'}.get(row['result'], row['result'])])
    if row['minimum_state']:
        fills[i, 5] = COLORS[row['minimum_state']]
    fills[i, 7] = COLORS[row['result']]
    widths = [36, 32, 60, 64, 54, 49, 56, 49]
    table_top = stats_top - 15
    row_h = min(28, 300 / len(rows), (table_top - 30 - 65) / len(rows))
    s.table(['Site', 'Count', 'Mean', 'Shift', 'StdDev', 'Cg*', 'Cg(MR)', 'Status'], rows,
            widths, 729, table_top, row_h, fills, 8)
    s.text(729, 55, 'Shift = Site Mean - Grand Mean. Cg / Cg(MR): 20% Tol.', 8)
    s.text(729, 43, 'Cg(MR) is diagnostic only; Status uses existing Cg / CPK rules.', 8)
    s.text(729, 31, 'Min/All: pooled statistics; minimum site Cg* and Cg(MR).', 8)
    s.text(58, 16, 'MR: solid line = site average; sigma(MR) = MR average / 1.128.', 8)
    s.text(729, 19, f'Part 3 | {stamp}', 8)


def render_cgm_pdf(report, *, sample_provider, metadata=None, device_id=None, generated_at=None, output=None):
    """Return PDF bytes; sample_provider(item) must resolve exact run samples."""
    report_progress('准备 CGM PDF', 0)
    data = prepare_cgm_pdf(report)
    metadata = metadata or {}
    # Native parser uses normalized MIR keys; accept raw MIR keys in external data.
    mir = (metadata.get('stdf_metadata') or {}).get('mir') or {}
    device = _identifier(report.get('items', []), metadata, 'part_type', device_id or mir.get('PART_TYP'), 'Device ID')
    moment = generated_at or datetime.now().astimezone()
    summary_pages = max(1, math.ceil(len(data['actions']) / 12))
    total = summary_pages + len(data['actions'])
    stamp = moment.strftime('%Y-%m-%d %H:%M:%S %Z')
    buffer = output if output is not None else BytesIO()
    canvas = Canvas(buffer, pagesize=(WIDTH, HEIGHT), pageCompression=1)
    canvas.setTitle(' - '.join(part for part in [device, 'CGM Analysis Report'] if part))
    canvas.setAuthor('Datalog Check')
    canvas.setSubject('CGM selected statistics; ' + json.dumps({'source': report.get('source'),
        'selection': report.get('selection'), 'bin1_cpk_source': report.get('bin1_cpk_source'),
        'cg_tolerance_fraction': TOLERANCE_FRACTION, 'sigma_mr_divisor': 1.128}, ensure_ascii=False))
    slides = Slides(canvas, stamp)
    for index in range(summary_pages):
        report_progress(f'生成 CGM PDF：第 {index + 1} / {total} 页', 20 + 75 * index / total)
        _summary_slide(slides, data, metadata, device, stamp, index, total)
        canvas.showPage()
    for index, row in enumerate(data['actions']):
        report_progress(f'生成 CGM PDF：第 {summary_pages + index + 1} / {total} 页', 20 + 75 * (summary_pages + index) / total)
        _detail_slide(slides, row, sample_provider(row), stamp, summary_pages + index + 1, total, data['sites'])
        canvas.showPage()
    report_progress('正在封装 CGM PDF', 95)
    with stage('export.pdf.package', '正在封装 CGM PDF'):
        canvas.save()
    report_progress('CGM PDF 已生成', 100)
    return {'filename': _report_filename(moment, device, 'Cgm_Report.pdf'), 'content': buffer.getvalue() if output is None else None,
            'page_count': total, 'evaluated': len(data['rows']), 'action_count': len(data['actions']),
            'excluded_count': data['excluded']}


def export_release_cgm_pdf(input_path, output_dir, *, device_id=None, rule_dir=None, **selection):
    """Standalone export from parsed Release Parquet, retaining its selection."""
    from deepyield.compute.release_stdf import read_release_wide_all
    from deepyield.compute.release_cgm import build_release_cgm_report
    from deepyield.compute.release_chart_samples import ReportSampleResolver
    from deepyield.rules.loader import load_analysis_rules
    frame, metadata = export_input(Path(input_path), read_release_wide_all)
    selection = {'part_indices': None, 'sites': None, 'test_columns': None, 'minimum_samples_per_site': None, **selection}
    report = build_release_cgm_report(frame, metadata, catalog=export_catalog(rule_dir), **selection)
    result = render_cgm_pdf(report, metadata=metadata, device_id=device_id,
        sample_provider=ReportSampleResolver(frame, metadata['source_sha256'], identifiers=False))
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / result['filename']
    with output.open('xb') as stream:
        stream.write(result['content'])
    return {key: value for key, value in result.items() if key != 'content'} | {'path': str(output.resolve())}
