import { fireEvent, render, screen, waitFor, cleanup } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { Selection } from './model';
import { ReleaseCheckPage } from './ReleaseCheckPage';
import { fetchRunWorkbook, fetchRunCgmPdf, fetchRunCpkPdf } from './reportExport';
import { downloadBlob } from './charts/export';

vi.mock('./model', async () => {
  const actual = await vi.importActual<typeof import('./model')>('./model');
  const dataset = { ...actual.demoDataset, id: 'owner-a' };
  return { ...actual, demoDataset: dataset,
    startingDemoSelections: () => actual.initialSelections(dataset),
    demoRun: () => ({ id: 'saved-run', dataset_id: 'owner-a', rows: [], warnings: [], rules_hash: 'rules' }),
    request: vi.fn(async () => ({ id: 'saved-run', dataset_id: 'owner-a', rows: [], warnings: [], rules_hash: 'rules' })) };
});
vi.mock('./useReleaseOperation', () => ({ useReleaseOperation: () => ({
  start: () => ({ id: 'job', controller: new AbortController() }), finish: vi.fn(),
  phase: '正在生成报表', progress: 45, active: false, cancelling: false }) }));
vi.mock('./reportExport', async (importOriginal) => ({ ...await importOriginal<typeof import('./reportExport')>(), fetchRunWorkbook: vi.fn(), fetchRunCgmPdf: vi.fn(), fetchRunCpkPdf: vi.fn() }));
vi.mock('./charts/export', () => ({ downloadBlob: vi.fn() }));
vi.mock('./ReviewWorkbench', () => ({ ReviewWorkbench: ({ onDirty }: { onDirty: (dirty: boolean) => void }) =>
  <button onClick={() => onDirty(true)}>编辑未保存备注</button> }));
vi.mock('./SelectionPanel', () => ({ SelectionPanel: ({ selection, onChange }: { selection: Selection; onChange: (value: Selection) => void }) =>
  <button onClick={() => onChange({ ...selection, minimum: '2' })}>修改计算范围</button> }));
vi.mock('./FilePool', () => ({ FilePool: () => null }));
vi.mock('./outliers/OutlierSettings', () => ({ OutlierSettings: () => null }));
vi.mock('./outliers/model', async () => ({
  ...await vi.importActual<typeof import('./outliers/model')>('./outliers/model'),
  validateOutliers: () => null,
}));

beforeEach(() => {
  vi.stubGlobal('fetch', vi.fn(async () => ({ ok: true })));
  vi.mocked(fetchRunWorkbook).mockReset();
  vi.mocked(fetchRunCgmPdf).mockReset();
  vi.mocked(fetchRunCpkPdf).mockReset();
  vi.mocked(downloadBlob).mockClear();
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function preparedPage() {
  render(<ReleaseCheckPage />);
  fireEvent.click(screen.getByRole('button', { name: '数据源与计算配置' }));
  fireEvent.click(screen.getByRole('button', { name: '重新计算' }));
  fireEvent.click(screen.getByText('导出', { selector: 'summary' }));
  await waitFor(() => expect(screen.getByRole('button', { name: 'CPK Excel (.xlsx)' })).toBeEnabled());
}

it('prepares Parquet only on demand and blocks repeated downloads until ready', async () => {
  await preparedPage();
  let finish!: (response: Response) => void;
  vi.mocked(fetch).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: '保存 Parquet' }));
  expect(fetch).toHaveBeenCalledWith('/api/release-check/datasets/owner-a/parquet?job_id=job',
    expect.objectContaining({ signal: expect.any(AbortSignal) }));
  expect(screen.getByRole('button', { name: '保存 Parquet' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'CPK Excel (.xlsx)' })).toBeDisabled();
  expect(screen.getByText('正在从原始 STDF 生成 Parquet…', { exact: false })).toBeVisible();
  const blob = new Blob(['PAR1']);
  finish({ ok: true, blob: async () => blob } as Response);
  await waitFor(() => expect(downloadBlob).toHaveBeenCalledWith(blob, expect.stringMatching(/\.wide_all\.parquet$/)));
  expect(screen.getByRole('button', { name: '保存 Parquet' })).toBeEnabled();
});

it('allows retry after Parquet generation fails', async () => {
  await preparedPage();
  vi.mocked(fetch).mockResolvedValue({ ok: false } as Response);
  fireEvent.click(screen.getByRole('button', { name: '保存 Parquet' }));
  await waitFor(() => expect(screen.getByRole('button', { name: '保存 Parquet' })).toBeEnabled());
  expect(downloadBlob).not.toHaveBeenCalled();
  expect(screen.getByText('下载失败，文件或结果可能已过期，请重新导入或计算。')).toBeVisible();
});

