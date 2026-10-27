# Site Diff PDF

接口：`GET /api/release-check/datasets/{dataset_id}/runs/{run_id}/reports/site_diff.pdf`，支持现有 `job_id`、`delivery=file`、`lang` 参数。

文件名：`YYYYMMDD_DeviceID_SiteDiff_Report.pdf`。设备标识缺失或混合时使用 `UnknownDevice`，不取第一份文件猜测设备。

- 16 × 9 英寸矢量 PDF；150 dpi 渲染时为 2400 × 1350 像素。
- 摘要明确标注 `Valid module records (PASS/FAIL included; not Bin 1 only)`。整体及分 Site 的 n、Mean、Std、Cp 使用已保存的全量有效记录统计；直方图重新从同一份完整参与记录做分箱。
- `Site Std Ratio` 沿用计算规则：最大 Site Std / Site Std 中位数。它不是 max/min；均值差占规格范围比例也不是独立的诊断触发条件。
- 摘要列出来源、Lot/Wafer、温度、Site 和所选执行记录数；4 张 KPI、每页最多 12 条异常清单。Fail 优先，再按均值差占规格范围比例、标准差比降序。
- 只有 Fail、Risk、Review Required 项进入详情；后两者显示为 `Need to check`。不适用、未评估、样本不足、无法计算、未执行单独计为排除项，不能当作 Pass。没有异常时仅输出一页摘要。
- 每个异常测项一页，固定 3 图 + 1 表：分 Site 次序、小提琴、全量直方图、Site 统计。次序和密度输入合计上限 5000 点；均值线仍取全量统计。
- Site 按真实编号显示，保留 S0；8 个以上 Site 的统计表分为左右两栏，最多支持 16 个 Site。Cg 固定 `--`；当前引擎没有独立的逐 Site Pass/Fail，因此表中标为 `Not classified`，样本不足单独标明。
- `Worst Site` 按所示均值差或标准差证据派生，保留并列 Site；Trigger 表达已保存的诊断分支。数值、规则、人工已保存备注合并到 Remark。长备注在正文显示可容纳部分，完整文本保留 PDF 批注。

Excel 配置：`config/release_site_diff_excel.json`。工作表为 `raw`、`Abnormal_Site_Diff`，首行深灰白字，冻结表头，保留数值型统计和备注来源批注。诊断末段为 **7 列**，不是 6 列。
