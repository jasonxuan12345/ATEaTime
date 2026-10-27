import { t, localize, useLanguage } from "../../../i18n";
import { useMemo, useState } from 'react';
import { format } from '../model';
import type { Candidate } from './model';
import { compareSortKeys, SortHeader, type SortDirection, type SortValue } from '../tableSort';
import s from './outliers.module.css';

type Column = 'record' | 'channel' | 'wafer' | 'hits' | 'score';
const sortKey = (candidate: Candidate, column: Column): SortValue[] => {
  switch (column) {
    case 'record': return [candidate.part_index, candidate.filename];
    case 'channel': return [candidate.head, candidate.site];
    case 'wafer': return [candidate.wafer, candidate.x, candidate.y];
    case 'hits': return [candidate.hit_count];
    case 'score': return [candidate.max_score];
  }
};

export function CandidateTable({ candidates, selectedKey, onSelect, compact = false }: {
  candidates: Candidate[]; selectedKey?: string; onSelect: (chip: Candidate) => void; compact?: boolean;
}) {
  useLanguage();
  const [search, setSearch] = useState(''), [page, setPage] = useState(0);
  const [sort, setSort] = useState<{ column: Column; direction: SortDirection } | null>(null);
  const shown = useMemo(() => {
    const filtered = candidates.filter(c => `${c.filename} ${c.part_index} ${c.part_id ?? ''} ${c.wafer ?? ''} ${c.head} ${c.site}`
      .toLowerCase().includes(search.toLowerCase()));
    return sort ? filtered.sort((a, b) => compareSortKeys(sortKey(a, sort.column), sortKey(b, sort.column), sort.direction)) : filtered;
  }, [candidates, search, sort]);
  function header(column: Column, label: string) {
    return <SortHeader label={label} direction={sort?.column === column ? sort.direction : undefined} onClick={() => {
      setSort(previous => ({ column, direction: previous?.column === column && previous.direction === 'desc' ? 'asc' : 'desc' }));
      setPage(0);
    }}/>;
  }
  const size = compact ? 5 : 40, current = Math.min(page, Math.max(0, Math.ceil(shown.length / size) - 1));
  return <div className={s.candidates}>
    {localize(!compact && <label className={s.search}>{t("搜索异常候选记录")}<input type="search" aria-label={t("搜索异常候选记录")} placeholder={t("文件 / 记录编号 / PART_ID / wafer")}
      value={search} onChange={e => { setSearch(e.target.value); setPage(0); }} /></label>)}
    <div className={s.tableScroll}><table className={s.table}><thead><tr>{localize(header('record', t("测试记录 / 源文件")))}{localize(header('channel', 'Head / Site'))}
      {localize(!compact && header('wafer', t("Wafer / 坐标")))}{localize(header('hits', t("检出异常的测项")))}{localize(header('score', t("最大偏离程度（稳健σ）")))}</tr></thead><tbody>
      {shown.slice(current * size, (current + 1) * size).map(c => <tr key={c.key} className={selectedKey === c.key ? s.selected : ''}>
        <td><button className={s.recordButton} aria-pressed={selectedKey === c.key} onClick={() => onSelect(c)}>{t("记录 #")}{localize(c.part_index)}</button>
          <small>{c.part_id ?? t("PART_ID 未记录")}</small><small title={c.filename}>{c.filename}</small></td>
        <td>H{localize(c.head ?? '—')} / S{localize(c.site ?? '—')}</td>
        {localize(!compact && <td>{localize(c.wafer ?? t("未记录"))}<small>X {localize(c.x ?? '—')} · Y {localize(c.y ?? '—')}</small></td>)}
        <td>{localize(c.hit_count)} {t(" 项")}</td><td className={s.amber}>{localize(c.hits[0]?.direction === 'HIGH' ? '↑' : '↓')} {localize(format(c.max_score, 1))} {t(" 倍")}</td>
      </tr>)}
    </tbody></table></div>
    {localize(!shown.length && <p className={s.empty}>{t("没有符合条件的异常候选记录。没有异常候选记录不代表所有分组均已评估，请查看各组状态。")}</p>)}
    <div className={s.pager}><span>{localize(shown.length)} {t(" 条异常候选记录 · 不按 PART_ID 合并")}</span><div>
      <button disabled={current === 0} onClick={() => setPage(current - 1)}>{t("上一页异常候选记录")}</button>
      <span>{localize(current + 1)} / {localize(Math.max(1, Math.ceil(shown.length / size)))}</span>
      <button disabled={(current + 1) * size >= shown.length} onClick={() => setPage(current + 1)}>{t("下一页异常候选记录")}</button>
    </div></div>
  </div>;
}
