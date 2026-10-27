# 中文 → 英文需求清单

检查日期：2026-10-26。范围：当前前端 UI、图表、导出报告，以及这些功能显示的后端提示。已完成中文措辞调整、中文到英文翻译和页面语言切换；本轮实现与验证见第 7 节。

只为现有中文增加英文显示。已有英文不要求增加中文；字段名、状态码、规则 ID、计算公式不翻译。以下行号是首次检查时的定位起点，修改后请以当前代码为准。

## 1. 前端页面

以下文件均位于 `frontend/workstation/src/features/release-check/`。

| 文件 | 需要提供英文的内容 |
|---|---|
| `ReleaseCheckPage.tsx` | 主页面按钮、导入区、模块选择、计算/取消/清空、导出入口、示例提示、过期提示、错误兜底、离开未保存页面的确认框。包括带文件名、数量的动态句子。 |
| `FilePool.tsx:11` | 文件列表表头：文件名、阶段、温度、记录数、测项数、操作；未记录、移除、选样说明；文件用途和移除操作的无障碍名称。 |
| `SelectionPanel.tsx` | Site、测项、记录选择区的标题、搜索框、全选/清除、过滤、样本数设置及说明、选择数量、空状态。 |
| `ReviewWorkbench.tsx` | 结果总览、筛选、表格列名、异常项导航、状态说明、空结果、来源和结果提示。 |
| `EvidencePanel.tsx:10` | 测项详情、有效上下限、参与计算的记录数、均值/标准差、Site Diff 参数说明、Bin1 CPK 接受规则的判定说明、来源、加载/空状态、参与计算的测试记录和测试条件。 |
| `Diagnosis.tsx:6` | 项目诊断说明、自动生成待复核、本次依据与项目经验来源、历史参考、适用范围未确认、来源、知识库和关联分析数据说明。诊断正文另见后端与已有英文部分。 |
| `model.ts:11` | 模块中文说明；状态名称、严重程度；选样校验、请求失败兜底；Site Diff 原因名称映射；示例记录名和示例警告。 |
| `useReleaseOperation.ts` | 操作失败、状态读取失败等用户可见兜底提示。后端返回的阶段文字也要处理。 |
| `reportExport.ts:25` | 导出失败、结果过期、下载地址无效。 |
| `tableSort.tsx` | 排序操作的中文无障碍提示。 |

### 图表分析

以下文件均位于上述目录的 `charts/` 子目录。

| 文件 | 需要提供英文的内容 |
|---|---|
| `AnalysisWorkspace.tsx:62` | 返回/上一个/下一个、分析区域、加载/失败/重试、来源切换提示、未保存确认、图组控制、样本与明细说明；导出图片的标题和说明也在此拼接。 |
| `AnalysisNavigation.tsx` | 测项/测项组导航、搜索、筛选、分组与计数、空状态。 |
| `AnalysisContext.tsx:6` | 各图的阅读指引、当前依据与来源、测项组概览、规则/单位/未记录、有效记录数和选样说明。 |
| `ChartPairControls.tsx` | 图组选择、显示选项、Site 与规格范围等控制文案。 |
| `ChartSlot.tsx` | 图表容器的提示和无障碍名称。 |
| `ChartSummary.tsx` | 图表摘要、统计和缺失说明。 |
| `chartScenarios.ts:6` | 图名、场景标题、分析建议、无数据/无法绘制原因。例如直方图、分 site 箱线图、重复性异常、均值贴限。 |
| `FamilyHeatmap.tsx` | 测项组 Cg 表标题、操作、图例/悬停、缺失数据与限制说明。 |
| `ObservationPanel.tsx` | 观察标签、补充 Remark、建议检查、最终 Remark、备注复核状态、批量应用、覆盖、保存/撤销/预览、会话保存说明。 |
| `ReleasePlot.tsx:38` | 框选、复位、放大、参与计算的记录数、抽样显示、晶圆排除记录、箱线离群点和 Bin1 CPK 接受规则的判定说明。 |
| `OverviewThumbnail.tsx:14` | 问题缩略图的无障碍名称。 |
| `SiteStatistics.tsx:9` | 各 site 统计、状态列、Site Mean − Grand Mean 的范围说明。Mean、Std、Cg 等原有英文保留。 |
| `SiteSequencePlot.tsx:12` | 分 site 测试次序图标题、无障碍名称、测试次序和均值/规格线说明。 |
| `options.ts:27` | 轴名、系列名、悬停内容、频数、记录/次序、Std 参考、Cg 达标、须线、离群点、Wafer 显示行列和原始坐标说明。 |
| `sampleShapes.ts:57` | Q-Q 图的“正态分位”轴名。 |
| `siteDeviation.ts:26` | 悬停中的“次序”、图中文字“点”。 |
| `siteSequence.ts:26` | 悬停中的次序/记录、缺少单位时的“测量值”。 |
| `workbenchOptions.ts:17` | “核密度（样本）”系列名称。 |
| `types.ts:43` | “未知 site”和观察特征的中文名称；同一映射中的英文 Remark 片段已存在，可以保留/复用。 |
| `export.ts:19` | 图形/图片导出失败、浏览器无法生成图组。 |

