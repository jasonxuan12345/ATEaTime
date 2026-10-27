import { afterEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { t, localizeReport, setLanguage, useLanguage } from './index';
import catalog from '../../../../config/i18n/zh-en.json';
import { wrapCanvasText } from '../features/release-check/charts/export';
import { demoChart } from '../features/release-check/charts/demo';
import { demoRun, startingDemoSelections } from '../features/release-check/model';
import { chartOption } from '../features/release-check/charts/options';
import { hitsOption, matrixOption } from '../features/release-check/outliers/chartOptions';
import { allBaselines, allCandidates } from '../features/release-check/outliers/model';
import { reportFor } from '../features/release-check/outliers/testFixtures';

afterEach(() => { setLanguage('zh'); vi.unstubAllGlobals(); });

it('changes rendered language without remounting or clearing edited data', () => {
  function Example() {
    const language = useLanguage();
    return <><button onClick={() => setLanguage(language === 'zh' ? 'en' : 'zh')}>{t('导出')}</button>
      <input aria-label='user note' defaultValue='用户备注：通过' />
      <select aria-label='review state' defaultValue='待复核'>
        {['待复核', '已确认观察'].map(state => <option key={state} value={state}>{t(state)}</option>)}
      </select></>;
  }
  render(<Example />);
  fireEvent.change(screen.getByLabelText('user note'), { target: { value: '保留我的输入' } });
  fireEvent.click(screen.getByRole('button', { name: '导出' }));
  expect(screen.getByRole('button', { name: 'Export' })).toBeVisible();
  expect(screen.getByLabelText('user note')).toHaveValue('保留我的输入');
  expect(screen.getByLabelText('review state')).toHaveValue('待复核');
  expect(screen.getByRole('option', { name: 'Pending review' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Export' }));
  expect(screen.getByRole('button', { name: '导出' })).toBeVisible();
  expect(screen.getByLabelText('user note')).toHaveValue('保留我的输入');
});

it('translates generated progress and preserves interpolated source data', () => {
  setLanguage('en');
  expect(t('正在解析 {0} / {1}：{2}…', 1, 2, '通过.stdf')).toBe('Parsing 1 / 2: 通过.stdf…');
  expect(t('写入 CPK Excel：3 / 10')).toBe('Writing CPK Excel: 3 / 10');
  expect(t('导出')).toBe('Export');
  expect(t('通过.stdf：文件已不在当前会话，请重新导入'))
    .toBe('通过.stdf: The file is no longer in this session. Import it again.');
  expect(t('Wafer Map')).toBe('Wafer Map');
});

it('localizes exported presentation fields without changing source identities or user remarks', () => {
  setLanguage('en');
  const report = { reason: '通过', state: '待复核', filename: '通过', name: '通过', remark: '通过',
    suggestion: '通过', conditions: { reason: '通过' }, selection: { tests: ['通过'] } };
  const exported = localizeReport(report);
  expect(exported).toEqual({ ...report, reason: 'Pass', state: 'Pending review' });
  expect(report.reason).toBe('通过');
});

it('keeps interpolation slots and English translations complete', () => {
  for (const [source, target] of Object.entries(catalog)) {
    expect(target, source).not.toMatch(/[\u3400-\u9fff]/);
    expect((target.match(/\{\d+\}/g) ?? []).sort(), source).toEqual((source.match(/\{\d+\}/g) ?? []).sort());
  }
});

it('updates chart axes, series and hover text in English', () => {
  setLanguage('en');
  const selections = startingDemoSelections(), row = demoRun(selections).rows[0];
  const settings = { sites: null, sort: 'site' as const, fullSpec: true, stdMode: false,
    connect: false, wafer: '', outOfSpec: false, selected: {} };
  const data = demoChart(row, 'cgm', selections);
  const reports = [reportFor()];
  const candidates = allCandidates(reports), baselines = allBaselines(reports);
  const options = [chartOption(data, 'histogram', settings), chartOption(data, 'cg', settings),
    chartOption(data, 'box', settings), chartOption(data, 'sequence', settings),
    hitsOption(candidates[0], 6), matrixOption(candidates, baselines)];
  expect(JSON.stringify(options)).not.toMatch(/[\u3400-\u9fff]/);
});

it('wraps report image text within its available width without splitting normal English words', () => {
  expect(wrapCanvasText('Reference records and specification', 19, value => value.length))
    .toEqual(['Reference records', 'and specification']);
  const lines = wrapCanvasText('中文说明测试 LONG_FILENAME_WITHOUT_SPACES', 12, value => value.length);
  expect(lines.every(line => line.length <= 12)).toBe(true);
});
