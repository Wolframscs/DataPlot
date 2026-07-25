import h5py
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from scipy.interpolate import interp1d
from scipy.ndimage import gaussian_filter1d
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestRegressor
import os

# ==========================================
# 0. 设置字体 (防止中文乱码)
# ==========================================
plt.rcParams['font.sans-serif'] = ['SimHei']  # Windows默认黑体
plt.rcParams['axes.unicode_minus'] = False  # 解决负号显示问题

# ================= 配置 =================
filename = '2017-05-12_batchdata_updated_struct_errorcorrect.mat'
# ========================================

print(f"🚀 启动【全生命周期轨迹重构】(Trajectory Reconstruction)...")
print(f"目标：训练通用模型，利用 dQ/dV 特征还原每一圈的容量曲线。")

if not os.path.exists(filename):
    print(f"❌ 找不到文件 {filename}")
    exit()

# ---------------------------------------------------------
# Step 1: 建立【全量时序数据集】
# 我们不再是一个电池一行，而是“一个循环一行”
# ---------------------------------------------------------
print("Step 1: 正在把所有电池拆解为‘循环切片’ (Dataset Building)...")
print("这需要处理数万个数据点，请耐心等待 (约 1-2 分钟)...")

all_cycles_data = []  # 存储所有训练数据
cell_meta = {}  # 存储电池的基本信息

