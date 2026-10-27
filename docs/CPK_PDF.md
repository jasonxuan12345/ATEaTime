# CPK PDF

页面「导出」可分别下载 CPK Excel、CPK PDF、Cgm Excel、Cgm PDF。
下载接口：`GET /api/release-check/datasets/{dataset_id}/runs/{run_id}/reports/cpk.pdf`。

- 文件名：`YYYYMMDD_DeviceID_Cpk_Report.pdf`，DeviceID 取对应 CPK 来源的 MIR / 测试条件。
- 页面：16 × 9 英寸矢量 PDF，150 dpi 渲染为 2400 × 1350。
- 判定沿用本次报告保存的 CPK 区间，默认 `<1.67` 为 Fail，`1.67～2.00` 为 Need to check，`>=2.00` 为 Pass。测项存在超规格 Fail Count 时为 Fail。
- 最终 Result 和排序依据 Overall CPK 及 Fail Count；各 Site CPK 独立着色，最差 Site 用于定位。
- 详情页右上方 Result 下显示已保存的 Remark，按条目编号；保存和撤销备注后重新导出会使用最新内容。历史来源及完整统计仍保留在诊断依据中，超长备注保留完整 PDF 批注。
- 所有 CPK 数值与五区详情图使用本次 CPK 选中的 Bin1 有效样本。PDF 核对保存统计与原始样本，不重新选择数据。
- 首页总执行数、Bin1 Yield 使用本次选中的全部 PRR 测试记录；Yield 分子为 Bin1 且 PRR 结果有效通过的记录。重复执行不推断物理芯片身份、不去重。
- 首页包含元数据、四张 KPI 卡和异常清单，每页最多 12 行。只有 Fail / Need to check 生成独立详情页，没有 All Tests 页面。
- 详情为上两区、下三区：Site 次序图、Wafer Map / Site 统计表、直方图和 KDE、正态 Q-Q、箱线图。
- 1～4 Sites：堆叠直方图及各 Site KDE；5～16 Sites：整体直方图，正常 Site 为淡蓝背景，只高亮最差 1～3 个异常 Site。Q-Q 使用同样规则。
- Site 填色使用保存的 1.67 / 2.00 区间。缺失 / 单样本 / 零标准差 Site 的 CPK 显示 `--`；常量或少于 2 个样本不绘制 KDE。没有可评估 CPK 的测项不计入 KPI，已知超规格失效仍列为 Fail。
- 有唯一有效 lot/wafer 身份和可用坐标时显示等比例圆形 Wafer Map 及连续色条；同一位置展示最后一条入选记录，注明重复位置数及缺坐标数量。多个晶圆不混成一张图，改为 Site 统计表并注明原因。
- 缺少坐标时显示 Site 统计表；超过 8 Sites 分成左右两表，每表最多 8 行。

独立导出解析后的 Parquet，已有输出拒绝覆盖：

```python
from deepyield.compute.release_cpk_pdf import export_release_cpk_pdf

output = export_release_cpk_pdf('data.wide_all.parquet', 'reports', sites=[1, 2])
print(output['path'])
```

生成模拟 STDF 并经过正式解析和计算：

```sh
python examples/generate_cpk_pdf_demo.py --output-dir output/pdf/cpk-demo
```

1 / 4 Sites 示例展示单栏表，8 Sites 展示 Wafer Map，16 Sites 展示双栏表及清单分页；所有示例仅用于功能和版式检查。

导出页面显示实际生成进度并支持取消。DeviceID 缺失或不唯一时，报告省略产品名称，文件名保留日期和报告类型；元数据不再作为导出的必填项。
