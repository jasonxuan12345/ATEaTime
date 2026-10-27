import { t, localize, useLanguage } from "../../i18n";
import { useEffect, useMemo, useState } from "react";
import { moduleLabels, type Dataset, type Module, type Selection } from "./model";
import { filterParts, selectedSites as sitesInSelection } from './selectionIndex';
import s from "./release-check.module.css";

function useSearch(value: string) {
  const [query, setQuery] = useState(value);
  useEffect(() => { const timer = setTimeout(() => setQuery(value), 150); return () => clearTimeout(timer); }, [value]);
  return query;
}

export function SelectionPanel({ dataset, module, selection, disabled, onChange }: {
  dataset: Dataset; module: Module | 'outlier'; selection: Selection; disabled: boolean; onChange: (next: Selection) => void;
}) {
  useLanguage();
  const [testSearch, setTestSearch] = useState("");
  const [testPage, setTestPage] = useState(0);
  const [partSearch, setPartSearch] = useState("");
  const [partPage, setPartPage] = useState(0);
  const testQuery = useSearch(testSearch), partQuery = useSearch(partSearch);
  const tests = useMemo(() => testQuery ? dataset.tests.filter(t => `${t.name} ${t.number}`.toLowerCase().includes(testQuery.toLowerCase())) : dataset.tests, [dataset.tests, testQuery]);
  const testsPage = Math.min(testPage, Math.max(0, Math.ceil(tests.length / 60) - 1));
  const parts = useMemo(() => filterParts(dataset.parts, partQuery), [dataset.parts, partQuery]);
  const page = Math.min(partPage, Math.max(0, Math.ceil(parts.length / 30) - 1));
  const selectedParts = useMemo(() => new Set(selection.parts ?? []), [selection.parts]);
  const excludedParts = useMemo(() => new Set(selection.excludedParts ?? []), [selection.excludedParts]);
  const selectedTests = useMemo(() => new Set(selection.tests), [selection.tests]);
  const selectedSites = useMemo(() => module === 'mmsa' ? sitesInSelection(dataset, selection) : new Set<number>(),
    [module, dataset, selection.parts, selection.excludedParts, selection.sites]);
  const selectedCount = selection.parts === null ? dataset.parts.length - excludedParts.size : selectedParts.size;
  const title = module === 'outlier' ? 'Outlier' : moduleLabels[module];
  function togglePart(id: number) {
    if (selection.parts === null && dataset.id !== 'demo') {
      const excluded = new Set(excludedParts);
      if (excluded.has(id)) excluded.delete(id); else excluded.add(id);
      onChange({ ...selection, excludedParts: [...excluded] });
      return;
    }
    const next = selection.parts === null ? new Set(dataset.parts.map(p => p.id)) : new Set(selectedParts);
    if (next.has(id)) next.delete(id); else next.add(id);
    onChange({ ...selection, parts: [...next].sort((a, b) => a - b), excludedParts: [] });
  }
  return <fieldset className={s.selectionForm} disabled={disabled} aria-label={localize(t("{0} 数据选择", title))}>
    <div className={s.population}><span className={s.scopeDot} />{localize(module === 'outlier' ? t("bin1 有效、规格内结果") : module === "cpk" ? t("bin1 有效结果") : t("全部有效结果"))}</div>
    <label className={s.fieldTitle}>{t("参与检查的 site ")}<span>{localize(selection.sites.length)} / {localize(dataset.sites.length)}</span></label>
    <div className={s.siteOptions}>{localize(dataset.sites.map(site => <label key={site} className={selection.sites.includes(site) ? s.siteChosen : ""}>
      <input type="checkbox" aria-label={localize(`${title} site ${site}`)} checked={selection.sites.includes(site)} onChange={() => onChange({ ...selection,
        sites: selection.sites.includes(site) ? selection.sites.filter(v => v !== site) : [...selection.sites, site].sort((a, b) => a - b) })} />S{localize(site)}</label>))}</div>
    {localize(module === "mmsa" && selectedSites.size < 2 && <p className={s.help} role="note">{t("当前记录范围只有 ")}{localize(selectedSites.size)} {t(" 个 site。Site Diff 至少需要 2 个，每个 site 至少 2 条有效记录；CPK 和 CGM 仍可计算。")}</p>)}
    <details className={s.selectionDetails}><summary>{t("选择测项 ")}<span>{localize(selection.tests.length)} / {localize(dataset.tests.length)}</span></summary>
      <input type="search" placeholder={t("名称 / 编号")} aria-label={localize(t("{0} 搜索测项", title))} value={testSearch} onChange={e => { setTestSearch(e.target.value); setTestPage(0); }} />
      <div className={s.smallActions}><button type="button" onClick={() => onChange({ ...selection, tests: dataset.tests.map(t => t.column) })}>{t("全部选择")}</button>
        <button type="button" onClick={() => onChange({ ...selection, tests: [] })}>{t("清除")}</button></div>
      <div className={s.checkList}>{tests.slice(testsPage * 60, testsPage * 60 + 60).map(test => <label key={test.column}><input type="checkbox" aria-label={t("{0} 测项 {1}", title, test.name)}
        checked={selectedTests.has(test.column)} onChange={() => onChange({ ...selection, tests: selectedTests.has(test.column)
          ? selection.tests.filter(v => v !== test.column) : [...selection.tests, test.column] })} /><span>{test.name}<small>{localize(test.number)}</small></span></label>)}</div>
      <div className={s.pager}><button type="button" disabled={testsPage === 0} aria-label={localize(t("{0} 上一页测项", title))} onClick={() => setTestPage(testsPage - 1)}>{t("上一页")}</button>
        <span>{localize(testsPage + 1)} / {localize(Math.max(1, Math.ceil(tests.length / 60)))}</span><button type="button" disabled={(testsPage + 1) * 60 >= tests.length} aria-label={localize(t("{0} 下一页测项", title))} onClick={() => setTestPage(testsPage + 1)}>{t("下一页")}</button></div>
    </details>
    <details className={s.selectionDetails}><summary>{t("选择测试记录 ")}<span>{localize(selectedCount)} / {localize(dataset.record_count)}</span></summary>
      <p>{t("每条记录单独保留，不按芯片名称合并。")}</p>
      <input type="search" placeholder={t("编号 / site / bin")} aria-label={localize(t("{0} 搜索记录", title))} value={partSearch} onChange={e => { setPartSearch(e.target.value); setPartPage(0); }} />
      <div className={s.smallActions}><button type="button" onClick={() => onChange({ ...selection, parts: null, excludedParts: [] })}>{t("全部选择")}</button>
        <button type="button" onClick={() => onChange({ ...selection, parts: [], excludedParts: [] })}>{t("清除")}</button></div>
      <div className={s.checkList}>{parts.slice(page * 30, page * 30 + 30).map(part => <label key={part.id}>
        <input type="checkbox" checked={selection.parts === null ? !excludedParts.has(part.id) : selectedParts.has(part.id)} aria-label={localize(t("{0} 记录 {1}", title, part.id))} onChange={() => togglePart(part.id)} />
        <span>{t("记录 ")}{localize(part.id)}<small>site {localize(part.site ?? "—")} · bin {localize(part.bin ?? "—")}{part.filename && t(" · {0} · 原记录 {1}", part.filename, part.sourcePartId)}</small></span></label>)}</div>
      <div className={s.pager}><button type="button" disabled={page === 0} onClick={() => setPartPage(page - 1)}>{t("上一页")}</button>
        <span>{localize(page + 1)} / {localize(Math.max(1, Math.ceil(parts.length / 30)))}</span><button type="button" disabled={(page + 1) * 30 >= parts.length} onClick={() => setPartPage(page + 1)}>{t("下一页")}</button></div>
    </details>
    {localize(module !== 'outlier' && <><label className={s.minimum}>{t("最小样本数")}{localize(module === "cpk" ? "" : " / site")}<input type="number" min="2" step="1" placeholder={t("未设置")} aria-label={localize(t("{0} 最小样本数", title))}
      value={selection.minimum} onChange={e => onChange({ ...selection, minimum: e.target.value })} /></label>
    <p className={s.help}>{t("留空时，样本充分性尚未评估。")}</p></>)}
  </fieldset>;
}
