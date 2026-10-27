import { t, localize, useLanguage } from "../../../i18n";
import { forwardRef, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import type { EChartsOption } from 'echarts';
import { BaseEChart, type BaseEChartHandle } from '../../../components/charts/BaseEChart';
import { brushPick } from './options';
import type { ChartData, ChartKind, ChartSettings, Pick } from './types';
import s from './charts.module.css';

export type PlotHandle = { image(): string | null; option(): EChartsOption };
export const ReleasePlot = forwardRef<PlotHandle, { title: string; data: ChartData; kind: ChartKind;
  settings: ChartSettings; option: EChartsOption; onPick: (pick: Pick) => void; expanded?: boolean; compact?: boolean; sharedLegend?: boolean; onFullscreen: () => void }>(function ReleasePlot(
  { title, data, kind, settings, option, onPick, expanded, onFullscreen, compact = false, sharedLegend = false }, ref) {
  const chart = useRef<BaseEChartHandle>(null);
  const zoom = useRef<{ start: number; end: number } | null>(null);
  const [brushing, setBrushing] = useState(false);
  const effectiveOption = useMemo(() => ({ ...option, ...(compact ? { grid: { top: 24, left: 35, right: kind === 'wafer' ? 35 : 12, bottom: 24, containLabel: true }, toolbox: { show: false }, legend: { show: false }, dataZoom: [], visualMap: Array.isArray(option.visualMap) ? option.visualMap.map(v => ({...v,show:false})) : option.visualMap ? {...option.visualMap,show:false} : undefined } : sharedLegend ? { legend: { show: false }, grid: { top: 44, left: 45, right: 22, bottom: 36, containLabel: true } } : {}), ...(!compact ? {dataZoom: Array.isArray(option.dataZoom) ? option.dataZoom.map(z => ({ ...z, ...(zoom.current ?? {}) })) : option.dataZoom} : {}) }), [option, compact, sharedLegend, kind]);
  useEffect(() => {
    chart.current?.getInstance()?.dispatchAction({ type: 'takeGlobalCursor', key: 'brush', brushOption: { brushType: brushing ? 'rect' : false, brushMode: 'single' } });
  }, [brushing, effectiveOption]);
  useImperativeHandle(ref, () => ({ image: () => chart.current?.getDataURL({ backgroundColor: '#ffffff' }) ?? null, option: () => option }), [option]);
  useEffect(() => {
    const instance = chart.current?.getInstance();
    if (!instance) return;
    const brush = (event: unknown) => {
      const area = (event as { areas?: { coordRange?: number[][] }[] }).areas?.[0];
      if (!area?.coordRange) { onPick({ selection: {} }); return; }
      const pick = brushPick(data, kind, settings, area.coordRange);
      if (pick) onPick(pick);
    };
    instance.on('brushEnd', brush);
    const restore = () => { zoom.current = null; setBrushing(false); onPick({ selection: {} }); };
    instance.on('restore', restore);
    return () => { instance.off('brushEnd', brush); instance.off('restore', restore); };
  }, [data, kind, settings, onPick]);
  const cell = data.row[data.context.module];
  useEffect(() => { chart.current?.resize(); }, [expanded]);
  return <section className={`${s.plot} ${expanded ? s.fullscreen : ''}`} aria-label={localize(title)}>
    <div className={s.plotHeading}><h3>{localize(title)}</h3><div>
      <button aria-pressed={brushing} onClick={() => setBrushing(!brushing)}>{t("框选样本")}</button>
      <button onClick={() => { zoom.current = null; setBrushing(false); chart.current?.getInstance()?.dispatchAction({ type: 'restore' }); onPick({ selection: {} }); }}>{t("复位此图")}</button>
      <button onClick={onFullscreen}>{localize(expanded ? t("退出放大") : t("放大"))}</button></div></div>
    <div className={s.source} title={JSON.stringify(data.context, null, 2)}>
      <strong>{localize(data.context.module.toUpperCase())}</strong><span>{data.context.filename}</span>
      <span>{localize(data.context.sample_type)} {t(" · 有效 n=")}{localize(data.total_points)}</span>
    </div>
    <div className={s.canvas}>
      <BaseEChart ref={chart} ariaLabel={localize(title)} theme='light' option={effectiveOption}
        onDataZoom={event => { const value = event as { start?: number; end?: number; batch?: { start: number; end: number }[] };
          const z = value.batch?.[0] ?? value; if (z.start !== undefined && z.end !== undefined) zoom.current = { start: z.start, end: z.end }; }}
        onClick={event => { const pick = (event as { data?: { pick?: Pick } }).data?.pick; if (pick) onPick(pick); }} />
    </div>
    <p className={s.caption}>{localize(kind === 'wafer' ? data.wafer.policy : t("沿用本模块报告样本；框选只改变观察范围。"))}
      {localize(data.sampled && kind === 'sequence' ? t(" 显示 {0}/{1} 个抽样及当前页选中明细点，统计采用全量。", data.displayed_points, data.total_points) : '')}
      {localize(kind === 'wafer' && <span> {t(" · 当前晶圆显示 ")}{localize((data.wafer.wafers.find(w => w.key === settings.wafer) ?? data.wafer.wafers[0])?.points.length ?? 0)}/{localize((data.wafer.wafers.find(w => w.key === settings.wafer) ?? data.wafer.wafers[0])?.total_dies ?? 0)} {t(" 个位置；排除身份或坐标不全 ")}{localize(data.wafer.excluded_count)} {t(" 条。")}</span>)}
      {localize(kind === 'box' && data.sites.some(site => site.box.outlier_count > site.box.outliers.length) ? t(" 离群点仅抽样显示；各 site 悬停可见全量数量。") : '')}
      {localize(cell?.override ? t(" 按 Bin1 CPK 接受规则判为通过，Cg 原始值仍保留。") : '')}</p>
  </section>;
});
