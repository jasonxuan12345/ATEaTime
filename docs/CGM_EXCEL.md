# Cgm Excel

与 Bin1 CPK Excel 相同，提供独立生成函数和 `release_cgm_excel` 调用入口。
页面右上角「导出」中可下载「CPK Excel (.xlsx)」和「Cgm Excel (.xlsx)」。
仅导出本次已计算并保存的结果。配置变化、观察备注未保存或处于示例模式时禁用。
后端按运行记录选择各模块自己的来源文件；Cgm 使用该次计算保存的 Bin1 CPK 支持值。
生成文件时不读取 STDF/Parquet、不遍历测量样本、不重算统计，也不复制样本引用。
重复下载复用同次结果、同一天、同备注版本生成的文件；保存或撤销备注后自动更新。
每份来源会话的 Excel 缓存最多 16 MB，旧计算结果移除时也会清理对应缓存。

接口：`GET /api/release-check/datasets/{运行所属文件ID}/runs/{运行ID}/reports/{cpk|cgm}.xlsx`。
文件名由后端根据对应模块的 STDF 元数据生成。结果过期、来源移除或不一致时拒绝导出。

```python
from deepyield.compute.release_cgm_excel_input import export_release_cgm_xlsx

# 使用页面导出的完整报告 JSON，保留当次 CGM 选样和 Bin1 CPK 来源。
output = export_release_cgm_xlsx("release-check-report.json", "reports")
print(output["path"])

# 或使用解析后的 Release Parquet，明确选择记录、Site 和测项。
output = export_release_cgm_xlsx(
    "data.wide_all.parquet", "other-reports",
    sites=[1, 2], test_columns=["1001_VDD"],
    # 可提供已保存的另一份 CPK 结果，沿用已有的严格测项匹配检查。
    # bin1_cpk_report_path="cpk-run.json",
)
```

- 文件名按示例为 `YYYYMMDD_DeviceID_Cgm_Report.xlsx`，日期取系统本地日期。
  DeviceID 从报告测试条件或 MIR 的 `part_type` 获取；缺失或包含多个产品时省略文件名中的产品部分，也可指定 `device_id`。
- 仅一个 `raw` Sheet；前三行、每 Site 七列，末四列为 `min Cg`、`Bin1 CPK`、`Result`、`Remark`。
  Site 升序，测项按 STDF 测试定义出现顺序。同一测项不同条件或规格保留独立行。
- 各 Site 使用 CGM 已选择的全部有效结果，而非只用 Bin1；样本标准差使用 `ddof=1`。
  `ExecCount` 为有效结果数，`FailCount` 与现有 CPK 报告一致，为有效结果中超出已知上下限的次数。
  只有一个有效样本时标准差及 Cg 留空；无有效样本的 Site 七格全空。
- 零标准差、等上下限输出文本 `NaN`；零标准差、不等上下限输出文本 `Infinity`。
  `min Cg` 保留 Infinity；任一 Site Cg 为 NaN 时结果为 NaN，不再写成 0。
- 固定报表规则在 `config/release_cgm_excel.json`：Cg 的 1.00 / 1.33 边界、
  CPK 的 2.0 接受条件和 1.67 预警边界，以及 `#74B230`、`#F0EA00`、`#EA0000` 三种颜色。
  Site Cg 和 min Cg 使用 1.00 / 1.33 边界；Bin1 CPK 独立按 `<1.67` 红色、
  `1.67≤CPK<2.00` 黄色、`CPK≥2.00` 绿色着色，缺失值不填状态色。
  除直接填色外，文件包含 Excel 条件格式规则。
- 全部有效 Cg 为 Infinity 时，`min Cg=Infinity` 为绿色且 Result 为 `Pass`。
  任一 Cg 为 NaN 时，`min Cg=NaN`、无 Cg 底色、Result 为黄色 `Need to check`。
  完全没有可计算 Cg 的行显示空白 min Cg、无 Cg 底色、Result 为黄色 `Need to check`。
- `Remark` 沿用页面中已保存的规则和知识库结论，包括已保存的人工最终备注。
  JSON 导出保留其中的最终备注；独立 Parquet 导出使用同一诊断函数生成说明。
  Remark 自动换行，单元格批注保留诊断来源及知识库版本。
- 这是指定 Excel 的展示判定；原有 CGM 分析状态及样本充分性检查保持原样。
  原始分析状态、来源、选样和 CPK 来源仍保存在单元格批注或工作簿属性中。
- 从 JSON 导出不会重算或改变选样。旧 JSON 若缺少部分 Site 统计，对应单元格留空；
  可重新运行分析，或改用已有 Parquet。输出文件已存在时拒绝覆盖。

页面中的 Excel / PDF 导出显示生成进度，可取消。DeviceID、温度等描述信息缺失不再阻止导出；不能唯一确定的产品名称也省略，不猜测其中一个。
