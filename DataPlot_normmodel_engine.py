# -*- coding: utf-8 -*-
"""
DataPlot_normmodel_engine.py
NASA NDARC Normalized Battery Model (Horton, Chapman & Leader, AIAA 2024-3620)
Pure NumPy/Pandas implementation (zero SciPy dependency) for bidirectional CC/DC voltage prediction
with temperature dependence.
"""

import numpy as np
import pandas as pd


class NormalizedBatteryModel:
    """NASA NDARC Normalized Battery Model for bidirectional CC/DC calculation."""

    def __init__(self, param_excel_path: str):
        df_p = pd.read_excel(param_excel_path, sheet_name='Params').set_index('Mode')
        df_dc_tbl = pd.read_excel(param_excel_path, sheet_name='DC_Fv_Table')
        df_cc_tbl = pd.read_excel(param_excel_path, sheet_name='CC_Fv_Table')

        self.dc = df_p.loc['DC'].to_dict()
        self.cc = df_p.loc['CC'].to_dict()
        self.dc_kd = np.ascontiguousarray(df_dc_tbl['kd'].values, dtype=np.float64)
        self.dc_fv = np.ascontiguousarray(df_dc_tbl['F_V'].values, dtype=np.float64)
        self.cc_kc = np.ascontiguousarray(df_cc_tbl['kc'].values, dtype=np.float64)
        self.cc_fv = np.ascontiguousarray(df_cc_tbl['F_V'].values, dtype=np.float64)

    def predict_voltage(self, soc: float, current_signed: float, T_cell: float,
                        T_amb: float = None, mode: str = None) -> float:
        """
        Compute terminal voltage [V].
        Sign convention: current_signed > 0 for Charge, < 0 for Discharge.
        Optional mode ('DC' or 'CC') overrides direction when current_signed == 0.
        """
        i_mag = abs(float(current_signed))
        is_dc = (mode == 'DC') if mode in ('DC', 'CC') else (current_signed < 0.0)
        if is_dc:
            p = self.dc
            t_ref = float(p['T_amb_degC']) if T_amb is None else float(T_amb)
            dT = float(T_cell) - t_ref
            kd = (1.0 - float(soc)) * (1.0 + float(p['k_dI_A_inv']) * i_mag - float(p['k_dT_K_inv']) * dT)
            fv = float(np.interp(kd, self.dc_kd, self.dc_fv))
            return float(float(p['V_max_V']) * fv + float(p['k_VT_V_K3']) * (dT ** 3) - i_mag * float(p['R_Ohm']))
        else:
            p = self.cc
            t_ref = float(p['T_amb_degC']) if T_amb is None else float(T_amb)
            dT = float(T_cell) - t_ref
            kc = float(soc) * (1.0 + float(p['k_dI_A_inv']) * i_mag - float(p['k_dT_K_inv']) * dT)
            fv = float(np.interp(kc, self.cc_kc, self.cc_fv))
            return float(float(p['V_max_V']) * fv + float(p['k_VT_V_K3']) * (dT ** 3) + i_mag * float(p['R_Ohm']))
