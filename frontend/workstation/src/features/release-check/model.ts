import { buildDemoReport, defaultSelection, demoTests, type DemoRow } from "../release-preview/demo";
import { decodeReleaseResponse } from './presentation';

export type Module = "cpk" | "cgm" | "mmsa";
export type Status = "PASS" | "FAIL" | "RISK" | "REVIEW_REQUIRED" | "NOT_CALCULABLE" | "INSUFFICIENT_DATA" | "NOT_EVALUATED" | "NOT_APPLICABLE";
export type Severity = "GREEN" | "YELLOW" | "RED";
export type CgValue = number | 'Infinity';
export const cgNumber = (value: CgValue | null | undefined): number | null => value === 'Infinity' ? Infinity : value ?? null;
export type Selection = { datasetId?: string; datasetIds?: string[]; sites: number[]; tests: string[]; parts: number[] | null; excludedParts?: number[]; minimum: string };
export type Selections = Record<Module, Selection>;
export const moduleLabels: Record<Module, string> = { cpk: "bin1 CPK", cgm: "CGM", mmsa: "Site Diff" };
export const moduleHints: Record<Module, string> = { cpk: "过程能力", cgm: "各 site 重复性", mmsa: "多 site 测量系统分析" };
export type Dataset = { id: string; filename: string; record_count: number; sites: number[];
  tests: { column: string; name: string; number: number | null; unit: string | null }[];
  conditions: Record<string, string>; parts: { id: number; site: number | null; bin: number | null; name: string; sourceId?: string; sourcePartId?: number; filename?: string }[] };
export type Diagnosis = { current_remark: string; auto_remark: string; remark: string; category: string | null;
  knowledge: { status: string; id?: string; historical_remark?: string; source?: { name: string; sha256: string } };
  knowledge_version: string; knowledge_hash: string; review_state: string; related_evidence?: unknown; detail_url?: string };
export type Cell = { status: Status; value: CgValue | null; n: number; reason: string; unit?: string | null;
  sites?: Site[];
  mean?: number | null; std?: number | null; cp?: number | null;
  shift_spec_pct?: number | null; var_ratio?: number | null;
  rule_id: string | null; override: boolean; supported_by_bin1_cpk: number | null; filename?: string; dataset_id?: string; group_id?: string | null; diagnosis?: Diagnosis };
export type S2s = { gate: { trigger: string; cpk_lt: number | null; cg_lt: number | null };
  bands: Record<string, { green_lt: number; red_ge: number }>;
  cg_pass_threshold: number; local_fault_max_sites: number; site_count: number;
  mean_shift: number | null; overall_std: number | null; median_site_std: number | null; max_site_std: number | null;
  min_site_mean: number | null; max_site_mean: number | null;
  shift_sigma: number | null; shift_severity: Severity | null;
  shift_spec_pct: number | null; shift_spec_severity: Severity | null;
  var_ratio: number | null; var_severity: Severity | null;
  cp: number | null; limit_margin_upper: number | null; limit_margin_lower: number | null; close_to: "HIGH" | "LOW" | null;
  cgm_min_cg: CgValue | null; cgm_failed_site_count: number; overall_bin1_cpk: number | null; secondary_rule_id: string | null };
export type Row = { key: string; column: string; name: string; number: number | null; unit: string | null;
  lsl: number | null; usl: number | null; conditions: Record<string, string>;
  cpk: Cell | null; cgm: Cell | null; mmsa: Cell | null;
  status: Status; reason: string };
export type Run = { id: string; dataset_id: string; rows: Row[]; warnings: string[]; rules_hash: string; modules?: Module[] };
export type Site = { site: number | null; n: number; mean?: number | null; std?: number | null;
  cg: CgValue | null; histogram?: number[]; status?: string };
export type ModuleDetail = { sites: Site[]; edges: number[]; n: number; mean: number | null; std: number | null;
  reason: string; rule_id: string | null; excluded_counts: Record<string, number>; s2s?: S2s | null;
  sample_refs: { part_index: number; record_sequence: number; site: number | null }[]; sample_ref_count: number };
export type Detail = { row: Row; details: Partial<Record<Module, ModuleDetail>> };
export const labels: Record<Status, string> = { PASS: "通过", FAIL: "未达标", RISK: "风险", REVIEW_REQUIRED: "需复核",
  NOT_CALCULABLE: "无法计算", INSUFFICIENT_DATA: "数据不足", NOT_EVALUATED: "尚未评估", NOT_APPLICABLE: "不适用" };
