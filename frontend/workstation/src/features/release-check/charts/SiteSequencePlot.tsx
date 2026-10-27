import { t, localize, useLanguage } from "../../../i18n";
import { useMemo } from 'react';
import { BaseEChart } from '../../../components/charts/BaseEChart';
import { orderedSites } from './options';
import { siteSequenceOption } from './siteSequence';
import type { ChartData, ChartSettings, Pick } from './types';
import s from './charts.module.css';

export function SiteSequencePlot({ data, settings, onPick }: {
  data: ChartData; settings: ChartSettings; onPick: (pick: Pick) => void;
}) {
  const language = useLanguage();
  const option = useMemo(() => siteSequenceOption(data, settings), [data, settings, language]);
  return <section className={`${s.plot} ${s.siteSequence}`} aria-label={t("分 site 测试次序图")}>
    <h3>{t("测试次序 · 分 site")}</h3>
    <div className={s.sequenceScroll}><div className={s.sequenceCanvas}
      style={{ minWidth: Math.max(360, orderedSites(data, settings).length * 155) }}>
      <BaseEChart ariaLabel={t("分 site 测试次序")} theme='light' option={option}
        onClick={event => { const pick = (event as { data?: { pick?: Pick } }).data?.pick; if (pick) onPick(pick); }} />
    </div></div>
    <p className={s.caption}>{t("每个 site 内从左到右按测试次序排列；黑色短线 Mean，红色虚线 LSL / USL。悬停查看原始次序。")}</p>
  </section>;
}
