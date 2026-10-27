import { t, localize, useLanguage } from "../../../i18n";
import type { EChartsOption, ScatterSeriesOption, YAXisComponentOption } from 'echarts';
import { format } from '../model';
import { orderedSites } from './options';
import { siteColor, siteName, type ChartData, type ChartSettings } from './types';
import { siteDeviationOption } from './siteDeviation';

export type Shape = 'density' | 'qq' | 'violin' | 'demean';
// Only sample shape is estimated here. Report statistics always come from the server.
export function density(values: number[]) {
  if (!values.length) return [];
  const mean = values.reduce((a, b) => a + b, 0) / values.length;
  const std = Math.sqrt(values.reduce((a, b) => a + (b - mean) ** 2, 0) / values.length);
  const h = Math.max(1.06 * std * values.length ** -.2, Math.abs(mean) * 1e-8, 1e-8);
  const low = Math.min(...values) - 3 * h, high = Math.max(...values) + 3 * h;
  return Array.from({ length: 81 }, (_, i) => {
    const x = low + (high - low) * i / 80;
    return [x, values.reduce((n, v) => n + Math.exp(-.5 * ((x - v) / h) ** 2), 0) / (values.length * h * Math.sqrt(2 * Math.PI))];
  });
}
function normalQuantile(p: number) {
  // Invert the normal CDF by bisection; stable at sample plotting positions.
  const cdf = (x: number) => { const t = 1 / (1 + .2316419 * Math.abs(x)); const tail = Math.exp(-x*x/2)/Math.sqrt(2*Math.PI)*t*(.319381530+t*(-.356563782+t*(1.781477937+t*(-1.821255978+t*1.330274429)))); return x >= 0 ? 1-tail : tail; };
  let a=-9,b=9; for(let i=0;i<45;i++){const m=(a+b)/2;if(cdf(m)<p)a=m;else b=m;}return(a+b)/2;
}
export function specificationOverlay(data: ChartData, fullSpec: boolean) {
  const { lsl, usl, spec_valid } = data.statistics;
  const limits = spec_valid ? [{ name: 'LSL', value: lsl }, { name: 'USL', value: usl }]
    .filter((limit): limit is { name: string; value: number } => limit.value !== null && Number.isFinite(limit.value)) : [];
  const axis: YAXisComponentOption = { type: 'value', scale: true };
  if (fullSpec && limits.length) {
    const bounds = (extent: { min: number; max: number }) => {
      const values = [extent.min, extent.max, ...limits.map(limit => limit.value)].filter(Number.isFinite);
      const min = Math.min(...values), max = Math.max(...values);
      const padding = (max - min) * .05 || Math.abs(min) * .05 || 1;
      return { min: min - padding, max: max + padding };
    };
    axis.min = extent => bounds(extent).min;
    axis.max = extent => bounds(extent).max;
  }
  const markLine: ScatterSeriesOption['markLine'] = {
    silent: true, symbol: 'none',
    lineStyle: { color: '#d32f2f', width: 2, type: 'dashed', opacity: 1 },
    label: { show: true, position: 'insideEndTop', color: '#d32f2f', fontWeight: 'bold', backgroundColor: '#fff', padding: [2, 3] },
    data: limits.map(limit => ({ name: limit.name, yAxis: limit.value,
      label: { formatter: `${limit.name}: ${format(limit.value, 4)}` } })),
  };
  return { axis, markLine };
}
export function sampleShape(data: ChartData, settings: ChartSettings, kind: Shape): EChartsOption {
  if(kind==='demean')return siteDeviationOption(data,settings);
  const sites=orderedSites(data,settings);
  const points=data.points.filter(p=>settings.sites===null||settings.sites.includes(p.site??-1));
  const base: EChartsOption={animation:false,grid:{top:25,left:46,right:18,bottom:38,containLabel:true},tooltip:{trigger:'item'},xAxis:{type:'value',scale:true},yAxis:{type:'value',scale:true},series:[]};
  if(kind==='qq'){
    const values=points.map(p=>p.value).sort((a,b)=>a-b);
    const limits=specificationOverlay(data,settings.fullSpec);
    return {...base,xAxis:{type:'value',name:t("正态分位")},yAxis:limits.axis,series:[{type:'scatter',symbolSize:3,data:values.map((v,i)=>[normalQuantile((i+.5)/values.length),v]),itemStyle:{color:'#52758d'},markLine:limits.markLine}]};
  }
  if(kind==='density')return {...base,series:[{type:'line',showSymbol:false,data:density(points.map(p=>p.value)),lineStyle:{color:'#52758d'},areaStyle:{opacity:.08}}]};
  const series: EChartsOption['series']=sites.map((site,index)=>{
    const curve=density(points.filter(p=>p.site===site.site).map(p=>p.value));const peak=Math.max(...curve.map(p=>p[1]),1e-12);
    return {type:'custom',name:siteName(site.site),data:curve.length?[[index,curve[0][0],curve[curve.length-1][0]]]:[],encode:{x:0,y:[1,2]},renderItem:(_params,api)=>{const polygon=[...curve.map(([v,d])=>api.coord([index-d/peak*.38,v])),...curve.slice().reverse().map(([v,d])=>api.coord([index+d/peak*.38,v]))];return {type:'polygon',shape:{points:polygon},style:{fill:siteColor(site.site),opacity:.45}};}};
  });
  const limits=specificationOverlay(data,settings.fullSpec);
  return {...base,xAxis:{type:'value',min:-.6,max:Math.max(.6,sites.length-.4),interval:1,axisLabel:{formatter:v=>sites[Number(v)]?siteName(sites[Number(v)].site):''}},yAxis:limits.axis,series:[...series,{type:'scatter',symbol:'rect',symbolSize:[14,3],data:sites.filter(s=>s.mean!==null).map(s=>[sites.indexOf(s),s.mean]),itemStyle:{color:'#111111'},markLine:limits.markLine}]};
}
