import { t, localize, useLanguage } from "../../../i18n";
import type { EChartsOption } from 'echarts';
import { format } from '../model';
import { matches, orderedSites } from './options';
import { specificationOverlay } from './sampleShapes';
import { siteColor, siteName, type ChartData, type ChartSettings } from './types';

// Each site has its own horizontal segment. Positions follow execution order;
// the tooltip retains the original execution sequence and sample identity.
export function siteSequenceOption(data: ChartData, settings: ChartSettings): EChartsOption {
  const sites = orderedSites(data, settings), stats = data.statistics;
  const selected = Object.keys(settings.selected).length > 0;
  const series = sites.map((site, index) => {
    const points = data.points.filter(point => point.site === site.site).slice()
      .sort((a, b) => a.record_sequence - b.record_sequence || a.part_index - b.part_index);
    const marks = [
      ...(site.mean !== null ? [{ name: 'Mean', value: site.mean, color: '#111111', type: 'solid' }] : []),
      ...(stats.spec_valid ? [{ name: 'LSL', value: stats.lsl, color: '#d32f2f', type: 'dashed' },
        { name: 'USL', value: stats.usl, color: '#d32f2f', type: 'dashed' }] : []),
    ].filter(mark => mark.value !== null && Number.isFinite(mark.value));
    return {
      name: siteName(site.site), type: settings.connect ? 'line' : 'scatter', symbolSize: 4,
      lineStyle: { width: 1, color: siteColor(site.site) },
      itemStyle: { color: siteColor(site.site) },
      data: points.map((point, order) => ({
        value: [index + (points.length > 1 ? (order / (points.length - 1) - .5) * .7 : 0), point.value],
        name: t("{0} · 次序 {1} · 记录 {2}\n{3} {4}", siteName(site.site), point.record_sequence, point.part_index, format(point.value, 5), stats.unit ?? ''),
        pick: { selection: { ids: [point.id] } },
        itemStyle: { opacity: !selected || matches(point, settings.selected) ? .9 : .15 },
      })),
      markLine: {
        silent: true, symbol: 'none',
        data: marks.map(mark => [{
          name: `${siteName(site.site)} ${mark.name}`, coord: [index - .39, mark.value],
          lineStyle: { color: mark.color, width: mark.name === 'Mean' ? 3 : 2, type: mark.type },
          label: { show: true, position: 'insideEndTop', formatter: `${mark.name} ${format(mark.value, 4)}`,
            color: mark.color, fontSize: 10, backgroundColor: '#fff', padding: [1, 2] },
        }, { coord: [index + .39, mark.value] }]),
      },
    };
  });
  return {
    animation: false, backgroundColor: '#fff',
    grid: { top: 24, bottom: 38, left: 55, right: 18, containLabel: true },
    tooltip: { confine: true, renderMode: 'richText', formatter: (event: unknown) =>
      (event as { data?: { name?: string } }).data?.name ?? '' },
    xAxis: { type: 'value', min: -.5, max: Math.max(.5, sites.length - .5), interval: 1,
      axisLabel: { customValues: sites.map((_, index) => index), formatter: (value: number) => sites[value] ? siteName(sites[value].site) : '' },
      axisTick: { show: false }, splitLine: { show: false } },
    yAxis: { ...specificationOverlay(data, settings.fullSpec).axis, name: stats.unit ?? t("测量值") },
    series,
  } as EChartsOption;
}
