import { t, localize, useLanguage } from "../../../i18n";
import { useEffect, useMemo, useState } from 'react';
import type { Module, Row } from '../model';
import { siteName, type MatrixGroup, type Notes } from './types';
import s from './charts.module.css';

type Props = { rows: Row[]; groups: MatrixGroup[]; currentKey: string; activeGroup: string;
  module: Module; notes: Notes; grouped: boolean; search: string; filter: string;
  onSearch: (value: string) => void; onFilter: (value: string) => void; onGrouped: (value: boolean) => void;
  onRow: (key: string) => void; onGroup: (key: string) => void };

function Branch({ label, active, children, className }: { label: React.ReactNode; active: boolean; children: () => React.ReactNode; className: string }) {
  const language = useLanguage();
  const [open, setOpen] = useState(active);
  useEffect(() => { if (active) setOpen(true); }, [active]);
  return <details className={className} open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>{localize(label)}</summary>{localize(open ? children() : null)}
  </details>;
}

function RowButtons({ rows, currentKey, onRow }: Pick<Props, 'rows' | 'currentKey' | 'onRow'>) {
  const language = useLanguage();
  const [requestedPage, setPage] = useState(0);
  useEffect(() => {
    const index = rows.findIndex(row => row.key === currentKey);
    setPage(index < 0 ? 0 : Math.floor(index / 100));
  }, [rows, currentKey]);
  const page = Math.min(requestedPage, Math.max(0, Math.ceil(rows.length / 100) - 1));
  return <>{rows.slice(page * 100, page * 100 + 100).map(row => <button key={row.key}
    aria-current={row.key === currentKey ? 'true' : undefined} onClick={() => onRow(row.key)}><span>{localize(row.number ?? '—')}</span>{row.name}</button>)}
    {localize(rows.length > 100 && <div><button aria-label={t("上一页测项导航")} disabled={page === 0} onClick={() => setPage(page - 1)}>{t("上一页")}</button>
      <span>{localize(page + 1)}/{localize(Math.ceil(rows.length / 100))}</span><button aria-label={t("下一页测项导航")} disabled={(page + 1) * 100 >= rows.length} onClick={() => setPage(page + 1)}>{t("下一页")}</button></div>)}</>;
}

export function AnalysisNavigation(props: Props) {
  const language = useLanguage();
  const { rows, groups, currentKey, activeGroup, module, notes, grouped, search, filter, onSearch, onFilter, onGrouped, onRow, onGroup } = props;
  const indexed = useMemo(() => {
    const byKey = new Map(rows.map(row => [row.key, row]));
    return new Map(groups.map(group => [group.key, group.keys.map(key => byKey.get(key)).filter((row): row is Row => !!row)]));
  }, [groups, rows]);
  const visible = groups.filter(group => indexed.get(group.key)?.length);
  const rules = [...new Set(visible.map(group => group.rule))];
  const entries = (group: MatrixGroup) => indexed.get(group.key) ?? [];
  return <aside className={s.nav} aria-label={t("异常项导航")}>
    <div className={s.navHeading}><strong>{t("异常项导航")}</strong><span>{localize(rows.length)} {t(" 项")}</span></div>
    <input type='search' aria-label={t("搜索分析测项")} placeholder={t("搜索测项 / 编号")} value={search} onChange={event => onSearch(event.target.value)} />
    <select aria-label={t("分析测项筛选")} value={filter} onChange={event => onFilter(event.target.value)}>
      <option value='all'>{t("当前列表全部测项")}</option><option value='attention'>{t("仅需关注")}</option><option value='unrecorded'>{t("尚未记录观察")}</option>
    </select>
    <div className={s.navModes}><button aria-pressed={!grouped} onClick={() => onGrouped(false)}>{t("测项顺序")}</button><button aria-pressed={grouped} onClick={() => onGrouped(true)}>{t("规则 / 测项组")}</button></div>
    {localize(grouped ? rules.map(rule => {
      const children = visible.filter(group => group.rule === rule);
      return <Branch key={rule} className={s.ruleBranch} active={children.some(group => group.keys.includes(currentKey) || group.key === activeGroup)}
        label={<>{localize(rule)}<span>{localize(children.reduce((sum, group) => sum + entries(group).length, 0))} {t(" 项")}</span></>}>
        {localize(() => children.map(group => <Branch key={group.key} className={s.familyBranch} active={group.keys.includes(currentKey) || group.key === activeGroup}
          label={<>{localize(group.family)}<span>{localize(entries(group).length)}/{localize(group.count)}</span></>}>
          {localize(() => <><small title={localize(group.basis)}>{t("共同异常：")}{localize(group.bad_sites.map(siteName).join(', ') || t("无共同低 Cg site"))}</small>
          <small>{t("已记录 ")}{localize(group.keys.filter(key => notes.notes[module + ':' + key]).length)}/{localize(group.count)}</small>
          <button className={s.overviewButton} aria-pressed={activeGroup === group.key} onClick={() => onGroup(group.key)}>{t("测项组概览")}</button>
          <RowButtons rows={entries(group)} currentKey={currentKey} onRow={onRow} /></>)}
        </Branch>))}
      </Branch>;
    }) : <RowButtons rows={rows} currentKey={currentKey} onRow={onRow} />)}
    {localize(!rows.length && <p className={s.caption}>{t("没有符合条件的测项，请调整搜索或筛选。")}</p>)}
  </aside>;
}
