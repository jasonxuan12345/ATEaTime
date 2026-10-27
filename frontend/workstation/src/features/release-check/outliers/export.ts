import type { Row, Run } from '../model';
import type { OutlierCell, SourceIssue, SourceReport } from './model';

export function outlierExport(original: { result?: Run; [key: string]: unknown }, rows: (Row & { outlier?: OutlierCell })[],
  reports: SourceReport[], issues: SourceIssue[], snapshot: string | null) {
  return { ...original, original_three_check_rows: original.result?.rows,
    result: { ...original.result, rows: rows.map(({ outlier, ...row }) => ({ ...row, outlier: outlier && {
      label: outlier.label, review: outlier.review, candidate_count: outlier.candidates.length, max_score: outlier.maxScore,
      filenames: outlier.filenames, candidate_keys: outlier.candidates.map(c => c.key),
      baseline_refs: outlier.baselines.map(b => ({ dataset_id: b.dataset_id, baseline_id: b.baseline_id })) } })) },
    outliers: { status: issues.length ? 'PARTIAL' : 'COMPLETE', source_reports: reports, source_issues: issues,
      settings: JSON.parse(snapshot ?? '{}') } };
}
