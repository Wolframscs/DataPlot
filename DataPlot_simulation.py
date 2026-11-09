"""
DataPlot_simulation.py:
DataPlot 热失控仿真与动力学参数辨识综合模块
包含三大模式：
1. 1D 热失控仿真 (1D TR Forward Simulation)
2. 1D 热失控副反应多步动力学 PSO 参数辨识 (1D TR Parameter Identification)
3. ARC 绝热热失控动力学参数辨识 (ARC 1-Equation Identification, 纯 NumPy Nelder-Mead + RK45 + PSO)
全部使用纯内置算法，零 scipy 依赖，完美兼顾打包 exe 极小体积与多反应兼容扩展性。
"""

import os
import json
import time
import numpy as np
import pandas as pd
from PySide6.QtCore import QObject, Signal, QThread, Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QComboBox, QFileDialog,
    QMessageBox, QTableWidget, QTableWidgetItem, QHeaderView, QScrollArea, QFrame,
    QMenu, QApplication
)
import matplotlib.pyplot as plt
from matplotlib.colors import Normalize

from DataPlot_identification_engine import TR1DIdentifyWorker, ARCIdentifyWorker


class CopyPasteTableWidget(QTableWidget):
    """支持 Ctrl+A 全选、Ctrl+C 复制 (TSV格式，可直接贴入Excel)、Ctrl+V 粘贴 (从Excel或文本) 的增强表格"""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

    def keyPressEvent(self, event):
        if event.matches(QKeySequence.SelectAll) or (event.key() == Qt.Key_A and event.modifiers() == Qt.ControlModifier):
            self.selectAll()
            event.accept()
            return
        if event.matches(QKeySequence.Copy) or (event.key() == Qt.Key_C and event.modifiers() == Qt.ControlModifier):
            self.copy_selection_to_clipboard()
            event.accept()
            return
        if event.matches(QKeySequence.Paste) or (event.key() == Qt.Key_V and event.modifiers() == Qt.ControlModifier):
            self.paste_from_clipboard()
            event.accept()
            return
        super().keyPressEvent(event)

    def _show_context_menu(self, pos):
        menu = QMenu(self)
        copy_act = menu.addAction("复制选中区域 (Ctrl+C)")
        paste_act = menu.addAction("粘贴数据 (Ctrl+V)")
        menu.addSeparator()
        select_all_act = menu.addAction("全选所有单元格 (Ctrl+A)")

        copy_act.triggered.connect(self.copy_selection_to_clipboard)
        paste_act.triggered.connect(self.paste_from_clipboard)
        select_all_act.triggered.connect(self.selectAll)
        menu.exec(self.mapToGlobal(pos))

    def copy_selection_to_clipboard(self):
        ranges = self.selectedRanges()
        if not ranges:
            return

        top_row = min(r.topRow() for r in ranges)
        bottom_row = max(r.bottomRow() for r in ranges)
        left_col = min(r.leftColumn() for r in ranges)
        right_col = max(r.rightColumn() for r in ranges)

        lines = []
        for r in range(top_row, bottom_row + 1):
            row_vals = []
            for c in range(left_col, right_col + 1):
                item = self.item(r, c)
                row_vals.append(item.text() if item else "")
            lines.append("\t".join(row_vals))

        clipboard_text = "\n".join(lines)
        QApplication.clipboard().setText(clipboard_text)

    def paste_from_clipboard(self):
        text = QApplication.clipboard().text()
        if not text:
            return

        lines = [line for line in text.strip("\r\n").split("\n") if line.strip()]
        if not lines:
            return

        start_row = self.currentRow()
        start_col = self.currentColumn()
        if start_row < 0:
            start_row = 0
        if start_col < 0:
            start_col = 0

        needed_rows = start_row + len(lines)
        while self.rowCount() < needed_rows:
            self.insertRow(self.rowCount())

        for r_offset, line in enumerate(lines):
            r = start_row + r_offset
            if "\t" in line:
                cells = line.split("\t")
            elif "," in line:
                cells = line.split(",")
            else:
                cells = line.split()

            for c_offset, cell_val in enumerate(cells):
                c = start_col + c_offset
                if c < self.columnCount():
                    val_str = cell_val.strip().strip('"').strip("'")
                    item = self.item(r, c)
                    if item is None:
                        item = QTableWidgetItem(val_str)
                        self.setItem(r, c, item)
                    else:
                        item.setText(val_str)


# 默认仿真控制、物性与动力学参数字典
DEFAULT_SIM_PARAMS = {
    "control": {
        "n_layers": 50,
        "dt": 0.5,
        "t_max": 3600.0,
        "th_end": 1000.0,
        "Ph": 300.0,
        "h_conv": 5.0,
        "T_init": 25.0,
        "T_amb": 25.0,
        "t_print": 100.0,
        "dc_tol": 0.01,
        "dT_tol": 1.5,
        "dt_sub_min": 1e-6,
        "k_max": 1000.0,
        "pso_workers": 16,
        "pso_particles": 36,
        "pso_iters": 35,
    },
    "geometry": {
        "H_m": 0.155,
        "W_m": 0.289,
        "L_m": 0.048,
        "M_cell": 3.8,
        "Cp": 1055.0,
        "lambda_thick": 1.56,
        "M_act": 2859.0,
        "GAS_R": 8.314,
    },
    "reactions": [
        {"name": "SEI",         "A": 1.667e+14, "Ea": 1.3508e+05, "H": 157.0, "n": 1.0, "m": 0.0, "c0": 1.0},
        {"name": "Anode+ELE",   "A": 2.500e+13, "Ea": 1.3508e+05, "H": 214.0, "n": 1.0, "m": 0.0, "c0": 1.0},
        {"name": "Binder",      "A": 5.140e+15, "Ea": 2.7400e+05, "H": 155.0, "n": 1.0, "m": 0.0, "c0": 1.0},
        {"name": "Cathode+ELE", "A": 2.000e+08, "Ea": 1.0300e+05, "H": 194.7, "n": 1.0, "m": 1.0, "c0": 1.0},
    ]
}

# 1D TR 副反应多步动力学辨识默认反应与物理搜索上下限 [lb, ub] (A统一为物理值，且支持m上下限)
DEFAULT_IDENT_REACTIONS = [
    {
        "name": "SEI",
        "A": 1.667e+14, "Ea": 1.3508e+05, "H": 157.0, "n": 1.000, "m": 0.000, "c0": 1.0,
        "lb_A": 3.1623e+06, "ub_A": 1.0000e+10,
        "lb_logA": 6.5, "ub_logA": 10.0,
        "lb_Ea": 7.0e4, "ub_Ea": 1.0e5,
        "lb_H": 5.0, "ub_H": 45.0,
        "lb_n": 1.0, "ub_n": 3.0,
        "lb_m": 0.0, "ub_m": 2.0,
    },
    {
        "name": "Anode+ELE",
        "A": 2.500e+13, "Ea": 1.3508e+05, "H": 214.0, "n": 1.000, "m": 0.000, "c0": 1.0,
        "lb_A": 1.0000e+18, "ub_A": 1.0000e+23,
        "lb_logA": 18.0, "ub_logA": 23.0,
        "lb_Ea": 2.2e5, "ub_Ea": 2.75e5,
        "lb_H": 220.0, "ub_H": 420.0,
        "lb_n": 0.8, "ub_n": 2.5,
        "lb_m": 0.0, "ub_m": 2.0,
    },
    {
        "name": "Binder",
        "A": 5.140e+15, "Ea": 2.7400e+05, "H": 155.0, "n": 1.000, "m": 0.000, "c0": 1.0,
        "lb_A": 1.0000e+13, "ub_A": 1.0000e+17,
        "lb_logA": 13.0, "ub_logA": 17.0,
        "lb_Ea": 1.7e5, "ub_Ea": 2.3e5,
        "lb_H": 50.0, "ub_H": 160.0,
        "lb_n": 1.0, "ub_n": 3.0,
        "lb_m": 0.0, "ub_m": 2.0,
    },
    {
        "name": "Cathode+ELE",
        "A": 2.000e+08, "Ea": 1.0300e+05, "H": 194.7, "n": 1.000, "m": 0.000, "c0": 1.0,
        "lb_A": 3.1623e+03, "ub_A": 3.1623e+07,
        "lb_logA": 3.5, "ub_logA": 7.5,
        "lb_Ea": 7.5e4, "ub_Ea": 1.15e5,
        "lb_H": 350.0, "ub_H": 600.0,
        "lb_n": 1.0, "ub_n": 2.5,
        "lb_m": 0.0, "ub_m": 2.0,
    },
]


class SimulationWorker(QThread):
    """1D TR 热失控仿真计算线程"""
    progress_signal = Signal(str)
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, params):
        super().__init__()
        self.params = params
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        try:
            ctrl = self.params["control"]
            geom = self.params["geometry"]
            rxns = self.params["reactions"]

            n_layers = int(ctrl.get("n_layers", 50))
            dt = float(ctrl.get("dt", 0.5))
            t_max = float(ctrl.get("t_max", 3600.0))
            th_end = float(ctrl.get("th_end", 1000.0))
            Ph = float(ctrl.get("Ph", 300.0))
            h_conv = float(ctrl.get("h_conv", 5.0))
            T_init = float(ctrl.get("T_init", 25.0))
            T_amb = float(ctrl.get("T_amb", 25.0))
            t_print = float(ctrl.get("t_print", 100.0))
            dc_tol = float(ctrl.get("dc_tol", 0.01))
            dT_tol = float(ctrl.get("dT_tol", 1.5))
            dt_sub_min = float(ctrl.get("dt_sub_min", 1e-6))
            k_max = float(ctrl.get("k_max", 1000.0))

            H_m = float(geom.get("H_m", 0.155))
            W_m = float(geom.get("W_m", 0.289))
            L_m = float(geom.get("L_m", 0.048))
            M_cell = float(geom.get("M_cell", 3.8))
            Cp = float(geom.get("Cp", 1055.0))
            lambda_thick = float(geom.get("lambda_thick", 1.56))
            M_act = float(geom.get("M_act", 2859.0))
            GAS_R = float(geom.get("GAS_R", 8.314))

            A_face = H_m * W_m
            C_th = M_cell * Cp

            rxn_names = [r["name"] for r in rxns]
            n_rxns = len(rxn_names)
            c0_vec = np.array([float(r["c0"]) for r in rxns], dtype=float)

            dz = L_m / n_layers
            z_coords = np.linspace(dz / 2.0, L_m - dz / 2.0, n_layers) * 1000.0  # mm
            c_th_layer = C_th / n_layers
            m_layer = M_act / n_layers
            area_side = 2 * (H_m + W_m) * dz

            g_cond = lambda_thick * A_face / dz
            h_layer = np.full(n_layers, h_conv * area_side)
            h_layer[-1] += h_conv * A_face

            dt_max_fo = c_th_layer / (2.0 * g_cond)
            dt_max_strict = c_th_layer / (2.0 * g_cond + np.max(h_layer))

            self.progress_signal.emit(
                f"[1D TR仿真初始化] 网格数 N={n_layers}, 单层厚度 dz={dz*1000:.3f} mm, 临界步长 dt_crit={dt_max_strict:.4f} s"
            )

            n_steps = int(round(t_max / dt)) + 1
            t_arr = np.linspace(0.0, t_max, n_steps)

            T_history = np.zeros((n_steps, n_layers))
            T_layers = np.full(n_layers, T_init)
            T_history[0] = T_layers

            c_mat = np.tile(c0_vec, (n_layers, 1))
            c_history = np.zeros((n_steps, n_layers, n_rxns))
            rate_history = np.zeros((n_steps, n_layers, n_rxns))
            power_history = np.zeros((n_steps, n_layers, n_rxns))
            c_history[0] = c_mat

            chem_rate_history = np.zeros((n_steps, n_rxns))
            rate_mean_history = np.zeros((n_steps, n_rxns))
            rate_max_history = np.zeros((n_steps, n_rxns))
            c_mean_history = np.zeros((n_steps, n_rxns))
            c_mean_history[0] = np.mean(c_mat, axis=0)

            total_substeps = 0
            next_print_t = t_print
            t_wall_start = time.perf_counter()

            for i in range(n_steps - 1):
                if self._is_cancelled:
                    self.progress_signal.emit("仿真已由用户手动中止。")
                    return

                t_sub_curr = t_arr[i]
                t_target = t_arr[i + 1]
                dt_macro = t_target - t_sub_curr

                step_rxn_energy = np.zeros(n_rxns)
                step_dc_mean_sum = np.zeros(n_rxns)
                step_rate_max = np.zeros(n_rxns)
                step_dc_layers = np.zeros((n_layers, n_rxns))
                step_energy_layers = np.zeros((n_layers, n_rxns))

                while t_sub_curr < t_target - 1e-12:
                    rem_time = t_target - t_sub_curr

                    # 1. 传热与表面散热
                    q_ht = np.zeros(n_layers)
                    q_flux = -g_cond * np.diff(T_layers)
                    q_ht[1:] += q_flux
                    q_ht[:-1] -= q_flux
                    q_ht -= h_layer * (T_layers - T_amb)
                    if t_sub_curr <= th_end:
                        q_ht[0] += Ph

                    # 2. 反应速率
                    TK = T_layers + 273.15
                    rt = GAS_R * TK
                    rate_mat = np.zeros((n_layers, n_rxns))
                    p_chem_inst = np.zeros(n_layers)

                    for k, r in enumerate(rxns):
                        c_k = c_mat[:, k]
                        mask = c_k > 1e-6
                        exp_arg = np.clip(-float(r["Ea"]) / rt, -700.0, 50.0)
                        k_rate = np.minimum(float(r["A"]) * np.exp(exp_arg), k_max)
                        c_clip = np.clip(c_k, 0.0, 1.0)
                        f_c = np.power(c_clip, float(r["n"]))
                        if float(r["m"]) != 0.0:
                            f_c = f_c * np.power(np.maximum(1.0 - c_clip, 1e-6), float(r["m"]))
                        rate_k = np.where(mask, k_rate * f_c, 0.0)
                        rate_mat[:, k] = rate_k
                        p_chem_inst += m_layer * float(r["H"]) * rate_k

                    # 3. 温升速率与安全子步长
                    dT_rate = (q_ht + p_chem_inst) / c_th_layer
                    max_dc_rate = float(np.max(rate_mat))
                    max_dT_rate = float(np.max(np.abs(dT_rate)))

                    dt_c_lim = (dc_tol / max_dc_rate) if max_dc_rate > 1e-12 else rem_time
                    dt_T_lim = (dT_tol / max_dT_rate) if max_dT_rate > 1e-12 else rem_time

                    dt_sub = min(rem_time, 0.9 * dt_max_strict, dt_c_lim, dt_T_lim)
                    dt_sub = max(dt_sub, min(rem_time, dt_sub_min))

                    # 4. 推进更新
                    dq_chem_layers = np.zeros(n_layers)
                    for k, r in enumerate(rxns):
                        c_k = c_mat[:, k]
                        delta_c = np.minimum(c_k, rate_mat[:, k] * dt_sub)
                        actual_rate_sub = delta_c / dt_sub
                        c_mat[:, k] -= delta_c
                        dq_layer_rxn = m_layer * float(r["H"]) * delta_c
                        dq_chem_layers += dq_layer_rxn
                        step_rxn_energy[k] += np.sum(dq_layer_rxn)
                        step_dc_mean_sum[k] += np.mean(delta_c)
                        step_rate_max[k] = max(step_rate_max[k], float(np.max(actual_rate_sub)))
                        step_dc_layers[:, k] += delta_c
                        step_energy_layers[:, k] += dq_layer_rxn

                    T_layers = T_layers + (q_ht * dt_sub + dq_chem_layers) / c_th_layer
                    t_sub_curr += dt_sub
                    total_substeps += 1

                chem_rate_history[i] = step_rxn_energy / dt_macro
                rate_mean_history[i] = step_dc_mean_sum / dt_macro
                rate_max_history[i] = step_rate_max
                rate_history[i] = step_dc_layers / dt_macro
                power_history[i] = step_energy_layers / dt_macro
                c_history[i + 1] = c_mat
                c_mean_history[i + 1] = np.mean(c_mat, axis=0)
                T_history[i + 1] = T_layers

                if t_target >= next_print_t - 1e-9 or (i + 1) == (n_steps - 1):
                    pct = 100.0 * t_target / t_max
                    c_mean_str = ", ".join(f"{val:.3f}" for val in c_mean_history[i + 1])
                    msg = (
                        f"  [进度 {pct:5.1f}%] t={t_target:6.1f}/{t_max:.0f}s | "
                        f"T_hot={T_layers[0]:.1f}°C, T_mean={np.mean(T_layers):.1f}°C, T_cold={T_layers[-1]:.1f}°C | "
                        f"c_mean=[{c_mean_str}] | 子步={total_substeps}"
                    )
                    self.progress_signal.emit(msg)
                    while next_print_t <= t_target + 1e-9:
                        next_print_t += t_print

            chem_rate_history[-1] = chem_rate_history[-2]
            rate_mean_history[-1] = rate_mean_history[-2]
            rate_max_history[-1] = rate_max_history[-2]
            rate_history[-1] = rate_history[-2]
            power_history[-1] = power_history[-2]

            elapsed = time.perf_counter() - t_wall_start
            self.progress_signal.emit(
                f"[1D TR仿真完成] 宏步={n_steps-1}, 累计自适应子步={total_substeps}, 耗时={elapsed:.2f}s"
            )

            # 汇总结果
            res = {
                "t_arr": t_arr,
                "z_coords": z_coords,
                "T_history": T_history,
                "c_history": c_history,
                "rate_history": rate_history,
                "power_history": power_history,
                "chem_rate_history": chem_rate_history,
                "rate_mean_history": rate_mean_history,
                "rate_max_history": rate_max_history,
                "c_mean_history": c_mean_history,
                "rxn_names": rxn_names,
                "params": self.params,
                "dt": dt,
                "th_end": th_end,
                "Ph": Ph,
                "t_max": t_max,
                "L_m": L_m,
                "n_layers": n_layers,
                "elapsed": elapsed,
                "total_substeps": total_substeps,
            }
            self.finished_signal.emit(res)
        except Exception as e:
            if self._is_cancelled:
                self.progress_signal.emit("[中止] 仿真计算已安全终止。")
                return
            import traceback
            err_msg = traceback.format_exc()
            self.error_signal.emit(f"仿真计算异常: {err_msg}")


