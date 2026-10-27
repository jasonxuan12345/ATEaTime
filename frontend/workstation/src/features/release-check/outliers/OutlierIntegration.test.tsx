import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter } from 'react-router-dom';
import { ReleaseCheckPage } from '../ReleaseCheckPage';
import { reportFor, run, sourceA, sourceB } from './testFixtures';
import { OutlierEvidence } from './OutlierEvidence';
import { ReferenceChart } from './ReferenceChart';
import { allBaselines, allCandidates, integrateOutliers } from './model';

const ok = (data: unknown) => ({ ok: true, json: async () => data });
function setup(failure?: string) {
  let imports = 0;
  const fetch = vi.fn(async (url: string, init?: RequestInit) => {
    if (url.startsWith('/api/release-check/datasets?')) return ok([sourceA, sourceB][imports++]);
    if (url.startsWith('/api/release-check/runs?')) return ok(run);
    if (url.includes('/outliers?')) {
      if (failure && url.includes(sourceB.id)) return { ok: false, status: 422, json: async () => ({ detail: failure }) };
      return ok(reportFor(url.includes(sourceB.id) ? sourceB : sourceA));
    }
    if (url.includes('/items/')) return ok({ row: run.rows[0], details: {} });
    if (url.endsWith('/outlier-reports') && init?.method === 'PUT') return ok({ source_count: 2, version: 1 });
    if (init?.method === 'DELETE') return ok({});
    return { ok: false, status: 404, json: async () => ({ detail: 'no active job' }) };
  });
  vi.stubGlobal('fetch', fetch);
  render(<MemoryRouter><ReleaseCheckPage /></MemoryRouter>);
  return fetch;
}
async function importTwo() {
  await userEvent.setup().upload(screen.getByLabelText('选择 STDF 文件'), [new File(['mock'], sourceA.filename), new File(['mock'], sourceB.filename)]);
  await screen.findByRole('checkbox', { name: `${sourceB.filename} 用于 outlier` });
  fireEvent.click(within(screen.getByRole('dialog')).getByRole('button',{name:'Outlier'}));
  fireEvent.click(screen.getByRole('checkbox', { name: `${sourceB.filename} 用于 outlier` }));
}
afterEach(() => { vi.restoreAllMocks(); vi.unstubAllGlobals(); });

it('calculates independent Outlier sources and displays the four charts in one page',async()=>{
const fetch=setup();await importTwo();const user=userEvent.setup();fireEvent.click(screen.getByText(/选择测试记录/));const records=screen.getByText(/选择测试记录/).closest('details')!;fireEvent.click(within(records).getByRole('button',{name:'下一页'}));fireEvent.click(screen.getByRole('checkbox',{name:'Outlier 记录 50'}));fireEvent.click(screen.getByRole('button',{name:'计算 Release 检查'}));await screen.findByText('计算完成',{exact:true});
const calls=fetch.mock.calls.filter(([url])=>url.includes('/outliers?'));expect(calls).toHaveLength(2);const a=JSON.parse(calls[0][1]!.body as string),b=JSON.parse(calls[1][1]!.body as string);expect(a.part_indices).toBeNull();expect(b.part_indices).not.toContain(0);expect(a.sites).toEqual([1,2]);expect(b.sites).toEqual([3]);expect(a.sigma_multiplier).toBe(6);expect(b.minimum_samples_per_site).toBe(30);
await user.click(screen.getByRole('button',{name:'Outlier'}));expect(screen.getByRole('region',{name:'Outlier 测项分析'})).toBeVisible();expect(screen.getByRole('img',{name:'当前记录各异常测项的偏离程度（稳健σ）'})).toBeVisible();expect(screen.getByRole('img',{name:'异常候选记录与测项异常分布表'})).toBeVisible();expect(screen.getByRole('img',{name:'Wafer 异常候选记录坐标位置图'})).toBeVisible();expect(screen.getByRole('button',{name:'Outlier'})).toHaveTextContent('1');
await user.click(screen.getByRole('button',{name:'按异常候选记录'}));const list=within(screen.getByRole('region',{name:'测项列表'}));expect(list.getAllByRole('button',{name:'记录 #41'})).toHaveLength(2);await user.click(list.getAllByRole('button',{name:'记录 #41'})[1]);expect(screen.getByRole('combobox',{name:'Outlier wafer'})).toHaveValue(JSON.stringify([sourceB.id,'L1','W1']));
},15000);

