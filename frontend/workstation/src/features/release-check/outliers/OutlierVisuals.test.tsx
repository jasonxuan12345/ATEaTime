import { act, fireEvent, render, screen, within, waitFor } from '@testing-library/react';
import { useState } from 'react';
import * as echarts from 'echarts';
import { ReferenceChart } from './ReferenceChart';
import { OutlierAnalysis } from './OutlierAnalysis';
import { allBaselines, allCandidates, integrateOutliers } from './model';
import { sourceA, reportFor, run } from './testFixtures';
import { focusPosition, hitsOption, matrixOption, waferOption, referenceOption, trendOption } from './chartOptions';
import { scoreText, SELECTED } from './display';

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('keeps wafer circle radii nonnegative while the chart container is temporarily zero-sized', () => {
  const option = waferOption([]) as any;
  const outline = option.series[0].renderItem({}, { getWidth: () => 0, getHeight: () => 0 });
  expect(outline.children[0].shape.r).toBe(0);
});

it('keeps the central distribution unchanged when a candidate is trillions of deviations away', () => {
  const source = reportFor(), baselines = allBaselines([source]), candidates = allCandidates([source]);
  const { rerender } = render(<ReferenceChart baselines={baselines} candidates={candidates} selectedKey={candidates[0].key} onSelect={vi.fn()} />);
  const box = screen.getByRole('img', { name: '相对各自同组中位数的偏离程度（稳健σ）' }).querySelector('rect[fill="#99D5D5"]')!;
  const width = box.getAttribute('width');
  const extreme = { ...candidates[0], hits: [{ ...candidates[0].hits[0], score: 6.36e11, value: 1.272e10 }] };
  rerender(<ReferenceChart baselines={baselines} candidates={[extreme]} selectedKey={extreme.key} onSelect={vi.fn()} />);
  expect(screen.getByRole('img', { name: '相对各自同组中位数的偏离程度（稳健σ）' }).querySelector('rect[fill="#99D5D5"]')).toHaveAttribute('width', width);
  const point = screen.getByRole('button', { name: /选择记录 41/ });
  expect(Number(point.getAttribute('cx'))).toBeGreaterThan(834);
  expect(Number(point.getAttribute('cx'))).toBeLessThan(984);
  expect(point).toHaveAttribute('fill', SELECTED);
  expect(point.querySelector('title')).toHaveTextContent('6.36e+11x');
  expect(screen.getByText('筛选上限 (+6.0x)')).toBeVisible();
  expect(focusPosition(6) - focusPosition(-6)).toBeCloseTo(1.36);
});

it('uses a log axis and short formatted numeric labels for concurrent hits', () => {
  const chip = structuredClone(allCandidates([reportFor()])[0]);
  chip.hits.push({ ...chip.hits[0], score: 6.36e11 });
  const option = hitsOption(chip, 6) as any;
  expect(option.xAxis).toMatchObject({ type: 'log', logBase: 10 });
  expect(option.series[0].label.position).toBe('right');
  expect(option.xAxis.max).toBe(1e12);
  expect(option.series[0].label.formatter({ value: 45.512345 })).toBe('45.5x');
  expect(scoreText(6.36e11)).toBe('6.36e+11x');
});

it('keeps every candidate in the trend even when normal points have been sampled', () => {
  const candidates = allCandidates([reportFor()]);
  const option = trendOption([{ dataset_id: candidates[0].dataset_id, report_id: 'r', group_id: candidates[0].hits[0].group_id,
    dies: [], points: [], sampled: true, total_points: 30000 }], candidates, candidates[0].key) as any;
  expect(option.series.find((series: any) => series.name === '本测项异常候选记录').data).toHaveLength(1);
  expect(option.series.find((series: any) => series.name === '选中外圈').data[0].candidateKey).toBe(candidates[0].key);
});

