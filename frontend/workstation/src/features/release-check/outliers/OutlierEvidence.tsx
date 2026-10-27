import { t, localize, useLanguage } from "../../../i18n";
import { Diagnosis } from '../Diagnosis';
import { useState } from 'react';
import { format } from '../model';
import { describeBaselines, type Candidate, type OutlierCell, type SourceBaseline } from './model';
import { CandidateTable } from './CandidateTable';
import { ReferenceChart } from './ReferenceChart';
import s from './outliers.module.css';

export function ChipEvidence({ chip, baselines, preferredBaselines }: { chip: Candidate; baselines: SourceBaseline[]; preferredBaselines?: SourceBaseline[] }) {
  const language = useLanguage();
  const [hitId, setHitId] = useState('');
  const inScope = chip.hits.filter(h => (preferredBaselines ?? baselines).some(b => b.dataset_id === chip.dataset_id && b.baseline_id === h.baseline_id));
  const hit = chip.hits.find(h => `${h.baseline_id}:${h.record_sequence}` === hitId) ?? inScope[0] ?? chip.hits[0];
  const b = baselines.find(b => b.dataset_id === chip.dataset_id && b.baseline_id === hit?.baseline_id);
  if (!hit) return null;
  const trace = { source: chip.filename, part_index: chip.part_index, part_id: chip.part_id, lot: chip.lot, wafer: chip.wafer,
    x: chip.x, y: chip.y, head: chip.head, site: chip.site, prr_record_sequence: chip.prr_record_sequence,
    test_record_sequence: hit.record_sequence, result_index: hit.result_index };
  return <section className={s.chipEvidence} aria-label={t("异常候选记录详情")}>
    <div className={s.chipTitle}><strong>{t("记录 #")}{localize(chip.part_index)}</strong><span>H{localize(chip.head ?? '—')} / S{localize(chip.site ?? '—')} · {localize(hit.direction === 'HIGH' ? t("↑ 偏高") : t("↓ 偏低"))}</span></div>
    <p className={s.filename}>{chip.filename} · {chip.part_id ?? t("PART_ID 未记录")}</p>
    <p><strong>{hit.test_name}</strong> {t(" 实测 ")}<b>{localize(format(hit.value, 4))} {hit.unit ?? t("单位未记录")}</b>{t("，同组中位数 ")}{localize(format(hit.median, 4))}{t("。")}</p>
    <p>{t("实测值与同组中位数相差 ")}<b className={s.amber}>{localize(format(hit.score, 1))} {t(" 倍稳健标准差")}</b>{localize(b && <>{t("，超过筛选阈值 ")}{localize(format(b.threshold, 1))} {t(" 倍")}</>)}{t("；该异常候选记录需要复核。")}</p>
    {b && <p>{t("原规格：")}{localize(format(b.lsl, 4))} ～ {localize(format(b.usl, 4))} {b.unit ?? ''}{t("。")}{localize(b.lsl !== null || b.usl !== null ? t("该记录在有效规格内。") : t("未提供规格，按统计偏离筛选。"))}</p>}
    {localize(b && <Diagnosis value={b.diagnosis} />)}
    {localize(b && <ReferenceChart key={`${chip.key}:${hit.baseline_id}`} baselines={[b]} candidates={[chip]} selectedKey={chip.key} onSelect={() => undefined} compact />)}
    <label className={s.hitPicker}>{t("该记录有 ")}{localize(chip.hit_count)} {t(" 个测项被列为异常候选")}<select aria-label={t("当前异常候选记录检出异常的测项")} value={`${hit.baseline_id}:${hit.record_sequence}`}
      onChange={e => setHitId(e.target.value)}>{chip.hits.map(h => <option key={`${h.baseline_id}:${h.record_sequence}`} value={`${h.baseline_id}:${h.record_sequence}`}>
        {localize(h.test_num)} · {h.test_name} · {localize(format(h.score, 1))} {t(" 倍")}</option>)}</select></label>
    <details><summary>{t("定位信息")}</summary><dl className={s.trace}>{localize(Object.entries(trace).map(([key, value]) => <div key={key}><dt>{localize(key)}</dt><dd>{localize(value ?? t("未记录"))}</dd></div>))}</dl>
      <button onClick={() => void navigator.clipboard?.writeText(JSON.stringify(trace, null, 2))}>{t("复制定位信息")}</button></details>
  </section>;
}

