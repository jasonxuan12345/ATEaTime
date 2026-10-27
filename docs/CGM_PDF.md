# CGM PDF

页面右上角「导出」分别提供 CGM、CPK 的 Excel 和 PDF。
PDF 使用当前计算中 CGM 自己的来源、选样、统计及已匹配的 Bin1 CPK。
详情图按已保存的样本引用读取原始测量值，不重新选择样本。

- 文件名：`YYYYMMDD_DeviceID_Cgm_Report.pdf`，日期为生成时本地日期，产品取 MIR `part_type` / `PART_TYP` 或报告条件。
- 页面：16 × 9 英寸，PDF 矢量文字和图形；按 150 dpi 渲染为 2400 × 1350。
- 首页为元数据、4 张 KPI 卡片及异常清单；续页只展示异常清单，每页最多 12 行。
- 异常按 Fail、Need to check 分组，再按 min Cg 升序排列；每个异常一页详情。
- 1～4 Sites 的清单逐站显示 Cg；5～16 Sites 改为 Worst Site、Max StdDev、min/Max Cg。
- 所有详情均采用左侧三张对齐泳道图、右侧统计表，保留真实 Site 编号（包含 S0）。
- 三张图依次为原始测量序列、组内偏差、移动极差 MR。组内偏差保持 `x - Site Mean`，绿色/黄色背景透明度为 0.15；每个 Site 的紫色虚线表示 `Site Mean - Grand Mean`。
- MR 按每个 Site 的测量记录顺序计算 `abs(x[i] - x[i-1])`，首点不画，实线表示该 Site 的 MR 均值。绿线为 `1.128 × 0.20 × Tol / (6 × 1.33)`，红线为绿线的 3.267 倍。
- 统计列为 `Site | Count | Mean | Shift | StdDev | Cg* | Cg(MR) | Status`。Shift 带正负号，与 Mean 使用相同小数位。
- 全工具 Cg 使用 `0.20 × Tol / (6 × StdDev)`；Cg(MR) 使用 `0.20 × Tol / (6 × (MR均值 / 1.128))`。Cg(MR) 仅作诊断参考，不改变 Status、Result 或现有 CPK 接受规则。旧结果需重新计算，旧口径或无口径标识的 JSON 不可直接导出。
- 与 Excel 共用 `config/release_cgm_excel.json` 的联合判定和颜色规则；全 Infinity 的 min Cg 显示 Infinity，任一 NaN 传递为 min Cg=NaN。
- Bin1 CPK 独立按 `<1.67` 红色、`1.67≤CPK<2.00` 黄色、`CPK≥2.00` 绿色着色。
- 异常清单在 Result 后显示 Remark；详情页统一在右上方 Result 下显示，统计表排在 Remark 下方。自动 Remark 以一句诊断建议开头，后续按条目编号列出实际 Site Diff、历史经验和人工补充；沿用页面已保存的人工最终备注，超长备注保留完整 PDF 批注。
- 等限值状态项、功能测试、无双边限值、样本不足等无法评估项不计入 KPI；首页注明排除数量。
- Min/All 的 Count、Mean、StdDev 基于该项所有有效样本；Shift 为 0，Cg* 和 Cg(MR) 分别取各 Site 最小值，Status 为联合判定。
- 仅输出汇总、异常清单、异常详情。没有 All Tests 页面。全部合格时仅输出一页汇总。

下载接口：`GET /api/release-check/datasets/{dataset_id}/runs/{run_id}/reports/cgm.pdf`。
结果过期或来源不一致时返回错误；配置变更、未保存观察备注、示例模式时页面禁用导出。

独立调用解析后的 Parquet（输出已存在时拒绝覆盖）：

```python
from deepyield.compute.release_cgm_pdf import export_release_cgm_pdf

result = export_release_cgm_pdf('data.wide_all.parquet', 'reports', sites=[1, 2])
print(result['path'])
```

生成示例：`python examples/generate_cgm_pdf_demo.py --output-dir output/pdf/cgm-demo`。
该脚本生成模拟 STDF，经项目 C++ 解析器和正式 CGM 计算后导出 1、4、8、16 Sites 报告。
16 Sites 示例包含超过 12 个异常，以检查汇总分页；其他案例覆盖 Infinity、CPK 豁免及状态项排除。
示例数据只用于功能和版式检查。

导出页面显示实际生成进度并支持取消。DeviceID 缺失或不唯一时，报告省略产品名称，文件名保留日期和报告类型；元数据不再作为导出的必填项。