### Outlier

以下文件均位于 `outliers/` 子目录。

| 文件 | 需要提供英文的内容 |
|---|---|
| `OutlierSettings.tsx` | 来源选择、偏离阈值、每组最少样本、选样条件和适用范围说明。 |
| `OutlierAnalysis.tsx` | 分析页面、筛选/导航/排序、来源问题、异常候选记录数量、过期/空结果、记录详情区、导出与操作提示。 |
| `CandidateTable.tsx` | 异常候选记录列表列名、筛选和翻页、选择记录、数量和空状态。 |
| `OutlierEvidence.tsx` | 芯片与检出异常的测项、参考样本统计、实测/偏离、参与计算的记录数、无法评估原因、来源、空状态。 |
| `ReferenceChart.tsx:34` | 实测值/偏离程度（稳健σ）切换、筛选上下限、超出图示范围的点、区外规格、异常候选点悬停、参考区间解释、分组翻页、无法计算筛选范围的提示。 |
| `chartOptions.ts` | 图表标题、轴/图例/悬停、参考区间和异常候选记录解释文字。 |
| `OutlierRemark.tsx:10` | 观察编辑、状态、保存/撤销/批量应用、英文预览标签；Excel（.xls）表头和导出的观察标签/备注复核状态。 |
| `model.ts:46` | 参数校验、无匹配结果/未计算/不适用/样本不足/部分未评估/发现异常候选记录等显示状态和汇总原因。 |
| `display.ts` | 通用显示兜底中的中文。 |

## 2. Report 和导出

| 输出 | 文件与位置 | 结论 |
|---|---|---|
| 图组图片 | `charts/AnalysisWorkspace.tsx:139`；`charts/export.ts`；各图表 option 文件 | 标题、完整范围/当前视图、来源/测项/规则/图组说明、抽样说明、未记录观察，以及图里的轴名/图例/悬停数据名称需要英文。不能只翻译页面按钮。 |
| 测项组 Cg 表 CSV | `charts/matrixExport.ts` | 固定表头已经是英文，无需改为双语。`Reason` 等动态内容继续检查其上游来源。 |
| Outlier 观察 Excel（.xls） | `outliers/OutlierRemark.tsx:13` | 名称、模块、观察标签、补充/最终 Remark、建议检查、备注复核状态、英文 Remark 等中文表头需要英文；标签和状态值也需要英文。 |
| 观察记录 Excel | `app/release_chart_notes.py:127`；`app/release_remark_service.py:8` | 表头已是英文；Observed features 使用 `OBSERVATIONS[t][0]`，仍输出中文。Review state 及默认“自动生成，待复核”也会输出中文。英文 Remark 拼接片段已有。 |
| CPK Excel | `deepyield/compute/release_cpk_excel.py:165`；`app/release_excel_service.py` | 固定工作表/表头无需整体翻译；需处理中文导出进度、校验失败。另核对附带诊断、注释和保存的状态文字。 |
| CGM Excel | `deepyield/compute/release_cgm_excel.py:158`；`app/release_excel_service.py` | 同上。固定英文内容保持，中文进度和异常提示需要英文。 |
| CPK PDF | `deepyield/compute/release_cpk_pdf.py:535`；`app/release_pdf_service.py` | 固定报告文字已是英文；中文主要在准备/分页/封装进度及错误提示。动态 Remark 来自诊断或用户输入，需区分来源。 |
| CGM PDF | `deepyield/compute/release_cgm_pdf.py:477`；`app/release_pdf_service.py` | 同上。 |
| Outlier Excel | `app/release_outlier_service.py:58` | 固定表头、自动诊断 Remark 已使用英文；需处理生成进度和报告过期等中文错误。 |
| JSON 导出 | `ReleaseCheckPage.tsx`、`outliers/export.ts`；`app/release_check_service.py` 等数据提供方 | JSON 字段名和状态码不翻译，但当前 UI 汇总的中文 label/reason、后端 status_label/warnings、中文备注复核状态等会被原样保存。应在生成这些展示文字的位置处理。 |