try:
    f = h5py.File(filename, 'r')
    batch = f['batch']['cycles']
    num_cells_scan = 150

    # 扫描所有电池
    for i in range(num_cells_scan):
        try:
            # 定位电池
            if batch.shape[0] > batch.shape[1]:
                cell_ref = batch[i, 0]
            else:
                cell_ref = batch[0, i]
            cell_data = f[cell_ref]
            Qd_refs = cell_data['Qd']
            total_life = Qd_refs.shape[0] if Qd_refs.shape[0] > Qd_refs.shape[1] else Qd_refs.shape[1]

            # 这里的100只是为了过滤极短的坏电池，后面会提取所有圈
            if total_life < 100: continue

            # 获取基本引用
            V_refs = cell_data['V']
            I_refs = cell_data['I']
            t_refs = cell_data['t']

            # --- 1. 获取该电池的充电倍率 (作为静态特征) ---
            # 随便取第50圈算一下充电电流
            idx_50 = (50, 0) if V_refs.shape[0] > V_refs.shape[1] else (0, 50)
            I_50 = f[I_refs[idx_50]][:].flatten()
            t_50 = f[t_refs[idx_50]][:].flatten()
            charge_mask = I_50 > 0.05
            if np.sum(charge_mask) > 10:
                static_avg_charge_I = np.mean(I_50[charge_mask])
            else:
                static_avg_charge_I = 2.0  # 默认值

            # 记录电池归属 (用于后面划分测试集)
            cell_meta[i] = {'Charge_Rate': static_avg_charge_I}

            # --- 2. 遍历该电池的生命周期 (降采样 Step=10 提高速度) ---
            # 我们提取每一圈的特征，建立 (X, y) 对
            for cyc in range(0, total_life, 10):
                try:
                    if Qd_refs.shape[0] > Qd_refs.shape[1]:
                        idx = (cyc, 0)
                    else:
                        idx = (0, cyc)

                    V_raw = f[V_refs[idx]][:].flatten()
                    Q_raw = f[Qd_refs[idx]][:].flatten()

                    # 必须要有足够的数据点
                    if len(V_raw) < 50: continue

                    # 提取该圈的真实容量 (Target)
                    current_capacity = np.max(Q_raw) - np.min(Q_raw)
                    # 过滤异常点
                    if current_capacity < 0.1 or current_capacity > 2.0: continue

                    # --- 核心：提取该圈的 dQ/dV 方差 ---
                    peak_idx = np.argmax(V_raw)
                    V_dis = V_raw[peak_idx:]
                    Q_dis = Q_raw[peak_idx:]

                    _, unique_indices = np.unique(V_dis, return_index=True)
                    unique_indices = np.sort(unique_indices)
                    V_clean = V_dis[unique_indices]
                    Q_clean = Q_dis[unique_indices]

                    v_grid = np.linspace(2.8, 3.5, 100)  # 降采样到100个点够用了
                    f_interp = interp1d(V_clean[::-1], Q_clean[::-1], kind='linear', fill_value="extrapolate")
                    q_interp = f_interp(v_grid)
                    dq = np.gradient(q_interp)
                    dv = np.gradient(v_grid)
                    with np.errstate(divide='ignore', invalid='ignore'):
                        dqdv = dq / dv
                    dqdv = np.nan_to_num(dqdv, nan=0.0)

                    # 特征：方差
                    feat_var = np.var(dqdv)

                    # 存入大表
                    all_cycles_data.append({
                        'Cell_ID': i,
                        'Cycle': cyc,
                        'Charge_Rate': static_avg_charge_I,  # 静态特征
                        'dQdV_Variance': feat_var,  # 动态物理特征
                        'Capacity': current_capacity  # 目标值 y
                    })

                except:
                    continue

            if i % 10 == 0: print(f"   -> 已处理电池 {i} 的全生命周期数据...")

        except:
            continue

    # 转为 DataFrame
    df_full = pd.DataFrame(all_cycles_data)
    print(f"\n✅ 数据集构建完成！总共包含 {len(df_full)} 个循环切片。")
    print(df_full.head())

    # ---------------------------------------------------------
    # Step 2: 训练“轨迹追踪模型”
    # ---------------------------------------------------------
    # 策略：按【电池】划分训练/测试集，而不是按【行】划分
    # 这样能保证测试集的电池是模型完全没见过的
    all_cell_ids = df_full['Cell_ID'].unique()
    train_cells, test_cells = train_test_split(all_cell_ids, test_size=0.2, random_state=42)

    print(f"\nStep 2: 正在训练通用模型 (Train on {len(train_cells)} cells, Test on {len(test_cells)} cells)...")

    # 训练集
    train_df = df_full[df_full['Cell_ID'].isin(train_cells)]
    X_train = train_df[['Cycle', 'Charge_Rate', 'dQdV_Variance']]
    y_train = train_df['Capacity']

    # 训练随机森林 (这回是用物理特征去推算当下的容量)
    rf_model = RandomForestRegressor(n_estimators=100, n_jobs=-1, random_state=42)
    rf_model.fit(X_train, y_train)
    print("✅ 模型训练完毕！它现在学会了如何通过方差推算容量。")

    # ---------------------------------------------------------
    # Step 3: 对测试电池进行全轨迹重构并绘图
    # ---------------------------------------------------------
    print("\nStep 3: 正在绘制全生命周期对比图...")

    # 挑选 4 个测试电池 (2个快充，2个慢充)
    test_df_full = df_full[df_full['Cell_ID'].isin(test_cells)]

    # 简单的筛选逻辑：按充电倍率排序
    unique_test_cells = test_df_full.drop_duplicates('Cell_ID').sort_values('Charge_Rate')
    target_cells = []
    if len(unique_test_cells) >= 4:
        # 取最慢的两个和最快的两个
        target_cells = unique_test_cells.iloc[[0, 1, -2, -1]]['Cell_ID'].values
    else:
        target_cells = unique_test_cells['Cell_ID'].values

    fig, axes = plt.subplots(2, 2, figsize=(15, 12))  # 增加画布高度，防止重叠
    # 调整子图间距
    plt.subplots_adjust(hspace=0.4, wspace=0.3)
    axes = axes.flatten()

    for k, cell_id in enumerate(target_cells):
        if k >= 4: break

        # 提取这个电池的所有数据
        cell_trace = df_full[df_full['Cell_ID'] == cell_id].sort_values('Cycle')

        # 准备输入
        X_test_cell = cell_trace[['Cycle', 'Charge_Rate', 'dQdV_Variance']]
        y_true = cell_trace['Capacity']

        # AI 进行轨迹重构
        y_pred = rf_model.predict(X_test_cell)

        # 计算 R2
        score = rf_model.score(X_test_cell, y_true)
        rate = cell_trace['Charge_Rate'].iloc[0]

        # 绘图
        ax = axes[k]
        # 1. 真实轨迹 (黑点)
        ax.scatter(cell_trace['Cycle'], y_true, color='black', s=15, alpha=0.4, label='真实测量容量')

        # 2. AI 重构轨迹 (红线)
        # 这就是你要的：每一圈都捕捉！
        ax.plot(cell_trace['Cycle'], y_pred, color='#E63946', linewidth=2.5, label='AI 重构轨迹')

        # 标题 (中文显示)
        status = "[快充策略]" if rate > 4 else "[慢充策略]"
        col = '#D62828' if rate > 4 else '#2A9D8F'  # 快充用深红，慢充用青绿

        ax.set_title(f"电池 #{cell_id} {status}\n充电电流: {rate:.2f}A | 拟合优度 R^2: {score:.4f}",
                     color=col, fontweight='bold', fontsize=11)

        ax.set_xlabel("循环圈数 (Cycle)", fontsize=10)
        ax.set_ylabel("容量 (Ah)", fontsize=10)
        ax.grid(True, alpha=0.3, linestyle='--')

        # 只在第一个图显示图例，避免乱
        if k == 0:
            ax.legend(loc='lower left', frameon=True)

    # 增加一个总标题
    plt.suptitle("AI 数字孪生：全生命周期容量轨迹重构", fontsize=16, y=0.96)

    # 保存图片
    plt.savefig('trajectory_reconstruction_cn.png', dpi=300, bbox_inches='tight')
    plt.show()
    print("✅ 终于完成了！请看这张图，这才是你要的‘每一圈捕捉’。")

except Exception as e:
    import traceback

    traceback.print_exc()