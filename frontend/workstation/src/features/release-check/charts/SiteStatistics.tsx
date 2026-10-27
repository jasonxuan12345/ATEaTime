import { t, localize, useLanguage } from "../../../i18n";
import { format } from '../model';
import { orderedSites } from './options';
import { siteName, type ChartData, type ChartSettings } from './types';
import { siteDifference } from './siteDeviation';
import s from './charts.module.css';

export function SiteStatistics({ data, settings }: { data: ChartData; settings: ChartSettings }) {
  useLanguage();
  const cgm = data.context.module === 'cgm';
  return <section className={s.plot}><h3>{t("各 site 统计")}</h3><div className={s.siteTable}>
    <table className={s.table}><thead><tr>{localize(['site', 'n', 'Mean', ...(cgm ? ['Site Diff'] : []), 'Std', 'Cg', t("状态")].map(value => <th key={value}>{localize(value)}</th>))}</tr></thead>
      <tbody>{localize(orderedSites(data, settings).map(site => <tr key={site.site}>
        <td>{localize(siteName(site.site))}</td><td>{localize(site.n)}</td><td>{localize(format(site.mean, 4))}</td>
        {localize(cgm && <td title={t("Site Mean − Grand Mean（全部参与计算的记录）")}>{localize(format(siteDifference(data, site), 4))}</td>)}
        <td>{localize(format(site.std, 4))}</td><td>{localize(format(site.cg))}</td><td>{localize(site.cg_status)}</td>
      </tr>))}</tbody></table>
  </div></section>;
}