it('retains successful sources and exposes a failed file in configuration',async()=>{setup('源文件会话已过期');await importTwo();fireEvent.click(screen.getByRole('button',{name:'计算 Release 检查'}));await screen.findByText('部分完成',{exact:true});expect(screen.getByRole('alert')).toHaveTextContent('beta.stdf');expect(screen.getByRole('dialog')).toBeVisible();expect(screen.getAllByText('源文件会话已过期').length).toBeGreaterThan(0);expect(screen.getByRole('region',{name:'当前测项分析'})).toHaveTextContent('来源计算未完成');},15000);

it('allows disabling Outlier and invalidates exports after Outlier settings change', async () => {
  const fetch = setup(); await importTwo();
  fireEvent.click(screen.getByRole('checkbox', { name: `${sourceB.filename} 用于 outlier` }));
  fireEvent.click(screen.getByRole('button', { name: '计算 Release 检查' }));
  await screen.findByText('计算完成', { exact: true });
  expect(fetch.mock.calls.filter(([url]) => url.includes('/outliers?'))).toHaveLength(1);
  expect(screen.getByRole('button', { name: '导出报告 JSON' })).toBeEnabled();
  fireEvent.click(screen.getByRole('button',{name:'数据源与计算配置'}));
  fireEvent.change(screen.getByRole('spinbutton', { name: 'Outlier 偏离阈值' }), { target: { value: '8' } });
  expect(screen.getByRole('button', { name: '导出报告 JSON' })).toBeDisabled();
  fireEvent.click(screen.getByRole('checkbox', { name: `${sourceA.filename} 用于 outlier` }));
  expect(screen.getByRole('button', { name: '重新计算' })).toBeEnabled();
}, 15000);
it('shows source, trace and exact candidate interval without a fabricated population distribution', () => {
  const reports = [reportFor()], candidates = allCandidates(reports), cell = integrateOutliers(run.rows, reports, [], false)[0].outlier;
  render(<OutlierEvidence cell={cell} chip={candidates[0]} baselines={allBaselines(reports)} onSelect={vi.fn()} onExpand={vi.fn()} />);
  const evidence = screen.getByRole('region', { name: '异常候选记录详情' });
  expect(evidence).toHaveTextContent('8.2 倍'); expect(evidence).toHaveTextContent('该记录在有效规格内');
  fireEvent.click(within(evidence).getByText('定位信息'));
  expect(evidence).toHaveTextContent('419'); expect(evidence).toHaveTextContent('410');
  expect(screen.getAllByRole('img', { name: '实测值、同组参考范围和异常候选记录' })).toHaveLength(2);
});
it('stops the remaining sources after cancellation and records unevaluated sources', async () => {
  const fetch = setup(); await importTwo();
  const previous = fetch.getMockImplementation()!;
  fetch.mockImplementation((url, init) => {
    if (url.includes('/source-a/outliers?')) return new Promise((_resolve, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Cancelled', 'AbortError')));
    });
    return previous(url, init);
  });
  fireEvent.click(screen.getByRole('button', { name: '计算 Release 检查' }));
  await screen.findByText(/正在计算 Outlier 1 \/ 2/);
  await act(async () => { fireEvent.click(screen.getByRole('button', { name: '取消操作' })); });
  await screen.findByText('部分完成', { exact: true });
  expect(fetch.mock.calls.filter(([url]) => url.includes('/outliers?'))).toHaveLength(1);
  expect(screen.getByRole('alert')).toHaveTextContent('尚未执行');
}, 15000);
it('removes the selected Outlier source and disables stale exports', async () => {
  setup(); await importTwo();
  fireEvent.click(screen.getByRole('button', { name: '计算 Release 检查' }));
  await screen.findByText('计算完成', { exact: true });
  fireEvent.click(screen.getByRole('button',{name:'数据源与计算配置'}));
  fireEvent.click(screen.getByRole('button', { name: `移除文件 ${sourceB.filename}` }));
  expect(screen.getByRole('region',{name:'当前测项分析'})).toHaveTextContent('1 条异常候选记录');
  expect(screen.queryByRole('checkbox', { name: `${sourceB.filename} 用于 outlier` })).not.toBeInTheDocument();
  expect(screen.getByRole('button', { name: '导出报告 JSON' })).toBeDisabled();
}, 15000);
it('switches from an empty PASS filter to Outlier candidate chips',async()=>{setup();await importTwo();fireEvent.click(screen.getByRole('button',{name:'计算 Release 检查'}));await screen.findByText('计算完成',{exact:true});fireEvent.click(screen.getByRole('button',{name:'通过'}));expect(screen.getByText(/没有符合条件的测项/)).toBeVisible();fireEvent.click(screen.getByRole('button',{name:'Outlier'}));fireEvent.click(screen.getByRole('button',{name:'按异常候选记录'}));const list=within(screen.getByRole('region',{name:'测项列表'}));fireEvent.click(list.getAllByRole('button',{name:'记录 #41'})[1]);expect(screen.getByRole('region',{name:'Outlier 测项分析'})).toHaveTextContent(sourceB.filename);},15000);