export const severityText: Record<Severity, string> = { GREEN: "良好", YELLOW: "警告", RED: "严重" };
export const format = (value: CgValue | null | undefined, digits = 2) => value == null ? "—" : value === 'Infinity' || value === Infinity ? '∞' :
  Math.abs(value) !== 0 && (Math.abs(value) < .001 || Math.abs(value) >= 1e6) ? value.toExponential(2) : value.toFixed(digits);
export const severity = (status: Status) => status === "FAIL" ? 0 : status === "RISK" ? 1 : status === "PASS" ? 3 : status === "NOT_APPLICABLE" ? 4 : 2;
const modules: Module[] = ["cpk", "cgm", "mmsa"];
const band = (value: number | null, green: number, red: number): Severity | null =>
  value === null ? null : value < green ? "GREEN" : value >= red ? "RED" : "YELLOW";
export function validate(selections: Selections) {
  for (const module of modules) {
    const s = selections[module];
    if (s.datasetIds?.length === 0) continue;
    if (!s.sites.length || !s.tests.length || s.parts?.length === 0) return `${moduleLabels[module]} 至少选择一个 site、一个测项和一条记录。`;
    if (s.minimum && (!Number.isSafeInteger(Number(s.minimum)) || Number(s.minimum) < 2)) return `${moduleLabels[module]} 最小样本数需要是至少 2 的整数。`;
  }
  return null;
}
export function initialSelections(dataset: Dataset): Selections {
  const make = (): Selection => ({ datasetId: dataset.id, datasetIds: [dataset.id], sites: [...dataset.sites], tests: dataset.tests.map(t => t.column), parts: null, minimum: "" });
  return { cpk: make(), cgm: make(), mmsa: make() };
}
export function sourceFiles(files: Dataset[], selection: Selection, fallback?: string): Dataset[] {
  const ids = selection.datasetIds ?? [selection.datasetId ?? fallback ?? ''];
  return files.filter(file => ids.includes(file.id));
}
export function pooledDataset(files: Dataset[]): Dataset | undefined {
  if (files.length < 2) return files[0];
  const tests = new Map<string, Dataset['tests'][number]>();
  const sites = new Set<number>(), parts: Dataset['parts'] = [];
  let offset = 0;
  for (const file of files) {
    file.sites.forEach(site => sites.add(site));
    file.tests.forEach(test => { if (!tests.has(test.column)) tests.set(test.column, test); });
    for (const part of file.parts) parts.push({ ...part, id: part.id + offset,
      sourceId: file.id, sourcePartId: part.id, filename: file.filename });
    offset += file.parts.reduce((max, part) => Math.max(max, part.id + 1), 0);
  }
  return { ...files[0], id: files.map(file => file.id).join('+'), filename: files.map(file => file.filename).join(' + '),
    sites: [...sites].sort((a,b) => a-b), tests: [...tests.values()], parts,
    record_count: files.reduce((sum,file) => sum + file.record_count, 0) };
}
export function changeSources(files: Dataset[], selection: Selection, ids: string[]): Selection {
  const previous = pooledDataset(sourceFiles(files, selection));
  const nextFiles = files.filter(file => ids.includes(file.id)), next = pooledDataset(nextFiles);
  const base = { ...selection, datasetId: nextFiles[0]?.id ?? '', datasetIds: nextFiles.map(file => file.id) };
  if (!next) return { ...base, sites: [], tests: [], parts: null, excludedParts: [] };
  if (!previous) return { ...initialSelections(next).cpk, ...base, sites: next.sites, tests: next.tests.map(test => test.column), parts: null, excludedParts: [] };
  const oldSites = new Set(previous.sites), oldTests = new Set(previous.tests.map(test => test.column));
  const partKey = (part: Dataset['parts'][number], pool: Dataset) => `${part.sourceId ?? pool.id}:${part.sourcePartId ?? part.id}`;
  const remap = (values: number[]) => {
    const chosen = new Set(values), keys = new Set(previous.parts.filter(part => chosen.has(part.id)).map(part => partKey(part, previous)));
    return next.parts.filter(part => keys.has(partKey(part, next))).map(part => part.id);
  };
  return { ...base, sites: next.sites.filter(site => selection.sites.includes(site) || !oldSites.has(site)),
    tests: next.tests.map(test => test.column).filter(col => selection.tests.includes(col) || !oldTests.has(col)),
    parts: selection.parts === null ? null : remap(selection.parts), excludedParts: remap(selection.excludedParts ?? []) };
}
export function apiSelection(s: Selection, dataset?: Dataset) {
  let parts = s.parts;
  if (parts === null && s.excludedParts?.length) {
    if (!dataset) throw new Error('无法确定所选记录的来源文件');
    const excluded = new Set(s.excludedParts);
    parts = dataset.parts.filter(p => !excluded.has(p.id)).map(p => p.id);
  }
  return { sites: s.sites, test_columns: s.tests, part_indices: parts, minimum_samples: s.minimum ? Number(s.minimum) : null };
}
export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  headers.set('X-Release-View', 'summary-v1');
  const response = await fetch(`/api/release-check${path}`, { ...init, headers });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(typeof error?.detail === "string" ? error.detail : `操作失败（${response.status}），请检查文件或重试。`);
  }
  return decodeReleaseResponse(await response.json()) as T;
}
export const demoDataset: Dataset = { id: "demo", filename: "DEMO_FT_25C_20261012.stdf", record_count: 256,
  sites: [1, 2, 3, 4], conditions: { part_type: "DEMO", test_code: "FT", test_temperature: "25", job_name: "DEMO_FT", job_revision: "B3" },
  tests: demoTests.map(t => ({ column: t.id, number: Number(t.id), name: t.name, unit: t.unit })),
  parts: Array.from({ length: 256 }, (_, i) => ({ id: i, site: i % 4 + 1, bin: i % 17 === 0 ? 2 : 1, name: `示例记录 ${i}` })) };