## 3. 后端返回给页面的中文

这些文件虽然不在前端目录，但返回值会显示在 UI 或进入报告，属于本次需求。

| 文件 | 中文内容 |
|---|---|
| `app/release_check_service.py:34` | REASONS、LABELS、具体判定原因、缺失名称兜底、部分检查未匹配、导入错误、读取/计算阶段。是页面判定中文的重要来源。 |
| `app/release_chart_service.py:60` | `sample_type` 的“测试记录”、`selection_policy` 的选样说明及图表相关校验提示。 |
| `app/release_chart_notes.py:32` | 保存/批量/撤销/预览校验失败；写入/封装 Excel 进度；观察导出的中文标签和状态。 |
| `app/release_remark_service.py:8` | 观察标签的中文名称。每个标签已有英文描述，但应注意“短标签”和“Remark 句子片段”的用途不同。 |
| `app/routers/release_check.py:75` | 上传和来源选择错误；备注复核状态的请求字段约束。 |
| `app/release_operations.py` | 操作编号冲突、操作已结束。 |
| `app/release_export_cache.py:35` | 等待正在生成的报告、报告已生成准备下载、下载已过期。 |
| `app/release_excel_service.py` | Excel 导出准备/完成、报表类型和来源校验。 |
| `app/release_pdf_service.py` | 模块缺失、报告与来源不一致。 |
| `app/release_outlier_service.py` | 结果过期、找不到分组、Excel 生成进度。 |
| `deepyield/compute/release_cpk.py:23` | `status_label`、单位/测试条件缺失、样本充分性警告、选样及输入校验。 |
| `deepyield/compute/release_cgm.py:23` | `status_label`、各 site 最少样本/CPK 匹配警告、支持报告和规则校验。 |
| `deepyield/compute/release_mmsa.py:30` | `status_label`、Site Diff 支持报告/样本/计算警告、规则与输入校验。 |
| `deepyield/compute/release_outliers.py:181` | 计算阶段、异常候选记录/适用范围/独立筛选等警告、样本不足及输入校验。 |
| `deepyield/compute/release_charts.py:40` | 分箱/点数配置校验、功能测试不适用连续图、没有有效测量值。 |
| `deepyield/compute/release_chart_samples.py:91` | 样本过期、不一致、重复引用、多个 head 共用 site 汇总的警告。 |
| `deepyield/compute/release_chart_wafer.py:41` | 产品坐标配置说明、最后一条记录显示策略、缺少晶圆身份/坐标。 |
| `deepyield/compute/release_stdf.py` | 解析器缺失/启动失败/超时/格式错误，解析 STDF 和整理数据进度。仅独立 CLI 使用的提示可留到 CLI 范围再做。 |
| `deepyield/compute/release_control.py:20` | 默认“准备处理”、操作取消提示。 |
| `deepyield/compute/release_numeric.py:13` | Cg 计算口径更新、要求重新计算后再导出的校验提示。 |
| `deepyield/compute/release_cgm_mr.py:15` | MR 公差、测量序列与相邻差分数值异常提示。 |
| `deepyield/compute/release_compact.py:87` | 计算字段类型不受支持的异常提示。 |
| 四个 CPK/CGM PDF/Excel 生成文件 | 见报告表：校验异常及生成进度会回到页面，不能因报告正文已英文而遗漏。 |

## 4. 已有英文和范围边界

