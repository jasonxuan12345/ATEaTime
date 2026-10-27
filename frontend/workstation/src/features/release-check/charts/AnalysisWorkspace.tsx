import { t, localize, useLanguage, localizeReport } from "../../../i18n";
import { requestReportDownload } from '../reportExport';
import { createPortal } from 'react-dom';
import { BaseEChart } from '../../../components/charts/BaseEChart';
import { sampleShape, density } from './sampleShapes';
import { workbenchOptions, compactChartOption } from './workbenchOptions';
import { SiteSequencePlot } from './SiteSequencePlot';
import { SiteStatistics } from './SiteStatistics';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { cgNumber, format, labels, moduleLabels, request, type Detail, type Module, type Row, type Run, type Selections } from '../model';
import { chartOption, matches, orderedSites } from './options';
import { demoChart, demoMatrix } from './demo';
import { ReleasePlot, type PlotHandle } from './ReleasePlot';
import { ChartPairControls } from './ChartPairControls';
import { chartNames, chartTitle, chartUnavailable, moduleForRow } from './chartScenarios';
import { useChartPair } from './useChartPair';
import { selectionEvidence } from './selectionEvidence';
import { FamilyHeatmap } from './FamilyHeatmap';
import { AnalysisNavigation } from './AnalysisNavigation';
import { AnalysisContext } from './AnalysisContext';
import { ChartSlot } from './ChartSlot';
import { ObservationPanel } from './ObservationPanel';
import { downloadBlob, exportChartGroup } from './export';
import { downloadMatrix } from './matrixExport';
import { siteName, type ChartData, type ChartKind, type ChartSettings, type MatrixData, type Notes, type Pick, type SamplePage } from './types';
import s from './charts.module.css';

