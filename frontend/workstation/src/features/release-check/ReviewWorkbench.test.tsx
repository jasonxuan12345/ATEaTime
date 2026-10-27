import { fireEvent, render, screen, within } from '@testing-library/react';
import { ReviewWorkbench } from './ReviewWorkbench';
import { demoRun, startingDemoSelections, type Run } from './model';

vi.mock('./charts/AnalysisWorkspace', () => ({ AnalysisWorkspace: () => null }));
vi.mock('./charts/OverviewThumbnail', () => ({ OverviewThumbnail: () => null }));
vi.mock('./outliers/OutlierAnalysis', () => ({ OutlierAnalysis: () => null }));
vi.mock('./outliers/OutlierRemark', () => ({ OutlierRemark: () => null, exportOutlierNotes: vi.fn() }));

function mount(run: Run) {
  const onSelect = vi.fn();
  render(<ReviewWorkbench run={run} rows={run.rows} selections={startingDemoSelections()} demo={false}
    stale={false} selectedKey={run.rows[0].key} onSelect={onSelect} onDirty={vi.fn()}
    reports={[]} issues={[]} detail={null} onOutlierNotes={vi.fn()} onTab={vi.fn()} />);
  fireEvent.click(screen.getByRole('button', { name: 'Bin1 CPK' }));
  fireEvent.click(screen.getByRole('button', { name: '全部' }));
  return onSelect;
}

it('shows every CPK row statistics before opening its charts', () => {
  const run = demoRun(startingDemoSelections());
  run.rows = run.rows.slice(0, 2);
  run.rows[0].cpk = { ...run.rows[0].cpk!, mean: 10.25, std: .125, cp: 3.25 };
  run.rows[1].cpk = { ...run.rows[1].cpk!, mean: 20.75, std: .25, cp: 4.5 };
  const onSelect = mount(run);
  for (const [index, meanStd, cp] of [[0, '10.2500 / 0.1250', '3.25'], [1, '20.7500 / 0.2500', '4.50']] as const) {
    const row = screen.getByRole('button', { name: run.rows[index].name }).closest('tr')!;
    expect(within(row).getByRole('cell', { name: meanStd })).toBeVisible();
    expect(within(row).getByRole('cell', { name: cp })).toBeVisible();
  }
  expect(onSelect).not.toHaveBeenCalledWith(run.rows[1].key);
});

it('distinguishes zero statistics from unavailable values without loading charts', () => {
  const run = demoRun(startingDemoSelections());
  run.rows = run.rows.slice(0, 2);
  run.rows[0].cpk = { ...run.rows[0].cpk!, mean: 0, std: 0, cp: null };
  run.rows[1].cpk = { ...run.rows[1].cpk!, mean: null, std: null, cp: null };
  mount(run);
  const zero = screen.getByRole('button', { name: run.rows[0].name }).closest('tr')!;
  const missing = screen.getByRole('button', { name: run.rows[1].name }).closest('tr')!;
  expect(within(zero).getByRole('cell', { name: '0.0000 / 0.0000' })).toBeVisible();
  expect(within(missing).getByRole('cell', { name: '— / —' })).toBeVisible();
  expect(within(zero).getAllByRole('cell')[3]).toHaveTextContent('—');
  expect(within(missing).getAllByRole('cell')[3]).toHaveTextContent('—');
});

it('shows Site Diff statistics for all rows before their details are opened', () => {
  const run = demoRun(startingDemoSelections());
  run.rows = run.rows.slice(0, 2);
  run.rows[0].mmsa = { ...run.rows[0].mmsa!, shift_spec_pct: 5.25, var_ratio: 1.4 };
  run.rows[1].mmsa = { ...run.rows[1].mmsa!, shift_spec_pct: 10, var_ratio: 2 };
  const onSelect = mount(run);
  fireEvent.click(screen.getByRole('button', { name: 'Site Diff' }));
  for (const [index, spec, variance] of [[0, '5.25%', '1.40×'], [1, '10.00%', '2.00×']] as const) {
    const row = screen.getByRole('button', { name: run.rows[index].name }).closest('tr')!;
    expect(within(row).getByRole('cell', { name: spec })).toBeVisible();
    expect(within(row).getByRole('cell', { name: variance })).toBeVisible();
  }
  expect(onSelect).not.toHaveBeenCalledWith(run.rows[1].key);
});