- `config/defaults/release_remark_templates.json` 的自动 Remark 模板、`release_knowledge.json` 和 `release_cgm_knowledge.json` 的现有内容没有检出中文；`release_remarks.py`、`release_cgm_diagnosis.py` 已有英文诊断文本，不要求反向增加中文。
- CPK、CGM、Site Diff、Outlier、Test ID、Mean、Std、Cg、CPK、LSL/USL、Wafer Map 等已有英文名称和缩写保留。
- 规则状态如 `PASS`、`FAIL`、`REVIEW_REQUIRED`，reason code、规则 ID、JSON key、数据匹配键保留原值；只为面向用户的中文提供英文。
- 用户输入的 Remark/建议、原始 STDF 测项名、文件名、产品名和测试条件属于用户数据，不自动翻译。
- `model.ts` 内置示例、`charts/demo.ts` 和 `features/release-preview/demo.ts` 含中文。主页面默认加载示例，因此实际展示的示例中文也应覆盖；预览专用内容按是否继续使用决定。
- `outliers/testFixtures.ts`、`*.test.*`、`tests/` 是测试素材，不计为产品翻译内容。本次已同步调整受中文改名影响的素材和断言。
- README、开发文档、代码注释、独立 CLI/skill 说明不计入本次 UI/report 范围。

## 5. 实现时需特别注意的两处

1. 备注复核状态目前直接使用中文作为值：`OutlierRemark.tsx`、`ObservationPanel.tsx` 以及 `app/routers/release_check.py:243` 的 `Literal['待复核', '已确认观察', '待跟进']`。不能只把前端提交值替换成英文，否则可能请求校验失败；应区分显示文字与保存值，或一起调整协议。
2. `release_cgm.py:234`、`release_mmsa.py:374` 用 `"最小样本" not in w` 过滤警告。翻译这些警告时必须同步处理这类文字依赖，避免英文模式下重复/遗漏警告。

初次清单验证方式：静态搜索中文并阅读页面、后端返回和报告写入位置，未逐条执行所有异常分支。本次措辞修改的验证结果见下节。


## 6. 已完成的中文措辞调整（2026-10-26）

上一轮只改中文显示、错误/警告说明、示例文字及相关测试断言，当时尚未增加英文词典或语言切换。以下保留中文调整记录；英文翻译已在本轮完成。

| 原说法 | 当前说法及含义 |
|---|---|
| 规格吞噬率 | Site 均值差占规格范围比例；说明为 Site 均值极差 ÷（USL−LSL）。 |
| 带宽 | Site 均值极差，即最大与最小 Site 均值之差。 |
| 均值偏置 S₂S_shift | Site 均值差（σ）；说明为 Site 均值极差 ÷ 总体标准差。 |
| 方差不齐 S₂S_var | Site 标准差比；说明为最大 Site 标准差 ÷ 各 Site 标准差的中位数。原计算使用标准差比，未修改公式。 |
| 最差 / 中位 | 最大 Site 标准差 / 标准差中位数。 |
| 位置与散度 | 各 Site 的测量水平和数据分散程度。 |
| 家族 / 家族概览 | 测项组 / 测项组概览；Cg 表注明按测项名称前缀及当前条件分组。 |
| 家族 Cg 矩阵 | 同组测项的各 Site Cg；其他入口称测项组 Cg 表。 |
| 测项证据 / 芯片证据 | 测项数据与判定依据 / 异常候选记录详情。 |
| 样本引用 / 有效样本 | 参与计算的测试记录 / 参与计算的记录数；不改称芯片数。 |
| CPK 接受 / 支持的 CPK | 按 Bin1 CPK 接受规则判为通过 / 接受规则所用的 Bin1 CPK。 |
| 诊断 gate | Site Diff 检查触发条件。 |
| 候选 | Outlier 场景明确为异常候选记录或异常候选点；备注批量选择场景改为所选测项。 |
| 命中测项 | 检出异常的测项；句子改为该记录有 X 个测项被列为异常候选。 |
| 偏离倍数 / 最大偏离 | 偏离程度（稳健σ）/ 最大偏离程度（稳健σ）；设置和详情说明相对同组中位数、以稳健标准差为单位。 |
| 基准 | 根据位置改为参考样本、参考样本统计或筛选范围。 |
| 极值溢出区 | 超出图示范围的点。 |
| 观察状态 / 记录状态 | 备注复核状态。 |
| site 间系统偏移成立 | Site 均值差达到所设异常阈值。 |
| 个别 site 突发性高波动成立 | 个别 Site 的标准差明显高于其他 Site。 |
| 无 site 差异证据，需数学形态细分判定 | 当前指标未检出明显 Site 差异，需查看分布图，检查是否偏宽、多峰或接近规格限值。 |
| 疑似硬件故障、测试方法问题、trim 双峰等 | 先描述 Cg、CPK 或分布结果，再提出接触、硬件、方法、规格限值或 trim 的检查建议。 |

