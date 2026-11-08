"""
DataPlot_optimization_core.py:
轻量级纯 NumPy 科学计算优化核心模块
提供：
1. nelder_mead: 纯 NumPy 下山单纯形法 (Downhill Simplex / Nelder-Mead)，无需依赖 scipy.optimize.minimize
2. rk45_adaptive: 纯 NumPy 自适应步长 Runge-Kutta-Fehlberg (RK45) 常微分方程求解器，无需依赖 scipy.integrate.solve_ivp
3. VectorizedPSO: 纯 NumPy 向量化粒子群算法基类 / 求解器
零 scipy 依赖，打包为可执行文件体积 0 额外增长。
"""

import numpy as np

def rk45_adaptive(fun, t_span, y0, t_eval=None, rtol=1e-6, atol=1e-8, max_step=np.inf):
    """
    纯 NumPy 实现的自适应步长 Dormand-Prince (RK45) ODE 求解器。
    
    参数:
        fun: 可调用函数 f(t, y) -> dy/dt (标量或 1D array)
        t_span: (t0, tf) 时间区间
        y0: 初始状态 (float 或 1D array)
        t_eval: 需要评估输出的时间点网格 (可选)
        rtol: 相对容差
        atol: 绝对容差
        max_step: 单步允许最大步长
    返回:
        sol_t: 求解的时间序列
        sol_y: 求解的状态序列 (len(y0), len(sol_t))
        success: 是否成功求解
    """
    t0, tf = float(t_span[0]), float(t_span[1])
    direction = 1.0 if tf >= t0 else -1.0
    y = np.array(y0, dtype=np.float64)
    if y.ndim == 0:
        y = y.reshape(1)
        
    # Dormand-Prince Butcher 表系数
    c = np.array([0.0, 1/5, 3/10, 4/5, 8/9, 1.0, 1.0], dtype=np.float64)
    a = [
        [],
        [1/5],
        [3/40, 9/40],
        [44/45, -56/15, 32/9],
        [19372/6561, -25360/2187, 64448/6561, -212/729],
        [9017/3168, -355/33, 46732/5247, 49/176, -5103/18656],
        [35/384, 0.0, 500/1113, 125/192, -2187/6784, 11/84]
    ]
    b = np.array([35/384, 0.0, 500/1113, 125/192, -2187/6784, 11/84, 0.0], dtype=np.float64)
    b_hat = np.array([5179/57600, 0.0, 7571/16695, 393/640, -92097/339200, 187/2100, 1/40], dtype=np.float64)

    t = t0
    h = min(1.0, abs(tf - t0) * 0.05, max_step) * direction

    ts = [t]
    ys = [y.copy()]

    f0 = np.array(fun(t, y), dtype=np.float64)
    
    max_steps = 100000
    step_count = 0

    while (tf - t) * direction > 1e-12 and step_count < max_steps:
        step_count += 1
        if abs(h) > abs(tf - t):
            h = tf - t
            
        k = [f0]
        step_eval_failed = False
        for s in range(1, 7):
            dy_stage = np.zeros_like(y)
            for j in range(s):
                dy_stage += a[s][j] * k[j]
            t_stage = t + c[s] * h
            y_stage = y + h * dy_stage
            try:
                f_val = np.array(fun(t_stage, y_stage), dtype=np.float64)
                if np.any(np.isnan(f_val)) or np.any(np.isinf(f_val)):
                    step_eval_failed = True
                    break
                k.append(f_val)
            except Exception:
                step_eval_failed = True
                break

        if step_eval_failed:
            h *= 0.2
            if abs(h) < 1e-14:
                return np.array(ts), np.column_stack(ys), False
            continue
            
        # 5阶解与4阶解误差估计
        y5 = y + h * sum(b[j] * k[j] for j in range(7))
        y4 = y + h * sum(b_hat[j] * k[j] for j in range(7))
        err = np.abs(y5 - y4)
        
        tol = atol + rtol * np.maximum(np.abs(y), np.abs(y5))
        err_ratio = np.max(err / tol)
        
        if np.isnan(err_ratio) or np.isinf(err_ratio):
            h *= 0.2
            if abs(h) < 1e-14:
                return np.array(ts), np.column_stack(ys), False
            continue

        if err_ratio <= 1.0:
            # 步长接受
            t += h
            y = y5
            f0 = k[6] # FSAL (First Same As Last)
            ts.append(t)
            ys.append(y.copy())
            
            # 预测下一适宜步长
            factor = 0.9 * (err_ratio ** -0.2) if err_ratio > 1e-4 else 2.0
            factor = min(max(factor, 0.2), 5.0)
            h = min(abs(h * factor), max_step) * direction
        else:
            # 步长拒绝，缩减步长重新计算
            factor = max(0.9 * (err_ratio ** -0.25), 0.1)
            h *= factor
            if abs(h) < 1e-14:
                return np.array(ts), np.column_stack(ys), False

    if step_count >= max_steps:
        return np.array(ts), np.column_stack(ys), False

    ts_arr = np.array(ts)
    ys_mat = np.column_stack(ys) # (dim, len(ts))
    
    if t_eval is not None:
        t_eval = np.asarray(t_eval, dtype=np.float64)
        ys_interp = np.zeros((len(y0), len(t_eval)), dtype=np.float64)
        for d in range(len(y0)):
            ys_interp[d, :] = np.interp(t_eval, ts_arr, ys_mat[d, :])
        return t_eval, ys_interp, True

    return ts_arr, ys_mat, True


