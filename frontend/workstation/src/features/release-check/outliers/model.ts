import { apiSelection, initialSelections, severity, type Dataset, type Diagnosis, type Row, type Selection } from '../model';

export type Baseline = {
  baseline_id: string; group_id: string; column: string; test_num: number | null; test_name: string;
  record_type: string; result_index: number | null; conditions: Record<string, string>;
  lot: string | null; wafer: string | null; head: number | null; site: number | null;
  unit: string | null; lsl: number | null; usl: number | null; n: number;
  q1: number | null; median: number | null; q3: number | null; robust_sigma: number | null;
  lower_limit: number | null; upper_limit: number | null; status: string; reason: string | null;
  diagnosis?: Diagnosis; candidate_count: number; excluded_counts: Record<string, number>;
};
export type Hit = Pick<Baseline, 'baseline_id' | 'group_id' | 'column' | 'test_num' | 'test_name' |
  'record_type' | 'result_index' | 'unit' | 'median' | 'robust_sigma' | 'lower_limit' | 'upper_limit'> & {
  record_sequence: number; value: number; score: number; direction: 'HIGH' | 'LOW';
};
export type Chip = {
  part_index: number; prr_record_sequence: number | null; part_id: string | null;
  lot: string | null; wafer: string | null; x: number | null; y: number | null;
  head: number | null; site: number | null; hit_count: number; max_score: number; hits: Hit[];
};
export type OutlierReport = {
  detail_level?: 'summary';
  baselines: Baseline[]; chips: Chip[]; warnings: string[]; unexecuted_test_columns: string[];
  algorithm: { sigma_multiplier: number; minimum_samples_per_site: number };
  summary: { selected_record_count: number; candidate_part_count: number; candidate_hit_count: number;
    evaluated_baseline_count: number; unevaluated_baseline_count: number };
};
export type SourceReport = { report_id?: string; dataset_id: string; filename: string; report: OutlierReport };
export type SourceIssue = { dataset_id: string; filename: string; error: string };
export type Candidate = Chip & { key: string; dataset_id: string; filename: string };
export type SourceBaseline = Baseline & { dataset_id: string; filename: string; threshold: number };
export type DiePosition = { lot: string | null; wafer: string | null; x: number; y: number };
export type TrendPoint = { part_index: number; record_sequence: number; value: number; site: number | null;
  head: number | null; lot: string | null; wafer: string | null; x: number | null; y: number | null; baseline_id: string };
export type OutlierView = { dataset_id: string; report_id: string; group_id: string; dies: DiePosition[];
  points: TrendPoint[]; total_points: number; sampled: boolean };
export type OutlierSettings = { sourceIds: string[]; selections: Record<string, Selection>; sigma: string; minimum: string };
export type OutlierCell = { baselines: SourceBaseline[]; candidates: Candidate[]; maxScore: number;
  label: string; review: boolean; filenames: string[] };
export const evaluated = (b: Baseline) => ['NO_OUTLIER_FOUND', 'OUTLIER_CANDIDATES'].includes(b.status);
export const candidateKey = (datasetId: string, part: number) => `${datasetId}:${part}`;
export function initialOutliers(dataset?: Dataset): OutlierSettings {
  return { sourceIds: dataset && dataset.id !== 'demo' ? [dataset.id] : [],
    selections: dataset && dataset.id !== 'demo' ? { [dataset.id]: initialSelections(dataset).cpk } : {}, sigma: '6', minimum: '30' };
}
export function validateOutliers(settings: OutlierSettings, files: Dataset[]) {
  if (!settings.sourceIds.length) return 'Outlier 至少选择一份已导入 STDF。';
  if (!Number.isFinite(Number(settings.sigma)) || Number(settings.sigma) <= 0) return 'Outlier 偏离阈值需要大于 0。';
  if (!Number.isSafeInteger(Number(settings.minimum)) || Number(settings.minimum) < 4) return 'Outlier 每组最少样本需要是至少 4 的整数。';
  let hasSamples = false;
  for (const id of settings.sourceIds) {
    const file = files.find(f => f.id === id), s = settings.selections[id];
    if (!file || !s) return 'Outlier 来源已移除，请重新选择。';
    if (hasOutlierSelection(settings, file)) hasSamples = true;
  }
  return hasSamples ? null : 'Outlier 至少选择一个 site、一个测项和一条记录。';
}
export function hasOutlierSelection(settings: OutlierSettings, file: Dataset) {
  const selection = settings.selections[file.id];
  if (!selection?.sites.length || !selection.tests.length) return false;
  const included = selection.parts === null ? null : new Set(selection.parts), excluded = new Set(selection.excludedParts);
  return file.parts.some(part => selection.sites.includes(part.site ?? -1) &&
    (included === null ? !excluded.has(part.id) : included.has(part.id)));
}
export function outlierRequest(settings: OutlierSettings, file: Dataset) {
  const { minimum_samples: _minimum, ...selection } = apiSelection(settings.selections[file.id], file);
  return { ...selection, sigma_multiplier: Number(settings.sigma), minimum_samples_per_site: Number(settings.minimum) };
}
export function allCandidates(reports: SourceReport[]): Candidate[] {
  return reports.flatMap(source => source.report.chips.map(chip => ({ ...chip,
    dataset_id: source.dataset_id, filename: source.filename, key: candidateKey(source.dataset_id, chip.part_index) })))
    .sort((a, b) => b.max_score - a.max_score || a.key.localeCompare(b.key));
}
export function allBaselines(reports: SourceReport[]): SourceBaseline[] {
  return reports.flatMap(source => source.report.baselines.map(b => ({ ...b, dataset_id: source.dataset_id,
    filename: source.filename, threshold: source.report.algorithm.sigma_multiplier })));
}
export const baselineKey = (b: SourceBaseline) => `${b.dataset_id}:${b.baseline_id}`;
export function describeBaselines(baselines: Baseline[]) {
  if (!baselines.length) return '无匹配结果';
  if (baselines.every(b => b.status === 'NOT_APPLICABLE')) return '不适用';
  if (baselines.every(b => b.status === 'INSUFFICIENT_DATA')) return '样本不足';
  if (baselines.some(b => !evaluated(b))) return baselines.some(evaluated) ? '部分未评估' : '无法计算';
  if (baselines.some(b => b.candidate_count > 0 || b.status === 'OUTLIER_CANDIDATES')) return '发现异常候选记录';
  return '未发现异常候选记录';
}
// Match the same complete identity; never attach a candidate to a merely same-named test.
const identity = (b: Pick<Baseline, 'test_num' | 'test_name' | 'unit' | 'lsl' | 'usl' | 'conditions'>) =>
  JSON.stringify([b.test_num, b.test_name, b.unit, b.lsl, b.usl, Object.entries(b.conditions).sort(([a], [c]) => a.localeCompare(c))]);
