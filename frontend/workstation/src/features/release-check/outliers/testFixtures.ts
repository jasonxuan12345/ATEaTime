// Synthetic response fixtures. No imported measurement files or real API calls.
import type { Dataset, Row, Run } from '../model';
import type { Baseline, Chip, SourceReport } from './model';

export const sourceA: Dataset = { id: 'source-a', filename: 'alpha.stdf', record_count: 50, sites: [1, 2],
  conditions: { test_temperature: '25' }, tests: [{ column: 'VREF', name: 'VREF', number: 1001, unit: 'V' }],
  parts: Array.from({ length: 50 }, (_, id) => ({ id, name: `record ${id}`, site: id % 2 + 1, bin: 1 })) };
export const sourceB: Dataset = { ...sourceA, id: 'source-b', filename: 'beta.stdf', sites: [3],
  parts: sourceA.parts.map(part => ({ ...part, site: 3 })) };
export const base: Baseline = { baseline_id: 'baseline-1', group_id: 'group-1', column: 'VREF', test_num: 1001,
  test_name: 'VREF', record_type: 'PTR', result_index: null, conditions: { test_temperature: '25' },
  lot: 'L1', wafer: 'W1', head: 1, site: 1, unit: 'V', lsl: .8, usl: 1.2,
  n: 40, q1: .9865, median: 1, q3: 1.0135, robust_sigma: .02, lower_limit: .88, upper_limit: 1.12,
  status: 'OUTLIER_CANDIDATES', reason: null, candidate_count: 1, excluded_counts: {} };
export const chip: Chip = { part_index: 41, prr_record_sequence: 419, part_id: 'REPEATED_ID', lot: 'L1', wafer: 'W1',
  x: 12, y: 8, head: 1, site: 1, hit_count: 1, max_score: 8.2,
  hits: [{ baseline_id: base.baseline_id, group_id: base.group_id, column: base.column, test_num: base.test_num,
    test_name: base.test_name, record_type: 'PTR', result_index: null, unit: 'V', median: 1, robust_sigma: .02,
    lower_limit: .88, upper_limit: 1.12, record_sequence: 410, value: 1.164, score: 8.2, direction: 'HIGH' }] };
export const reportFor = (file = sourceA): SourceReport => ({ dataset_id: file.id, filename: file.filename,
  report: { algorithm: { sigma_multiplier: 6, minimum_samples_per_site: 30 }, baselines: [{ ...base, site: file.sites[0] }],
    chips: [{ ...chip, site: file.sites[0] }], warnings: ['仅筛选异常候选记录'], unexecuted_test_columns: [],
    summary: { selected_record_count: 50, candidate_part_count: 1, candidate_hit_count: 1, evaluated_baseline_count: 1, unevaluated_baseline_count: 0 } } });
const cell = { status: 'PASS' as const, value: 2.3, n: 40, reason: '通过', rule_id: null, override: false, supported_by_bin1_cpk: null };
export const row: Row = { key: 'row-1', column: base.column, name: base.test_name, number: base.test_num,
  unit: base.unit, lsl: base.lsl, usl: base.usl, conditions: base.conditions, cpk: { ...cell }, cgm: { ...cell }, mmsa: { ...cell }, status: 'PASS', reason: '通过' };
export const run: Run = { id: 'mock-run', dataset_id: sourceA.id, rows: [row], warnings: [], rules_hash: 'mock-rules' };
export const emptyOutlierReport = { dataset_id: 'mock', report: { ...reportFor().report, baselines: [], chips: [] } };