const initialSettings = (): ChartSettings => ({ sites: null, sort: 'site', fullSpec: true, stdMode: false, connect: false, wafer: '', outOfSpec: false, selected: {} });
export function AnalysisWorkspace({ run, rows, row, selections, initialModule, demo, onSelect, onClose, onDirtyChange, embedded = false, stale = false, initialNotes, onNotesChange, onData, detail, navigationTarget }: {
  run: Run; rows: Row[]; row: Row; selections: Selections; initialModule: Module; demo: boolean;
  onSelect: (key: string) => void; onClose: () => void; onDirtyChange?: (dirty: boolean) => void; embedded?: boolean; stale?: boolean; initialNotes?: Notes; onNotesChange?: (notes: Notes) => void; onData?: (data: ChartData) => void; detail?: Detail | null; navigationTarget?: HTMLElement | null }) {
  const language = useLanguage();
  const [preferredModule, setModule] = useState<Module>(initialModule);
  const module = moduleForRow(row, embedded ? initialModule : preferredModule);
  const pair = useChartPair(`${run.id}:${row.key}:${module}`, row, module);
  const kind = embedded ? module === 'cpk' ? 'histogram' : module === 'cgm' ? 'cg' : pair.scenario.primary : pair.primary, secondaryKind = pair.secondary;
  const [settings, setSettings] = useState<ChartSettings>(initialSettings), [bins, setBins] = useState(24), [width, setWidth] = useState('');
  const [loadedData, setData] = useState<ChartData | null>(null), [error, setError] = useState(''), [retry, setRetry] = useState(0);
  const data = loadedData?.row.key === row.key && loadedData.context.module === module ? loadedData : null;
  const [matrix, setMatrix] = useState<MatrixData | null>(null), [matrixError, setMatrixError] = useState(''), [matrixOffset, setMatrixOffset] = useState(0);
  const [groups, setGroups] = useState<MatrixData['groups']>([]);
  const [matrixSort, setMatrixSort] = useState<'report' | 'cg'>('report');
  const [groupKey, setGroupKey] = useState(''), [overview, setOverview] = useState(false), [groupChosen, setGroupChosen] = useState(true);
  const [grouped, setGrouped] = useState(true), [search, setSearch] = useState(''), [navFilter, setNavFilter] = useState('all'), [samplePage, setSamplePage] = useState<SamplePage | null>(null), [offset, setOffset] = useState(0);
  const [sampleError, setSampleError] = useState(''), [dirty, setDirty] = useState(false), [notes, setNotes] = useState<Notes>(() => initialNotes ?? { notes: {}, version: 0 });
  const [notice, setNotice] = useState(''), [exporting, setExporting] = useState(false), [expanded, setExpanded] = useState<'primary' | 'secondary' | null>(null), [reset, setReset] = useState(0);
  const [comparisonSite, setComparisonSite] = useState(''), [matrixWide, setMatrixWide] = useState(false);
  useEffect(() => { onNotesChange?.(notes); }, [notes, onNotesChange]);
  useEffect(() => { if(data) onData?.(data); }, [data,onData]);
  const mainPlot = useRef<PlotHandle>(null), secondaryPlot = useRef<PlotHandle>(null);
  const base = `/datasets/${run.dataset_id}/runs/${run.id}`;
  const scopeKeys = useMemo(() => rows.map(r => r.key), [rows]);
  const group = groups.find(g => g.keys.includes(row.key)) ?? null;
  const visibleRows = useMemo(() => rows.filter(r => `${r.number} ${r.name}`.toLowerCase().includes(search.toLowerCase())
    && (navFilter === 'all' || (navFilter === 'unrecorded' ? r.key === row.key || !notes.notes[module + ':' + r.key] : !['PASS', 'NOT_APPLICABLE'].includes(r.status)))),
    [rows, search, navFilter, row.key, notes.notes, module]);
  const activeGroup = groups.find(g => g.key === groupKey) ?? null;
  const visibleKeys = useMemo(() => new Set(visibleRows.map(r => r.key)), [visibleRows]);
  const navigationGroups = useMemo(() => groups.filter(g => g.keys.some(key => visibleKeys.has(key))), [groups, visibleKeys]);
  const activeKeys = useMemo(() => new Set(activeGroup?.keys), [activeGroup]);
  const navigationKeys = useMemo(() => visibleRows.filter(r => !overview || !groupKey || activeKeys.has(r.key)).map(r => r.key),
    [visibleRows, overview, groupKey, activeKeys]);
  useEffect(() => { setSettings(initialSettings()); setComparisonSite(''); setOverview(false); setOffset(0); }, [initialModule,row.key]);
  const guard = useCallback(() => !dirty || window.confirm(t('当前观察尚未保存，是否放弃编辑并继续？')), [dirty]);
  const reportDirty = useCallback((value: boolean) => { setDirty(value); onDirtyChange?.(value); }, [onDirtyChange]);
  const choose = (key: string, force = false) => { if ((force || guard()) && rows.some(r => r.key === key)) {
    setExpanded(null); onSelect(key); setSettings(initialSettings()); setOffset(0); setComparisonSite(''); setGroupChosen(true); setDirty(false); } };
  const move = (direction: number, force = false) => {
    const keys = navigationKeys;
    const next = keys[keys.indexOf(row.key) + direction]; if (next) choose(next, force);
  };
  useEffect(() => {
    const before = (event: BeforeUnloadEvent) => { if (dirty) { event.preventDefault(); event.returnValue = ''; } };
    const escape = (event: KeyboardEvent) => { if (event.key === 'Escape') setExpanded(null); };
    window.addEventListener('beforeunload', before); window.addEventListener('keydown', escape);
    return () => { window.removeEventListener('beforeunload', before); window.removeEventListener('keydown', escape); };
  }, [dirty]);
  useEffect(() => {
    const abort = new AbortController(); setData(null); setError('');
    if (demo) { setData(demoChart(row, module, selections)); return () => abort.abort(); }
    const query = new URLSearchParams({ bins: String(bins) }); if (width) query.set('bin_width', width);
    request<ChartData>(`${base}/items/${encodeURIComponent(row.key)}/charts/${module}?${query}`, { signal: abort.signal })
      .then(value => { if (!abort.signal.aborted) setData(value); }).catch(err => { if (!abort.signal.aborted) setError(err.message); });
    return () => abort.abort();
  }, [base, row.key, module, bins, width, retry, demo, selections]);
  useEffect(() => {
    const abort = new AbortController(); setMatrix(null); setMatrixError('');
    if (demo) { const all = demoMatrix(run, selections), scoped = all.rows.filter(r => scopeKeys.includes(r.key));
      const scopedGroups = all.groups.map(group => ({ ...group, keys: group.keys.filter(key => scopeKeys.includes(key)) })).filter(group => group.keys.length).map(group => ({ ...group, count: group.keys.length }));
      const allowed = scoped.filter(r => !groupKey || r.group === groupKey);
      if (matrixSort === 'cg') allowed.sort((a, b) => Math.min(...a.sites.map(s => cgNumber(s.cg) ?? Infinity)) - Math.min(...b.sites.map(s => cgNumber(s.cg) ?? Infinity)));
      setGroups(scopedGroups); setMatrix({ ...all, groups: scopedGroups, rows: allowed.slice(matrixOffset, matrixOffset + 100), total: allowed.length, offset: matrixOffset }); return () => abort.abort(); }
    const query = new URLSearchParams({ offset: String(matrixOffset) }); if (groupKey) query.set('group', groupKey);
    request<MatrixData>(`${base}/chart-matrix?${query}`, { method: 'POST', signal: abort.signal,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ keys: scopeKeys, sort: matrixSort }) })
      .then(value => { if (!abort.signal.aborted) { setMatrix(value); setGroups(value.groups); } }).catch(err => { if (!abort.signal.aborted) setMatrixError(err.message); });
    return () => abort.abort();
  }, [base, matrixOffset, groupKey, matrixSort, retry, demo, run, selections, scopeKeys]);
  useEffect(() => {
    const abort = new AbortController();
    if (!demo) request<Notes>(`${base}/observations`, { signal: abort.signal }).then(value => { if (!abort.signal.aborted) setNotes(value); }).catch(err => { if (!abort.signal.aborted) setNotice(err.message); });
    return () => abort.abort();
  }, [base, demo]);
  useEffect(() => {
    const abort = new AbortController(); setSamplePage(null); setSampleError('');
    if (!data) return () => abort.abort();
    if (!demo && offset === 0 && !Object.keys(settings.selected).length && data.sample_page) {
      setSamplePage(data.sample_page); return () => abort.abort();
    }
    if (demo) {
      const selected = data.points.filter(p => matches(p, settings.selected));
      setSamplePage({ points: selected.slice(offset, offset + 100), total: selected.length, offset, limit: 100, context: data.context });
    } else request<SamplePage>(`${base}/items/${encodeURIComponent(row.key)}/samples/${module}?offset=${offset}`, { method: 'POST', signal: abort.signal,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(settings.selected) })
      .then(value => { if (!abort.signal.aborted) setSamplePage(value); }).catch(err => { if (!abort.signal.aborted) setSampleError(err.message); });
    return () => abort.abort();
  }, [data, settings.selected, offset, base, row.key, module, demo]);
  const pick = useCallback((value: Pick) => {
    setOffset(0); setSettings(previous => ({ ...previous, selected: value.selection ?? (value.site !== undefined ? { sites: [value.site ?? -1] } : {}) }));
  }, []);
  function chooseMatrix(key: string, site?: number) {
    if (!guard()) return;
    if (!rows.find(r => r.key === key)?.cgm) { setNotice('此项没有 CGM 来源，无法从 Cg 矩阵打开。'); return; }
    choose(key, true); setModule('cgm'); pair.restore();
    if (site !== undefined) pick({ site });
    setNotice('矩阵使用 CGM 来源，已打开对应 CGM 数据；未选择 CPK 样本。');
  }
  const primarySettings = { ...settings, stdMode: pair.primaryStd };
  const secondarySettings = { ...settings, stdMode: pair.secondaryStd };
  const viewData = useMemo(() => data ? selectionEvidence(data, samplePage, settings.selected) : null, [data, samplePage, settings.selected]);
  const option = useMemo(() => { if (!viewData) return {}; const option = chartOption(viewData, kind, { ...settings, stdMode: pair.primaryStd }); if (embedded && kind === 'histogram') { const curve = density(viewData.points.filter(p => settings.sites === null || settings.sites.includes(p.site ?? -1)).map(p => p.value)); return {...option,yAxis:[option.yAxis as object,{type:'value' as const,show:false}],series:[...(Array.isArray(option.series)?option.series:[]),{type:'line' as const,name:t('核密度（样本）'),data:curve,yAxisIndex:1,showSymbol:false,lineStyle:{color:'#384f63',width:2}}]}; } return option; }, [viewData, kind, settings, pair.primaryStd, language]);
  const secondaryOption = useMemo(() => viewData ? chartOption(viewData, secondaryKind, { ...settings, stdMode: pair.secondaryStd }) : {}, [viewData, secondaryKind, settings, pair.secondaryStd, language]);
  const primaryReason = chartUnavailable(data, kind, pair.primaryStd), secondaryReason = chartUnavailable(data, secondaryKind, pair.secondaryStd);
  const hasReportCg = !!data?.sites.some(site => site.cg !== null);
  const selectChart = (role: 'primary' | 'secondary', value: ChartKind) => { pair.select(role, value, value === 'cg' && !hasReportCg); setExpanded(null); };
  const hasChart = (value: ChartKind) => (embedded && value === 'sequence') || kind === value || (embedded && module === 'cpk' && (value === 'box' || value === 'wafer' || value === 'sequence')) || (!overview && secondaryKind === value);
  async function exportImages(full: boolean) {
    if (!data) return; setExporting(true); setNotice('');
    try {
      const fullSettings = { ...settings, sites: null, fullSpec: true, selected: {} };
      await exportChartGroup(`${row.name} · ${t(full ? '完整范围' : '当前视图')}${demo ? t(' · 合成示例') : ''}`,
        [t('来源 {0} / {1} / SHA256 {2}', data.context.filename, module, data.context.source_sha256), t('测项 {0} · {1} · 有效 n={2} · {3}', row.number, row.unit ?? '', data.total_points, t(data.context.selection_policy)),
          `LSL=${format(data.statistics.lsl, 5)} USL=${format(data.statistics.usl, 5)} Mean=${format(data.statistics.mean, 5)} Std=${format(data.statistics.std, 5)} · ${t(data.sampled ? '点图有抽样，统计全量' : '全量')}`,
          t('规则 {0} · {1}', data.context.rules_hash, full ? t('导出全部 site') : t('当前可见 site ') + (settings.sites?.join(', ') ?? t('全部'))),
          t('图组 {0}', embedded ? t('分 site 测试次序 / ') + t(module === 'cpk' ? '直方图 / Q-Q / 箱线 / Wafer 或 site 统计' : module === 'cgm' ? '各 site Cg / 去均值波动 / site 统计' : '小提琴 / 直方图 / site 统计') : t(chartTitle(kind, pair.primaryStd)) + (overview ? t('；测项组 Cg 表另导出 CSV') : ' / ' + t(chartTitle(secondaryKind, pair.secondaryStd)))), ...(full ? [t('完整样本范围，保留当前图组类型与排序')] : []), `Remark: ${notes.remarks?.[module + ':' + row.key]?.remark ?? notes.notes[module + ':' + row.key]?.remark ?? row[module]?.diagnosis?.remark ?? t('未记录观察')}`],
        (embedded ? workbenchOptions(data, module, kind, { ...(full ? fullSettings : settings), stdMode: pair.primaryStd }) : full ? [chartOption(data, kind, { ...fullSettings, stdMode: pair.primaryStd }), ...(!overview ? [chartOption(data, secondaryKind, { ...fullSettings, stdMode: pair.secondaryStd })] : [])] : [option, ...(!overview ? [secondaryOption] : [])]),
        full || embedded ? undefined : [mainPlot.current?.image() ?? null, ...(!overview ? [secondaryPlot.current?.image() ?? null] : [])]);
    } catch (err) { setNotice((err as Error).message); } finally { setExporting(false); }
  }
  async function exportNotes() {
    try { if (demo) { downloadBlob(new Blob([JSON.stringify(localizeReport(notes), null, 2)], { type: 'application/json' }), 'DEMO-observations.json'); return; }
      await requestReportDownload(`/api/release-check${base}/observations.xlsx`); } catch (err) { setNotice((err as Error).message); }
  }
  async function exportMatrix() {
    setExporting(true);
    try {
      const all: MatrixData['rows'] = [];
      if (demo) all.push(...demoMatrix(run, selections).rows.filter(r => scopeKeys.includes(r.key) && (!groupKey || r.group === groupKey)));
      else for (let start = 0; ; start += 200) {
        const query = new URLSearchParams({ offset: String(start), limit: '200' }); if (groupKey) query.set('group', groupKey);
        const page = await request<MatrixData>(`${base}/chart-matrix?${query}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ keys: scopeKeys, sort: matrixSort }) });
        all.push(...page.rows); if (all.length >= page.total || !page.rows.length) break;
      }
      downloadMatrix(all, run.rules_hash);
    } catch (err) { setNotice((err as Error).message); } finally { setExporting(false); }
  }
  useEffect(() => {
    if (!embedded) return;
    const handler = (event: Event) => {
      if (stale) return;
      const action = (event as CustomEvent<string>).detail;
      if (action === 'notes') void exportNotes();
      else if (action === 'matrix') void exportMatrix();
      else if (data && action === 'images') void exportImages(false);
      else if (data && action === 'full') void exportImages(true);
      else if (data && action === 'context') downloadBlob(new Blob([JSON.stringify(localizeReport({ context: data.context, statistics: data.statistics, quality: data.quality, excluded: data.excluded_counts, settings, observation: notes.notes[module + ':' + row.key] }), null, 2)], {type:'application/json'}), 'release-chart-context.json');
    };
    window.addEventListener('release-export', handler); return () => window.removeEventListener('release-export', handler);
  });
  const shownN = data ? orderedSites(data, settings).reduce((sum, site) => sum + site.n, 0) : 0;
  const selectedIndex = navigationKeys.indexOf(row.key);
  const hasSelection = !overview || groupChosen;
  function openGroup(key: string) {
    if (!guard()) return;
    setExpanded(null); setGroupKey(key); setMatrixOffset(0); setOverview(true); setGroupChosen(false); setMatrixWide(false);
    setSettings(initialSettings()); setComparisonSite(''); setOffset(0); setDirty(false);
  }
  function changeModule(next: Module) {
    if (!guard()) return;
    setOverview(false); setComparisonSite(''); setModule(next); setExpanded(null); setSettings(initialSettings()); setOffset(0); setDirty(false);
    setNotice('已切换本模块数据，样本选择已清除；不同来源不自动联动。');
  }
  return <section className={`${s.workspace} ${embedded ? s.embedded : ''}`}  aria-label={t("测项图表分析")}>
    {localize(!embedded && <header className={s.header}><button onClick={() => { if (guard()) onClose(); }}>{t("← 返回结果列表")}</button><h2>{t("测项 / 测项组分析")}</h2>
      <div><button disabled={!hasSelection || selectedIndex <= 0} onClick={() => move(-1)}>{t("上一个异常项")}</button><button disabled={!hasSelection || selectedIndex < 0 || selectedIndex >= navigationKeys.length - 1} onClick={() => move(1)}>{t("下一个异常项")}</button></div></header>)}
    <div className={s.body}>
      {localize((!embedded || module === 'cgm') && (embedded && navigationTarget ? createPortal(<AnalysisNavigation rows={visibleRows} groups={groups} currentKey={hasSelection ? row.key : ''} activeGroup={overview ? groupKey : ''} module={module} notes={notes}
        grouped={grouped} search={search} filter={navFilter} onSearch={setSearch} onFilter={setNavFilter} onGrouped={setGrouped}
        onRow={key => { if (guard()) { choose(key, true); setOverview(false); } }} onGroup={openGroup} />,navigationTarget) : <AnalysisNavigation rows={visibleRows} groups={groups} currentKey={hasSelection ? row.key : ''} activeGroup={overview ? groupKey : ''} module={module} notes={notes}
        grouped={grouped} search={search} filter={navFilter} onSearch={setSearch} onFilter={setNavFilter} onGrouped={setGrouped}
        onRow={key => { if (guard()) { choose(key, true); setOverview(false); } }} onGroup={openGroup} />))}

      <main className={s.content}>
        {localize(!embedded && <AnalysisContext row={hasSelection ? row : null} group={activeGroup} module={module} selections={selections} data={hasSelection ? data : null} kind={kind} />)}
        {localize(!embedded && demo && <p className={s.notice}>{t("合成示例图，仅演示交互，图形统计与列表示例指标独立，不代表真实检测结果。")}</p>)}
        {localize(notice && <p role='status' className={s.caption}>{localize(notice)}</p>)}
        {localize(matrixError && !overview && <p role='alert' className={s.error}>{localize(matrixError)}<button onClick={() => setRetry(retry + 1)}>{t("重试分组")}</button></p>)}
        {localize(!embedded && hasSelection && <div className={s.tabs}>{localize((['cpk', 'cgm', 'mmsa'] as const).map(m => <button key={m} disabled={!row[m]} aria-pressed={module === m} onClick={() => changeModule(m)}>{localize(moduleLabels[m])}</button>))}</div>)}
        {embedded && <div className={s.diagnosis}><strong>{localize(row.number)} · {row.name}</strong><span>{localize(row[module]?.rule_id ?? t("无命中规则"))} · {localize(row[module]?.override ? t("按 Bin1 CPK 接受规则判为通过") : row[module] ? labels[row[module]!.status] : t("未计算"))}</span><span>{localize(row[module]?.reason)}</span><span>{t("来源：")}{data?.context.filename ?? row[module]?.filename ?? (demo ? t("示例数据") : t("正在读取"))}</span>{localize(module === 'mmsa' && detail?.details.mmsa?.s2s && <span>{t("Site Diff 检查触发条件：bin1 CPK ")}{localize(format(detail.details.mmsa.s2s.overall_bin1_cpk))} · min Cg {localize(format(detail.details.mmsa.s2s.cgm_min_cg))} {t(" · 不及格 site ")}{localize(detail.details.mmsa.s2s.cgm_failed_site_count)} / {localize(detail.details.mmsa.s2s.site_count)} {t(" · Site 均值差占规格范围比例 ")}{localize(format(detail.details.mmsa.s2s.shift_spec_pct))}{t("% · Site 标准差比 ")}{localize(format(detail.details.mmsa.s2s.var_ratio))}×</span>)}<details><summary>{t("详情")}</summary><pre>{JSON.stringify(localizeReport({context:data?.context,excluded:data?.excluded_counts,rules:run.rules_hash}),null,2)}</pre></details></div>}
        {localize(hasSelection && data && <>      <div className={s.metrics}><span>{t("有效 n")}<strong>{localize(data.total_points)}</strong></span><span>Mean<strong>{localize(format(data.statistics.mean, 5))}</strong></span><span>Std<strong>{localize(format(data.statistics.std, 5))}</strong></span>
        <span>Cp<strong>{localize(format(data.statistics.cp))}</strong></span>{localize(module === 'cpk' && <span>Cpk<strong>{localize(format(data.statistics.cpk))}</strong></span>)}<span>{t("可见 n")}<strong>{localize(shownN)}</strong></span><span>{t("偏移度")}<strong>{localize(data.statistics.mean !== null && data.statistics.lsl !== null && data.statistics.usl !== null && data.statistics.usl > data.statistics.lsl ? format(Math.abs(data.statistics.mean - (data.statistics.usl + data.statistics.lsl) / 2) / ((data.statistics.usl - data.statistics.lsl) / 2)) : '—')}</strong></span></div>
</>)}
        {localize(!embedded && hasSelection && <><ChartPairControls data={data} scenario={pair.scenario} primary={kind} secondary={secondaryKind} manual={pair.manual} family={overview}
          onPrimary={value => selectChart('primary', value)} onSecondary={value => selectChart('secondary', value)}
          onSwap={() => { pair.swap(); setExpanded(null); }} onRestore={() => { pair.restore(); setExpanded(null); setReset(reset + 1); }} />
          <div className={s.tools} aria-label={t("主图切换")}>{localize((['histogram', 'cg', 'box', 'sequence', 'wafer'] as const).map(k => <button key={k} aria-pressed={kind === k} disabled={!!chartUnavailable(data, k)} title={localize(chartUnavailable(data, k) ?? '')} onClick={() => selectChart('primary', k)}>{localize(k === 'cg' && !hasReportCg ? t("各 site Std") : chartNames[k])}</button>))}</div></>)}
        {localize(overview && <div className={s.tools} aria-label={t("测项组浏览")}>
          <button disabled={navigationGroups.findIndex(g => g.key === groupKey) <= 0} onClick={() => openGroup(navigationGroups[navigationGroups.findIndex(g => g.key === groupKey) - 1].key)}>{t("上一个组")}</button>
          <button disabled={navigationGroups.findIndex(g => g.key === groupKey) < 0 || navigationGroups.findIndex(g => g.key === groupKey) >= navigationGroups.length - 1} onClick={() => openGroup(navigationGroups[navigationGroups.findIndex(g => g.key === groupKey) + 1].key)}>{t("下一个组")}</button>
          <button aria-pressed={matrixWide} onClick={() => setMatrixWide(!matrixWide)}>{localize(matrixWide ? t("收起矩阵宽视图") : t("展开矩阵宽视图"))}</button>
        </div>)}
        {localize(embedded && !data && <div className={s.notice} role={error ? 'alert' : 'status'}>{localize(error || t("正在读取图表…"))}{localize(error && <button onClick={()=>setRetry(retry+1)}>{t("重试图表")}</button>)}</div>)}
        {localize(embedded && data && <div className={s.sites}>{localize(data.sites.map(site=><label key={site.site}><input type='checkbox' aria-label={localize(t("显示 ")+siteName(site.site))} checked={settings.sites===null||settings.sites.includes(site.site??-1)} onChange={e=>{const current=settings.sites??data.sites.map(s=>s.site??-1);setSettings({...settings,sites:e.target.checked?[...current,site.site??-1]:current.filter(s=>s!==(site.site??-1))});}} />{localize(siteName(site.site))}</label>))}</div>)}
        {localize(embedded && !overview && data && <div className={s.fourCharts}>
          <SiteSequencePlot data={viewData ?? data} settings={settings} onPick={pick} />
          <div className={s.mainChart}>{localize(module === 'mmsa'
            ? <section className={s.plot}><h3>{t("分 site 小提琴 · 均值线")}</h3><div className={s.canvas}><BaseEChart ariaLabel={t("分 site 小提琴")} theme='light' option={sampleShape(data,settings,'violin')} /></div></section>
            : <ReleasePlot sharedLegend title={localize(module === 'cpk' ? t("直方图 · 核密度") : chartTitle(kind, pair.primaryStd))} data={viewData ?? data} kind={kind} settings={primarySettings} option={option} onPick={pick} onFullscreen={() => setExpanded(expanded ? null : 'primary')} expanded={expanded === 'primary'} ref={mainPlot} />)}</div>
          <div className={`${s.smallCharts} ${module !== 'cpk' ? s.twoCharts : ''}`}>
            {localize(module === 'cpk' ? <>
              <section className={s.plot}><h3>Q-Q{localize(data.sampled ? t(" · 抽样 n=") + data.displayed_points : '')}</h3><div className={s.canvas}><BaseEChart ariaLabel='Q-Q' theme='light' option={sampleShape(data,settings,'qq')} /></div></section>
              <ReleasePlot compact sharedLegend title={t("分 site 箱线图")} data={data} kind='box' settings={settings} option={chartOption(data,'box',settings)} onPick={pick} onFullscreen={() => {}} />
              {localize(data.wafer.wafers.length ? <ReleasePlot compact sharedLegend title='Wafer' data={data} kind='wafer' settings={settings} option={chartOption(data,'wafer',settings)} onPick={pick} onFullscreen={() => {}} /> : <SiteStatistics data={data} settings={settings} />)}
            </> : <>
              <section className={s.plot}><h3>{localize(module === 'cgm' ? t("去均值波动 · Site Diff") : t("直方图"))}</h3><div className={s.canvas}><BaseEChart ariaLabel={t("辅助波动")} theme='light' option={module === 'cgm' ? sampleShape(data,settings,'demean') : compactChartOption(chartOption(data,'histogram',settings))} /></div></section>
              <SiteStatistics data={data} settings={settings} />
            </>)}
          </div>
          {localize(data.sampled && <small>{t("样本形状图：抽样 n=")}{localize(data.displayed_points)}{t("；Mean、Std、Cp、Cpk 使用全量统计。")}</small>)}
        </div>)}
        {localize((!embedded || overview) && <div className={`${s.charts} ${embedded && overview ? s.familyMode : overview && matrixWide ? s.wideMatrix : ''}`} aria-label={t("主辅图展示")}>
          <ChartSlot role='主图' label={hasSelection ? '当前测项 · 同模块数据' : '待选择测项'}>
            {localize(!hasSelection ? <div className={s.plotPlaceholder}><strong>{t("请选择测项组中的一个测项")}</strong><p>{t("点击右侧矩阵任一行，在此查看单项图形。")}</p><small>{t("不以代表项代替全组确认")}</small></div>
              : error ? <div className={s.plotPlaceholder} role='alert'><p>{localize(error)}</p><button onClick={() => setRetry(retry + 1)}>{t("重试图表")}</button></div>
              : !data ? <div className={s.plotPlaceholder} role='status'>{t("正在读取本模块图表…")}</div>
              : primaryReason ? <div className={s.plotPlaceholder}>{localize(primaryReason)}</div>
              : <ReleasePlot key={`${row.key}-${module}-${kind}-${reset}`} ref={mainPlot} title={localize(chartTitle(kind, pair.primaryStd))} data={viewData ?? data} kind={kind} settings={primarySettings} option={option} onPick={pick} expanded={expanded === 'primary'} onFullscreen={() => setExpanded(expanded === 'primary' ? null : 'primary')} />)}
          </ChartSlot>
          <ChartSlot role='辅图' label={overview ? '同组测项的各 Site Cg · CGM 来源' : '辅助对照 · 同模块数据'}>
            {localize(overview ? matrixError ? <div className={s.plotPlaceholder} role='alert'>{localize(matrixError)}<button onClick={() => setRetry(retry + 1)}>{t("重试矩阵")}</button></div>
              : !matrix ? <div className={s.plotPlaceholder} role='status'>{t("正在读取测项组 Cg 表…")}</div>
              : <><FamilyHeatmap data={matrix} currentKey={groupChosen ? row.key : ''} selectedSites={groupChosen && module === 'cgm' ? settings.selected.sites : undefined} onRow={chooseMatrix} onSite={site => { if (groupChosen) chooseMatrix(row.key, site); else setNotice('请先选择矩阵中的具体测项，再选择 site。'); }} onPage={setMatrixOffset} />
                <div className={s.tools}><label>{t("矩阵排序")}<select aria-label={t("矩阵排序")} value={matrixSort} onChange={event => { setMatrixSort(event.target.value as 'report' | 'cg'); setMatrixOffset(0); }}><option value='report'>{t("报告顺序")}</option><option value='cg'>{t("最低 Cg 升序")}</option></select></label><button disabled={exporting} onClick={() => void exportMatrix()}>{t("导出本组完整矩阵 CSV")}</button></div></>
              : !data || error ? <div className={s.plotPlaceholder}>{t("辅图区域 · 与主图使用同一模块样本")}</div>
              : secondaryReason ? <div className={s.plotPlaceholder}>{localize(secondaryReason)}</div>
              : <ReleasePlot key={`${row.key}-${module}-${secondaryKind}-${reset}`} ref={secondaryPlot} title={localize(chartTitle(secondaryKind, pair.secondaryStd))} data={viewData ?? data} kind={secondaryKind} settings={secondarySettings} option={secondaryOption} onPick={pick} expanded={expanded === 'secondary'} onFullscreen={() => setExpanded(expanded === 'secondary' ? null : 'secondary')} />)}
          </ChartSlot>
        </div>)
        }
        {hasSelection && data && <details className={s.displayControls} open={embedded ? undefined : true}><summary>{t("图表设置与对照")}</summary><section aria-label={t("图表显示与联动")}>
          <div className={s.linkage}><strong>{t("联动：")}{localize(overview ? t("CGM 单项与矩阵 site") : t("同模块样本"))}</strong><span>{t("排序、隐藏、框选不改判 · 跨模块切换清除选择")}</span><span>{t("选中 ")}{localize(samplePage?.total ?? '…')} {t(" 条")}</span></div>
                <div className={s.tools}>        <label>{t("排序")}<select aria-label={t("图表 site 排序")} value={settings.sort} onChange={event => setSettings({ ...settings, sort: event.target.value as ChartSettings['sort'] })}>{localize(['site', 'mean', 'std', 'cg'].map(sort => <option key={sort} value={sort}>{localize(({ site: t("Site 编号"), mean: t("均值升序"), std: t("Std 升序"), cg: t("Cg 升序") } as Record<string, string>)[sort])}</option>))}</select></label>
        <button onClick={() => { setSettings(initialSettings()); setOffset(0); setReset(reset + 1); }}>{t("复位图表")}</button><button onClick={() => pick({ selection: {} })}>{t("清除样本选择")}</button></div>
      {localize(!embedded && <div className={s.sites}>{localize(data.sites.map(site => <label key={site.site ?? 'unknown'}><input type='checkbox' aria-label={localize(t("显示 {0}", siteName(site.site)))} checked={settings.sites === null || settings.sites.includes(site.site ?? -1)} onChange={event => {
        const current = settings.sites ?? data.sites.map(value => value.site ?? -1); setSettings({ ...settings, sites: event.target.checked ? [...current, site.site ?? -1] : current.filter(value => value !== (site.site ?? -1)) }); }} />{localize(siteName(site.site))}</label>))}</div>)}
      <div className={s.tools}>{localize(hasChart('histogram') && <><label>{t("分箱数")}<input type='number' aria-label={t("直方图分箱数")} min={1} max={200} value={bins} onChange={event => { const n = Number(event.target.value); if (n >= 1 && n <= 200) { setBins(n); setWidth(''); } }} /></label>
        <label>{t("分箱宽度")}<input type='number' aria-label={t("直方图分箱宽度")} min={0} value={width} placeholder={t("自动")} onChange={event => setWidth(event.target.value)} /></label>
        <button aria-pressed={settings.fullSpec} onClick={() => setSettings({ ...settings, fullSpec: !settings.fullSpec })}>{localize(settings.fullSpec ? t("完整规格") : t("数据细节"))}</button></>)}
        {localize(kind === 'cg' && <button disabled={!hasReportCg && pair.primaryStd} title={localize(!hasReportCg ? t("当前模块未提供报告 Cg，可显示 Std") : '')} aria-pressed={pair.primaryStd} onClick={() => pair.toggleStd('primary')}>{localize(pair.primaryStd ? t("切换 Cg") : t("切换 Std"))}</button>)}
        {localize(!overview && secondaryKind === 'cg' && <button disabled={!hasReportCg && pair.secondaryStd} title={localize(!hasReportCg ? t("当前模块未提供报告 Cg，可显示 Std") : '')} aria-pressed={pair.secondaryStd} onClick={() => pair.toggleStd('secondary')}>{localize(pair.secondaryStd ? t("辅图切换 Cg") : t("辅图切换 Std"))}</button>)}
        {localize(hasChart('sequence') && <label><input type='checkbox' checked={settings.connect} onChange={event => setSettings({ ...settings, connect: event.target.checked })} />{t("连接同 site 点（不代表同颗芯片）")}</label>)}
        {localize(hasChart('wafer') && <><select aria-label={t("选择晶圆")} value={settings.wafer || data.wafer.wafers[0]?.key || ''} onChange={event => { setSettings({ ...settings, wafer: event.target.value, selected: {} }); setOffset(0); }}>{localize(data.wafer.wafers.map(wafer => <option key={wafer.key} value={wafer.key}>{localize(wafer.lot)} / {localize(wafer.wafer)}</option>))}</select>
          <button onClick={() => setSettings({ ...settings, outOfSpec: !settings.outOfSpec })}>{localize(settings.outOfSpec ? t("数值着色") : t("超规格标记"))}</button><span>{localize(data.wafer.display_note)}</span></>)}
      </div>

          {localize(module === 'cgm' && <div className={s.tools}><label>{t("正常 site 对照")}<select aria-label={t("选择对照 site")} value={comparisonSite} onChange={event => setComparisonSite(event.target.value)}><option value=''>{t("请选择 Cg 达标 site")}</option>{localize(data.sites.filter(site => site.site !== null && site.cg !== null && cgNumber(site.cg)! >= data.thresholds.cg_pass).map(site => <option key={site.site} value={String(site.site)}>{localize(siteName(site.site))} · Cg {localize(format(site.cg))}</option>))}</select></label>
            <button disabled={!comparisonSite} onClick={() => setSettings({ ...settings, sites: [...new Set([...data.sites.filter(site => site.cg !== null && cgNumber(site.cg)! < data.thresholds.cg_pass).map(site => site.site ?? -1), Number(comparisonSite)])] })}>{t("仅看异常与对照")}</button><button onClick={() => setSettings({ ...settings, sites: null })}>{t("显示全部 site")}</button></div>)}
                {localize(data.quality.warnings.map(w => <p key={w} className={s.notice}>{localize(w)}</p>))}
      {localize(!data.statistics.spec_valid && <p className={s.notice}>{t("有效规格顺序无效，仅显示分布，不补造参考限。")}</p>)}

          {localize(data.wafer.empty_reason && <p className={s.caption}>{t("Wafer Map：")}{localize(data.wafer.empty_reason)}</p>)}
                <div className={s.tools}><button disabled={exporting || !!primaryReason || (!overview && !!secondaryReason)} onClick={() => void exportImages(false)}>{localize(overview ? t("导出当前单项图组") : t("导出当前视图图组"))}</button><button disabled={exporting || !!primaryReason || (!overview && !!secondaryReason)} onClick={() => void exportImages(true)}>{localize(overview ? t("导出完整单项图组") : t("导出完整范围图组"))}</button><button onClick={() => downloadBlob(new Blob([JSON.stringify(localizeReport({ context: data.context, statistics: data.statistics, quality: data.quality, excluded: data.excluded_counts, chart: kind, chart_pair: { primary: kind, secondary: overview ? 'family_matrix_csv' : secondaryKind, primary_std: pair.primaryStd, secondary_std: !overview && pair.secondaryStd }, settings, diagnosis: notes.remarks?.[module + ':' + row.key] ?? row[module]?.diagnosis ?? null, observation: notes.notes[module + ':' + row.key] ?? null }), null, 2)], { type: 'application/json' }), 'release-chart-context.json')}>{t("导出图表上下文 JSON")}</button><button onClick={() => void exportNotes()}>{localize(demo ? t("导出示例记录") : t("导出观察 Excel"))}</button></div>

        </section></details>}
        {localize(hasSelection ? <ObservationPanel base={base} row={row} module={module} group={group} rows={rows} notes={notes} onNotes={setNotes} onDirty={reportDirty} compact={embedded} disabled={stale} onNext={() => move(1, true)} demo={demo} />
          : <p className={s.notice}>{t("选中具体测项后，可勾选观察、编辑 Remark，或预览批量适用范围。")}</p>)}
        {hasSelection && data && <div className={s.detailSections}>      <details><summary>{t("样本明细与选择（选中 ")}{localize(samplePage?.total ?? '…')} {t(" 条，不改判）")}</summary>
        {localize(sampleError && <p className={s.error}>{localize(sampleError)}<button onClick={() => setRetry(retry + 1)}>{t("重试明细")}</button></p>)}
        <div className={s.tableWrap}><table className={s.table}><thead><tr><th>{t("选择")}</th><th>site</th><th>{t("值")}</th><th>{t("记录")}</th><th>{t("原始次序")}</th><th>{t("晶圆 / X / Y")}</th></tr></thead><tbody>{localize(samplePage?.points.map(point => <tr key={point.id}>
          <td><button onClick={() => pick({ selection: { ids: [point.id] } })}>{t("选中样本")}</button></td><td>{localize(siteName(point.site))}</td><td>{localize(format(point.value, 6))}</td><td>{localize(point.part_index)}</td><td>{localize(point.record_sequence)}</td><td>{localize(point.wafer ?? t("未记录"))} / {localize(point.x ?? '—')} / {localize(point.y ?? '—')}</td></tr>))}</tbody></table></div>
        <div className={s.tools}><button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>{t("上一页样本")}</button><button disabled={!samplePage || offset + samplePage.limit >= samplePage.total} onClick={() => setOffset(offset + 100)}>{t("下一页样本")}</button></div></details>
      <details><summary>{t("各 site 统计与完整上下文")}</summary><div className={s.tableWrap}><table className={s.table}><thead><tr><th>site</th><th>n</th><th>Mean</th><th>Std</th><th>Cg</th><th>{t("状态")}</th></tr></thead><tbody>{localize(data.sites.map(site => <tr key={site.site ?? 'unknown'}><td><button onClick={() => pick({ site: site.site })}>{localize(siteName(site.site))}</button></td><td>{localize(site.n)}</td><td>{localize(format(site.mean, 5))}</td><td>{localize(format(site.std, 5))}</td><td>{localize(format(site.cg))}</td><td>{localize(site.cg_status)} {localize(site.reason)}</td></tr>))}</tbody></table></div>
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{JSON.stringify(localizeReport({ context: data.context, excluded: data.excluded_counts }), null, 2)}</pre></details>
</div>}
      </main>
    </div>
  </section>;
}
