import { t, localize, useLanguage } from "../../i18n";
import { Diagnosis } from './Diagnosis';
import { useEffect, useState } from "react";
import { OutlierEvidence } from './outliers/OutlierEvidence';
import type { Candidate, OutlierCell, SourceBaseline } from './outliers/model';
import { ChartSummary } from "./charts/ChartSummary";
import { format, labels, moduleLabels, severityText, type Detail, type Module, type Row, type Severity } from "./model";
import s from "./release-check.module.css";

const colors = ["#087f88", "#6384c7", "#bd864b", "#a269ab", "#71964b", "#af6169", "#658d9e", "#a8a04c"];
const moduleTabLabels: Record<Module, string> = { cpk: "bin1 CPK", cgm: "CGM / 各 site", mmsa: "Site Diff / 各 site" };
const metricLabels: Record<Module, string> = { cpk: "bin1 CPK", cgm: "min Cg", mmsa: "S₂S Shift (σ)" };
const sevClass: Record<Severity, string> = { RED: s.sev_red, YELLOW: s.sev_yellow, GREEN: s.sev_green };
function S2sPanel({ detail }: { detail: NonNullable<Detail["details"][Module]> }) {
  const language = useLanguage();
  const s2s = detail.s2s;
  if (!s2s) return null;
  const cell = (title: string, value: string, severity: Severity | null, note: string) => <div>
    <dt>{localize(title)}</dt><dd>{localize(value)}</dd>{localize(severity && <small className={sevClass[severity]}>{localize(severityText[severity])}</small>)}<small>{localize(note)}</small></div>;
  return <dl className={s.s2sGrid} aria-label={t("Site Diff 双参数")}>
    {localize(cell(t("Site 均值差（σ）"), `${format(s2s.shift_sigma)} σ`, s2s.shift_severity, t("Site 均值极差 {0} / 总体标准差 {1}", format(s2s.mean_shift, 3), format(s2s.overall_std, 3))))}
    {localize(cell(t("Site 均值差占规格范围比例"), s2s.shift_spec_pct === null ? t("单侧规格") : `${format(s2s.shift_spec_pct)} %`, s2s.shift_spec_severity,
      s2s.shift_spec_pct === null ? t("仅双侧规格计算") : t("Site 均值极差 {0} /（USL−LSL）", format(s2s.mean_shift, 3))))}
    {localize(cell(t("Site 标准差比"), `${format(s2s.var_ratio)} ×`, s2s.var_severity, t("最大 Site 标准差 {0} / 标准差中位数 {1}", format(s2s.max_site_std, 3), format(s2s.median_site_std, 3))))}
  </dl>;
}
export function EvidencePanel({ row, detail, loading, error, demo, onExpand, outlierCell, outlierChip, outlierBaselines = [], outlierFocus = 0, onSelectChip, onExpandOutlier }: {
  row: Row | null; detail: Detail | null; loading: boolean; error: string | null; demo: boolean; onExpand?: (module: Module) => void;
  outlierCell?: OutlierCell; outlierChip?: Candidate | null; outlierBaselines?: SourceBaseline[]; outlierFocus?: number;
  onSelectChip?: (chip: Candidate) => void; onExpandOutlier?: () => void;
}) {
  const language = useLanguage();
  const [module, setModule] = useState<Module | 'outlier'>("cgm");
  useEffect(() => { if (outlierFocus) setModule('outlier'); }, [outlierFocus]);
  if (!row) return <aside className={s.evidence}><div className={s.evidenceEmpty}><span>⌁</span><h3>{t("测项数据与判定依据")}</h3><p>{t("点击结果表中的测项，查看分布与判断依据。")}</p></div></aside>;
  const data = module === 'outlier' ? null : detail?.details[module];
  const cell = module === 'outlier' ? null : (detail?.row[module] ?? row[module]);
  const sites = data?.sites ?? [];
  return <aside className={s.evidence} aria-label={t("测项数据与判定依据")}>
    <div className={s.evidenceHead}><span className={s.eyebrow}>{t("测项详情")}</span><span className={s.testNumber}>Test ID {localize(row.number ?? "—")}</span></div>
    <h2 className={s.itemTitle}>{row.name || t("名称未记录")}</h2><div className={s.evidenceVerdict}><span className={`${s.badge} ${s[row.status.toLowerCase()]}`}>{localize(labels[row.status])}</span><span>{row.unit ?? t("单位缺失")}</span></div>
    <dl className={s.specGrid}><div><dt>{t("有效下限")}</dt><dd>{localize(format(row.lsl, 3))}</dd></div><div><dt>{t("有效上限")}</dt><dd>{localize(format(row.usl, 3))}</dd></div></dl>
    <div className={s.detailTabs}>{localize((["cpk", "cgm", "mmsa", 'outlier'] as const).map(m => <button key={m} aria-pressed={module === m} onClick={() => setModule(m)}>{localize(m === 'outlier' ? t("Outlier / 芯片") : moduleTabLabels[m])}</button>))}</div>
    {module === 'outlier' ? demo ? <p className={s.detailLoading}>{t("导入 STDF 后计算 Outlier；示例不生成检测结果。")}</p> : <OutlierEvidence cell={outlierCell} chip={outlierChip ?? null} baselines={outlierBaselines}
      onSelect={chip => onSelectChip?.(chip)} onExpand={() => onExpandOutlier?.()} /> : <>
    {cell?.filename && <div className={s.evidenceSource}>{t("源文件：")}{cell.filename}</div>}
    {loading ? <div className={s.detailLoading} role="status">{t("正在读取测项数据与判定依据…")}</div> : error ? <p className={s.error} role="alert">{localize(error)}</p> : !data || !cell ? <div className={s.detailLoading}>{t("此检查没有匹配的测项结果。请核对测项选择、Test ID、Test Name 及重复项。")}</div> : <>
      <div className={s.detailMetric}><div><span>{localize(metricLabels[module])}</span><strong>{localize(format(cell.value))}</strong></div><div><span>{t("参与计算的记录数")}</span><strong>{localize(data.n.toLocaleString())}</strong></div></div>
      {localize(module === "mmsa" && <S2sPanel detail={data} />)}
      <ChartSummary row={row} module={module} detail={data} onExpand={onExpand} />
      <div className={s.siteTableWrap}><table className={s.siteTable}><thead><tr><th>site</th><th>n</th><th>{t("均值")}</th><th>{t("标准差")}</th><th>Cg</th></tr></thead><tbody>{localize(sites.map((site, index) => <tr key={site.site ?? "unknown"}>
        <td><i style={{ background: colors[index % colors.length] }} />{localize(site.site === -1 || site.site === null ? t("未知") : `S${site.site}`)}</td><td>{localize(site.n)}</td><td>{localize(format(site.mean, 3))}</td><td>{localize(format(site.std, 3))}</td><td>{localize(format(site.cg))}</td>
      </tr>))}</tbody></table></div>
      <div className={`${s.ruleBox} ${cell.override ? s.accepted : ""}`}><strong>{localize(cell.override ? t("按 Bin1 CPK 接受规则判为通过") : module === "mmsa" ? t("Site Diff 判定") : t("判断依据"))}</strong><p>{localize(cell.reason)}</p>
        {localize(module === "mmsa" && data.s2s && <span>{t("Site Diff 检查触发条件：bin1 CPK ")}{localize(format(data.s2s.overall_bin1_cpk))}{t("（")}{"<"} {localize(format(data.s2s.gate.cpk_lt, 1))} {t(" 触发）· min Cg ")}{localize(format(data.s2s.cgm_min_cg))}{t("（")}{"<"} {localize(format(data.s2s.gate.cg_lt, 2))} {t(" 触发 · 不及格 site ")}{localize(data.s2s.cgm_failed_site_count)} / {localize(data.s2s.site_count)}{t("）")}{localize(data.s2s.cp !== null && ` · Cp ${format(data.s2s.cp)}${data.s2s.close_to ? t("（贴{0}限 {1}%）", data.s2s.close_to === "HIGH" ? "上" : "下", format(Math.min(data.s2s.limit_margin_upper ?? 0, data.s2s.limit_margin_lower ?? 0) * 100)) : ""}`)}</span>)}
        {cell.override && <span>{t("接受规则所用的 Bin1 CPK：")}{localize(format(cell.supported_by_bin1_cpk))}{row.cpk?.filename && ` · ${row.cpk.filename}`}</span>}<code>{localize([cell.rule_id ?? (demo ? t("示例规则") : t("没有命中判断规则")), data.s2s?.secondary_rule_id].filter(Boolean).join(" + "))}</code></div>
      <Diagnosis value={cell.diagnosis} />
      {localize(!demo && <details className={s.sampleEvidence}><summary>{t("参与计算的测试记录与排除记录")}</summary><p>{t("参与计算的测试记录 ")}{localize(data.sample_ref_count)} {t(" 条，显示前 20 条。")}</p>
        {localize(data.sample_refs.map(ref => <div key={`${ref.part_index}-${ref.record_sequence}`}>{t("记录 ")}{localize(ref.part_index)} {t(" · 序号 ")}{localize(ref.record_sequence)} · site {localize(ref.site ?? t("未知"))}</div>))}
        {localize(Object.entries(data.excluded_counts).map(([reason, n]) => <div key={reason}>{localize(reason)}{t("：")}{localize(n)}</div>))}</details>)}
    </>}</>}
    <details className={s.conditions}><summary>{t("测试条件与分组")}</summary>{localize(Object.entries(row.conditions).map(([key, value]) => <div key={key}><span>{localize(({ part_type: t("产品"), job_name: t("程序"), job_revision: t("程序版本"), test_code: t("阶段"), test_temperature: t("温度") } as Record<string, string>)[key] ?? key)}</span><strong>{value || t("未记录")}</strong></div>))}<code>{localize(row.key)}</code></details>
  </aside>;
}