it('renders a complete discrete matrix with short unique identifiers and a red selected row', () => {
  const source = reportFor(), candidates = allCandidates([source]);
  const other = { ...candidates[0], key: 'another:41', dataset_id: 'another', filename: 'very-long-original-name.stdf', hits: [] };
  const option = matrixOption([...candidates, other], allBaselines([source]), other.key) as any;
  expect(option.visualMap.type).toBe('piecewise');
  expect(option.visualMap.pieces.map((piece: any) => piece.color)).toEqual(['#F1F5F9', '#FDE047', '#FB923C', '#DC2626']);
  expect(option.yAxis.data.every((label: string) => !label.includes('.stdf'))).toBe(true);
  expect(new Set(option.yAxis.data).size).toBe(2);
  expect(option.xAxis.axisLabel.formatter('LONG_TEST_NAME_12345')).toBe('LONG_TEST_NA...');
  expect(option.series[0].data[1].itemStyle).toMatchObject({ borderColor: SELECTED, borderWidth: 2.5 });
  expect(option.series[0].data[1].name).toContain('very-long-original-name.stdf');
  expect(option.series[0].data[1].name).toContain('不等于已评估通过');
});

it('keeps tested dies, current-test candidates, and the selected crosshair in separate wafer layers', async () => {
  const candidates = allCandidates([reportFor()]);
  const option = waferOption(candidates, candidates[0].key, [{ lot: 'L1', wafer: 'W1', x: 0, y: 0 }]) as any;
  expect(option.series.map((series: any) => series.name)).toEqual(['Wafer 轮廓', '全部测试 Die', '本测项全部异常候选记录', '当前选中芯片']);
  expect(option.series[1].data).toHaveLength(1);
  const echarts = await vi.importActual<typeof import('echarts')>('echarts');
  echarts.setPlatformAPI({ measureText: text => ({ width: String(text).length * 7 }) });
  const chart = echarts.init(null, undefined, { renderer: 'svg', ssr: true, width: 600, height: 320 });
  try {
    chart.setOption(option);
    const svg = new DOMParser().parseFromString(chart.renderToSVGString(), 'image/svg+xml');
    const circle = svg.querySelector('[stroke="#94A3B8"]');
    expect(circle).not.toBeNull();
    expect(svg.querySelector('[fill="#E2E8F0"]')).not.toBeNull();
    expect(svg.querySelector('[fill="#EF4444"]')).not.toBeNull();
    expect(svg.documentElement.outerHTML).not.toMatch(/NaN|Infinity/);
    chart.setOption(referenceOption(allBaselines([reportFor()]), candidates, candidates[0].key), { notMerge: true });
    const exported = chart.renderToSVGString();
    expect(exported).toContain('Spec USL');
    expect(exported).toContain('筛选上限');
    expect(exported).not.toMatch(/NaN|Infinity/);
  } finally { chart.dispose(); }
});

it('updates all four charts when a candidate is chosen from the matrix', async () => {
  const source = reportFor();
  source.report.chips.push({ ...source.report.chips[0], part_index: 42, x: 15, y: 10, max_score: 10000,
    hits: [{ ...source.report.chips[0].hits[0], score: 10000, value: 201 }] });
  const reports = [source], rows = integrateOutliers(run.rows, reports, [], false).filter(row => !!row.outlier) as any;
  const candidates = allCandidates(reports), target = candidates.find(c => c.part_index === 41)!;
  function Harness() {
    const [selected, setSelected] = useState(candidates[0].key);
    return <OutlierAnalysis rows={rows} rowKey={rows[0].key} reports={reports} issues={[]} selectedKey={selected} stale={false}
      onSelectRow={vi.fn()} onSelectChip={chip => setSelected(chip.key)} onClose={vi.fn()} embedded />;
  }
  render(<Harness />);
  const instances = vi.mocked(echarts.init).mock.results.map(result => result.value) as any[];
  const matrix = instances.find(instance => instance.setOption.mock.calls.some((call: any[]) => call[0].series?.[0]?.type === 'heatmap'));
  const click = matrix.on.mock.calls.find((call: any[]) => call[0] === 'click')[1];
  act(() => click({ data: { candidateKey: target.key } }));
  await waitFor(() => expect(screen.getByRole('status', { name: '当前 Outlier 选中状态' })).toHaveTextContent('8.2x'));
  const point = screen.getAllByRole('button', { name: /选择记录 41/ })[0];
  expect(point).toHaveAttribute('fill', SELECTED);
  const wafer = instances.find(instance => instance.setOption.mock.calls.some((call: any[]) => call[0].series?.[0]?.name === 'Wafer 轮廓'));
  expect(wafer.setOption.mock.calls.at(-1)[0].series[3].data[0].candidateKey).toBe(target.key);
  expect(matrix.setOption.mock.calls.at(-1)[0].series[0].data.find((item: any) => item.candidateKey === target.key).itemStyle.borderColor).toBe(SELECTED);
});
