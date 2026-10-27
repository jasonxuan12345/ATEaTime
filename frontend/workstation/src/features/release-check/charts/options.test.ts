import { brushPick, chartOption, matches, orderedSites } from './options';
import { demoChart } from './demo';
import { demoRun, startingDemoSelections } from '../model';
import type { ChartSettings } from './types';
import { matrixCsv } from './matrixExport';
import { demoMatrix } from './demo';

const selections = startingDemoSelections(), row = demoRun(selections).rows[0];
const settings: ChartSettings = { sites: null, sort: 'site', fullSpec: true, stdMode: false, connect: false, wafer: '', outOfSpec: false, selected: {} };

it('shows infinite Cg with an infinity label and a finite illustrative bar', () => {
  const data = demoChart(row, 'cgm', selections);
  data.sites[0] = { ...data.sites[0], std: 0, cg: 'Infinity', cg_status: 'PASS' };
  const option = chartOption(data, 'cg', settings) as any;
  expect(Number.isFinite(option.series[0].data[0].value)).toBe(true);
  expect(option.series[0].data[0].label.formatter).toBe('∞');
  expect(option.series[0].data[0].name).toContain('Cg ∞');
  expect(option.series[0].data[0].name).toContain('柱高仅示意');
  const sites = orderedSites(data, { ...settings, sort: 'cg' });
  expect(sites.at(-1)?.cg).toBe('Infinity');
});

it('colors real MMSA Std by its reference, independently of unavailable report Cg', () => {
  const original = demoChart(row, 'mmsa', selections);
  const data = { ...original, std_threshold: 2, sites: [
    { ...original.sites[0], std: 1, cg: null, cg_status: 'NOT_CALCULABLE' },
    { ...original.sites[1], std: 3, cg: null, cg_status: 'NOT_CALCULABLE' },
    { ...original.sites[0], site: 99, std: null, cg: null, cg_status: 'NOT_CALCULABLE' },
  ] };
  const option = chartOption(data, 'cg', { ...settings, stdMode: true }) as any;
  expect(option.series[0].data[0].itemStyle.borderColor).not.toBe('#bd474b');
  expect(option.series[0].data[1].itemStyle.borderColor).toBe('#bd474b');
  expect(option.series[0].data[0].name).toContain('Std 未超参考限');
  expect(option.series[0].data[0].name).not.toContain('NOT_CALCULABLE');
  expect(option.series[0].data[2].name).toContain('Std 无法计算');
});

it('preserves selected unknown sites during full-data brushing', () => {
  const data = demoChart(row, 'cgm', selections);
  expect(brushPick(data, 'sequence', { ...settings, sites: [-1] }, [[0, 100], [1, 2]])?.selection?.sites).toEqual([-1]);
});

it('keeps a real zero lower limit and full spec view even for a narrow data distribution', () => {
  const data = demoChart(row, 'cpk', selections);
  data.statistics.lsl = 0; data.statistics.usl = 1e6; data.statistics.spec_valid = true;
  const option = chartOption(data, 'histogram', settings) as any;
  expect(option.xAxis.min).toBeLessThanOrEqual(0);
  expect(option.xAxis.max).toBeGreaterThanOrEqual(1e6);
  expect(option.series[0].markLine.data.some((line: any) => line.xAxis === 0 && line.name === 'LSL')).toBe(true);
});

it('sorts boxes by their actual mean while keeping all selected populations untouched', () => {
  const data = demoChart(row, 'cgm', selections);
  const before = JSON.stringify(data);
  const ordered = orderedSites(data, { ...settings, sort: 'mean' });
  expect(ordered.map(site => site.mean)).toEqual(ordered.map(site => site.mean).slice().sort((a, b) => a! - b!));
  chartOption(data, 'box', { ...settings, sites: [data.sites[0].site!] });
  expect(JSON.stringify(data)).toBe(before);
});

it('a histogram range selects the intended points without including the next bin boundary', () => {
  const data = demoChart(row, 'cpk', selections);
  const point = data.points[0];
  expect(matches(point, { value_min: point.value, value_max: point.value, value_max_exclusive: true })).toBe(false);
  const picked = brushPick(data, 'sequence', settings, [[point.record_sequence, point.record_sequence], [point.value, point.value]])!;
  expect(matches(point, picked.selection!)).toBe(true);
});

it('wafer brushing reverses the existing display flip exactly once to raw coordinates', () => {
  const data = demoChart(row, 'cgm', selections);
  data.wafer.display = { origin: 'corner', corner: 'top_left', flip_x: true, flip_y: false, applied: true };
  const wafer = data.wafer.wafers[0];
  wafer.points = [
    { ...wafer.points[0], x: 10, y: 0, raw_x: 0, raw_y: 0 },
    { ...wafer.points[1], x: 0, y: 5, raw_x: 10, raw_y: 5 },
  ];
  const pick = brushPick(data, 'wafer', settings, [[10, 10], [0, 0]])!;
  expect(pick.selection).toMatchObject({ x_min: 0, x_max: 0, y_min: 0, y_max: 0, wafer_key: [wafer.lot, wafer.wafer] });
});

it('box brushing preserves the value range and does not select the whole site', () => {
  const data = demoChart(row, 'cgm', selections), point = data.points[0];
  const index = orderedSites(data, settings).findIndex(site => site.site === point.site);
  const pick = brushPick(data, 'box', settings, [[index, index], [point.value, point.value]])!;
  expect(pick.selection).toMatchObject({ sites: [point.site], value_min: point.value, value_max: point.value });
  expect(matches(point, pick.selection!)).toBe(true);
  expect(matches({ ...point, value: point.value + 1 }, pick.selection!)).toBe(false);
});

it('wafer uses complete bounds and color range even if the sampled display omits extremes', () => {
  const data = demoChart(row, 'cgm', selections), wafer = data.wafer.wafers[0];
  wafer.bounds = { minX: -100, maxX: 100, minY: -50, maxY: 50 };
  data.wafer.value_range = [-10, 40];
  const option = chartOption(data, 'wafer', settings) as any;
  expect(option.xAxis.max).toBe(200.5);
  expect(option.visualMap).toMatchObject({ min: -10, max: 40 });
});

it('matrix export retains row and source identities and escapes text formula cells', () => {
  const matrix = demoMatrix(demoRun(selections), selections);
  matrix.rows[0].name = '=1+1';
  const csv = matrixCsv(matrix.rows, 'rules-v1');
  expect(csv).toContain('"\'=1+1"');
  expect(csv).toContain(matrix.rows[0].key);
  expect(csv).toContain('rules-v1');
  expect(csv.split('\r\n')).toHaveLength(1 + matrix.rows.reduce((sum, r) => sum + Math.max(1, r.sites.length), 0));
});