export function integrateOutliers(rows: Row[], reports: SourceReport[], issues: SourceIssue[], pending: boolean) {
  const baselines = allBaselines(reports), candidates = allCandidates(reports);
  const byIdentity = new Map<string, SourceBaseline[]>();
  for (const b of baselines) { const key = identity(b), items = byIdentity.get(key) ?? []; items.push(b); byIdentity.set(key, items); }
  const hitsByBaseline = new Map<string, { chip: Candidate; score: number }[]>();
  for (const chip of candidates) for (const hit of chip.hits) {
    const key = `${chip.dataset_id}:${hit.baseline_id}`;
    const items = hitsByBaseline.get(key) ?? [];
    items.push({ chip, score: hit.score }); hitsByBaseline.set(key, items);
  }
  const result: (Row & { outlier: OutlierCell })[] = [];
  const used = new Set<string>();
  // A row can only absorb a full identity if it is unique in the original table.
  const rowIdentity = (r: Row) => identity({ test_num: r.number, test_name: r.name, unit: r.unit, lsl: r.lsl, usl: r.usl, conditions: r.conditions });
  const frequencies = new Map<string, number>();
  rows.forEach(r => frequencies.set(rowIdentity(r), (frequencies.get(rowIdentity(r)) ?? 0) + 1));
  const cell = (bs: SourceBaseline[]): OutlierCell => {
    const matched = bs.flatMap(b => hitsByBaseline.get(baselineKey(b)) ?? []);
    const chips = [...new Map(matched.map(item => [item.chip.key, item.chip])).values()].sort((a, b) => b.max_score - a.max_score);
    return { baselines: bs, candidates: chips, maxScore: matched.reduce((a, b) => Math.max(a, b.score), 0),
      label: issues.length ? '来源计算未完成' : bs.length ? describeBaselines(bs) : pending ? '未计算' : '无匹配结果',
      review: issues.length > 0 || chips.length > 0 || !bs.length || bs.some(b => !evaluated(b) && b.status !== 'NOT_APPLICABLE'),
      filenames: [...new Set(bs.map(b => b.filename))] };
  };
  const push = (r: Row, bs: SourceBaseline[]) => {
    const outlier = cell(bs);
    bs.forEach(b => used.add(baselineKey(b)));
    result.push({ ...r, outlier, status: outlier.review && !['FAIL', 'RISK'].includes(r.status) ? 'REVIEW_REQUIRED' : r.status,
      reason: outlier.review && !['FAIL', 'RISK'].includes(r.status) ? outlier.candidates.length ? '发现 Outlier 异常候选记录，需要复核' : outlier.label : r.reason });
  };
  for (const row of rows) {
    const possible = frequencies.get(rowIdentity(row)) === 1 ? byIdentity.get(rowIdentity(row)) ?? [] : [];
    const groups = new Map<string, Set<string>>();
    for (const b of possible) { const ids = groups.get(b.dataset_id) ?? new Set<string>(); ids.add(b.group_id); groups.set(b.dataset_id, ids); }
    push(row, possible.filter(b => {
      if (groups.get(b.dataset_id)?.size !== 1) return false;
      const cells = [row.cpk, row.cgm, row.mmsa].filter(c => c !== null);
      const local = cells.filter(c => c.dataset_id === b.dataset_id);
      if (local.length) return local.some(c => c.group_id === b.group_id) || row.key === b.group_id;
      // The legacy cross-source row lacks MPR channel identity. Keep MPR separate unless proven by a local group ID.
      if (b.record_type === 'MPR') return false;
      return row.column === b.column || cells.some(c => c.dataset_id && row.column === `${c.dataset_id}:${b.column}`);
    }));
  }
  // Keep unmatched/ambiguous MPR, conditions and specs visible as separate rows.
  const remaining = new Map<string, SourceBaseline[]>();
  for (const b of baselines.filter(b => !used.has(baselineKey(b)))) {
    const key = `${b.dataset_id}:${b.group_id}`;
    const items = remaining.get(key) ?? []; items.push(b); remaining.set(key, items);
  }
  for (const [key, bs] of remaining) {
    const b = bs[0];
    push({ key: `outlier:${key}`, column: b.column, number: b.test_num, name: b.test_name, unit: b.unit,
      lsl: b.lsl, usl: b.usl, conditions: b.conditions, cpk: null, cgm: null, mmsa: null,
      status: 'REVIEW_REQUIRED', reason: '仅 Outlier 有匹配结果，请核对其他检查选样' }, bs);
  }
  return result.sort((a, b) => severity(a.status) - severity(b.status));
}