it('renders infinite minimum Cg in the result table', () => {
  const run = demoRun(startingDemoSelections());
  run.rows = run.rows.slice(0, 1);
  run.rows[0].cgm = { ...run.rows[0].cgm!, value: 'Infinity', status: 'PASS' };
  mount(run);
  fireEvent.click(screen.getByRole('button', { name: 'CGM' }));
  const row = screen.getByRole('button', { name: run.rows[0].name }).closest('tr')!;
  expect(within(row).getByRole('cell', { name: '∞' })).toBeVisible();
});

it('shows worst sites, verdicts and missing sites for unopened CGM rows', () => {
  const run = demoRun(startingDemoSelections());
  run.rows = run.rows.slice(0, 2);
  for (const row of run.rows) row.cgm = { ...row.cgm!, value: .8, status: 'FAIL', override: false, sites: [
    { site: 0, n: 3, cg: 'Infinity', status: 'CALCULATED' },
    { site: 1, n: 3, cg: 1.2, status: 'CALCULATED' },
    { site: 2, n: 3, cg: .8, status: 'CALCULATED' },
    { site: 3, n: 3, cg: .9, status: 'CALCULATED' },
    { site: 4, n: 0, cg: null, status: 'INSUFFICIENT_DATA' },
    { site: 5, n: 1, cg: null, status: 'INSUFFICIENT_DATA' },
  ] };
  const onSelect = mount(run);
  fireEvent.click(screen.getByRole('button', { name: 'CGM' }));
  for (const item of run.rows) {
    const row = screen.getByRole('button', { name: item.name }).closest('tr')!;
    expect(within(row).getByRole('cell', { name: '0.80' })).toBeVisible();
    expect(within(row).getByText('S2、S3、S1')).toBeVisible();
    expect(within(row).getByText('未达标')).toBeVisible();
    expect(within(row).getByText('Site 4 无数据')).toHaveClass(/missingSite/);
    expect(within(row).getByText('Site 5 样本不足')).toBeVisible();
  }
  expect(onSelect).not.toHaveBeenCalledWith(run.rows[1].key);
});

it('keeps infinite sites in stable order and shows no invented worst sites without usable data', () => {
  const run = demoRun(startingDemoSelections());
  run.rows = run.rows.slice(0, 2);
  run.rows[0].cgm = { ...run.rows[0].cgm!, value: 'Infinity', status: 'PASS', override: false,
    sites: [3, 0, 2, 1].map(site => ({ site, n: 3, cg: 'Infinity', status: 'CALCULATED' })) };
  run.rows[1].cgm = { ...run.rows[1].cgm!, value: null, status: 'NOT_EVALUATED', override: false,
    sites: [{ site: 4, n: 0, cg: null, status: 'INSUFFICIENT_DATA' }] };
  mount(run);
  fireEvent.click(screen.getByRole('button', { name: 'CGM' }));
  const good = screen.getByRole('button', { name: run.rows[0].name }).closest('tr')!;
  const missing = screen.getByRole('button', { name: run.rows[1].name }).closest('tr')!;
  expect(within(good).getByText('S0、S1、S2')).toBeVisible();
  expect(within(good).getByText('通过')).toBeVisible();
  expect(within(missing).getByText('尚未评估')).toBeVisible();
  expect(within(missing).getByText('Site 4 无数据')).toBeVisible();
  expect(within(missing).getAllByRole('cell')[1]).toHaveTextContent('—');
});

