import { t, localize, useLanguage } from "../../../i18n";
import type { EChartsOption } from 'echarts';
import { format, type Module } from '../model';
import { chartOption, orderedSites } from './options';
import { density, sampleShape } from './sampleShapes';
import { siteSequenceOption } from './siteSequence';
import { siteDifference } from './siteDeviation';
import { siteName, type ChartData, type ChartKind, type ChartSettings } from './types';

export function compactChartOption(option:EChartsOption):EChartsOption {
  return {...option,grid:{top:18,left:30,right:15,bottom:24,containLabel:true},legend:{show:false},toolbox:{show:false},dataZoom:[],visualMap:Array.isArray(option.visualMap)?option.visualMap.map(v=>({...v,show:false})):option.visualMap?{...option.visualMap,show:false}:undefined,
    series:Array.isArray(option.series)?option.series.map(series=>({...series,...('markLine' in series?{markLine:{...series.markLine,label:{show:false}}}:{})})):option.series};
}

export function histogramWithDensity(data: ChartData, settings: ChartSettings): EChartsOption {
  const option=chartOption(data,'histogram',settings);
  const values=data.points.filter(p=>settings.sites===null||settings.sites.includes(p.site??-1)).map(p=>p.value);
  return {...option,yAxis:[option.yAxis as object,{type:'value',show:false}],series:[...(Array.isArray(option.series)?option.series:[]),{type:'line',name:t("核密度（样本）"),data:density(values),yAxisIndex:1,showSymbol:false,lineStyle:{color:'#384f63',width:2}}]};
}
export function siteTableOption(data:ChartData, settings:ChartSettings):EChartsOption {
  const cgm = data.context.module === 'cgm';
  const lines=[`site      n          Mean          ${cgm ? 'Site Diff     ' : ''}Std          Cg`,...orderedSites(data,settings).map(s=>`${siteName(s.site).padEnd(8)}${String(s.n).padEnd(11)}${format(s.mean,4).padEnd(14)}${cgm ? format(siteDifference(data,s),4).padEnd(14) : ''}${format(s.std,4).padEnd(13)}${format(s.cg)}`)];
  if(cgm)lines.push(`Grand Mean: ${format(data.statistics.mean,4)} (all selected samples)`);
  return {animation:false,graphic:{type:'text',left:30,top:24,style:{text:lines.join('\n'),font:'16px Consolas, monospace',lineHeight:cgm?Math.min(28,400/lines.length):28,fill:'#344e60'}}};
}
export function workbenchOptions(data:ChartData,module:Module,main:ChartKind,settings:ChartSettings):EChartsOption[]{
  const sequence=siteSequenceOption(data,settings);
  if(module==='cpk')return [sequence,histogramWithDensity(data,settings),sampleShape(data,settings,'qq'),chartOption(data,'box',settings),data.wafer.wafers.length?chartOption(data,'wafer',settings):siteTableOption(data,settings)];
  if(module==='cgm')return [sequence,chartOption(data,'cg',settings),sampleShape(data,settings,'demean'),siteTableOption(data,settings)];
  return [sequence,sampleShape(data,settings,'violin'),chartOption(data,'histogram',settings),siteTableOption(data,settings)];
}
