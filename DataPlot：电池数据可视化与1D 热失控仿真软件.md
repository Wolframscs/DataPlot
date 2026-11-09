# DataPlot：电池数据可视化与1D 热失控仿真软件


## 前言
将[ 1D FVM 大面加热膜恒功率加热热失控仿真代码 ](https://mp.weixin.qq.com/s/InlsbQ7rsZCMqKlpG48WBg#:~:text=%E6%B1%9F%E8%8B%8F-,%E9%94%82%E7%A6%BB%E5%AD%90%E7%94%B5%E6%B1%A0%E4%BB%BF%E7%9C%9F%2D1D%20FVM%20%E5%A4%A7%E9%9D%A2%E5%8A%A0%E7%83%AD%E8%86%9C%E6%81%92%E5%8A%9F%E7%8E%87%E5%8A%A0%E7%83%AD%E7%83%AD%E5%A4%B1%E6%8E%A7%E4%BB%BF%E7%9C%9F%EF%BC%88Python%E4%BB%A3%E7%A0%81%EF%BC%89,-%E5%89%8D%E6%96%87%E9%93%BE%E6%8E%A5)集成到了数据处理软件DataPlot中，并且增加了基于大面加热热失控触发实验与ARC 绝热热失控实验的参数辨识功能，可基于实验数据参数辨识拟合用于描述热失控反应动力学方程：
$$
\frac{dc_k}{dt}=-A_{k}\exp \left( -\frac{Ea_{k}}{RT}\right) c_{k}^{n_k}(1-c_{k})^{m_k}
$$
中的模型参数。式中各物理量与模型参数的定义如下：
- **$k$**：副反应反应编号（$k = 1, 2, \dots, N_{\text{rxn}}$），通常对应电芯内部划分的各阶段典型副反应（如 $k=1$ 对应 SEI 分解，$k=2$ 对应负极-电解液反应等）。
- **$c_k$**：第 $k$ 个副反应参与反应物的**无量纲归一化剩余浓度**（或未反应活性物质转化率份额），$c_k \in [0, 1]$；初始未反应状态下通常取 $c_{k,0} = 1.0$。
- **$t$**：反应时间（Time），单位为 $\mathrm{s}$。
- $\dfrac{\mathrm{d}c_k}{\mathrm{d}t}$：第 $k$ 个副反应物浓度的瞬时变化率（反应消耗速率），单位为 $\mathrm{s^{-1}}$。
- **$A_k$**：第 $k$ 个副反应的**指前因子**（Pre-exponential Factor / 频率因子），表征分子碰撞活化概率，单位为 $\mathrm{s^{-1}}$。
- **$Ea_k$**：第 $k$ 个副反应的**表观活化能**（Apparent Activation Energy），表征引发副反应所需克服的能量势垒，单位为 $\mathrm{J/mol}$。
- **$R$**：摩尔气体常数（Ideal Gas Constant），取值 $8.314\,\mathrm{J/(mol\cdot K)}$。
- **$T$**：材料或控制体单元的**绝对热力学温度**（Absolute Temperature），单位为 $\mathrm{K}$（计算时满足 $T = T_{\text{cell}}(^\circ\mathrm{C}) + 273.15$）。
- **$n_k$**：第 $k$ 个副反应的**反应级数**（Reaction Order），反映反应速率对当前未反应物剩余浓度 $c_k$ 的依赖敏感度，无量纲（$-$）。
- **$m_k$**（或后一项指数）：第 $k$ 个副反应的**自催化指数**（Autocatalytic Exponent），表征反应生成物或中间产物对副反应继续进行的自催化加速效应，无量纲（$-$）。当 $m_k = 0$ 时，模型简化为传统的一级/多级非催化热分解动力学方程。在自催化动力学通用扩展形式（Sestak-Berggren 模型 / Prout-Tompkins 扩展型）中，未反应物反应级数项与产物自催化项指数通常分别记为 $n_k$ 与 $m_k$（当 $m_k = 0$ 时退化为经典 $n$ 级反应）。若采用对称指数形式则 $m_k = n_k$。


## 选项卡-Simulation
包含三种模式：1D 热失控仿真计算、1D TR 动力学参数辨识、ARC 热失控动力学参数辨识
![](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422005.png)

### 1D 热失控仿真计算
 仿真计算逻辑与[锂离子电池仿真-1D FVM 大面加热膜恒功率加热热失控仿真（Python代码）](https://mp.weixin.qq.com/s/InlsbQ7rsZCMqKlpG48WBg) 中一致。
![DataPlot：电池数据可视化与1D 热失控仿真-20](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422006.png)
热失控反应动力学参数dc中可根据具体需求添加/删除反应，并可直接将参数从xlsx中复制/粘贴到面板中，如：
```python
# 参数示意
SEI	1.667000e+14	135080.00	157.00	1.000	0.000	1.000
Anode+ELE	2.500000e+13	135080.00	214.00	1.000	0.000	1.000
Binder	5.140000e+15	274000.00	155.00	1.000	0.000	1.000
Cathode+ELE	2.000000e+08	103000.00	194.70	1.000	1.000	1.000
```

在仿真图表视图切换与数据导出选项卡，可选择绘制不同类型的数据对比图，图形与[锂离子电池仿真-1D FVM 大面加热膜恒功率加热热失控仿真（Python代码）](https://mp.weixin.qq.com/s/InlsbQ7rsZCMqKlpG48WBg) 中一致。如：
![DataPlot：电池数据可视化与1D 热失控仿真-26](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422007.png)


| ![DataPlot：电池数据可视化与1D 热失控仿真-41](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422008.png) | ![DataPlot：电池数据可视化与1D 热失控仿真-42](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422009.png) |
| ------------------------------------- | ------------------------------------- |
| ![DataPlot：电池数据可视化与1D 热失控仿真-43](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422010.png) | ![DataPlot：电池数据可视化与1D 热失控仿真-44](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422011.png)  |




### 1D TR 动力学参数辨识
通过导入格式为 `时间-温度（热失控测试）-环境温度（可选）`格式的xlsx或csv文件，可基于**实验温度值**反向参数辨识得到近似满足实验数据的**反应动力学参数**，并自动绘制仿真-实验对比曲线。
![DataPlot：电池数据可视化与1D 热失控仿真-24](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422012.png)

其中，反应速率方程：
$$
\frac{dc_k}{dt}=-A_{k}\exp \left( -\frac{Ea_{k}}{RT}\right) c_{k}^{n_k}(1-c_{k})^{m_k}
$$
中的自催化反应级数 $m_k$ 默认设置为0，表示不对其进行辨识，即实际反应速率中不包含该项：
$$
\frac{dc_k}{dt}=-A_{k}\exp \left( -\frac{Ea_{k}}{RT}\right) c_{k}^{n_k}
$$

程序采用内置 [并行PSO算法](https://mp.weixin.qq.com/s/ks5cXgzDXC9331jFGmbhKw#:~:text=%E7%B2%92%E5%AD%90%E7%BE%A4%E4%BC%98,%E6%B1%9F%E8%8B%8F)。在计算控制参数面板可用于设置PSO算法辨识相关参数，如并行CPU数、PSO种群大小、PSO迭代数。
![DataPlot：电池数据可视化与1D 热失控仿真-22](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422013.png)
并且可以根据需求自定义添加/删除热失控相关机理反应。
![DataPlot：电池数据可视化与1D 热失控仿真-23](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422014.png)
参数辨识后，在输出窗口自动打印输出辨识参数值，如：
```python
[1D TR 辨识完成] 总耗时: 229.51 秒, 最优拟合适应度: 63.8678
  A_1, Ea_1, H_1, n_1, m_1, c0_1 = 1.000000e+10, 8.741841e+04, 10.867, 1.413, 0.000, 1.000
  A_2, Ea_2, H_2, n_2, m_2, c0_2 = 2.150330e+21, 2.695576e+05, 380.169, 2.214, 0.000, 1.000
  A_3, Ea_3, H_3, n_3, m_3, c0_3 = 5.024115e+15, 1.822292e+05, 50.000, 2.783, 0.000, 1.000
  A_4, Ea_4, H_4, n_4, m_4, c0_4 = 1.086184e+06, 7.500000e+04, 350.000, 2.458, 0.000, 1.000
```
点击`仿真计算`按钮，则可将辨识后的参数值代入到 `1D 热失控仿真计算` 面板，进行其它工况仿真计算。
![DataPlot：电池数据可视化与1D 热失控仿真-25](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422015.png)
自动导入后的参数值：
![DataPlot：电池数据可视化与1D 热失控仿真-27](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422016.png)
并可单独导入/导出参数，便于后续使用。
![DataPlot：电池数据可视化与1D 热失控仿真-29](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422017.png)
其中，默认参数参考修改自文献：
```python
Int. J. Heat MassTransf., Vol. 103 pp. 1008-1016,2016.
```

### ARC 热失控动力学参数辨识
专用于基于ARC 测试得到的时间-温升曲线，基于集总反应方程：
$$
\frac{dc}{dt}=-A_{}\exp \left( -\frac{Ea_{}}{RT}\right) c_{}^{n}(1-c_{})^{m}
$$
进行参数辨识。本质为 [【2024R1】Ansys Fluent电池热失控仿真教程](https://mp.weixin.qq.com/s/JTawNT9X8TZom-2u7YGSRg) 中的一方程热失控模型一致。
![DataPlot：电池数据可视化与1D 热失控仿真-30](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422018.png)
ARC 测试数据导入格式为：时间-温度（℃），并且可手动（建议）输入自产热起始温度 $T_1$ 与热失控最高温度 $T_3$，电芯密度与比热容根据实际值进行输入，延长因子是为提升辨识精确性做的修正值（保持默认即可）。
![DataPlot：电池数据可视化与1D 热失控仿真-31](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422019.png)
ARC 测试数据可采用3种参数辨识方法：无初始值要求的PSO、有初始值要求的Nelder-Mead 单纯形法、先基于PSO进行无初始值辨识，再基于PSO辨识结果基于Nelder-Mead 单纯形法进行细化辨识。
![DataPlot：电池数据可视化与1D 热失控仿真-33](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422020.png)
其中，由于指前因子与活化能与反应级数相差几个数量级，因此在输入上下限时，通过对其取 $\ln$ 后再进行输入。如下所示为案例辨识结果：
![DataPlot：电池数据可视化与1D 热失控仿真-34](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422021.png)
并也可点击`导入仿真计算`，将基于ARC 测试的反应动力学参数作为 `1D 热失控仿真计算` 模型中的 集总模型反应参数进行计算。
![DataPlot：电池数据可视化与1D 热失控仿真-35](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422022.png)
![DataPlot：电池数据可视化与1D 热失控仿真-36](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422023.png)


| ![DataPlot：电池数据可视化与1D 热失控仿真-37](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422024.png) | ![DataPlot：电池数据可视化与1D 热失控仿真-38](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422025.png) |
| ------------------------------------- | ------------------------------------- |
| ![DataPlot：电池数据可视化与1D 热失控仿真-39](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422026.png) | ![DataPlot：电池数据可视化与1D 热失控仿真-40](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422027.png)  |



## 选项卡-FLOEFD
专用于处理流体仿真软件FLOEFD 导出的xlsx文件数据：
![DataPlot：电池数据可视化与1D 热失控仿真-8](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422028.png)
可将直接由FLOEFD 导出的数据：
![DataPlot：电池数据可视化与1D 热失控仿真-1](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422029.png)
处理为去除无效行和重复时间列、空列的数据格式（点击清洗可将清理后的数据保存到新xlsx中）：
![DataPlot：电池数据可视化与1D 热失控仿真-2](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422030.png)
并可直接在软件中进行绘图。并最多支持3种物理变量的绘制（左y1轴-右y2轴-右y3轴）。并可在软件中单独/多选对多个数据列进行重命名。点击保存可将绘图数据保存至xlsx文件中。
![DataPlot：电池数据可视化与1D 热失控仿真-3](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422031.png)

## 选项卡-General
![DataPlot：电池数据可视化与1D 热失控仿真-4](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422032.png)
可用于处理任意格式xlsx文件和csv文件，当单个xlsx文件中sheet超过xlsx文件行数限制时，可通过多选sheet，将多个sheet中的数据合并后导致（需保证不同sheet中数据格式一致）。在多y轴绘图时，可以为三个y轴分别设置不同的x轴（如果不同数据采样间隔不一致)。

![DataPlot：电池数据可视化与1D 热失控仿真-5](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422033.png)
## 选项卡-Battery
专用于电池测试数据处理-分析的功能面板，可用于分析/绘制/对比不同循环对应的电压/电流/dqdv/dvdq等。
![DataPlot：电池数据可视化与1D 热失控仿真-15](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422034.png)

### 电池数据配置面板
用于选择对应的数据列，其中时间筛选可以输入满足指定时间间隔的循环数，如0-3600，则表示只读取默认时间差在3600s内的循环数。
![DataPlot：电池数据可视化与1D 热失控仿真-14](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422035.png)
电压列：xlsx数据中电压所在列标题名
电流列：xlsx数据中电流所在列标题名
比例因子：绘图数据默认按照电压单位为V，电流单位为A，若为mV或mA，则输入比例因子1e-3/1e-3进行转换。
循环列：xlsx数据中循环序号所在列标题名，如实际循环列名为“循环序号"，序号范围为1~1000，
循环筛选：可单独绘制某一循环对应的数据，如实际循环序号范围为1~1000，单独绘制第100圈，则进行勾选。
工步列：xlsx数据中工步序号所在列标题名，用来筛选充电-静置-放电等不同工况下的数据
工步筛选：可单独绘制某一循环/全部循环中具体工步对应的数据如全部循环中充电过程的数据。



### 启用循环对比面板
可用于对比不同循环下电压曲线响应，结合`电池数据配置面板`参数配置。
![DataPlot：电池数据可视化与1D 热失控仿真-17](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422036.png)
其中，CC极性表示充电过程电流正负，若充电过程电流大于0，则为正，否则为负。


### dqdv/dvdq滤波
结合指定的滤波方式，可用于绘制循环过程中的dqdv或dvdq数据曲线，并显示在绘图面板中。
![DataPlot：电池数据可视化与1D 热失控仿真-18](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422037.png)

由于在电池电化学测试中，充放电电压平台期往往存在由于设备 ADC 采样精度不足导致的**多点电压重复或微小波动**。若直接进行简单差分 $\dfrac{\Delta Q}{\Delta V}$，极易因分母 $\Delta V \approx 0$ 而产生严重的数值无穷大（$\pm \infty$）和离散毛刺。
为此，DataPlot 在求导前提供了 3 种处理模式：
“去重”模式：
   * **去重与单调排序**：利用 `np.unique` 剔除电压停滞的重复采样点，并按电势单调升序排列；
   * **均匀网格化（Uniform Grid Resampling）**：在设定的电压范围 $[V_{\min}, V_{\max}]$ 内构建等间距均匀网格：
$$V_{\text{grid}} = \text{np.linspace}(V_{\min}, V_{\max}, N_{\text{pts}}) \quad (N_{\text{pts}}\text{ 默认 100}\sim\text{500 点})$$
   * **线性插值重构**：将离散的实测容量对齐插值到均匀电压网格上得到连续可微的 $Q(V_{\text{grid}})$；
   * **中心差分梯度**：调用 `np.gradient(Q)` 和 `np.gradient(V)` 计算一阶导数，并通过 `np.nan_to_num` 将极值截断置零。
 **“原始”模式**：
   * 在原始未插值的数据点上直接做梯度差分，保留最真实的原始离散跳变；
 **“对比”模式**：
   * 在同一张图上同时绘制虚线（去重重采样曲线）与实线（原始微商曲线），方便工程师直观评估仪器采样误差对相变峰（Redox Peaks）的影响。


求导后的微商曲线会天然放大仪器高频白噪声。DataPlot 的 `apply_filtering` 函数提供了 5 种滤波算法，可在界面中自由选择**滤波类型**与**窗口长度（Window Size，默认 15）**：
#### Savitzky-Golay (S-G) 滤波（推荐首选）
* **数学机理**：在滑动窗口内对数据进行局部移动多项式最小二乘拟合；
* **工程优势**：在极大滤除高频噪声的同时，**能最好地保留电池氧化还原相变峰的峰位（Peak Position）、峰高（Peak Height）和峰面积**，几乎不会发生传统平均滤波引起的特征峰钝化和位置漂移；
* **双轨容错机制**：优先调用 `scipy.signal.savgol_filter`，若在纯轻量化运行环境（无 scipy）下，系统自动启用纯 NumPy 矩阵伪逆与卷积核计算，保证任何场景下永不报错。
#### 高斯滤波 (Gaussian Filter)
* **数学机理**：采用一维高斯正态分布衰减核对局部数据进行加权卷积平滑，标准差参数自动适配 $\sigma = \max(1.0,\, \frac{w}{6.0})$；
* **特点**：曲线过渡极其平滑柔顺，适合分析宽泛的固溶体相变区。

#### 移动平均滤波 (Moving Average)
* **数学机理**：滑动窗口内计算中心对称算术平均值；
* **特点**：计算开销极低，适合快速粗略平滑。

#### 中值滤波 (Median Filter)
* **数学机理**：滑动窗口内取中位数作为当前点的值；
* **工程优势**：对电芯接触不良、继电器切换或通信丢包瞬间产生的**孤立极端脉冲“野点/飞点”具有天然的免疫力**，能快速剔除奇异尖峰。

#### 指数平滑 (Exponential Moving Average)
* **数学机理**：以衰减系数 $\alpha = \dfrac{2}{w + 1}$ 进行单向历史加权平均；
* **特点**：保留最新趋势响应，曲线平滑连续。


## 通用设置选项卡
### 坐标轴范围面板
在坐标轴范围选项卡中，可为每个X、Y轴设置坐标轴显示的最小、最大值，并通过`显示`按钮控制其在绘图面板的显示。
![DataPlot：电池数据可视化与1D 热失控仿真-9](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422038.png)
### 绘图配置面板
在绘图配置面板可以设置的参数如下：
![DataPlot：电池数据可视化与1D 热失控仿真-11](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422040.png)
通过拖动图例水平位置滑动块，可进行调整不同Y轴图例的显示位置，避免重叠。

### 高级配置选项卡
在高级配置选项卡，可用于设置画布相关参数，并进行`保存设置`。
![DataPlot：电池数据可视化与1D 热失控仿真-7](https://raw.githubusercontent.com/Wolframscs/PIC-Vault/main/pic/20261108222422041.png)