class SimulationMixin:
    """热失控仿真与双动力学辨识挂载 Mixin，整合到 DataPlot GUI"""

    def setup_simulation_ui(self, control_layout):
        """构建仿真与辨识控制面板并加入 control_layout"""
        self.sim_frame = QGroupBox("热失控仿真与动力学参数辨识")
        self.sim_frame.setCheckable(False)

        sim_main_layout = QVBoxLayout(self.sim_frame)
        sim_main_layout.setContentsMargins(8, 10, 8, 8)
        sim_main_layout.setSpacing(8)

        # ----------------------------------------------------------------------
        # 0. 模式选择下拉框 (Mode Switcher)
        # ----------------------------------------------------------------------
        mode_box = QGroupBox("模型与计算模式选择")
        mode_lay = QHBoxLayout(mode_box)
        mode_lay.setContentsMargins(8, 6, 8, 6)
        mode_lay.addWidget(QLabel("当前工作模式:"))

        self.sim_mode_combo = QComboBox()
        self.sim_mode_combo.addItems([
            "1D 热失控仿真计算 (1D TR Simulation)",
            "1D 热失控动力学参数辨识 (1D TR Multi-Reaction )",
            "ARC 热失控动力学参数辨识 (ARC 1-Equation )"
        ])
        self.sim_mode_combo.setStyleSheet("font-weight: bold; color: #1e3a8a;")
        self.sim_mode_combo.currentIndexChanged.connect(self.on_sim_mode_changed)
        mode_lay.addWidget(self.sim_mode_combo, 1)
        sim_main_layout.addWidget(mode_box)

        # ======================================================================
        # 共享组件 1: 电芯物性与几何 (1D 仿真与 1D 辨识共享)
        # ======================================================================
        self.shared_geom_box = QGroupBox("电芯物性与几何")
        self.shared_geom_box.setCheckable(True)
        self.shared_geom_box.setChecked(True)
        geom_content = QWidget()
        geom_grid = QGridLayout(geom_content)
        geom_grid.setContentsMargins(0, 4, 0, 4)
        geom_grid.setHorizontalSpacing(6)
        geom_grid.setVerticalSpacing(4)

        self.sim_geom_inputs = {}
        geom_fields = [
            ("H_m", "电芯高度 H (m):", "0.155", 0, 0),
            ("W_m", "电芯宽度 W (m):", "0.289", 0, 2),
            ("L_m", "电芯厚度 L (m):", "0.048", 1, 0),
            ("M_cell", "电芯质量 (kg):", "3.8", 1, 2),
            ("Cp", "电芯比热容 Cp (J/kg·K):", "1055.0", 2, 0),
            ("lambda_thick", "电芯热导率 λ (W/mK):", "1.56", 2, 2),
            ("M_act", "活性材料质量 M_act (g):", "2859.0", 3, 0),
            ("GAS_R", "理想气体常数 R (J/molK):", "8.314", 3, 2),
        ]
        for key, label_text, default_val, r, c in geom_fields:
            lbl = QLabel(label_text)
            edit = QLineEdit(default_val)
            self.sim_geom_inputs[key] = edit
            geom_grid.addWidget(lbl, r, c)
            geom_grid.addWidget(edit, r, c + 1)

        geom_box_lay = QVBoxLayout(self.shared_geom_box)
        geom_box_lay.setContentsMargins(6, 2, 6, 6)
        geom_box_lay.addWidget(geom_content)
        self.shared_geom_box.toggled.connect(geom_content.setVisible)
        sim_main_layout.addWidget(self.shared_geom_box)

        # ======================================================================
        # 共享组件 2: 计算控制参数 (1D 仿真与 1D 辨识共享，紧随物性几何下方)
        # ======================================================================
        self.shared_ctrl_box = QGroupBox("计算控制参数")
        self.shared_ctrl_box.setCheckable(True)
        self.shared_ctrl_box.setChecked(True)
        ctrl_content = QWidget()
        ctrl_grid = QGridLayout(ctrl_content)
        ctrl_grid.setContentsMargins(0, 4, 0, 4)
        ctrl_grid.setHorizontalSpacing(6)
        ctrl_grid.setVerticalSpacing(4)

        self.sim_ctrl_inputs = {}
        default_cores = str(min(os.cpu_count() or 4, 16))
        ctrl_fields = [
            ("n_layers", "网格层数 N (-):", "50", 0, 0),
            ("dt", "时间步长 dt (s):", "0.5", 0, 2),
            ("t_max", "仿真时间 t_max (s):", "3600.0", 1, 0),
            ("th_end", "加热停止 t_end(s):", "1000.0", 1, 2),
            ("Ph", "加热功率 Ph (W):", "300.0", 2, 0),
            ("h_conv", "对流换热 H (W/m²K):", "5.0", 2, 2),
            ("T_init", "初始温度 (°C):", "25.0", 3, 0),
            ("T_amb", "环境温度 (°C):", "25.0", 3, 2),
            ("dc_tol", "子步浓度容差 dc_tol (-):", "0.01", 4, 0),
            ("dT_tol", "子步温升容差 (°C):", "1.5", 4, 2),
            ("k_max", "反应速率限值 k_max (1/s):", "1000.0", 5, 0),
            ("t_print", "进度打印间隔 (s):", "100.0", 5, 2),
            ("dt_sub_min", "最小时间步长 (s):", "1e-6", 6, 0),
            ("pso_workers", "PSO 并行:", default_cores, 6, 2),
            ("pso_particles", "PSO 种群:", "36", 7, 0),
            ("pso_iters", "PSO 迭代:", "35", 7, 2),
        ]
        for key, label_text, default_val, r, c in ctrl_fields:
            lbl = QLabel(label_text)
            edit = QLineEdit(default_val)
            self.sim_ctrl_inputs[key] = edit
            ctrl_grid.addWidget(lbl, r, c)
            ctrl_grid.addWidget(edit, r, c + 1)

        # 保留快速别名，供辨识模块与旧接口直接读取
        self.tr_pso_particles_edit = self.sim_ctrl_inputs["pso_particles"]
        self.tr_pso_iters_edit = self.sim_ctrl_inputs["pso_iters"]
        self.tr_ident_workers_edit = self.sim_ctrl_inputs["pso_workers"]

        ctrl_box_lay = QVBoxLayout(self.shared_ctrl_box)
        ctrl_box_lay.setContentsMargins(6, 2, 6, 6)
        ctrl_box_lay.addWidget(ctrl_content)
        self.shared_ctrl_box.toggled.connect(ctrl_content.setVisible)
        sim_main_layout.addWidget(self.shared_ctrl_box)

        # ======================================================================
        # 模块 A: 1D 仿真控制组件 (专属)
        # ======================================================================
        self.widget_sim_mode = QWidget()
        sim_vlay = QVBoxLayout(self.widget_sim_mode)
        sim_vlay.setContentsMargins(0, 0, 0, 0)
        sim_vlay.setSpacing(6)

        # 顶部操作栏（开始仿真、终止仿真、导入、导出、重置）
        top_btn_layout = QGridLayout()
        top_btn_layout.setContentsMargins(0, 0, 0, 0)
        top_btn_layout.setSpacing(6)

        self.sim_run_btn = QPushButton("开始仿真")
        self.sim_run_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
        self.sim_run_btn.clicked.connect(self.start_simulation)
        top_btn_layout.addWidget(self.sim_run_btn, 0, 0)

        self.sim_stop_btn = QPushButton("终止仿真")
        self.sim_stop_btn.setEnabled(False)
        self.sim_stop_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
        self.sim_stop_btn.clicked.connect(self.stop_simulation)
        top_btn_layout.addWidget(self.sim_stop_btn, 0, 1)

        self.sim_load_btn = QPushButton("导入参数")
        self.sim_load_btn.clicked.connect(self.load_simulation_params)
        top_btn_layout.addWidget(self.sim_load_btn, 1, 0)

        self.sim_save_params_btn = QPushButton("导出参数")
        self.sim_save_params_btn.clicked.connect(self.save_simulation_params)
        top_btn_layout.addWidget(self.sim_save_params_btn, 1, 1)

        self.sim_reset_btn = QPushButton("恢复默认参数")
        self.sim_reset_btn.clicked.connect(self.reset_simulation_params)
        top_btn_layout.addWidget(self.sim_reset_btn, 2, 0, 1, 2)
        sim_vlay.addLayout(top_btn_layout)

        # 仿真模式专属副反应动力学参数 TableWidget (支持 Ctrl+A 全选 / Ctrl+C 复制到Excel / Ctrl+V 粘贴)
        self.sim_rxn_box = QGroupBox("热失控反应动力学参数")
        self.sim_rxn_box.setCheckable(True)
        self.sim_rxn_box.setChecked(True)
        sim_rxn_content = QWidget()
        sim_rxn_vlay = QVBoxLayout(sim_rxn_content)
        sim_rxn_vlay.setContentsMargins(0, 4, 0, 0)
        sim_rxn_vlay.setSpacing(4)

        sim_rxn_btn_bar = QHBoxLayout()
        sim_rxn_btn_bar.setContentsMargins(0, 0, 0, 0)
        sim_rxn_btn_bar.setSpacing(6)

        self.add_sim_rxn_btn = QPushButton("添加反应")
        self.add_sim_rxn_btn.setStyleSheet("background-color: #2563eb; color: white;")
        self.add_sim_rxn_btn.clicked.connect(self.add_sim_reaction_row)
        sim_rxn_btn_bar.addWidget(self.add_sim_rxn_btn)

        self.del_sim_rxn_btn = QPushButton("删除反应")
        self.del_sim_rxn_btn.setStyleSheet("background-color: #dc2626; color: white;")
        self.del_sim_rxn_btn.clicked.connect(self.remove_sim_reaction_row)
        sim_rxn_btn_bar.addWidget(self.del_sim_rxn_btn)
        sim_rxn_vlay.addLayout(sim_rxn_btn_bar)

        self.sim_rxn_table = CopyPasteTableWidget(4, 7)
        self.sim_rxn_table.setHorizontalHeaderLabels(["副反应名称", "指前因子 A", "活化能 Ea", "反应热 H", "反应级数 n", "自催化 m", "初始浓度 c0"])
        self.sim_rxn_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.sim_rxn_table.verticalHeader().setDefaultSectionSize(26)
        self.sim_rxn_table.setFixedHeight(140)
        self._populate_sim_rxn_table(DEFAULT_SIM_PARAMS["reactions"])
        sim_rxn_vlay.addWidget(self.sim_rxn_table)

        sim_rxn_box_lay = QVBoxLayout(self.sim_rxn_box)
        sim_rxn_box_lay.setContentsMargins(6, 2, 6, 6)
        sim_rxn_box_lay.addWidget(sim_rxn_content)
        self.sim_rxn_box.toggled.connect(sim_rxn_content.setVisible)
        sim_vlay.addWidget(self.sim_rxn_box)

        # 仿真核心指标概览卡片
        self.summary_box = QGroupBox("核心指标概览")
        self.summary_box.setCheckable(True)
        self.summary_box.setChecked(True)
        summary_content = QWidget()
        summary_grid = QGridLayout(summary_content)
        summary_grid.setContentsMargins(6, 6, 6, 6)
        summary_grid.setHorizontalSpacing(8)
        summary_grid.setVerticalSpacing(5)

        val_style = "font-weight: bold; color: #1e293b; font-size: 11px;"
        lbl_tmax = QLabel("最高温度 T_max:")
        self.card_tmax_val = QLabel("-")
        self.card_tmax_val.setStyleSheet("font-weight: bold; color: #dc2626; font-size: 11px;")
        summary_grid.addWidget(lbl_tmax, 0, 0)
        summary_grid.addWidget(self.card_tmax_val, 0, 1)

        lbl_tr = QLabel("热失控触发时刻 t_TR:")
        self.card_t_tr_val = QLabel("-")
        self.card_t_tr_val.setStyleSheet("font-weight: bold; color: #dc2626; font-size: 11px;")
        summary_grid.addWidget(lbl_tr, 0, 2)
        summary_grid.addWidget(self.card_t_tr_val, 0, 3)

        lbl_tcold = QLabel("冷面最高温度:")
        self.card_tcold_val = QLabel("-")
        self.card_tcold_val.setStyleSheet(val_style)
        summary_grid.addWidget(lbl_tcold, 1, 0)
        summary_grid.addWidget(self.card_tcold_val, 1, 1)

        lbl_dtrate = QLabel("受热面最大温升速率:")
        self.card_dtrate_val = QLabel("-")
        self.card_dtrate_val.setStyleSheet("font-weight: bold; color: #ea580c; font-size: 11px;")
        summary_grid.addWidget(lbl_dtrate, 1, 2)
        summary_grid.addWidget(self.card_dtrate_val, 1, 3)

        lbl_tmean = QLabel("均温最高温度:")
        self.card_tmean_val = QLabel("-")
        self.card_tmean_val.setStyleSheet(val_style)
        summary_grid.addWidget(lbl_tmean, 2, 0)
        summary_grid.addWidget(self.card_tmean_val, 2, 1)

        lbl_heatend = QLabel("停热时刻受热面 T:")
        self.card_theatend_val = QLabel("-")
        self.card_theatend_val.setStyleSheet(val_style)
        summary_grid.addWidget(lbl_heatend, 2, 2)
        summary_grid.addWidget(self.card_theatend_val, 2, 3)

        lbl_perf = QLabel("计算统计 / 耗时:")
        self.card_perf_val = QLabel("-")
        self.card_perf_val.setStyleSheet("color: #475569; font-size: 11px;")
        summary_grid.addWidget(lbl_perf, 3, 0)
        summary_grid.addWidget(self.card_perf_val, 3, 1, 1, 3)

        summary_box_lay = QVBoxLayout(self.summary_box)
        summary_box_lay.setContentsMargins(6, 4, 6, 6)
        summary_box_lay.addWidget(summary_content)
        self.summary_box.toggled.connect(summary_content.setVisible)
        sim_vlay.addWidget(self.summary_box)

        # 仿真图表视图切换与数据导出 GroupBox
        post_box = QGroupBox("后处理绘图")
        post_grid = QGridLayout(post_box)
        post_grid.setContentsMargins(8, 8, 8, 8)
        post_grid.setHorizontalSpacing(6)
        post_grid.setVerticalSpacing(6)

        post_grid.addWidget(QLabel("当前显示视图:"), 0, 0)
        self.sim_view_combo = QComboBox()
        self.sim_view_combo.addItems([
            "综合曲线演化图 (全部子图)",
            "子图(a) 反应物平均浓度",
            "子图(b) 反应物平均反应速率",
            "子图(c) 反应物最大反应速率",
            "子图(d) 全域副反应放热与加热功率",
            "子图(e) 厚度方向不同测点温度演变",
            "子图(f) 关键时刻厚度剖面 T(z)",
            "温度时空云图",
            "反应物浓度时空云图 (多子图)",
            "反应速率时空云图 (多子图)",
            "产热功率时空云图 (多子图)",
        ])
        self.sim_view_combo.currentIndexChanged.connect(self.on_sim_view_changed)
        post_grid.addWidget(self.sim_view_combo, 0, 1, 1, 2)

        self.save_std_btn = QPushButton("保存仿真数据 (Excel)")
        self.save_std_btn.setEnabled(False)
        self.save_std_btn.clicked.connect(lambda: self.export_sim_excel(full_matrix=False))
        post_grid.addWidget(self.save_std_btn, 1, 0, 1, 3)

        self.save_full_btn = QPushButton("保存完整云图矩阵数据 (Excel)")
        self.save_full_btn.setEnabled(False)
        self.save_full_btn.setStyleSheet("background-color: #8b5cf6; color: white;")
        self.save_full_btn.clicked.connect(lambda: self.export_sim_excel(full_matrix=True))
        post_grid.addWidget(self.save_full_btn, 2, 0, 1, 3)
        sim_vlay.addWidget(post_box)

        sim_main_layout.addWidget(self.widget_sim_mode)

        # ======================================================================
        # 模块 B: 1D 热失控副反应多步 PSO 并行辨识面板
        # ======================================================================
        self.widget_tr_ident_mode = QWidget()
        tr_id_lay = QVBoxLayout(self.widget_tr_ident_mode)
        tr_id_lay.setContentsMargins(0, 0, 0, 0)
        tr_id_lay.setSpacing(6)

        # 多反应动力学方程模型展示卡片 (与 ARC 模型界面保持完全一致的专业呈现风格)
        tr_eq_box = QGroupBox("多反应动力学方程模型")
        tr_eq_vlay = QVBoxLayout(tr_eq_box)
        tr_eq_vlay.setContentsMargins(8, 6, 8, 6)
        tr_eq_vlay.setSpacing(4)
        tr_eq_lbl = QLabel("热失控反应速率方程：da_k/dt = -A_k · exp(-Ea_k / (R·T)) · a_k^n_k · (1 - a_k)^m_k ")
        tr_eq_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #1e3a8a; background-color: #f1f5f9; padding: 6px; border-radius: 4px;")
        tr_eq_vlay.addWidget(tr_eq_lbl)
        tr_eq_sub = QLabel("   a_k: 热失控反应速率方程无量纲反应物浓度，起始为1，结束为0")
        tr_eq_sub.setStyleSheet("color: #475569; font-size: 14px;")
        tr_eq_vlay.addWidget(tr_eq_sub)
        tr_id_lay.addWidget(tr_eq_box)

        tr_ctrl_btn_grid = QGridLayout()
        self.tr_id_run_btn = QPushButton("开始 1D TR 参数辨识")
        self.tr_id_run_btn.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold;")
        self.tr_id_run_btn.clicked.connect(self.start_tr_identify)
        tr_ctrl_btn_grid.addWidget(self.tr_id_run_btn, 0, 0)

        self.tr_id_stop_btn = QPushButton("终止辨识")
        self.tr_id_stop_btn.setEnabled(False)
        self.tr_id_stop_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
        self.tr_id_stop_btn.clicked.connect(self.stop_tr_identify)
        tr_ctrl_btn_grid.addWidget(self.tr_id_stop_btn, 0, 1)

        self.tr_id_apply_btn = QPushButton("导入仿真计算")
        self.tr_id_apply_btn.setEnabled(False)
        self.tr_id_apply_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
        self.tr_id_apply_btn.clicked.connect(self.apply_tr_identify_to_rxn_table)
        tr_ctrl_btn_grid.addWidget(self.tr_id_apply_btn, 1, 0)

        self.tr_id_save_btn = QPushButton("保存辨识参数")
        self.tr_id_save_btn.setEnabled(False)
        self.tr_id_save_btn.clicked.connect(self.save_tr_identify_params_txt)
        tr_ctrl_btn_grid.addWidget(self.tr_id_save_btn, 1, 1)
        tr_id_lay.addLayout(tr_ctrl_btn_grid)

        # 实验数据加载与测点对齐设置
        tr_data_box = QGroupBox("实验对标数据与对齐测点")
        tr_data_grid = QGridLayout(tr_data_box)
        tr_data_grid.setContentsMargins(6, 6, 6, 6)
        tr_data_grid.setHorizontalSpacing(6)
        tr_data_grid.setVerticalSpacing(4)

        tr_data_grid.addWidget(QLabel("实验数据:"), 0, 0)
        self.tr_exp_file_edit = QLineEdit()
        self.tr_exp_file_edit.setPlaceholderText("选择 Test Data.xlsx 或 .csv")
        self.tr_exp_file_edit.returnPressed.connect(lambda: self.load_ident_sheets("tr", self.tr_exp_file_edit.text().strip()))
        tr_data_grid.addWidget(self.tr_exp_file_edit, 0, 1)

        self.tr_sheet_combo = QComboBox()
        self.tr_sheet_combo.currentIndexChanged.connect(lambda: self.load_ident_columns("tr"))
        tr_data_grid.addWidget(self.tr_sheet_combo, 0, 2)

        self.tr_exp_browse_btn = QPushButton("导入")
        self.tr_exp_browse_btn.setStyleSheet("background-color: #2563eb; color: white;")
        self.tr_exp_browse_btn.clicked.connect(lambda: self.browse_ident_file("tr"))
        tr_data_grid.addWidget(self.tr_exp_browse_btn, 0, 3)

        tr_data_grid.addWidget(QLabel("对齐测点:"), 1, 0)
        self.tr_pos_combo = QComboBox()
        self.tr_pos_combo.addItems([
            "全域均温 (Tmean)",
            "加热面表面 (z = 0)",
            "1/4 厚度处 (z = L/4)",
            "中心位置 (z = L/2)",
            "3/4 厚度处 (z = 3L/4)",
            "背火面表面 (z = L)"
        ])
        tr_data_grid.addWidget(self.tr_pos_combo, 1, 1)

        tr_data_grid.addWidget(QLabel("环境温度来源:"), 1, 2)
        self.tr_amb_combo = QComboBox()
        self.tr_amb_combo.addItems(["Constant"])
        self.tr_amb_combo.currentIndexChanged.connect(self.on_tr_amb_source_changed)
        tr_data_grid.addWidget(self.tr_amb_combo, 1, 3)

        tr_data_grid.addWidget(QLabel("时间列 (t):"), 2, 0)
        self.tr_time_col_combo = QComboBox()
        tr_data_grid.addWidget(self.tr_time_col_combo, 2, 1)

        tr_data_grid.addWidget(QLabel("温度列 (T):"), 2, 2)
        self.tr_temp_col_combo = QComboBox()
        tr_data_grid.addWidget(self.tr_temp_col_combo, 2, 3)

        tr_data_grid.addWidget(QLabel("环境温度 (°C):"), 3, 0)
        self.tr_amb_val_edit = QLineEdit("25.0")
        tr_data_grid.addWidget(self.tr_amb_val_edit, 3, 1, 1, 3)
        tr_id_lay.addWidget(tr_data_box)

        # 1D 辨识专属动力学参数与搜索上下限表格 (17列，包含 A 与 m 的独立搜索上下限)
        self.ident_rxn_box = QGroupBox("1D 动力学参数辨识设置与上下限 [A, Ea, H, n, m]")
        self.ident_rxn_box.setCheckable(True)
        self.ident_rxn_box.setChecked(True)
        ident_rxn_content = QWidget()
        ident_rxn_vlay = QVBoxLayout(ident_rxn_content)
        ident_rxn_vlay.setContentsMargins(0, 4, 0, 0)
        ident_rxn_vlay.setSpacing(4)

        ident_btn_bar = QHBoxLayout()
        ident_btn_bar.setContentsMargins(0, 0, 0, 0)
        ident_btn_bar.setSpacing(6)

        self.add_ident_rxn_btn = QPushButton("添加反应")
        self.add_ident_rxn_btn.setStyleSheet("background-color: #2563eb; color: white;")
        self.add_ident_rxn_btn.clicked.connect(self.add_ident_reaction_row)
        ident_btn_bar.addWidget(self.add_ident_rxn_btn)

        self.del_ident_rxn_btn = QPushButton("删除反应")
        self.del_ident_rxn_btn.setStyleSheet("background-color: #dc2626; color: white;")
        self.del_ident_rxn_btn.clicked.connect(self.remove_ident_reaction_row)
        ident_btn_bar.addWidget(self.del_ident_rxn_btn)

        self.reset_ident_rxn_btn = QPushButton("默认上下限")
        self.reset_ident_rxn_btn.clicked.connect(self.reset_ident_reaction_defaults)
        ident_btn_bar.addWidget(self.reset_ident_rxn_btn)
        ident_rxn_vlay.addLayout(ident_btn_bar)

        self.ident_rxn_table = CopyPasteTableWidget(4, 17)
        ident_headers = [
            "副反应", "初值 A", "初值 Ea", "初值 H", "初值 n", "初值 m", "初值 c0",
            "A下限", "A上限", "Ea下限", "Ea上限", "H下限", "H上限", "n下限", "n上限", "m下限", "m上限"
        ]
        self.ident_rxn_table.setHorizontalHeaderLabels(ident_headers)
        self.ident_rxn_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.ident_rxn_table.verticalHeader().setDefaultSectionSize(26)
        self.ident_rxn_table.setFixedHeight(150)
        self._populate_ident_rxn_table(DEFAULT_IDENT_REACTIONS)
        ident_rxn_vlay.addWidget(self.ident_rxn_table)

        ident_tip_lbl = QLabel("    反应初值 m 若设为 0.0，则表示不计算/不辨识该反应自催化项。")
        ident_tip_lbl.setStyleSheet("color: #475569; font-size: 11px;")
        ident_rxn_vlay.addWidget(ident_tip_lbl)

        ident_rxn_box_lay = QVBoxLayout(self.ident_rxn_box)
        ident_rxn_box_lay.setContentsMargins(6, 2, 6, 6)
        ident_rxn_box_lay.addWidget(ident_rxn_content)
        self.ident_rxn_box.toggled.connect(ident_rxn_content.setVisible)
        tr_id_lay.addWidget(self.ident_rxn_box)

        sim_main_layout.addWidget(self.widget_tr_ident_mode)

        # ======================================================================
        # 模块 C: ARC 绝热热失控动力学参数辨识面板
        # ======================================================================
        self.widget_arc_ident_mode = QWidget()
        arc_id_lay = QVBoxLayout(self.widget_arc_ident_mode)
        arc_id_lay.setContentsMargins(0, 0, 0, 0)
        arc_id_lay.setSpacing(6)

        # ARC 动力学方程模型展示卡片 (统一为 a 从 1 -> 0, a^n * (1-a)^m)
        arc_eq_box = QGroupBox("ARC 动力学方程模型")
        arc_eq_vlay = QVBoxLayout(arc_eq_box)
        arc_eq_vlay.setContentsMargins(8, 6, 8, 6)
        arc_eq_vlay.setSpacing(4)
        arc_eq_lbl = QLabel("热失控反应速率方程：da/dt = -A · exp(-Ea / (R·T)) · a^n · (1 - a)^m ")
        arc_eq_lbl.setStyleSheet("font-size: 13px; font-weight: bold; color: #1e3a8a; background-color: #f1f5f9; padding: 6px; border-radius: 4px;")
        arc_eq_vlay.addWidget(arc_eq_lbl)
        arc_eq_sub = QLabel("   a: 热失控反应速率方程无量纲反应物浓度，起始为1，结束为0")
        arc_eq_sub.setStyleSheet("color: #475569; font-size: 14px;")
        arc_eq_vlay.addWidget(arc_eq_sub)
        arc_id_lay.addWidget(arc_eq_box)

        arc_btn_grid = QGridLayout()
        self.arc_run_btn = QPushButton("开始 ARC 参数辨识")
        self.arc_run_btn.setStyleSheet("background-color: #0284c7; color: white; font-weight: bold;")
        self.arc_run_btn.clicked.connect(self.start_arc_identify)
        arc_btn_grid.addWidget(self.arc_run_btn, 0, 0)

        self.arc_stop_btn = QPushButton("终止辨识")
        self.arc_stop_btn.setEnabled(False)
        self.arc_stop_btn.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold;")
        self.arc_stop_btn.clicked.connect(self.stop_arc_identify)
        arc_btn_grid.addWidget(self.arc_stop_btn, 0, 1)

        self.arc_import_btn = QPushButton("导入仿真计算")
        self.arc_import_btn.setEnabled(False)
        self.arc_import_btn.setStyleSheet("background-color: #10b981; color: white; font-weight: bold;")
        self.arc_import_btn.clicked.connect(self.import_arc_to_sim_table)
        arc_btn_grid.addWidget(self.arc_import_btn, 1, 0)

        self.arc_save_btn = QPushButton("保存辨识参数")
        self.arc_save_btn.setEnabled(False)
        self.arc_save_btn.clicked.connect(self.save_arc_params_txt)
        arc_btn_grid.addWidget(self.arc_save_btn, 1, 1)
        arc_id_lay.addLayout(arc_btn_grid)

        # ARC 数据文件与测点选择
        arc_data_box = QGroupBox("ARC 数据文件配置")
        arc_data_grid = QGridLayout(arc_data_box)
        arc_data_grid.setContentsMargins(6, 6, 6, 6)
        arc_data_grid.setHorizontalSpacing(6)
        arc_data_grid.setVerticalSpacing(4)

        arc_data_grid.addWidget(QLabel("ARC 数据:"), 0, 0)
        self.arc_file_edit = QLineEdit()
        self.arc_file_edit.setPlaceholderText("选择 ARC DATA.xlsx 或 .csv")
        self.arc_file_edit.returnPressed.connect(lambda: self.load_ident_sheets("arc", self.arc_file_edit.text().strip()))
        arc_data_grid.addWidget(self.arc_file_edit, 0, 1)

        self.arc_sheet_combo = QComboBox()
        self.arc_sheet_combo.currentIndexChanged.connect(lambda: self.load_ident_columns("arc"))
        arc_data_grid.addWidget(self.arc_sheet_combo, 0, 2)

        self.arc_browse_btn = QPushButton("导入")
        self.arc_browse_btn.setStyleSheet("background-color: #0284c7; color: white;")
        self.arc_browse_btn.clicked.connect(lambda: self.browse_ident_file("arc"))
        arc_data_grid.addWidget(self.arc_browse_btn, 0, 3)

        arc_data_grid.addWidget(QLabel("时间列:"), 1, 0)
        self.arc_time_combo = QComboBox()
        arc_data_grid.addWidget(self.arc_time_combo, 1, 1)

        arc_data_grid.addWidget(QLabel("温度列:"), 1, 2)
        self.arc_temp_combo = QComboBox()
        self.arc_temp_combo.currentIndexChanged.connect(self.auto_fill_arc_temps)
        arc_data_grid.addWidget(self.arc_temp_combo, 1, 3)

        arc_data_grid.addWidget(QLabel("自产热起始 T1 (°C):"), 2, 0)
        self.arc_t1_edit = QLineEdit("85.0")
        arc_data_grid.addWidget(self.arc_t1_edit, 2, 1)

        arc_data_grid.addWidget(QLabel("热失控最高 T3 (°C):"), 2, 2)
        self.arc_t3_edit = QLineEdit("650.0")
        arc_data_grid.addWidget(self.arc_t3_edit, 2, 3)

        arc_data_grid.addWidget(QLabel("电芯密度 (kg/m³):"), 3, 0)
        self.arc_rho_edit = QLineEdit("2400.0")
        arc_data_grid.addWidget(self.arc_rho_edit, 3, 1)

        arc_data_grid.addWidget(QLabel("比热容 (J/kg·K):"), 3, 2)
        self.arc_cp_edit = QLineEdit("1071.0")
        arc_data_grid.addWidget(self.arc_cp_edit, 3, 3)

        arc_data_grid.addWidget(QLabel("延长因子:"), 4, 0)
        self.arc_ext_factor_edit = QLineEdit("1.35")
        self.arc_ext_factor_edit.setToolTip("数据末端时间延长倍数，默认 1.35；在 t > t3 延长段保持 T3，强化对最高温贴合")
        arc_data_grid.addWidget(self.arc_ext_factor_edit, 4, 1)
        arc_id_lay.addWidget(arc_data_box)

        # 优化算法与动力学参数搜索范围设置
        arc_opt_box = QGroupBox("ARC 辨识优化算法配置与参数范围")
        arc_opt_grid = QGridLayout(arc_opt_box)
        arc_opt_grid.setContentsMargins(6, 6, 6, 6)
        arc_opt_grid.setHorizontalSpacing(6)
        arc_opt_grid.setVerticalSpacing(4)

        arc_opt_grid.addWidget(QLabel("优化算法策略:"), 0, 0)
        self.arc_algo_combo = QComboBox()
        self.arc_algo_combo.addItems([
            "PSO 粒子群 + Nelder-Mead (无初值+初始值)",
            "仅 Nelder-Mead 单纯形法 (基于初值)",
            "仅 PSO 粒子群算法 (全局搜索)"
        ])
        arc_opt_grid.addWidget(self.arc_algo_combo, 0, 1, 1, 3)

        # PSO 规模/代数移动到优化算法下方，并分开为两个独立输入框
        arc_opt_grid.addWidget(QLabel("PSO 种群:"), 1, 0)
        self.arc_pso_particles_edit = QLineEdit("30")
        arc_opt_grid.addWidget(self.arc_pso_particles_edit, 1, 1)

        arc_opt_grid.addWidget(QLabel("PSO 迭代:"), 1, 2)
        self.arc_pso_iters_edit = QLineEdit("25")
        arc_opt_grid.addWidget(self.arc_pso_iters_edit, 1, 3)

        # 初值 ln(A0) 与 ln(E0)
        arc_opt_grid.addWidget(QLabel("初值 ln(A0):"), 2, 0)
        self.arc_lna_edit = QLineEdit("22.757")
        arc_opt_grid.addWidget(self.arc_lna_edit, 2, 1)

        arc_opt_grid.addWidget(QLabel("初值 ln(E0):"), 2, 2)
        self.arc_lne_edit = QLineEdit("11.671")
        arc_opt_grid.addWidget(self.arc_lne_edit, 2, 3)

        # 初值 n0 与 初值 m0 (m初值移到n0右侧)
        arc_opt_grid.addWidget(QLabel("初值 n0:"), 3, 0)
        self.arc_n_edit = QLineEdit("3.278")
        arc_opt_grid.addWidget(self.arc_n_edit, 3, 1)

        arc_opt_grid.addWidget(QLabel("初值 m0:"), 3, 2)
        self.arc_init_m_edit = QLineEdit("0.000")
        self.arc_init_m_edit.setToolTip("自催化初值若设为 0 则不辨识 m")
        arc_opt_grid.addWidget(self.arc_init_m_edit, 3, 3)

        # ln(A) 范围 与 ln(E) 范围
        arc_opt_grid.addWidget(QLabel("ln(A) 范围 [lb, ub]:"), 4, 0)
        arc_lna_lay = QHBoxLayout()
        self.arc_lb_lna_edit = QLineEdit("5.0")
        self.arc_ub_lna_edit = QLineEdit("65.0")
        arc_lna_lay.addWidget(self.arc_lb_lna_edit)
        arc_lna_lay.addWidget(QLabel("~"))
        arc_lna_lay.addWidget(self.arc_ub_lna_edit)
        arc_opt_grid.addLayout(arc_lna_lay, 4, 1)

        arc_opt_grid.addWidget(QLabel("ln(E) 范围 [lb, ub]:"), 4, 2)
        arc_lne_lay = QHBoxLayout()
        self.arc_lb_lne_edit = QLineEdit("8.0")
        self.arc_ub_lne_edit = QLineEdit("16.0")
        arc_lne_lay.addWidget(self.arc_lb_lne_edit)
        arc_lne_lay.addWidget(QLabel("~"))
        arc_lne_lay.addWidget(self.arc_ub_lne_edit)
        arc_opt_grid.addLayout(arc_lne_lay, 4, 3)

        # 反应级数 n 范围 与 反应级数 m 范围 (m范围移动到n范围右侧)
        arc_opt_grid.addWidget(QLabel("反应级数 n 范围:"), 5, 0)
        arc_n_lay = QHBoxLayout()
        self.arc_lb_n_edit = QLineEdit("0.2")
        self.arc_ub_n_edit = QLineEdit("4.5")
        arc_n_lay.addWidget(self.arc_lb_n_edit)
        arc_n_lay.addWidget(QLabel("~"))
        arc_n_lay.addWidget(self.arc_ub_n_edit)
        arc_opt_grid.addLayout(arc_n_lay, 5, 1)

        arc_opt_grid.addWidget(QLabel("反应级数 m 范围:"), 5, 2)
        arc_m_lay = QHBoxLayout()
        self.arc_lb_m_edit = QLineEdit("0.0")
        self.arc_ub_m_edit = QLineEdit("2.0")
        arc_m_lay.addWidget(self.arc_lb_m_edit)
        arc_m_lay.addWidget(QLabel("~"))
        arc_m_lay.addWidget(self.arc_ub_m_edit)
        arc_opt_grid.addLayout(arc_m_lay, 5, 3)

        arc_id_lay.addWidget(arc_opt_box)
        sim_main_layout.addWidget(self.widget_arc_ident_mode)

        # 初始视图状态切换
        self.widget_tr_ident_mode.setVisible(False)
        self.widget_arc_ident_mode.setVisible(False)
        self.sim_frame.setVisible(False)
        control_layout.addWidget(self.sim_frame)

        # 内部状态记录
        self.sim_results = None
        self.sim_worker = None
        self.tr_id_worker = None
        self.arc_id_worker = None
        self.last_tr_id_result = None
        self.last_arc_id_result = None
        self.current_ident_df_tr = None
        self.current_ident_df_arc = None

    # --------------------------------------------------------------------------
    # 模式切换响应
    # --------------------------------------------------------------------------
    def on_sim_mode_changed(self):
        idx = self.sim_mode_combo.currentIndex()
        if idx == 0:
            # 1D 热失控仿真计算
            self.shared_geom_box.setVisible(True)
            self.shared_ctrl_box.setVisible(True)
            self.widget_sim_mode.setVisible(True)
            self.widget_tr_ident_mode.setVisible(False)
            self.widget_arc_ident_mode.setVisible(False)
        elif idx == 1:
            # 1D TR 动力学参数辨识
            self.shared_geom_box.setVisible(True)
            self.shared_ctrl_box.setVisible(True)
            self.widget_sim_mode.setVisible(False)
            self.widget_tr_ident_mode.setVisible(True)
            self.widget_arc_ident_mode.setVisible(False)
        elif idx == 2:
            # ARC 热失控动力学参数辨识
            self.shared_geom_box.setVisible(False)
            self.shared_ctrl_box.setVisible(False)
            self.widget_sim_mode.setVisible(False)
            self.widget_tr_ident_mode.setVisible(False)
            self.widget_arc_ident_mode.setVisible(True)

    # --------------------------------------------------------------------------
    # 仿真表格增删与填充辅助
    # --------------------------------------------------------------------------
    def _adjust_sim_rxn_table_height(self):
        row_cnt = self.sim_rxn_table.rowCount()
        new_h = min(260, max(100, 32 + row_cnt * 26))
        self.sim_rxn_table.setFixedHeight(new_h)

    def add_sim_reaction_row(self):
        row = self.sim_rxn_table.rowCount()
        self.sim_rxn_table.insertRow(row)
        new_name = f"Reaction_{row + 1}"
        default_vals = [new_name, "1.0000e+08", "100000.00", "100.00", "1.000", "0.000", "1.000"]
        for col, val in enumerate(default_vals):
            self.sim_rxn_table.setItem(row, col, QTableWidgetItem(val))
        self._adjust_sim_rxn_table_height()
        self.update_status(f"已为仿真模型添加副反应: {new_name}（当前共 {row + 1} 个反应）")

    def remove_sim_reaction_row(self):
        count = self.sim_rxn_table.rowCount()
        if count <= 1:
            QMessageBox.warning(self, "提示", "仿真模型至少需要保留 1 个副反应。")
            return
        curr_row = self.sim_rxn_table.currentRow()
        if curr_row < 0 or curr_row >= count:
            curr_row = count - 1
        name = self.sim_rxn_table.item(curr_row, 0).text() if self.sim_rxn_table.item(curr_row, 0) else f"行{curr_row+1}"
        self.sim_rxn_table.removeRow(curr_row)
        self._adjust_sim_rxn_table_height()
        self.update_status(f"已删除仿真副反应: {name}（剩余 {count - 1} 个反应）")

    def _populate_sim_rxn_table(self, rxns_list):
        self.sim_rxn_table.setRowCount(len(rxns_list))
        for row, r in enumerate(rxns_list):
            self.sim_rxn_table.setItem(row, 0, QTableWidgetItem(str(r.get("name", f"Rxn_{row+1}"))))
            self.sim_rxn_table.setItem(row, 1, QTableWidgetItem(f"{float(r.get('A', 1e8)):.6e}"))
            self.sim_rxn_table.setItem(row, 2, QTableWidgetItem(f"{float(r.get('Ea', 1e5)):.2f}"))
            self.sim_rxn_table.setItem(row, 3, QTableWidgetItem(f"{float(r.get('H', 100.0)):.2f}"))
            self.sim_rxn_table.setItem(row, 4, QTableWidgetItem(f"{float(r.get('n', 1.0)):.3f}"))
            self.sim_rxn_table.setItem(row, 5, QTableWidgetItem(f"{float(r.get('m', 0.0)):.3f}"))
            self.sim_rxn_table.setItem(row, 6, QTableWidgetItem(f"{float(r.get('c0', 1.0)):.3f}"))
        self._adjust_sim_rxn_table_height()

    # --------------------------------------------------------------------------
    # 1D 辨识表格增删与填充辅助
    # --------------------------------------------------------------------------
    def _adjust_ident_rxn_table_height(self):
        row_cnt = self.ident_rxn_table.rowCount()
        new_h = min(280, max(110, 34 + row_cnt * 26))
        self.ident_rxn_table.setFixedHeight(new_h)

    def add_ident_reaction_row(self):
        row = self.ident_rxn_table.rowCount()
        self.ident_rxn_table.insertRow(row)
        new_name = f"Reaction_{row + 1}"
        default_vals = [
            new_name, "1.0000e+08", "100000.0", "100.0", "1.000", "0.000", "1.000",
            "1.0000e+05", "1.0000e+15", "6.0e4", "1.5e5", "10.0", "300.0", "0.5", "3.0", "0.00", "2.00"
        ]
        for col, val in enumerate(default_vals):
            self.ident_rxn_table.setItem(row, col, QTableWidgetItem(val))
        self._adjust_ident_rxn_table_height()
        self.update_status(f"已添加 1D 辨识副反应: {new_name}（当前共 {row + 1} 个待辨识反应）")

    def remove_ident_reaction_row(self):
        count = self.ident_rxn_table.rowCount()
        if count <= 1:
            QMessageBox.warning(self, "提示", "1D 辨识模型至少需要保留 1 个反应。")
            return
        curr_row = self.ident_rxn_table.currentRow()
        if curr_row < 0 or curr_row >= count:
            curr_row = count - 1
        name = self.ident_rxn_table.item(curr_row, 0).text() if self.ident_rxn_table.item(curr_row, 0) else f"行{curr_row+1}"
        self.ident_rxn_table.removeRow(curr_row)
        self._adjust_ident_rxn_table_height()
        self.update_status(f"已删除辨识反应: {name}（剩余 {count - 1} 个反应）")

    def _populate_ident_rxn_table(self, rxns_list):
        self.ident_rxn_table.setRowCount(len(rxns_list))
        for row, r in enumerate(rxns_list):
            self.ident_rxn_table.setItem(row, 0, QTableWidgetItem(str(r.get("name", f"Rxn_{row+1}"))))
            self.ident_rxn_table.setItem(row, 1, QTableWidgetItem(f"{float(r.get('A', 1e8)):.6e}"))
            self.ident_rxn_table.setItem(row, 2, QTableWidgetItem(f"{float(r.get('Ea', 1e5)):.2f}"))
            self.ident_rxn_table.setItem(row, 3, QTableWidgetItem(f"{float(r.get('H', 100.0)):.2f}"))
            self.ident_rxn_table.setItem(row, 4, QTableWidgetItem(f"{float(r.get('n', 1.0)):.3f}"))
            self.ident_rxn_table.setItem(row, 5, QTableWidgetItem(f"{float(r.get('m', 0.0)):.3f}"))
            self.ident_rxn_table.setItem(row, 6, QTableWidgetItem(f"{float(r.get('c0', 1.0)):.3f}"))

            # 7: A下限, 8: A上限
            lb_A = r.get("lb_A", 10.0 ** float(r.get("lb_logA", 5.0)))
            ub_A = r.get("ub_A", 10.0 ** float(r.get("ub_logA", 25.0)))
            self.ident_rxn_table.setItem(row, 7, QTableWidgetItem(f"{float(lb_A):.4e}"))
            self.ident_rxn_table.setItem(row, 8, QTableWidgetItem(f"{float(ub_A):.4e}"))

            self.ident_rxn_table.setItem(row, 9, QTableWidgetItem(f"{float(r.get('lb_Ea', 5e4)):.1e}"))
            self.ident_rxn_table.setItem(row, 10, QTableWidgetItem(f"{float(r.get('ub_Ea', 3e5)):.1e}"))
            self.ident_rxn_table.setItem(row, 11, QTableWidgetItem(f"{float(r.get('lb_H', 10.0)):.1f}"))
            self.ident_rxn_table.setItem(row, 12, QTableWidgetItem(f"{float(r.get('ub_H', 500.0)):.1f}"))
            self.ident_rxn_table.setItem(row, 13, QTableWidgetItem(f"{float(r.get('lb_n', 0.5)):.2f}"))
            self.ident_rxn_table.setItem(row, 14, QTableWidgetItem(f"{float(r.get('ub_n', 3.0)):.2f}"))
            self.ident_rxn_table.setItem(row, 15, QTableWidgetItem(f"{float(r.get('lb_m', 0.0)):.2f}"))
            self.ident_rxn_table.setItem(row, 16, QTableWidgetItem(f"{float(r.get('ub_m', 2.0)):.2f}"))
        self._adjust_ident_rxn_table_height()

    def reset_ident_reaction_defaults(self):
        self._populate_ident_rxn_table(DEFAULT_IDENT_REACTIONS)
        self.update_status("已恢复 1D TR 辨识默认 4 步副反应及其物理搜索上下限。")

    def get_current_sim_params(self):
        ctrl = {}
        for k, edit in self.sim_ctrl_inputs.items():
            ctrl[k] = float(edit.text().strip())

        geom = {}
        for k, edit in self.sim_geom_inputs.items():
            geom[k] = float(edit.text().strip())

        rxns = []
        for row in range(self.sim_rxn_table.rowCount()):
            name = self.sim_rxn_table.item(row, 0).text().strip()
            A = float(self.sim_rxn_table.item(row, 1).text().strip())
            Ea = float(self.sim_rxn_table.item(row, 2).text().strip())
            H = float(self.sim_rxn_table.item(row, 3).text().strip())
            n = float(self.sim_rxn_table.item(row, 4).text().strip())
            m = float(self.sim_rxn_table.item(row, 5).text().strip())
            c0 = float(self.sim_rxn_table.item(row, 6).text().strip())
            rxns.append({"name": name, "A": A, "Ea": Ea, "H": H, "n": n, "m": m, "c0": c0})

        return {"control": ctrl, "geometry": geom, "reactions": rxns}

    def get_current_ident_reactions(self):
        rxns = []
        for row in range(self.ident_rxn_table.rowCount()):
            name = self.ident_rxn_table.item(row, 0).text().strip()
            A = float(self.ident_rxn_table.item(row, 1).text().strip())
            Ea = float(self.ident_rxn_table.item(row, 2).text().strip())
            H = float(self.ident_rxn_table.item(row, 3).text().strip())
            n = float(self.ident_rxn_table.item(row, 4).text().strip())
            m = float(self.ident_rxn_table.item(row, 5).text().strip())
            c0 = float(self.ident_rxn_table.item(row, 6).text().strip())
            lb_A = float(self.ident_rxn_table.item(row, 7).text().strip())
            ub_A = float(self.ident_rxn_table.item(row, 8).text().strip())
            lb_Ea = float(self.ident_rxn_table.item(row, 9).text().strip())
            ub_Ea = float(self.ident_rxn_table.item(row, 10).text().strip())
            lb_H = float(self.ident_rxn_table.item(row, 11).text().strip())
            ub_H = float(self.ident_rxn_table.item(row, 12).text().strip())
            lb_n = float(self.ident_rxn_table.item(row, 13).text().strip())
            ub_n = float(self.ident_rxn_table.item(row, 14).text().strip())
            lb_m = float(self.ident_rxn_table.item(row, 15).text().strip()) if self.ident_rxn_table.columnCount() > 15 and self.ident_rxn_table.item(row, 15) else 0.0
            ub_m = float(self.ident_rxn_table.item(row, 16).text().strip()) if self.ident_rxn_table.columnCount() > 16 and self.ident_rxn_table.item(row, 16) else 2.0

            lb_logA = np.log10(max(lb_A, 1e-30))
            ub_logA = np.log10(max(ub_A, 1e-30))

            rxns.append({
                "name": name, "A": A, "Ea": Ea, "H": H, "n": n, "m": m, "c0": c0,
                "lb_A": lb_A, "ub_A": ub_A,
                "lb_logA": lb_logA, "ub_logA": ub_logA,
                "lb_Ea": lb_Ea, "ub_Ea": ub_Ea,
                "lb_H": lb_H, "ub_H": ub_H,
                "lb_n": lb_n, "ub_n": ub_n,
                "lb_m": lb_m, "ub_m": ub_m
            })
        return rxns

    # --------------------------------------------------------------------------
    # 结果卡片展示
    # --------------------------------------------------------------------------
    def reset_summary_card(self, computing=False):
        txt = "计算中..." if computing else "-"
        color = "#2563eb" if computing else "#475569"
        style = f"font-weight: bold; color: {color}; font-size: 11px;"
        for val_widget in [self.card_tmax_val, self.card_t_tr_val, self.card_tcold_val,
                           self.card_dtrate_val, self.card_tmean_val, self.card_theatend_val, self.card_perf_val]:
            val_widget.setText(txt)
            val_widget.setStyleSheet(style)

    def update_summary_card(self, results):
        t_arr = results["t_arr"]
        T_history = results["T_history"]
        th_end = float(results["th_end"])
        elapsed = results.get("elapsed", 0.0)
        total_substeps = results.get("total_substeps", 0)

        T_hot = T_history[:, 0]
        T_cold = T_history[:, -1]
        T_mean = np.mean(T_history, axis=1)

        max_idx = np.unravel_index(np.argmax(T_history), T_history.shape)
        T_max_val = T_history[max_idx]
        t_max_time = t_arr[max_idx[0]]
        layer_max = max_idx[1]
        layer_desc = "受热面" if layer_max == 0 else ("冷面" if layer_max == T_history.shape[1] - 1 else f"第{layer_max+1}层")

        cold_pk_idx = np.argmax(T_cold)
        T_cold_max = T_cold[cold_pk_idx]
        t_cold_max = t_arr[cold_pk_idx]
        T_mean_max = np.max(T_mean)

        idx_th = np.argmin(np.abs(t_arr - th_end))
        T_at_heat_end = T_hot[idx_th]

        dT_hot_dt = np.gradient(T_hot, t_arr)
        max_dT_hot_rate = float(np.max(dT_hot_dt))
        t_max_rate = float(t_arr[np.argmax(dT_hot_dt)])

        tr_indices = np.where((dT_hot_dt >= 1.0) & (T_hot >= 60.0))[0]
        if len(tr_indices) > 0:
            tr_idx = tr_indices[0]
            t_tr = t_arr[tr_idx]
            tr_text = f"{t_tr:.1f} s (已触发)"
            tr_style = "font-weight: bold; color: #dc2626; font-size: 11px;"
        else:
            tr_text = "未触发 (温升正常)"
            tr_style = "font-weight: bold; color: #16a34a; font-size: 11px;"

        self.card_tmax_val.setText(f"{T_max_val:.1f} °C ({layer_desc}, t={t_max_time:.1f}s)")
        self.card_tmax_val.setStyleSheet("font-weight: bold; color: #dc2626; font-size: 11px;")
        self.card_t_tr_val.setText(tr_text)
        self.card_t_tr_val.setStyleSheet(tr_style)
        self.card_tcold_val.setText(f"{T_cold_max:.1f} °C (t={t_cold_max:.1f}s)")
        self.card_tcold_val.setStyleSheet("font-weight: bold; color: #1e293b; font-size: 11px;")
        self.card_dtrate_val.setText(f"{max_dT_hot_rate:.2f} °C/s (t={t_max_rate:.1f}s)")
        self.card_dtrate_val.setStyleSheet("font-weight: bold; color: #ea580c; font-size: 11px;")
        self.card_tmean_val.setText(f"{T_mean_max:.1f} °C")
        self.card_tmean_val.setStyleSheet("font-weight: bold; color: #1e293b; font-size: 11px;")
        self.card_theatend_val.setText(f"{T_at_heat_end:.1f} °C (t={th_end:g}s)")
        self.card_theatend_val.setStyleSheet("font-weight: bold; color: #1e293b; font-size: 11px;")

        step_info = f"宏步: {len(t_arr)-1}"
        if total_substeps > 0:
            step_info += f" | 自适应子步: {total_substeps}"
        step_info += f" | 耗时: {elapsed:.2f}s"
        self.card_perf_val.setText(step_info)
        self.card_perf_val.setStyleSheet("color: #475569; font-size: 11px;")

    def reset_simulation_params(self):
        for k, v in DEFAULT_SIM_PARAMS["control"].items():
            if k in self.sim_ctrl_inputs:
                self.sim_ctrl_inputs[k].setText(str(v))
        for k, v in DEFAULT_SIM_PARAMS["geometry"].items():
            if k in self.sim_geom_inputs:
                self.sim_geom_inputs[k].setText(str(v))
        self._populate_sim_rxn_table(DEFAULT_SIM_PARAMS["reactions"])
        self.reset_summary_card(computing=False)
        self.update_status("已恢复 1D TR 仿真默认参数。")

    def save_simulation_params(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "导出仿真参数", "TR_Simulation_Params.json", "JSON Files (*.json);;Text Files (*.txt)")
        if not file_path:
            return
        try:
            params = self.get_current_sim_params()
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(params, f, indent=4, ensure_ascii=False)
            self.update_status(f"仿真参数成功导出至: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出参数出错: {str(e)}")

    def load_simulation_params(self):
        file_path, _ = QFileDialog.getOpenFileName(self, "导入仿真参数", "", "JSON Files (*.json);;Text Files (*.txt)")
        if not file_path:
            return
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                params = json.load(f)
            if "control" in params:
                for k, v in params["control"].items():
                    if k in self.sim_ctrl_inputs:
                        self.sim_ctrl_inputs[k].setText(str(v))
            if "geometry" in params:
                for k, v in params["geometry"].items():
                    if k in self.sim_geom_inputs:
                        self.sim_geom_inputs[k].setText(str(v))
            if "reactions" in params:
                self._populate_sim_rxn_table(params["reactions"])
            self.reset_summary_card(computing=False)
            self.update_status(f"成功导入仿真参数文件: {file_path}")
        except Exception as e:
            QMessageBox.critical(self, "导入失败", f"读取参数文件失败: {str(e)}")

    # --------------------------------------------------------------------------
    # 模式 A: 1D 仿真控制
    # --------------------------------------------------------------------------
    def start_simulation(self):
        try:
            params = self.get_current_sim_params()
        except Exception as e:
            QMessageBox.warning(self, "参数错误", f"输入的仿真参数格式不合法，请检查: {str(e)}")
            return

        self.sim_run_btn.setEnabled(False)
        self.sim_stop_btn.setEnabled(True)
        self.save_std_btn.setEnabled(False)
        self.save_full_btn.setEnabled(False)

        self.sim_results = None
        self.reset_summary_card(computing=True)

        self.update_status("="*50)
        self.update_status("[开始] 1D 热失控仿真开始运行...")
        self.update_status("="*50)

        self.sim_worker = SimulationWorker(params)
        self.sim_worker.progress_signal.connect(self.update_status)
        self.sim_worker.finished_signal.connect(self.on_simulation_finished)
        self.sim_worker.error_signal.connect(self.on_simulation_error)
        self.sim_worker.finished.connect(self._on_simulation_worker_finished)
        self.sim_worker.start()

    def _on_simulation_worker_finished(self):
        self.sim_run_btn.setEnabled(True)
        self.sim_stop_btn.setEnabled(False)
        if self.sim_results is None:
            self.reset_summary_card(computing=False)

    def stop_simulation(self):
        if self.sim_worker and self.sim_worker.isRunning():
            self.sim_stop_btn.setEnabled(False)
            self.update_status("[中止] 正在请求终止仿真线程，请稍候...")
            self.sim_worker.cancel()
        else:
            self._on_simulation_worker_finished()

    def on_simulation_error(self, err_msg):
        self.update_status(err_msg)
        self._on_simulation_worker_finished()
        QMessageBox.critical(self, "仿真错误", err_msg)

    def on_simulation_finished(self, results):
        self.sim_results = results
        self._on_simulation_worker_finished()
        self.save_std_btn.setEnabled(True)
        self.save_full_btn.setEnabled(True)

        self.update_summary_card(results)
        self.update_status("="*50)
        self.update_status("[完成] 1D 热失控仿真计算完成！正在绘制默认综合曲线演化图...")
        self.update_status("="*50)

        self.on_sim_view_changed()

    def on_sim_view_changed(self):
        if self.sim_results is None:
            return

        idx = self.sim_view_combo.currentIndex()
        res = self.sim_results

        t_arr = res["t_arr"]
        z_coords = res["z_coords"]
        T_history = res["T_history"]
        c_history = res["c_history"]
        rate_history = res["rate_history"]
        power_history = res["power_history"]
        chem_rate_history = res["chem_rate_history"]
        rate_mean_history = res["rate_mean_history"]
        rate_max_history = res["rate_max_history"]
        c_mean_history = res["c_mean_history"]
        rxn_names = res["rxn_names"]
        n_rxns = len(rxn_names)
        th_end = res["th_end"]
        Ph = res["Ph"]
        t_max = res["t_max"]
        L_m = res["L_m"]
        n_layers = res["n_layers"]

        rxn_colors = ['#4daf4a', '#ff7f00', '#377eb8', '#e41a1c', '#984ea3', '#a65628', '#f781bf', '#999999']
        self.fig.clf()

        if idx == 0:
            gs = self.fig.add_gridspec(2, 3, hspace=0.32, wspace=0.28)
            ax_a = self.fig.add_subplot(gs[0, 0])
            for k, name in enumerate(rxn_names):
                ax_a.plot(t_arr, c_mean_history[:, k], label=name, lw=1.8, color=rxn_colors[k % len(rxn_colors)])
            ax_a.axvline(th_end, color='gray', linestyle=':', lw=1.2, label=f'{th_end:g}s 停热')
            ax_a.set_title('(a) 反应物平均浓度', fontsize=11, fontweight='bold')
            ax_a.set_xlabel('时间 Time (s)', fontsize=9.5)
            ax_a.set_ylabel('归一化平均浓度 (-)', fontsize=9.5)
            ax_a.set_xlim(0, t_max)
            ax_a.set_ylim(-0.05, 1.05)
            ax_a.grid(True, linestyle='--', alpha=0.5)
            ax_a.legend(loc='best', fontsize=7.5, frameon=False)

            ax_b = self.fig.add_subplot(gs[0, 1])
            for k, name in enumerate(rxn_names):
                ax_b.plot(t_arr, rate_mean_history[:, k], label=name, lw=1.6, color=rxn_colors[k % len(rxn_colors)])
            ax_b.set_title('(b) 反应物平均反应速率', fontsize=11, fontweight='bold')
            ax_b.set_xlabel('时间 Time (s)', fontsize=9.5)
            ax_b.set_ylabel('平均反应速率 (1/s)', fontsize=9.5)
            ax_b.set_xlim(0, t_max)
            ax_b.set_yscale('log')
            ax_b.set_ylim(1e-6, max(1e0, 10.0 ** np.ceil(np.log10(max(np.max(rate_mean_history), 1e-5)))))
            ax_b.grid(True, linestyle='--', alpha=0.5, which='both')
            ax_b.legend(loc='best', fontsize=7.5, frameon=False)

            ax_c = self.fig.add_subplot(gs[0, 2])
            for k, name in enumerate(rxn_names):
                ax_c.plot(t_arr, rate_max_history[:, k], label=name, lw=1.6, color=rxn_colors[k % len(rxn_colors)])
            ax_c.set_title('(c) 反应物最大反应速率', fontsize=11, fontweight='bold')
            ax_c.set_xlabel('时间 Time (s)', fontsize=9.5)
            ax_c.set_ylabel('最大反应速率 (1/s)', fontsize=9.5)
            ax_c.set_xlim(0, t_max)
            ax_c.set_yscale('log')
            ax_c.set_ylim(1e-6, max(1e1, 10.0 ** np.ceil(np.log10(max(np.max(rate_max_history), 1e-5)))))
            ax_c.grid(True, linestyle='--', alpha=0.5, which='both')
            ax_c.legend(loc='best', fontsize=7.5, frameon=False)

            ax_d = self.fig.add_subplot(gs[1, 0])
            P_heat_arr = np.where(t_arr <= th_end, Ph, 0.0)
            for k, name in enumerate(rxn_names):
                ax_d.plot(t_arr, chem_rate_history[:, k], label=name, lw=1.8, color=rxn_colors[k % len(rxn_colors)])
            ax_d.plot(t_arr, np.maximum(P_heat_arr, 1e-12), label=f'加热功率 ({Ph:g}W)', lw=1.6, linestyle='--', color='#333333')
            ax_d.set_title('(d) 全域放热功率', fontsize=11, fontweight='bold')
            ax_d.set_xlabel('时间 Time (s)', fontsize=9.5)
            ax_d.set_ylabel('放热/加热功率 (W)', fontsize=9.5)
            ax_d.set_xlim(0, t_max)
            ax_d.set_yscale('log')
            ax_d.set_ylim(1e-1, max(1e5, 10.0 ** np.ceil(np.log10(max(np.max(chem_rate_history), Ph, 1.0)))))
            ax_d.grid(True, linestyle='--', alpha=0.5, which='both')
            ax_d.legend(loc='best', fontsize=7.5, frameon=False)

            ax_e = self.fig.add_subplot(gs[1, 1])
            ax_e.plot(t_arr, T_history[:, 0], color='#e41a1c', lw=1.8, label='受热面 (z=0)')
            ax_e.plot(t_arr, T_history[:, n_layers // 4], color='#ff7f00', lw=1.5, label='1/4厚度')
            ax_e.plot(t_arr, T_history[:, n_layers // 2], color='#4daf4a', lw=1.6, label='中心 (z=L/2)')
            ax_e.plot(t_arr, T_history[:, 3 * n_layers // 4], color='#377eb8', lw=1.5, label='3/4厚度')
            ax_e.plot(t_arr, T_history[:, -1], color='#984ea3', lw=1.8, linestyle='--', label='冷面 (z=L)')
            ax_e.plot(t_arr, np.mean(T_history, axis=1), color='#a65628', lw=1.8, linestyle='-.', label='均温')
            ax_e.set_title('(e) 厚度方向不同位置温度', fontsize=11, fontweight='bold')
            ax_e.set_xlabel('时间 Time (s)', fontsize=9.5)
            ax_e.set_ylabel('温度 (°C)', fontsize=9.5)
            ax_e.set_xlim(0, t_max)
            ax_e.set_ylim(0, max(650.0, np.ceil(np.max(T_history) / 50.0) * 50.0))
            ax_e.grid(True, linestyle='--', alpha=0.5)
            ax_e.legend(loc='best', fontsize=7.5, frameon=False)

            ax_f = self.fig.add_subplot(gs[1, 2])
            snapshot_times = [600, 800, 900, 1000, 1500, 2350]
            colors = plt.cm.plasma(np.linspace(0.1, 0.9, len(snapshot_times)))
            for t_snap, c in zip(snapshot_times, colors):
                if t_snap <= t_max:
                    idx_snap = np.argmin(np.abs(t_arr - t_snap))
                    ax_f.plot(z_coords, T_history[idx_snap], '-o', ms=2.5, color=c, lw=1.6, label=f't={t_snap}s')
            ax_f.set_title('(f) 厚度方向温度剖面', fontsize=11, fontweight='bold')
            ax_f.set_xlabel('厚度 z (mm)', fontsize=9.5)
            ax_f.set_ylabel('温度 (°C)', fontsize=9.5)
            ax_f.set_xlim(0, L_m * 1000.0)
            ax_f.set_ylim(20, max(650.0, np.ceil(np.max(T_history) / 50.0) * 50.0))
            ax_f.grid(True, linestyle='--', alpha=0.5)
            ax_f.legend(loc='best', fontsize=7.5, frameon=False)

        elif idx in [1, 2, 3, 4, 5, 6]:
            ax = self.fig.add_subplot(111)
            if idx == 1:
                for k, name in enumerate(rxn_names):
                    ax.plot(t_arr, c_mean_history[:, k], label=name, lw=2.2, color=rxn_colors[k % len(rxn_colors)])
                ax.axvline(th_end, color='gray', linestyle=':', lw=1.5, label=f'{th_end:g}s 停热')
                ax.set_title('反应物平均浓度随时间演化曲线', fontsize=14, fontweight='bold')
                ax.set_xlabel('时间 Time (s)', fontsize=12)
                ax.set_ylabel('归一化平均浓度 (-)', fontsize=12)
                ax.set_xlim(0, t_max)
                ax.set_ylim(-0.05, 1.05)
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.legend(loc='best', fontsize=11, frameon=False)
            elif idx == 2:
                for k, name in enumerate(rxn_names):
                    ax.plot(t_arr, rate_mean_history[:, k], label=name, lw=2.0, color=rxn_colors[k % len(rxn_colors)])
                ax.set_title('反应物全域平均反应速率演化', fontsize=14, fontweight='bold')
                ax.set_xlabel('时间 Time (s)', fontsize=12)
                ax.set_ylabel('平均反应速率 (1/s)', fontsize=12)
                ax.set_xlim(0, t_max)
                ax.set_yscale('log')
                ax.grid(True, linestyle='--', alpha=0.5, which='both')
                ax.legend(loc='best', fontsize=11, frameon=False)
            elif idx == 3:
                for k, name in enumerate(rxn_names):
                    ax.plot(t_arr, rate_max_history[:, k], label=name, lw=2.0, color=rxn_colors[k % len(rxn_colors)])
                ax.set_title('反应物局部最大反应速率演化', fontsize=14, fontweight='bold')
                ax.set_xlabel('时间 Time (s)', fontsize=12)
                ax.set_ylabel('最大反应速率 (1/s)', fontsize=12)
                ax.set_xlim(0, t_max)
                ax.set_yscale('log')
                ax.grid(True, linestyle='--', alpha=0.5, which='both')
                ax.legend(loc='best', fontsize=11, frameon=False)
            elif idx == 4:
                P_heat_arr = np.where(t_arr <= th_end, Ph, 0.0)
                for k, name in enumerate(rxn_names):
                    ax.plot(t_arr, chem_rate_history[:, k], label=name, lw=2.2, color=rxn_colors[k % len(rxn_colors)])
                ax.plot(t_arr, np.maximum(P_heat_arr, 1e-12), label=f'加热功率 ({Ph:g}W)', lw=2.0, linestyle='--', color='#333333')
                ax.set_title('全域化学放热功率与加热功率', fontsize=14, fontweight='bold')
                ax.set_xlabel('时间 Time (s)', fontsize=12)
                ax.set_ylabel('功率 (W)', fontsize=12)
                ax.set_xlim(0, t_max)
                ax.set_yscale('log')
                ax.set_ylim(1e-1, max(1e5, 10.0 ** np.ceil(np.log10(max(np.max(chem_rate_history), Ph, 1.0)))))
                ax.grid(True, linestyle='--', alpha=0.5, which='both')
                ax.legend(loc='best', fontsize=11, frameon=False)
            elif idx == 5:
                ax.plot(t_arr, T_history[:, 0], color='#e41a1c', lw=2.2, label='受热面 (z=0)')
                ax.plot(t_arr, T_history[:, n_layers // 4], color='#ff7f00', lw=1.8, label='1/4厚度')
                ax.plot(t_arr, T_history[:, n_layers // 2], color='#4daf4a', lw=2.0, label='中心 (z=L/2)')
                ax.plot(t_arr, T_history[:, 3 * n_layers // 4], color='#377eb8', lw=1.8, label='3/4厚度')
                ax.plot(t_arr, T_history[:, -1], color='#984ea3', lw=2.2, linestyle='--', label='冷面 (z=L)')
                ax.plot(t_arr, np.mean(T_history, axis=1), color='#a65628', lw=2.0, linestyle='-.', label='电芯均温 Mean')
                ax.set_title('厚度方向各测点温度随时间变化', fontsize=14, fontweight='bold')
                ax.set_xlabel('时间 Time (s)', fontsize=12)
                ax.set_ylabel('温度 (°C)', fontsize=12)
                ax.set_xlim(0, t_max)
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.legend(loc='best', fontsize=11, frameon=False)
            elif idx == 6:
                snapshot_times = [600, 800, 900, 1000, 1200, 1500, 2000, 2500]
                colors = plt.cm.plasma(np.linspace(0.1, 0.9, len(snapshot_times)))
                for t_snap, c in zip(snapshot_times, colors):
                    if t_snap <= t_max:
                        idx_snap = np.argmin(np.abs(t_arr - t_snap))
                        ax.plot(z_coords, T_history[idx_snap], '-o', ms=4.0, color=c, lw=2.0, label=f't = {t_snap} s')
                ax.set_title('厚度方向关键时刻温度剖面 T(z)', fontsize=14, fontweight='bold')
                ax.set_xlabel('厚度方向位置 z (mm)', fontsize=12)
                ax.set_ylabel('温度 (°C)', fontsize=12)
                ax.set_xlim(0, L_m * 1000.0)
                ax.grid(True, linestyle='--', alpha=0.5)
                ax.legend(loc='best', fontsize=10, frameon=False)

        elif idx == 7:
            ax = self.fig.add_subplot(111)
            pcm = ax.pcolormesh(z_coords, t_arr, T_history, shading='gouraud', cmap='coolwarm')
            cbar = self.fig.colorbar(pcm, ax=ax, pad=0.02)
            cbar.set_label('温度 (°C)', fontsize=11)
            ax.axhline(th_end, color='white', linestyle='--', lw=1.5, alpha=0.9, label=f'{th_end:g}s 停热')
            ax.set_title('厚度-时间-温度演变云图 T(z, t)', fontsize=14, fontweight='bold')
            ax.set_xlabel('厚度方向位置 z (mm)', fontsize=12)
            ax.set_ylabel('时间 Time (s)', fontsize=12)
            ax.set_ylim(0, t_max)
            ax.legend(loc='upper right', fontsize=10, labelcolor='white', frameon=False)

        elif idx in [8, 9, 10]:
            data_3d = c_history if idx == 8 else rate_history if idx == 9 else power_history
            cbar_label = '归一化浓度 (-)' if idx == 8 else '反应速率 (1/s)' if idx == 9 else '产热功率 (W)'
            cmap_name = 'viridis' if idx in [8, 9] else 'inferno'
            title_kind = '浓度' if idx == 8 else '反应速率' if idx == 9 else '产热功率'

            if n_rxns <= 1:
                n_rows, n_cols = 1, 1
            elif n_rxns == 2:
                n_rows, n_cols = 1, 2
            else:
                n_cols = 2
                n_rows = int(np.ceil(n_rxns / 2))

            axes = self.fig.subplots(n_rows, n_cols)
            if isinstance(axes, np.ndarray):
                ax_flat = axes.flatten()
            else:
                ax_flat = [axes]

            self.fig.subplots_adjust(hspace=0.32, wspace=0.26)
            sub_labels = [f"({chr(ord('a') + i)})" for i in range(max(26, n_rxns))]

            for k, ax_k in enumerate(ax_flat):
                if k < n_rxns:
                    vmin = 0.0 if idx == 8 else None
                    vmax = 1.0 if idx == 8 else None
                    pcm = ax_k.pcolormesh(z_coords, t_arr, data_3d[:, :, k], shading='gouraud', cmap=cmap_name, vmin=vmin, vmax=vmax)
                    cbar = self.fig.colorbar(pcm, ax=ax_k, pad=0.02)
                    cbar.set_label(cbar_label, fontsize=9.0)
                    ax_k.axhline(th_end, color='white', linestyle='--', lw=1.2, alpha=0.8)
                    ax_k.set_title(f"{sub_labels[k]} 厚度-时间-{rxn_names[k]}{title_kind}", fontsize=10.5, fontweight='bold')
                    ax_k.set_xlabel('厚度 z (mm)', fontsize=9.0)
                    ax_k.set_ylabel('时间 Time (s)', fontsize=9.0)
                    ax_k.set_xlim(z_coords[0], z_coords[-1])
                    ax_k.set_ylim(0, t_max)
                else:
                    ax_k.set_visible(False)

        self.canvas.draw_idle()

    def export_sim_excel(self, full_matrix=False):
        if self.sim_results is None:
            QMessageBox.warning(self, "提示", "尚未进行仿真计算或暂无数据。")
            return

        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存完整云图矩阵数据" if full_matrix else "保存仿真时程数据",
            f"1D_TR_Simulation_{'Full' if full_matrix else 'Summary'}.xlsx",
            "Excel Files (*.xlsx)"
        )
        if not file_path:
            return

        try:
            self.update_status(f"正在导出 Excel 文件 (full_matrix={full_matrix})...")
            res = self.sim_results

            t_arr = res["t_arr"]
            z_coords = res["z_coords"]
            T_history = res["T_history"]
            c_history = res["c_history"]
            rate_history = res["rate_history"]
            power_history = res["power_history"]
            chem_rate_history = res["chem_rate_history"]
            rate_mean_history = res["rate_mean_history"]
            rate_max_history = res["rate_max_history"]
            c_mean_history = res["c_mean_history"]
            rxn_names = res["rxn_names"]
            dt = res["dt"]
            th_end = res["th_end"]
            n_layers = res["n_layers"]

            save_stride = max(1, int(round(0.5 / dt))) if len(t_arr) > 50000 else 1
            t_slice = t_arr[::save_stride]

            T_hot = T_history[:, 0]
            T_qtr = T_history[:, n_layers // 4]
            T_mid = T_history[:, n_layers // 2]
            T_3qtr = T_history[:, 3 * n_layers // 4]
            T_cold = T_history[:, -1]
            T_mean = np.mean(T_history, axis=1)

            history_data = {
                't (s)': t_slice,
                'T_hot (C)': T_hot[::save_stride],
                'T_qtr (C)': T_qtr[::save_stride],
                'T_mid (C)': T_mid[::save_stride],
                'T_3qtr (C)': T_3qtr[::save_stride],
                'T_cold (C)': T_cold[::save_stride],
                'T_mean (C)': T_mean[::save_stride],
            }
            for k, name in enumerate(rxn_names):
                history_data[f'c_mean_{name} (-)'] = c_mean_history[::save_stride, k]
            for k, name in enumerate(rxn_names):
                history_data[f'rate_mean_{name} (1/s)'] = rate_mean_history[::save_stride, k]
            for k, name in enumerate(rxn_names):
                history_data[f'rate_max_{name} (1/s)'] = rate_max_history[::save_stride, k]
            for k, name in enumerate(rxn_names):
                history_data[f'P_{name} (W)'] = chem_rate_history[::save_stride, k]
            history_df = pd.DataFrame(history_data)

            pk_idx_cold = np.argmax(T_cold)
            pk_idx_mean = np.argmax(T_mean)
            summary_df = pd.DataFrame({
                'Metric': ['Initial Temp T0 (C)', f'Heating End T({th_end:g}s) (C)', 'Peak Temp Tmax (C)', 'Peak Time tmax (s)'],
                '1D_Cold_Face': [T_cold[0], np.interp(th_end, t_arr, T_cold), T_cold[pk_idx_cold], t_arr[pk_idx_cold]],
                '1D_Mean_Temp': [T_mean[0], np.interp(th_end, t_arr, T_mean), T_mean[pk_idx_mean], t_arr[pk_idx_mean]],
            })

            try:
                import xlsxwriter
                engine_name = 'xlsxwriter'
            except ImportError:
                engine_name = 'openpyxl'

            with pd.ExcelWriter(file_path, engine=engine_name) as writer:
                history_df.to_excel(writer, sheet_name='Time_History', index=False)
                summary_df.to_excel(writer, sheet_name='Summary_Metrics', index=False)

                if full_matrix:
                    z_cols = [f'z={z:.2f}mm' for z in z_coords]
                    t_col_2d = t_slice[:, None]
                    all_cols = ['t (s)'] + z_cols

                    df_T = pd.DataFrame(np.hstack([t_col_2d, T_history[::save_stride, :]]), columns=all_cols)
                    df_T.to_excel(writer, sheet_name='Contour_T'[:31], index=False)

                    for k, name in enumerate(rxn_names):
                        safe_name = name.replace('/', '_').replace('\\', '_').replace('*', '_').replace(':', '_')
                        df_c = pd.DataFrame(np.hstack([t_col_2d, c_history[::save_stride, :, k]]), columns=all_cols)
                        df_c.to_excel(writer, sheet_name=f'C_{safe_name}'[:31], index=False)

                        df_r = pd.DataFrame(np.hstack([t_col_2d, rate_history[::save_stride, :, k]]), columns=all_cols)
                        df_r.to_excel(writer, sheet_name=f'Rate_{safe_name}'[:31], index=False)

                        df_p = pd.DataFrame(np.hstack([t_col_2d, power_history[::save_stride, :, k]]), columns=all_cols)
                        df_p.to_excel(writer, sheet_name=f'Power_{safe_name}'[:31], index=False)

            self.update_status(f"[成功] 数据成功保存至: {file_path}")
            QMessageBox.information(self, "导出成功", f"数据已成功导出保存至:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", f"导出 Excel 出现异常: {str(e)}")

    # --------------------------------------------------------------------------
    # 模式 B: 1D 热失控动力学参数辨识实现
    # --------------------------------------------------------------------------
    def on_tr_amb_source_changed(self):
        curr_text = self.tr_amb_combo.currentText()
        is_fixed = (curr_text == "Constant")
        self.tr_amb_val_edit.setEnabled(is_fixed)
        if not is_fixed and self.current_ident_df_tr is not None:
            if curr_text in self.current_ident_df_tr.columns:
                try:
                    val0 = float(self.current_ident_df_tr[curr_text].dropna().iloc[0])
                    self.tr_amb_val_edit.setText(f"{val0:.2f}")
                except Exception:
                    pass

    def start_tr_identify(self):
        file_path = self.tr_exp_file_edit.text().strip()
        if not file_path or not os.path.exists(file_path):
            QMessageBox.warning(self, "提示", "请先选择有效的实验对标数据文件 (.xlsx 或 .csv)！")
            return

        t_col = self.tr_time_col_combo.currentText()
        T_col = self.tr_temp_col_combo.currentText()
        if not t_col or not T_col or self.current_ident_df_tr is None:
            QMessageBox.warning(self, "提示", "请选择正确的时间列和温度列！")
            return

        df = self.current_ident_df_tr
        try:
            t_exp_arr = df[t_col].to_numpy(dtype=float)
            T_exp_arr = df[T_col].to_numpy(dtype=float)
            mask = np.isfinite(t_exp_arr) & np.isfinite(T_exp_arr) & (t_exp_arr >= 0.0)
            t_exp_arr = t_exp_arr[mask]
            T_exp_arr = T_exp_arr[mask]
            if len(t_exp_arr) < 5:
                QMessageBox.warning(self, "数据错误", "实验数据有效点数过少！")
                return
        except Exception as e:
            QMessageBox.critical(self, "数据解析失败", f"读取实验数据列出错: {str(e)}")
            return

        # 测点映射
        pos_txt = self.tr_pos_combo.currentText()
        pos_map = {
            "全域均温 (Tmean)": "mean",
            "加热面表面 (z = 0)": "0",
            "1/4 厚度处 (z = L/4)": "1/4",
            "中心位置 (z = L/2)": "1/2",
            "3/4 厚度处 (z = 3L/4)": "3/4",
            "背火面表面 (z = L)": "1"
        }
        target_pos_mode = pos_map.get(pos_txt, "mean")

        # 几何物性参数从共享面板提取
        sim_params = self.get_current_sim_params()
        # 1D 辨识专用反应与自定义上下限直接从专属表格提取
        ident_rxns = self.get_current_ident_reactions()
        n_rxns = len(ident_rxns)
        if n_rxns == 0:
            QMessageBox.warning(self, "提示", "待辨识反应列表不能为空！")
            return

        n_particles = int(self.tr_pso_particles_edit.text().strip() or "36")
        max_iter = int(self.tr_pso_iters_edit.text().strip() or "35")
        n_workers = int(self.tr_ident_workers_edit.text().strip() or "8")
        dt_ident = float(self.sim_ctrl_inputs.get("dt", QLineEdit("0.5")).text().strip() or "0.5")
        T_amb_val = float(self.tr_amb_val_edit.text().strip() or "25.0")

        ident_config = {
            "geometry": sim_params["geometry"],
            "control": sim_params["control"],
            "identify": {
                "t_exp": t_exp_arr,
                "T_exp": T_exp_arr,
                "target_pos_mode": target_pos_mode,
                "reactions": ident_rxns,
                "dt": dt_ident,
                "n_workers": n_workers,
                "n_particles": n_particles,
                "max_iter": max_iter,
                "T_amb_val": T_amb_val
            }
        }

        # 如果上一次的辨识 worker 仍在运行，做安全清理
        if self.tr_id_worker and self.tr_id_worker.isRunning():
            try:
                self.tr_id_worker.cancel()
                self.tr_id_worker.terminate()
                self.tr_id_worker.wait(150)
            except Exception:
                pass

        self.tr_id_run_btn.setEnabled(False)
        self.tr_id_stop_btn.setEnabled(True)
        self.tr_id_apply_btn.setEnabled(False)
        self.tr_id_save_btn.setEnabled(False)

        self.update_status("="*50)
        self.update_status(f"[启动] 开始 1D TR 副反应多步参数 PSO 辨识 (反应数={n_rxns}, 目标={pos_txt})...")
        self.update_status("="*50)

        self.tr_id_worker = TR1DIdentifyWorker(ident_config)
        self.tr_id_worker.progress_signal.connect(self.update_status)
        self.tr_id_worker.finished_signal.connect(self.on_tr_identify_finished)
        self.tr_id_worker.error_signal.connect(self.on_tr_identify_error)
        self.tr_id_worker.finished.connect(self._on_tr_identify_worker_finished)
        self.tr_id_worker.start()

    def _on_tr_identify_worker_finished(self):
        self.tr_id_run_btn.setEnabled(True)
        self.tr_id_stop_btn.setEnabled(False)

    def stop_tr_identify(self):
        if self.tr_id_worker and self.tr_id_worker.isRunning():
            self.tr_id_stop_btn.setEnabled(False)
            self.update_status("[中止] 正在请求终止 1D TR 辨识进程，请稍候...")
            self.tr_id_worker.cancel()
            from PySide6.QtCore import QTimer
            QTimer.singleShot(600, self._force_check_tr_identify_stopped)
        else:
            self._on_tr_identify_worker_finished()

    def _force_check_tr_identify_stopped(self):
        if self.tr_id_worker and self.tr_id_worker.isRunning():
            try:
                self.tr_id_worker.terminate()
                self.tr_id_worker.wait(100)
            except Exception:
                pass
        self._on_tr_identify_worker_finished()
        self.update_status("[中止] 1D TR 辨识进程已安全退出，可以重新开始辨识。")

    def on_tr_identify_error(self, err):
        self.update_status(err)
        self._on_tr_identify_worker_finished()
        QMessageBox.critical(self, "辨识错误", err)

    def on_tr_identify_finished(self, res):
        self.last_tr_id_result = res
        self._on_tr_identify_worker_finished()
        self.tr_id_apply_btn.setEnabled(True)
        self.tr_id_save_btn.setEnabled(True)

        self.update_status("="*50)
        self.update_status(f"[辨识成功] 1D TR 最优参数求解完成！正在右侧主画布渲染实验对标图...")
        self.update_status("="*50)

        # 绘制对标曲线图
        self.plot_tr_identify_comparison(res)

    def plot_tr_identify_comparison(self, res):
        self.fig.clf()
        ax = self.fig.add_subplot(111)

        t_arr = res["t_arr"]
        T_sim_target = res["T_sim_target"]
        T_sim_mean = res["T_sim_mean"]
        t_exp = res["t_exp"]
        T_exp = res["T_exp"]
        target_name = res["target_name"]
        th_end = res["th_end"]

        # 绘制仿真曲线
        ax.plot(t_arr, T_sim_target, color='#1f77b4', lw=2.4, label=f'1D 仿真 ({target_name})')
        if "均温" not in target_name:
            ax.plot(t_arr, T_sim_mean, color='#7f7f7f', lw=1.6, linestyle='-.', alpha=0.85, label='1D 仿真全域均温 $T_{mean}$')

        # 绘制实验实测数据
        stride_exp = max(1, len(t_exp) // 120)
        ax.plot(t_exp[::stride_exp], T_exp[::stride_exp], 'o', ms=3.5, color='#d62728', alpha=0.85, label='实验实测温度')
        ax.plot(t_arr, res["T_exp_interp"], color='#d62728', lw=1.6, linestyle='--', alpha=0.7)

        ax.axvline(th_end, color='gray', linestyle=':', lw=1.5, label=f'{th_end:g}s 停热')

        stats_text = (
            f"1D TR 辨识误差统计:\n"
            f"• RMSE = {res['rmse']:.2f} °C\n"
            f"• MAE  = {res['mae']:.2f} °C\n"
            f"• 最大偏差 = {res['max_err']:.2f} °C\n"
            f"• 仿真最高温 = {np.max(T_sim_target):.1f} °C\n"
            f"• 实验最高温 = {np.max(T_exp):.1f} °C\n"
            f"• 耗时 = {res['elapsed']:.2f} s"
        )
        ax.legend(loc='upper left', fontsize=10.5, frameon=False)
        ax.text(
            0.97, 0.05, stats_text,
            transform=ax.transAxes,
            fontsize=10,
            verticalalignment='bottom',
            horizontalalignment='right',
            bbox=dict(boxstyle='round,pad=0.6', facecolor='#f8fafc', edgecolor='#94a3b8', alpha=0.92, lw=1.2)
        )

        ax.set_title(f'1D 热失控动力学参数辨识结果对标 ({target_name} vs. 实验实测)', fontsize=13, fontweight='bold', pad=10)
        ax.set_xlabel('时间 Time (s)', fontsize=11)
        ax.set_ylabel('温度 Temperature (°C)', fontsize=11)
        ax.set_xlim(0, t_arr[-1])
        y_max = max(650.0, np.ceil(max(np.max(T_sim_target), np.max(T_exp)) / 50.0) * 50.0)
        ax.set_ylim(0, y_max)
        ax.grid(True, linestyle='--', alpha=0.5)
        self.canvas.draw_idle()

    def apply_tr_identify_to_rxn_table(self):
        if not self.last_tr_id_result:
            return
        opt_rxns = self.last_tr_id_result["opt_reactions"]
        self._populate_sim_rxn_table(opt_rxns)
        self.update_status("已成功将 1D TR 辨识出的最优参数导入前向仿真动力学表格！")
        reply = QMessageBox.question(
            self, "导入成功",
            "辨识得到的最优动力学参数已成功导入前向仿真动力学表格！\n是否立即切换至【1D 热失控仿真计算】模式？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            self.sim_mode_combo.setCurrentIndex(0)

    def save_tr_identify_params_txt(self):
        if not self.last_tr_id_result:
            return
        default_name = f"TR_Sim_1D_Identify_Result_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        file_path, _ = QFileDialog.getSaveFileName(self, "保存辨识参数", default_name, "Text Files (*.txt);;All Files (*)")
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"# 1D TR 热失控动力学参数 PSO 辨识结果 ({time.strftime('%Y-%m-%d %H:%M:%S')})\n")
                f.write(f"# 对齐测点: {self.last_tr_id_result['target_name']}, RMSE: {self.last_tr_id_result['rmse']:.3f} °C\n\n")
                f.write("\n".join(self.last_tr_id_result["param_lines"]) + "\n")
            self.update_status(f"辨识参数已保存至: {file_path}")
            QMessageBox.information(self, "保存成功", f"参数已保存至:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存出错: {str(e)}")

    # --------------------------------------------------------------------------
    # 模式 C: ARC 绝热热失控动力学参数辨识实现
    # --------------------------------------------------------------------------
    def auto_fill_arc_temps(self):
        if self.current_ident_df_arc is None:
            return
        col = self.arc_temp_combo.currentText()
        if col in self.current_ident_df_arc.columns:
            try:
                s = pd.to_numeric(self.current_ident_df_arc[col], errors='coerce').dropna()
                if not s.empty:
                    t1_val = float(s.iloc[0])
                    t3_val = float(s.max())
                    self.arc_t1_edit.setText(f"{t1_val:.1f}")
                    self.arc_t3_edit.setText(f"{t3_val:.1f}")
            except Exception:
                pass

    def start_arc_identify(self):
        file_path = self.arc_file_edit.text().strip()
        if not file_path or not os.path.exists(file_path):
            QMessageBox.warning(self, "提示", "请先选择有效的 ARC 数据文件！")
            return

        t_col = self.arc_time_combo.currentText()
        T_col = self.arc_temp_combo.currentText()
        if not t_col or not T_col or self.current_ident_df_arc is None:
            QMessageBox.warning(self, "提示", "请选择正确的时间列和温度列！")
            return

        df = self.current_ident_df_arc
        try:
            t_s = df[t_col].to_numpy(dtype=float)
            T_C = df[T_col].to_numpy(dtype=float)
            mask = np.isfinite(t_s) & np.isfinite(T_C) & (t_s >= 0.0)
            t_s = t_s[mask]
            T_C = T_C[mask]
            if len(t_s) < 5:
                QMessageBox.warning(self, "数据错误", "ARC 数据点数过少！")
                return
        except Exception as e:
            QMessageBox.critical(self, "数据解析失败", f"解析数据出错: {str(e)}")
            return

        T1 = float(self.arc_t1_edit.text().strip() or str(T_C[0])) + 273.15
        T3 = float(self.arc_t3_edit.text().strip() or str(np.max(T_C))) + 273.15
        rho = float(self.arc_rho_edit.text().strip() or "2400.0")
        cp = float(self.arc_cp_edit.text().strip() or "1071.0")

        algo_idx = self.arc_algo_combo.currentIndex()
        method_mode = "hybrid" if algo_idx == 0 else ("nm" if algo_idx == 1 else "pso")

        lna0 = float(self.arc_lna_edit.text().strip() or "22.757")
        lne0 = float(self.arc_lne_edit.text().strip() or "11.671")
        n0 = float(self.arc_n_edit.text().strip() or "3.278")

        pso_n = int(self.arc_pso_particles_edit.text().strip() or "30")
        pso_k = int(self.arc_pso_iters_edit.text().strip() or "25")

        # 提取搜索范围与自催化 m 设置
        lb_lna = float(self.arc_lb_lna_edit.text().strip() or "5.0")
        ub_lna = float(self.arc_ub_lna_edit.text().strip() or "65.0")
        lb_lne = float(self.arc_lb_lne_edit.text().strip() or "8.0")
        ub_lne = float(self.arc_ub_lne_edit.text().strip() or "16.0")
        lb_n = float(self.arc_lb_n_edit.text().strip() or "0.2")
        ub_n = float(self.arc_ub_n_edit.text().strip() or "4.5")
        init_m = float(self.arc_init_m_edit.text().strip() or "0.0")
        lb_m = float(self.arc_lb_m_edit.text().strip() or "0.0")
        ub_m = float(self.arc_ub_m_edit.text().strip() or "2.0")

        ext_factor = float(self.arc_ext_factor_edit.text().strip() or "1.0")

        arc_cfg = {
            "t_s": t_s,
            "T_C": T_C,
            "T1": T1,
            "T3": T3,
            "rho": rho,
            "cp": cp,
            "ext_factor": ext_factor,
            "method_mode": method_mode,
            "init_x": [lna0, lne0, n0],
            "init_m": init_m,
            "lb_lnA": lb_lna,
            "ub_lnA": ub_lna,
            "lb_lnE": lb_lne,
            "ub_lnE": ub_lne,
            "lb_n": lb_n,
            "ub_n": ub_n,
            "lb_m": lb_m,
            "ub_m": ub_m,
            "pso_particles": pso_n,
            "pso_iters": pso_k
        }

        self.arc_run_btn.setEnabled(False)
        self.arc_stop_btn.setEnabled(True)
        self.arc_import_btn.setEnabled(False)
        self.arc_save_btn.setEnabled(False)

        self.update_status("="*50)
        self.update_status(f"[启动] 开始 ARC 动力学参数辨识 (模式={method_mode}, 自催化m={'是' if init_m > 0 else '否'})...")
        self.update_status("="*50)

        self.arc_id_worker = ARCIdentifyWorker(arc_cfg)
        self.arc_id_worker.progress_signal.connect(self.update_status)
        self.arc_id_worker.finished_signal.connect(self.on_arc_identify_finished)
        self.arc_id_worker.error_signal.connect(self.on_arc_identify_error)
        self.arc_id_worker.finished.connect(self._on_arc_identify_worker_finished)
        self.arc_id_worker.start()

    def _on_arc_identify_worker_finished(self):
        self.arc_run_btn.setEnabled(True)
        self.arc_stop_btn.setEnabled(False)

    def stop_arc_identify(self):
        if self.arc_id_worker and self.arc_id_worker.isRunning():
            self.arc_stop_btn.setEnabled(False)
            self.update_status("[中止] 正在请求终止 ARC 辨识线程，请稍候...")
            self.arc_id_worker.cancel()
        else:
            self._on_arc_identify_worker_finished()

    def on_arc_identify_error(self, err):
        self.update_status(err)
        self._on_arc_identify_worker_finished()
        QMessageBox.critical(self, "辨识错误", err)

    def on_arc_identify_finished(self, res):
        self.last_arc_id_result = res
        self._on_arc_identify_worker_finished()
        self.arc_import_btn.setEnabled(True)
        self.arc_save_btn.setEnabled(True)

        self.update_status("="*50)
        self.update_status(f"[辨识成功] ARC 最优动力学参数辨识完成！已渲染反应进度与温度双对标图。")
        self.update_status("="*50)

        self.plot_arc_identify_comparison(res)

    def plot_arc_identify_comparison(self, res):
        self.fig.clf()
        axes = self.fig.subplots(1, 2)
        ax1, ax2 = axes[0], axes[1]
        self.fig.subplots_adjust(wspace=0.25, hspace=0.2)

        t_crop = res.get("t_crop", res.get("t_all"))
        a_exp = res.get("a_exp", res.get("alpha_exp"))
        a_cal = res.get("a_cal", res.get("alphacal"))
        T_exp_C = res["T_exp_C"]
        T_sim_C = res["T_sim_C"]
        pt_t1 = res.get("pt_t1", (float(t_crop[0]), float(T_exp_C[0])))
        pt_t3 = res.get("pt_t3", (float(t_crop[-1]), float(T_exp_C[-1])))

        # 子图 1: 反应物剩余无量纲浓度 a(t) (1 -> 0) (仅限定于 T1 到 T3 真实自发热时间段)
        ax1.plot(t_crop, a_exp, 'b-', lw=2.2, label='ARC 实验浓度 a (1→0)')
        ax1.plot(t_crop, a_cal, 'r--', lw=2.0, label='方程拟合浓度 a (1→0)')
        ax1.set_xlabel('时间 Time (s)', fontsize=11)
        ax1.set_ylabel('反应物浓度 a (-)', fontsize=11)
        ax1.set_title('反应物无量纲浓度 a 与时间关系 (1 → 0)', fontsize=12, fontweight='bold')
        ax1.set_ylim(-0.05, 1.05)
        ax1.grid(True, linestyle=':', alpha=0.6)
        ax1.legend(loc='best', fontsize=10, frameon=False)

        # 子图 2: 温度曲线 T(t) (包含 T1~T3 真实自发热升温段及延长因子覆盖的 T3 贴合平台期)
        ax2.plot(t_crop, T_exp_C, 'b-', lw=2.2, label='ARC 实验温度 (°C)')
        ax2.plot(t_crop, T_sim_C, 'r--', lw=2.0, label='方程拟合温度 (°C)')

        # 采用 * 号醒目标注 T1 起始自发热点与 T3 失控峰值点
        ax2.plot(pt_t1[0], pt_t1[1], '*', color='#ea580c', markersize=14, markeredgewidth=1.5,
                 label=f'T1 自发热点 ({pt_t1[1]:.1f}°C)', zorder=6)
        ax2.plot(pt_t3[0], pt_t3[1], '*', color='#dc2626', markersize=16, markeredgewidth=1.5,
                 label=f'T3 峰值点 ({pt_t3[1]:.1f}°C)', zorder=6)

        ext_factor = res.get("ext_factor", 1.0)
        ext_title_str = f" [延长因子×{ext_factor:g}]" if ext_factor > 1.0 else ""
        ax2.set_xlabel('时间 Time (s)', fontsize=11)
        ax2.set_ylabel('温度 Temperature (°C)', fontsize=11)
        ax2.set_title(f'热失控自发热温度演化 (T1={res["T1_C"]:.1f}°C → T3={res["T3_C"]:.1f}°C){ext_title_str}', fontsize=12, fontweight='bold')
        ax2.grid(True, linestyle=':', alpha=0.6)
        ax2.legend(loc='best', fontsize=9.5, frameon=False)

        m_line = f"• m = {res.get('m', 0.0):.4f}\n" if res.get("identify_m", False) else ""
        stats_box = (
            f"ARC 拟合参数:\n"
            f"• A = {res['A']:.4e} 1/s\n"
            f"• E = {res['Ea']:.2f} J/mol ({res['Ea']/1000:.2f} kJ/mol)\n"
            f"• n = {res['n']:.4f}\n"
            f"{m_line}"
            f"• H = {res['H']:.2f} J/g\n"
            f"• RMSE = {res['rmse']:.6e}\n"
            f"• 耗时 = {res['elapsed']:.2f} s"
        )
        ax2.text(
            0.96, 0.05, stats_box,
            transform=ax2.transAxes,
            fontsize=9.5,
            verticalalignment='bottom',
            horizontalalignment='right',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#f8fafc', edgecolor='#94a3b8', alpha=0.92, lw=1.2)
        )
        self.canvas.draw_idle()

    def import_arc_to_sim_table(self):
        if not self.last_arc_id_result:
            return
        res = self.last_arc_id_result
        # 组装为集总反应 lumped，只导入到前向仿真动力学表格中，完全不干扰 1D 辨识表格
        lumped_rxn = [{
            "name": "lumped",
            "A": res["A"],
            "Ea": res["Ea"],
            "H": res["H"],
            "n": res["n"],
            "m": res.get("m", 0.0),
            "c0": 1.0
        }]
        self._populate_sim_rxn_table(lumped_rxn)
        self.update_status(f"已将 ARC 辨识得到的结果导入为仿真动力学表格的单一 lumped 集总反应！")
        reply = QMessageBox.question(
            self, "导入成功",
            f"已将 ARC 辨识结果作为名为 'lumped' 的单一反应成功导入前向仿真动力学参数表中！\n"
            f"(注: 1D 热失控多步辨识参数表保持独立，未受任何影响)\n\n是否立即切换至【1D 热失控仿真计算】模式？",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
        )
        if reply == QMessageBox.Yes:
            self.sim_mode_combo.setCurrentIndex(0)

    def save_arc_params_txt(self):
        if not self.last_arc_id_result:
            return
        res = self.last_arc_id_result
        default_name = f"ARC_ParaIden_Result_{time.strftime('%Y%m%d_%H%M%S')}.txt"
        file_path, _ = QFileDialog.getSaveFileName(self, "保存辨识参数", default_name, "Text Files (*.txt);;All Files (*)")
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(f"# ARC 热失控动力学参数辨识结果 ({time.strftime('%Y-%m-%d %H:%M:%S')})\n")
                f.write(f"# T1 = {res['T1_C']:.2f} °C, T3 = {res['T3_C']:.2f} °C, ΔT = {res['deltaT']:.2f} K\n")
                f.write(f"A = {res['A']:.6e} 1/s\n")
                f.write(f"E = {res['Ea']:.6e} J/mol ({res['Ea']/1000:.2f} kJ/mol)\n")
                f.write(f"n = {res['n']:.6f}\n")
                if res.get("identify_m", False):
                    f.write(f"m = {res.get('m', 0.0):.6f}\n")
                f.write(f"H = {res['H']:.4f} J/g\n")
                f.write(f"RMSE = {res['rmse']:.6e}\n")
                f.write(f"耗时 = {res['elapsed']:.2f} s\n")
            self.update_status(f"ARC 辨识参数已保存至: {file_path}")
            QMessageBox.information(self, "保存成功", f"ARC 辨识参数已成功保存至:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存出错: {str(e)}")
            self.update_status(f"ARC 辨识参数已保存至: {file_path}")
            QMessageBox.information(self, "保存成功", f"ARC 辨识参数已成功保存至:\n{file_path}")
        except Exception as e:
            QMessageBox.critical(self, "保存失败", f"保存出错: {str(e)}")

    # --------------------------------------------------------------------------
    # 文件浏览与通用列更新辅助函数
    # --------------------------------------------------------------------------
    def browse_ident_file(self, target_type):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "选择实验对标数据文件" if target_type == "tr" else "选择 ARC 测试数据文件",
            "",
            "Data Files (*.xlsx *.xls *.csv);;Excel Files (*.xlsx *.xls);;CSV Files (*.csv)"
        )
        if not file_path:
            return

        if target_type == "tr":
            self.tr_exp_file_edit.setText(file_path)
            self.load_ident_sheets("tr", file_path)
        else:
            self.arc_file_edit.setText(file_path)
            self.load_ident_sheets("arc", file_path)

    def load_ident_sheets(self, target_type, file_path):
        combo = self.tr_sheet_combo if target_type == "tr" else self.arc_sheet_combo
        combo.blockSignals(True)
        combo.clear()
        try:
            if file_path.lower().endswith(('.xlsx', '.xls')):
                xl = pd.ExcelFile(file_path)
                sheets = xl.sheet_names
                combo.addItems(sheets)
                combo.blockSignals(False)
                self.load_ident_columns(target_type)
            else:
                combo.addItem("[CSV 无 Sheet]")
                combo.blockSignals(False)
                self.load_ident_columns(target_type)
        except Exception as e:
            combo.blockSignals(False)
            self.update_status(f"读取表格结构失败: {str(e)}")

    def load_ident_columns(self, target_type):
        is_tr = (target_type == "tr")
        file_path = self.tr_exp_file_edit.text().strip() if is_tr else self.arc_file_edit.text().strip()
        if not file_path or not os.path.exists(file_path):
            return

        sheet_combo = self.tr_sheet_combo if is_tr else self.arc_sheet_combo
        sheet = sheet_combo.currentText()

        try:
            if file_path.lower().endswith(('.xlsx', '.xls')):
                df = pd.read_excel(file_path, sheet_name=sheet)
            else:
                df = pd.read_csv(file_path)

            cols = [str(c) for c in df.columns]
            if is_tr:
                self.current_ident_df_tr = df
                self.tr_time_col_combo.clear()
                self.tr_temp_col_combo.clear()
                self.tr_amb_combo.clear()
                self.tr_amb_combo.addItem("Constant")

                self.tr_time_col_combo.addItems(cols)
                self.tr_temp_col_combo.addItems(cols)
                self.tr_amb_combo.addItems(cols)

                # 默认时间列选第 1 列，温度列选第 2 列
                if len(cols) >= 1:
                    self.tr_time_col_combo.setCurrentIndex(0)
                if len(cols) >= 2:
                    self.tr_temp_col_combo.setCurrentIndex(1)

                # 优先匹配更明确的列名
                for i, c in enumerate(cols):
                    cl = c.lower()
                    if cl in ['t', 'time', '时间', 't (s)', 'time (s)']:
                        self.tr_time_col_combo.setCurrentIndex(i)
                        break
                for i, c in enumerate(cols):
                    cl = c.lower()
                    if any(k in cl for k in ['tmean', 'mean', 'mid', 'center', 'temp', '温度']):
                        self.tr_temp_col_combo.setCurrentIndex(i)
                        break
            else:
                self.current_ident_df_arc = df
                self.arc_time_combo.clear()
                self.arc_temp_combo.clear()
                self.arc_time_combo.addItems(cols)
                self.arc_temp_combo.addItems(cols)

                # ARC 严格按要求：默认时间列选择读取的第一列，温度列为第二列
                if len(cols) >= 1:
                    self.arc_time_combo.setCurrentIndex(0)
                if len(cols) >= 2:
                    self.arc_temp_combo.setCurrentIndex(1)

                self.auto_fill_arc_temps()

            self.update_status(f"成功载入数据文件: {os.path.basename(file_path)} (共 {len(df)} 行, {len(cols)} 列)")
        except Exception as e:
            self.update_status(f"解析列名失败: {str(e)}")
