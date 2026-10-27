import { t, localize, useLanguage } from "../../../i18n";
import { cgNumber, format } from '../model';
import { siteName, type MatrixData } from './types';
import s from './charts.module.css';

export function FamilyHeatmap({ data, currentKey, selectedSites, onRow, onSite, onPage }: {
  data: MatrixData; currentKey: string; selectedSites?: number[]; onRow: (key: string, site?: number) => void; onSite: (site: number) => void; onPage: (offset: number) => void }) {
  useLanguage();
  const sites = [...new Set(data.rows.flatMap(row => row.sites.map(site => site.site)))].sort((a, b) => (a ?? Infinity) - (b ?? Infinity));
  return <section className={s.plot} aria-label={t("测项 × site Cg 热力图")}>
    <div className={s.plotHeading}><h3>{t("同组测项的各 Site Cg")}</h3><span>{localize(data.total)} {t(" 项 · 当前 ")}{localize(data.offset + 1)}–{localize(Math.min(data.offset + data.limit, data.total))}</span></div>
    <div className={s.source}><strong>CGM</strong><span>{data.rows[0]?.filename ?? t("来源见各行")}</span><span>{t("按测项名称前缀及当前条件分组；显示当前结果列表内的完整组，左侧搜索只筛选导航列表")}</span></div>
    <p className={s.caption}>{t("统一色标：Cg < ")}{localize(data.thresholds.cg_fail)} {t(" 未达标；< ")}{localize(data.thresholds.cg_pass)} {t(" 风险；其余达标。灰格为缺失或不可计算。")}</p>
    <div className={s.tableWrap}><table className={`${s.table} ${s.matrix}`}><thead><tr><th>{t("测项 / 来源")}</th>{localize(sites.map(site => <th key={site ?? 'unknown'}>
      {localize(site === null ? t("未知 site") : <button aria-pressed={selectedSites?.includes(site) ?? false} onClick={() => onSite(site)}>{localize(siteName(site))}</button>)}</th>))}</tr></thead>
      <tbody>{data.rows.map(row => <tr key={row.key} aria-current={row.key === currentKey}>
        <td><button onClick={() => onRow(row.key)}><strong>{localize(row.number)} · {row.name}</strong></button><small className={s.caption}>{localize(row.rule)} · {row.filename ?? t("无 CGM 来源")}</small></td>
        {sites.map(site => { const cell = row.sites.find(value => value.site === site), cg = cell?.cg;
          const value = cgNumber(cg);
          const color = value == null ? '#e5eaed' : value < data.thresholds.cg_fail ? '#f4d2d4' : value < data.thresholds.cg_pass ? '#f9e8bb' : '#cce9df';
          const reason = cell?.reason ?? '';
          return <td key={site ?? 'unknown'}><button className={s.cell} data-selected={selectedSites?.includes(site ?? -1)} style={{ background: color }} onClick={() => onRow(row.key, site ?? undefined)}
            aria-label={`${row.name} ${siteName(site)} Cg ${format(cg)}`} title={`${row.name}\n${siteName(site)} · Cg ${format(cg)} · n=${cell?.n ?? 0}\n${row.filename ?? ''}\n${row.cpk_override_applied ? t("按 Bin1 CPK 接受规则判为通过，颜色仍按实际 Cg") : row.status}\n${reason}`}>
            {localize(format(cg))}<small style={{ display: 'block', fontSize: 10 }}>n={localize(cell?.n ?? 0)}</small></button></td>; })}</tr>)}</tbody></table></div>
    {localize(!data.total && <p className={s.empty}>{t("当前组没有可用测项。")}</p>)}
    <div className={s.tools}><button disabled={data.offset === 0} onClick={() => onPage(Math.max(0, data.offset - data.limit))}>{t("上一页矩阵")}</button>
      <button disabled={data.offset + data.limit >= data.total} onClick={() => onPage(data.offset + data.limit)}>{t("下一页矩阵")}</button></div>
  </section>;
}
