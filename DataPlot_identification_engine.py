"""
DataPlot_identification_engine.py:
用于 1D 热失控模型与 ARC 测试数据的动力学参数辨识计算引擎。
支持:
1. 1D TR 仿真副反应多步动力学参数多核多进程 PSO 并行辨识:
   - 支持动态指定反应个数 (1, 2, 4 或 N 个反应)
   - 支持自定义每个反应的取值上下限 [lb, ub] (以 log10(A), Ea, H, n, m 为基准)
   - 支持自催化级数 m 判断: 若初始 m=0 则不辨识/不计入该参数，若 m>0 则自动纳入辨识变量
   - 支持多核并行加速 (multiprocessing spawn 架构)，大幅降低寻优耗时
   - 同步计算控制参数 (dt, dt_sub_min, dc_tol, dT_tol, k_max) 与电芯物性几何
2. ARC 绝热热失控动力学参数辨识 (纯 NumPy 实现的下山单纯形法 Nelder-Mead 与 PSO):
   - 支持自定义各动力学因子的上下限范围 [lb, ub]
   - 动力学方程支持经典一方程与自催化 m 扩展方程
   - 纯内置算法，零 scipy 依赖，打包体积超轻
"""

import os
import time
import multiprocessing
import numpy as np
import pandas as pd
from PySide6.QtCore import QThread, Signal
from DataPlot_optimization_core import nelder_mead, rk45_adaptive


# ------------------------------------------------------------------------------
# 模块级顶层静态评估函数 (供多进程 spawn Pool 安全调用，无需依赖 Qt 对象)
# ------------------------------------------------------------------------------
def _evaluate_1d_simulation_single_particle(task):
    """
    单粒子 1D 热失控前向仿真适应度评估函数
    task = (x_vec, static_meta)
    """
    try:
        x_vec, static_meta = task
        rxn_meta = static_meta["rxn_meta"] # 包含每个反应的 m 状态
        ctrl = static_meta["ctrl"]
        geom = static_meta["geom"]
        pre = static_meta["pre"]

        n_rxns = len(rxn_meta)
        A_vec = np.zeros(n_rxns, dtype=np.float64)
        E_vec = np.zeros(n_rxns, dtype=np.float64)
        H_vec = np.zeros(n_rxns, dtype=np.float64)
        n_vec = np.zeros(n_rxns, dtype=np.float64)
        m_vec = np.zeros(n_rxns, dtype=np.float64)

        cur_idx = 0
        for j in range(n_rxns):
            # log10(A), Ea, H, n
            A_vec[j] = 10.0 ** x_vec[cur_idx]
            E_vec[j] = x_vec[cur_idx + 1]
            H_vec[j] = x_vec[cur_idx + 2]
            n_vec[j] = x_vec[cur_idx + 3]
            cur_idx += 4
            if rxn_meta[j]["has_m"]:
                m_vec[j] = x_vec[cur_idx]
                cur_idx += 1
            else:
                m_vec[j] = 0.0

        n_layers = ctrl["n_layers"]
        dt = ctrl["dt"]
        th_end = ctrl["th_end"]
        Ph = ctrl["Ph"]
        T_init = ctrl["T_init"]
        T_amb = ctrl["T_amb"]
        k_max = ctrl["k_max"]
        dc_tol = ctrl["dc_tol"]
        dT_tol = ctrl["dT_tol"]
        dt_sub_min = ctrl["dt_sub_min"]

        c_th_layer = geom["c_th_layer"]
        m_layer = geom["m_layer"]
        g_cond = geom["g_cond"]
        h_layer = geom["h_layer"]
        dt_max_strict = geom["dt_max_strict"]
        GAS_R = geom["GAS_R"]

        n_steps = pre["n_steps"]
        t_arr = pre["t_arr"]
        T_exp_interp = pre["T_exp_interp"]
        weights = pre["weights"]
        use_mean_temp = pre["use_mean_temp"]
        target_layer_idx = pre["target_layer_idx"]

        T_layers = np.full(n_layers, T_init, dtype=np.float64)
        c_mat = np.ones((n_layers, n_rxns), dtype=np.float64)
        T_target_hist = np.zeros(n_steps, dtype=np.float64)
        T_mean_hist = np.zeros(n_steps, dtype=np.float64)
        T_target_hist[0] = T_init
        T_mean_hist[0] = T_init

        for i in range(n_steps - 1):
            t_sub_curr = t_arr[i]
            t_target = t_arr[i + 1]
            sub_cnt = 0
            while t_sub_curr < t_target - 1e-12:
                rem_time = t_target - t_sub_curr
                q_ht = np.zeros(n_layers, dtype=np.float64)
                q_flux = -g_cond * np.diff(T_layers)
                q_ht[1:] += q_flux
                q_ht[:-1] -= q_flux
                q_ht -= h_layer * (T_layers - T_amb)
                if t_sub_curr <= th_end:
                    q_ht[0] += Ph

                TK = T_layers + 273.15
                rt = GAS_R * TK
                exp_arg = np.clip(-E_vec[None, :] / rt[:, None], -700.0, 50.0)
                k_rate = np.minimum(A_vec[None, :] * np.exp(exp_arg), k_max)

                # 反应物浓度与自催化动力学
                c_pos = np.clip(c_mat, 0.0, 1.0)
                alpha_pos = np.clip(1.0 - c_pos, 0.0, 1.0)
                
                # rate = k * c^n * (1-c)^m
                rate_chem = k_rate * np.power(c_pos, n_vec[None, :])
                # 若 m > 0 则乘自催化因子
                has_any_m = np.any(m_vec > 0.0)
                if has_any_m:
                    m_factor = np.where(m_vec[None, :] > 0.0, np.power(alpha_pos, m_vec[None, :]), 1.0)
                    rate_chem *= m_factor

                rate_mat = np.where(c_mat > 1e-6, rate_chem, 0.0)
                p_chem_inst = m_layer * np.dot(rate_mat, H_vec)

                dT_rate = (q_ht + p_chem_inst) / c_th_layer
                max_dc_rate = float(np.max(rate_mat))
                max_dT_rate = float(np.max(np.abs(dT_rate)))

                dt_c_lim = (dc_tol / max_dc_rate) if max_dc_rate > 1e-12 else rem_time
                dt_T_lim = (dT_tol / max_dT_rate) if max_dT_rate > 1e-12 else rem_time
                dt_sub = min(rem_time, 0.9 * dt_max_strict, dt_c_lim, dt_T_lim)
                dt_sub = max(dt_sub, min(rem_time, dt_sub_min))

                delta_c = np.minimum(c_mat, rate_mat * dt_sub)
                c_mat -= delta_c
                dq_chem_layers = m_layer * np.dot(delta_c, H_vec)
                T_layers += (q_ht * dt_sub + dq_chem_layers) / c_th_layer
                t_sub_curr += dt_sub
                sub_cnt += 1
                if sub_cnt > 2500:
                    break

            T_mean_hist[i + 1] = np.mean(T_layers)
            if use_mean_temp:
                T_target_hist[i + 1] = T_mean_hist[i + 1]
            else:
                T_target_hist[i + 1] = T_layers[target_layer_idx]

        diff = T_target_hist - T_exp_interp
        w_rmse = float(np.sqrt(np.mean(weights * (diff ** 2))))
        peak_err = abs(np.max(T_target_hist) - np.max(T_exp_interp))
        c_end = np.mean(c_mat, axis=0)
        penalty_c = float(np.sum(np.maximum(c_end - 0.05, 0.0)) * 100.0)
        
        fitness_val = w_rmse + 0.4 * peak_err + penalty_c
        if np.isnan(fitness_val) or np.isinf(fitness_val):
            return 1e6
        return float(fitness_val)
    except Exception:
        return 1e6


