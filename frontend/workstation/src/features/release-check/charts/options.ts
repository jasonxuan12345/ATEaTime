import { t, localize, useLanguage } from "../../../i18n";
import type { EChartsOption } from 'echarts';
import { dieToGrid, gridToDie } from '../../../components/wafer/waferDisplay';
import { cgNumber, format } from '../model';
import { siteColor, siteName, type ChartData, type ChartKind, type ChartSettings, type Pick, type Sample, type Selection } from './types';

type Datum = { value: unknown; name?: string; pick?: Pick; itemStyle?: Record<string, unknown> };
export function matches(point: Sample, selection: Selection): boolean {
  if (selection.ids && !selection.ids.includes(point.id)) return false;
  if (selection.sites && !selection.sites.includes(point.site ?? -1)) return false;
  if (selection.wafer_key && (point.lot !== selection.wafer_key[0] || point.wafer !== selection.wafer_key[1])) return false;
  for (const [low, high, field] of [['value_min', 'value_max', 'value'], ['sequence_min', 'sequence_max', 'record_sequence'],
    ['x_min', 'x_max', 'x'], ['y_min', 'y_max', 'y']] as const) {
    const v = point[field];
    if (selection[low] !== undefined && (v === null || v < selection[low]!)) return false;
    if (selection[high] !== undefined && (v === null || v > selection[high]!)) return false;
  }
  return !(selection.value_max_exclusive && point.value === selection.value_max);
}
export function orderedSites(data: ChartData, settings: ChartSettings) {
  return data.sites.filter(s => settings.sites === null || settings.sites.includes(s.site ?? -1)).slice().sort((a, b) => {
    const av = settings.sort === 'site' ? a.site : settings.sort === 'cg' ? cgNumber(a.cg) : a[settings.sort];
    const bv = settings.sort === 'site' ? b.site : settings.sort === 'cg' ? cgNumber(b.cg) : b[settings.sort];
    return av === null ? bv === null ? 0 : 1 : bv === null ? -1 : av - bv;
  });
}
function pointName(p: Sample, unit: string | null) {
  return t("{0} · {1} {2}\n记录 {3} / 次序 {4}\n{5}", siteName(p.site), format(p.value, 5), unit ?? '', p.part_index, p.record_sequence, p.id);
}
function siteOpacity(data: ChartData, settings: ChartSettings, site: number | null) {
  if (!Object.keys(settings.selected).length) return 1;
  if (settings.selected.sites) return settings.selected.sites.includes(site ?? -1) ? 1 : .2;
  return data.points.some(p => p.site === site && matches(p, settings.selected)) ? 1 : .2;
}
function markLines(data: ChartData, axis: 'xAxis' | 'yAxis') {
  const stats = data.statistics;
  return { silent: true, symbol: 'none', label: { position: 'insideEndTop' }, data: [
    ...(stats.spec_valid ? [[stats.lsl, 'LSL'], [stats.usl, 'USL']] : []).filter(v => v[0] !== null).map(([v, n]) =>
      ({ [axis]: v, name: n, label: { formatter: `${n}: ${format(v as number, 4)}` }, lineStyle: { color: '#bd474b', type: 'dashed' } })),
    ...(stats.mean !== null ? [{ [axis]: stats.mean, name: 'Mean', label: { formatter: `Mean: ${format(stats.mean, 4)}` }, lineStyle: { color: '#087f88' } }] : [])] };
}
export function waferGeometry(data: ChartData, settings: ChartSettings) {
  const wafer = data.wafer.wafers.find(w => w.key === settings.wafer) ?? data.wafer.wafers[0];
  const xs = wafer?.points.map(p => p.x) ?? [0], ys = wafer?.points.map(p => p.y) ?? [0];
  const bounds = wafer?.bounds ?? { minX: Math.min(...xs), maxX: Math.max(...xs), minY: Math.min(...ys), maxY: Math.max(...ys) };
  return { wafer, bounds };
}
export function brushPick(data: ChartData, kind: ChartKind, settings: ChartSettings, range: number[][]): Pick | null {
  if (!range?.[0]?.length || !range?.[1]?.length) return null;
  const [x1, x2] = range[0].slice().sort((a, b) => a - b), [y1, y2] = range[1].slice().sort((a, b) => a - b);
  const sites = settings.sites;
  if (kind === 'histogram') return { selection: { value_min: x1, value_max: x2, ...(sites ? { sites } : {}) } };
  if (kind === 'sequence') return { selection: { sequence_min: x1, sequence_max: x2, value_min: y1, value_max: y2, ...(sites ? { sites } : {}) } };
  if (kind === 'wafer') {
    const { wafer, bounds } = waferGeometry(data, settings);
    if (!wafer) return null;
    const display = data.wafer.display;
    const raw = (col: number, row: number) => {
      const pos = gridToDie(col, row, bounds, display);
      return { x: display?.flip_x ? bounds.minX + bounds.maxX - pos.x : pos.x,
        y: display?.flip_y ? bounds.minY + bounds.maxY - pos.y : pos.y };
    };
    const a = raw(x1, y1), b = raw(x2, y2);
    return { selection: { wafer_key: [wafer.lot, wafer.wafer], x_min: Math.min(a.x, b.x), x_max: Math.max(a.x, b.x),
      y_min: Math.min(a.y, b.y), y_max: Math.max(a.y, b.y), ...(sites ? { sites } : {}) } };
  }
  const sitesInRange = orderedSites(data, settings).filter((_, index) => index >= Math.ceil(x1) && index <= Math.floor(x2));
  return { selection: { sites: sitesInRange.map(s => s.site ?? -1), ...(kind === 'box' ? { value_min: y1, value_max: y2 } : {}) } };
}
export function chartOption(data: ChartData, kind: ChartKind, settings: ChartSettings): EChartsOption {
  const sites = orderedSites(data, settings), stats = data.statistics;
  const label = stats.unit ?? t("单位未记录");
  const selected = Object.keys(settings.selected).length > 0;
  const base = { animation: false, aria: { enabled: true }, backgroundColor: '#ffffff',
    grid: { top: 76, left: 65, right: 30, bottom: 64, containLabel: true },
    tooltip: { confine: true, renderMode: 'richText', formatter: (p: unknown) => {
      const datum = (p as { data?: Datum }).data; return datum?.name ?? '';
    } }, legend: { type: 'scroll', selectedMode: false, top: 4, left: 0, right: 8 },
    toolbox: { right: 8, top: 28, feature: { dataZoom: { yAxisIndex: 'none' }, brush: { type: ['rect', 'clear'] }, restore: {}, saveAsImage: { pixelRatio: 2 } } },
    brush: { xAxisIndex: 0, yAxisIndex: 0, brushMode: 'single' },
    dataZoom: [{ type: 'inside', filterMode: 'none' }, { type: 'slider', filterMode: 'none', bottom: 8, height: 18 }] };
  if (kind === 'histogram') {
    const edges = data.histogram.edges;
    const lo = settings.fullSpec && stats.spec_valid ? Math.min(edges[0] ?? 0, stats.lsl ?? Infinity) : edges[0];
    const hi = settings.fullSpec && stats.spec_valid ? Math.max(edges.at(-1) ?? 1, stats.usl ?? -Infinity) : edges.at(-1);
    return { ...base, xAxis: { type: 'value', name: label, min: lo, max: hi }, yAxis: { type: 'value', name: t("频数"), minInterval: 1 },
      series: sites.map((site, index) => ({ name: siteName(site.site), type: 'bar', stack: 'frequency',
        itemStyle: { color: siteColor(site.site), opacity: siteOpacity(data, settings, site.site) }, markLine: index === 0 ? markLines(data, 'xAxis') : undefined,
        data: site.histogram.map((count, i) => ({ value: [(edges[i] + edges[i + 1]) / 2, count],
          name: t("{0}\n[{1}, {2}{3}\n频数 {4} / {5} ({6}%)", siteName(site.site), format(edges[i], 5), format(edges[i + 1], 5), i === edges.length - 2 ? ']' : ')', count, site.n, site.n ? (count / site.n * 100).toFixed(1) : 0),
          pick: { selection: { sites: [site.site ?? -1], value_min: edges[i], value_max: edges[i + 1], value_max_exclusive: i !== edges.length - 2 } } })) })) } as EChartsOption;
  }
  if (kind === 'cg') {
    const threshold = settings.stdMode ? data.std_threshold : data.thresholds.cg_pass;
    const cgCap = Math.max(data.thresholds.cg_pass * 1.4, ...sites.map(s => typeof s.cg === 'number' ? s.cg : 0));
    return { ...base, xAxis: { type: 'category', data: sites.map(s => siteName(s.site)) },
      yAxis: { type: 'value', name: settings.stdMode ? `Std (${label})` : 'Cg', min: 0 },
      series: [{ type: 'bar', markLine: { symbol: 'none', data: threshold === null ? [] : [{ yAxis: threshold,
        lineStyle: { color: '#bd474b', type: 'dashed' }, label: { formatter: `${settings.stdMode ? t("Std 参考") : t("Cg 达标")} ${format(threshold)}` } }] },
        data: sites.map(s => {
          const stdHigh = s.std !== null && threshold !== null && s.std > threshold;
          const state = settings.stdMode ? s.std === null ? t("Std 无法计算") : threshold === null ? t("无 Std 参考限") : stdHigh ? t("Std 超出参考限（不改判）") : t("Std 未超参考限（不改判）") : s.cg_status;
          const infinite = !settings.stdMode && s.cg === 'Infinity';
          return { value: settings.stdMode ? s.std : infinite ? cgCap : s.cg,
            ...(infinite ? { label: { show: true, position: 'top', formatter: '∞', color: '#111', fontSize: 18 } } : {}),
            name: `${siteName(s.site)}\n${settings.stdMode ? '' : 'Cg ' + format(s.cg) + ' · '}Std ${format(s.std, 5)} · n=${s.n}\n${infinite ? t("Std=0，所选记录无波动；柱高仅示意 ∞\n") : ''}${state}${!settings.stdMode && data.cpk_override_applied ? t(" · 按 Bin1 CPK 接受规则判为通过") : ''}\n${settings.stdMode ? '' : t(s.reason ?? '')}`,
            pick: { site: s.site }, itemStyle: { color: siteColor(s.site), opacity: siteOpacity(data, settings, s.site), borderColor: (settings.stdMode ? stdHigh : s.cg_status !== 'PASS') ? '#bd474b' : siteColor(s.site), borderWidth: 2 } };
        }) }] } as EChartsOption;
  }
  if (kind === 'box') {
    return { ...base, xAxis: { type: 'category', data: sites.map(s => siteName(s.site)) }, yAxis: { type: 'value', name: label, scale: true },
      series: [{ name: t("箱线"), type: 'boxplot', markLine: markLines(data, 'yAxis'), data: sites.map(s => ({
        value: s.n ? [s.box.low, s.box.q1, s.box.median, s.box.q3, s.box.high] : [],
        name: t("{0} · n={1}\nMean {2} · Std {3}\nQ1 {4} · Median {5} · Q3 {6}\n须线 {7} ~ {8}\n离群点 {9}", siteName(s.site), s.n, format(s.mean, 5), format(s.std, 5), format(s.box.q1, 5), format(s.box.median, 5), format(s.box.q3, 5), format(s.box.low, 5), format(s.box.high, 5), s.box.outlier_count),
        itemStyle: { color: siteColor(s.site) + '44', opacity: siteOpacity(data, settings, s.site), borderColor: siteColor(s.site) }, pick: { site: s.site } })) },
        { name: 'Mean', type: 'scatter', symbol: 'diamond', symbolSize: 9, data: sites.map((s, i) => ({ value: [i, s.mean], name: `${siteName(s.site)} Mean ${format(s.mean, 5)}`, pick: { site: s.site }, itemStyle: { color: siteColor(s.site) } })) },
        { name: t("箱线离群点（未排除）"), type: 'scatter', symbol: 'cross', symbolSize: 8,
          data: sites.flatMap((s, i) => s.box.outliers.map(p => ({ value: [i, p.value], name: pointName(p, stats.unit), pick: { selection: { ids: [p.id] } }, itemStyle: { color: '#bd474b', opacity: !selected || matches(p, settings.selected) ? 1 : .18 } }))) }] } as EChartsOption;
  }
  if (kind === 'sequence') {
    return { ...base, xAxis: { type: 'value', name: t("原始记录次序"), scale: true }, yAxis: { type: 'value', name: label, scale: true },
      series: sites.map((s, index) => ({ name: siteName(s.site), type: settings.connect ? 'line' : 'scatter', symbolSize: 7,
        markLine: index === 0 ? markLines(data, 'yAxis') : undefined,
        data: data.points.filter(p => p.site === s.site).map(p => ({ value: [p.record_sequence, p.value], name: pointName(p, stats.unit),
          pick: { selection: { ids: [p.id] } }, itemStyle: { color: siteColor(s.site), opacity: !selected || matches(p, settings.selected) ? .9 : .15 } })) })) } as EChartsOption;
  }
  const { wafer, bounds } = waferGeometry(data, settings);
  const points = (wafer?.points ?? []).filter(p => settings.sites === null || settings.sites.includes(p.site ?? -1));
  const allValues = data.wafer.wafers.flatMap(w => w.points.map(p => p.value));
  const colorRange = data.wafer.value_range ?? (allValues.length ? [Math.min(...allValues), Math.max(...allValues)] : [0, 1]);
  return { ...base, legend: undefined, xAxis: { type: 'value', name: t("显示列"), min: -.5, max: bounds.maxX - bounds.minX + .5 },
    yAxis: { type: 'value', name: t("显示行"), inverse: true, min: -.5, max: bounds.maxY - bounds.minY + .5 },
    visualMap: settings.outOfSpec ? undefined : { min: colorRange[0], max: colorRange[1] === colorRange[0] ? colorRange[1] + (Math.abs(colorRange[1]) * .01 || 1) : colorRange[1], dimension: 2,
      type: 'continuous', right: 0, top: 50, inRange: { color: ['#d9eff0', '#087f88', '#122a37'] } },
    series: [{ type: 'scatter', symbol: 'rect', symbolSize: Math.max(3, Math.min(22, 320 / Math.max(1, bounds.maxX - bounds.minX + 1), 250 / Math.max(1, bounds.maxY - bounds.minY + 1))),
      data: points.map(p => { const pos = dieToGrid(p.x, p.y, bounds, data.wafer.display); const fail = (stats.lsl !== null && p.value < stats.lsl) || (stats.usl !== null && p.value > stats.usl);
        return { value: [pos.col, pos.row, p.value], name: t("{0} / {1}\n原始 ({2}, {3})\n{4}\n该位置 {5} 条入选记录，显示最后一条", wafer!.lot, wafer!.wafer, p.raw_x, p.raw_y, pointName(p, stats.unit), p.count),
          pick: { selection: { wafer_key: [wafer!.lot, wafer!.wafer], x_min: p.raw_x, x_max: p.raw_x, y_min: p.raw_y, y_max: p.raw_y } },
          itemStyle: { ...(settings.outOfSpec ? { color: fail ? '#bd474b' : '#087f88' } : {}), opacity: !selected || matches({ ...p, x: p.raw_x, y: p.raw_y }, settings.selected) ? 1 : .15 } }; }) }] } as EChartsOption;
}
