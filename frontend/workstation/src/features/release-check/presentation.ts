import type { Chip, Hit, OutlierReport, SourceReport } from './outliers/model';

type WireHit = [number, number, number, number, 'HIGH' | 'LOW'];
type WireChip = Omit<Chip, 'hits'> & { hits: WireHit[] };
type WireResponse = { wire_format: 'release-ui-v1'; report: Omit<OutlierReport, 'chips'> & { chips: WireChip[] } };

export function decodeReleaseResponse(value: unknown): unknown {
  if (!value || typeof value !== 'object' || !('wire_format' in value) || value.wire_format !== 'release-ui-v1') return value;
  const wire = value as WireResponse;
  const chips = wire.report.chips.map(chip => ({ ...chip, hits: chip.hits.map(([index, sequence, measured, score, direction]): Hit => {
    const b = wire.report.baselines[index];
    if (!b) throw new Error('Invalid Outlier reference index');
    return { baseline_id: b.baseline_id, group_id: b.group_id, column: b.column, test_num: b.test_num,
      test_name: b.test_name, record_type: b.record_type, result_index: b.result_index, unit: b.unit,
      median: b.median, robust_sigma: b.robust_sigma, lower_limit: b.lower_limit, upper_limit: b.upper_limit,
      record_sequence: sequence, value: measured, score, direction };
  }) }));
  return { ...wire, report: { ...wire.report, chips } };
}

export async function fullOutlierReports(reports: SourceReport[], signal?: AbortSignal): Promise<SourceReport[]> {
  return Promise.all(reports.map(async source => {
    signal?.throwIfAborted();
    if (source.report.detail_level !== 'summary') return source;
    if (!source.report_id) throw new Error('下载失败，文件或结果可能已过期，请重新导入或计算。');
    const response = await fetch(`/api/release-check/datasets/${source.dataset_id}/outliers/${source.report_id}/report`, { signal });
    if (!response.ok) throw new Error('下载失败，文件或结果可能已过期，请重新导入或计算。');
    const report = await response.json() as OutlierReport;
    signal?.throwIfAborted();
    return { ...source, report };
  }));
}
