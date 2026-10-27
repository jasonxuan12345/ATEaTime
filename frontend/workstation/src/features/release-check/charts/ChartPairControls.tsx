import { t, localize, useLanguage } from "../../../i18n";
import { chartNames, chartUnavailable, type ChartScenario } from './chartScenarios';
import type { ChartData, ChartKind } from './types';
import s from './charts.module.css';

export function ChartPairControls({ data, scenario, primary, secondary, manual, family, onPrimary, onSecondary, onSwap, onRestore }: {
  data: ChartData | null; scenario: ChartScenario; primary: ChartKind; secondary: ChartKind; manual: boolean; family: boolean;
  onPrimary: (kind: ChartKind) => void; onSecondary: (kind: ChartKind) => void; onSwap: () => void; onRestore: () => void }) {
  useLanguage();
  const choices = (Object.keys(chartNames) as ChartKind[]).map(kind => <option key={kind} value={kind} disabled={!!chartUnavailable(data, kind)}>{localize(kind === 'cg' && !data?.sites.some(site => site.cg !== null) ? t("各 site Std（未提供 Cg）") : chartNames[kind])}</option>);
  return <section className={s.pairControls} aria-label={t("图组选择")}>
    <div className={s.guidance}><strong>{localize(scenario.title)}</strong><span>{localize(scenario.description)}</span></div>
    <div className={s.tools}><label>{t("主图")}<select aria-label={t("选择主图")} value={primary} disabled={!data} onChange={event => onPrimary(event.target.value as ChartKind)}>{localize(choices)}</select></label>
      <label>{t("辅图")}{localize(family ? <span>{t("同组测项的各 Site Cg（CGM）")}</span> : <select aria-label={t("选择辅图")} value={secondary} disabled={!data} onChange={event => onSecondary(event.target.value as ChartKind)}>{localize(choices)}</select>)}</label>
      <button disabled={!data || family} onClick={onSwap}>{t("交换主辅图")}</button><button disabled={!data} onClick={onRestore}>{t("恢复推荐图组")}</button><small>{localize(manual ? t("已手动调整图组") : t("按当前模块与规则推荐"))}</small>
    </div>
    {localize(chartUnavailable(data, 'wafer') && data && <p className={s.caption}>{t("Wafer Map：")}{localize(chartUnavailable(data, 'wafer'))}</p>)}
  </section>;
}
