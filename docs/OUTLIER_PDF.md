# Outlier Excel / PDF

接口：`GET /api/release-check/datasets/{dataset_id}/runs/{run_id}/reports/outlier.{xlsx|pdf}`。文件名为 `YYYYMMDD_DeviceID_Outlier_Report.{xlsx|pdf}`；缺失或混合设备标识使用 `UnknownDevice`。

## 数据与备注

参考组按 `File + Test & Condition（含有效规格）+ Lot + Wafer + Head + Site` 建立，使用当前算法选中的有效、规格内 Bin1 PASS 记录。候选记录不等于确认的芯片缺陷；重测 PRR 保持独立，候选身份使用来源数据集和 Part Index。

页面完成计算时调用 `PUT /datasets/{dataset_id}/runs/{run_id}/outlier-reports`，关联各来源的 `dataset_id/report_id`。导出前再次同步已保存的 Outlier 观察，按 `dataset_id/group_id` 关联。未保存的编辑不能导出。后台保留当前 run 对应的准确结果，即使来源的最近三次 Outlier 列表淘汰了该报告，也不会改用新报告。来源会话删除或 run 过期则拒绝导出。

关联对象保存在会话内部，不会向普通结果 JSON 塞入重复报告。备注变化增加版本，文件缓存随版本和语言区分；重复下载复用生成文件。支持现有生成状态、取消和文件下载机制。

## Excel

配置：`config/release_outlier_excel.json`。

1. `Reference_Group_Stats`：所有参考组、条件、n、Median/Q1/Q3、稳健 σ、筛选限、候选数、结果与保存的观察；包含未评估组。
2. `Candidate_Records`：每个候选 PRR 一行，保留文件、Lot/Wafer/Head/Site、坐标、Hard Bin/Part Result、命中测项数、最大偏离与前 10 个命中。全部命中见第 4 表。
3. `Candidate_Test_Matrix`：来源和完整测项组分别成列，命中时填偏离程度；未命中留空并填淡灰，批注注明 `Grey cells mean no outlier hit; they do not mean evaluated PASS.`
4. `Outlier_Hit_Details`：每个命中一行，包含实测值、参考统计、筛选限、稳健 σ、偏离程度、原始记录序号、Baseline ID 和条件，支持透视分析。

表头深灰白字，冻结表头。用户文本按文本保存，不按公式或链接执行。超出 Excel 行列限制时明确拒绝，不静默丢弃候选或测项。

## PDF

16 × 9 英寸矢量报告，分三部分：

- 摘要：来源、参考维度、参考样本范围、组数/测项数、所选执行记录数、候选数、异常测项数；候选和测项清单每页最多 12 行，必要时续页。未评估组单独标明。
- 矩阵：16 条记录 × 8 个测项，按两个方向分页，遍历全部候选与命中测项组合。灰格说明与配色说明在矩阵旁保留。
- 测项详情：同页并列显示分布偏离图和序列趋势图；下方为 Wafer 空间图及参考统计/Top 候选记录。每组分布最多 1000 个候选点，优先最大偏离；背景趋势每测项最多 20000 点，但异常候选命中全部叠加。背景取样不改变参考统计。
- 参考组超过 8 个或有多个 Wafer 时续页，避免合并不同组或省略 Wafer。没有坐标时以参考统计表替代空间图。
- 对多测项命中的候选记录，以及每个已备注测项组中最严重的一条候选记录生成芯片档案页，条形图显示前 40 个命中，右侧保留定位和观察。全部明细在 Excel 中。
- 分布图以实测值为主轴，同时标注最显著候选的稳健 σ；各组单独使用自身中位数和筛选限，不能合并计算。Wafer X/Y 等比例，灰色已测位置、橙色候选、红色重点记录。

报告统计与 Excel 不依赖全量 Parquet。PDF 从内存计算数据定位参与记录；每个来源只建立一次所需测项分组，最多缓存两份展示数据，避免逐测项重新扫描所有测项。

## 本次验证

前端 151 项测试及生产构建通过。后端新旧报告、Outlier、备注和下载缓存相关 218 项回归通过；补充检查未评估组、原样本一致性和最近结果淘汰后的快照保持。使用生产构建在中文/英文及 390–1920 像素窗口检查 156 个页面状态，未发现整页溢出、按钮裁切、控件重叠或脚本错误。

PDF 生成后使用 PyMuPDF 渲染并检查页面文字边界，人工查看摘要、矩阵、芯片档案和 16 Site 详情页；Excel 使用 openpyxl 检查 Sheet、列、数值、备注及批注。样例和截图保存于当前任务的 `review-reports` 目录。验证使用生成数据，尚未对量产数据测量导出耗时和文件大小。
