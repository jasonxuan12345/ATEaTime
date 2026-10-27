import { t, localize, useLanguage } from "../../../i18n";
import { useEffect, useState } from 'react';
import { BaseEChart } from '../../../components/charts/BaseEChart';
import { request, type Module, type Row, type Run, type Selections } from '../model';
import { demoChart } from './demo';
import { chartOption } from './options';
import type { ChartData, ChartSettings } from './types';

export function OverviewThumbnail({row,module,run,selections,demo}:{row:Row;module:Module;run:Run;selections:Selections;demo:boolean}){
  useLanguage();
  const [data,setData]=useState<ChartData|null>(null);
  useEffect(()=>{const abort=new AbortController();setData(null);if(demo)setData(demoChart(row,module,selections));else void request<ChartData>(`/datasets/${run.dataset_id}/runs/${run.id}/items/${encodeURIComponent(row.key)}/charts/${module}?bins=24`,{signal:abort.signal}).then(value=>{if(!abort.signal.aborted && Array.isArray(value.sites) && value.statistics && value.context)setData(value);}).catch(()=>{});return()=>abort.abort();},[row,module,run,selections,demo]);
  if(!data)return null;
  const settings:ChartSettings={sites:null,sort:'site',fullSpec:true,stdMode:false,connect:false,wafer:'',outOfSpec:false,selected:{}};
  const option=chartOption(data,module==='cgm'?'cg':'histogram',settings);
  return <div style={{height:64,width:180,pointerEvents:'none'}}><BaseEChart ariaLabel={localize(t("{0} 问题缩略图", module))} theme='light' option={{...option,legend:{show:false},toolbox:{show:false},dataZoom:[],grid:{top:10,bottom:16,left:20,right:10},xAxis:{...(option.xAxis as object),axisLabel:{show:false},name:''},yAxis:{...(option.yAxis as object),axisLabel:{show:false},name:''}}}/></div>;
}
