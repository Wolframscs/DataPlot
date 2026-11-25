# -*- coding: utf-8 -*-
"""
DataPlot_ecthermal_gui.py
Electro-Thermal Coupled Simulation GUI Mixin for DataPlot:
  - Integrates HGP_OP_Extend Cycle (all CC/DC/Rest/CMap/NegRef/CProfile/PProfile/CV modes + NASA NormModel)
  - Integrates FVM_Thermal_OP_TPower (3D cell-centered FVM transient thermal conduction)
  - Supports selectable 0D Lumped vs 3D FVM thermal model with bidirectional coupling
  - Supports standalone 3D FVM driven by external Power.xlsx table or constant power
  - Embedded Qt/Matplotlib 2x3 curves, 3D CAE solid/cutaway/6-surface views, time slider, Excel & VTK export
"""

import os
import time
import numpy as np
import pandas as pd

from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QGroupBox,
    QLabel, QLineEdit, QPushButton, QComboBox, QCheckBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
    QMessageBox, QSlider, QSpinBox
)
from PySide6.QtCore import Qt, QThread, Signal

from DataPlot_hgp_engine import (
    HGPConfig, inspect_hgp_workbook, run_hgp_simulation, save_hgp_results_to_excel
)
from DataPlot_normmodel_engine import NormalizedBatteryModel
from DataPlot_fvm3d_engine import (
    FVM3DConfig, FVM3DThermalStepper, HAS_SCIPY_SPARSE,
    load_power_table, run_fvm3d_standalone
)
from DataPlot_fvm3d_post import (
    render_3d_overview_on_fig, render_cae_3d_cutaway_on_fig,
    render_6_surfaces_on_fig, export_vtk_structured_grid
)


class ECThermalWorker(QThread):
    """Background worker thread for 0D/3D Electro-Thermal and Standalone 3D FVM simulations."""
    progress_signal = Signal(str)
    finished_signal = Signal(dict)
    error_signal = Signal(str)

    def __init__(self, job: dict):
        super().__init__()
        self.job = job
        self._cancelled = False

    def cancel(self):
        self._cancelled = True

    def _is_cancelled(self) -> bool:
        return self._cancelled

    def run(self):
        try:
            run_mode = self.job['run_mode']
            if run_mode == 'fvm3d_standalone':
                fvm_cfg = self.job['fvm_cfg']
                qh_type = self.job['qh_type']
                power_table = None
                if qh_type == 'table':
                    power_table = load_power_table(
                        self.job['power_file'],
                        sheet_name=self.job.get('power_sheet', 'Sheet1'),
                        time_col=self.job.get('power_tcol', 'Time'),
                        power_col=self.job.get('power_pcol', 'Power'),
                    )
                res = run_fvm3d_standalone(
                    fvm_cfg=fvm_cfg,
                    total_time=float(self.job['total_time']),
                    qh_type=qh_type,
                    qh_const_W=float(self.job.get('qh_const_W', 120.0)),
                    power_table=power_table,
                    adaptive_dt=bool(self.job.get('adaptive_dt', False)),
                    dt_initial=float(self.job.get('dt_initial', 1.0)),
                    progress_cb=self.progress_signal.emit,
                    cancel_check=self._is_cancelled,
                )
                self.finished_signal.emit(res)
            else:
                hgp_cfg: HGPConfig = self.job['hgp_cfg']
                cond_list = self.job['cond_list']
                input_path = self.job['input_path']

                norm_model = None
                if hgp_cfg.voltage_model == 'norm_model':
                    norm_path = self.job.get('norm_param_path', '')
                    if not norm_path or not os.path.exists(norm_path):
                        raise FileNotFoundError("选择 NASA 归一化模型时，请先指定有效的 HGP_NormModel_Params.xlsx 文件！")
                    norm_model = NormalizedBatteryModel(norm_path)

                thermal_stepper = None
                if self.job['thermal_dim'] == '3D':
                    thermal_stepper = FVM3DThermalStepper(self.job['fvm_cfg'])

                res = run_hgp_simulation(
                    input_path=input_path,
                    cond_list=cond_list,
                    cfg=hgp_cfg,
                    thermal_stepper=thermal_stepper,
                    norm_model=norm_model,
                    progress_cb=self.progress_signal.emit,
                    cancel_check=self._is_cancelled,
                )
                self.finished_signal.emit(res)
        except Exception as e:
            import traceback
            self.error_signal.emit(f"电-热耦合仿真运行出错:\n{str(e)}\n\n{traceback.format_exc()}")


