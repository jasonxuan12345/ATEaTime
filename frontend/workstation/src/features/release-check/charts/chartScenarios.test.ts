import { act, renderHook } from '@testing-library/react';
import { chartScenario, chartUnavailable, moduleForRow } from './chartScenarios';
import { useChartPair } from './useChartPair';
import { demoRun, startingDemoSelections, type Module } from '../model';
import { demoChart } from './demo';
import { selectionEvidence } from './selectionEvidence';

const selections = startingDemoSelections(), row = demoRun(selections).rows[0];
it.each([
  ['MMSA-001', 'sequence', 'cg', false], ['MMSA-002', 'sequence', 'cg', false],
  ['MMSA-003', 'box', 'histogram', false], ['MMSA-004', 'cg', 'box', true],
  ['MMSA-005', 'histogram', 'box', false], ['MMSA-006', 'box', 'sequence', false],
  ['MMSA-007', 'histogram', 'box', false], ['MMSA-008', 'histogram', 'box', false],
  ['unknown', 'box', 'sequence', false],
])('recommends the evidence pair for %s without changing source or verdict', (rule_id, primary, secondary, primaryStd) => {
  const current = { ...row, mmsa: { ...row.mmsa!, rule_id } };
  expect(chartScenario(current, 'mmsa')).toMatchObject({ primary, secondary, primaryStd });
  expect(chartScenario(current, 'cpk')).toMatchObject({ primary: 'histogram', secondary: 'box', primaryStd: false });
  expect(chartScenario(current, 'cgm')).toMatchObject({ primary: 'cg', secondary: 'box', primaryStd: false });
  expect(current.mmsa.rule_id).toBe(rule_id);
});
it('preserves the current comparison for wafer, swaps flags, and resets recommendation on item/module changes', () => {
  const { result, rerender } = renderHook(({ owner, module }) => useChartPair(owner, row, module), { initialProps: { owner: 'a', module: 'cgm' as Module } });
  act(() => result.current.toggleStd('primary'));
  act(() => result.current.select('primary', 'wafer'));
  expect(result.current).toMatchObject({ primary: 'wafer', secondary: 'cg', secondaryStd: true });
  act(() => result.current.swap());
  expect(result.current).toMatchObject({ primary: 'cg', primaryStd: true, secondary: 'wafer' });
  rerender({ owner: 'b', module: 'cpk' });
  expect(result.current).toMatchObject({ primary: 'histogram', secondary: 'box', manual: false });
  rerender({ owner: 'a', module: 'cgm' });
  expect(result.current).toMatchObject({ primary: 'cg', secondary: 'box', primaryStd: false, manual: false });
  act(() => result.current.select('secondary', 'sequence'));
  act(() => result.current.restore());
  expect(result.current.secondary).toBe('box');
});
it('requires actual data and coordinates, and falls back only to an available module', () => {
  const data = demoChart(row, 'cpk', selections);
  expect(chartUnavailable({ ...data, wafer: { ...data.wafer, wafers: [], empty_reason: '没有坐标' } }, 'wafer')).toBe('没有坐标');
  expect(chartUnavailable({ ...data, points: [] }, 'sequence')).toBeTruthy();
  expect(chartUnavailable(data, 'box')).toBeNull();
  expect(moduleForRow({ ...row, cgm: null }, 'cgm')).toBe('cpk');
});
it('adds selected full-data samples omitted from scatter without changing statistics or accepting another source', () => {
  const data = demoChart(row, 'cgm', selections), point = data.points[0];
  const sampled = { ...data, points: data.points.slice(1), displayed_points: data.points.length - 1, sampled: true };
  const page = { points: [point], total: 1, offset: 0, limit: 100, context: data.context };
  const shown = selectionEvidence(sampled, page, { ids: [point.id] });
  expect(shown.points.some(p => p.id === point.id)).toBe(true);
  expect(shown.statistics).toBe(data.statistics);
  expect(selectionEvidence(sampled, { ...page, context: { ...data.context, module: 'cpk' } }, { ids: [point.id] })).toBe(sampled);
});
