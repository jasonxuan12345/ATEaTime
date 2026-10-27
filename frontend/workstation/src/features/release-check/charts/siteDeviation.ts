import { t, localize, useLanguage } from "../../../i18n";
import type { EChartsOption } from 'echarts';
import { format } from '../model';
import { orderedSites } from './options';
import { siteColor, siteName, type ChartData, type ChartSettings, type ChartSite } from './types';

export function siteDifference(data: ChartData, site: ChartSite): number | null {
  const grandMean = data.statistics.mean;
  return site.n > 0 && site.mean !== null && grandMean !== null
    && Number.isFinite(site.mean) && Number.isFinite(grandMean) ? site.mean - grandMean : null;
}

export function siteDeviationOption(data: ChartData, settings: ChartSettings): EChartsOption {
  const sites = orderedSites(data, settings);
  const pointsBySite = new Map<number | null, ChartData['points']>();
  for (const point of data.points) {
    const group = pointsBySite.get(point.site) ?? [];
    group.push(point); pointsBySite.set(point.site, group);
  }
  const series = sites.map((site, index) => {
    const points = (pointsBySite.get(site.site) ?? []).slice()
      .sort((a, b) => a.record_sequence - b.record_sequence || a.part_index - b.part_index);
    return { name: siteName(site.site), type: 'scatter', symbolSize: 3,
      itemStyle: { color: siteColor(site.site) },
      data: site.mean === null ? [] : points.map((point, order) => ({
        value: [index + (points.length > 1 ? (order / (points.length - 1) - .5) * .7 : 0), point.value - site.mean!],
        name: t("{0} · Value − Site Mean: {1}\n次序 {2}", siteName(site.site), format(point.value - site.mean!, 5), point.record_sequence),
      })),
    };
  });
  return { animation: false,
    grid: { top: 32, left: 38, right: 12, bottom: 28, containLabel: true },
    graphic: { type: 'text', left: 4, top: 1, style: { text: t("点：Value − Site Mean\n◇ Site Diff = Site Mean − Grand Mean"), font: '10px sans-serif', lineHeight: 13, fill: '#344e60' } },
    tooltip: { confine: true, renderMode: 'richText', formatter: (event: unknown) =>
      (event as { data?: { name?: string } }).data?.name ?? '' },
    xAxis: { type: 'value', min: -.5, max: Math.max(.5, sites.length - .5), interval: 1,
      axisLabel: { customValues: sites.map((_, index) => index), formatter: value => sites[value] ? siteName(sites[value].site) : '' },
      axisTick: { show: false }, splitLine: { show: false } },
    yAxis: { type: 'value', scale: true },
    series: [...series, { type: 'scatter', name: 'Site Diff', symbol: 'emptyDiamond', symbolSize: 12,
      itemStyle: { color: '#111111', borderColor: '#111111', borderWidth: 2 }, z: 10,
      data: sites.flatMap((site, index) => {
        const difference = siteDifference(data, site);
        return difference === null ? [] : [{ value: [index, difference],
          name: `${siteName(site.site)} · Site Diff: ${format(difference, 5)}\nSite Mean: ${format(site.mean, 5)}\nGrand Mean: ${format(data.statistics.mean, 5)}` }];
      }),
      markLine: { silent: true, symbol: 'none', label: { show: false },
        lineStyle: { color: '#657785', type: 'dashed', width: 1 }, data: [{ yAxis: 0 }] },
    }],
  } as EChartsOption;
}