def nelder_mead(func, x0, args=(), bounds=None, tol=1e-8, max_iter=2000, max_fev=4000):
    """
    纯 NumPy 实现的下山单纯形优化算法 (Nelder-Mead / fminsearch)
    
    参数:
        func: 目标适应度函数 f(x, *args) -> float
        x0: 初始决策变量向量 (d,)
        args: 目标函数附加参数元组
        bounds: 决策变量范围约束元组 (lb, ub) (可选，自动通过惩罚函数限幅)
        tol: 收敛残差阈值
        max_iter: 最大单纯形迭代代数
        max_fev: 最大函数评估次数
    返回:
        res: 包含 'x', 'fun', 'nit', 'nfev', 'success' 的字典对象
    """
    x0 = np.asarray(x0, dtype=np.float64)
    n = len(x0)
    
    if bounds is not None:
        lb = np.asarray(bounds[0], dtype=np.float64)
        ub = np.asarray(bounds[1], dtype=np.float64)
    else:
        lb, ub = None, None
        
    def _obj(x):
        if lb is not None and ub is not None:
            # 边界罚函数
            violation = np.maximum(lb - x, 0.0) + np.maximum(x - ub, 0.0)
            if np.any(violation > 0):
                return 1e10 + 1e8 * float(np.sum(violation ** 2))
        return float(func(x, *args))

    # 标准单纯形更新超参数
    alpha = 1.0   # 反射
    gamma = 2.0   # 扩张
    rho = 0.5     # 收缩
    sigma = 0.5   # 缩减

    # 构建初始单纯形顶点 (n + 1 个点)
    sim = np.zeros((n + 1, n), dtype=np.float64)
    sim[0] = x0
    for i in range(n):
        point = x0.copy()
        if abs(point[i]) > 1e-4:
            point[i] *= 1.05
        else:
            point[i] = 0.001
        sim[i + 1] = point

    fsim = np.zeros(n + 1, dtype=np.float64)
    for i in range(n + 1):
        fsim[i] = _obj(sim[i])
    nfev = n + 1

    nit = 0
    while nit < max_iter and nfev < max_fev:
        # 排序
        order = np.argsort(fsim)
        sim = sim[order]
        fsim = fsim[order]

        # 检查收敛条件
        if np.max(np.abs(sim[1:] - sim[0])) < tol and (fsim[-1] - fsim[0]) < tol:
            break

        # 计算除最差点之外的前 n 个点的重心
        x_bar = np.mean(sim[:-1], axis=0)

        # 1. 反射操作 (Reflection)
        x_r = x_bar + alpha * (x_bar - sim[-1])
        f_r = _obj(x_r)
        nfev += 1

        if fsim[0] <= f_r < fsim[-2]:
            sim[-1] = x_r
            fsim[-1] = f_r
        elif f_r < fsim[0]:
            # 2. 扩张操作 (Expansion)
            x_e = x_bar + gamma * (x_r - x_bar)
            f_e = _obj(x_e)
            nfev += 1
            if f_e < f_r:
                sim[-1] = x_e
                fsim[-1] = f_e
            else:
                sim[-1] = x_r
                fsim[-1] = f_r
        else:
            # 3. 收缩操作 (Contraction)
            if f_r < fsim[-1]:
                # 外部收缩
                x_c = x_bar + rho * (x_r - x_bar)
                f_c = _obj(x_c)
                nfev += 1
                if f_c <= f_r:
                    sim[-1] = x_c
                    fsim[-1] = f_c
                else:
                    # 缩减
                    for i in range(1, n + 1):
                        sim[i] = sim[0] + sigma * (sim[i] - sim[0])
                        fsim[i] = _obj(sim[i])
                    nfev += n
            else:
                # 内部收缩
                x_c = x_bar - rho * (x_bar - sim[-1])
                f_c = _obj(x_c)
                nfev += 1
                if f_c < fsim[-1]:
                    sim[-1] = x_c
                    fsim[-1] = f_c
                else:
                    # 缩减
                    for i in range(1, n + 1):
                        sim[i] = sim[0] + sigma * (sim[i] - sim[0])
                        fsim[i] = _obj(sim[i])
                    nfev += n
        nit += 1

    order = np.argsort(fsim)
    return {
        'x': sim[order[0]],
        'fun': fsim[order[0]],
        'nit': nit,
        'nfev': nfev,
        'success': nit < max_iter and nfev < max_fev
    }