补充核对：Outlier 观察记录当前实际导出为 Excel XML 格式的 .xls 文件，已更正初次清单中的 CSV 描述。测项组 Cg 表仍导出 CSV。

同步范围：结果表及详情、图表说明与图片导出、Outlier 设置/列表/图表/观察 Excel（.xls）、后端中文判定原因与警告、内置示例、受影响测试。

保持不变：计算公式、规则阈值、JSON 字段名、分组键、原有英文、用户输入；观察状态值仍为“待复核 / 已确认观察 / 待跟进”，接口约束不变。“最小样本”警告过滤逻辑未改。

验证结果：

- 前端类型检查通过（`npm run typecheck`）。
- 前端 19 个测试文件、140 项测试通过。沙箱默认临时缓存目录出现权限错误，改用可写临时目录运行；图表库首次加载超过默认 5 秒，测试等待时间调整为本次命令的 20 秒，未修改项目测试配置。
- 后端 CPK、Site Diff、Outlier、图表计算相关 145 项测试通过；Site Diff API 7 项测试通过。最后统一零标准差警告文字后，重新运行 Site Diff 计算测试，33 项通过。API 测试在沙箱内创建本地事件循环套接字时阻塞，在沙箱外运行后通过。
- 前端生产构建通过（`npm exec vite build`），已更新 `frontend/dist`。构建仍提示图表依赖文件体积较大。
- 已检查旧词残留、前后端判定说明一致性、中文状态值与“最小样本”过滤逻辑。未逐个打开页面做人工布局检查，未逐份生成 PDF/Excel 报告。

## 7. 已完成的中英文切换与翻译（2026-10-26）

页面顶部新增“中文 / English”按钮，默认中文，记住用户选择。切换语言不重新导入文件、不重新计算，也不清除选样、当前测项或正在编辑的备注。页面语言标记和无障碍名称同步更新。

### 翻译覆盖

| 范围 | 当前行为 |
|---|---|
| 主页面、导入设置、选样、结果列表、详情、导航、排序、空状态、确认框 | 为原有中文提供英文显示；原有英文和缩写保留。 |
| CPK、CGM、Site Diff、Outlier 图表 | 标题、轴名、系列、悬停、统计说明、参考范围、观察标签随语言更新。 |
| 后端原因、警告、错误和处理进度 | 保留内部中文原文，在显示或英文报告输出时翻译；不改计算用的字符串、规则和已保存对象。 |
| 图组图片 | 标题与说明使用当前语言；长标题和说明按画布宽度排列，普通英文单词不会被任意拆开。 |
| 测项组 CSV、Outlier 观察 Excel（.xls）、图表上下文和报告 JSON | 系统生成的说明、标签、复核状态使用当前语言；数据字段名、状态码、规则 ID 保留。 |
| 服务端观察 Excel | 英文导出翻译 Observed features 和 Review state；用户建议和最终 Remark 保持原文。 |
| CPK/CGM PDF、Excel 和 Outlier 诊断 Excel | 已有固定英文标题、表头和自动 Remark 保持英文；英文输出中的系统说明及诊断注释按需翻译。 |
| 下载缓存 | 中文和英文分别缓存，防止复用另一语言的观察记录文件；下载保留生成文件的语言标记。 |

用户输入的 Remark、建议、原始测项名、文件名、产品名、测试条件不自动翻译。观察状态提交值仍为“待复核 / 已确认观察 / 待跟进”，只翻译可见选项文字。CGM/MMSA 的“最小样本”警告过滤逻辑不变。

