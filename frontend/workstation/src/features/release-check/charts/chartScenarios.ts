import type { Module, Row } from '../model';
import type { ChartData, ChartKind } from './types';

export type ChartPair = { primary: ChartKind; secondary: ChartKind; primaryStd: boolean; secondaryStd: boolean };
export type ChartScenario = ChartPair & { title: string; description: string };
export const chartNames: Record<ChartKind, string> = { histogram: '直方图', cg: '各 site Cg', box: '分 site 箱线图', sequence: '测试次序', wafer: 'Wafer Map' };
export function chartTitle(kind: ChartKind, stdMode = false) {
  return kind === 'cg' && stdMode ? '各 site Std' : kind === 'box' ? '分 site 箱线图＋均值点' : chartNames[kind];
}
export function moduleForRow(row: Row, preferred: Module): Module {
  return row[preferred] ? preferred : (['cpk', 'cgm', 'mmsa'] as const).find(m => row[m]) ?? preferred;
}
export function chartScenario(row: Row, module: Module): ChartScenario {
  const pair = (primary: ChartKind, secondary: ChartKind, title: string, description: string, primaryStd = false): ChartScenario =>
    ({ primary, secondary, primaryStd, secondaryStd: false, title, description });
  if (module === 'cpk') return pair('histogram', 'box', 'CPK · 分布与规格', '先看宽度与贴限，再比较各 Site 的测量水平和数据分散程度。');
  if (module === 'cgm') return pair('cg', 'box', 'CGM · 重复性与 site 对照', '保留原始 Cg，与同模块箱线对照；可切测试次序查看跳点。');
  switch (row.mmsa?.rule_id) {
    case 'MMSA-001': case 'MMSA-002':
      return { ...pair('sequence', 'cg', 'Site Diff · 重复性异常', '按次序查看跳点、漂移或台阶，用同模块 Std 比较 site 波动；Site Diff 不提供报告 Cg，需切 CGM 单独查看。'), secondaryStd: true };
    case 'MMSA-003':
      return pair('box', 'histogram', 'Site Diff · site 均值偏移', '箱线均值点比较位置，直方图核对各 site 分布；两图均使用 Site Diff 样本。');
    case 'MMSA-004':
      return pair('cg', 'box', 'Site Diff · site 波动差异', '用 Std 柱比较数据分散程度，箱线图核对波动与离群点，不排除样本。', true);
    case 'MMSA-007':
      return pair('histogram', 'box', 'Site Diff · 均值贴限', '完整规格视野观察均值与近侧限值，再比较 site；不把规格中点作为 Target。');
    case 'MMSA-008':
      return pair('histogram', 'box', 'Site Diff · 分布偏宽', '查看真实分布和 Site 内数据的分散程度；分布宽不能直接确认双峰或 trim 根因。');
    case 'MMSA-005':
      return pair('histogram', 'box', 'Site Diff · 分布与限值待复核', '先查看基础分布与规格，不补造形态分类或关联数据。');
    default:
      return pair('box', 'sequence', 'Site Diff · 人工复核', '比较 site 统计与原始次序；缺少充分证据时保留人工复核。');
  }
}
export function chartUnavailable(data: ChartData | null, kind: ChartKind, stdMode?: boolean): string | null {
  if (!data) return '正在读取当前模块数据';
  if (data.empty_reason) return data.empty_reason;
  if (kind === 'wafer') return data.wafer.wafers.length ? null : data.wafer.empty_reason ?? '缺少有效晶圆身份或坐标';
  if (kind === 'histogram') return data.histogram.edges.length > 1 ? null : '没有可用的完整分箱';
  if (kind === 'sequence') return data.points.some(point => Number.isFinite(point.record_sequence)) ? null : '没有可靠的测试次序';
  if (kind === 'cg') {
    if (stdMode === false && !data.sites.some(site => site.cg !== null)) return '当前模块没有可计算的报告 Cg，可切换 Std 查看。';
    if (stdMode === true && !data.sites.some(site => site.std !== null)) return '当前模块 Std 无法计算，请查看原始分布。';
    if (!data.sites.some(site => site.cg !== null || site.std !== null)) return '当前模块 Cg 和 Std 均无法计算';
  }
  return data.sites.some(site => site.n > 0) ? null : '没有有效 site 样本';
}
