import type { Run } from './model';
import type { Row } from './model';
import type { SourceReport, SourceIssue, OutlierCell } from './outliers/model';
import type { OutlierNotes } from './outliers/OutlierRemark';
import { getLanguage } from '../../i18n';
import { downloadBlob } from './charts/export';

export type ReportDownload = { filename: string; blob: Blob; url?: never } | { filename: string; url: string; blob?: never };

export async function requestReportDownload(path: string) {
  const handled = !window.dispatchEvent(new CustomEvent('release-report-download', { detail: path, cancelable: true }));
  if (!handled) downloadReport(await fetchReportFile(path));
}

export function downloadReport(report: ReportDownload) {
  if (report.url) {
    const link = document.createElement('a');
    link.href = report.url; link.download = report.filename;
    document.body.appendChild(link); link.click(); link.remove();
  } else downloadBlob(report.blob!, report.filename);
}

export async function fetchReportFile(path: string, signal?: AbortSignal, jobId?: string): Promise<ReportDownload> {
  const query = new URLSearchParams({ delivery: 'file', lang: getLanguage() });
  if (jobId) query.set('job_id', jobId);
  const response = await fetch(`${path}${path.includes('?') ? '&' : '?'}${query}`, { signal });
  if (!response.ok) {
    const error = await response.json().catch(() => null);
    throw new Error(typeof error?.detail === 'string' ? error.detail : '导出失败，结果可能已过期，请重新计算。');
  }
  if (response.headers.get('Content-Type')?.includes('application/json')) {
    const ready = await response.json();
    if (typeof ready.url !== 'string' || !/^\/api\/release-check\/report-downloads\/[a-f0-9]{32}$/.test(ready.url)
        || typeof ready.filename !== 'string') throw new Error('下载地址无效，请重新导出。');
    return { url: ready.url, filename: ready.filename };
  }
  const disposition = response.headers.get('Content-Disposition') ?? '';
  const encoded = /filename\*=UTF-8''([^;]+)/i.exec(disposition)?.[1];
  const plain = /filename="([^"]+)"/i.exec(disposition)?.[1];
  return { blob: await response.blob(), filename: encoded ? decodeURIComponent(encoded) : plain ?? path.split('/').pop()! };
}

export async function fetchRunWorkbook(run: Pick<Run, 'id' | 'dataset_id'>, module: 'cpk' | 'cgm', signal?: AbortSignal, jobId?: string) {
  return fetchRunReport(run, module, 'xlsx', signal, jobId);
}

export async function fetchRunCgmPdf(run: Pick<Run, 'id' | 'dataset_id'>, signal?: AbortSignal, jobId?: string) {
  return fetchRunReport(run, 'cgm', 'pdf', signal, jobId);
}

export async function fetchRunCpkPdf(run: Pick<Run, 'id' | 'dataset_id'>, signal?: AbortSignal, jobId?: string) {
  return fetchRunReport(run, 'cpk', 'pdf', signal, jobId);
}

export async function fetchRunReviewReport(run: Pick<Run, 'id' | 'dataset_id'>, module: 'site_diff' | 'outlier', format: 'xlsx' | 'pdf', signal?: AbortSignal, jobId?: string) {
  return fetchReportFile(`/api/release-check/datasets/${encodeURIComponent(run.dataset_id)}/runs/${encodeURIComponent(run.id)}/reports/${module}.${format}`, signal, jobId);
}

export async function saveRunOutlierSnapshot(run: Pick<Run, 'id' | 'dataset_id'>, reports: SourceReport[], notes: OutlierNotes,
  rows: (Row & { outlier?: OutlierCell })[], issues: SourceIssue[], signal?: AbortSignal) {
  const observations = new Map<string, {dataset_id: string; group_id: string; note: OutlierNotes[string]}>();
  for (const row of rows) {
    const note = notes[row.key];
    if (!note) continue;
    for (const baseline of row.outlier?.baselines ?? []) observations.set(`${baseline.dataset_id}:${baseline.group_id}`,
      {dataset_id: baseline.dataset_id, group_id: baseline.group_id, note});
  }
  const response = await fetch(`/api/release-check/datasets/${encodeURIComponent(run.dataset_id)}/runs/${encodeURIComponent(run.id)}/outlier-reports`, {
    method: 'PUT', signal, headers: {'Content-Type':'application/json'}, body: JSON.stringify({
      sources: reports.filter(r => r.report_id).map(r => ({dataset_id:r.dataset_id,report_id:r.report_id})),
      observations: [...observations.values()], issues,
    }),
  });
  if (!response.ok) throw new Error('Outlier 报告关联或备注保存失败，请重新计算后再导出。');
}

async function fetchRunReport(run: Pick<Run, 'id' | 'dataset_id'>, module: 'cpk' | 'cgm', format: 'xlsx' | 'pdf', signal?: AbortSignal, jobId?: string) {
  return fetchReportFile(`/api/release-check/datasets/${encodeURIComponent(run.dataset_id)}/runs/${encodeURIComponent(run.id)}/reports/${module}.${format}`, signal, jobId);
}
