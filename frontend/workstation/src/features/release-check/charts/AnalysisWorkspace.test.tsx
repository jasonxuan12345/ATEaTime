import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { AnalysisWorkspace } from './AnalysisWorkspace';
import { demoChart, demoMatrix } from './demo';
import { demoRun, startingDemoSelections } from '../model';
import { exportChartGroup } from './export';

vi.mock('./export', async importOriginal => ({ ...await importOriginal<typeof import('./export')>(), exportChartGroup: vi.fn(async () => {}) }));

const selections = startingDemoSelections();
const run = { ...demoRun(selections), id: 'real-run', dataset_id: 'real-data' };
const row = run.rows[0];
const matrix = demoMatrix(run, selections);
function mockApi() {
  const calls: { url: string; body: any }[] = [];
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    const body = init?.body ? JSON.parse(String(init.body)) : {};
    calls.push({ url, body });
    let value: unknown;
    if (url.includes('/charts/')) {
      const module = url.includes('/charts/cpk') ? 'cpk' : url.includes('/charts/cgm') ? 'cgm' : 'mmsa';
      const chart = demoChart(row, module, selections);
      value = module === 'mmsa' ? { ...chart, sites: chart.sites.map(site => ({ ...site, cg: null, cg_status: 'NOT_CALCULABLE' })) } : chart;
    } else if (url.includes('/samples/')) {
      const data = demoChart(row, 'cgm', selections);
      value = { points: data.points.slice(0, 100), total: data.total_points, offset: 0, limit: 100, context: data.context };
    } else if (url.includes('/chart-matrix')) value = matrix;
    else if (url.endsWith('/observations/preview')) value = { remark: 'first/last sequence spikes observed. Check settling time' };
    else if (init?.method === 'POST') {
      const note = { key: row.key, module: body.module, tags: body.tags, suggestion: body.suggestion, custom_remark: body.remark,
        remark: 'first/last sequence spikes observed. Check settling time', state: body.state, revision: 1, saved_at: 'now', source: {}, selection: {}, rules_hash: 'real' };
      value = { notes: { [body.module + ':' + row.key]: note }, version: 1, saved: [row.key], skipped: [], undo_id: 'undo1' };
    } else value = { notes: {}, version: 0 };
    return { ok: true, json: async () => value };
  });
  vi.stubGlobal('fetch', fetch);
  return { fetch, calls };
}
function mount() {
  const onSelect = vi.fn(), onClose = vi.fn();
  const result = render(<AnalysisWorkspace run={run} rows={run.rows} row={row} selections={selections} initialModule='cgm'
    demo={false} onSelect={onSelect} onClose={onClose} />);
  return { ...result, onSelect, onClose };
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it.each(['cpk', 'cgm', 'mmsa'] as const)('places site sequence above the main %s distribution in the page', async module => {
  render(<AnalysisWorkspace run={run} rows={run.rows} row={row} selections={selections} initialModule={module}
    demo={true} embedded onSelect={vi.fn()} onClose={vi.fn()} />);
  const sequence = await screen.findByRole('region', { name: '分 site 测试次序图' });
  const main = await screen.findByRole('img', { name: module === 'cpk' ? '直方图 · 核密度' : module === 'cgm' ? '各 site Cg' : '分 site 小提琴' });
  expect(sequence.compareDocumentPosition(main) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  expect(screen.getAllByRole('img', { name: '分 site 测试次序' })).toHaveLength(1);
  expect(sequence).toHaveTextContent('黑色短线 Mean');
});

it('integrates rule-driven chart pairs, manual swaps and export of the visible pair', async () => {
  mockApi(); const user = userEvent.setup();
  const current = { ...row, mmsa: { ...row.mmsa!, rule_id: 'MMSA-001' } };
  render(<AnalysisWorkspace run={run} rows={run.rows} row={current} selections={selections} initialModule='mmsa' demo={false} onSelect={vi.fn()} onClose={vi.fn()} />);
  await screen.findByRole('img', { name: '测试次序' });
  expect(within(screen.getByRole('region', { name: '辅图区域' })).getByRole('img', { name: '各 site Std' })).toBeVisible();
  await user.selectOptions(screen.getByRole('combobox', { name: '选择辅图' }), 'histogram');
  expect(screen.getByRole('spinbutton', { name: '直方图分箱数' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: '交换主辅图' }));
  expect(within(screen.getByRole('region', { name: '主图区域' })).getByRole('img', { name: '直方图' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: '导出完整范围图组' }));
  await waitFor(() => expect(exportChartGroup).toHaveBeenCalled());
  const options = vi.mocked(exportChartGroup).mock.calls.at(-1)![2] as any[];
  expect(options).toHaveLength(2);
  expect(options[0].series[0].type).toBe('bar');
  expect(options[1].series[0].type).toBe('scatter');
  expect(options.flatMap(option => option.series).some(series => series.type === 'boxplot')).toBe(false);
  await user.click(screen.getByRole('button', { name: '恢复推荐图组' }));
  expect(screen.getByRole('combobox', { name: '选择主图' })).toHaveValue('sequence');
  expect(screen.getByRole('combobox', { name: '选择辅图' })).toHaveValue('cg');
  expect(screen.getByRole('button', { name: '辅图切换 Cg' })).toBeDisabled();
});

it('starts variance review with Std and retains observation draft through chart-pair changes', async () => {
  mockApi(); const user = userEvent.setup();
  const current = { ...row, mmsa: { ...row.mmsa!, rule_id: 'MMSA-004' } };
  render(<AnalysisWorkspace run={run} rows={run.rows} row={current} selections={selections} initialModule='mmsa' demo={false} onSelect={vi.fn()} onClose={vi.fn()} />);
  await screen.findByRole('img', { name: '各 site Std' });
  await user.type(screen.getByRole('textbox', { name: '补充 Remark' }), 'Keep review draft');
  await user.click(screen.getByRole('button', { name: '交换主辅图' }));
  expect(within(screen.getByRole('region', { name: '辅图区域' })).getByRole('img', { name: '各 site Std' })).toBeVisible();
  expect(screen.getByRole('button', { name: '辅图切换 Cg' })).toBeDisabled();
  await user.click(screen.getByRole('button', { name: '恢复推荐图组' }));
  expect(screen.getByRole('textbox', { name: '补充 Remark' })).toHaveValue('Keep review draft');
  expect(screen.getByRole('img', { name: '各 site Std' })).toBeVisible();
});

it('loads module-specific chart evidence and renders default Cg plus box with source badges', async () => {
  const { calls } = mockApi(); mount();
  await screen.findByRole('img', { name: '各 site Cg' });
  expect(screen.getByRole('img', { name: '分 site 箱线图＋均值点' })).toBeVisible();
  expect(screen.getAllByText('DEMO_synthetic_points.stdf').length).toBeGreaterThan(0);
  expect(calls.some(call => call.url.includes('/charts/cgm'))).toBe(true);
  expect(calls.find(call => call.url.includes('/chart-matrix'))?.body.keys).toEqual(run.rows.map(r => r.key));
  await userEvent.setup().click(screen.getByRole('button', { name: '切换 Std' }));
  expect(screen.getByRole('img', { name: '各 site Std' })).toBeVisible();
});

it('switches to sequence and wafer views, preserves the report and supports keyboard-accessible selection', async () => {
  mockApi(); mount(); const user = userEvent.setup();
  await screen.findByRole('img', { name: '各 site Cg' });
  await user.click(screen.getByRole('button', { name: '测试次序' }));
  expect(screen.getByRole('img', { name: '测试次序' })).toBeVisible();
  await user.click(screen.getByRole('button', { name: 'Wafer Map' }));
  expect(screen.getByRole('combobox', { name: '选择晶圆' })).toBeVisible();
  expect(screen.getByRole('img', { name: 'Wafer Map' })).toBeVisible();
  await user.click(screen.getAllByRole('button', { name: '放大' })[0]);
  expect(screen.getByRole('button', { name: '退出放大' })).toBeVisible();
  fireEvent.keyDown(window, { key: 'Escape' });
  expect(screen.queryByRole('button', { name: '退出放大' })).not.toBeInTheDocument();
});

it('saves confirmed true directly and advances without a confirmation checkbox', async () => {
  const { calls } = mockApi(); const { onSelect } = mount(); const user = userEvent.setup();
  await screen.findByRole('img', { name: '各 site Cg' });
  const panel = within(screen.getByRole('region', { name: '图形观察与结论记录' }));
  expect(panel.queryByRole('checkbox',{name:/我已看图确认/})).not.toBeInTheDocument();
  await user.click(panel.getByRole('checkbox', { name: '首尾跳点' }));
  await user.type(panel.getByRole('textbox', { name: '建议检查' }), 'Check settling time');
  await waitFor(() => expect(panel.getByLabelText('英文 Remark 预览')).toHaveTextContent('sequence spikes observed'));
  await user.click(panel.getByRole('button', { name: '确认并保存，下一项' }));
  await waitFor(() => expect(calls.some(call => call.url.endsWith('/observations') && call.body.confirmed)).toBe(true));
  const posted = calls.find(call => call.body.confirmed)!.body;
  expect(posted).toMatchObject({ keys: [row.key], module: 'cgm', tags: ['end_spikes'], version: 0 });
  expect(onSelect).toHaveBeenCalledWith(run.rows[1].key);
});

it('does not discard unsaved observations when leaving is cancelled', async () => {
  mockApi(); const { onClose } = mount(); const user = userEvent.setup();
  await screen.findByRole('img', { name: '各 site Cg' });
  await user.click(screen.getByRole('checkbox', { name: '首尾跳点' }));
  vi.spyOn(window, 'confirm').mockReturnValue(false);
  await user.click(screen.getByRole('button', { name: /返回结果列表/ }));
  expect(onClose).not.toHaveBeenCalled();
  expect(screen.getByRole('checkbox', { name: '首尾跳点' })).toBeChecked();
});

it('keeps an unsaved observation when histogram bin settings reload the chart', async () => {
  const { calls } = mockApi(); mount(); const user = userEvent.setup();
  await screen.findByRole('img', { name: '各 site Cg' });
  await user.click(screen.getByRole('button', { name: '直方图' }));
  await user.type(screen.getByRole('textbox', { name: '补充 Remark' }), 'Keep this draft');
  fireEvent.change(screen.getByRole('spinbutton', { name: '直方图分箱数' }), { target: { value: '12' } });
  await waitFor(() => expect(calls.some(call => call.url.includes('bins=12'))).toBe(true));
  await screen.findByRole('img', { name: '直方图' });
  expect(screen.getByRole('textbox', { name: '补充 Remark' })).toHaveValue('Keep this draft');
});

it('opens CGM evidence rather than selecting CPK samples from the CGM family matrix', async () => {
  const { calls } = mockApi(); mount(); const user = userEvent.setup();
  await screen.findByRole('img', { name: '各 site Cg' });
  await user.click(screen.getByRole('button', { name: 'bin1 CPK' }));
  await screen.findByRole('img', { name: '直方图' });
  await user.click(screen.getAllByRole('button', { name: '测项组概览' })[0]);
  const matrixPanel = within(screen.getByRole('region', { name: '测项 × site Cg 热力图' }));
  await user.click(matrixPanel.getAllByRole('button', { name: /Cg / })[0]);
  await screen.findByRole('img', { name: '各 site Cg' });
  await waitFor(() => expect(calls.filter(call => call.url.includes('/samples/')).at(-1)?.url).toContain('/samples/cgm'));
  expect(screen.getByRole('button', { name: 'CGM' })).toHaveAttribute('aria-pressed', 'true');
});

it('reserves both chart areas while chart data is loading', async () => {
  const { fetch } = mockApi();
  fetch.mockImplementationOnce(() => new Promise<never>(() => undefined));
  mount();
  expect(screen.getByRole('region', { name: '主图区域' })).toBeVisible();
  expect(screen.getByRole('region', { name: '辅图区域' })).toBeVisible();
  expect(within(screen.getByRole('region', { name: '主图区域' })).getByRole('status')).toHaveTextContent('正在读取');
  expect(screen.queryByRole('img', { name: '各 site Cg' })).not.toBeInTheDocument();
});

it('places the family matrix in the auxiliary area and keeps the primary area unassigned until a row is chosen', async () => {
  mockApi(); mount(); const user = userEvent.setup();
  await screen.findByRole('img', { name: '各 site Cg' });
  await user.click(screen.getAllByRole('button', { name: '测项组概览' })[0]);
  const primary = within(screen.getByRole('region', { name: '主图区域' }));
  const auxiliary = within(screen.getByRole('region', { name: '辅图区域' }));
  expect(primary.getByText('请选择测项组中的一个测项')).toBeVisible();
  expect(primary.queryByRole('img')).not.toBeInTheDocument();
  await auxiliary.findByRole('region', { name: '测项 × site Cg 热力图' });
  await user.click(auxiliary.getAllByRole('button', { name: /Cg / })[0]);
  expect(await primary.findByRole('img', { name: '各 site Cg' })).toBeVisible();
  expect(auxiliary.getByRole('region', { name: '测项 × site Cg 热力图' })).toBeVisible();
  expect(screen.queryByRole('img', { name: '分 site 箱线图＋均值点' })).not.toBeInTheDocument();
  await user.click(screen.getByRole('button', { name: '导出完整单项图组' }));
  const exported = vi.mocked(exportChartGroup).mock.calls.at(-1)!;
  expect(exported[2]).toHaveLength(1);
  expect(exported[1].join(' ')).toContain('测项组 Cg 表另导出 CSV');
});

it('shows rule then family navigation and uses the analysis search scope for next-group controls', async () => {
  mockApi(); mount(); const user = userEvent.setup();
  await screen.findByRole('img', { name: '各 site Cg' });
  const nav = screen.getByRole('complementary', { name: '异常项导航' });
  await user.type(within(nav).getByRole('searchbox'), row.name);
  const branch = within(nav).getByRole('button', { name: '测项组概览' }).closest('details')!;
  expect(branch.parentElement?.closest('details')?.querySelector('summary')).toHaveTextContent(matrix.groups[0].rule);
  await user.click(within(nav).getByRole('button', { name: '测项组概览' }));
  expect(screen.getByRole('button', { name: '下一个组' })).toBeDisabled();
});

it('does not carry a CPK site selection into the CGM family matrix before a row is chosen', async () => {
  mockApi(); mount(); const user = userEvent.setup();
  await screen.findByRole('img', { name: '各 site Cg' });
  await user.click(screen.getByRole('button', { name: 'bin1 CPK' }));
  await screen.findByRole('img', { name: '直方图' });
  await user.click(screen.getByText('各 site 统计与完整上下文'));
  const stats = screen.getByText('各 site 统计与完整上下文').closest('details')!;
  await user.click(within(stats).getAllByRole('button')[0]);
  await user.click(screen.getAllByRole('button', { name: '测项组概览' })[0]);
  const heatmap = await screen.findByRole('region', { name: '测项 × site Cg 热力图' });
  for (const header of within(heatmap).getAllByRole('columnheader').slice(1)) {
    const button = within(header).queryByRole('button');
    if (button) expect(button).toHaveAttribute('aria-pressed', 'false');
  }
});
