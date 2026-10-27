import { t, localize, useLanguage } from "../../../i18n";
import { useMemo } from 'react';
import { initialSelections, pooledDataset, type Dataset, type Selection } from '../model';
import { SelectionPanel } from '../SelectionPanel';
import type { OutlierSettings as Settings } from './model';
import s from './outliers.module.css';

export function OutlierSettings({ files, value, disabled, onChange }: {
  files: Dataset[]; value: Settings; disabled: boolean; onChange: (next: Settings) => void;
}) {
  useLanguage();
  const chosen = files.filter(file => file.id !== 'demo' && value.sourceIds.includes(file.id));
  const pool = useMemo(() => pooledDataset(chosen), [files, value.sourceIds]);
  const selection = useMemo(() => {
    if (!pool) return null;
    const selected = chosen.map(file => value.selections[file.id] ?? initialSelections(file).cpk);
    const sites = [...new Set(selected.flatMap(item => item.sites))].sort((a,b) => a-b);
    const tests = [...new Set(selected.flatMap(item => item.tests))];
    const parts: number[] = [], excludedParts: number[] = [];
    const all = selected.every(item => item.parts === null);
    for (const part of pool.parts) {
      const source = value.selections[part.sourceId ?? pool.id];
      const id = part.sourcePartId ?? part.id;
      if (source?.parts === null ? !source.excludedParts?.includes(id) : source?.parts.includes(id)) parts.push(part.id);
      else excludedParts.push(part.id);
    }
    return { ...initialSelections(pool).cpk, sites, tests, parts: all ? null : parts, excludedParts: all ? excludedParts : [] };
  }, [pool, value.selections]);
  function change(next: Selection) {
    if (!pool) return;
    const selections = { ...value.selections };
    for (const file of chosen) {
      const local = pool.parts.filter(part => (part.sourceId ?? pool.id) === file.id);
      const remap = (ids: number[]) => { const selected = new Set(ids); return local.filter(part => selected.has(part.id)).map(part => part.sourcePartId ?? part.id); };
      selections[file.id] = { ...initialSelections(file).cpk,
        sites: file.sites.filter(site => next.sites.includes(site)),
        tests: file.tests.map(test => test.column).filter(col => next.tests.includes(col)),
        parts: next.parts === null ? null : remap(next.parts), excludedParts: remap(next.excludedParts ?? []) };
    }
    onChange({ ...value, selections });
  }
  return <fieldset className={s.settings} disabled={disabled} aria-label={t("Outlier 设置")}>
    <p>{t("在上方文件名旁勾选 Outlier。各文件分别计算参考样本统计和筛选范围，汇总展示异常候选记录。")}</p>
    {localize(pool && selection ? <SelectionPanel key={pool.id} dataset={pool} module="outlier" selection={selection} disabled={disabled} onChange={change}/>
      : <p>{t("勾选文件后，可以选择 site、测项和测试记录。")}</p>)}
    <label className={s.parameter}>{t("偏离阈值")}<input type="number" min="0" step="0.5" aria-label={t("Outlier 偏离阈值")} value={value.sigma}
      onChange={e => onChange({ ...value, sigma: e.target.value })} /><small>{t("与同组中位数的距离超过所设倍数的稳健标准差时，列为异常候选记录")}</small></label>
    <label className={s.parameter}>{t("每组最少有效记录数")}<input type="number" min="4" step="1" aria-label={t("Outlier 每组最少有效记录数")} value={value.minimum}
      onChange={e => onChange({ ...value, minimum: e.target.value })} /></label>
    <p>{t("同测项、条件、规格、lot、wafer、head 和 site 分组；使用有效 bin1、规格内记录。异常候选记录需人工确认。")}</p>
  </fieldset>;
}
