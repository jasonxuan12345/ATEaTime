import { vi, afterEach, it, expect } from 'vitest';
import { fetchRunWorkbook, fetchRunCgmPdf, fetchRunCpkPdf, downloadReport, requestReportDownload, fetchRunReviewReport, saveRunOutlierSnapshot } from './reportExport';
import { integrateOutliers } from './outliers/model';
import { sourceA, sourceB, reportFor, run } from './outliers/testFixtures';

afterEach(() => vi.unstubAllGlobals());

it('uses the same run owner, job and file delivery for Site Diff and Outlier reports', async () => {
  const fetch=vi.fn().mockResolvedValue({ok:true,headers:new Headers(),blob:async()=>new Blob(['report'])});
  vi.stubGlobal('fetch',fetch);
  for(const module of ['site_diff','outlier'] as const)for(const format of ['xlsx','pdf'] as const)
    await fetchRunReviewReport({dataset_id:'owner',id:'saved'},module,format,undefined,'job');
  expect(fetch.mock.calls.map(c=>c[0])).toEqual(['site_diff.xlsx','site_diff.pdf','outlier.xlsx','outlier.pdf'].map(name=>
    `/api/release-check/datasets/owner/runs/saved/reports/${name}?delivery=file&lang=zh&job_id=job`));
});

it('binds saved observations to exact source/group identities before export', async () => {
  const reports=[reportFor(sourceA),reportFor(sourceB)].map((r,i)=>({...r,report_id:`saved-outlier-${i}`}));
  const rows=integrateOutliers(run.rows,reports,[],false);
  const note={tags:[],remark:'Saved user text',suggestion:'',final:null,state:'待复核',preview:'Saved user text'};
  const row=rows.find(r=>r.outlier?.baselines.length)!;
  const fetch=vi.fn().mockResolvedValue({ok:true});vi.stubGlobal('fetch',fetch);
  await saveRunOutlierSnapshot(run,reports,{[row.key]:note},rows,[]);
  const body=JSON.parse(fetch.mock.calls[0][1].body);
  expect(body.sources).toEqual(reports.map(r=>({dataset_id:r.dataset_id,report_id:r.report_id})));
  expect(body.observations).toHaveLength(new Set(row.outlier!.baselines.map(b=>`${b.dataset_id}:${b.group_id}`)).size);
  expect(body.observations.every((o:any)=>o.note.remark==='Saved user text')).toBe(true);
});

it('attaches a pollable job id for Excel and both PDF exports', async () => {
  const fetch = vi.fn().mockResolvedValue({ ok: true, headers: new Headers(), blob: async () => new Blob(['report']) });
  vi.stubGlobal('fetch', fetch);
  const run = { dataset_id: 'a', id: 'r' };
  await fetchRunWorkbook(run, 'cgm', undefined, 'job-1');
  await fetchRunCgmPdf(run, undefined, 'job-2');
  await fetchRunCpkPdf(run, undefined, 'job-3');
  expect(fetch.mock.calls.map(call => call[0])).toEqual([
    '/api/release-check/datasets/a/runs/r/reports/cgm.xlsx?delivery=file&lang=zh&job_id=job-1',
    '/api/release-check/datasets/a/runs/r/reports/cgm.pdf?delivery=file&lang=zh&job_id=job-2',
    '/api/release-check/datasets/a/runs/r/reports/cpk.pdf?delivery=file&lang=zh&job_id=job-3',
  ]);
});

