import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AnalysisWorkspace } from './AnalysisWorkspace';
import { ObservationPanel } from './ObservationPanel';
import { demoChart, demoMatrix } from './demo';
import { demoRun, startingDemoSelections, type Diagnosis } from '../model';
import { downloadBlob, exportChartGroup } from './export';

vi.mock('./export', async original => ({ ...await original<typeof import('./export')>(), exportChartGroup: vi.fn(async () => {}), downloadBlob: vi.fn() }));
const selections = startingDemoSelections();
const run = { ...demoRun(selections), id: 'remarks-run', dataset_id: 'remarks-data' };
const automatic = '1. Check contact and setup on S4.\n2. Synthetic reference for the example measurement.';
const finalRemark = automatic + '\n3. Engineer confirmed S4.';
const diagnosis: Diagnosis = { current_remark: 'Current S4 has low Cg.', auto_remark: automatic, remark: automatic,
  category: 'CAT4', knowledge: { status: 'reference', id: 'EXAMPLE-1002', historical_remark: 'Synthetic reference for the example measurement.',
    source: { name: 'synthetic-test-fixture', sha256: 'source-sha' } },
  knowledge_hash: 'knowledge-hash', knowledge_version: '1.0', review_state: 'AUTO_REVIEW_REQUIRED' };

afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('shows scoped synthetic references and exports the exact server-saved Remark', async () => {
  const row = { ...run.rows[0], cgm: { ...run.rows[0].cgm!, diagnosis } };
  const savedDiagnosis = { ...diagnosis, remark: finalRemark, review_state: '已确认观察' };
  vi.stubGlobal('fetch', vi.fn(async (url: string, init?: RequestInit) => {
    let value: unknown;
    if (url.includes('/charts/')) value = demoChart(row, 'cgm', selections);
    else if (url.includes('/samples/')) { const chart = demoChart(row, 'cgm', selections);
      value = { points: chart.points, total: chart.total_points, offset: 0, limit: 100, context: chart.context }; }
    else if (url.includes('/chart-matrix')) value = demoMatrix(run, selections);
    else if (url.endsWith('/observations/preview')) value = { remark: finalRemark };
    else if (init?.method === 'POST') value = { notes: { ['cgm:' + row.key]: { key: row.key, module: 'cgm',
      tags: [], suggestion: '', custom_remark: 'Engineer confirmed S4.', remark: finalRemark,
      state: '已确认观察', revision: 1, saved_at: 'now', source: {}, selection: {}, rules_hash: 'rules' } },
      version: 1, remarks: { ['cgm:' + row.key]: savedDiagnosis }, saved: [row.key], skipped: [], undo_id: 'undo' };
    else value = { notes: {}, version: 0, remarks: { ['cgm:' + row.key]: diagnosis } };
    return { ok: true, json: async () => value };
  }));
  render(<AnalysisWorkspace run={run} rows={[row]} row={row} selections={selections} initialModule='cgm'
    demo={false} onSelect={vi.fn()} onClose={vi.fn()} />);
  const panel = within(await screen.findByRole('region', { name: '图形观察与结论记录' }));
  expect(within(panel.getByRole('region', { name: '项目诊断说明' })).getByText(
    (_, element) => element?.tagName === 'P' && element.textContent === automatic)).toBeVisible();
  expect(panel.getByText(/适用范围尚未确认/)).toBeInTheDocument();
  const user = userEvent.setup();
  await user.type(panel.getByRole('textbox', { name: '补充 Remark' }), 'Engineer confirmed S4.');
  await waitFor(() => expect(panel.getByLabelText('英文 Remark 预览').textContent).toBe(finalRemark));
  await user.click(panel.getByRole('button', { name: '保存当前观察' }));
  await waitFor(() => expect(panel.getByText('已确认观察', { selector: 'small' })).toBeInTheDocument());
  await user.click(screen.getByRole('button', { name: '导出完整范围图组' }));
  await waitFor(() => expect(exportChartGroup).toHaveBeenCalled());
  expect(vi.mocked(exportChartGroup).mock.calls.at(-1)![1]).toContain('Remark: ' + finalRemark);
  await user.click(screen.getByRole('button', { name: '导出图表上下文 JSON' }));
  const exported = vi.mocked(downloadBlob).mock.calls.at(-1)![0];
  const content = await new Promise<string>(resolve => { const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result)); reader.readAsText(exported); });
  expect(JSON.parse(content).diagnosis.remark).toBe(finalRemark);
});

it('allows saving an empty CPK Remark after clearing the last manual addition', async () => {
  const row = { ...run.rows[0], cpk: { ...run.rows[0].cpk!, diagnosis: { ...diagnosis, auto_remark: '', remark: '' } } };
  const onNotes = vi.fn();
  const fetchMock = vi.fn(async (url: string) => ({ ok: true, json: async () =>
    url.endsWith('/preview') ? { remark: '' } : { notes: {}, version: 2, remarks: {}, saved: [row.key], skipped: [], undo_id: 'undo' } }));
  vi.stubGlobal('fetch', fetchMock);
  render(<ObservationPanel base='/datasets/test/runs/run' row={row} rows={[row]} module='cpk' group={null}
    notes={{ version: 1, notes: { ['cpk:' + row.key]: { key: row.key, module: 'cpk', tags: [],
      suggestion: '', custom_remark: 'Remove this note', remark: '1. Remove this note', state: '待复核',
      revision: 1, saved_at: 'now', source: {}, selection: {}, rules_hash: 'rules' } } }}
    onNotes={onNotes} onDirty={vi.fn()} onNext={vi.fn()} demo={false} />);
  const user = userEvent.setup();
  await user.clear(screen.getByRole('textbox', { name: '补充 Remark' }));
  const save = screen.getByRole('button', { name: '保存当前观察' });
  await waitFor(() => expect(fetchMock).toHaveBeenCalledWith('/api/release-check/datasets/test/runs/run/observations/preview', expect.anything()));
  await waitFor(() => expect(save).toBeEnabled());
  await user.click(save);
  await waitFor(() => expect(onNotes).toHaveBeenCalled());
  expect(fetchMock).toHaveBeenCalledWith('/api/release-check/datasets/test/runs/run/observations', expect.objectContaining({ method: 'POST' }));
});
