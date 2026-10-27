import { t, localize, useLanguage } from "../../../i18n";
import { useEffect, useState } from 'react';
import { format } from '../model';
import { baselineKey, evaluated, type Candidate, type SourceBaseline } from './model';
import { focusPosition } from './chartOptions';
import { SELECTED, scoreText, signedScore } from './display';
import s from './outliers.module.css';

export function ReferenceChart({ baselines, candidates, selectedKey, onSelect, compact = false }: {
  baselines: SourceBaseline[]; candidates: Candidate[]; selectedKey?: string;
  onSelect: (chip: Candidate) => void; compact?: boolean;
}) {
  useLanguage();
  const [relative, setRelative] = useState(!compact), [page, setPage] = useState(0);
  const valid = baselines.filter(b => evaluated(b) && b.median !== null && b.robust_sigma !== null && b.robust_sigma > 0);
  const size = compact ? 3 : 8, current = Math.min(page, Math.max(0, Math.ceil(valid.length / size) - 1));
  const selectedChip = candidates.find(c => c.key === selectedKey);
  const activeIndex = valid.findIndex(b => b.dataset_id === selectedChip?.dataset_id && selectedChip.hits.some(h => h.baseline_id === b.baseline_id));
  const activeBaseline = activeIndex >= 0 ? baselineKey(valid[activeIndex]) : '';
  useEffect(() => { if (activeIndex >= 0) setPage(Math.floor(activeIndex / size)); }, [selectedKey, activeIndex, activeBaseline, size]);
  const shown = valid.slice(current * size, (current + 1) * size);
  const pointLimit = compact ? 50 : 1000;
  const points = shown.map(b => {
    const matches = candidates.flatMap(c => c.dataset_id !== b.dataset_id ? [] : c.hits
      .filter(h => h.baseline_id === b.baseline_id).map(h => ({ c, h })));
    return { b, points: [...matches.filter(p => p.c.key === selectedKey), ...matches.filter(p => p.c.key !== selectedKey)].slice(0, pointLimit), total: matches.length };
  });
  const rawLow = Math.min(...shown.map(b => b.lower_limit!)), rawHigh = Math.max(...shown.map(b => b.upper_limit!));
  const span = rawHigh - rawLow || 1, low = rawLow - span * .25, high = rawHigh + span * .25;
  const start = 188, end = 822, center = (start + end) / 2, rowHeight = compact ? 70 : 88;
  const position = (b: SourceBaseline, value: number) => relative
    ? center + focusPosition((value - b.median!) / b.robust_sigma!, b.threshold) * (end - start) / 2
    : start + Math.max(0, Math.min(1, (value - low) / (high - low))) * (end - start);
  const outside = (b: SourceBaseline, value: number) => relative ? Math.abs((value - b.median!) / b.robust_sigma!) > 15 : value < low || value > high;
  const height = shown.length * rowHeight + 68;
  return <section className={s.reference} aria-label={t("各 site 参考区间与异常候选点")}>
    <div className={s.referenceToolbar}><strong>{localize(relative ? t("偏离程度（稳健σ） · 中间筛选区间放大，两侧压缩") : t("实测值 · 优先展示各组参考区间"))}</strong>
      {localize(!compact && <button aria-pressed={relative} onClick={() => setRelative(!relative)}>{localize(relative ? t("切换到实测值") : t("切换到偏离程度（稳健σ）"))}</button>)}</div>
    {!shown.length ? <p className={s.empty}>{t("无法计算有效筛选范围，请查看参考记录数和无法评估原因。")}</p> : <div className={s.referenceScroll}><svg role='img'
      aria-label={localize(relative ? t("相对各自同组中位数的偏离程度（稳健σ）") : t("实测值、同组参考范围和异常候选记录"))} viewBox={`0 0 1000 ${height}`}>
      <rect x='108' y='24' width='68' height={height - 40} fill='#FFF7ED' rx='3' />
      <rect x='834' y='24' width='150' height={height - 40} fill='#FFF7ED' rx='3' />
      <line x1='176' x2='176' y1='24' y2={height - 16} stroke='#CBD5E1' strokeDasharray='4 4' />
      <line x1='834' x2='834' y1='24' y2={height - 16} stroke='#CBD5E1' strokeDasharray='4 4' />
      <text x='142' y='15' textAnchor='middle' fontSize='10' fill='#9A3412'>{localize(relative ? '< −15x' : t("低于图示范围"))}</text>
      <text x='910' y='15' textAnchor='middle' fontSize='10' fill='#9A3412'>{localize(relative ? t("超出图示范围的点（>15x）") : t("超出图示范围的点"))}</text>
      {points.map(({ b, points: ps }, i) => {
        const y = 64 + i * rowHeight;
        return <g key={baselineKey(b)}>
          <title>{`${b.filename} · wafer ${b.wafer ?? t("未记录")} · H${b.head}/S${b.site} · n=${b.n}`}</title>
          <text x='5' y={y - 2} fontSize='13' fill='#334155'>H{localize(b.head)}/S{localize(b.site)}</text>
          <text x='5' y={y + 16} fontSize='10' fill='#64748B'>{localize(b.wafer ?? 'W?')} · n={localize(b.n)}</text>
          <line x1={start} x2={end} y1={y} y2={y} stroke='#E2E8F0' />
          <line x1={position(b, b.lower_limit!)} x2={position(b, b.upper_limit!)} y1={y} y2={y} stroke='#8DBBC0' strokeWidth='3' />
          <rect x={position(b, b.q1!)} y={y - 9} width={Math.max(2, position(b, b.q3!) - position(b, b.q1!))} height='18' fill='#99D5D5'><title>Q1 {localize(format(b.q1, 5))} · Q3 {localize(format(b.q3, 5))}</title></rect>
          <line x1={position(b, b.median!)} x2={position(b, b.median!)} y1={y - 12} y2={y + 12} stroke='#111827' strokeWidth='2' />
          {localize([b.lower_limit!, b.upper_limit!].map((v, j) => <g key={'filter' + j}>
            <line x1={position(b, v)} x2={position(b, v)} y1={y - 17} y2={y + 17} stroke='#0F766E' strokeWidth='2' />
            <text x={position(b, v)} y={y - 23} textAnchor='middle' fontSize='10' fill='#0F766E'>{localize(j ? t("筛选上限") : t("筛选下限"))} ({localize(relative ? (j ? '+' : '−') + scoreText(b.threshold) : format(v, 4))})</text>
          </g>))}
          {localize([b.lsl, b.usl].map((v, j) => v === null ? null : <g key={'spec' + j}>
            <line x1={position(b, v)} x2={position(b, v)} y1={y - 22} y2={y + 22} stroke='#DC2626' strokeDasharray='4 3' />
            <text x={position(b, v)} y={y + 36} textAnchor='middle' fontSize='10' fill='#B91C1C'>Spec {localize(j ? 'USL' : 'LSL')}{localize(outside(b, v) ? t("（区外）") : '')}</text>
            <title>{localize(j ? 'USL' : 'LSL')} {localize(format(v, 6))}</title>
          </g>))}
          {ps.slice().sort((a, b) => Number(a.c.key === selectedKey) - Number(b.c.key === selectedKey)).map(({ c, h }) => {
            const score = signedScore(h), overflow = outside(b, h.value);
            const hash = [...c.key + ':' + h.record_sequence].reduce((n, char) => n * 31 + char.charCodeAt(0) | 0, 0) >>> 0;
            const selected = c.key === selectedKey;
            const px = overflow ? (score < 0 ? 142 : 872) + (hash % 3 - 1) * 12 : position(b, h.value);
            const py = y + (selected ? 0 : (hash % 5 - 2) * 4);
            return <g key={`${c.key}:${h.record_sequence}:${h.result_index}`}>
              {localize(selected && <circle cx={px} cy={py} r='11' fill='none' stroke={SELECTED} strokeWidth='2' />)}
              <circle role='button' tabIndex={0} aria-label={t("选择记录 {0}，{1}，偏离 {2}", c.part_index, c.filename, scoreText(h.score))}
                cx={px} cy={py} r={selected ? 7 : 3.5} fill={selected ? SELECTED : '#F59E0B'} fillOpacity={selected ? 1 : .65}
                stroke={selected ? '#fff' : 'none'} strokeWidth='2' onClick={() => onSelect(c)}
                onKeyDown={event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); onSelect(c); } }}>
                <title>{t("{0} · 记录 #{1} · 实测 {2} {3} · 真实偏离 {4}", c.filename, c.part_index, format(h.value, 6), h.unit ?? '', scoreText(h.score))}</title>
              </circle>
              {localize(selected && overflow && <text x={px + 12} y={py - 9} fontSize='11' fill={SELECTED}>{localize(scoreText(h.score))}</text>)}
            </g>;
          })}
        </g>;
      })}
      <text x={center} y={height - 8} textAnchor='middle' fontSize='11' fill='#64748B'>{localize(relative ? t("0x = 同组中位数 · 单位为稳健标准差，超出范围的点显示在边缘") : t("实测值 · 超出图示范围的点显示在两侧"))}</text>
    </svg></div>}
    {localize(points.some(p => p.total > pointLimit) && <p className={s.caption}>{t("每组最多显示 ")}{localize(pointLimit)} {t(" 个异常候选点，优先显示当前芯片；完整异常候选记录见列表。")}</p>)}
    {localize(valid.length > size && <div className={s.pager}><button disabled={!current} onClick={() => setPage(current - 1)}>{t("上一组参考范围")}</button>
      <span>{localize(current + 1)} / {localize(Math.ceil(valid.length / size))}</span><button disabled={(current + 1) * size >= valid.length} onClick={() => setPage(current + 1)}>{t("下一组参考范围")}</button></div>)}
  </section>;
}