it('sorts Site Diff columns descending first and ascending next, with missing values last', () => {
  const run = demoRun(startingDemoSelections()), base = run.rows[0];
  const values = [2, 10, 0, -1, null, 'Infinity'] as const;
  run.rows = values.map((value, index) => ({ ...base, key: `site-${index}`, name: `Site item ${index}`, number: index === 4 ? null : index,
    mmsa: { ...base.mmsa!, value, shift_spec_pct: index === 4 ? null : 10 - index,
      var_ratio: index === 4 ? null : index * 2, rule_id: index === 4 ? null : `RULE_${index}` } }));
  const original = [...run.rows];
  const onSelect = mount(run);
  fireEvent.click(screen.getByRole('button', { name: 'Site Diff' }));
  const order = () => screen.getAllByRole('row').slice(1).map(row => within(row).getByRole('button').getAttribute('aria-label'));
  const cases: [string, number[], number[]][] = [
    ['Site 均值差（σ）', [5, 1, 0, 2, 3, 4], [3, 2, 0, 1, 5, 4]],
    ['Site 均值差占规格范围比例', [0, 1, 2, 3, 5, 4], [5, 3, 2, 1, 0, 4]],
    ['Site 标准差比', [5, 3, 2, 1, 0, 4], [0, 1, 2, 3, 5, 4]],
    ['规则号', [5, 3, 2, 1, 0, 4], [0, 1, 2, 3, 5, 4]],
    ['Test ID / 名称', [5, 3, 2, 1, 0, 4], [0, 1, 2, 3, 5, 4]],
  ];
  for (const [label, descending, ascending] of cases) {
    const header = screen.getByRole('button', { name: `按 ${label} 排序` });
    fireEvent.click(header);
    expect(order()).toEqual(descending.map(i => `Site item ${i}`));
    expect(header.closest('th')).toHaveAttribute('aria-sort', 'descending');
    fireEvent.click(header);
    expect(order()).toEqual(ascending.map(i => `Site item ${i}`));
    expect(header.closest('th')).toHaveAttribute('aria-sort', 'ascending');
  }
  const observation = screen.getByRole('button', { name: '按 备注复核状态 排序' });
  fireEvent.click(observation);
  expect(observation.closest('th')).toHaveAttribute('aria-sort', 'descending');
  fireEvent.click(observation);
  expect(observation.closest('th')).toHaveAttribute('aria-sort', 'ascending');
  expect(run.rows).toEqual(original);
  expect(onSelect).not.toHaveBeenCalled();
});

it('sorts Site Diff across pages, preserves its sort across tabs and applies search before pagination', () => {
  const run = demoRun(startingDemoSelections()), base = run.rows[0];
  run.rows = Array.from({ length: 45 }, (_, index) => ({ ...base, key: `site-page-${index}`, name: `Site item ${index}`,
    mmsa: { ...base.mmsa!, value: index } }));
  mount(run);
  fireEvent.click(screen.getByRole('button', { name: 'Site Diff' }));
  const header = screen.getByRole('button', { name: '按 Site 均值差（σ） 排序' });
  const names = () => screen.getAllByRole('row').slice(1).map(row => within(row).getByRole('button').getAttribute('aria-label'));
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  fireEvent.click(header);
  expect(names()[0]).toBe('Site item 44');
  expect(names()).toHaveLength(40);
  expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  expect(names()[0]).toBe('Site item 4');
  fireEvent.click(header);
  expect(names()[0]).toBe('Site item 0');
  expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();
  fireEvent.click(screen.getByRole('button', { name: 'Bin1 CPK' }));
  fireEvent.click(screen.getByRole('button', { name: 'Site Diff' }));
  expect(screen.getByRole('button', { name: '按 Site 均值差（σ） 排序' }).closest('th')).toHaveAttribute('aria-sort', 'ascending');
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索测项结果' }), { target: { value: 'Site item 4' } });
  expect(names()).toEqual([4, 40, 41, 42, 43, 44].map(i => `Site item ${i}`));
});

