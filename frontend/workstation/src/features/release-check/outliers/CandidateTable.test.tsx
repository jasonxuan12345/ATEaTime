import { fireEvent, render, screen } from '@testing-library/react';
import { CandidateTable } from './CandidateTable';
import { allCandidates, type Candidate } from './model';
import { reportFor } from './testFixtures';

const template = allCandidates([reportFor()])[0];
const candidate = (part: number, extra: Partial<Candidate> = {}): Candidate => ({
  ...template, key: `candidate-${part}`, part_index: part, ...extra,
});
const order = () => screen.getAllByRole('button', { name: /^记录 #/ }).map(button => button.textContent);
const sort = (label: string) => fireEvent.click(screen.getByRole('button', { name: `按 ${label} 排序` }));

it('sorts numeric deviation and hit counts descending then ascending without changing selection or input order', () => {
  const candidates = [candidate(1, { max_score: 2, hit_count: 10 }), candidate(2, { max_score: 10, hit_count: 2 }),
    candidate(3, { max_score: 0, hit_count: 0 })];
  const onSelect = vi.fn();
  render(<CandidateTable candidates={candidates} selectedKey={candidates[0].key} onSelect={onSelect}/>);
  sort('最大偏离程度（稳健σ）');
  expect(order()).toEqual(['记录 #2', '记录 #1', '记录 #3']);
  expect(screen.getByRole('button', { name: '按 最大偏离程度（稳健σ） 排序' }).closest('th')).toHaveAttribute('aria-sort', 'descending');
  sort('最大偏离程度（稳健σ）');
  expect(order()).toEqual(['记录 #3', '记录 #1', '记录 #2']);
  sort('检出异常的测项');
  expect(order()).toEqual(['记录 #1', '记录 #2', '记录 #3']);
  sort('检出异常的测项');
  expect(order()).toEqual(['记录 #3', '记录 #2', '记录 #1']);
  expect(candidates.map(c => c.part_index)).toEqual([1, 2, 3]);
  expect(onSelect).not.toHaveBeenCalled();
  expect(screen.getByRole('button', { name: '记录 #1' })).toHaveAttribute('aria-pressed', 'true');
  fireEvent.click(screen.getByRole('button', { name: '记录 #2' }));
  expect(onSelect).toHaveBeenCalledWith(candidates[1]);
});

it('sorts record numbers and compound Head/Site columns numerically with missing channels last', () => {
  render(<CandidateTable candidates={[
    candidate(2, { head: 1, site: 2 }), candidate(10, { head: 1, site: 10 }),
    candidate(0, { head: 0, site: 0 }), candidate(3, { head: null, site: null }),
  ]} onSelect={vi.fn()}/>);
  sort('测试记录 / 源文件');
  expect(order()).toEqual(['记录 #10', '记录 #3', '记录 #2', '记录 #0']);
  sort('测试记录 / 源文件');
  expect(order()).toEqual(['记录 #0', '记录 #2', '记录 #3', '记录 #10']);
  sort('Head / Site');
  expect(order()).toEqual(['记录 #10', '记录 #2', '记录 #0', '记录 #3']);
  sort('Head / Site');
  expect(order()).toEqual(['记录 #0', '记录 #2', '记录 #10', '记录 #3']);
});

it('sorts wafer names naturally and coordinates numerically with absent wafer data last', () => {
  render(<CandidateTable candidates={[
    candidate(1, { wafer: 'W2', x: -2, y: 0 }), candidate(2, { wafer: 'W10', x: 0, y: 0 }),
    candidate(3, { wafer: 'W2', x: 0, y: 0 }), candidate(4, { wafer: null, x: null, y: null }),
  ]} onSelect={vi.fn()}/>);
  sort('Wafer / 坐标');
  expect(order()).toEqual(['记录 #2', '记录 #3', '记录 #1', '记录 #4']);
  sort('Wafer / 坐标');
  expect(order()).toEqual(['记录 #1', '记录 #3', '记录 #2', '记录 #4']);
});

it('sorts before compact pagination, resets the page and preserves search sorting', () => {
  const candidates = Array.from({ length: 12 }, (_, index) => candidate(index, { max_score: index, part_id: `record ${index}` }));
  const { rerender } = render(<CandidateTable candidates={candidates} onSelect={vi.fn()} compact/>);
  expect(screen.queryByRole('button', { name: '按 Wafer / 坐标 排序' })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: '下一页异常候选记录' }));
  sort('最大偏离程度（稳健σ）');
  expect(order()).toEqual([11, 10, 9, 8, 7].map(i => `记录 #${i}`));
  expect(screen.getByRole('button', { name: '上一页异常候选记录' })).toBeDisabled();
  sort('最大偏离程度（稳健σ）');
  expect(order()).toEqual([0, 1, 2, 3, 4].map(i => `记录 #${i}`));
  rerender(<CandidateTable candidates={candidates} onSelect={vi.fn()}/>);
  fireEvent.change(screen.getByRole('searchbox', { name: '搜索异常候选记录' }), { target: { value: 'record 1' } });
  expect(order()).toEqual([1, 10, 11].map(i => `记录 #${i}`));
});
