# ATEaTime

一杯茶的时间，看懂 ATE 测试数据

本地运行的 ATE 测试数据分析工具。目前支持 STDF 导入、Bin1 CPK、CGM、Site Diff、异常值分析、交互图表和报告导出。

导入时只准备分析所需的计算数据，不生成 Parquet。原始 STDF（包括 `.gz`、`.bz2`）保留在当前会话的临时目录中；点击“保存 Parquet”时才重新调用解析器生成包含全部执行记录的文件。生成成功后，再次下载复用该文件；删除或过期的会话会释放原文件和导出文件。详见 [导入与 Parquet 导出](docs/RELEASE_IMPORT.md)。

源码仓库不包含测试数据、项目经验表、私有产品配置或预编译程序。首次使用需要构建前端和 C++ 解析器。当前构建步骤面向 Windows x64，其他平台尚未验证。

## 准备环境

安装 Node.js 22.12+ 或 24、Conda，以及 Visual Studio 2022 或更新版本的“使用 C++ 的桌面开发”组件。也可以安装带有此组件的 Build Tools。原生解析器需要 MSVC x64、CMake、Ninja、Arrow/Parquet 和 bzip2 开发文件。

打开“x64 Native Tools Command Prompt for VS”。确认这个窗口可以运行 `conda`、`node`、`npm` 和 `cl`，然后进入项目根目录。以下命令均在这个窗口执行。

```bat
conda create -n ateatime -c conda-forge python=3.11 pyarrow=21.0.0 bzip2 cmake ninja
conda activate ateatime
python -m pip install -r requirements.txt
```

Conda 提供 Arrow/Parquet 和 bzip2 的头文件、链接库及 DLL。`pyarrow` 必须保持为 21.0.0。解析器和 Python 服务需要使用同一个环境。代码中的 `deepyield` 是本项目的包名，不需要安装其他项目。

## 构建前端

在项目根目录执行：

```bat
cd frontend
npm ci
npm run build
cd ..
```

构建结果保存在 `frontend/dist/`。日常启动服务不需要 Node.js。更新前端源码后，需要重新构建。

## 构建原生解析器

保持 `ateatime` 环境激活，在项目根目录执行：

```bat
cmake -S cpp/stdf_parser -B build/stdf_parser_msvc_ninja -G Ninja -DCMAKE_CXX_COMPILER=cl -DCMAKE_BUILD_TYPE=Release
cmake --build build/stdf_parser_msvc_ninja --parallel
```

CMake 从 `CONDA_PREFIX` 查找依赖。程序保存在 `build/stdf_parser_msvc_ninja/stdf2parquet.exe`，需要的 DLL 会复制到同一目录。保留这个目录中的 DLL。

更换编译器或 Python 环境时，使用新的构建目录，或先备份并移走旧的构建目录。修改 Arrow/pyarrow 版本后，需要重新构建解析器。

如果 CMake 提示找不到 `cl`，重新打开 Visual Studio 的 x64 开发命令窗口。如果提示找不到 Arrow 或 bzip2，确认已激活上述 Conda 环境，且 `CONDA_PREFIX` 指向该环境。

## 启动和关闭

完成两项构建后，在项目根目录执行：

```bat
python deploy/start_release_check.py
```

浏览器会打开 `http://127.0.0.1:8012/release-check`。如需其他端口，使用 `python deploy/start_release_check.py --port 8013`。不自动打开浏览器时，追加 `--no-browser`。

关闭服务时执行：

```bat
python deploy/stop_release_check.py
```

启动时使用其他端口，关闭时也要传入相同的 `--port`。关闭命令窗口不会停止后台服务。

也可以在设置 `DEEPYIELD_PYTHON` 指向上述环境的 `python.exe` 后，双击 `start_release_check.bat` 和 `stop_release_check.bat`。已有匹配的原生依赖时，`setup.bat` 可以创建仅含 Python 依赖的 `.venv`，但它不会构建前端或安装原生开发文件。

## 通用默认值和可选私有配置

默认读取 `config/defaults/`。经验库为空，图表没有产品专用前缀分组。CGM 提示只描述当前统计结果，不根据测试名称推断硬件故障或项目结论。晶圆图使用原始坐标方向，默认设备文件只包含一个停用的虚构示例。

私有配置需要显式指定目录。在启动服务前执行：

```bat
set "ATEATIME_PRIVATE_CONFIG=%CD%\config\private"
python deploy/start_release_check.py
```

先自行创建该目录，再放入所需的配置文件。支持的文件如下：

- `release_knowledge.json`：按测试编号及名称匹配的历史参考。
- `release_cgm_knowledge.json`：按名称模式匹配的历史参考。
- `release_remark_templates.json`：文字模板。
- `release_chart_families.yaml`：图表分组前缀。
- `device_import.yaml`：产品及晶圆图显示配置。

每个同名文件整体替换默认文件，不合并字段。目录内缺失的文件继续使用通用默认值。格式错误或不支持的版本会报错。配置修改后重启服务。历史参考用于人工复核，不改变计算结果或验收状态。

未设置环境变量时，程序不会自动读取 `config/` 根目录和原设备目录中的私有文件。`config/private/` 已加入 Git 忽略规则，私有经验和业务数据不随源码发布。

测试中的历史参考、产品名和测量数据均为虚构示例。报告状态仍由 `config/analysis_rule/` 中的配置决定，CGM Excel 联合判定使用 `config/release_cgm_excel.json`。

## 开发和测试

在上述环境中安装测试依赖并运行检查：

```bat
python -m pip install -r requirements-dev.txt
python -m compileall -q app deepyield
python -m pytest tests/test_api tests/test_compute tests/test_skills -q
ctest --test-dir build/stdf_parser_msvc_ninja --output-on-failure
cd frontend
npm run test
npm run build
cd ..
```

前端开发可在 `frontend` 中执行 `npm run dev`。默认代理本机 8012 端口。其他端口可设置 `DEEPYIELD_API_URL`。

## 数据和文档

服务只监听本机 `127.0.0.1`。上传数据使用内存和系统临时目录，没有数据库，也不恢复历史会话。观察记录和计算结果需要及时导出。运行日志保存在 `runtime/release-check/`。

当前限制见 [OPEN_ISSUES.md](docs/OPEN_ISSUES.md)。导出格式见 [CGM_EXCEL.md](docs/CGM_EXCEL.md)、[CGM_PDF.md](docs/CGM_PDF.md) 和 [CPK_PDF.md](docs/CPK_PDF.md)。

计算内存、索引缓存、页面精简响应及合成数据性能对照见 [计算性能说明](docs/RELEASE_COMPUTE_PERFORMANCE.md)。
