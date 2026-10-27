import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { decodeReleaseResponse, fullOutlierReports } from './presentation';
import { Diagnosis } from './Diagnosis';
import { request, type Diagnosis as DiagnosisValue } from './model';
import { reportFor, sourceA } from './outliers/testFixtures';

afterEach(() => vi.unstubAllGlobals());

it('decodes every sparse hit against its exact reference group without changing the wire object', () => {
  const source = reportFor(sourceA);
  const positions = new Map(source.report.baselines.map((b, i) => [b.baseline_id, i]));
  const wire = { ...source, wire_format: 'release-ui-v1', report: { ...source.report,
    chips: source.report.chips.map(c => ({ ...c, hits: c.hits.map(h => [positions.get(h.baseline_id), h.record_sequence, h.value, h.score, h.direction]) })) } };
  const before = JSON.stringify(wire);
  const decoded = decodeReleaseResponse(wire) as typeof source;
  expect(decoded.report.chips).toEqual(source.report.chips);
  expect(JSON.stringify(wire)).toBe(before);
  expect(decodeReleaseResponse(source)).toBe(source);
});

it('requests the UI summary while preserving caller headers and cancellation', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => ({ value: 1 }) });
  vi.stubGlobal('fetch', fetch);
  const controller = new AbortController();
  expect(await request('/runs', { method: 'POST', headers: { 'Content-Type': 'application/json' }, signal: controller.signal })).toEqual({ value: 1 });
  const init = fetch.mock.calls[0][1];
  expect(init.headers.get('X-Release-View')).toBe('summary-v1');
  expect(init.headers.get('Content-Type')).toBe('application/json');
  expect(init.signal).toBe(controller.signal);
});

it('reads complete Outlier evidence only for an explicit full export', async () => {
  const source = { ...reportFor(sourceA), report_id: 'saved', report: { ...reportFor(sourceA).report, detail_level: 'summary' as const } };
  const complete = reportFor(sourceA).report;
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => complete });
  vi.stubGlobal('fetch', fetch);
  const result = await fullOutlierReports([source, reportFor(sourceA)]);
  expect(fetch).toHaveBeenCalledTimes(1);
  expect(fetch.mock.calls[0][0]).toBe(`/api/release-check/datasets/${source.dataset_id}/outliers/saved/report`);
  expect(result[0].report).toEqual(complete);
  expect(result[1].report).toEqual(complete);
  expect(source.report.detail_level).toBe('summary');
});

it('loads diagnostic provenance only when expanded', async () => {
  const value: DiagnosisValue = { remark: 'Saved remark', auto_remark: 'Saved remark', current_remark: '',
    category: null, knowledge: { status: 'none' }, knowledge_hash: 'hash', knowledge_version: '1',
    review_state: 'AUTO_REVIEW_REQUIRED', detail_url: '/datasets/source/runs/run/items/test/diagnosis/cpk' };
  const full = { ...value, current_remark: 'Full calculation evidence', knowledge: { status: 'reference', historical_remark: 'Historical detail' } };
  const fetch = vi.fn().mockResolvedValue({ ok: true, json: async () => full });
  vi.stubGlobal('fetch', fetch);
  render(<Diagnosis value={value} />);
  expect(fetch).not.toHaveBeenCalled();
  const detail = screen.getByText('本次依据与项目经验来源').closest('details')!;
  detail.open = true; fireEvent(detail, new Event('toggle'));
  await screen.findByText('Full calculation evidence');
  expect(screen.getByText(/Historical detail/)).toBeTruthy();
  expect(fetch).toHaveBeenCalledTimes(1);
});

it('keeps the saved remark visible when lazy evidence fails and retries after reopening', async () => {
  const value: DiagnosisValue = { remark: 'Saved remark', auto_remark: '', current_remark: '', category: null,
    knowledge: { status: 'none' }, knowledge_hash: 'hash', knowledge_version: '1', review_state: 'AUTO_REVIEW_REQUIRED',
    detail_url: '/datasets/source/runs/run/items/test/diagnosis/cpk' };
  const fetch = vi.fn().mockRejectedValueOnce(new Error('Evidence unavailable')).mockResolvedValue({ ok: true,
    json: async () => ({ ...value, current_remark: 'Recovered evidence' }) });
  vi.stubGlobal('fetch', fetch);
  render(<Diagnosis value={value} />);
  const detail = screen.getByText('本次依据与项目经验来源').closest('details')!;
  detail.open = true; fireEvent(detail, new Event('toggle'));
  await screen.findByRole('alert');
  expect(screen.getByText('Saved remark')).toBeTruthy();
  detail.open = false; fireEvent(detail, new Event('toggle'));
  detail.open = true; fireEvent(detail, new Event('toggle'));
  await screen.findByText('Recovered evidence');
});
