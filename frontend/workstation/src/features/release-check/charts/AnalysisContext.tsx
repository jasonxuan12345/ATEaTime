import { t, localize, useLanguage } from "../../../i18n";
import { format, labels, moduleLabels, type Module, type Row, type Selections } from '../model';
import type { ChartData, ChartKind, MatrixGroup } from './types';
import s from './charts.module.css';

const guidance: Record<ChartKind, [string, string]> = {
  histogram: ['先看分布宽度和均值是否贴近规格', '直方图对照规格位置；切换图组时保留当前模块的数据来源。'],
  cg: ['先定位低 Cg site，再比较内部波动', '柱状图保留报告原值；Bin1 CPK 接受规则的判定结果与原始 Cg 数值分别说明。'],
  box: ['比较 各 Site 的测量水平和数据分散程度', '离群点是图形标记，仍保留在原始统计中。'],
  sequence: ['观察跳点、漂移和台阶', '横轴为原始测试次序，不自动当作同颗样片的 Loop。'],
  wafer: ['观察异常是否在空间上聚集', '按当前模块和单个晶圆展示，不把聚集形态直接认作工艺原因。'],
};
export function AnalysisContext({ row, group, module, selections, data, kind }: {
  row: Row | null; group: MatrixGroup | null; module: Module; selections: Selections; data: ChartData | null; kind: ChartKind }) {
  useLanguage();
  return <section className={s.context} aria-label={t("当前分析依据与来源")}>
    <div className={s.contextHeading}><div><span className={s.eyebrow}>{localize(row ? `Test ID ${row.number ?? t("未记录")}` : t("测项组概览"))}</span>
      <h2>{row?.name ?? group?.family ?? t("请选择测项组")}</h2></div>
      {localize(row && <span className={s.verdict}>{localize(labels[row.status])}</span>)}</div>
    {row ? <><div className={s.contextFacts}><span>{t("规则 ")}<strong>{localize(row[module]?.rule_id ?? t("无命中规则"))}</strong></span><span>{t("单位 ")}<strong>{row.unit ?? t("未记录")}</strong></span>
      <span>LSL <strong>{localize(format(row.lsl, 4))}</strong></span><span>USL <strong>{localize(format(row.usl, 4))}</strong></span></div>
      <p className={s.caption}>{localize(row[module]?.reason ?? t("当前模块没有匹配结果"))}</p>
      <div className={s.sourceLedger}>{(['cpk', 'cgm', 'mmsa'] as const).map(m => <div key={m} data-active={module === m}>
        <strong>{localize(moduleLabels[m])}</strong><span title={row[m]?.filename}>{row[m] ? row[m]?.filename ?? t("当前模块来源") : t("未参与")}</span>
        {localize(row[m] && <small>{t("有效 n=")}{localize(row[m]!.n)} · {localize(selections[m].sites.length)} site · {localize(selections[m].parts === null ? t("全部入选记录") : t("{0} 条所选记录", selections[m].parts!.length))}</small>)}
      </div>)}</div>
    </> : <p className={s.caption}>{localize(group?.rule)} · {localize(group?.count ?? 0)} {t(" 项 · ")}{localize(group?.basis)}{t("。先选具体测项，再核对该项数据与判定依据。")}</p>}
    <div className={s.guidance}><strong>{localize(row ? guidance[kind][0] : t("先从矩阵确认共同异常列"))}</strong>
      <span>{localize(row ? guidance[kind][1] : t("点击右侧任一行打开单项主图；尚未选行，不展示代表项。"))}</span>
      {localize(data?.context.sample_type && row && <small>{localize(data.context.sample_type)} {t(" · 观察和选择不改变原始判定")}</small>)}</div>
  </section>;
}