it('downloads CPK PDF independently with its exact filename', async () => {
  const filename = '20261020_设备A_Cpk_Report.pdf';
  const fetch = vi.fn().mockResolvedValue({ ok: true,
    headers: new Headers({ 'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(filename)}` }),
    blob: async () => new Blob(['%PDF']) });
  vi.stubGlobal('fetch', fetch);
  const controller = new AbortController();
  const result = await fetchRunCpkPdf({ dataset_id: 'a', id: 'run' }, controller.signal);
  expect(fetch).toHaveBeenCalledWith('/api/release-check/datasets/a/runs/run/reports/cpk.pdf?delivery=file&lang=zh', { signal: controller.signal });
  expect(result.filename).toBe(filename);
});

it('downloads CGM PDF independently with the actual Unicode filename', async () => {
  const filename = '20261020_设备B_Cgm_Report.pdf';
  const fetch = vi.fn().mockResolvedValue({ ok: true,
    headers: new Headers({ 'Content-Disposition': `attachment; filename*=UTF-8''${encodeURIComponent(filename)}` }),
    blob: async () => new Blob(['%PDF']) });
  vi.stubGlobal('fetch', fetch);
  const result = await fetchRunCgmPdf({ dataset_id: 'a', id: 'run' });
  expect(fetch).toHaveBeenCalledWith('/api/release-check/datasets/a/runs/run/reports/cgm.pdf?delivery=file&lang=zh', { signal: undefined });
  expect(result.filename).toBe(filename);
});

it('uses the calculated run owner and the exact Unicode filename returned by the server', async () => {
  const filename = '20261020_设备B_Cgm_Report.xlsx';
  const fetch = vi.fn().mockResolvedValue({ ok: true,
    headers: new Headers({ 'Content-Disposition': `attachment; filename="cgm-report.xlsx"; filename*=UTF-8''${encodeURIComponent(filename)}` }),
    blob: async () => new Blob(['PK']) });
  vi.stubGlobal('fetch', fetch);
  const controller = new AbortController();
  const result = await fetchRunWorkbook({ dataset_id: 'owner-a', id: 'saved-run' }, 'cgm', controller.signal);
  expect(fetch).toHaveBeenCalledWith('/api/release-check/datasets/owner-a/runs/saved-run/reports/cgm.xlsx?delivery=file&lang=zh', { signal: controller.signal });
  expect(result.filename).toBe(filename);
  expect(result.blob?.size).toBe(2);
});

it('shows the actual server error and never downloads an error body as Excel', async () => {
  const blob = vi.fn();
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({ detail: '结果已过期，请重新计算' }), blob }));
  await expect(fetchRunWorkbook({ dataset_id: 'a', id: 'old' }, 'cpk')).rejects.toThrow('结果已过期');
  expect(blob).not.toHaveBeenCalled();
});

it('passes abort requests through without replacing the error', async () => {
  const controller = new AbortController(); controller.abort();
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new DOMException('Aborted', 'AbortError')));
  await expect(fetchRunWorkbook({ dataset_id: 'a', id: 'run' }, 'cpk', controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
});

it('receives a download address without buffering the report as a browser Blob', async () => {
  const blob = vi.fn();
  const ready = { filename: 'large.pdf', url: '/api/release-check/report-downloads/' + 'a'.repeat(32), size: 93_000_000 };
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: true,
    headers: new Headers({ 'Content-Type': 'application/json' }), json: async () => ready, blob }));
  const result = await fetchRunCpkPdf({ dataset_id: 'a', id: 'r' });
  expect(result).toEqual({ filename: ready.filename, url: ready.url });
  expect(blob).not.toHaveBeenCalled();
  const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
  downloadReport(result);
  expect(click).toHaveBeenCalledOnce();
  click.mockRestore();
});

it('delegates auxiliary reports to the page so they share progress and cancellation', async () => {
  const fetch = vi.fn(); vi.stubGlobal('fetch', fetch);
  const handler = vi.fn((event: Event) => event.preventDefault());
  window.addEventListener('release-report-download', handler);
  try {
    await requestReportDownload('/api/release-check/datasets/a/runs/r/observations.xlsx');
    expect(handler).toHaveBeenCalledOnce();
    expect(fetch).not.toHaveBeenCalled();
  } finally { window.removeEventListener('release-report-download', handler); }
});