it('downloads the selected saved run and blocks repeated export while waiting', async () => {
  await preparedPage();
  let finish!: (value: { blob: Blob; filename: string }) => void;
  vi.mocked(fetchRunWorkbook).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  const cpk = screen.getByRole('button', { name: 'CPK Excel (.xlsx)' });
  fireEvent.click(cpk); fireEvent.click(cpk);
  expect(fetchRunWorkbook).toHaveBeenCalledTimes(1);
  expect(fetchRunWorkbook).toHaveBeenCalledWith(expect.objectContaining({ dataset_id: 'owner-a', id: 'saved-run' }), 'cpk', expect.any(AbortSignal), 'job');
  expect(screen.getByRole('progressbar', { name: '报告导出进度' })).toHaveAttribute('value', '45');
  expect(screen.getByRole('button', { name: 'Cgm Excel (.xlsx)' })).toBeDisabled();
  const blob = new Blob(['PK']);
  finish({ blob, filename: 'actual-source-report.xlsx' });
  await waitFor(() => expect(downloadBlob).toHaveBeenCalledWith(blob, 'actual-source-report.xlsx'));
  expect(screen.getByRole('button', { name: 'CPK Excel (.xlsx)' })).toBeEnabled();
  expect(screen.queryByRole('progressbar')).not.toBeInTheDocument();
});

it('downloads PDF independently and blocks both Excel exports while waiting', async () => {
  await preparedPage();
  let finish!: (value: { blob: Blob; filename: string }) => void;
  vi.mocked(fetchRunCgmPdf).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'Cgm PDF (.pdf)' }));
  expect(fetchRunCgmPdf).toHaveBeenCalledWith(expect.objectContaining({ id: 'saved-run' }), expect.any(AbortSignal), 'job');
  expect(screen.getByRole('progressbar', { name: '报告导出进度' })).toBeVisible();
  expect(screen.getByRole('button', { name: 'CPK Excel (.xlsx)' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Cgm Excel (.xlsx)' })).toBeDisabled();
  expect(fetchRunWorkbook).not.toHaveBeenCalled();
  const blob = new Blob(['%PDF']);
  finish({ blob, filename: 'actual.pdf' });
  await waitFor(() => expect(downloadBlob).toHaveBeenCalledWith(blob, 'actual.pdf'));
});

it('downloads CPK PDF and blocks concurrent CGM PDF and Excel requests', async () => {
  await preparedPage();
  let finish!: (value: { blob: Blob; filename: string }) => void;
  vi.mocked(fetchRunCpkPdf).mockReturnValue(new Promise(resolve => { finish = resolve; }));
  fireEvent.click(screen.getByRole('button', { name: 'CPK PDF (.pdf)' }));
  expect(fetchRunCpkPdf).toHaveBeenCalledWith(expect.objectContaining({ id: 'saved-run' }), expect.any(AbortSignal), 'job');
  expect(screen.getByRole('button', { name: 'Cgm PDF (.pdf)' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'CPK Excel (.xlsx)' })).toBeDisabled();
  expect(fetchRunWorkbook).not.toHaveBeenCalled();
  const blob = new Blob(['%PDF']);
  finish({ blob, filename: 'actual-cpk.pdf' });
  await waitFor(() => expect(downloadBlob).toHaveBeenCalledWith(blob, 'actual-cpk.pdf'));
});

it('blocks both workbooks when observations have not been saved', async () => {
  await preparedPage();
  fireEvent.click(screen.getByRole('button', { name: '编辑未保存备注' }));
  expect(screen.getByRole('button', { name: 'CPK Excel (.xlsx)' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Cgm Excel (.xlsx)' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Cgm PDF (.pdf)' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'CPK PDF (.pdf)' })).toBeDisabled();
  expect(fetchRunWorkbook).not.toHaveBeenCalled();
});

it('blocks both workbooks after changing the calculation range', async () => {
  await preparedPage();
  fireEvent.click(screen.getByRole('button', { name: '数据源与计算配置' }));
  fireEvent.click(screen.getByRole('button', { name: '修改计算范围' }));
  expect(screen.getByRole('button', { name: 'CPK Excel (.xlsx)' })).toBeDisabled();
  expect(screen.getByRole('button', { name: 'Cgm Excel (.xlsx)' })).toBeDisabled();
});
