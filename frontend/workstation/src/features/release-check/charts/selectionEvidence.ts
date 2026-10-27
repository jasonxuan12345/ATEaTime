import { matches } from './options';
import type { ChartData, SamplePage, Selection } from './types';

// Full-data selection queries can return samples omitted by the scatter downsample.
// Add only the current page as display evidence; report statistics stay unchanged.
export function selectionEvidence(data: ChartData, page: SamplePage | null, selection: Selection): ChartData {
  if (!page || !Object.keys(selection).length) return data;
  const fields = ['dataset_id', 'run_id', 'module', 'source_sha256', 'group_id', 'column', 'result_index'] as const;
  if (fields.some(field => data.context[field] !== page.context[field])) return data;
  const ids = new Set(data.points.map(point => point.id));
  const extra = page.points.filter(point => !ids.has(point.id) && matches(point, selection));
  if (!extra.length) return data;
  const points = [...data.points, ...extra].sort((a, b) => a.record_sequence - b.record_sequence);
  return { ...data, points, displayed_points: points.length };
}