function demoCell(row: DemoRow): Cell {
  return { status: row.status, value: row.value, n: row.n, reason: row.reason, rule_id: null,
    mean: row.test.mean, std: row.test.std,
    cp: row.test.high > row.test.low && row.test.std > 0 ? (row.test.high - row.test.low) / (6 * row.test.std) : null,
    override: row.override, supported_by_bin1_cpk: row.supportingCpk };
}
// Illustrative fixtures only; production calls Python/YAML, never this adapter.
const demoReasons: Record<string, string> = {
  GATE_NOT_TRIGGERED: "CPK 与 Cg 均达标，无需 Site Diff 诊断",
  LOCAL_SITE_HARDWARE_FAULT: "少数 Site 的 Cg 未达标，建议检查接触和局部测试硬件",
  TEST_RESOLUTION_OR_METHOD: "多数 Site 的 Cg 未达标，建议检查测试分辨率和测试方法",
  SYSTEMATIC_SITE_SHIFT: "Site 均值差达到所设异常阈值", "SITE_VARIANCE_ANOMALY": "个别 Site 的标准差明显高于其他 Site",
  DISTRIBUTION_OR_LIMIT_REVIEW: "当前指标未检出明显 Site 差异，需查看分布图，检查是否偏宽、多峰或接近规格限值",
  CLOSE_TO_LIMIT_MARGIN: "当前指标未检出明显 Site 差异，均值接近规格限值且 CPK 偏低，建议检查规格限值设置和 trim 目标",
  DISTRIBUTION_WIDE_OR_BIMODAL: "当前指标未检出明显 Site 差异，数据分布偏宽且 CPK 偏低，建议查看分布图并检查 trim 设置",
  S2S_EVIDENCE_INCONCLUSIVE: "Site Diff 指标处于警告区间，需人工复核" };
