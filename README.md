# DataPlot (数据绘图分析与格式转换工具)

DataPlot 是一款基于 Python Tkinter 和 Matplotlib 开发的数据可视化与后处理工具，专为海量实验数据（如电池充放电测试、温度采集等）的快速展示、分析与格式转换而设计。

---

## 🌟 核心特性

1. **多维度交互式绘图**：

   * 支持多达 3 个 Y 轴数据同屏绘制，支持不同轴数据的线型（实线、虚线、点划线等）与颜色定制。
   * 支持鼠标缩放、拖拽及坐标数据悬停提示（Matplotlib 自带交互）。
   * 预设过滤选项，支持按“循环（Cycle）”和“工步（Step）”对海量数据进行快速过滤重绘。
2. **高 DPI 与 4K 屏幕自适应**：

   * 原生兼容 Windows 高 DPI 缩放，文字与控件自动缩放，无模糊感。
   * **动态图纸边距算法**：图表边距根据 Y 轴数量、画布像素大小及 DPI 自适应调整，防止多坐标轴刻度及标题在 4K/高分屏上被截断或留白过大。
3. **双轨数据读取引擎（极速与兼容并蓄）**：

   * **优先使用 Polars 引擎**：数据读取时，优先使用超高速 Rust 编写的 Polars 解析器进行加载，通过非 pyarrow 内存共享技术转换，保障大型文件（如 CSV、XLSX）瞬间载入。
   * **智能降级 Pandas 兼容机制**：如遇非 UTF-8（如中文 GBK、GB18030 编码的 CSV）或 Polars 不支持的复杂 Excel 格式（如含有图表工作表等），程序会自动静默降级切换至 Pandas (Calamine / openpyxl) 进行多编码兼容读取，保证 100% 稳定性。
4. **海量 CSV 极速转 Excel (xlsx) 工具**：

   * **超低内存设计**：采用 Polars 批读取流与 `xlsxwriter` 常驻物理内存模式 (`constant_memory=True`)，即便是数百万行的 GB 级别 CSV，也能在几秒钟内完成转换且不爆内存。
   * **自动切分工作表**：完美遵循 Excel 硬件单表最大 `1,048,575` 数据行限制，超限数据自动平滑切分至 `Sheet2`、`Sheet3` 等新工作表中。
   * **无缝工作流**：转换成功后，程序会自动将输入文件路径替换为新生成的 `.xlsx`，一键即可直接“计算”绘图。

---

## 🚀 快速开始

### 运行源码

1. **安装环境依赖**：

   ```bash
   pip install pandas polars openpyxl xlsxwriter python-calamine matplotlib
   ```
2. **运行程序**：

   ```bash
   python PlotPostProcessing.py
   ```

---

## 📦 打包与分发 (Windows EXE)

程序内置了完整的可执行程序打包与安装包制作脚本：

### 1. 编译为绿色版文件夹

我们提供了预设的 PyInstaller 打包脚本 `PlotPostProcessing_exe.py`，它会自动执行清理、排除冗余库并导出为 `--onedir` 格式：

```bash
python PlotPostProcessing_exe.py
```

运行后，生成的文件位于当前项目的 `dist/DataPlot/` 目录下。

### 2. 制作 Windows 安装包 (.exe 安装文件)

项目根目录中包含 [setup.iss](file:///e:/ProcedureCode/PythonFloefdPic/floefdpic4_Setup/setup.iss) 脚本，可使用 **Inno Setup** 编译器直接打开并构建。这会把 `dist/DataPlot/` 的内容封装为标准 Windows 安装向导，带有桌面快捷方式及开始菜单项。

---

## 📂 项目结构

* `PlotPostProcessing.py`：绘图与分析主程序的 GUI 源码。
* `PlotPostProcessing_exe.py`：PyInstaller 自动打包配置脚本。
* `setup.iss`：Inno Setup 安装包制作脚本。
* `icon.ico`：程序图标。
* `settings.json`：本地保存的配置项。
* `.gitignore`：Git 忽略配置文件，过滤掉了生成的二进制文件及本地大体积数据集。
