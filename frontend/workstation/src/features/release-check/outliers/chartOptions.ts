import { t, localize, useLanguage } from "../../../i18n";
import type { EChartsOption } from 'echarts';
import { siteColor } from '../charts/types';
import { SELECTED, chipLabel, chipTooltip, scoreText, signedScore } from './display';
import { baselineKey, type Candidate, type SourceBaseline, type DiePosition, type OutlierView } from './model';

export const waferKey = (c: Pick<Candidate, 'dataset_id' | 'lot' | 'wafer'>) => JSON.stringify([c.dataset_id, c.lot, c.wafer]);
export function focusPosition(score: number, threshold = 6) {
  const focus = Math.min(Math.max(threshold, Number.EPSILON), 15), value = Math.min(Math.abs(score), 15);
  if (focus === 15) return Math.sign(score) * value / 15;
  const part = value <= focus ? value / focus * .68 : .68 + (value - focus) / (15 - focus) * .32;
  return Math.sign(score) * part;
}
export function hitsOption(chip: Candidate | null, threshold: number, currentGroup?: string): EChartsOption {
  const hits = chip?.hits.slice(0, 40) ?? [];
  const minimum = Math.min(1, threshold, ...hits.map(h => h.score).filter(v => v > 0)) / 2;
  const largest = Math.max(threshold, ...hits.map(h => h.score));
  const roundedMax = 10 ** Math.ceil(Math.log10(largest));
  return { animation: false, grid: { left: 104, right: 70, top: 16, bottom: 40 },
    tooltip: { trigger: 'item', confine: true, renderMode: 'richText', formatter: (event: any) => event.data?.name ?? '' },
    xAxis: { type: 'log', logBase: 10, min: Math.max(Number.MIN_VALUE, minimum), max: Number.isFinite(roundedMax) ? roundedMax : largest, name: t("偏离程度（稳健σ） (log10)"), nameLocation: 'middle', nameGap: 24,
      axisLabel: { formatter: (value: number) => scoreText(value), fontSize: 9 } },
    yAxis: { type: 'category', inverse: true, data: hits.map(h => h.test_name),
      axisLabel: { width: 94, overflow: 'truncate', fontSize: 10 } },
    series: [{ type: 'bar', barMaxWidth: 19, label: { show: true, position: 'right', distance: 5, fontSize: 10,
        formatter: (event: any) => scoreText(event.value) },
      data: hits.map(h => ({ value: h.score, candidateKey: chip!.key, groupId: h.group_id, baselineId: h.baseline_id,
        name: t("{0} {1}\n{2}\n偏离 {3} · 实测 {4} {5}", h.test_num ?? '—', h.test_name, chipTooltip(chip!), scoreText(h.score), h.value.toPrecision(6), h.unit ?? ''),
        itemStyle: { color: h.group_id === currentGroup ? SELECTED : '#FB923C', borderColor: SELECTED, borderWidth: 1.5 } })),
      markLine: { silent: true, symbol: 'none', label: { formatter: t("筛选 ") + scoreText(threshold), fontSize: 9 },
        data: [{ xAxis: threshold }], lineStyle: { color: '#0F766E', type: 'dashed' } } }] };
}
export function matrixOption(candidates: Candidate[], baselines: SourceBaseline[], selectedKey?: string): EChartsOption {
  const columns = [...new Map(baselines.map(b => [`${b.dataset_id}:${b.group_id}`, b])).entries()];
  const labels = candidates.map(chipLabel), duplicates = new Set(labels.filter((label, i) => labels.indexOf(label) !== i));
  const sourceIds = [...new Set(candidates.map(c => c.dataset_id))];
  const names = candidates.map((c, i) => labels[i] + (duplicates.has(labels[i]) ? ` #${c.part_index}/F${sourceIds.indexOf(c.dataset_id) + 1}` : ''));
  const data = candidates.flatMap((c, y) => columns.map(([, b], x) => {
    const hit = c.dataset_id === b.dataset_id ? c.hits.find(h => h.group_id === b.group_id) : undefined;
    return { value: [x, y, hit?.score ?? 0], candidateKey: c.key, groupId: b.group_id,
      name: t("{0}\n{1} {2}\n测项来源：{3}\n{4}", chipTooltip(c), b.test_num ?? '—', b.test_name, b.filename, hit ? t("偏离 ") + scoreText(hit.score) : c.dataset_id !== b.dataset_id ? t("不同来源，未比较；不等于已评估通过") : t("未检出异常记录；不等于已评估通过")),
      itemStyle: { borderColor: c.key === selectedKey ? SELECTED : '#fff', borderWidth: c.key === selectedKey ? 2.5 : .5 } };
  }));
  return { animation: false, tooltip: { confine: true, renderMode: 'richText', formatter: (event: any) => event.data?.name ?? '' },
    grid: { top: 6, bottom: 44, left: 54, right: 6 },
    xAxis: { type: 'category', data: columns.map(([, b]) => b.test_name), axisTick: { show: false },
      axisLabel: { interval: 0, rotate: 30, fontSize: 9, formatter: (label: string) => label.length > 12 ? label.slice(0, 12) + '...' : label } },
    yAxis: { type: 'category', data: names, inverse: true, axisTick: { show: false },
      axisLabel: { fontSize: 9, lineHeight: 11, formatter: (label: string, index: number) => {
        const short = label.replace(' (X:', '\n').replace(', Y:', ',').replace(')', '');
        return candidates[index]?.key === selectedKey ? `{selected|${short}}` : short;
      }, rich: { selected: { color: SELECTED, fontWeight: 'bold', fontSize: 9, lineHeight: 11 } } } },
    visualMap: { type: 'piecewise', show: false, dimension: 2, pieces: [
      { gte: 0, lt: 6, color: '#F1F5F9' }, { gte: 6, lt: 10, color: '#FDE047' },
      { gte: 10, lte: 50, color: '#FB923C' }, { gt: 50, color: '#DC2626' }] },
    series: [{ type: 'heatmap', data, emphasis: { itemStyle: { borderColor: SELECTED, borderWidth: 3 } } }] };
}
export function waferOption(candidates: Candidate[], selectedKey?: string, dies: DiePosition[] = []): EChartsOption {
  const located = candidates.filter(c => c.x !== null && c.y !== null);
  const overlapCounts = new Map<string, number>();
  for (const chip of located) {
    const key = `${chip.x}:${chip.y}`;
    overlapCounts.set(key, (overlapCounts.get(key) ?? 0) + 1);
  }
  const all = [...dies, ...located] as { x: number; y: number }[];
  const bounds = all.reduce((b, c) => ({ lowX: Math.min(b.lowX, c.x), highX: Math.max(b.highX, c.x), lowY: Math.min(b.lowY, c.y), highY: Math.max(b.highY, c.y) }), { lowX: Infinity, highX: -Infinity, lowY: Infinity, highY: -Infinity });
  const cx = all.length ? (bounds.lowX + bounds.highX) / 2 : 0;
  const cy = all.length ? (bounds.lowY + bounds.highY) / 2 : 0;
  const radius = all.reduce((r, c) => Math.max(r, Math.hypot(c.x - cx, c.y - cy)), 1) * 1.06;
  const layout = (api: any, x: number, y: number) => {
    const scale = Math.max(0, Math.min(api.getWidth(), api.getHeight()) - 34) / (2 * radius);
    return { x: api.getWidth() / 2 + (x - cx) * scale, y: api.getHeight() / 2 - (y - cy) * scale, r: radius * scale };
  };
  const selected = located.find(c => c.key === selectedKey);
  return { animation: false, grid: { left: 0, right: 0, top: 0, bottom: 0 },
    tooltip: { confine: true, renderMode: 'richText', formatter: (event: any) => event.data?.name ?? '' },
    xAxis: { type: 'value', min: cx - radius, max: cx + radius, show: false },
    yAxis: { type: 'value', min: cy - radius, max: cy + radius, show: false },
    series: [{ type: 'custom', name: t("Wafer 轮廓"), silent: true, data: [[cx, cy]], renderItem: (_: unknown, api: any) => {
      const p = layout(api, cx, cy), r = p.r;
      return { type: 'group', children: [{ type: 'circle', shape: { cx: p.x, cy: p.y, r }, style: { fill: 'none', stroke: '#94A3B8', lineWidth: 1 } },
        { type: 'polyline', shape: { points: [[p.x - 7, p.y + r - 1], [p.x, p.y + r - 8], [p.x + 7, p.y + r - 1]] }, style: { fill: '#fff', stroke: '#94A3B8', lineWidth: 1.5 } }] };
    } }, { type: 'custom', name: t("全部测试 Die"), silent: true, data: dies.map(c => [c.x, c.y]), renderItem: (_: unknown, api: any) => {
      const p = layout(api, api.value(0), api.value(1));
      return { type: 'circle', shape: { cx: p.x, cy: p.y, r: 2 }, style: { fill: '#E2E8F0' } };
    } }, { type: 'custom', name: t("本测项全部异常候选记录"), data: located.map(c => ({ value: [c.x, c.y], candidateKey: c.key, name: chipTooltip(c) + t("\n最大偏离程度（稳健σ） ") + scoreText(c.max_score) + t("\n该位置 {0} 条异常候选记录，完整记录见异常候选记录列表", overlapCounts.get(`${c.x}:${c.y}`)) })), renderItem: (_: unknown, api: any) => {
      const p = layout(api, api.value(0), api.value(1));
      return { type: 'circle', shape: { cx: p.x, cy: p.y, r: 3.5 }, style: { fill: 'rgba(245,158,11,0.7)' } };
    } }, { type: 'custom', name: t("当前选中芯片"), z: 10,
      data: selected ? [{ value: [selected.x, selected.y], candidateKey: selected.key, name: chipTooltip(selected) }] : [], renderItem: (_: unknown, api: any) => {
        const p = layout(api, api.value(0), api.value(1));
        return { type: 'group', children: [
          { type: 'circle', shape: { cx: p.x, cy: p.y, r: 12 }, style: { fill: 'none', stroke: SELECTED, lineWidth: 1.5 } },
          { type: 'line', shape: { x1: p.x - 20, x2: p.x + 20, y1: p.y, y2: p.y }, style: { stroke: SELECTED, lineWidth: 1 } },
          { type: 'line', shape: { x1: p.x, x2: p.x, y1: p.y - 20, y2: p.y + 20 }, style: { stroke: SELECTED, lineWidth: 1 } },
          { type: 'circle', shape: { cx: p.x, cy: p.y, r: 7 }, style: { fill: SELECTED, stroke: '#fff', lineWidth: 2 } }] };
      } }] } as EChartsOption;
}
export function trendOption(views: OutlierView[], candidates: Candidate[], selectedKey?: string): EChartsOption {
  const sites = [...new Set(views.flatMap(view => view.points.map(point => point.site)))];
  const points = views.flatMap(view => view.points.map(point => ({ ...point, dataset_id: view.dataset_id })));
  const series: any[] = sites.map(site => ({ type: 'scatter', name: 'S' + site, symbolSize: 3,
    itemStyle: { color: siteColor(site) }, data: points.filter(p => p.site === site).map(p => ({ value: [p.part_index, p.value], candidateKey: `${p.dataset_id}:${p.part_index}`,
      name: t("S{0} · 记录 #{1} · 次序 {2}\n实测 {3}", p.site, p.part_index, p.record_sequence, p.value.toPrecision(6)) })) }));
  const candidatePoints = candidates.flatMap(candidate => candidate.hits
    .filter(hit => views.some(view => view.dataset_id === candidate.dataset_id && view.group_id === hit.group_id))
    .map(hit => ({ value: [candidate.part_index, hit.value], candidateKey: candidate.key,
      name: chipTooltip(candidate) + t("\n偏离 ") + scoreText(hit.score) })));
  const selectedPoints = candidatePoints.filter(point => point.candidateKey === selectedKey);
  series.push({ type: 'scatter', name: t("本测项异常候选记录"), symbolSize: 7, z: 5, itemStyle: { color: '#F59E0B' }, data: candidatePoints },
    { type: 'scatter', name: t("选中外圈"), symbolSize: 22, z: 9, silent: true, itemStyle: { color: 'transparent', borderColor: SELECTED, borderWidth: 2 }, data: selectedPoints },
    { type: 'scatter', name: t("当前选中芯片"), symbolSize: 14, z: 10, itemStyle: { color: SELECTED, borderColor: '#fff', borderWidth: 2 }, data: selectedPoints });
  return { animation: false, grid: { left: 60, right: 52, top: 30, bottom: 50 }, legend: { top: 0 },
    tooltip: { confine: true, renderMode: 'richText', formatter: (event: any) => event.data?.name ?? '' },
    xAxis: { type: 'value', name: t("Part Index / 测试先后顺序") }, yAxis: { type: 'value', scale: true, name: t("测项值") },
    dataZoom: [{ type: 'inside', filterMode: 'none' }, { type: 'slider', height: 14, bottom: 4 }, { type: 'slider', yAxisIndex: 0, right: 3, width: 12, filterMode: 'none' }], series };
}
export function referenceOption(baselines: SourceBaseline[], candidates: Candidate[], selectedKey?: string): EChartsOption {
  const coordinate = (b: SourceBaseline, value: number) => focusPosition((value - b.median!) / b.robust_sigma!, b.threshold) * 15;
  const candidatePoints = candidates.flatMap(c => c.hits.flatMap(h => {
    const index = baselines.findIndex(b => b.dataset_id === c.dataset_id && b.baseline_id === h.baseline_id);
    if (index < 0) return [];
    const score = signedScore(h), position = Math.abs(score) > 15 ? Math.sign(score) * 18 : focusPosition(score, baselines[index].threshold) * 15;
    return [{ value: [position, index], candidateKey: c.key, name: chipTooltip(c) + t("\n真实偏离 ") + scoreText(h.score),
      symbolSize: c.key === selectedKey ? 14 : 7, itemStyle: { color: c.key === selectedKey ? SELECTED : '#F59E0B', borderColor: c.key === selectedKey ? '#fff' : undefined, borderWidth: 2 } }];
  }));
  return { animation: false, grid: { left: 80, right: 40, top: 40, bottom: 20 },
    tooltip: { renderMode: 'richText', formatter: (event: any) => event.data?.name ?? '' },
    xAxis: { type: 'value', min: -20, max: 20, show: false },
    yAxis: { type: 'category', data: baselines.map(b => `H${b.head}/S${b.site}`) },
    graphic: [{ type: 'text', right: 35, top: 8, style: { text: t("超出图示范围的点（>15x）"), fill: '#9A3412', fontSize: 11 } }],
    series: [{ type: 'custom', silent: true, data: baselines.map((b, i) => [i]), renderItem: (_: unknown, api: any) => {
      const b = baselines[api.value(0)], index = api.value(0);
      if (b.robust_sigma === null || b.robust_sigma <= 0 || b.median === null) return;
      const pos = (value: number) => api.coord([coordinate(b, value), index]);
      const low = pos(b.lower_limit!), high = pos(b.upper_limit!), q1 = pos(b.q1!), q3 = pos(b.q3!), median = pos(b.median);
      const line = (p: number[], color: string, dashed = false) => ({ type: 'line', shape: { x1: p[0], x2: p[0], y1: p[1] - 14, y2: p[1] + 14 }, style: { stroke: color, lineWidth: 1.5, lineDash: dashed ? [4, 3] : undefined } });
      const text = (p: number[], label: string, offset: number, color: string) => ({ type: 'text', x: p[0], y: p[1] + offset, style: { text: label, fill: color, align: 'center', fontSize: 9 } });
      return { type: 'group', children: [
        { type: 'line', shape: { x1: low[0], y1: low[1], x2: high[0], y2: high[1] }, style: { stroke: '#8DBBC0', lineWidth: 3 } },
        { type: 'rect', shape: { x: q1[0], y: q1[1] - 7, width: Math.max(1, q3[0] - q1[0]), height: 14 }, style: { fill: '#99D5D5' } },
        line(median, '#111827'), line(low, '#0F766E'), line(high, '#0F766E'),
        text(low, t("筛选下限 −") + scoreText(b.threshold), -25, '#0F766E'), text(high, t("筛选上限 +") + scoreText(b.threshold), -25, '#0F766E'),
        ...[b.lsl, b.usl].flatMap((value, i) => value === null ? [] : [line(pos(value), '#DC2626', true), text(pos(value), 'Spec ' + (i ? 'USL' : 'LSL'), 17, '#DC2626')]),
      ] };
    } }, { type: 'scatter', data: candidatePoints }, { type: 'scatter', silent: true, symbolSize: 22,
      data: candidatePoints.filter(point => point.candidateKey === selectedKey).map(point => ({ value: point.value })),
      itemStyle: { color: 'transparent', borderColor: SELECTED, borderWidth: 2 } }] } as EChartsOption;
}
export function baselinesForChip(chip: Candidate | null, baselines: SourceBaseline[]) {
  const ids = new Set(chip?.hits.map(h => `${chip.dataset_id}:${h.baseline_id}`));
  return baselines.filter(b => ids.has(baselineKey(b)));
}