it('sorts CPK and min Cg descending first, then ascending, with zero and missing values handled numerically', () => {
  const run = demoRun(startingDemoSelections());
  const base = run.rows[0];
  const values = [2, 10, 0, -1, null, 'Infinity'] as const;
  run.rows = values.map((value, index) => ({ ...base, key: `sort-${index}`, name: `Sort ${index}`,
    cpk: { ...base.cpk!, value }, cgm: { ...base.cgm!, value } }));
  const original = [...run.rows];
  mount(run);
  const order = () => screen.getAllByRole('row').slice(1).map(row => within(row).getByRole('button').getAttribute('aria-label'));
  fireEvent.click(screen.getByRole('button', { name: '按 Bin1 CPK 排序' }));
  expect(order()).toEqual([5, 1, 0, 2, 3, 4].map(i => `Sort ${i}`));
  expect(screen.getByRole('button', { name: '按 Bin1 CPK 排序' }).closest('th')).toHaveAttribute('aria-sort', 'descending');
  fireEvent.click(screen.getByRole('button', { name: '按 Bin1 CPK 排序' }));
  expect(order()).toEqual([3, 2, 0, 1, 5, 4].map(i => `Sort ${i}`));
  fireEvent.click(screen.getByRole('button', { name: 'CGM' }));
  fireEvent.click(screen.getByRole('button', { name: '按 min Cg 排序' }));
  expect(order()).toEqual([5, 1, 0, 2, 3, 4].map(i => `Sort ${i}`));
  fireEvent.click(screen.getByRole('button', { name: '按 min Cg 排序' }));
  expect(order()).toEqual([3, 2, 0, 1, 5, 4].map(i => `Sort ${i}`));
  fireEvent.click(screen.getByRole('button', { name: 'Bin1 CPK' }));
  expect(order()).toEqual([3, 2, 0, 1, 5, 4].map(i => `Sort ${i}`));
  expect(run.rows).toEqual(original);
});

it('sorts all filtered rows before pagination and resets to the first page', () => {
  const run = demoRun(startingDemoSelections()), base = run.rows[0];
  run.rows = Array.from({ length: 45 }, (_, index) => ({ ...base, key: `page-${index}`, name: `Item ${index}`,
    cpk: { ...base.cpk!, value: index } }));
  mount(run);
  fireEvent.click(screen.getByRole('button', { name: '按 Bin1 CPK 排序' }));
  const names = () => screen.getAllByRole('row').slice(1).map(row => within(row).getByRole('button').getAttribute('aria-label'));
  expect(names()[0]).toBe('Item 44');
  expect(names()).toHaveLength(40);
  fireEvent.click(screen.getByRole('button', { name: '下一页' }));
  expect(names()[0]).toBe('Item 4');
  fireEvent.click(screen.getByRole('button', { name: '按 Bin1 CPK 排序' }));
  expect(names()[0]).toBe('Item 0');
  expect(screen.getByRole('button', { name: '上一页' })).toBeDisabled();
});

it('sorts Outlier test counts, deviation and Test ID with the same two-click behavior', () => {
  const run = demoRun(startingDemoSelections()), base = run.rows[0];
  const values = [{ count: 2, score: 10, number: 2 }, { count: 10, score: 2, number: 10 },
    { count: 0, score: null, number: null }, { count: 1, score: 0, number: 0 }];
  run.rows = values.map((value, index) => ({ ...base, key: `outlier-${index}`, name: `Outlier ${index}`, number: value.number,
    outlier: value.score === null ? undefined : { baselines: [], candidates: Array.from({ length: value.count }, () => ({})),
      maxScore: value.score, label: '发现候选', review: false, filenames: [] } }));
  mount(run);
  fireEvent.click(screen.getByRole('button', { name: 'Outlier' }));
  const order = () => screen.getAllByRole('row').slice(1).map(row => within(row).getByRole('button').getAttribute('aria-label'));
  const click = (label: string) => fireEvent.click(screen.getByRole('button', { name: `按 ${label} 排序` }));
  click('异常候选记录条数');
  expect(order()).toEqual([1, 0, 3, 2].map(i => `Outlier ${i}`));
  click('异常候选记录条数');
  expect(order()).toEqual([2, 3, 0, 1].map(i => `Outlier ${i}`));
  click('最大偏离程度（稳健σ）');
  expect(order()).toEqual([0, 1, 3, 2].map(i => `Outlier ${i}`));
  click('最大偏离程度（稳健σ）');
  expect(order()).toEqual([3, 1, 0, 2].map(i => `Outlier ${i}`));
  click('Test ID / 名称');
  expect(order()).toEqual([1, 0, 3, 2].map(i => `Outlier ${i}`));
  click('Test ID / 名称');
  expect(order()).toEqual([3, 0, 1, 2].map(i => `Outlier ${i}`));
  expect(screen.getByRole('button', { name: '按 Test ID / 名称 排序' }).closest('th')).toHaveAttribute('aria-sort', 'ascending');
  click('备注复核状态');
  expect(screen.getByRole('button', { name: '按 备注复核状态 排序' }).closest('th')).toHaveAttribute('aria-sort', 'descending');
});
