import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import * as echarts from 'echarts';
import { ReleasePlot } from './ReleasePlot';
import { demoChart } from './demo';
import { demoRun, startingDemoSelections } from '../model';
import { chartOption } from './options';
import type { ChartSettings } from './types';

it('keeps the rendered chart stable when enabling brush and delivers full-data range selections', async () => {
  const selections = startingDemoSelections(), data = demoChart(demoRun(selections).rows[0], 'cgm', selections);
  const settings: ChartSettings = { sites: null, sort: 'site', fullSpec: true, stdMode: false, connect: false, wafer: '', outOfSpec: false, selected: {} };
  const onPick = vi.fn(), option = chartOption(data, 'sequence', settings);
  render(<ReleasePlot title='测试次序' data={data} kind='sequence' settings={settings} option={option} onPick={onPick} onFullscreen={vi.fn()} />);
  const instance = vi.mocked(echarts.init).mock.results.at(-1)!.value;
  const initialUpdates = instance.setOption.mock.calls.length;
  await userEvent.setup().click(screen.getByRole('button', { name: '框选样本' }));
  expect(screen.getByRole('button', { name: '框选样本' })).toHaveAttribute('aria-pressed', 'true');
  expect(instance.setOption.mock.calls.length).toBe(initialUpdates);
  expect(instance.dispatchAction).toHaveBeenLastCalledWith(expect.objectContaining({ type: 'takeGlobalCursor', brushOption: { brushType: 'rect', brushMode: 'single' } }));
  const brush = instance.on.mock.calls.find((call: unknown[]) => call[0] === 'brushEnd')![1];
  act(() => brush({ areas: [{ coordRange: [[10, 20], [1, 3]] }] }));
  expect(onPick).toHaveBeenLastCalledWith({ selection: { sequence_min: 10, sequence_max: 20, value_min: 1, value_max: 3 } });
  act(() => brush({ areas: [] }));
  expect(onPick).toHaveBeenLastCalledWith({ selection: {} });
  await userEvent.setup().click(screen.getByRole('button', { name: '复位此图' }));
  expect(instance.dispatchAction).toHaveBeenCalledWith({ type: 'restore' });
  expect(screen.getByRole('button', { name: '框选样本' })).toHaveAttribute('aria-pressed', 'false');
});
