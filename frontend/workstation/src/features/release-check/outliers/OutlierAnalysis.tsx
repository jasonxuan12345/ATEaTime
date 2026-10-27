import { t, localize, useLanguage } from "../../../i18n";
import { requestReportDownload } from '../reportExport';
import { useEffect, useMemo, useRef, useState } from 'react';
import { BaseEChart, type BaseEChartHandle } from '../../../components/charts/BaseEChart';
import { type Row } from '../model';
import { downloadBlob, exportChartGroup } from '../charts/export';
import { CandidateTable } from './CandidateTable';
import { BaselineTable, ChipEvidence } from './OutlierEvidence';
import { ReferenceChart } from './ReferenceChart';
import { allBaselines, allCandidates, type Candidate, type OutlierCell, type OutlierView, type SourceIssue, type SourceReport } from './model';
import { hitsOption, matrixOption, waferKey, waferOption, referenceOption, trendOption } from './chartOptions';
import { chipLabel, scoreText } from './display';
import s from './outliers.module.css';
import cs from '../charts/charts.module.css';
import { siteName } from '../charts/types';

type OutlierRow = Row & { outlier: OutlierCell };
export function OutlierAnalysis({ rows, rowKey, reports, issues, selectedKey, stale, onSelectRow, onSelectChip, onClose, embedded = false }: {
  rows: OutlierRow[]; rowKey: string; reports: SourceReport[]; issues: SourceIssue[]; selectedKey?: string; stale: boolean;
  onSelectRow: (key: string) => void; onSelectChip: (chip: Candidate) => void; onClose: () => void; embedded?: boolean;
}) {
  const language = useLanguage();
  const [search, setSearch] = useState(''), [candidatePage, setCandidatePage] = useState(0), [columnPage, setColumnPage] = useState(0);
  const [wafer, setWafer] = useState(''), [exportError, setExportError] = useState('');
  const [visibleSites, setVisibleSites] = useState<number[] | null>(null), [mainView, setMainView] = useState<'distribution' | 'trend'>('distribution');
  const [views, setViews] = useState<OutlierView[]>([]), [viewLoading, setViewLoading] = useState(false), [viewError, setViewError] = useState('');
  const cache = useRef(new Map<string, OutlierView>());
  const matrixRef = useRef<BaseEChartHandle>(null), waferRef = useRef<BaseEChartHandle>(null), barRef = useRef<BaseEChartHandle>(null), referenceRef = useRef<HTMLDivElement>(null);
  const allSiteIds = [...new Set(reports.flatMap(r => r.report.baselines.map(b => b.site ?? -1)))].sort((a, b) => a - b);
  const baselines = useMemo(() => allBaselines(reports).filter(b => visibleSites === null || visibleSites.includes(b.site ?? -1)), [reports, visibleSites]);
  const candidates = useMemo(() => allCandidates(reports).filter(c => visibleSites === null || visibleSites.includes(c.site ?? -1)), [reports, visibleSites]);
  const shownRows = rows.filter(r => `${r.name} ${r.number}`.toLowerCase().includes(search.toLowerCase()));
  const row = rows.find(r => r.key === rowKey) ?? rows[0];
  const scope = (row?.outlier.candidates ?? []).filter(c => visibleSites === null || visibleSites.includes(c.site ?? -1));
  const rowBaselines = (row?.outlier.baselines ?? []).filter(b => visibleSites === null || visibleSites.includes(b.site ?? -1));
  const chip = scope.find(c => c.key === selectedKey) ?? scope[0] ?? null;
  const activeKey = chip?.key;
  const selectedHit = chip?.hits.find(h => rowBaselines.some(b => b.dataset_id === chip.dataset_id && b.baseline_id === h.baseline_id));
  const threshold = reports.find(r => r.dataset_id === chip?.dataset_id)?.report.algorithm.sigma_multiplier ?? reports[0]?.report.algorithm.sigma_multiplier ?? 6;
  const hitChart = useMemo(() => hitsOption(chip, threshold, selectedHit?.group_id), [chip, threshold, selectedHit?.group_id, language]);
  const groups = [...new Map(baselines.map(b => [`${b.dataset_id}:${b.group_id}`, b])).values()];
  const maxPage = Math.max(0, Math.ceil(candidates.length / 16) - 1), page = Math.min(candidatePage, maxPage);
  const maxColumns = Math.max(0, Math.ceil(groups.length / 8) - 1), columns = Math.min(columnPage, maxColumns);
  const groupIds = new Set(groups.slice(columns * 8, (columns + 1) * 8).map(b => `${b.dataset_id}:${b.group_id}`));
  const matrixBaselines = baselines.filter(b => groupIds.has(`${b.dataset_id}:${b.group_id}`));
  const matrix = matrixOption(candidates.slice(page * 16, (page + 1) * 16), matrixBaselines, activeKey);
  useEffect(() => {
    if (!chip) return;
    if (chip.key !== selectedKey) onSelectChip(chip);
    const index = candidates.findIndex(c => c.key === chip.key);
    if (index >= 0) setCandidatePage(Math.floor(index / 16));
    setWafer(waferKey(chip));
  }, [activeKey, selectedKey, onSelectChip]);
  useEffect(() => {
    const index = groups.findIndex(group => rowBaselines.some(b => b.dataset_id === group.dataset_id && b.group_id === group.group_id));
    if (index >= 0) setColumnPage(Math.floor(index / 8));
  }, [row?.key]);
  const requests = [...new Map(rowBaselines.map(b => {
    const source = reports.find(source => source.dataset_id === b.dataset_id);
    return [`${b.dataset_id}:${source?.report_id}:${b.group_id}`, { dataset_id: b.dataset_id, report_id: source?.report_id, group_id: b.group_id }];
  })).values()].filter(source => source.report_id);
  const requestKey = JSON.stringify(requests);
  useEffect(() => {
    const sources = JSON.parse(requestKey) as { dataset_id: string; report_id: string; group_id: string }[];
    const controller = new AbortController(); let active = true;
    setViews([]); setViewError(''); setViewLoading(!!sources.length);
    Promise.allSettled(sources.map(async source => {
      const key = `${source.dataset_id}:${source.report_id}:${source.group_id}`;
      if (cache.current.has(key)) return cache.current.get(key)!;
      const response = await fetch(`/api/release-check/datasets/${encodeURIComponent(source.dataset_id)}/outliers/${encodeURIComponent(source.report_id)}/view?group_id=${encodeURIComponent(source.group_id)}`, { signal: controller.signal });
      if (!response.ok) throw new Error('完整测试坐标或趋势数据读取失败，请重新计算。');
      const value = await response.json() as OutlierView;
      if (!Array.isArray(value.dies) || !Array.isArray(value.points)) throw new Error('测试坐标或趋势数据格式不完整，请重新计算。');
      cache.current.set(key, value);
      while (cache.current.size > 8) cache.current.delete(cache.current.keys().next().value!);
      return value;
    })).then(results => {
      if (!active) return;
      setViews(results.flatMap(result => result.status === 'fulfilled' ? [result.value] : []));
      setViewError(results.some(result => result.status === 'rejected') ? '部分完整测试坐标或趋势数据读取失败，请重新计算。' : '');
      setViewLoading(false);
    });
    return () => { active = false; controller.abort(); };
  }, [requestKey]);
  const located = scope.filter(c => c.x !== null && c.y !== null && c.wafer !== null);
  const wafers = [...new Map(located.map(c => [waferKey(c), c])).entries()];
  const activeWafer = wafers.some(([key]) => key === wafer) ? wafer : chip && located.some(c => waferKey(c) === waferKey(chip)) ? waferKey(chip) : wafers[0]?.[0];
  const waferChips = located.filter(c => waferKey(c) === activeWafer);
  const dies = views.flatMap(view => view.dies.filter(die => waferKey({ dataset_id: view.dataset_id, ...die }) === activeWafer));
  const waferChart = waferOption(waferChips, activeKey, dies);
  const visibleViews = views.map(view => ({ ...view, points: view.points.filter(p => visibleSites === null || visibleSites.includes(p.site ?? -1)) }));
  const trend = trendOption(visibleViews, scope, activeKey);
  function selectFromChart(event: unknown) {
    const data = (event as { data?: { candidateKey?: string; groupId?: string } }).data;
    const next = candidates.find(c => c.key === data?.candidateKey);
    if (!next) return;
    const hitRows = rows.filter(r => r.outlier.candidates.some(c => c.key === next.key));
    const target = data?.groupId ? hitRows.find(r => r.outlier.baselines.some(b => b.dataset_id === next.dataset_id && b.group_id === data.groupId)) : undefined;
    if (target) onSelectRow(target.key);
    else if (!hitRows.some(r => r.key === rowKey) && hitRows[0]) onSelectRow(hitRows[0].key);
    onSelectChip(next);
  }
  async function exportRemarks(source: SourceReport) {
    try {
      await requestReportDownload(`/api/release-check/datasets/${source.dataset_id}/outliers/${source.report_id}.xlsx`);
    } catch (error) { setExportError((error as Error).message); }
  }
  useEffect(() => {
    const handler = async (event: Event) => {
      if (stale) return;
      const action = (event as CustomEvent<string>).detail;
      if (action === 'context') downloadBlob(new Blob([JSON.stringify({ row, chip, baselines: rowBaselines, issues, views: views.map(v => ({ dataset_id: v.dataset_id, total_points: v.total_points, sampled: v.sampled })) }, null, 2)], { type: 'application/json' }), 'outlier-context.json');
      if (action !== 'images' && action !== 'full') return;
      try {
        const full = action === 'full';
        const svg = referenceRef.current?.querySelector('svg');
        const snapshot = svg?.cloneNode(true) as SVGSVGElement | undefined;
        if (snapshot) { snapshot.setAttribute('width', String(svg!.viewBox.baseVal.width)); snapshot.setAttribute('height', String(svg!.viewBox.baseVal.height)); }
        const svgImage = snapshot && mainView === 'distribution' ? 'data:image/svg+xml;charset=utf-8,' + encodeURIComponent(new XMLSerializer().serializeToString(snapshot)) : null;
        await exportChartGroup('Outlier · ' + (row?.name ?? ''), ['异常候选记录，不是缺陷。', chip ? '当前芯片 ' + chipLabel(chip) : '没有异常候选记录', '超出图示范围的点显示在边缘，实际偏离程度保留在数据中。'],
          [mainView === 'trend' ? trend : referenceOption(rowBaselines, scope, activeKey), hitChart,
            full ? matrixOption(candidates, baselines, activeKey) : matrix, waferChart],
          full ? undefined : [svgImage, barRef.current?.getDataURL({ backgroundColor: '#fff' }) ?? null, matrixRef.current?.getDataURL({ backgroundColor: '#fff' }) ?? null, waferRef.current?.getDataURL({ backgroundColor: '#fff' }) ?? null], 'outlier-charts.png');
      } catch (error) { setExportError((error as Error).message); }
    };
    window.addEventListener('release-export', handler); return () => window.removeEventListener('release-export', handler);
  });
  const board = <div className={s.visualBoard}>
    {localize(stale && <p className={cs.notice}>{t("选样或参数已更改，当前显示上次结果，请重新计算。")}</p>)}
    <div className={s.statusBar} role='status' aria-label={t("当前 Outlier 选中状态")}>
      <span>{t("当前测项：")}<strong>{localize(row?.number ?? '—')} {row?.name ?? t("没有测项结果")}</strong></span>
      <span>{t("当前选中芯片：")}<strong>{localize(chip ? `Wafer ${chip.wafer ?? '—'} (X:${chip.x ?? '—'}, Y:${chip.y ?? '—'}) S${chip.site ?? '—'}` : t("暂无异常候选记录"))}</strong></span>
      <span>{t("偏离程度（稳健σ）：")}<strong className={s.selectedScore}>{localize(selectedHit ? scoreText(selectedHit.score) : '—')}</strong></span>
    </div>
    <div className={s.boardTools}><span>{t("阈值 ")}{localize(scoreText(threshold))} {t(" · 本测项 ")}{localize(scope.length)} {t(" 条异常候选记录")}</span>
      {localize(allSiteIds.map(site => <label key={site}><input type='checkbox' aria-label={localize(t("显示 ") + siteName(site < 0 ? null : site))} checked={visibleSites === null || visibleSites.includes(site)} onChange={event => {
        const current = visibleSites ?? allSiteIds; setVisibleSites(event.target.checked ? [...current, site] : current.filter(value => value !== site));
      }} />{localize(siteName(site < 0 ? null : site))}</label>))}
      <details><summary>{t("来源与计算信息")}</summary>{reports.map(source => <p key={source.dataset_id}>{source.filename} {t(" · 已评估 ")}{localize(source.report.summary.evaluated_baseline_count)} {t(" 组 · 未评估 ")}{localize(source.report.summary.unevaluated_baseline_count)} {t(" 组")}</p>)}{issues.map(issue => <p key={issue.dataset_id}>{issue.filename}{t("：")}{localize(issue.error)}</p>)}</details>
    </div>
    <section className={s.visualCard} ref={referenceRef}>
      <h3>{t("1. 各 Site 分布与异常偏离度（正常区间 vs 异常候选点）")}</h3>
      <nav className={s.viewTabs} aria-label={t("Outlier 主图视图")}>
        <button aria-pressed={mainView === 'distribution'} onClick={() => setMainView('distribution')}>{t("分布区间视图")}</button>
        <button aria-pressed={mainView === 'trend'} onClick={() => setMainView('trend')}>{t("测试序列趋势图")}</button>
      </nav>
      {localize(mainView === 'distribution' ? <ReferenceChart baselines={rowBaselines} candidates={scope} selectedKey={activeKey} onSelect={onSelectChip} />
        : views.length ? <><div className={s.trendCanvas}><BaseEChart ariaLabel={t("Outlier 测试序列趋势图")} theme='light' option={trend} onClick={selectFromChart} /></div>
          <p className={s.caption}>{t("按 site 分色，X 为 Part Index；不同文件各自从自己的记录编号开始。")}{localize(views.some(view => view.sampled) && t("正常分布点为抽样，异常候选点和选中芯片单独保留。"))}</p></>
        : <p className={s.empty}>{localize(viewLoading ? t("正在读取测试序列…") : t("没有可用的测试序列，请重新计算 Outlier。"))}</p>)}
    </section>
    <div className={s.bottomCharts}>
      <section className={s.visualCard}><h3>{t("2. 当前选中芯片并发异常的测项（共 ")}{localize(chip?.hit_count ?? 0)} {t(" 项）")}</h3>
        {localize(chip ? <div className={s.barScroll}><div className={s.barCanvas} style={{ height: Math.max(290, Math.min(chip.hits.length, 40) * 25 + 65) }}><BaseEChart ref={barRef} ariaLabel={t("当前记录各异常测项的偏离程度（稳健σ）")} theme='light' option={hitChart} onClick={selectFromChart} /></div></div> : <p className={s.empty}>{t("本测项没有异常候选记录。")}</p>)}
        {localize(chip && chip.hits.length > 40 && <p className={s.caption}>{t("显示前 40 个异常测项，完整列表见下方异常候选记录详情。")}</p>)}
      </section>
      <section className={s.visualCard}><h3>{t("3. 异常候选记录 × 测项偏离程度")}</h3>
        <div className={s.matrixCanvas} style={{height: Math.max(320, Math.min(16, candidates.length - page * 16) * 34 + 80)}}><BaseEChart ref={matrixRef} ariaLabel={t("异常候选记录与测项异常分布表")} theme='light' option={matrix} onClick={selectFromChart} /></div>
        <div className={s.colorBins}><span style={{ background: '#F1F5F9' }}>{t("未检出异常 / <6x")}</span><span style={{ background: '#FDE047' }}>6–10x</span><span style={{ background: '#FB923C' }}>10–50x</span><span style={{ background: '#DC2626', color: '#fff' }}>&gt;50x</span></div>
        <p className={s.caption}>{t("灰格仅表示未检出异常记录，不等于已评估通过。完整文件名见悬停。")}</p>
        <div className={s.matrixPager}><button disabled={!page} onClick={() => setCandidatePage(page - 1)}>{t("上一页记录")}</button><span>{localize(page + 1)}/{localize(maxPage + 1)}</span><button disabled={page >= maxPage} onClick={() => setCandidatePage(page + 1)}>{t("下一页记录")}</button>
          <button disabled={!columns} onClick={() => setColumnPage(columns - 1)}>{t("上一页测项")}</button><span>{localize(columns + 1)}/{localize(maxColumns + 1)}</span><button disabled={columns >= maxColumns} onClick={() => setColumnPage(columns + 1)}>{t("下一页测项")}</button></div>
      </section>
      <section className={s.visualCard}><h3>{t("4. Wafer 空间位置分布（橙色：本测项全部异常 / 红色十字：当前选中芯片）")}</h3>
        {wafers.length ? <><select className={s.waferSelect} aria-label='Outlier wafer' value={activeWafer} onChange={event => { setWafer(event.target.value); const next = located.find(candidate => waferKey(candidate) === event.target.value); if (next) onSelectChip(next); }}>{wafers.map(([key, c]) => <option key={key} value={key}>{c.filename} / {localize(c.lot ?? '—')} / {localize(c.wafer)}</option>)}</select>
          <div className={s.waferCanvas}><BaseEChart ref={waferRef} ariaLabel={t("Wafer 异常候选记录坐标位置图")} theme='light' option={waferChart} onClick={selectFromChart} /></div>
          <p className={s.caption}>{localize(viewLoading ? t("正在读取全部测试 Die…") : dies.length ? t("浅灰底图：{0} 个已测试位置。", dies.length) : t("未读取到全部测试坐标，仅显示已有异常候选记录。"))}{t("圆形轮廓按已读取坐标拟合，X/Y 等比例。")}</p></>
          : <p className={s.empty}>{t("当前异常候选记录没有完整 Wafer / X / Y 坐标，可在下方列表查看。")}</p>}
      </section>
    </div>
    {localize((viewError || exportError) && <p className={cs.error} role='alert'>{localize(viewError || exportError)}</p>)}
    <details><summary>{t("异常候选记录、芯片定位与各 Site 参考样本统计")}</summary>
      <CandidateTable candidates={scope} selectedKey={activeKey} onSelect={onSelectChip} />
      {localize(chip && <ChipEvidence key={`${chip.key}:${row?.key}`} chip={chip} baselines={baselines} preferredBaselines={rowBaselines} />)}
      <BaselineTable baselines={rowBaselines} />
    </details>
    <details><summary>{t("导出诊断与计算说明")}</summary>{reports.filter(source => source.report_id).map(source => <button key={source.report_id} disabled={stale} onClick={() => void exportRemarks(source)}>{t("导出诊断 Excel · ")}{source.filename}</button>)}</details>
  </div>;
  if (embedded) return <section aria-label={t("Outlier 测项分析")}>{localize(board)}</section>;
  return <section className={cs.workspace} aria-label={t("Outlier 展开分析")}><header className={cs.header}><h2>{t("Outlier · 异常候选记录分析")}</h2><button onClick={onClose}>{t("返回测项检查")}</button></header>
    <div className={cs.body}><nav className={cs.nav} aria-label={t("Outlier 测项导航")}><input type='search' aria-label={t("搜索 Outlier 分析测项")} value={search} onChange={event => setSearch(event.target.value)} />
      {shownRows.map(item => <button key={item.key} aria-current={item.key === row?.key} onClick={() => onSelectRow(item.key)}>{localize(item.number)} · {item.name}<small>{localize(item.outlier.candidates.length)} {t(" 条异常候选记录")}</small></button>)}</nav>
      <div className={cs.content}>{localize(board)}</div></div></section>;
}