# ------------------------------------------------------------------------------
# 1. 向量化轻量级粒子群求解器 (支持单核串行与多核进程池批评估)
# ------------------------------------------------------------------------------
class InternalPSO:
    def __init__(self, fitness_func=None, batch_eval_func=None, dim=4, bounds=None,
                 n_particles=36, max_iter=35, w=0.7298, c1=1.4962, c2=1.4962, v_ratio=0.15, seed=42):
        self.fitness_func = fitness_func
        self.batch_eval_func = batch_eval_func
        self.dim = dim
        self.lb = np.array(bounds[0], dtype=np.float64)
        self.ub = np.array(bounds[1], dtype=np.float64)
        self.n_particles = n_particles
        self.max_iter = max_iter
        self.w = w
        self.c1 = c1
        self.c2 = c2
        self.v_ratio = v_ratio

        if seed is not None:
            np.random.seed(seed)

        self.v_max = self.v_ratio * (self.ub - self.lb)
        self.v_min = -self.v_max

        self.X = np.random.uniform(self.lb, self.ub, size=(n_particles, dim))
        self.V = np.random.uniform(self.v_min, self.v_max, size=(n_particles, dim))

        self.pbest_X = self.X.copy()
        self.pbest_fit = np.full(n_particles, np.inf)
        self.gbest_X = self.X[0].copy()
        self.gbest_fit = np.inf
        self.convergence_curve = []

    def optimize_step(self, step_idx):
        """单代评估与演化"""
        if self.batch_eval_func is not None:
            fits = self.batch_eval_func(self.X)
        else:
            fits = np.zeros(self.n_particles)
            for i in range(self.n_particles):
                fits[i] = self.fitness_func(self.X[i])

        # 更新个体历史最优
        improved = fits < self.pbest_fit
        self.pbest_fit[improved] = fits[improved]
        self.pbest_X[improved] = self.X[improved]

        # 更新全局最优
        min_idx = np.argmin(fits)
        if fits[min_idx] < self.gbest_fit:
            self.gbest_fit = float(fits[min_idx])
            self.gbest_X = self.X[min_idx].copy()

        self.convergence_curve.append(self.gbest_fit)

        # 运动学向量化速度与位置更新
        r1 = np.random.rand(self.n_particles, self.dim)
        r2 = np.random.rand(self.n_particles, self.dim)

        self.V = (self.w * self.V +
                  self.c1 * r1 * (self.pbest_X - self.X) +
                  self.c2 * r2 * (self.gbest_X - self.X))
        self.V = np.clip(self.V, self.v_min, self.v_max)

        self.X = self.X + self.V
        self.X = np.clip(self.X, self.lb, self.ub)

        return self.gbest_X, self.gbest_fit


