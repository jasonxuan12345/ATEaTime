import { siteSequenceOption } from './siteSequence';
import { demoChart } from './demo';
import { demoRun, startingDemoSelections } from '../model';
import type { ChartSettings } from './types';

const selections = startingDemoSelections(), row = demoRun(selections).rows[0];
const settings: ChartSettings = { sites: null, sort: 'site', fullSpec: true, stdMode: false, connect: false, wafer: '', outOfSpec: false, selected: {} };

it('separates sites and sorts each site by its original sequence with independent mean and limit segments', () => {
  const data = demoChart(row, 'cgm', selections);
  data.points.reverse();
  const before = JSON.stringify(data);
  const option = siteSequenceOption(data, settings) as any;
  expect(option.series).toHaveLength(data.sites.length);
  option.series.forEach((series: any, index: number) => {
    const original = data.points.filter(point => point.site === data.sites[index].site).sort((a, b) => a.record_sequence - b.record_sequence);
    expect(series.data.map((point: any) => point.pick.selection.ids[0])).toEqual(original.map(point => point.id));
    expect(series.data.every((point: any) => point.value[0] >= index - .35 && point.value[0] <= index + .35)).toBe(true);
    const marks = series.markLine.data;
    expect(marks).toHaveLength(3);
    expect(marks[0][0].coord[1]).toBe(data.sites[index].mean);
    expect(marks[0][0].lineStyle).toMatchObject({ color: '#111111', type: 'solid' });
    expect(marks.slice(1).every((mark: any) => mark[0].lineStyle.color === '#d32f2f')).toBe(true);
    expect(marks.every((mark: any) => mark[0].coord[0] > index - .5 && mark[1].coord[0] < index + .5)).toBe(true);
  });
  expect(JSON.stringify(data)).toBe(before);
});

it('keeps the full report mean when filtering sites and never joins different sites', () => {
  const data = demoChart(row, 'cpk', selections);
  const site = data.sites[1];
  site.mean = 123;
  const option = siteSequenceOption(data, { ...settings, sites: [site.site!], connect: true }) as any;
  expect(option.series).toHaveLength(1);
  expect(option.series[0].type).toBe('line');
  expect(option.series[0].markLine.data[0][0].coord[1]).toBe(123);
  expect(option.series[0].name).toBe(`S${site.site}`);
});

it('draws a zero lower limit but does not invent a missing upper limit', () => {
  const data = demoChart(row, 'cpk', selections);
  data.statistics.lsl = 0; data.statistics.usl = null;
  const option = siteSequenceOption(data, settings) as any;
  expect(option.series[0].markLine.data.map((mark: any) => mark[0].name)).toEqual(['S1 Mean', 'S1 LSL']);
  expect(option.series[0].markLine.data[1][0].coord[1]).toBe(0);
});

it('renders black mean and red limit segments separately for each site', async () => {
  const echarts = await vi.importActual<typeof import('echarts')>('echarts');
  echarts.setPlatformAPI({ measureText: text => ({ width: String(text).length * 7 }) });
  const data = demoChart(row, 'cpk', selections);
  const option = siteSequenceOption(data, { ...settings, sites: [1, 2] });
  const chart = echarts.init(null, undefined, { renderer: 'svg', ssr: true, width: 900, height: 300 });
  try {
    chart.setOption(option);
    const svg = chart.renderToSVGString();
    const rendered = new DOMParser().parseFromString(svg, 'image/svg+xml');
    expect(rendered.querySelectorAll('path[stroke="#111111"]')).toHaveLength(2);
    expect(rendered.querySelectorAll('path[stroke="#d32f2f"][stroke-dasharray]')).toHaveLength(4);
    expect(rendered.documentElement.textContent).toContain('Mean');
    expect(rendered.documentElement.textContent).toContain('LSL');
    expect(rendered.documentElement.textContent).toContain('USL');
    expect(rendered.documentElement.textContent).toContain('S1');
    expect(rendered.documentElement.textContent).toContain('S2');
    expect(svg).not.toMatch(/NaN|Infinity/);
  } finally { chart.dispose(); }
});
