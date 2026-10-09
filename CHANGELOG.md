# DataPlot 更新日志 (Changelog)

本项目遵循语义化版本规范，所有重大更新、功能特性和 Bug 修复记录如下：

---

## [v1.1.1] - 2026-10-09

### 🐛 核心修复 (Bug Fixes)
- **绘图作用域 NameError 修复**：在 [DataPlot_plot_engine.py](file:///e:/Antigravity/DataPlot/DataPlot_QT/DataPlot_plot_engine.py) 中引入全局 `Var` 包装类与 `_is_axis_title_visible` 方法，解决选择数据时触发的 `NameError: name 'Var' is not defined`。
- **坐标轴标题安全读取**：修复非 general 模式下 `self.x_title.get()` 导致的 `AttributeError: 'QLineEdit' object has no attribute 'get'` 隐患，支持动态识别 `text()` 与 `get()`。
- **Y 轴范围与标题显隐解耦**：解决在 Y 轴范围 Min/Max 为空（使用自动缩放）时，点击“显示/隐藏”或修改标题未刷新界面的问题。
- **图例清理异常防御**：封装 `_clear_legends` 安全清理辅助函数，彻底避免 Matplotlib 在特定多轴图例移除时抛出的 `ValueError: list.remove(x): x not in list` 内部异常。
- **数据转换回调闭包修复**：解决 [DataPlot_data_loader.py](file:///e:/Antigravity/DataPlot/DataPlot_QT/DataPlot_data_loader.py) 异常处理中在 `QTimer.singleShot` 回调内捕获已被 Python 销毁的异常变量 `e` 引起的闭包作用域错误。
- **模块导入与缺省参数统一**：
  - [DataPlot_battery_math.py](file:///e:/Antigravity/DataPlot/DataPlot_QT/DataPlot_battery_math.py) 顶层补充显式 `import datetime`；
  - 统一 `parse_legend_x_positions` 缺省水平图例坐标为 `[0.0, 0.3, 0.7]`。

---

## [v1.1.0] - 2026-09-30

### 🚀 新增功能与界面重构 (Features)
- **坐标轴标题显隐切换**：在 X 轴与 Y 轴范围配置行尾增加独立的“显示/隐藏”开关按钮，支持单独控制每个坐标轴标题的显示状态。
- **图例位置调节优化**：
  - 图例垂直位置由输入框改为紧凑调节控件；
  - 增加水平图例位置内嵌折叠面板，支持滑块滑动精细调节 X1/X2/X3 对应图例锚点；
  - 支持一键重置默认图例位置 (`0.0, 0.3, 0.7`)。
- **配置持久化增强**：在 [settings.json](file:///e:/Antigravity/DataPlot/DataPlot_QT/settings.json) 中加入各轴标题显隐状态 (`x1_title_visible` ~ `y3_title_visible`) 的保存与恢复。

---

## [v1.0.9] - 2026-09-21

### 🚀 优化与扩展 (Improvements)
- **FLOEFD 仿真数据处理**：增强仿真数据清洗与极速 Excel 导出逻辑。
- **通道列表增强**：列表项右键支持智能重命名与自增命名规则。
- **独立多 X 轴支持**：General 模式下扩展支持独立多 X 轴数据序列。
- **布局重构**：循环对比布局对齐优化，默认时间标题更名为 `Time/s`，X 轴配置所在行按列宽自适应均匀分配。

---

## [v1.0.8] - 2026-09-10

### 🚀 电池配置与筛选增强
- 在电池配置面板增加工步时间与循环时间筛选功能 (`tmin` / `tmax`)。
- 支持正向与反向时间区间筛选模式。
- 修复早期版本中识别的所有核心数据加载与计算 Bug。

---

## [v1.0.7] - 2026-07-28
- 首个稳定多功能发布版本，支持多 Y 轴绘图、电池充放电曲线分析与格式转换。