const reasons: Record<string, string> = { TOO_FEW_VALID_SAMPLES: '参与计算的记录数不足', ZERO_ROBUST_SPREAD: '四分位间距为零，无法计算筛选范围',
  NON_FINITE_STATISTICS: '统计量无法计算', NON_FINITE_SCORES: '偏离程度（稳健σ）无法计算', MISSING_OR_INVALID_CHANNEL: 'head / site 缺失或无效',
  NON_PARAMETRIC_TEST: '功能测试不适用', INVALID_LIMIT_ORDER: '规格上下限顺序有误' };
export function BaselineTable({ baselines }: { baselines: SourceBaseline[] }) {
  const language = useLanguage();
  return <div className={s.tableScroll}><table className={s.table}><thead><tr><th>{t("来源 / Wafer")}</th><th>Head / Site</th><th>{t("有效 n")}</th><th>{t("中位数")}</th><th>{t("状态")}</th></tr></thead>
    <tbody>{baselines.map(b => <tr key={`${b.dataset_id}:${b.baseline_id}`}><td>{b.filename}<small>lot {localize(b.lot ?? '—')} · wafer {localize(b.wafer ?? '—')}</small></td>
      <td>H{localize(b.head ?? '—')} / S{localize(b.site ?? '—')}</td><td>{localize(b.n)}</td><td>{localize(format(b.median, 4))}</td>
      <td>{localize(b.candidate_count ? t("{0} 条异常候选记录", b.candidate_count) : describeBaselines([b]))}<small>{localize(b.reason ? reasons[b.reason] ?? b.reason : '')}</small>
        <Diagnosis value={b.diagnosis} />
        {localize(Object.keys(b.excluded_counts).length > 0 && <details><summary>{t("排除记录")}</summary>{localize(Object.entries(b.excluded_counts).map(([reason, n]) => <small key={reason}>{localize(reason)}{t("：")}{localize(n)}</small>))}</details>)}</td></tr>)}</tbody>
  </table></div>;
}
export function OutlierEvidence({ cell, chip, baselines, onSelect, onExpand }: {
  cell: OutlierCell | undefined; chip: Candidate | null; baselines: SourceBaseline[];
  onSelect: (chip: Candidate) => void; onExpand: () => void;
}) {
  const language = useLanguage();
  return <div className={s.evidence}>
    <div className={s.heading}><strong>Outlier · {localize(cell?.candidates.length ?? 0)} {t(" 条异常候选记录")}</strong><button onClick={onExpand}>{t("展开分析 ↗")}</button></div>
    <p>{localize(cell?.filenames.join(t("、")) || t("当前测项没有匹配的 Outlier 结果。"))}</p>
    <p>{localize(cell?.label)} {t(" · 参考样本只使用有效 Bin1、规格内记录")}</p>
    {localize(chip && <ChipEvidence key={`${chip.key}:${cell?.baselines[0]?.group_id}`} chip={chip} baselines={baselines} preferredBaselines={cell?.baselines} />)}
    {localize(cell && <><ReferenceChart baselines={cell.baselines} candidates={cell.candidates} selectedKey={chip?.key} onSelect={onSelect} compact />
      <details><summary>{t("各组样本数与评估状态 · ")}{localize(cell.baselines.length)} {t(" 组")}</summary><BaselineTable baselines={cell.baselines} /></details>
      <CandidateTable candidates={cell.candidates} selectedKey={chip?.key} onSelect={onSelect} compact /></>)}
    <p className={s.caption}>{t("异常候选记录只表示统计偏离，不自动判定缺陷或修改 bin。偏态、多峰、离散测项需确认适用性；site 整体偏移请结合 Site Diff。")}</p>
  </div>;
}