# ------------------------------------------------------------------------------
# 2. 1D 热失控副反应动力学参数 PSO 并行辨识后台线程
# ------------------------------------------------------------------------------
class TR1DIdentifyWorker(QThread):
    progress_signal = Signal(str)
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._is_cancelled = False
        self.pool = None

    def cancel(self):
        self._is_cancelled = True
        p = self.pool
        if p is not None:
            try:
                p.terminate()
            except Exception:
                pass
            self.pool = None

    def run(self):
        self.pool = None
        try:
            cfg = self.config
            geom_raw = cfg["geometry"]
            ctrl_raw = cfg["control"]
            ident_cfg = cfg["identify"]

            # 1. 解析目标测点位置与实验数据
            t_exp = np.array(ident_cfg["t_exp"], dtype=np.float64)
            T_exp = np.array(ident_cfg["T_exp"], dtype=np.float64)
            t_max_exp = float(np.nanmax(t_exp)) if len(t_exp) > 0 else 3600.0
            
            # 辨识总时长以真实实验数据最大时间为准，防止被正向仿真控制面板中的 t_max (例如用户测试填写的 20s) 截断
            user_ident_tmax = float(ident_cfg.get("ident_t_max", 0.0))
            if user_ident_tmax > 0.0:
                t_max = min(user_ident_tmax, t_max_exp)
            else:
                t_max = t_max_exp
            
            # 允许在辨识时独立配置宏观步长 dt
            dt = float(ident_cfg.get("dt", ctrl_raw.get("dt", 0.5)))
            th_end = float(ctrl_raw.get("th_end", 1000.0))
            Ph = float(ctrl_raw.get("Ph", 300.0))
            h_conv = float(ctrl_raw.get("h_conv", 5.0))
            T_init = float(ctrl_raw.get("T_init", T_exp[0]))
            T_amb = float(ident_cfg.get("T_amb_val", ctrl_raw.get("T_amb", 25.0)))
            k_max = float(ctrl_raw.get("k_max", 1000.0))
            dc_tol = float(ctrl_raw.get("dc_tol", 0.02))
            dT_tol = float(ctrl_raw.get("dT_tol", 2.5))
            dt_sub_min = float(ctrl_raw.get("dt_sub_min", 1e-6))
            n_layers = int(ctrl_raw.get("n_layers", 50))

            H_m = float(geom_raw.get("H_m", 0.155))
            W_m = float(geom_raw.get("W_m", 0.289))
            L_m = float(geom_raw.get("L_m", 0.048))
            M_cell = float(geom_raw.get("M_cell", 3.8))
            Cp = float(geom_raw.get("Cp", 1055.0))
            lambda_thick = float(geom_raw.get("lambda_thick", 1.56))
            M_act = float(geom_raw.get("M_act", 2859.0))
            GAS_R = float(geom_raw.get("GAS_R", 8.314))

            A_face = H_m * W_m
            C_th = M_cell * Cp
            dz = L_m / n_layers
            c_th_layer = C_th / n_layers
            m_layer = M_act / n_layers
            area_side = 2 * (H_m + W_m) * dz
            g_cond = lambda_thick * A_face / dz
            h_layer = np.full(n_layers, h_conv * area_side)
            h_layer[-1] += h_conv * A_face
            dt_max_strict = c_th_layer / (2.0 * g_cond + np.max(h_layer))

            n_steps = int(round(t_max / dt)) + 1
            t_arr = np.linspace(0.0, t_max, n_steps)
            T_exp_interp = np.interp(t_arr, t_exp, T_exp)

            # 测点映射
            pos_mode = ident_cfg.get("pos_mode", "mean")
            use_mean_temp = (str(pos_mode).strip().lower() == "mean")
            target_layer_idx = 0
            if not use_mean_temp:
                pos_map = {"0": 0.0, "1/4": 0.25, "1/2": 0.5, "3/4": 0.75, "1": 1.0}
                p_val = pos_map.get(str(pos_mode), 0.5)
                try:
                    p_val = float(p_val)
                except ValueError:
                    p_val = 0.5
                target_layer_idx = int(np.clip(round(p_val * (n_layers - 1)), 0, n_layers - 1))

            rxn_specs = ident_cfg["reactions"]
            n_rxns = len(rxn_specs)

            # 解析优化变量与 m 状态: 若某反应的初始 m == 0，则不辨识 m；若 m > 0 则辨识 m
            rxn_meta = []
            lb_list = []
            ub_list = []
            param_names = []

            for r in rxn_specs:
                r_name = r["name"]
                init_m = float(r.get("m", 0.0))
                has_m = (init_m > 0.0)
                rxn_meta.append({"name": r_name, "has_m": has_m, "init_m": init_m})

                # 解析 A 的上下限 (支持直接传入物理 A 或 log10(A))
                if "lb_logA" in r:
                    lb_logA = float(r["lb_logA"])
                else:
                    lb_logA = np.log10(max(float(r.get("lb_A", 1e5)), 1e-30))

                if "ub_logA" in r:
                    ub_logA = float(r["ub_logA"])
                else:
                    ub_logA = np.log10(max(float(r.get("ub_A", 1e25)), 1e-30))

                # log10(A), Ea, H, n
                lb_list.extend([lb_logA, float(r["lb_Ea"]), float(r["lb_H"]), float(r["lb_n"])])
                ub_list.extend([ub_logA, float(r["ub_Ea"]), float(r["ub_H"]), float(r["ub_n"])])
                param_names.extend([f"{r_name}_logA", f"{r_name}_Ea", f"{r_name}_H", f"{r_name}_n"])

                if has_m:
                    lb_list.append(float(r.get("lb_m", 0.0)))
                    ub_list.append(float(r.get("ub_m", 2.0)))
                    param_names.append(f"{r_name}_m")

            dim = len(lb_list)
            lb = np.array(lb_list, dtype=np.float64)
            ub = np.array(ub_list, dtype=np.float64)

            # 权重数组强化热失控温升区
            weights = np.ones_like(t_arr)
            th_zone = (t_arr >= th_end - 150.0) & (t_arr <= min(t_max, th_end + 600.0))
            weights[th_zone] = 3.5

            # 并行核心数
            cpu_cores = int(ident_cfg.get("n_workers", min(os.cpu_count() or 4, 16)))
            cpu_cores = max(1, min(cpu_cores, os.cpu_count() or 1))

            self.progress_signal.emit(
                f"[1D TR 辨识启动] 反应个数={n_rxns}, 决策维度={dim}, 网格N={n_layers}, dt={dt:.2f}s, CPU核数={cpu_cores}, 对齐测点={pos_mode}"
            )

            # 构造静态元数据字典供子进程评估使用
            static_meta = {
                "rxn_meta": rxn_meta,
                "ctrl": {
                    "n_layers": n_layers, "dt": dt, "th_end": th_end, "Ph": Ph,
                    "T_init": T_init, "T_amb": T_amb, "k_max": k_max,
                    "dc_tol": dc_tol, "dT_tol": dT_tol, "dt_sub_min": dt_sub_min
                },
                "geom": {
                    "c_th_layer": c_th_layer, "m_layer": m_layer, "g_cond": g_cond,
                    "h_layer": h_layer, "dt_max_strict": dt_max_strict, "GAS_R": GAS_R
                },
                "pre": {
                    "n_steps": n_steps, "t_arr": t_arr, "T_exp_interp": T_exp_interp,
                    "weights": weights, "use_mean_temp": use_mean_temp,
                    "target_layer_idx": target_layer_idx
                }
            }

            # 启动多进程池
            if cpu_cores > 1:
                ctx = multiprocessing.get_context("spawn")
                self.pool = ctx.Pool(processes=cpu_cores)
                
                def eval_batch_pso(X_mat):
                    if self._is_cancelled or self.pool is None:
                        return np.full(len(X_mat), 1e9, dtype=np.float64)
                    tasks = [(X_mat[k], static_meta) for k in range(len(X_mat))]
                    try:
                        async_res = self.pool.map_async(_evaluate_1d_simulation_single_particle, tasks, chunksize=1)
                        while not async_res.ready():
                            if self._is_cancelled or self.pool is None:
                                if self.pool is not None:
                                    try:
                                        self.pool.terminate()
                                    except Exception:
                                        pass
                                    self.pool = None
                                return np.full(len(X_mat), 1e9, dtype=np.float64)
                            async_res.wait(timeout=0.05)
                        if self._is_cancelled or self.pool is None:
                            if self.pool is not None:
                                try:
                                    self.pool.terminate()
                                except Exception:
                                    pass
                                self.pool = None
                            return np.full(len(X_mat), 1e9, dtype=np.float64)
                        fits = async_res.get(timeout=2.0)
                        return np.array(fits, dtype=np.float64)
                    except Exception:
                        return np.full(len(X_mat), 1e9, dtype=np.float64)
                
                pso_eval_func = None
                pso_batch_eval = eval_batch_pso
            else:
                def eval_single(x_vec):
                    if self._is_cancelled:
                        return 1e9
                    return _evaluate_1d_simulation_single_particle((x_vec, static_meta))
                pso_eval_func = eval_single
                pso_batch_eval = None

            # 启动 PSO 求解器
            n_particles = int(ident_cfg.get("n_particles", 36))
            max_iter = int(ident_cfg.get("max_iter", 35))

            pso = InternalPSO(
                fitness_func=pso_eval_func,
                batch_eval_func=pso_batch_eval,
                dim=dim,
                bounds=(lb, ub),
                n_particles=n_particles,
                max_iter=max_iter,
                seed=42
            )

            t_start = time.perf_counter()
            self.progress_signal.emit(f"开始执行 PSO 并行寻优 (种群规模={n_particles}, 最大代数={max_iter})...")

            for step in range(max_iter):
                if self._is_cancelled:
                    self.progress_signal.emit("[中止] 1D TR 辨识已由用户中止。")
                    if self.pool is not None:
                        try:
                            self.pool.terminate()
                        except Exception:
                            pass
                        self.pool = None
                    return

                best_x, best_fit = pso.optimize_step(step)
                if self._is_cancelled:
                    self.progress_signal.emit("[中止] 1D TR 辨识已由用户中止。")
                    if self.pool is not None:
                        try:
                            self.pool.terminate()
                        except Exception:
                            pass
                        self.pool = None
                    return

                elapsed_iter = time.perf_counter() - t_start
                self.progress_signal.emit(
                    f"  [PSO代数 {step+1:2d}/{max_iter} ({(step+1)/max_iter*100:4.1f}%)] "
                    f"当前最优适应度={best_fit:.4f} | 耗时={elapsed_iter:.1f}s"
                )

            # 正常辨识完成，安全关闭进程池
            if self.pool is not None:
                try:
                    self.pool.close()
                    self.pool.join()
                except Exception:
                    pass
                self.pool = None

            elapsed_total = time.perf_counter() - t_start

            # 4. 解析全局最优动力学参数
            g_opt = pso.gbest_X
            opt_reactions = []
            opt_lines = []
            cur_idx = 0

            for j in range(n_rxns):
                r_name = rxn_specs[j]["name"]
                c0_val = float(rxn_specs[j].get("c0", 1.0))
                A_opt = 10.0 ** g_opt[cur_idx]
                E_opt = g_opt[cur_idx + 1]
                H_opt = g_opt[cur_idx + 2]
                n_opt = g_opt[cur_idx + 3]
                cur_idx += 4
                if rxn_meta[j]["has_m"]:
                    m_opt = g_opt[cur_idx]
                    cur_idx += 1
                else:
                    m_opt = 0.0

                opt_reactions.append({
                    "name": r_name,
                    "A": float(A_opt),
                    "Ea": float(E_opt),
                    "H": float(H_opt),
                    "n": float(n_opt),
                    "m": float(m_opt),
                    "c0": c0_val
                })
                opt_lines.append(
                    f"A_{j+1}, Ea_{j+1}, H_{j+1}, n_{j+1}, m_{j+1}, c0_{j+1} = "
                    f"{A_opt:.6e}, {E_opt:.6e}, {H_opt:.3f}, {n_opt:.3f}, {m_opt:.3f}, {c0_val:.3f}"
                )

            self.progress_signal.emit("=" * 60)
            self.progress_signal.emit(f"[1D TR 辨识完成] 总耗时: {elapsed_total:.2f} 秒, 最优拟合适应度: {pso.gbest_fit:.4f}")
            for l in opt_lines:
                self.progress_signal.emit(f"  {l}")
            self.progress_signal.emit("=" * 60)

            # 5. 采用最优参数运行最终精细模拟计算用于绘图
            # 最终出图使用高精度模拟
            final_task = (g_opt, static_meta)
            _ = _evaluate_1d_simulation_single_particle(final_task)

            # 重现高精度曲线
            A_vec_opt = np.array([r["A"] for r in opt_reactions])
            E_vec_opt = np.array([r["Ea"] for r in opt_reactions])
            H_vec_opt = np.array([r["H"] for r in opt_reactions])
            n_vec_opt = np.array([r["n"] for r in opt_reactions])
            m_vec_opt = np.array([r["m"] for r in opt_reactions])

            T_layers = np.full(n_layers, T_init, dtype=np.float64)
            c_mat = np.ones((n_layers, n_rxns), dtype=np.float64)
            T_target_hist = np.zeros(n_steps, dtype=np.float64)
            T_mean_hist = np.zeros(n_steps, dtype=np.float64)
            T_target_hist[0] = T_init
            T_mean_hist[0] = T_init

            for i in range(n_steps - 1):
                t_sub_curr = t_arr[i]
                t_target = t_arr[i + 1]
                sub_cnt = 0
                while t_sub_curr < t_target - 1e-12:
                    rem_time = t_target - t_sub_curr
                    q_ht = np.zeros(n_layers, dtype=np.float64)
                    q_flux = -g_cond * np.diff(T_layers)
                    q_ht[1:] += q_flux
                    q_ht[:-1] -= q_flux
                    q_ht -= h_layer * (T_layers - T_amb)
                    if t_sub_curr <= th_end:
                        q_ht[0] += Ph

                    TK = T_layers + 273.15
                    rt = GAS_R * TK
                    exp_arg = np.clip(-E_vec_opt[None, :] / rt[:, None], -700.0, 50.0)
                    k_rate = np.minimum(A_vec_opt[None, :] * np.exp(exp_arg), k_max)

                    c_pos = np.clip(c_mat, 0.0, 1.0)
                    alpha_pos = np.clip(1.0 - c_pos, 0.0, 1.0)
                    rate_chem = k_rate * np.power(c_pos, n_vec_opt[None, :])
                    if np.any(m_vec_opt > 0.0):
                        m_fac = np.where(m_vec_opt[None, :] > 0.0, np.power(alpha_pos, m_vec_opt[None, :]), 1.0)
                        rate_chem *= m_fac

                    rate_mat = np.where(c_mat > 1e-6, rate_chem, 0.0)
                    p_chem_inst = m_layer * np.dot(rate_mat, H_vec_opt)

                    dT_rate = (q_ht + p_chem_inst) / c_th_layer
                    max_dc_rate = float(np.max(rate_mat))
                    max_dT_rate = float(np.max(np.abs(dT_rate)))

                    dt_c_lim = (dc_tol / max_dc_rate) if max_dc_rate > 1e-12 else rem_time
                    dt_T_lim = (dT_tol / max_dT_rate) if max_dT_rate > 1e-12 else rem_time
                    dt_sub = min(rem_time, 0.9 * dt_max_strict, dt_c_lim, dt_T_lim)
                    dt_sub = max(dt_sub, min(rem_time, dt_sub_min))

                    delta_c = np.minimum(c_mat, rate_mat * dt_sub)
                    c_mat -= delta_c
                    dq_chem_layers = m_layer * np.dot(delta_c, H_vec_opt)
                    T_layers += (q_ht * dt_sub + dq_chem_layers) / c_th_layer
                    t_sub_curr += dt_sub
                    sub_cnt += 1
                    if sub_cnt > 2500:
                        break

                T_mean_hist[i + 1] = np.mean(T_layers)
                if use_mean_temp:
                    T_target_hist[i + 1] = T_mean_hist[i + 1]
                else:
                    T_target_hist[i + 1] = T_layers[target_layer_idx]

            diff = T_target_hist - T_exp_interp
            rmse_val = float(np.sqrt(np.mean(diff ** 2)))
            mae_val = float(np.mean(np.abs(diff)))
            max_err_val = float(np.max(np.abs(diff)))

            result = {
                "opt_reactions": opt_reactions,
                "param_lines": opt_lines,
                "t_arr": t_arr,
                "t_exp": t_exp,
                "T_exp": T_exp,
                "T_exp_interp": T_exp_interp,
                "T_sim_target": T_target_hist,
                "T_sim_mean": T_mean_hist,
                "rmse": rmse_val,
                "mae": mae_val,
                "max_err": max_err_val,
                "elapsed": elapsed_total,
                "target_name": "全域均温 Tmean" if use_mean_temp else f"测点位置 (z/L={pos_mode})",
                "th_end": th_end,
                "best_fit": pso.gbest_fit
            }
            self.finished_signal.emit(result)

        except Exception as e:
            if self._is_cancelled:
                self.progress_signal.emit("[中止] 1D TR 辨识进程已安全终止。")
                return
            import traceback
            err = traceback.format_exc()
            self.error_signal.emit(f"1D TR 辨识异常: {err}")
        finally:
            if self.pool is not None:
                try:
                    self.pool.terminate()
                except Exception:
                    pass
                self.pool = None