### 主要文件

- `config/i18n/zh-en.json`：前后端共用的中文到英文词典，当前 1069 条；包含动态数量、错误和进度模板。
- `frontend/workstation/src/i18n/index.ts`：语言选择、记忆、文本显示、报告副本翻译；不修改原始对象。
- `frontend/workstation/src/features/release-check/ReleaseCheckPage.tsx`：顶部切换按钮和报告导出。
- 第 1 节列出的前端组件与图表辅助文件：在显示文字的位置使用翻译函数；只在语言变化时重建图表显示配置。
- `deepyield/i18n.py`、`app/release_request_timing.py`：请求语言与报告展示字段翻译。
- `app/release_chart_notes.py`、`app/release_export_cache.py`、`deepyield/compute/release_export_workbook.py`：观察记录、报告缓存和诊断注释。
- `frontend/vite.config.ts`：开发模式允许读取共用词典目录，不增加新依赖。

### 显示调整

- 顶部按钮、设置页签、图表控制和观察标签按可用宽度排列，按钮文字保持完整。
- 窄窗口内将结果列表与图表上下排列；密集表格和参考范围图在各自区域滚动，不撑宽整个页面。
- 文件名和测项名保留完整单行，使用表格内滚动查看；说明文字按词自然换行。
- 展开设置和备注时不再覆盖图表；取消会裁切长说明的固定高度。
- Outlier 矩阵按记录数留出行高；修复晶圆图容器临时变为零尺寸时产生负半径的问题。
- 示例图的模拟文件名、lot 和 wafer 使用固定英文标识；真实输入文件与身份信息不改。

### 本轮验证

- 前端类型检查、20 个测试文件中的 147 项测试、生产构建通过；已更新 `frontend/dist`。词典扫描未发现遗漏的中文翻译调用。
- 后端计算与备注相关 164 项、报告导出与翻译相关 186 项、API 相关 21 项测试通过。
- 浏览器：Microsoft Edge，无头模式，使用生产构建。覆盖中文、英文及 390、768、1024、1280、1440、1920 像素窗口，共 156 个界面状态，包括总览、三个统计模块、Outlier 分布/趋势、展开备注、四种模块设置、展开选样和导出菜单。未发现整页横向溢出、按钮裁切、同组控件重叠或页面脚本错误。
- 浏览器测试包含长文件名、长测项名、极端 Outlier 分数；英文界面检查中未检出应翻译的中文。真实输入中的中文按原文保留。
- 开发模式：临时启动 Vite，确认共用词典可读取，页面可切换到英文，检查结束后关闭测试服务器。
- 单元测试覆盖语言切换不清除输入、中文状态提交值不变、插值不翻译文件名、词典占位符一致、英文图表文字、图片排版、语言缓存隔离和英文观察 Excel。
- API 测试覆盖并发请求的语言隔离、英文错误/进度、JSON 只翻译展示字段，以及原报告和用户文字保持不变。
- 测试入口：`frontend/workstation/src/i18n/i18n.test.tsx`、`tests/test_compute/test_i18n.py`、`tests/test_api/test_release_i18n.py`、`tests/ui/check_i18n_layout.py`。
- 浏览器检查结果与截图：`C:/Users/xjn3sgh/.codex/visualizations/2026/10/26/01a17629-78ce-7021-bbeb-83a873534896/i18n-work/ui-final/result.json`。

测试使用示例和模拟输入检查布局，并用现有计算、API、PDF/Excel 测试检查报告行为；没有遍历任意长度的用户输入或每一种系统字体与浏览器缩放设置。

### 按需 Parquet 导出补充

默认导入改为只准备计算数据；“保存 Parquet”才从保留的原始 STDF 生成完整文件。新增准备计算数据、生成完整 Parquet、原文件缺失和取消提示均已加入中英词典。生成时使用现有可换行状态区域与取消按钮，禁用重复导出。

### Site Diff / Outlier 报告补充

新增两个模块的 Excel/PDF 入口。固定报告字段沿用现有英文报告风格；原始条件、测项名、文件名和人工备注保持原文。报告生成使用现有可取消进度区域。Outlier 结果关联或备注同步失败提示已加入中英词典。
