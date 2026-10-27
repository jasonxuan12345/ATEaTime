import { t, localize, useLanguage } from "../../i18n";
import { requestReportDownload } from './reportExport';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { cgNumber, format, labels, moduleLabels, type Cell, type Detail, type Module, type Row, type Run, type Selections } from './model';
import { OverviewThumbnail } from './charts/OverviewThumbnail';
import { AnalysisWorkspace } from './charts/AnalysisWorkspace';
import { OutlierRemark, exportOutlierNotes, type OutlierNotes } from './outliers/OutlierRemark';
import { OutlierAnalysis } from './outliers/OutlierAnalysis';
import { CandidateTable } from './outliers/CandidateTable';
import { allCandidates, type OutlierCell, type SourceIssue, type SourceReport } from './outliers/model';
import type { Notes } from './charts/types';
import { downloadBlob } from './charts/export';
import { compareSortKeys, SortHeader, type SortDirection, type SortValue } from './tableSort';
import s from './review-workbench.module.css';

type Tab = 'overview' | Module | 'outlier';
type ReviewRow = Row & { outlier?: OutlierCell };
type OutlierColumn = 'test' | 'count' | 'score' | 'observation';
type SiteDiffColumn = 'test' | 'shift' | 'spec' | 'variance' | 'rule' | 'observation';
const tabs: { id: Tab; label: string }[] = [{ id: 'overview', label: '综合总览' }, { id: 'cpk', label: 'Bin1 CPK' }, { id: 'cgm', label: 'CGM' }, { id: 'mmsa', label: 'Site Diff' }, { id: 'outlier', label: 'Outlier' }];
const attention = (row: ReviewRow, tab: Tab) => tab === 'overview' ? !['PASS', 'NOT_APPLICABLE'].includes(row.status) : tab === 'outlier' ? !!row.outlier?.candidates.length : !!row[tab] && !row[tab]?.override && !['PASS', 'NOT_APPLICABLE'].includes(row[tab]!.status);
function CgmSites({ cell }: { cell: Cell | null }) {
  const language = useLanguage();
  const sites = cell?.sites ?? [];
  const usable = sites.filter(site => site.cg !== null && site.status === 'CALCULATED');
  const worst = [...usable].sort((a, b) => {
    const left = cgNumber(a.cg)!, right = cgNumber(b.cg)!;
    return left === right ? (a.site ?? -1) - (b.site ?? -1) : left < right ? -1 : 1;
  }).slice(0, 3);
  return <>{localize(worst.map(site => 'S' + site.site).join(t("、")) || '—')}
    <small data-status={cell?.override ? 'PASS' : cell?.status}>{localize(cell?.override ? t("按 Bin1 CPK 接受规则判为通过") : cell && labels[cell.status])}</small>
    {localize(sites.filter(site => !usable.includes(site)).map(site => <small className={s.missingSite} key={site.site}>
      Site {localize(site.site ?? t("未知"))} {localize(site.n === 0 ? t("无数据") : site.status === 'INSUFFICIENT_DATA' ? t("样本不足") : t("无法计算"))}
    </small>))}
  </>;
}
export function ReviewWorkbench({ run, rows, selections, demo, stale, selectedKey, onSelect, onDirty, reports, issues, detail, onOutlierNotes, onTab }: {
  onTab: (tab: string) => void; onOutlierNotes: (notes: OutlierNotes) => void; detail: Detail | null; run: Run | null; rows: ReviewRow[]; selections: Selections; demo: boolean; stale: boolean; selectedKey: string;
  onSelect: (key: string) => void; onDirty: (dirty: boolean) => void; reports: SourceReport[]; issues: SourceIssue[];
}) {
  const language = useLanguage();
  const [tab, setTab] = useState<Tab>('overview'), [filter, setFilter] = useState('attention'), [search, setSearch] = useState('');
  useEffect(()=>onTab(tab),[tab,onTab]);
  const [narrow, setNarrow] = useState(false), [page, setPage] = useState(0), [dirty, setDirty] = useState(false);
  const [outlierNotes, setOutlierNotes] = useState<OutlierNotes>({});
  const receiveOutlierNotes=useCallback((value:OutlierNotes)=>{setOutlierNotes(value);onOutlierNotes(value);},[onOutlierNotes]);
  useEffect(()=>{setOutlierNotes({});onOutlierNotes({});},[run,onOutlierNotes]);
  const [navigationTarget,setNavigationTarget]=useState<HTMLDivElement|null>(null);
  const [notes, setNotes] = useState<Notes>({ notes: {}, version: 0 });
  const [metricSort, setMetricSort] = useState<Partial<Record<'cpk' | 'cgm', 'asc' | 'desc'>>>({});
  const [outlierSort, setOutlierSort] = useState<{ column: OutlierColumn; direction: SortDirection } | null>(null);
  const [siteDiffSort, setSiteDiffSort] = useState<{ column: SiteDiffColumn; direction: SortDirection } | null>(null);
  useEffect(() => { setNotes({notes:{},version:0}); }, [run]);
  const [chipView, setChipView] = useState(false), [chipKey, setChipKey] = useState('');
  const reportDirty = useCallback((value: boolean) => { setDirty(value); onDirty(value); }, [onDirty]);
  const candidates = useMemo(() => allCandidates(reports), [reports]);
  const filtered = useMemo(() => rows.filter(r => (tab === 'overview' || tab === 'outlier' ? true : !!r[tab]) && `${r.number} ${r.name}`.toLowerCase().includes(search.toLowerCase()) && (filter === 'all' || (filter === 'attention' ? attention(r, tab) : filter === 'outlier' ? !!r.outlier?.candidates.length : filter === 'review' ? !['PASS', 'FAIL', 'RISK', 'NOT_APPLICABLE'].includes(tab === 'overview' || tab === 'outlier' ? r.status : r[tab]!.status) : (tab === 'overview' || tab === 'outlier' ? r.status : r[tab]!.override ? 'PASS' : r[tab]!.status) === filter))), [rows, tab, search, filter]);
  const shown = useMemo(() => {
    if (tab === 'mmsa' && siteDiffSort) {
      const key = (row: ReviewRow): SortValue[] => {
        switch (siteDiffSort.column) {
          case 'test': return [row.number, row.name];
          case 'shift': return [cgNumber(row.mmsa?.value)];
          case 'spec': return [row.mmsa?.shift_spec_pct];
          case 'variance': return [row.mmsa?.var_ratio];
          case 'rule': return [row.mmsa?.rule_id];
          case 'observation': return [notes.notes['mmsa:' + row.key]?.state];
        }
      };
      return [...filtered].sort((a, b) => compareSortKeys(key(a), key(b), siteDiffSort.direction));
    }
    if (tab === 'outlier' && outlierSort) {
      const key = (row: ReviewRow): SortValue[] => {
        switch (outlierSort.column) {
          case 'test': return [row.number, row.name];
          case 'count': return [row.outlier?.candidates.length ?? 0];
          case 'score': return [row.outlier?.maxScore];
          case 'observation': return [outlierNotes[row.key]?.state];
        }
      };
      return [...filtered].sort((a, b) => compareSortKeys(key(a), key(b), outlierSort.direction));
    }
    if ((tab !== 'cpk' && tab !== 'cgm') || !metricSort[tab]) return filtered;
    return [...filtered].sort((a, b) => compareSortKeys([cgNumber(a[tab]?.value)], [cgNumber(b[tab]?.value)], metricSort[tab]!));
  }, [filtered, tab, metricSort, outlierSort, outlierNotes, siteDiffSort, notes]);
  function siteDiffHeader(column: SiteDiffColumn, label: string) {
    return <SortHeader label={label} direction={siteDiffSort?.column === column ? siteDiffSort.direction : undefined} onClick={() => {
      setSiteDiffSort(previous => ({ column, direction: previous?.column === column && previous.direction === 'desc' ? 'asc' : 'desc' }));
      setPage(0);
    }}/>;
  }
  function outlierHeader(column: OutlierColumn, label: string) {
    return <SortHeader label={label} direction={outlierSort?.column === column ? outlierSort.direction : undefined} onClick={() => {
      setOutlierSort(previous => ({ column, direction: previous?.column === column && previous.direction === 'desc' ? 'asc' : 'desc' }));
      setPage(0);
    }}/>;
  }
  function toggleMetricSort(metric: 'cpk' | 'cgm') {
    setMetricSort(previous => ({ ...previous, [metric]: previous[metric] === 'desc' ? 'asc' : 'desc' }));
    setPage(0);
  }
  const selected = shown.find(r => r.key === selectedKey) ?? shown[0];
  const hasMissingUnit = selected && (tab === 'overview'
    ? (['cpk', 'cgm', 'mmsa'] as const).some(m => selected[m] && !(selected[m]!.unit !== undefined ? selected[m]!.unit : selected.unit)?.trim())
    : tab === 'outlier' ? !selected.unit?.trim()
    : !(selected[tab]?.unit !== undefined ? selected[tab]?.unit : selected.unit)?.trim());
  const guard = () => !dirty || window.confirm(t('当前观察尚未保存，是否放弃编辑并继续？'));
  function choose(key: string) { if (guard()) { setDirty(false); onDirty(false); onSelect(key); } }
  function switchTab(next: Tab, fromOverview = false) { if (!guard()) return; setDirty(false); onDirty(false); setTab(next); setPage(0); if (next === 'outlier') setFilter('all'); if (fromOverview) { setFilter('all'); setSearch(''); } }
  useEffect(() => { setPage(0); }, [filter, search, tab, run]);
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (!['ArrowUp', 'ArrowDown'].includes(e.key) || (e.target instanceof HTMLElement && (e.target.closest('input,textarea,select,[contenteditable]')))) return;
      const next = shown[shown.findIndex(r => r.key === selected?.key) + (e.key === 'ArrowDown' ? 1 : -1)];
      if (next) { e.preventDefault(); choose(next.key); setPage(Math.floor(shown.indexOf(next) / 40)); }
    };
    window.addEventListener('keydown', handler); return () => window.removeEventListener('keydown', handler);
  }, [shown, selected?.key, dirty]);
  useEffect(() => { if (selected && selected.key !== selectedKey) onSelect(selected.key); }, [selected?.key, selectedKey, onSelect]);
  useEffect(()=>{const handler=(e:Event)=>{if(!stale&&(e as CustomEvent).detail==='notes'&&Object.keys(outlierNotes).length)exportOutlierNotes(outlierNotes,rows);};window.addEventListener('release-export',handler);return()=>window.removeEventListener('release-export',handler);},[outlierNotes,rows,stale]);
  useEffect(() => { const handler = async (event: Event) => {
    if (stale || !run || (event as CustomEvent).detail !== 'notes' || (tab !== 'overview' && tab !== 'outlier')) return;
    try { if (demo) downloadBlob(new Blob([JSON.stringify(notes,null,2)],{type:'application/json'}),'DEMO-observations.json');
      else { await requestReportDownload('/api/release-check/datasets/'+run.dataset_id+'/runs/'+run.id+'/observations.xlsx'); }
    } catch(error) { window.alert((error as Error).message); }
  }; window.addEventListener('release-export',handler);return()=>window.removeEventListener('release-export',handler); }, [tab,stale,run,demo,notes]);
  const currentPage = Math.min(page, Math.max(0, Math.ceil(shown.length / 40) - 1));
  return <main className={s.workbench}>
    <nav className={s.tabs} aria-label={t("检查模块")}>{localize(tabs.map(t => <button key={t.id} aria-label={localize(t.label)} aria-pressed={tab === t.id} onClick={() => switchTab(t.id)}>{localize(t.label)}<span>{localize(rows.filter(r => attention(r, t.id)).length)}</span></button>))}</nav>
    {localize(stale && <p className={s.pending}>{t("配置已修改，当前仍为上次结果。重新计算后才能导出。")}</p>)}
    <div className={`${s.body} ${narrow ? s.narrow : ''} ${stale ? s.stale : ''}`}>
      <section className={s.list} aria-label={t("测项列表")}><div className={s.tools}><strong>{localize(tab === 'outlier' ? t("Outlier 测项") : t("测项"))}</strong><button onClick={() => setNarrow(!narrow)}>{localize(narrow ? t("展开") : t("收窄"))}</button><label><input type='search' aria-label={t("搜索测项结果")} placeholder={t("搜索名称 / 编号")} value={search} onChange={e => {if(guard())setSearch(e.target.value);}} /></label></div>
        <div className={s.filters}>{localize([['attention', t("需关注")], ['all', t("全部")], ['FAIL', t("未达标")], ['RISK', t("风险")], ['review', t("需复核")], ['PASS', t("通过")], ['outlier', t("含 Outlier")]].map(([id, label]) => <button key={id} aria-pressed={filter === id} onClick={() => {if(guard())setFilter(id);}}>{localize(label)}</button>))}</div>
        {localize(tab === 'cgm' && <div className={s.familyNavigation} ref={setNavigationTarget} />)}
        {localize(tab === 'outlier' && <><p className={s.caption}>{localize(candidates.length)} {t(" 条异常候选记录 · 涉及 ")}{localize(rows.filter(r => r.outlier?.candidates.length).length)} {t(" 个测项")}</p><div className={s.tools}><button aria-pressed={!chipView} onClick={() => setChipView(false)}>{t("按测项")}</button><button aria-pressed={chipView} onClick={() => setChipView(true)}>{t("按异常候选记录")}</button></div></>)}
        {tab === 'outlier' && chipView ? <CandidateTable candidates={candidates} selectedKey={chipKey} onSelect={chip => { if (!guard()) return; setChipKey(chip.key); const row = rows.find(r => r.outlier?.candidates.some(c => c.key === chip.key)); if (row) { setFilter('all'); setSearch(''); onSelect(row.key); } }} /> : <div className={s.tableScroll}><table><thead><tr>{localize(tab === "outlier" ? outlierHeader("test", t("Test ID / 名称")) : tab === "mmsa" ? siteDiffHeader("test", t("Test ID / 名称")) : <th>{t("Test ID / 名称")}</th>)}{localize(!narrow && <>{localize(tab === 'overview' ? <><th>{t("综合状态")}</th><th>CPK / CGM / Site Diff / Outlier</th><th>{t("备注复核状态")}</th></> : tab === 'cpk' ? <><th>{t("规格 LSL ~ USL")}</th><th>Mean / Std</th><th>Cp</th><th aria-sort={metricSort.cpk === "desc" ? "descending" : metricSort.cpk === "asc" ? "ascending" : "none"}><button className={s.sortHeader} aria-label={t("按 Bin1 CPK 排序")} onClick={() => toggleMetricSort("cpk")}>Bin1 CPK <span aria-hidden="true">{localize(metricSort.cpk === "desc" ? "↓" : metricSort.cpk === "asc" ? "↑" : "↕")}</span></button></th><th>{t("备注复核状态")}</th></> : tab === 'cgm' ? <><th aria-sort={metricSort.cgm === "desc" ? "descending" : metricSort.cgm === "asc" ? "ascending" : "none"}><button className={s.sortHeader} aria-label={t("按 min Cg 排序")} onClick={() => toggleMetricSort("cgm")}>min Cg <span aria-hidden="true">{localize(metricSort.cgm === "desc" ? "↓" : metricSort.cgm === "asc" ? "↑" : "↕")}</span></button></th><th>{t("Cg 最低的三个 Site")}</th><th>{t("备注复核状态")}</th></> : tab === 'mmsa' ? <>{localize(siteDiffHeader("shift", t("Site 均值差（σ）")))}{localize(siteDiffHeader("spec", t("Site 均值差占规格范围比例")))}{localize(siteDiffHeader("variance", t("Site 标准差比")))}{localize(siteDiffHeader("rule", t("规则号")))}{localize(siteDiffHeader("observation", t("备注复核状态")))}</> : <>{localize(outlierHeader("count", t("异常候选记录条数")))}{localize(outlierHeader("score", t("最大偏离程度（稳健σ）")))}{localize(outlierHeader("observation", t("备注复核状态")))}</>)}</>)}</tr></thead><tbody>{shown.slice(currentPage * 40, (currentPage + 1) * 40).map(row => <tr key={row.key} aria-selected={row.key === selected?.key} onClick={() => choose(row.key)}><td><button aria-label={row.name} aria-expanded={row.key === selected?.key} onClick={e => { e.stopPropagation(); choose(row.key); }}><small>{localize(row.number ?? '—')}</small><strong>{row.name || t("名称未记录")}</strong></button></td>{localize(!narrow && <>{localize(tab === 'overview' ? <><td data-status={row.status}>{localize(labels[row.status])}</td><td className={s.moduleMarks}>{localize((['cpk', 'cgm', 'mmsa'] as const).map(m => <span key={m} title={localize(t("{0}：{1}", moduleLabels[m], row[m]?.override ? '按 Bin1 CPK 接受规则判为通过' : row[m] ? labels[row[m]!.status] : '无匹配结果'))} data-status={row[m]?.override ? 'PASS' : row[m]?.status}>{localize(m === 'mmsa' ? 'Site Diff' : m.toUpperCase())}</span>))}<span data-status={row.outlier?.candidates.length ? 'RISK' : 'PASS'}>Outlier</span></td><td>{localize((['cpk','cgm','mmsa'] as const).map(m=><small key={m}>{localize(moduleLabels[m])}{t("：")}{localize(notes.notes[m+':'+row.key] ? t("已记录") : t("未记录"))}</small>))}</td></> : tab === 'cpk' ? <><td>{localize(format(row.lsl, 3))} ~ {localize(format(row.usl, 3))}</td><td>{localize(format(row.cpk?.mean,4))} / {localize(format(row.cpk?.std,4))}</td><td>{localize(format(row.cpk?.cp))}</td><td data-status={row.cpk?.status}><strong>{localize(format(row.cpk?.value))}</strong><small>{localize(row.cpk && labels[row.cpk.status])}</small></td><td>{localize(notes.notes['cpk:'+row.key]?.state ?? t("未记录"))}</td></> : tab === 'cgm' ? <><td data-status={row.cgm?.override ? 'PASS' : row.cgm?.status}>{localize(format(row.cgm?.value))}</td><td><CgmSites cell={row.cgm} /></td><td>{localize(notes.notes['cgm:'+row.key]?.state ?? t("未记录"))}</td></> : tab === 'mmsa' ? <><td data-status={row.mmsa?.status}>{localize(format(row.mmsa?.value))}</td><td>{localize(format(row.mmsa?.shift_spec_pct))}%</td><td>{localize(format(row.mmsa?.var_ratio))}×</td><td>{localize(row.mmsa?.rule_id ?? '—')}</td><td>{localize(notes.notes['mmsa:'+row.key]?.state ?? t("未记录"))}</td></> : <><td>{localize(row.outlier?.candidates.length ?? 0)}</td><td>{localize(format(row.outlier?.maxScore, 1))} {t(" 倍")}</td><td>{localize(outlierNotes[row.key]?.state ?? t("未记录"))}</td></>)}</>)}</tr>)}</tbody></table>{localize(!shown.length && <p className={s.empty}>{t("没有符合条件的测项，请调整筛选或搜索。")}</p>)}</div>}
        <div className={s.pager}><span>{localize(shown.length)} {t(" 个测项")}</span><button disabled={!currentPage} onClick={() => setPage(currentPage - 1)}>{t("上一页")}</button><span>{localize(currentPage + 1)} / {localize(Math.max(1, Math.ceil(shown.length / 40)))}</span><button disabled={(currentPage + 1) * 40 >= shown.length} onClick={() => setPage(currentPage + 1)}>{t("下一页")}</button></div>
      </section>
      <section className={s.evidence} aria-label={t("当前测项分析")}>{localize(hasMissingUnit && <p className={s.unitNotice} role='note'>{t("单位缺失")}</p>)}{!selected || !run ? <p className={s.empty}>{t("选择测项查看结果；没有结果时，请先打开“数据源与计算配置”并计算。")}</p> : tab === 'overview' ? <><h2>{localize(selected.number)} · {selected.name}</h2><p className={s.caption}>{t("各模块结果 · 点击一行查看图表")}</p><div className={s.scorecard}>{(['cpk', 'cgm', 'mmsa'] as const).map(m => <button key={m} onClick={() => switchTab(m, true)}><strong>{localize(moduleLabels[m])}</strong><b data-status={selected[m]?.override ? 'PASS' : selected[m]?.status}>{localize(format(selected[m]?.value))} · {localize(selected[m]?.override ? t("按 Bin1 CPK 接受规则判为通过") : selected[m] ? labels[selected[m]!.status] : t("无匹配结果"))}</b><span>{t("来源：")}{selected[m]?.filename ?? (demo ? t("示例数据") : t("未选择"))}</span><p>{localize(selected[m]?.reason ?? t("该模块未选择此测项，或测项信息不一致。"))}</p>{localize(attention(selected,m) && <OverviewThumbnail row={selected} module={m} run={run} selections={selections} demo={demo} />)}</button>)}<button onClick={() => switchTab('outlier', true)}><strong>Outlier</strong><b>{localize(selected.outlier?.label ?? t("未计算"))} · {localize(selected.outlier?.candidates.length ?? 0)} {t(" 条异常候选记录")}</b><span>{t("来源：")}{localize(selected.outlier?.filenames.join(t("、")) || t("未选择"))}</span><p>{t("Outlier 是异常候选记录，不是缺陷。没有异常候选记录不等于每组都已评估。")}</p></button></div></> : tab === 'outlier' ? <><OutlierAnalysis rows={rows.filter((r): r is ReviewRow & { outlier: OutlierCell } => !!r.outlier)} rowKey={selected.key} reports={reports} issues={issues} selectedKey={chipKey} stale={stale} onSelectRow={choose} onSelectChip={chip => setChipKey(chip.key)} onClose={() => switchTab('overview')} embedded /><div className={s.outlierRemark}><OutlierRemark row={selected} rows={rows} notes={outlierNotes} onNotes={receiveOutlierNotes} onDirty={reportDirty} stale={stale} onNext={()=>{const next=shown[shown.indexOf(selected)+1];if(next){setDirty(false);onDirty(false);onSelect(next.key);}}} /></div></> : <AnalysisWorkspace key={run.id} run={run} rows={shown.filter(r => !r.key.startsWith('outlier:'))} row={selected} selections={selections} initialModule={tab} demo={demo} navigationTarget={navigationTarget} onSelect={key=>{setDirty(false);onDirty(false);onSelect(key);}} onClose={() => switchTab('overview')} onDirtyChange={reportDirty} initialNotes={notes} onNotesChange={setNotes} detail={detail?.row.key===selected.key ? detail : null} embedded stale={stale} />}</section>
    </div>
  </main>;
}
