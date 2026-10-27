import { allCandidates, initialOutliers, integrateOutliers, outlierRequest, validateOutliers } from './model';
import { base, reportFor, row, sourceA, sourceB } from './testFixtures';
import { hitsOption, matrixOption, waferKey, waferOption } from './chartOptions';
import { outlierExport } from './export';
import { run } from './testFixtures';

it('keeps the same part index and PART_ID separate across sources', () => {
  const reports = [reportFor(), reportFor(sourceB)];
  const chips = allCandidates(reports);
  expect(chips.map(c => c.key)).toEqual(['source-a:41', 'source-b:41']);
  const rows = integrateOutliers([row], reports, [], false);
  expect(rows).toHaveLength(1); expect(rows[0].outlier.candidates).toHaveLength(2);
  expect(rows[0].outlier.label).toBe('发现异常候选记录');
  expect(rows[0].status).toBe('REVIEW_REQUIRED'); expect(row.status).toBe('PASS');
});
it('retains FAIL and RISK when candidates are found', () => {
  for (const status of ['FAIL', 'RISK'] as const) expect(integrateOutliers([{ ...row, status }], [reportFor()], [], false)[0].status).toBe(status);
});
it.each(['INSUFFICIENT_DATA', 'NOT_CALCULABLE'])('does not treat %s as passing', status => {
  const source = reportFor(); source.report.chips = []; source.report.baselines[0] = { ...base, status, candidate_count: 0 };
  expect(integrateOutliers([row], [source], [], false)[0].status).toBe('REVIEW_REQUIRED');
});
it('leaves a complete no-candidate result and an inapplicable check distinct', () => {
  const source = reportFor(); source.report.chips = [];
  source.report.baselines[0] = { ...base, status: 'NO_OUTLIER_FOUND', candidate_count: 0 };
  const result = integrateOutliers([row], [source], [], false)[0];
  expect(result.outlier.label).toBe('未发现异常候选记录'); expect(result.status).toBe('PASS');
  source.report.baselines[0].status = 'NOT_APPLICABLE';
  expect(integrateOutliers([row], [source], [], false)[0].outlier.label).toBe('不适用');
  expect(integrateOutliers([row], [source], [], false)[0].status).toBe('PASS');
});
it('marks partial evaluation and failed sources explicitly', () => {
  const source = reportFor(); source.report.baselines.push({ ...base, baseline_id: 'short', site: 2, status: 'INSUFFICIENT_DATA', n: 2 });
  expect(integrateOutliers([row], [source], [], false)[0].outlier.label).toBe('部分未评估');
  const failed = integrateOutliers([row], [], [{ dataset_id: sourceA.id, filename: sourceA.filename, error: 'cancelled' }], false)[0];
  expect(failed.status).toBe('REVIEW_REQUIRED'); expect(failed.outlier.label).toBe('来源计算未完成');
});
it('never joins a same-name test with different spec, unit or conditions', () => {
  for (const patch of [{ usl: 2 }, { unit: 'mV' }, { conditions: { test_temperature: '125' } }]) {
    const source = reportFor(); Object.assign(source.report.baselines[0], patch);
    const results = integrateOutliers([row], [source], [], false);
    expect(results).toHaveLength(2); expect(results[0].outlier.candidates).toHaveLength(0);
    expect(results[1].outlier.candidates).toHaveLength(1);
  }
});
it('does not collapse ambiguous MPR identities or duplicate original rows', () => {
  const source = reportFor(); source.report.baselines.push({ ...base, group_id: 'group-2', baseline_id: 'mpr-2', result_index: 1 });
  expect(integrateOutliers([row], [source], [], false)).toHaveLength(3);
  expect(integrateOutliers([row, { ...row, key: 'duplicate' }], [reportFor()], [], false)).toHaveLength(3);
});
it('requires proven local group identity for MPR channels and rejects a different column', () => {
  const source = reportFor(); source.report.baselines[0] = { ...base, record_type: 'MPR', result_index: 1, column: 'MPR_B' };
  const original = { ...row, column: 'MPR_A', cpk: { ...row.cpk!, dataset_id: sourceA.id, group_id: 'MPR_A_GROUP' } };
  expect(integrateOutliers([original], [source], [], false)).toHaveLength(2);
  original.cpk.group_id = source.report.baselines[0].group_id;
  expect(integrateOutliers([original], [source], [], false)).toHaveLength(1);
});
it('validates source selections and converts per-file excluded records', () => {
  const settings = initialOutliers(sourceA);
  settings.selections[sourceA.id].excludedParts = [0];
  expect(validateOutliers(settings, [sourceA])).toBeNull();
  const payload = outlierRequest(settings, sourceA);
  expect(payload.part_indices).toHaveLength(49); expect(payload.part_indices).not.toContain(0);
  expect(payload).not.toHaveProperty('minimum_samples'); expect(payload.minimum_samples_per_site).toBe(30);
  settings.sourceIds = []; expect(validateOutliers(settings, [sourceA])).toMatch('至少选择一份');
});
it.each([['sigma', '0'], ['sigma', 'Infinity'], ['minimum', '3'], ['minimum', '4.5']])('rejects invalid %s %s', (key, value) => {
  const settings = { ...initialOutliers(sourceA), [key]: value };
  expect(validateOutliers(settings, [sourceA])).not.toBeNull();
});
it('builds chart points only from hits and keeps wafers isolated by source/lot', () => {
  const chips = allCandidates([reportFor(), reportFor(sourceB)]);
  expect(waferKey(chips[0])).not.toBe(waferKey(chips[1]));
  const matrix = matrixOption(chips, [
    { ...base, dataset_id: sourceA.id, filename: sourceA.filename, threshold: 6 },
    { ...base, dataset_id: sourceB.id, filename: sourceB.filename, threshold: 6 }]);
  expect(JSON.stringify(matrix)).toContain('source-a:41'); expect(JSON.stringify(matrix)).toContain('source-b:41');
  expect(JSON.stringify(hitsOption(chips[0], 6))).toContain('8.2');
  expect(JSON.stringify(waferOption(chips, chips[0].key))).toContain('2 条异常候选记录');
});
it('exports the displayed verdict and all source evidence without duplicating complete chip hits per row', () => {
  const reports = [reportFor(), reportFor(sourceB)], rows = integrateOutliers([row], reports, [], false);
  const exported = outlierExport({ result: run, reports: { cpk: { old: true } } }, rows, reports, [], JSON.stringify(initialOutliers(sourceA)));
  expect(exported.result.rows[0].status).toBe('REVIEW_REQUIRED');
  expect(exported.original_three_check_rows?.[0].status).toBe('PASS');
  expect(exported.result.rows[0].outlier?.candidate_keys).toEqual(['source-a:41', 'source-b:41']);
  expect(exported.result.rows[0].outlier).not.toHaveProperty('candidates');
  expect(exported.outliers.source_reports).toHaveLength(2); expect(exported).toMatchObject({ reports: { cpk: { old: true } } });
  const partial = outlierExport({ result: run }, rows, reports, [{ dataset_id: 'missing', filename: 'missing.stdf', error: 'cancelled' }], null);
  expect(partial.outliers.status).toBe('PARTIAL');
});
