import { demoDetail, type Module, type Row, type Run, type Selections } from '../model';
import type { ChartData, ChartSite, MatrixData, MatrixGroup, Sample } from './types';

function quantile(values: number[], q: number) { const index = (values.length - 1) * q, lo = Math.floor(index), hi = Math.ceil(index); return values[lo] + (values[hi] - values[lo]) * (index - lo); }
export function demoChart(row: Row, module: Module, selections: Selections): ChartData {
  const detail = demoDetail(row, selections).details[module];
  const lo = row.lsl, hi = row.usl;
  const mean = detail?.mean ?? ((lo ?? 0) + (hi ?? 2)) / 2;
  const std = detail?.std || Math.abs((hi ?? 2) - (lo ?? 0)) / 12 || 1;
  const selectedSites = selections[module].sites;
  const points: Sample[] = selectedSites.flatMap((site, j) => Array.from({ length: 50 }, (_, i) => ({
    id: `demo-${row.key}-${module}-${site}-${i}`, part_index: j * 50 + i,
    record_sequence: i * selectedSites.length + j, column: row.column, result_index: null, record_type: 'PTR',
    head: 1, site, value: mean + std * (Math.sin(i * 1.7) + .35 * j + (i < 2 && j === 0 ? 2 : 0)),
    lot: 'DEMO_LOT', wafer: 'DEMO_W01', x: (i % 10) + j * 10, y: Math.floor(i / 10), part_id: null })));
  const values = points.map(p => p.value), actualMean = values.length ? values.reduce((a, b) => a + b, 0) / values.length : 0;
  const actualStd = values.length > 1 ? Math.sqrt(values.reduce((sum, v) => sum + (v - actualMean) ** 2, 0) / (values.length - 1)) : null;
  const min = Math.min(...values, mean - std), max = Math.max(...values, mean + std);
  const edges = Array.from({ length: 25 }, (_, i) => min + (max - min) * i / 24);
  const sites: ChartSite[] = selectedSites.map(site => {
    const samples = points.filter(p => p.site === site), xs = samples.map(p => p.value).sort((a, b) => a - b);
    const m = xs.reduce((a, b) => a + b, 0) / xs.length;
    const sd = Math.sqrt(xs.reduce((sum, v) => sum + (v - m) ** 2, 0) / (xs.length - 1));
    const q1 = quantile(xs, .25), q3 = quantile(xs, .75), iqr = q3 - q1;
    const inside = xs.filter(v => v >= q1 - 1.5 * iqr && v <= q3 + 1.5 * iqr);
    const outliers = samples.filter(p => p.value < q1 - 1.5 * iqr || p.value > q3 + 1.5 * iqr);
    const cg = lo !== null && hi !== null && hi > lo && sd > 0 ? 0.20 * (hi - lo) / (6 * sd) : null;
    return { site, n: xs.length, mean: m, std: sd, cg, status: 'CALCULATED', reason: null, heads: [1],
      cg_status: cg === null ? 'NOT_CALCULABLE' : cg < 1 ? 'FAIL' : cg < 1.33 ? 'RISK' : 'PASS',
      histogram: edges.slice(0, -1).map((lower, i) => xs.filter(v => v >= lower && (i === 23 ? v <= edges[i + 1] : v < edges[i + 1])).length),
      box: { q1, median: quantile(xs, .5), q3, low: inside[0], high: inside.at(-1)!, outliers, outlier_count: outliers.length } };
  });
  const cp = lo !== null && hi !== null && hi > lo && actualStd ? (hi - lo) / (6 * actualStd) : null;
  const cpk = actualStd && (lo !== null || hi !== null) ? Math.min(lo === null ? Infinity : (actualMean - lo) / (3 * actualStd), hi === null ? Infinity : (hi - actualMean) / (3 * actualStd)) : null;
  return { context: { dataset_id: 'demo', run_id: 'demo-run', filename: 'DEMO_synthetic_points.stdf', source_sha256: 'DEMO',
    module, group_id: row.key, column: row.column, record_type: 'PTR', result_index: null, conditions: row.conditions,
    selection: selections[module], rules_hash: 'DEMO', sample_type: '合成示例点（非真实检测）', selection_policy: '仅示例图，不代表真实规则结果' }, row,
    statistics: { n: points.length, mean: actualMean, std: actualStd, cp, cpk: module === 'cpk' ? cpk : null,
      lsl: lo, usl: hi, spec_valid: !(lo !== null && hi !== null && lo >= hi), unit: row.unit },
    sites, histogram: { edges }, points, total_points: points.length, displayed_points: points.length, sampled: false,
    std_threshold: actualStd ? actualStd * 2.5 : null, thresholds: { cg_fail: 1, cg_pass: 1.33, var_red: 2.5 },
    status: row.status, reason: '示例图形采用合成点，与列表示例指标独立，不可用于判定', cpk_override_applied: false,
    excluded_counts: {}, empty_reason: null, quality: { mixed_head_sites: [], warnings: [] },
    wafer: { wafers: [{ key: 'demo-wafer', lot: 'DEMO_LOT', wafer: 'DEMO_W01', total_dies: points.length, sampled: false,
      duplicate_positions: 0, points: points.map(p => ({ ...p, x: p.x!, y: p.y!, raw_x: p.x!, raw_y: p.y!, count: 1 })) }],
      excluded_count: 0, display: null, display_note: '合成晶圆坐标，仅示例', policy: '合成示例点，非真实晶圆记录', empty_reason: null } };
}
export function demoMatrix(run: Run, selections: Selections): MatrixData {
  const groups: MatrixGroup[] = [];
  const rows = run.rows.map(row => {
    const data = demoChart(row, 'cgm', selections);
    const family = row.name.split('_')[0], rule = row.mmsa?.rule_id ?? '示例规则';
    const key = family + ':' + rule;
    let group = groups.find(g => g.key === key);
    if (!group) { group = { key, family, rule, keys: [], count: 0, bad_sites: [], basis: '合成示例组' }; groups.push(group); }
    group.keys.push(row.key); group.count += 1;
    return { key: row.key, name: row.name, number: row.number, group: key, family, rule, sites: data.sites,
      unit: row.unit, status: row.status, filename: data.context.filename, source_sha256: 'DEMO' };
  });
  return { rows, groups, total: rows.length, offset: 0, limit: 100, thresholds: { cg_pass: 1.33, cg_fail: 1, var_red: 2.5 } };
}