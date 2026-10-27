import { render, screen, within } from '@testing-library/react';
import { demoChart } from './demo';
import { demoRun, startingDemoSelections } from '../model';
import { siteDeviationOption, siteDifference } from './siteDeviation';
import { SiteStatistics } from './SiteStatistics';
import { workbenchOptions } from './workbenchOptions';
import type { ChartSettings } from './types';

const selections = startingDemoSelections(), row = demoRun(selections).rows[0];
const settings: ChartSettings = { sites: null, sort: 'site', fullSpec: true, stdMode: false,
  connect: false, wafer: '', outOfSpec: false, selected: {} };

function unequalSites() {
  const data = demoChart(row, 'cgm', selections);
  data.sites = data.sites.slice(0, 2);
  data.sites[0] = { ...data.sites[0], mean: 10, n: 2 };
  data.sites[1] = { ...data.sites[1], mean: 14, n: 6 };
  data.statistics.mean = 13; data.statistics.n = 8;
  data.points = [
    { ...data.points[0], site: data.sites[0].site, value: 11, record_sequence: 2 },
    { ...data.points[0], site: data.sites[0].site, value: 9, record_sequence: 1 },
    { ...data.points[0], site: data.sites[1].site, value: 14, record_sequence: 3 },
  ];
  data.sampled = true; data.displayed_points = 3; data.total_points = 8;
  return data;
}

it('retains within-site residuals and overlays offsets from the full weighted mean', () => {
  const data = unequalSites(), before = JSON.stringify(data);
  const option = siteDeviationOption(data, settings) as any;
  expect(option.series[0].data.map((point: any) => point.value[1])).toEqual([-1, 1]);
  expect(option.series[0].data.every((point: any) => point.value[0] >= -.35 && point.value[0] <= .35)).toBe(true);
  expect(option.series[1].data[0].value).toEqual([1, 0]);
  const offsets = option.series.at(-1);
  expect(offsets.symbol).toBe('emptyDiamond');
  expect(offsets.data.map((point: any) => point.value)).toEqual([[0, -3], [1, 1]]);
  expect(offsets.data[0].name).toContain('Grand Mean: 13.00000');
  const filtered = siteDeviationOption(data, { ...settings, sites: [data.sites[0].site!] }) as any;
  expect(filtered.series.at(-1).data[0].value[1]).toBe(-3);
  expect(JSON.stringify(data)).toBe(before);
});

it('shows unavailable offsets as missing instead of inventing zero values', () => {
  const data = unequalSites();
  const site = data.sites[0];
  expect(siteDifference(data, { ...site, n: 0 })).toBeNull();
  expect(siteDifference(data, { ...site, mean: null })).toBeNull();
  data.statistics.mean = null;
  expect(siteDifference(data, site)).toBeNull();
  expect((siteDeviationOption(data, settings) as any).series.at(-1).data).toEqual([]);
});

it('places Site Diff immediately after Mean in the screen and exported CGM tables', () => {
  const data = unequalSites();
  render(<SiteStatistics data={data} settings={settings} />);
  expect(screen.getAllByRole('columnheader').map(cell => cell.textContent)).toEqual(['site', 'n', 'Mean', 'Site Diff', 'Std', 'Cg', '状态']);
  expect(within(screen.getAllByRole('row')[1]).getAllByRole('cell')[3]).toHaveTextContent('-3.0000');
  const exported = workbenchOptions(data, 'cgm', 'cg', settings) as any[];
  expect(exported[2].series.at(-1).data.map((point: any) => point.value[1])).toEqual([-3, 1]);
  expect(exported[3].graphic.style.text).toMatch(/Mean\s+Site Diff\s+Std/);
  expect(exported[3].graphic.style.text).toContain('-3.0000');
});

it('keeps large offsets visible without changing statistics or adding another chart', async () => {
  const echarts = await vi.importActual<typeof import('echarts')>('echarts');
  echarts.setPlatformAPI({ measureText: text => ({ width: String(text).length * 6 }) });
  const data = unequalSites(); data.sites[0].mean = -500;
  const chart = echarts.init(null, undefined, { renderer: 'svg', ssr: true, width: 800, height: 300 });
  try {
    chart.setOption(siteDeviationOption(data, settings));
    const svg = chart.renderToSVGString();
    expect(svg).not.toMatch(/NaN|Infinity/);
    expect(svg).toContain('Site Diff');
    const model = (chart as any).getModel();
    expect(model.getComponent('yAxis').axis.scale.getExtent()[0]).toBeLessThanOrEqual(-513);
    expect(workbenchOptions(data, 'cgm', 'cg', settings)).toHaveLength(4);
  } finally { chart.dispose(); }
});