# ------------------------------------------------------------------------------
# 3. ARC 绝热热失控动力学参数辨识后台线程 (Nelder-Mead / PSO / 混合算法)
# ------------------------------------------------------------------------------
class ARCIdentifyWorker(QThread):
    progress_signal = Signal(str)
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, config):
        super().__init__()
        self.config = config
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            cfg = self.config
            t_orig = np.array(cfg["t_s"], dtype=np.float64)
            T_orig_C = np.array(cfg["T_C"], dtype=np.float64)

            # 用户输入的 T1 和 T3 (摄氏度)
            T1_input_C = float(cfg.get("T1", T_orig_C[0] + 273.15)) - 273.15
            T3_input_C = float(cfg.get("T3", np.max(T_orig_C) + 273.15)) - 273.15

            if T3_input_C <= T1_input_C + 1.0:
                raise ValueError(f"最高温 T3 ({T3_input_C:.1f}°C) 必须显著高于起始温 T1 ({T1_input_C:.1f}°C)")

            # ------------------------------------------------------------------
            # 自动查找并裁剪属于 T1 <= T <= T3 的 [t, T] 纯净自发热区间
            # 兼容完整实际时序: T0(等待) -> T1(自发热) -> T2 -> T3(失控峰值) -> T4(降温吹扫, T4 < T3)
            # ------------------------------------------------------------------
            # 1. 查找满足 T >= T1 的第一个数据点 (起始自发热时刻 t1)
            idx_ge_t1 = np.where(T_orig_C >= T1_input_C)[0]
            if len(idx_ge_t1) == 0:
                raise ValueError(f"实测数据最高温仅 {np.max(T_orig_C):.1f}°C，未达到设定的自发热起始 T1 ({T1_input_C:.1f}°C)！")
            idx_start = int(idx_ge_t1[0])

            # 2. 查找在 idx_start 之后首次达到 T3 的索引 (终止失控峰值时刻 t3)
            # 先定位 idx_start 之后的全局峰值索引，防止误跨入降温段 T4
            idx_max = idx_start + int(np.argmax(T_orig_C[idx_start:]))

            # 在 idx_start 到 idx_max 之间查找首次达到 T3_input_C 的点
            idx_ge_t3 = np.where(T_orig_C[idx_start:idx_max + 1] >= T3_input_C)[0]
            if len(idx_ge_t3) > 0:
                idx_end = idx_start + int(idx_ge_t3[0])
            else:
                idx_end = idx_max

            if idx_end <= idx_start + 3:
                raise ValueError(
                    f"在 [T1={T1_input_C:.1f}°C, T3={T3_input_C:.1f}°C] 范围内裁剪到的数据点过少 "
                    f"(仅 {idx_end - idx_start + 1} 个点)，请检查输入的 T1 和 T3 是否合理！"
                )

            # 裁剪获得真实、纯净的 T1~T3 自发热升温段数据
            t_crop = t_orig[idx_start:idx_end + 1]
            T_crop_C = T_orig_C[idx_start:idx_end + 1]

            # 标记点坐标 (t, T) 用于绘图显示 * 标注 (精确定位在真实的自发热起点 T1 与失控峰值点 T3)
            pt_t1 = (float(t_crop[0]), float(T_crop_C[0]))
            pt_t3 = (float(t_crop[-1]), float(T_crop_C[-1]))

            # 延长因子处理 (默认 1.35，若 > 1.0 则将数据范围延长至 ext_factor * t3，在 t > t3 延长段温度恒为 T3，a=0)
            ext_factor = float(cfg.get("ext_factor", 1.35))
            if ext_factor > 1.0:
                t_dur = t_crop[-1] - t_crop[0]
                t_ext_end = t_crop[0] + t_dur * ext_factor
                dt_step = max(0.5, (t_crop[1] - t_crop[0]) if len(t_crop) > 1 else 1.0)
                t_ext = np.arange(t_crop[-1] + dt_step, t_ext_end + dt_step * 0.5, dt_step)
                if len(t_ext) > 0:
                    T_ext_C = np.full_like(t_ext, T3_input_C)
                    t_ident = np.concatenate([t_crop, t_ext])
                    T_ident_C = np.concatenate([T_crop_C, T_ext_C])
                else:
                    t_ident = t_crop
                    T_ident_C = T_crop_C
            else:
                t_ident = t_crop
                T_ident_C = T_crop_C

            T_ident_K = T_ident_C + 273.15
            T1 = T1_input_C + 273.15
            T3 = T3_input_C + 273.15
            deltaT = T3 - T1

            rho = float(cfg.get("rho", 2400.0))
            cp = float(cfg.get("cp", 1071.0))
            R = float(cfg.get("R", 8.314))
            HW = cp * rho * deltaT  # J/m^3
            H_per_g = cp * deltaT / 1000.0  # 反应热 J/g

            # 统一状态变量 a 为剩余浓度: a 从 1.0 -> 0.0 (与 1D TR 的 c 严格一致，t > t3 时 a = 0.0)
            a_exp = np.clip((T3 - T_ident_K) / deltaT, 0.0, 1.0)

            method_mode = cfg.get("method_mode", "hybrid")  # 'nm', 'pso', 'hybrid'

            # 是否辨识自催化级数 m
            init_m = float(cfg.get("init_m", 0.0))
            identify_m = (init_m > 0.0)

            ext_info = f", 延长因子={ext_factor:.2f}(覆盖至 {t_ident[-1]:.1f}s)" if ext_factor > 1.0 else " (未延长)"
            self.progress_signal.emit(
                f"[ARC 数据裁剪与延长完成] 截取自发热段: t=[{t_crop[0]:.1f}s ~ {t_crop[-1]:.1f}s]{ext_info}, "
                f"T1={pt_t1[1]:.1f}°C, T3={pt_t3[1]:.1f}°C, 总计算点数={len(t_ident)}"
            )
            self.progress_signal.emit(
                f"[ARC 辨识启动] 模式={method_mode}, 自催化m={'参与辨识' if identify_m else '关闭(经典一方程)'}, "
                f"T1={T1_input_C:.1f}°C, T3={T3_input_C:.1f}°C, ΔT={deltaT:.1f}K"
            )

            # ODE 动力学模型: da/dt = -A * exp(-E / (R*T)) * a^n * (1 - a)^m
            # 其中 a 从 1.0 -> 0.0 (剩余浓度), 转化率 alpha = 1 - a (0 -> 1)
            # 温度关系: T = T1 + (1 - a) * deltaT
            def ode_rhs(t, y, A, E, n, m_val):
                a_val = float(y[0])
                a_clamped = min(max(a_val, 0.0), 1.0)
                T_curr = T1 + (1.0 - a_clamped) * deltaT
                rt = R * T_curr
                if rt <= 0:
                    return [0.0]
                exp_arg = -E / rt
                exp_term = np.exp(np.clip(exp_arg, -500.0, 50.0))
                deg_n = a_clamped ** n
                if m_val > 0.0:
                    deg_m = (1.0 - a_clamped) ** m_val
                    rate = A * exp_term * deg_n * deg_m
                else:
                    rate = A * exp_term * deg_n

                if np.isnan(rate) or np.isinf(rate):
                    return [0.0]
                rate_clamped = float(np.clip(rate, 0.0, 1e8))
                return [-rate_clamped]

            # 目标评估函数
            def fitness(x):
                if self._is_cancelled:
                    return 1e9
                try:
                    ln_A = float(np.clip(x[0], -20.0, 80.0))
                    ln_E = float(np.clip(x[1], 4.0, 30.0))
                    n = float(np.clip(x[2], 0.0, 10.0))
                    m_val = float(np.clip(x[3], 0.0, 5.0)) if identify_m else 0.0

                    A = np.exp(ln_A)
                    E = np.exp(ln_E)

                    t_eval, sol_y, success = rk45_adaptive(
                        fun=lambda tc, yc: ode_rhs(tc, yc, A, E, n, m_val),
                        t_span=(t_ident[0], t_ident[-1]),
                        y0=[1.0],
                        t_eval=t_ident,
                        rtol=1e-5,
                        atol=1e-6
                    )
                    if not success or sol_y.shape[1] != len(t_ident) or np.any(np.isnan(sol_y)) or np.any(np.isinf(sol_y)):
                        return 1e6

                    acal = np.clip(sol_y[0], 0.0, 1.0)
                    rmse = float(np.sqrt(np.mean((acal - a_exp) ** 2)))
                    if np.isnan(rmse) or np.isinf(rmse):
                        return 1e6
                    return rmse
                except Exception:
                    return 1e6

            # 搜索界限与初始值
            # x = [ln(A), ln(E), n, (可选 m)]
            lb_lnA = float(cfg.get("lb_lnA", 5.0))
            ub_lnA = float(cfg.get("ub_lnA", 65.0))
            lb_lnE = float(cfg.get("lb_lnE", 8.0))
            ub_lnE = float(cfg.get("ub_lnE", 16.0))
            lb_n = float(cfg.get("lb_n", 0.2))
            ub_n = float(cfg.get("ub_n", 4.5))

            if identify_m:
                lb_m = float(cfg.get("lb_m", 0.0))
                ub_m = float(cfg.get("ub_m", 2.0))
                lb = np.array([lb_lnA, lb_lnE, lb_n, lb_m], dtype=np.float64)
                ub = np.array([ub_lnA, ub_lnE, ub_n, ub_m], dtype=np.float64)
                init_x_3 = list(cfg.get("init_x", [22.7570, 11.6706, 3.2781]))[:3]
                init_x = np.array(init_x_3 + [init_m], dtype=np.float64)
            else:
                lb = np.array([lb_lnA, lb_lnE, lb_n], dtype=np.float64)
                ub = np.array([ub_lnA, ub_lnE, ub_n], dtype=np.float64)
                init_x = np.array(list(cfg.get("init_x", [22.7570, 11.6706, 3.2781]))[:3], dtype=np.float64)

            best_x = init_x.copy()
            best_rmse = np.inf
            t_start = time.perf_counter()

            # 1. PSO 粗辨识阶段
            if method_mode in ["pso", "hybrid"]:
                pso_particles = int(cfg.get("pso_particles", 30))
                pso_iters = int(cfg.get("pso_iters", 25 if method_mode == "hybrid" else 45))
                self.progress_signal.emit(f"  [步骤 1] 运行全域 PSO 粗辨识 (粒子数={pso_particles}, 代数={pso_iters})...")

                pso = InternalPSO(
                    fitness_func=fitness,
                    dim=len(lb),
                    bounds=(lb, ub),
                    n_particles=pso_particles,
                    max_iter=pso_iters,
                    seed=42
                )
                for step in range(pso_iters):
                    if self._is_cancelled:
                        self.progress_signal.emit("[中止] ARC 辨识已由用户中止。")
                        return
                    bx, bfit = pso.optimize_step(step)
                    if (step + 1) % 5 == 0 or step == pso_iters - 1:
                        self.progress_signal.emit(f"    PSO 迭代 {step+1}/{pso_iters} | 当前最优 RMSE={bfit:.6e}")
                best_x = pso.gbest_X
                best_rmse = pso.gbest_fit

            # 2. Nelder-Mead 精细辨识阶段
            if method_mode in ["nm", "hybrid"]:
                if self._is_cancelled:
                    self.progress_signal.emit("[中止] ARC 辨识已由用户中止。")
                    return
                if method_mode == "hybrid":
                    self.progress_signal.emit("  [步骤 2] 基于 PSO 最优解，执行 Nelder-Mead 局部精细辨识...")
                    x_start = best_x
                else:
                    self.progress_signal.emit("  执行 Nelder-Mead 单纯形法辨识 (基于面板设定初值)...")
                    x_start = init_x

                nm_res = nelder_mead(
                    func=fitness,
                    x0=x_start,
                    bounds=(lb, ub),
                    tol=1e-8,
                    max_iter=int(cfg.get("nm_max_iter", 1500)),
                    max_fev=int(cfg.get("nm_max_fev", 3000))
                )
                if self._is_cancelled:
                    self.progress_signal.emit("[中止] ARC 辨识已由用户中止。")
                    return
                if nm_res["fun"] < best_rmse:
                    best_x = nm_res["x"]
                    best_rmse = nm_res["fun"]
                self.progress_signal.emit(f"    Nelder-Mead 完成 (迭代 {nm_res['nit']} 次) | 拟合 RMSE={best_rmse:.6e}")

            if self._is_cancelled:
                self.progress_signal.emit("[中止] ARC 辨识已由用户中止。")
                return

            elapsed = time.perf_counter() - t_start

            # 提取物理参数
            A_opt = float(np.exp(best_x[0]))
            E_opt = float(np.exp(best_x[1]))
            n_opt = float(best_x[2])
            m_opt = float(best_x[3]) if identify_m else 0.0

            # 最终高精度模拟计算用于绘图 (根据 ext_factor 覆盖至延长点，显示 T3 贴合平台)
            _, sol_final, _ = rk45_adaptive(
                fun=lambda tc, yc: ode_rhs(tc, yc, A_opt, E_opt, n_opt, m_opt),
                t_span=(t_ident[0], t_ident[-1]),
                y0=[1.0],
                t_eval=t_ident,
                rtol=1e-6,
                atol=1e-8
            )
            acal = np.clip(sol_final[0], 0.0, 1.0)
            Tc_K = T1 + (1.0 - acal) * deltaT
            Tc_C = Tc_K - 273.15

            m_str = f", m={m_opt:.4f}" if identify_m else ""
            self.progress_signal.emit(
                f"[ARC 辨识成功] A={A_opt:.4e} 1/s, E={E_opt:.2f} J/mol ({E_opt/1000:.2f} kJ/mol), "
                f"n={n_opt:.4f}{m_str}, H_eq={H_per_g:.2f} J/g, 耗时={elapsed:.2f}s"
            )

            res = {
                "A": A_opt,
                "Ea": E_opt,
                "n": n_opt,
                "m": m_opt,
                "H": H_per_g,  # J/g
                "rmse": best_rmse,
                "elapsed": elapsed,
                "T1_C": T1_input_C,
                "T3_C": T3_input_C,
                "deltaT": deltaT,
                "ext_factor": ext_factor,
                "t_crop": t_ident,
                "t_all": t_ident,  # 兼容旧字段
                "t_real_crop": t_crop,
                "t_orig": t_orig,
                "a_exp": a_exp,
                "a_cal": acal,
                "alpha_exp": a_exp,
                "alphacal": acal,
                "T_exp_C": T_ident_C,
                "T_sim_C": Tc_C,
                "pt_t1": pt_t1,
                "pt_t3": pt_t3,
                "identify_m": identify_m
            }
            self.finished_signal.emit(res)

        except Exception as e:
            if self._is_cancelled:
                self.progress_signal.emit("[中止] ARC 辨识已安全终止。")
                return
            import traceback
            err = traceback.format_exc()
            self.error_signal.emit(f"ARC 辨识异常: {err}")
