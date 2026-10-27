import { t, localize, useLanguage } from "../../../i18n";
import { cgNumber, format, type Module, type ModuleDetail, type Row } from '../model';
import s from './charts.module.css';

export function ChartSummary({ row, module, detail, onExpand }: { row: Row; module: Module; detail?: ModuleDetail;
  onExpand?: (module: Module) => void }) {
  useLanguage();
  const stats = detail;
  const complete = !!stats?.n && stats.sites.reduce((sum, site) => sum + site.n, 0) === stats.n && stats.sites.every(site => !site.n || site.mean != null);
  const aggregateMean = complete ? stats!.sites.reduce((sum, site) => sum + site.n * (site.mean ?? 0), 0) / stats!.n : null;
  const aggregateStd = complete && stats!.n > 1 && aggregateMean !== null ? Math.sqrt(stats!.sites.reduce((sum, site) => sum + Math.max(0, site.n - 1) * (site.std ?? 0) ** 2 + site.n * ((site.mean ?? 0) - aggregateMean) ** 2, 0) / (stats!.n - 1)) : null;
  const mean = stats?.mean ?? aggregateMean, std = stats?.std ?? aggregateStd;
  const values = [row.lsl, row.usl, mean != null && std != null ? mean - 3 * std : null,
    mean != null && std != null ? mean + 3 * std : null].filter((n): n is number => n != null && Number.isFinite(n));
  const low = Math.min(...values), high = Math.max(...values), span = high - low || Math.abs(low) * .1 || 1;
  const x = (value: number) => 20 + (value - low) / span * 260;
  const cp = row.lsl !== null && row.usl !== null && row.lsl < row.usl && std ? (row.usl - row.lsl) / (6 * std) : null;
  const sites = (stats?.sites ?? []).filter(site => site.cg !== null).slice().sort((a, b) => cgNumber(a.cg)! - cgNumber(b.cg)!).slice(0, 3);
  const cgMax = Math.max(1.33, ...sites.map(site => typeof site.cg === 'number' ? site.cg : 0));
  return <div className={s.summary} aria-label={t("规格与 site 摘要")}>
    <p>{t("规格位置 · ")}{row[module]?.filename ?? t("当前模块来源")}</p>
    {localize(values.length > 0 ? <svg viewBox='0 0 300 90' role='img' aria-label={t("规格带尺")}>
      <line x1={20} x2={280} y1={38} y2={38} stroke='#a8bdc6' strokeWidth={3} />
      {localize(mean != null && std != null && <line x1={x(mean - 3 * std)} x2={x(mean + 3 * std)} y1={38} y2={38} stroke='#087f88' strokeWidth={8} />)}
      {localize([row.lsl, row.usl].map((limit, i) => limit !== null && <g key={i}><line x1={x(limit)} x2={x(limit)} y1={20} y2={55} stroke='#bd474b' strokeWidth={2} /><text x={x(limit)} y={76} textAnchor={i ? 'end' : 'start'} fontSize={11}>{localize(i ? 'USL' : 'LSL')} {localize(format(limit, 3))}</text></g>))}
      {localize(mean != null && <g><circle cx={x(mean)} cy={38} r={5} fill='#122a37' /><text x={150} y={13} textAnchor='middle' fontSize={11}>Mean {localize(format(mean, 4))} {t(" · ±3Std 参考")}</text></g>)}
    </svg> : <p>{t("暂无有效规格或总体统计，可展开查看分布。")}</p>)}
    <p>Cp {localize(format(cp))}{localize(module === 'cpk' ? ` · Cpk ${format(row.cpk?.value)}` : t(" · 同模块样本统计"))}{localize(row.lsl !== null && row.usl !== null && row.lsl >= row.usl ? t(" · 规格顺序无效") : '')}</p>
    <p>{t("最低 Cg site（最多 3 项）")}{localize(module !== 'cgm' && t(" · 来自当前模块统计"))}</p>
    {localize(sites.length ? <svg viewBox={`0 0 300 ${sites.length * 30 + 15}`} role='img' aria-label={t("最低 Cg site 摘要")}>
      {localize(sites.map((site, i) => <g key={site.site ?? 'unknown'} onClick={() => onExpand?.(module)} style={{ cursor: 'pointer' }}>
        <text x={0} y={i * 30 + 19} fontSize={12}>S{localize(site.site)}</text><rect x={40} y={i * 30 + 7} width={site.cg === 'Infinity' ? 155 : (cgNumber(site.cg) ?? 0) / cgMax * 155} height={13} fill='#087f88' />
        <text x={205} y={i * 30 + 19} fontSize={12}>{localize(format(site.cg))}</text></g>))}
    </svg> : <p>{t("本模块未提供 Cg，可切换 CGM 查看。")}</p>)}
    {localize(onExpand && <button onClick={() => onExpand(module)}>{t("展开分析 →")}</button>)}
  </div>;
}