it('automatically brings the selected candidate baseline into the reference chart page', () => {
  const source = reportFor(), candidates = allCandidates([source]);
  const baselines = Array.from({ length: 15 }, (_, index) => ({ ...allBaselines([source])[0], baseline_id: `b-${index}`, site: index + 1 }));
  const far = { ...candidates[0], hits: [{ ...candidates[0].hits[0], baseline_id: 'b-14' }] };
  render(<ReferenceChart baselines={baselines} candidates={[far]} selectedKey={far.key} onSelect={vi.fn()} compact />);
  expect(screen.getByRole('button', { name: /选择记录 41/ })).toBeVisible();
  expect(screen.getByText('5 / 5')).toBeVisible();
});
it('uses the current test as the initial explanation and redraws the selected hit interval', () => {
  const source = reportFor();
  source.report.baselines.push({ ...source.report.baselines[0], baseline_id: 'other', group_id: 'other', test_name: 'OTHER_TEST', column: 'OTHER', median: 2, lower_limit: 1.88, upper_limit: 2.12 });
  const otherHit = { ...source.report.chips[0].hits[0], baseline_id: 'other', group_id: 'other', test_name: 'OTHER_TEST', column: 'OTHER', value: 2.2, median: 2, score: 10 };
  source.report.chips[0].hits.unshift(otherHit); source.report.chips[0].hit_count = 2;
  const candidates = allCandidates([source]), baselines = allBaselines([source]);
  const cell = integrateOutliers(run.rows, [source], [], false)[0].outlier;
  render(<OutlierEvidence cell={cell} chip={candidates[0]} baselines={baselines} onSelect={vi.fn()} onExpand={vi.fn()} />);
  const select = screen.getByRole('combobox', { name: '当前异常候选记录检出异常的测项' });
  expect(select).toHaveValue('baseline-1:410');
  fireEvent.change(select, { target: { value: 'other:410' } });
  const evidence = screen.getByRole('region', { name: '异常候选记录详情' });
  expect(evidence).toHaveTextContent('2.2000');
  expect(within(evidence).getByRole('button', { name: /选择记录 41/ })).toHaveAttribute('cx');
});