class ECThermalMixin:
    """Mixin integrated into PlotterGUI for Mode 3: Electro-Thermal Coupled Simulation (0D/3D)."""

    def _build_ecthermal_panel(self) -> QWidget:
        self.ec_worker = None
        self.ec_last_result = None
        self.ec_cmap_options = [
            "jet", "turbo", "coolwarm", "inferno", "plasma", "viridis",
            "magma", "cividis", "hot", "afmhot", "RdYlBu_r", "Spectral_r",
            "rainbow", "seismic", "bwr", "YlOrRd", "Blues", "Greens"
        ]

        container = QWidget()
        main_lay = QVBoxLayout(container)
        main_lay.setContentsMargins(0, 0, 0, 0)
        main_lay.setSpacing(6)

        # ------------------------------------------------------------------
        # 0. Action Buttons
        # ------------------------------------------------------------------
        btn_grid = QGridLayout()
        btn_grid.setSpacing(6)

        self.ec_run_btn = QPushButton("开始电-热耦合仿真")
        self.ec_run_btn.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold; padding: 6px;")
        self.ec_run_btn.clicked.connect(self.start_ecthermal_sim)
        btn_grid.addWidget(self.ec_run_btn, 0, 0)

        self.ec_stop_btn = QPushButton("终止计算")
        self.ec_stop_btn.setEnabled(False)
        self.ec_stop_btn.setStyleSheet("background-color: #dc2626; color: white; font-weight: bold; padding: 6px;")
        self.ec_stop_btn.clicked.connect(self.stop_ecthermal_sim)
        btn_grid.addWidget(self.ec_stop_btn, 0, 1)

        self.ec_export_excel_btn = QPushButton("导出结果 (Excel)")
        self.ec_export_excel_btn.setEnabled(False)
        self.ec_export_excel_btn.setStyleSheet("background-color: #059669; color: white; font-weight: bold; padding: 5px;")
        self.ec_export_excel_btn.clicked.connect(self.export_ecthermal_excel)
        btn_grid.addWidget(self.ec_export_excel_btn, 1, 0)

        self.ec_export_vtk_btn = QPushButton("导出 3D 温度场 (VTK)")
        self.ec_export_vtk_btn.setEnabled(False)
        self.ec_export_vtk_btn.setStyleSheet("background-color: #7c3aed; color: white; font-weight: bold; padding: 5px;")
        self.ec_export_vtk_btn.clicked.connect(self.export_ecthermal_vtk)
        btn_grid.addWidget(self.ec_export_vtk_btn, 1, 1)

        main_lay.addLayout(btn_grid)

        # ------------------------------------------------------------------
        # 1. Model & Heat Source Selection
        # ------------------------------------------------------------------
        model_box = QGroupBox("模型架构与求解模式")
        mg = QGridLayout(model_box)
        mg.setContentsMargins(6, 6, 6, 6)
        mg.setHorizontalSpacing(6)
        mg.setVerticalSpacing(4)

        mg.addWidget(QLabel("产热驱动方式:"), 0, 0)
        self.ec_source_combo = QComboBox()
        self.ec_source_combo.addItems([
            "HGP 电-热耦合模型 (产热+电流+电压)",
            "外部产热表 / 恒定功率驱动 3D FVM"
        ])
        self.ec_source_combo.currentIndexChanged.connect(self._on_ec_ui_mode_changed)
        mg.addWidget(self.ec_source_combo, 0, 1, 1, 3)

        mg.addWidget(QLabel("热模型维度:"), 1, 0)
        self.ec_dim_combo = QComboBox()
        self.ec_dim_combo.addItems(["0D 集总热模型 (快速)", "3D FVM 瞬态导热模型 (空间场)"])
        self.ec_dim_combo.currentIndexChanged.connect(self._on_ec_ui_mode_changed)
        mg.addWidget(self.ec_dim_combo, 1, 1)

        mg.addWidget(QLabel("电压模型:"), 1, 2)
        self.ec_vmodel_combo = QComboBox()
        self.ec_vmodel_combo.addItems(["倍率-SOC 查表模型", "NASA 归一化模型 (含温变)"])
        self.ec_vmodel_combo.currentIndexChanged.connect(self._on_ec_ui_mode_changed)
        mg.addWidget(self.ec_vmodel_combo, 1, 3)

        mg.addWidget(QLabel("3D 线性求解器:"), 2, 0)
        self.ec_solver_combo = QComboBox()
        solver_items = ["纯 NumPy PCG (零新依赖)"]
        if HAS_SCIPY_SPARSE:
            solver_items.append("SciPy 稀疏直接求解 (缓存LU)")
        self.ec_solver_combo.addItems(solver_items)
        mg.addWidget(self.ec_solver_combo, 2, 1)

        mg.addWidget(QLabel("3D 反馈测点:"), 2, 2)
        self.ec_feedback_combo = QComboBox()
        self.ec_feedback_combo.addItems(["T_mean", "T_max", "T_core", "T_bottom_avg"])
        mg.addWidget(self.ec_feedback_combo, 2, 3)

        main_lay.addWidget(model_box)

        # ------------------------------------------------------------------
        # 2. Input Data Files
        # ------------------------------------------------------------------
        self.ec_data_box = QGroupBox("输入数据文件配置")
        dg = QGridLayout(self.ec_data_box)
        dg.setContentsMargins(6, 6, 6, 6)
        dg.setHorizontalSpacing(6)
        dg.setVerticalSpacing(4)

        self.ec_hgp_lbl = QLabel("HGP 输入表:")
        self.ec_hgp_file_edit = QLineEdit()
        self.ec_hgp_file_edit.setPlaceholderText("选择 HGP_Input_Data.xlsx")
        self.ec_hgp_browse_btn = QPushButton("导入")
        self.ec_hgp_browse_btn.setStyleSheet("background-color: #2563eb; color: white;")
        self.ec_hgp_browse_btn.clicked.connect(self._browse_hgp_input_file)
        dg.addWidget(self.ec_hgp_lbl, 0, 0)
        dg.addWidget(self.ec_hgp_file_edit, 0, 1, 1, 2)
        dg.addWidget(self.ec_hgp_browse_btn, 0, 3)

        self.ec_hgp_status_lbl = QLabel("未加载工作簿")
        self.ec_hgp_status_lbl.setStyleSheet("color: #475569; font-size: 11px;")
        dg.addWidget(self.ec_hgp_status_lbl, 1, 0, 1, 4)

        self.ec_norm_lbl = QLabel("归一化参数表:")
        self.ec_norm_file_edit = QLineEdit()
        self.ec_norm_file_edit.setPlaceholderText("选择 HGP_NormModel_Params.xlsx")
        self.ec_norm_browse_btn = QPushButton("导入")
        self.ec_norm_browse_btn.clicked.connect(self._browse_norm_param_file)
        dg.addWidget(self.ec_norm_lbl, 2, 0)
        dg.addWidget(self.ec_norm_file_edit, 2, 1, 1, 2)
        dg.addWidget(self.ec_norm_browse_btn, 2, 3)

        # Standalone external power table controls
        self.ec_pwr_widget = QWidget()
        pg = QGridLayout(self.ec_pwr_widget)
        pg.setContentsMargins(0, 2, 0, 2)
        pg.setHorizontalSpacing(6)
        pg.setVerticalSpacing(4)

        pg.addWidget(QLabel("热源类型:"), 0, 0)
        self.ec_ext_qtype_combo = QComboBox()
        self.ec_ext_qtype_combo.addItems(["功率表插值 (table)", "恒定总功率 (constant)"])
        pg.addWidget(self.ec_ext_qtype_combo, 0, 1)

        pg.addWidget(QLabel("总时长 (s):"), 0, 2)
        self.ec_ext_total_time_edit = QLineEdit("600.0")
        pg.addWidget(self.ec_ext_total_time_edit, 0, 3)

        pg.addWidget(QLabel("功率表文件:"), 1, 0)
        self.ec_pwr_file_edit = QLineEdit()
        self.ec_pwr_file_edit.setPlaceholderText("选择 Power.xlsx 或 .csv")
        self.ec_pwr_browse_btn = QPushButton("导入")
        self.ec_pwr_browse_btn.clicked.connect(self._browse_power_table_file)
        pg.addWidget(self.ec_pwr_file_edit, 1, 1, 1, 2)
        pg.addWidget(self.ec_pwr_browse_btn, 1, 3)

        pg.addWidget(QLabel("Sheet / 时间列:"), 2, 0)
        self.ec_pwr_sheet_edit = QLineEdit("Sheet1")
        self.ec_pwr_tcol_edit = QLineEdit("Time")
        pg.addWidget(self.ec_pwr_sheet_edit, 2, 1)
        pg.addWidget(self.ec_pwr_tcol_edit, 2, 2)
        self.ec_pwr_pcol_edit = QLineEdit("Power")
        self.ec_pwr_pcol_edit.setToolTip("功率列名")
        pg.addWidget(self.ec_pwr_pcol_edit, 2, 3)

        pg.addWidget(QLabel("恒定功率 (W):"), 3, 0)
        self.ec_ext_const_p_edit = QLineEdit("120.0")
        pg.addWidget(self.ec_ext_const_p_edit, 3, 1)
        self.ec_ext_adapt_chk = QCheckBox("启用自适应步长 (dt 0.1~20s)")
        self.ec_ext_adapt_chk.setChecked(False)
        pg.addWidget(self.ec_ext_adapt_chk, 3, 2, 1, 2)

        dg.addWidget(self.ec_pwr_widget, 3, 0, 1, 4)
        main_lay.addWidget(self.ec_data_box)

        # ------------------------------------------------------------------
        # 3. Multi-Stage / Multi-Cycle Condition Editor
        # ------------------------------------------------------------------
        self.ec_cond_box = QGroupBox("多阶段与多循环工况序列编辑器")
        self.ec_cond_box.setCheckable(True)
        self.ec_cond_box.setChecked(True)
        cond_content = QWidget()
        cond_lay = QVBoxLayout(cond_content)
        cond_lay.setContentsMargins(0, 4, 0, 0)
        cond_lay.setSpacing(4)

        preset_bar = QHBoxLayout()
        self.ec_preset_combo = QComboBox()
        self.ec_preset_combo.addItems([
            "CMap 快充 + 静置1h + 功率放电 (默认)",
            "恒负参防析锂快充 + 静置 + 1C 放电",
            "1C 恒流充 + CV 恒压 + 静置 + 1C 放电",
            "电流工况充 (CC_CProfile) + 功率工况放 (DC_PProfile)"
        ])
        preset_bar.addWidget(self.ec_preset_combo, 1)
        btn_load_preset = QPushButton("载入预设")
        btn_load_preset.clicked.connect(self._load_ec_cond_preset)
        preset_bar.addWidget(btn_load_preset)
        cond_lay.addLayout(preset_bar)

        tool_bar = QHBoxLayout()
        btn_add_stage = QPushButton("添加阶段")
        btn_add_stage.setStyleSheet("background-color: #2563eb; color: white;")
        btn_add_stage.clicked.connect(self._add_ec_stage_row)
        btn_del_stage = QPushButton("删除阶段")
        btn_del_stage.setStyleSheet("background-color: #dc2626; color: white;")
        btn_del_stage.clicked.connect(self._del_ec_stage_row)
        tool_bar.addWidget(btn_add_stage)
        tool_bar.addWidget(btn_del_stage)
        tool_bar.addWidget(QLabel("循环圈数 N:"))
        self.ec_ncycles_spin = QSpinBox()
        self.ec_ncycles_spin.setRange(1, 5000)
        self.ec_ncycles_spin.setValue(1)
        tool_bar.addWidget(self.ec_ncycles_spin)
        cond_lay.addLayout(tool_bar)

        self.ec_cond_table = QTableWidget(0, 7)
        self.ec_cond_table.setHorizontalHeaderLabels([
            "类别", "模式", "倍率(C)", "起始SOC", "截止SOC", "截止电压(V)", "时长(s)"
        ])
        self.ec_cond_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.ec_cond_table.verticalHeader().setDefaultSectionSize(24)
        self.ec_cond_table.setFixedHeight(130)
        cond_lay.addWidget(self.ec_cond_table)

        tip_lbl = QLabel("模式支持: CC / CV / CC_CMap / CC_NegRef / CC_CProfile / CC_PProfile / DC / DC_CProfile / DC_PProfile / Rest")
        tip_lbl.setStyleSheet("color: #475569; font-size: 11px;")
        tip_lbl.setWordWrap(True)
        cond_lay.addWidget(tip_lbl)

        cond_box_lay = QVBoxLayout(self.ec_cond_box)
        cond_box_lay.setContentsMargins(6, 2, 6, 6)
        cond_box_lay.addWidget(cond_content)
        self.ec_cond_box.toggled.connect(cond_content.setVisible)
        main_lay.addWidget(self.ec_cond_box)

        # Populate default preset
        self._load_ec_cond_preset()

        # ------------------------------------------------------------------
        # 4. Electrical & Aging Parameters
        # ------------------------------------------------------------------
        self.ec_elec_box = QGroupBox("电芯电参数与老化设置")
        self.ec_elec_box.setCheckable(True)
        self.ec_elec_box.setChecked(False)
        elec_content = QWidget()
        eg = QGridLayout(elec_content)
        eg.setContentsMargins(0, 4, 0, 4)
        eg.setHorizontalSpacing(6)
        eg.setVerticalSpacing(4)

        self.ec_elec_inputs = {}
        elec_fields = [
            ("speci_capac", "标称容量 (Ah):", "156.0", 0, 0),
            ("soc_init", "初始 SOC (0~1):", "0.0", 0, 2),
            ("step_dt", "电模型步长 dt (s):", "1.0", 1, 0),
            ("rest_dt", "静置步长 (s):", "10.0", 1, 2),
            ("pile_max_c", "最大充电倍率 (C):", "6.0", 2, 0),
            ("ref_min", "负参防析锂限 (mV):", "5.0", 2, 2),
            ("u_max", "默认充电截止 (V):", "3.65", 3, 0),
            ("u_min", "默认放电截止 (V):", "2.0", 3, 2),
            ("voltage_filter_weight", "电压弛豫权重 w:", "9.0", 4, 0),
            ("cv_i_cut_c", "CV 截止倍率 (C):", "0.05", 4, 2),
            ("soh_init", "初始 SOH:", "1.0", 5, 0),
            ("soh_fade_per_cycle", "SOH衰减/圈:", "0.0", 5, 2),
            ("r_growth_per_cycle", "内阻增长/圈:", "0.0", 6, 0),
            ("coeff_temp", "产热修正系数:", "1.0", 6, 2),
        ]
        for k, lbl_txt, def_v, r, c in elec_fields:
            eg.addWidget(QLabel(lbl_txt), r, c)
            ed = QLineEdit(def_v)
            self.ec_elec_inputs[k] = ed
            eg.addWidget(ed, r, c + 1)

        self.ec_ref_cal_chk = QCheckBox("计算负参电位 (REF_CAL)")
        self.ec_ref_cal_chk.setChecked(True)
        eg.addWidget(self.ec_ref_cal_chk, 7, 0, 1, 2)

        elec_box_lay = QVBoxLayout(self.ec_elec_box)
        elec_box_lay.setContentsMargins(6, 2, 6, 6)
        elec_box_lay.addWidget(elec_content)
        self.ec_elec_box.toggled.connect(elec_content.setVisible)
        elec_content.setVisible(False)
        main_lay.addWidget(self.ec_elec_box)

        # ------------------------------------------------------------------
        # 5. 0D Lumped Thermal Parameters
        # ------------------------------------------------------------------
        self.ec_0d_box = QGroupBox("0D 集总热模型与水冷滞回参数")
        self.ec_0d_box.setCheckable(True)
        self.ec_0d_box.setChecked(True)
        t0_content = QWidget()
        t0g = QGridLayout(t0_content)
        t0g.setContentsMargins(0, 4, 0, 4)
        t0g.setHorizontalSpacing(6)
        t0g.setVerticalSpacing(4)

        self.ec_0d_inputs = {}
        t0_fields = [
            ("cell_mass", "电芯质量 m (kg):", "2.6", 0, 0),
            ("cell_cp", "比热容 cp (J/kgK):", "1063.0", 0, 2),
            ("cell_l", "长度 L (m):", "0.058", 1, 0),
            ("cell_w", "宽度 W (m):", "0.080", 1, 2),
            ("cell_h", "高度 H (m):", "0.208", 2, 0),
            ("t_init", "初始/环境温度 (°C):", "25.0", 2, 2),
            ("h_liquid_on", "水冷开启 h_on:", "46.0", 3, 0),
            ("h_liquid_off", "水冷关闭 h_off:", "10.0", 3, 2),
            ("t_cool_on", "水冷开启阈值 (°C):", "35.0", 4, 0),
            ("t_cool_off", "水冷关闭阈值 (°C):", "30.0", 4, 2),
            ("t_liquid", "冷却液温度 (°C):", "20.0", 5, 0),
            ("cell_h_conv_air", "空冷对流 h_air:", "46.0", 5, 2),
        ]
        for k, lbl_txt, def_v, r, c in t0_fields:
            t0g.addWidget(QLabel(lbl_txt), r, c)
            ed = QLineEdit(def_v)
            self.ec_0d_inputs[k] = ed
            t0g.addWidget(ed, r, c + 1)

        self.ec_0d_liquid_chk = QCheckBox("启用底面水冷滞回 (35°C开 / 30°C关)")
        self.ec_0d_liquid_chk.setChecked(True)
        t0g.addWidget(self.ec_0d_liquid_chk, 6, 0, 1, 4)

        t0_box_lay = QVBoxLayout(self.ec_0d_box)
        t0_box_lay.setContentsMargins(6, 2, 6, 6)
        t0_box_lay.addWidget(t0_content)
        self.ec_0d_box.toggled.connect(t0_content.setVisible)
        main_lay.addWidget(self.ec_0d_box)

        # ------------------------------------------------------------------
        # 6. 3D FVM Mesh, Material & 6-Face Boundary Conditions
        # ------------------------------------------------------------------
        self.ec_3d_box = QGroupBox("3D FVM 几何网格、导热物性与六面边界条件")
        self.ec_3d_box.setCheckable(True)
        self.ec_3d_box.setChecked(True)
        t3_content = QWidget()
        t3_lay = QVBoxLayout(t3_content)
        t3_lay.setContentsMargins(0, 4, 0, 4)
        t3_lay.setSpacing(4)

        t3g = QGridLayout()
        t3g.setHorizontalSpacing(6)
        t3g.setVerticalSpacing(4)
        self.ec_3d_inputs = {}
        t3_fields = [
            ("lx", "x 长度 Lx (m):", "0.160", 0, 0),
            ("ly", "y 厚度 Ly (m):", "0.020", 0, 2),
            ("lz", "z 高度 Lz (m):", "0.200", 1, 0),
            ("t_init", "初始温度 (°C):", "25.0", 1, 2),
            ("nx", "网格 nx:", "20", 2, 0),
            ("ny", "网格 ny:", "10", 2, 2),
            ("nz", "网格 nz:", "20", 3, 0),
            ("dt_th", "3D 热宏步长 dt_th(s):", "5.0", 3, 2),
            ("kx", "kx (W/mK):", "25.0", 4, 0),
            ("ky", "ky (W/mK):", "0.99", 4, 2),
            ("kz", "kz (W/mK):", "25.0", 5, 0),
            ("rho", "密度 ρ (kg/m3):", "2500.0", 5, 2),
            ("cp", "比热容 cp (J/kgK):", "1080.0", 6, 0),
            ("theta", "θ (0.5=CN, 1=BE):", "0.5", 6, 2),
            ("snapshot_interval", "3D 快照间隔 (s):", "60.0", 7, 0),
            ("h_liquid_on", "底面水冷开 h_on:", "300.0", 7, 2),
        ]
        for k, lbl_txt, def_v, r, c in t3_fields:
            t3g.addWidget(QLabel(lbl_txt), r, c)
            ed = QLineEdit(def_v)
            self.ec_3d_inputs[k] = ed
            t3g.addWidget(ed, r, c + 1)

        self.ec_3d_hyst_chk = QCheckBox("3D 底面启用水冷滞回 (35/30°C)")
        self.ec_3d_hyst_chk.setChecked(False)
        t3g.addWidget(self.ec_3d_hyst_chk, 8, 0, 1, 2)
        t3g.addWidget(QLabel("滞回测点:"), 8, 2)
        self.ec_3d_cool_sensor_combo = QComboBox()
        self.ec_3d_cool_sensor_combo.addItems(["T_max", "T_mean", "T_bottom_avg"])
        t3g.addWidget(self.ec_3d_cool_sensor_combo, 8, 3)
        t3_lay.addLayout(t3g)

        # 6-Face Boundary Table
        self.ec_bc_table = QTableWidget(6, 4)
        self.ec_bc_table.setHorizontalHeaderLabels(["表面", "边界类型", "h / T_wall / q", "T_inf (°C)"])
        self.ec_bc_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.ec_bc_table.verticalHeader().setDefaultSectionSize(23)
        self.ec_bc_table.setFixedHeight(168)
        default_bcs = [
            ("bottom", "convection", "300.0", "25.0"),
            ("top",    "convection", "5.0",   "25.0"),
            ("left",   "convection", "5.0",   "25.0"),
            ("right",  "convection", "5.0",   "25.0"),
            ("front",  "convection", "5.0",   "25.0"),
            ("back",   "convection", "5.0",   "25.0"),
        ]
        for row, (fname, btype, p1, p2) in enumerate(default_bcs):
            item_name = QTableWidgetItem(fname)
            item_name.setFlags(item_name.flags() & ~Qt.ItemIsEditable)
            self.ec_bc_table.setItem(row, 0, item_name)
            cb = QComboBox()
            cb.addItems(["convection", "temperature", "heat_flux", "adiabatic"])
            cb.setCurrentText(btype)
            self.ec_bc_table.setCellWidget(row, 1, cb)
            self.ec_bc_table.setItem(row, 2, QTableWidgetItem(p1))
            self.ec_bc_table.setItem(row, 3, QTableWidgetItem(p2))
        t3_lay.addWidget(self.ec_bc_table)

        t3_box_lay = QVBoxLayout(self.ec_3d_box)
        t3_box_lay.setContentsMargins(6, 2, 6, 6)
        t3_box_lay.addWidget(t3_content)
        self.ec_3d_box.toggled.connect(t3_content.setVisible)
        main_lay.addWidget(self.ec_3d_box)

        # ------------------------------------------------------------------
        # 7. Postprocessing View & 3D Snapshot Controls
        # ------------------------------------------------------------------
        post_box = QGroupBox("电-热耦合后处理绘图")
        pg_lay = QGridLayout(post_box)
        pg_lay.setContentsMargins(6, 6, 6, 6)
        pg_lay.setHorizontalSpacing(6)
        pg_lay.setVerticalSpacing(4)

        pg_lay.addWidget(QLabel("当前视图:"), 0, 0)
        self.ec_view_combo = QComboBox()
        self.ec_view_combo.addItems([
            "电-热综合 2x3 曲线图 (SOC/I/V/P/HGP/T)",
            "产热功率分解图 (不可逆/可逆/总产热)",
            "3D 温度统计与实体云图总览",
            "3D 实体与内部正交剖切三联图 (CAE)",
            "3D 六表面温度展开云图 (2x3)",
            "3D 温差演化曲线 (T_max - T_min)"
        ])
        self.ec_view_combo.currentIndexChanged.connect(self.render_ecthermal_view)
        pg_lay.addWidget(self.ec_view_combo, 0, 1, 1, 3)

        pg_lay.addWidget(QLabel("云图色谱 Map:"), 1, 0)
        self.ec_cmap_combo = QComboBox()
        self.ec_cmap_combo.addItems(self.ec_cmap_options)
        self.ec_cmap_combo.setCurrentText("jet")
        self.ec_cmap_combo.currentIndexChanged.connect(self.render_ecthermal_view)
        pg_lay.addWidget(self.ec_cmap_combo, 1, 1)

        self.ec_snap_lbl = QLabel("3D 时刻: -")
        pg_lay.addWidget(self.ec_snap_lbl, 1, 2, 1, 2)

        self.ec_snap_slider = QSlider(Qt.Horizontal)
        self.ec_snap_slider.setRange(0, 0)
        self.ec_snap_slider.setEnabled(False)
        self.ec_snap_slider.valueChanged.connect(self._on_ec_snap_slider_changed)
        pg_lay.addWidget(self.ec_snap_slider, 2, 0, 1, 4)

        main_lay.addWidget(post_box)

        self._on_ec_ui_mode_changed()
        return container

    # ------------------------------------------------------------------
    # UI Mode Visibility & File Browsers
    # ------------------------------------------------------------------
    def _on_ec_ui_mode_changed(self):
        is_ext_power = (self.ec_source_combo.currentIndex() == 1)
        if is_ext_power:
            self.ec_dim_combo.setCurrentIndex(1)
            self.ec_dim_combo.setEnabled(False)
        else:
            self.ec_dim_combo.setEnabled(True)

        is_3d = (self.ec_dim_combo.currentIndex() == 1)
        is_norm = (self.ec_vmodel_combo.currentIndex() == 1) and (not is_ext_power)

        self.ec_vmodel_combo.setEnabled(not is_ext_power)
        self.ec_solver_combo.setEnabled(is_3d)
        self.ec_feedback_combo.setEnabled(is_3d and (not is_ext_power))

        self.ec_hgp_lbl.setVisible(not is_ext_power)
        self.ec_hgp_file_edit.setVisible(not is_ext_power)
        self.ec_hgp_browse_btn.setVisible(not is_ext_power)
        self.ec_hgp_status_lbl.setVisible(not is_ext_power)

        self.ec_norm_lbl.setVisible(is_norm)
        self.ec_norm_file_edit.setVisible(is_norm)
        self.ec_norm_browse_btn.setVisible(is_norm)

        self.ec_pwr_widget.setVisible(is_ext_power)
        self.ec_cond_box.setVisible(not is_ext_power)
        self.ec_elec_box.setVisible(not is_ext_power)
        self.ec_0d_box.setVisible((not is_ext_power) and (not is_3d))
        self.ec_3d_box.setVisible(is_3d)

    def _browse_hgp_input_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 HGP 输入数据文件", "", "Excel Files (*.xlsx *.xls);;All Files (*)")
        if not path:
            return
        self.ec_hgp_file_edit.setText(path)
        try:
            info = inspect_hgp_workbook(path)
            found = [k.replace('has_', '').upper() for k, v in info.items() if k.startswith('has_') and v]
            self.ec_hgp_status_lbl.setText(f"已识别工作表: {', '.join(found)}")
            self.update_status(f"[HGP] 已加载工作簿 {os.path.basename(path)}，包含: {', '.join(found)}")
        except Exception as e:
            self.ec_hgp_status_lbl.setText(f"解析失败: {e}")

    def _browse_norm_param_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 NASA 归一化模型参数表", "", "Excel Files (*.xlsx *.xls);;All Files (*)")
        if path:
            self.ec_norm_file_edit.setText(path)

    def _browse_power_table_file(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择外部产热功率表", "", "Data Files (*.xlsx *.xls *.csv);;All Files (*)")
        if path:
            self.ec_pwr_file_edit.setText(path)

    # ------------------------------------------------------------------
    # Condition Sequence Table Helpers
    # ------------------------------------------------------------------
    def _set_ec_stage_rows(self, rows_data):
        self.ec_cond_table.setRowCount(0)
        for cat, mode, crate, soc0, soc1, ulim, dur in rows_data:
            r = self.ec_cond_table.rowCount()
            self.ec_cond_table.insertRow(r)
            cb_cat = QComboBox()
            cb_cat.addItems(["CC", "DC", "Rest"])
            cb_cat.setCurrentText(cat)
            self.ec_cond_table.setCellWidget(r, 0, cb_cat)

            cb_mode = QComboBox()
            cb_mode.addItems([
                "CC_CMap", "CC_NegRef", "CC", "CV", "CC_CProfile", "CC_PProfile",
                "DC", "DC_CProfile", "DC_PProfile", "Rest"
            ])
            cb_mode.setCurrentText(mode)
            self.ec_cond_table.setCellWidget(r, 1, cb_mode)

            for c_idx, val in enumerate([crate, soc0, soc1, ulim, dur], start=2):
                self.ec_cond_table.setItem(r, c_idx, QTableWidgetItem(str(val)))

    def _load_ec_cond_preset(self):
        idx = self.ec_preset_combo.currentIndex()
        if idx == 0:
            rows = [
                ("CC",   "CC_CMap",     "",    "", "1.0", "3.65", ""),
                ("Rest", "Rest",        "",    "", "",    "",     "3600"),
                ("DC",   "DC_PProfile", "",    "", "",    "2.0",  ""),
            ]
        elif idx == 1:
            rows = [
                ("CC",   "CC_NegRef",   "",    "", "0.9", "3.65", ""),
                ("Rest", "Rest",        "",    "", "",    "",     "1800"),
                ("DC",   "DC",          "1.0", "", "0.1", "2.0",  ""),
            ]
        elif idx == 2:
            rows = [
                ("CC",   "CC",          "1.0", "", "1.0", "3.65", ""),
                ("CC",   "CV",          "",    "", "1.0", "3.65", "3600"),
                ("Rest", "Rest",        "",    "", "",    "",     "1800"),
                ("DC",   "DC",          "1.0", "", "0.0", "2.0",  ""),
            ]
        else:
            rows = [
                ("CC",   "CC_CProfile", "",    "", "0.95", "3.65", ""),
                ("Rest", "Rest",        "",    "", "",     "",     "600"),
                ("DC",   "DC_PProfile", "",    "", "",     "2.0",  ""),
            ]
        self._set_ec_stage_rows(rows)

    def _add_ec_stage_row(self):
        r = self.ec_cond_table.rowCount()
        self.ec_cond_table.insertRow(r)
        cb_cat = QComboBox()
        cb_cat.addItems(["CC", "DC", "Rest"])
        self.ec_cond_table.setCellWidget(r, 0, cb_cat)
        cb_mode = QComboBox()
        cb_mode.addItems([
            "CC", "CV", "CC_CMap", "CC_NegRef", "CC_CProfile", "CC_PProfile",
            "DC", "DC_CProfile", "DC_PProfile", "Rest"
        ])
        self.ec_cond_table.setCellWidget(r, 1, cb_mode)
        for c_idx, val in enumerate(["1.0", "", "0.9", "3.65", ""], start=2):
            self.ec_cond_table.setItem(r, c_idx, QTableWidgetItem(val))

    def _del_ec_stage_row(self):
        cnt = self.ec_cond_table.rowCount()
        if cnt <= 1:
            return
        row = self.ec_cond_table.currentRow()
        if row < 0:
            row = cnt - 1
        self.ec_cond_table.removeRow(row)

    def _collect_cond_list(self):
        stages = []
        for r in range(self.ec_cond_table.rowCount()):
            cat = self.ec_cond_table.cellWidget(r, 0).currentText().strip()
            mode = self.ec_cond_table.cellWidget(r, 1).currentText().strip()
            def _cell(c):
                it = self.ec_cond_table.item(r, c)
                return it.text().strip() if it else ""

            crate_s, soc0_s, soc1_s, ulim_s, dur_s = _cell(2), _cell(3), _cell(4), _cell(5), _cell(6)
            if cat == "Rest" or mode == "Rest":
                stages.append({'Rest_Cond': float(dur_s or 600.0)})
            elif cat == "CC":
                d = {'CC_Cond': mode}
                if mode == 'CC':
                    d['C_rate'] = float(crate_s or 1.0)
                if soc0_s:
                    d['SOC_Init'] = float(soc0_s)
                if soc1_s:
                    d['SOC'] = float(soc1_s)
                if ulim_s:
                    d['U_Max'] = float(ulim_s)
                if dur_s:
                    d['time'] = float(dur_s)
                stages.append(d)
            else:
                d = {'DC_Cond': mode}
                if mode == 'DC':
                    d['C_rate'] = float(crate_s or 1.0)
                if soc0_s:
                    d['SOC_Init'] = float(soc0_s)
                if soc1_s:
                    d['SOC'] = float(soc1_s)
                if ulim_s:
                    d['U_Min'] = float(ulim_s)
                if dur_s:
                    d['time'] = float(dur_s)
                stages.append(d)
        return stages * int(self.ec_ncycles_spin.value())

    def _collect_fvm3d_cfg(self) -> FVM3DConfig:
        bcs = {}
        for r in range(6):
            fname = self.ec_bc_table.item(r, 0).text().strip()
            btype = self.ec_bc_table.cellWidget(r, 1).currentText().strip()
            p1 = float(self.ec_bc_table.item(r, 2).text().strip() or "0.0")
            p2 = float(self.ec_bc_table.item(r, 3).text().strip() or "25.0")
            if btype == 'convection':
                bcs[fname] = {'type': 'convection', 'h': p1, 'T_inf': p2}
            elif btype == 'temperature':
                bcs[fname] = {'type': 'temperature', 'T_wall': p1}
            elif btype == 'heat_flux':
                bcs[fname] = {'type': 'heat_flux', 'q_flux': p1}
            else:
                bcs[fname] = {'type': 'adiabatic'}

        def _f(k, default):
            return float(self.ec_3d_inputs[k].text().strip() or default)

        solver_mode = 'scipy_direct' if (self.ec_solver_combo.currentIndex() == 1 and HAS_SCIPY_SPARSE) else 'numpy_pcg'
        t_init_v = _f("t_init", "25.0")
        return FVM3DConfig(
            lx=_f("lx", "0.16"), ly=_f("ly", "0.02"), lz=_f("lz", "0.20"),
            nx=max(1, int(round(_f("nx", "20")))),
            ny=max(1, int(round(_f("ny", "10")))),
            nz=max(1, int(round(_f("nz", "20")))),
            kx=_f("kx", "25.0"), ky=_f("ky", "0.99"), kz=_f("kz", "25.0"),
            rho=_f("rho", "2500.0"), cp=_f("cp", "1080.0"),
            theta=_f("theta", "0.5"), dt_th=_f("dt_th", "5.0"),
            solver_mode=solver_mode,
            feedback_sensor=self.ec_feedback_combo.currentText(),
            cooling_sensor=self.ec_3d_cool_sensor_combo.currentText(),
            bottom_liquid_hysteresis=self.ec_3d_hyst_chk.isChecked(),
            h_liquid_on=_f("h_liquid_on", "300.0"),
            h_liquid_off=float(self.ec_0d_inputs["h_liquid_off"].text().strip() or "10.0"),
            t_cool_on=float(self.ec_0d_inputs["t_cool_on"].text().strip() or "35.0"),
            t_cool_off=float(self.ec_0d_inputs["t_cool_off"].text().strip() or "30.0"),
            t_liquid=float(self.ec_0d_inputs["t_liquid"].text().strip() or "20.0"),
            t_init=t_init_v, t_amb=t_init_v,
            coeff_temp=float(self.ec_elec_inputs["coeff_temp"].text().strip() or "1.0"),
            snapshot_interval=_f("snapshot_interval", "60.0"),
            boundary_conditions=bcs,
        )

    # ------------------------------------------------------------------
    # Start / Stop / Finish Handlers
    # ------------------------------------------------------------------
    def start_ecthermal_sim(self):
        try:
            is_ext_power = (self.ec_source_combo.currentIndex() == 1)
            if is_ext_power:
                qh_type = 'table' if self.ec_ext_qtype_combo.currentIndex() == 0 else 'constant'
                pwr_file = self.ec_pwr_file_edit.text().strip()
                if qh_type == 'table' and (not pwr_file or not os.path.exists(pwr_file)):
                    QMessageBox.warning(self, "提示", "请先选择有效的外部产热功率表文件 (Power.xlsx 或 .csv)！")
                    return
                job = {
                    'run_mode': 'fvm3d_standalone',
                    'fvm_cfg': self._collect_fvm3d_cfg(),
                    'qh_type': qh_type,
                    'power_file': pwr_file,
                    'power_sheet': self.ec_pwr_sheet_edit.text().strip() or 'Sheet1',
                    'power_tcol': self.ec_pwr_tcol_edit.text().strip() or 'Time',
                    'power_pcol': self.ec_pwr_pcol_edit.text().strip() or 'Power',
                    'qh_const_W': float(self.ec_ext_const_p_edit.text().strip() or "120.0"),
                    'total_time': float(self.ec_ext_total_time_edit.text().strip() or "600.0"),
                    'adaptive_dt': self.ec_ext_adapt_chk.isChecked(),
                    'dt_initial': 0.1 if self.ec_ext_adapt_chk.isChecked() else float(self.ec_3d_inputs["dt_th"].text().strip() or "5.0"),
                }
            else:
                in_path = self.ec_hgp_file_edit.text().strip()
                if not in_path or not os.path.exists(in_path):
                    QMessageBox.warning(self, "提示", "请先导入有效的 HGP 输入数据文件 (HGP_Input_Data.xlsx)！")
                    return
                def _ef(k, d): return float(self.ec_elec_inputs[k].text().strip() or d)
                def _tf(k, d): return float(self.ec_0d_inputs[k].text().strip() or d)
                t_init_v = _tf("t_init", "25.0")
                hgp_cfg = HGPConfig(
                    ref_cal=self.ec_ref_cal_chk.isChecked(),
                    speci_capac=_ef("speci_capac", "156.0"),
                    soc_init=_ef("soc_init", "0.0"),
                    step_dt=_ef("step_dt", "1.0"),
                    rest_dt=_ef("rest_dt", "10.0"),
                    pile_max_c=_ef("pile_max_c", "6.0"),
                    ref_min=_ef("ref_min", "5.0"),
                    u_max=_ef("u_max", "3.65"),
                    u_min=_ef("u_min", "2.0"),
                    cv_i_cut_c=_ef("cv_i_cut_c", "0.05"),
                    voltage_filter_weight=_ef("voltage_filter_weight", "9.0"),
                    soh_init=_ef("soh_init", "1.0"),
                    soh_fade_per_cycle=_ef("soh_fade_per_cycle", "0.0"),
                    r_growth_per_cycle=_ef("r_growth_per_cycle", "0.0"),
                    coeff_temp=_ef("coeff_temp", "1.0"),
                    voltage_model='norm_model' if self.ec_vmodel_combo.currentIndex() == 1 else 'lookup',
                    t_init=t_init_v, t_amb=t_init_v,
                    cooling_liquid=self.ec_0d_liquid_chk.isChecked(),
                    t_cool_on=_tf("t_cool_on", "35.0"),
                    t_cool_off=_tf("t_cool_off", "30.0"),
                    h_liquid_on=_tf("h_liquid_on", "46.0"),
                    h_liquid_off=_tf("h_liquid_off", "10.0"),
                    t_liquid=_tf("t_liquid", "20.0"),
                    cell_h_conv_air=_tf("cell_h_conv_air", "46.0"),
                    cell_l=_tf("cell_l", "0.058"),
                    cell_w=_tf("cell_w", "0.080"),
                    cell_h=_tf("cell_h", "0.208"),
                    cell_mass=_tf("cell_mass", "2.6"),
                    cell_cp=_tf("cell_cp", "1063.0"),
                )
                thermal_dim = '3D' if self.ec_dim_combo.currentIndex() == 1 else '0D'
                job = {
                    'run_mode': 'hgp',
                    'input_path': in_path,
                    'cond_list': self._collect_cond_list(),
                    'hgp_cfg': hgp_cfg,
                    'thermal_dim': thermal_dim,
                    'fvm_cfg': self._collect_fvm3d_cfg() if thermal_dim == '3D' else None,
                    'norm_param_path': self.ec_norm_file_edit.text().strip(),
                }
        except Exception as e:
            QMessageBox.critical(self, "参数错误", f"请检查输入参数格式:\n{str(e)}")
            return

        if self.ec_worker and self.ec_worker.isRunning():
            self.ec_worker.cancel()
            self.ec_worker.wait(200)

        self.ec_run_btn.setEnabled(False)
        self.ec_stop_btn.setEnabled(True)
        self.ec_export_excel_btn.setEnabled(False)
        self.ec_export_vtk_btn.setEnabled(False)

        self.update_status("=" * 50)
        self.update_status(f"[启动] 开始电-热耦合仿真 (模式={job['run_mode']}, 维度={job.get('thermal_dim', '3D')})...")
        self.update_status("=" * 50)

        self.ec_worker = ECThermalWorker(job)
        self.ec_worker.progress_signal.connect(self.update_status)
        self.ec_worker.finished_signal.connect(self._on_ecthermal_finished)
        self.ec_worker.error_signal.connect(self._on_ecthermal_error)
        self.ec_worker.finished.connect(self._on_ecthermal_thread_done)
        self.ec_worker.start()

    def stop_ecthermal_sim(self):
        if self.ec_worker and self.ec_worker.isRunning():
            self.ec_stop_btn.setEnabled(False)
            self.ec_worker.cancel()
            self.update_status("[中止] 正在请求终止电-热耦合仿真...")

    def _on_ecthermal_thread_done(self):
        self.ec_run_btn.setEnabled(True)
        self.ec_stop_btn.setEnabled(False)

    def _on_ecthermal_error(self, err_msg: str):
        self.update_status(err_msg)
        self._on_ecthermal_thread_done()
        QMessageBox.critical(self, "仿真失败", err_msg)

    def _on_ecthermal_finished(self, res: dict):
        self.ec_last_result = res
        self._on_ecthermal_thread_done()
        self.ec_export_excel_btn.setEnabled(True)

        thermal = res.get('thermal', {})
        has_3d = (thermal.get('thermal_dim') == '3D')
        self.ec_export_vtk_btn.setEnabled(has_3d)

        if has_3d and thermal.get('t_snapshots'):
            n_snaps = len(thermal['t_snapshots'])
            self.ec_snap_slider.blockSignals(True)
            self.ec_snap_slider.setRange(0, max(0, n_snaps - 1))
            self.ec_snap_slider.setValue(max(0, n_snaps - 1))
            self.ec_snap_slider.setEnabled(n_snaps > 1)
            self.ec_snap_slider.blockSignals(False)
            self.ec_snap_lbl.setText(f"3D 时刻: {thermal['t_snapshots'][-1]:.1f} s ({n_snaps}帧)")
        else:
            self.ec_snap_slider.setEnabled(False)
            self.ec_snap_lbl.setText("3D 时刻: -")

        # Auto-select sensible default view
        if res['mode'] == 'fvm3d_standalone':
            self.ec_view_combo.setCurrentIndex(2)
        else:
            self.ec_view_combo.setCurrentIndex(0)
        self.render_ecthermal_view()

    def _on_ec_snap_slider_changed(self, idx: int):
        if not self.ec_last_result:
            return
        thermal = self.ec_last_result.get('thermal', {})
        t_snaps = thermal.get('t_snapshots', [])
        if 0 <= idx < len(t_snaps):
            self.ec_snap_lbl.setText(f"3D 时刻: {t_snaps[idx]:.1f} s ({idx+1}/{len(t_snaps)})")
        if self.ec_view_combo.currentIndex() in (2, 3, 4):
            self.render_ecthermal_view()

    # ------------------------------------------------------------------
    # Visualization
    # ------------------------------------------------------------------
    def render_ecthermal_view(self):
        if not self.ec_last_result:
            return
        res = self.ec_last_result
        view_idx = self.ec_view_combo.currentIndex()
        cmap_name = self.ec_cmap_combo.currentText()
        thermal = res.get('thermal', {})
        has_3d = (thermal.get('thermal_dim') == '3D')
        snap_idx = self.ec_snap_slider.value() if self.ec_snap_slider.isEnabled() else -1

        if view_idx in (2, 3, 4, 5) and not has_3d:
            self.fig.clf()
            ax = self.fig.add_subplot(111)
            ax.text(0.5, 0.5, "当前结果为 0D 集总模型，请选择 3D FVM 模型以查看空间云图", ha='center', va='center', fontsize=12)
            ax.axis('off')
            self.canvas.draw_idle()
            return

        if view_idx in (0, 1) and res['mode'] == 'fvm3d_standalone':
            view_idx = 2

        if view_idx == 0:
            self._plot_hgp_2x3_overview(res['out_df'], thermal if has_3d else None)
        elif view_idx == 1:
            self._plot_hgp_heat_breakdown(res['out_df'])
        elif view_idx == 2:
            render_3d_overview_on_fig(self.fig, thermal, snap_idx=snap_idx, cmap_name=cmap_name)
        elif view_idx == 3:
            render_cae_3d_cutaway_on_fig(self.fig, thermal, snap_idx=snap_idx, cmap_name=cmap_name)
        elif view_idx == 4:
            render_6_surfaces_on_fig(self.fig, thermal, snap_idx=snap_idx, cmap_name=cmap_name)
        elif view_idx == 5:
            self._plot_3d_deltat_curve(thermal)

        self.canvas.draw_idle()

    def _plot_hgp_2x3_overview(self, out_df: pd.DataFrame, thermal_3d=None):
        self.fig.clf()
        if out_df is None or out_df.empty:
            return
        plot_df = out_df.iloc[::max(1, len(out_df) // 60000)] if len(out_df) > 60000 else out_df
        t = plot_df['time'].values
        dt_full = np.diff(out_df['time'].values)
        p_full = out_df['Power'].values
        q_full = out_df['HGP_dUdT'].values
        w_tot = float(np.sum(((np.abs(p_full[1:]) + np.abs(p_full[:-1])) / 2.0) * dt_full)) if len(dt_full) else 0.0
        q_tot = float(np.sum(((q_full[1:] + q_full[:-1]) / 2.0) * dt_full)) if len(dt_full) else 0.0
        qw_ratio = (q_tot / w_tot * 100.0) if w_tot != 0.0 else 0.0

        self.fig.suptitle('电芯电-热耦合多阶段仿真综合结果', fontsize=13, fontweight='bold')

        # 1. SOC & C-rate
        ax1 = self.fig.add_subplot(2, 3, 1)
        l1 = ax1.plot(t, plot_df['SOC'].values * 100.0, color='#2563eb', lw=1.8, label='SOC (%)')[0]
        ax1.set_xlabel('Time (s)', fontsize=9)
        ax1.set_ylabel('SOC (%)', fontsize=9)
        ax1.grid(True, linestyle='--', alpha=0.5)
        ax1r = ax1.twinx()
        l2 = ax1r.plot(t, plot_df['Rate'].values, color='#7c3aed', linestyle=':', lw=1.5, label='C-rate')[0]
        ax1r.set_ylabel('C-rate (C)', fontsize=9, color='#7c3aed')
        ax1.legend([l1, l2], ['SOC', 'C-rate'], loc='best', fontsize=8, frameon=False)
        ax1.set_title('(a) SOC 与充放电倍率', fontsize=10)

        # 2. Current
        ax2 = self.fig.add_subplot(2, 3, 2)
        ax2.plot(t, plot_df['Current'].values, color='#059669', lw=1.8)
        ax2.set_xlabel('Time (s)', fontsize=9)
        ax2.set_ylabel('Current (A)', fontsize=9)
        ax2.grid(True, linestyle='--', alpha=0.5)
        ax2.set_title('(b) 电流曲线', fontsize=10)

        # 3. Voltage, OCV & NegRef
        ax3 = self.fig.add_subplot(2, 3, 3)
        lv1 = ax3.plot(t, plot_df['Voltage'].values, color='#7c3aed', lw=1.8, label='端电压 U')[0]
        lv2 = ax3.plot(t, plot_df['OCV'].values, color='#0f172a', linestyle='-.', lw=1.4, label='OCV')[0]
        lines_v = [lv1, lv2]
        if 'Voltage_Test' in plot_df.columns and not plot_df['Voltage_Test'].isna().all():
            lines_v.append(ax3.plot(t, plot_df['Voltage_Test'].values, color='#f59e0b', linestyle=':', lw=1.4, label='实测电压')[0])
        ax3.set_xlabel('Time (s)', fontsize=9)
        ax3.set_ylabel('Voltage (V)', fontsize=9)
        ax3.grid(True, linestyle='--', alpha=0.5)
        if 'REF_Voltage' in plot_df.columns and not plot_df['REF_Voltage'].isna().all():
            ax3r = ax3.twinx()
            lines_v.append(ax3r.plot(t, plot_df['REF_Voltage'].values, color='#9a3412', linestyle='--', lw=1.3, label='负参 (mV)')[0])
            ax3r.set_ylabel('NegRef (mV)', fontsize=9, color='#9a3412')
        ax3.legend(lines_v, [l.get_label() for l in lines_v], loc='best', fontsize=7.5, frameon=False)
        ax3.set_title('(c) 端电压、OCV 与负参电位', fontsize=10)

        # 4. Electrical Power
        ax4 = self.fig.add_subplot(2, 3, 4)
        ax4.plot(t, plot_df['Power'].values, color='#65a30d', lw=1.8)
        ax4.set_xlabel('Time (s)', fontsize=9)
        ax4.set_ylabel('Power (W)', fontsize=9)
        ax4.grid(True, linestyle='--', alpha=0.5)
        ax4.set_title('(d) 电功率曲线', fontsize=10)

        # 5. Heat Generation Power
        ax5 = self.fig.add_subplot(2, 3, 5)
        ax5.plot(t, plot_df['HGP_dUdT'].values, color='#dc2626', lw=1.8, label=f'总产热 (Q/W={qw_ratio:.2f}%)')
        ax5.plot(t, plot_df['HGP'].values, color='#64748b', linestyle=':', lw=1.5, label='不可逆产热 Q_irr')
        ax5.set_xlabel('Time (s)', fontsize=9)
        ax5.set_ylabel('Heat Power (W)', fontsize=9)
        ax5.legend(loc='best', fontsize=8, frameon=False)
        ax5.grid(True, linestyle='--', alpha=0.5)
        ax5.set_title('(e) 产热功率演化', fontsize=10)

        # 6. Temperature & Cooling State
        ax6 = self.fig.add_subplot(2, 3, 6)
        lines_t = []
        if thermal_3d is not None:
            t3 = thermal_3d['time_history']
            st = thermal_3d['stats']
            lines_t.append(ax6.plot(t3, st['max'], color='#dc2626', lw=1.8, label=f"3D T_max ({max(st['max']):.1f}°C)")[0])
            lines_t.append(ax6.plot(t3, st['avg'], color='#0284c7', lw=1.8, label=f"3D T_avg ({np.mean(st['avg']):.1f}°C)")[0])
            lines_t.append(ax6.plot(t3, st['min'], color='#1d4ed8', lw=1.4, linestyle='--', label='3D T_min')[0])
        else:
            lines_t.append(ax6.plot(t, plot_df['T_calc'].values, color='#0284c7', lw=1.8, label=f"0D 温度 (均温={np.mean(plot_df['T_calc'].values):.1f}°C)")[0])
        if 'T_Test' in plot_df.columns and not plot_df['T_Test'].isna().all():
            lines_t.append(ax6.plot(t, plot_df['T_Test'].values, color='#9a3412', linestyle=':', lw=1.5, label='实测温度')[0])
        ax6.set_xlabel('Time (s)', fontsize=9)
        ax6.set_ylabel('Temperature (°C)', fontsize=9)
        ax6.grid(True, linestyle='--', alpha=0.5)
        ax6r = ax6.twinx()
        lines_t.append(ax6r.step(t, plot_df['Cooling_On'].values, where='post', color='#ef4444', linestyle='--', lw=1.2, alpha=0.65, label='水冷 0/1')[0])
        ax6r.set_ylim(-0.1, 1.35)
        ax6r.set_ylabel('Cooling', fontsize=9, color='#ef4444')
        ax6.legend(lines_t, [l.get_label() for l in lines_t], loc='best', fontsize=7.5, frameon=False)
        ax6.set_title('(f) 温度演化与冷却状态', fontsize=10)

        self.fig.tight_layout(rect=[0, 0, 1, 0.94])

    def _plot_hgp_heat_breakdown(self, out_df: pd.DataFrame):
        self.fig.clf()
        ax = self.fig.add_subplot(111)
        t = out_df['time'].values
        ax.plot(t, out_df['HGP_dUdT'].values, color='#dc2626', lw=2.0, label='总产热功率 Q_tot (HGP_dUdT)')
        ax.plot(t, out_df['HGP'].values, color='#2563eb', lw=1.7, linestyle='--', label='不可逆产热 Q_irr (极化/焦耳热)')
        if 'Q_rev' in out_df.columns:
            ax.plot(t, out_df['Q_rev'].values, color='#059669', lw=1.7, linestyle='-.', label='可逆熵热 Q_rev (I·T·dU/dT)')
        ax.axhline(0.0, color='#94a3b8', lw=0.9)
        ax.set_title('电芯产热功率分量分解 (Bernardi 产热方程)', fontsize=13, fontweight='bold')
        ax.set_xlabel('时间 Time (s)', fontsize=11)
        ax.set_ylabel('产热功率 Heat Power (W)', fontsize=11)
        ax.legend(loc='best', fontsize=10)
        ax.grid(True, linestyle='--', alpha=0.5)
        self.fig.tight_layout()

    def _plot_3d_deltat_curve(self, thermal: dict):
        self.fig.clf()
        ax = self.fig.add_subplot(111)
        t = thermal['time_history']
        st = thermal['stats']
        dt_arr = np.array(st['max']) - np.array(st['min'])
        ax.plot(t, dt_arr, color='#dc2626', lw=2.2, label=f'全域最大温差 ΔT = T_max - T_min (峰值={np.max(dt_arr):.2f}°C)')
        ax.plot(t, st['std'], color='#2563eb', lw=1.8, linestyle='--', label='体积加权温度标准差 σ_T')
        ax.set_title('3D 电芯空间温差与温度均匀性演化', fontsize=13, fontweight='bold')
        ax.set_xlabel('时间 Time (s)', fontsize=11)
        ax.set_ylabel('温差 (°C)', fontsize=11)
        ax.legend(loc='best', fontsize=10)
        ax.grid(True, linestyle='--', alpha=0.5)
        self.fig.tight_layout()

    # ------------------------------------------------------------------
    # Export Excel & VTK
    # ------------------------------------------------------------------
    def export_ecthermal_excel(self):
        if not self.ec_last_result:
            return
        default_name = f"ECThermal_Result_{time.strftime('%Y%m%d_%H%M%S')}.xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "导出电-热耦合仿真结果", default_name, "Excel Files (*.xlsx)")
        if not path:
            return
        try:
            res = self.ec_last_result
            thermal = res.get('thermal', {})
            t3_df = None
            if thermal.get('thermal_dim') == '3D' and res['mode'] == 'hgp':
                st = thermal['stats']
                t3_df = pd.DataFrame({
                    'time': thermal['time_history'],
                    'Q_avg_W': thermal['q_history'],
                    'T_min_degC': st['min'],
                    'T_avg_degC': st['avg'],
                    'T_max_degC': st['max'],
                    'T_std_degC': st['std'],
                    'DeltaT_degC': np.array(st['max']) - np.array(st['min']),
                })
            save_hgp_results_to_excel(res['out_df'], res['summary_df'], path, thermal_stats_df=t3_df)
            self.update_status(f"[导出成功] 电-热耦合结果已保存至: {path}")
            QMessageBox.information(self, "导出成功", f"仿真结果已保存至:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))

    def export_ecthermal_vtk(self):
        if not self.ec_last_result:
            return
        thermal = self.ec_last_result.get('thermal', {})
        if thermal.get('thermal_dim') != '3D':
            return
        snap_idx = self.ec_snap_slider.value() if self.ec_snap_slider.isEnabled() else -1
        T_snaps = thermal['T_snapshots']
        t_snaps = thermal['t_snapshots']
        idx = max(0, min(len(T_snaps) - 1, snap_idx if snap_idx >= 0 else len(T_snaps) - 1))
        default_name = f"FVM_3D_Field_t{t_snaps[idx]:.0f}s.vtk"
        path, _ = QFileDialog.getSaveFileName(self, "导出 ParaView VTK 温度场", default_name, "VTK Files (*.vtk)")
        if not path:
            return
        try:
            export_vtk_structured_grid(
                thermal['mesh'], T_snaps[idx], path,
                material_props=thermal['material_props'],
                boundary_conditions=thermal['boundary_conditions'],
            )
            self.update_status(f"[VTK导出成功] 已保存 t={t_snaps[idx]:.1f}s 3D 温度场至: {path}")
            QMessageBox.information(self, "导出成功", f"ParaView VTK 文件已保存至:\n{path}")
        except Exception as e:
            QMessageBox.critical(self, "导出失败", str(e))
