import { density, sampleShape } from './sampleShapes';
import { workbenchOptions } from './workbenchOptions';
import { demoChart } from './demo';
import { demoRun, startingDemoSelections } from '../model';
import type { ChartSettings } from './types';
const selections=startingDemoSelections(),run=demoRun(selections);
const settings:ChartSettings={sites:null,sort:'site',fullSpec:true,stdMode:false,connect:false,wafer:'',outOfSpec:false,selected:{}};
it('keeps constant samples finite and gives the density approximately unit area',()=>{
  const curve=density([2,2,2,2]);expect(curve.every(p=>p.every(Number.isFinite))).toBe(true);
  const area=curve.slice(1).reduce((sum,p,i)=>sum+(p[0]-curve[i][0])*(p[1]+curve[i][1])/2,0);expect(area).toBeCloseTo(1,2);expect(density([])).toEqual([]);
});
it('uses the server site mean for demeaning without changing the supplied report statistics',()=>{
  const data=demoChart(run.rows[0],'cgm',selections);data.sites[0].mean=1000;data.statistics.cpk=123;const before=JSON.stringify(data);
  const option=sampleShape(data,{...settings,sites:[data.sites[0].site??-1]},'demean');const series=option.series as {data:{value:number[]}[]}[];
  expect(series).toHaveLength(2);expect(series[0].data[0].value[1]).toBe(data.points.find(p=>p.site===data.sites[0].site)!.value-1000);expect(JSON.stringify(data)).toBe(before);
});
it('encodes violin value extents so values outside zero to one remain visible',()=>{
  const data=demoChart(run.rows[0],'mmsa',selections);const option=sampleShape(data,settings,'violin');const series=option.series as {data:number[][];encode?:unknown}[];
  expect(series[0].encode).toEqual({x:0,y:[1,2]});expect(series[0].data[0][2]).toBeGreaterThan(series[0].data[0][1]);
});
it('exports site sequence above each module main chart and retains the other evidence',()=>{
  for(const module of ['cpk','cgm','mmsa'] as const){const data=demoChart(run.rows[0],module,selections);data.wafer.wafers=[];const before=JSON.stringify(data.statistics);const options=workbenchOptions(data,module,module==='cpk'?'histogram':module==='cgm'?'cg':'sequence',settings);expect(options).toHaveLength(module==='cpk'?5:4);expect(JSON.stringify(data.statistics)).toBe(before);expect(JSON.stringify(options[0])).toContain('Mean');}
});

it.each(['qq', 'violin'] as const)('shows labeled red dashed limits on the measured-value axis of %s', kind => {
  const data = demoChart(run.rows[0], 'cpk', selections);
  data.statistics.lsl = 0; data.statistics.usl = 100; data.statistics.spec_valid = true;
  const before = JSON.stringify(data);
  const option = sampleShape(data, settings, kind) as any;
  const marker = option.series.find((series: any) => series.markLine)?.markLine;
  expect(marker.lineStyle).toMatchObject({ color: '#d32f2f', type: 'dashed', width: 2 });
  expect(marker.label).toMatchObject({ show: true, color: '#d32f2f' });
  expect(marker.data).toEqual([
    { name: 'LSL', yAxis: 0, label: { formatter: 'LSL: 0.0000' } },
    { name: 'USL', yAxis: 100, label: { formatter: 'USL: 100.0000' } },
  ]);
  const extent = { min: 40, max: 41 };
  expect(option.yAxis.min(extent)).toBeLessThan(0);
  expect(option.yAxis.max(extent)).toBeGreaterThan(100);
  expect(JSON.stringify(data)).toBe(before);
});

describe.each(['qq', 'violin'] as const)('%s unavailable and single-sided limits', kind => {
  it.each([
    [0, null, true, ['LSL']], [null, 100, true, ['USL']],
    [null, null, true, []], [100, 0, false, []],
  ])('only draws available valid limits: LSL=%s USL=%s valid=%s', (lsl, usl, valid, names) => {
    const data = demoChart(run.rows[0], 'cpk', selections);
    data.statistics.lsl = lsl as number | null;
    data.statistics.usl = usl as number | null;
    data.statistics.spec_valid = valid as boolean;
    const option = sampleShape(data, settings, kind) as any;
    const marker = option.series.find((series: any) => series.markLine)?.markLine;
    expect(marker.data.map((line: any) => line.name)).toEqual(names);
    if (!(names as string[]).length) {
      expect(option.yAxis.min).toBeUndefined();
      expect(option.yAxis.max).toBeUndefined();
    }
  });

  it('keeps the data-detail view available without forcing distant limits into its range', () => {
    const data = demoChart(run.rows[0], 'cpk', selections);
    const option = sampleShape(data, { ...settings, fullSpec: false }, kind) as any;
    expect(option.yAxis.min).toBeUndefined();
    expect(option.yAxis.max).toBeUndefined();
    expect(option.series.find((series: any) => series.markLine).markLine.data).toHaveLength(2);
  });
});

it.each(['qq', 'violin'] as const)('renders visible limit lines and labels for %s even far outside the data', async kind => {
  const echarts = await vi.importActual<typeof import('echarts')>('echarts');
  echarts.setPlatformAPI({ measureText: text => ({ width: String(text).length * 7 }) });
  const data = demoChart(run.rows[0], 'cpk', selections);
  data.statistics.lsl = -100; data.statistics.usl = 100; data.statistics.spec_valid = true;
  const chart = echarts.init(null, undefined, { renderer: 'svg', ssr: true, width: 800, height: 460 });
  try {
    chart.setOption(sampleShape(data, settings, kind));
    const svg = chart.renderToSVGString();
    const rendered = new DOMParser().parseFromString(svg, 'image/svg+xml');
    expect(rendered.querySelector('parsererror')).toBeNull();
    expect(rendered.documentElement.textContent).toContain('LSL: -100.0000');
    expect(rendered.documentElement.textContent).toContain('USL: 100.0000');
    expect(rendered.querySelectorAll('path[stroke="#d32f2f"][stroke-dasharray]')).toHaveLength(2);
    expect(svg).not.toMatch(/NaN|Infinity/);
  } finally { chart.dispose(); }
});

it('includes the same limit overlays in exported Q-Q and violin charts', () => {
  for (const [module, index] of [['cpk', 2], ['mmsa', 1]] as const) {
    const data = demoChart(run.rows[0], module, selections);
    const option = workbenchOptions(data, module, 'sequence', settings)[index] as any;
    expect(option.series.find((series: any) => series.markLine).markLine.data.map((line: any) => line.name)).toEqual(['LSL', 'USL']);
  }
});