function demoMmsa(test: (typeof demoTests)[number], selections: Selections): { cell: Cell; s2s: S2s } {
  const sites = selections.mmsa.sites;
  // Deterministic illustrative per-site moments; not measurements from a file.
  const stats = sites.map((site, index) => ({ site, n: 64,
    mean: test.mean + (index - (sites.length - 1) / 2) * (test.std || 1) * .45,
    std: (test.std || 1) * (1 + .18 * index) }));
  const means = stats.map(s => s.mean!), stds = stats.map(s => s.std!);
  const shift = Math.max(...means) - Math.min(...means);
  const sigma = test.std ? shift / test.std : null;
  const spec = test.low !== test.high ? shift / (test.high - test.low) * 100 : null;
  const ratio = Math.min(...stds) > 0 ? Math.max(...stds) / Math.min(...stds) : null;
  const shiftSev = band(sigma, 1, 2), specSev = band(spec, 8, 15), varSev = band(ratio, 1.5, 2.5);
  const minCg = Math.min(...selections.mmsa.sites.map(site => test.cg[site - 1] ?? NaN));
  const cgmBad = Number.isFinite(minCg) && minCg < 1.33;
  const cpkBad = test.cpk !== null && test.cpk < 2;
  const failed = selections.mmsa.sites.filter(site => (test.cg[site - 1] ?? NaN) < 1.33).length;
  const bandwidth = test.high - test.low;
  const cp = bandwidth > 0 && test.std ? bandwidth / (6 * test.std) : null;
  const marginUpper = bandwidth > 0 ? (test.high - test.mean) / bandwidth : null;
  const marginLower = bandwidth > 0 ? (test.mean - test.low) / bandwidth : null;
  let status: Status = "NOT_APPLICABLE", rule: string | null = null,
    reason = demoReasons.GATE_NOT_TRIGGERED, closeTo: "HIGH" | "LOW" | null = null;
  if (!cpkBad && !cgmBad) {
    // below every gate condition
  } else if (cgmBad) {
    status = "RISK"; rule = failed <= 4 ? "MMSA-001" : "MMSA-002";
    reason = demoReasons[failed <= 4 ? "LOCAL_SITE_HARDWARE_FAULT" : "TEST_RESOLUTION_OR_METHOD"];
  } else {
    const red = shiftSev === "RED" || varSev === "RED";
    const green = shiftSev === "GREEN" && varSev === "GREEN";
    if (red) {
      status = "RISK";
      rule = shiftSev === "RED" ? "MMSA-003" : "MMSA-004";
      reason = demoReasons[rule === "MMSA-003" ? "SYSTEMATIC_SITE_SHIFT" : "SITE_VARIANCE_ANOMALY"];
    } else if (green) {
      status = "RISK"; rule = "MMSA-005";
      if (cp !== null && marginUpper !== null && marginLower !== null) {
        if (cp >= 2 && Math.min(marginUpper, marginLower) < .22) {
          closeTo = marginUpper <= marginLower ? "HIGH" : "LOW";
          rule = "MMSA-007"; reason = demoReasons.CLOSE_TO_LIMIT_MARGIN;
        } else if (cp < 1.67) { rule = "MMSA-008"; reason = demoReasons.DISTRIBUTION_WIDE_OR_BIMODAL; }
        else { rule = "MMSA-005"; reason = demoReasons.DISTRIBUTION_OR_LIMIT_REVIEW; status = "REVIEW_REQUIRED"; }
      } else { reason = demoReasons.DISTRIBUTION_OR_LIMIT_REVIEW; status = "REVIEW_REQUIRED"; }
    } else {
      status = "REVIEW_REQUIRED"; rule = "MMSA-006"; reason = demoReasons.S2S_EVIDENCE_INCONCLUSIVE;
    }
  }
  return { cell: { status, value: sigma, n: sites.length * 64, reason, rule_id: rule, override: false, supported_by_bin1_cpk: test.cpk, shift_spec_pct: spec, var_ratio: ratio },
    s2s: { gate: { trigger: "ANY", cpk_lt: 2, cg_lt: 1.33 },
      bands: { shift_sigma: { green_lt: 1, red_ge: 2 },
      shift_spec_pct: { green_lt: 8, red_ge: 15 }, var_ratio: { green_lt: 1.5, red_ge: 2.5 } },
      cg_pass_threshold: 1.33, local_fault_max_sites: 4, site_count: sites.length,
      mean_shift: shift, overall_std: test.std, median_site_std: [...stds].sort((a, b) => a - b)[Math.floor(stds.length / 2)],
      max_site_std: Math.max(...stds), min_site_mean: Math.min(...means), max_site_mean: Math.max(...means),
      shift_sigma: sigma, shift_severity: shiftSev, shift_spec_pct: spec, shift_spec_severity: specSev,
      var_ratio: ratio, var_severity: varSev, cp, limit_margin_upper: marginUpper, limit_margin_lower: marginLower, close_to: closeTo,
      cgm_min_cg: Number.isFinite(minCg) ? minCg : null,
      cgm_failed_site_count: failed, overall_bin1_cpk: test.cpk, secondary_rule_id: null } };
}
export function demoRun(selections: Selections): Run {
  const cpk = buildDemoReport("cpk", selections.cpk, null);
  const cgm = buildDemoReport("cgm", selections.cgm, cpk);
  const rows = demoTests.filter(t => selections.cpk.tests.includes(t.id) || selections.cgm.tests.includes(t.id)
    || selections.mmsa.tests.includes(t.id)).map(t => {
    const cpkRow = cpk.rows.find(r => r.test.id === t.id), cgmRow = cgm.rows.find(r => r.test.id === t.id);
    const mmsa = demoMmsa(t, selections);
    const cells = [cpkRow && demoCell(cpkRow), cgmRow && demoCell(cgmRow), selections.mmsa.tests.includes(t.id) ? mmsa.cell : null]
      .filter(Boolean) as Cell[];
    const worst = cells.sort((a, b) => severity(a.status) - severity(b.status))[0];
    const partial = (!cpkRow || !cgmRow || !selections.mmsa.tests.includes(t.id)) && worst.status === "PASS";
    return { key: t.id, column: t.id, name: t.name, number: Number(t.id), unit: t.unit, lsl: t.low, usl: t.high,
      conditions: demoDataset.conditions, cpk: cpkRow ? demoCell(cpkRow) : null,
      cgm: cgmRow ? { ...demoCell(cgmRow), sites: selections.cgm.sites.map(site => ({ site,
        n: t.cg[site - 1] == null ? 0 : 64, cg: t.cg[site - 1] ?? null,
        status: t.cg[site - 1] == null ? 'INSUFFICIENT_DATA' : 'CALCULATED' })) } : null,
      mmsa: selections.mmsa.tests.includes(t.id) ? mmsa.cell : null,
      status: severity(worst.status) === 2 || partial ? "REVIEW_REQUIRED" as const : worst.status,
      reason: partial ? "部分检查没有匹配结果，请核对数据选择" : worst.reason };
  }).sort((a, b) => severity(a.status) - severity(b.status));
  return { id: "demo-run", dataset_id: "demo", rows, warnings: ["示例指标与图形用于体验页面，不随记录选择重新计算统计量。"], rules_hash: "DEMO" };
}
export function demoDetail(row: Row, selections: Selections): Detail {
  const test = demoTests.find(t => t.id === row.column)!;
  const details: Detail["details"] = {};
  const histogram = (index: number) => [1, 2, 4, 7, 11, 16, 18, 15, 10, 6, 3, 2, 1].map(v => Math.max(0, v - index));
  for (const module of ["cpk", "cgm"] as const) {
    if (!row[module]) continue;
    details[module] = { n: row[module]!.n, mean: test.mean, std: test.std, reason: row[module]!.reason,
      rule_id: null, excluded_counts: {}, sample_refs: [], sample_ref_count: 0, s2s: null,
      edges: Array.from({ length: 14 }, (_, i) => test.low + (test.high - test.low) * i / 13),
      sites: selections[module].sites.map((site, index) => ({ site, n: module === "cpk" ? 61 : 64,
        mean: test.mean, std: test.std, cg: test.cg[site - 1], histogram: histogram(index) })) };
  }
  if (row.mmsa) {
    const { s2s } = demoMmsa(test, selections);
    details.mmsa = { n: row.mmsa.n, mean: test.mean, std: test.std, reason: row.mmsa.reason,
      rule_id: row.mmsa.rule_id, excluded_counts: {}, sample_refs: [], sample_ref_count: 0, s2s,
      edges: Array.from({ length: 14 }, (_, i) => test.low + (test.high - test.low) * i / 13),
      sites: selections.mmsa.sites.map((site, index) => ({ site, n: 64,
        mean: s2s.max_site_mean !== null && s2s.min_site_mean !== null
          ? s2s.min_site_mean + (s2s.max_site_mean - s2s.min_site_mean) * index / Math.max(1, s2s.site_count - 1) : test.mean,
        std: s2s.max_site_std !== null ? s2s.max_site_std - .18 * (test.std || 1) * (s2s.site_count - 1 - index) : test.std,
        cg: test.cg[site - 1], histogram: histogram(index) })) };
  }
  return { row, details };
}
export const startingDemoSelections = (): Selections =>
  ({ cpk: { ...defaultSelection(), parts: null }, cgm: { ...defaultSelection(), parts: null }, mmsa: { ...defaultSelection(), parts: null } });
