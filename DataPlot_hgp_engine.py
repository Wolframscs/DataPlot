# -*- coding: utf-8 -*-
"""
DataPlot_hgp_engine.py
Multi-Stage & Multi-Cycle Battery Electro-Thermal Simulation Engine (HGP Core)
Refactored from HGP_Calc_Temp_REF_Module_Cycle.py into a pure, stateless,
config-driven engine with pluggable 0D Lumped or 3D FVM thermal coupling.

Supports all operating modes:
  - Charge: CC (const C-rate), CC_CMap (2D T x SOC map), CC_NegRef (anti-plating),
            CC_CProfile (current profile), CC_PProfile (power profile), CV (const voltage)
  - Discharge: DC (const C-rate), DC_CProfile (bidirectional current), DC_PProfile (bidirectional power)
  - Rest: Rest (voltage relaxation & cooling)
  - External Power Table: bypasses electrochemical model and drives 0D/3D thermal model directly

Zero SciPy dependency; pure NumPy + Pandas.
"""

import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd


@dataclass
class HGPConfig:
    """All configurable parameters for the HGP electro-thermal engine."""

    # Sheet names in HGP input workbook
    sheet_ocv: str = 'OCV'
    sheet_dudt: str = 'dUdT'
    sheet_cc: str = 'CC'
    sheet_dc: str = 'DC'
    sheet_cc_cprofile: str = 'CC_CProfile'
    sheet_cc_pprofile: str = 'CC_PProfile'
    sheet_dc_cprofile: str = 'DC_CProfile'
    sheet_dc_pprofile: str = 'DC_PProfile'
    sheet_cc_cmap: str = 'CC_CMap'

    # Electrical & numerical parameters
    ref_cal: bool = True
    negref_unit: str = 'mV'
    speci_capac: float = 156.0
    soc_init: float = 0.0
    step_dt: float = 1.0
    rest_dt: float = 10.0
    pile_max_c: float = 6.0
    static_rate_anchor: float = 0.04
    voltage_filter_weight: float = 9.0
    min_voltage_for_power: float = 0.5
    max_stage_steps: int = 5000000

    # Safety & cutoff defaults
    ref_min: float = 5.0
    u_min: float = 2.0
    u_max: float = 3.65
    cv_i_cut_c: float = 0.05
    cmap_t_thresh: float = 45.0

    # SOH & internal resistance degradation
    soh_init: float = 1.0
    soh_min: float = 0.5
    soh_fade_per_cycle: float = 0.0
    r_growth_per_cycle: float = 0.0

    # Voltage model selection: 'lookup' or 'norm_model'
    voltage_model: str = 'lookup'

    # 0D Lumped thermal & liquid cooling parameters
    t_init: float = 25.0
    t_amb: Optional[float] = None  # If None, defaults to t_init
    cooling_liquid: bool = True
    t_cool_on: float = 35.0
    t_cool_off: float = 30.0
    h_liquid_on: float = 46.0
    h_liquid_off: float = 10.0
    t_liquid: float = 20.0
    cell_h_conv_air: float = 46.0
    cell_h_conv_insul: float = 1e-10
    coeff_temp: float = 1.0
    cell_l: float = 0.0580
    cell_w: float = 0.0800
    cell_h: float = 0.2080
    cell_mass: float = 2.6
    cell_cp: float = 1063.0
    # Legacy compatibility: original script included L*W in both A_air and A_bottom in Convection mode
    legacy_air_compat: bool = False

    # Excel export limits
    max_excel_rows: int = 1000000
    excel_overflow_mode: str = 'downsample'

    # Derived constants (computed in __post_init__)
    cell_a_cool_side_top: float = field(init=False)
    cell_a_cool_bottom: float = field(init=False)
    cell_a_cool_air_eff: float = field(init=False)
    cell_h_conv_eff: float = field(init=False)
    t_liquid_eff: float = field(init=False)
    tau_air: float = field(init=False)

    def __post_init__(self):
        if self.t_amb is None:
            self.t_amb = float(self.t_init)
        self.cell_a_cool_side_top = 2.0 * (self.cell_l * self.cell_h + self.cell_w * self.cell_h) + self.cell_l * self.cell_w
        self.cell_a_cool_bottom = self.cell_l * self.cell_w
        if self.cooling_liquid:
            self.cell_h_conv_eff = float(self.cell_h_conv_insul)
            self.t_liquid_eff = float(self.t_liquid)
            self.cell_a_cool_air_eff = self.cell_a_cool_side_top
        else:
            self.cell_h_conv_eff = float(self.cell_h_conv_air)
            self.t_liquid_eff = float(self.t_amb)
            if self.legacy_air_compat:
                self.cell_a_cool_air_eff = self.cell_a_cool_side_top
            else:
                # Avoid double-counting bottom area when convection applies uniformly to all 6 faces
                self.cell_a_cool_air_eff = 2.0 * (self.cell_l * self.cell_h + self.cell_w * self.cell_h) + self.cell_l * self.cell_w
        denom = self.cell_h_conv_eff * self.cell_a_cool_air_eff
        self.tau_air = (self.cell_mass * self.cell_cp / denom) if denom > 0.0 else 1e30


# ==============================================================================
# 1. Fast Interpolation & Electrochemical Lookup Helpers
# ==============================================================================

def fast_interp1d_extrap(x_arr: np.ndarray, y_arr: np.ndarray, x_val: float) -> float:
    """
    Fast 1D piecewise linear interpolation with linear extrapolation.
    Numerically equivalent to scipy.interpolate.interp1d(..., kind='slinear', fill_value='extrapolate').
    """
    n = len(x_arr)
    if n == 1:
        return float(y_arr[0])
    if x_val <= x_arr[0]:
        dx = x_arr[1] - x_arr[0]
        return float(y_arr[0] + (y_arr[1] - y_arr[0]) * (x_val - x_arr[0]) / dx) if dx != 0.0 else float(y_arr[0])
    if x_val >= x_arr[-1]:
        dx = x_arr[-1] - x_arr[-2]
        return float(y_arr[-1] + (y_arr[-1] - y_arr[-2]) * (x_val - x_arr[-1]) / dx) if dx != 0.0 else float(y_arr[-1])
    if n <= 16:
        idx = 1
        while idx < n and x_arr[idx] < x_val:
            idx += 1
    else:
        idx = int(np.searchsorted(x_arr, x_val))
    x0, x1 = x_arr[idx - 1], x_arr[idx]
    y0, y1 = y_arr[idx - 1], y_arr[idx]
    dx = x1 - x0
    return float(y0 + (y1 - y0) * (x_val - x0) / dx) if dx != 0.0 else float(y0)


def get_u_dyna_fast(i_val: float, soc: float, tables: Dict[str, Any],
                    u_stat_val: float, capac: float) -> float:
    """Bidirectional BOL dynamic terminal voltage lookup."""
    if i_val == 0.0:
        return u_stat_val
    if i_val > 0.0:
        rates_ext = tables['cc_rates_ext']
        soc_list = tables['cc_v_soc']
        val_list = tables['cc_v_val']
    else:
        rates_ext = tables['dc_rates_ext']
        soc_list = tables['dc_v_soc']
        val_list = tables['dc_v_val']

    rate_t = abs(i_val) / capac
    u_list = [u_stat_val]
    for x_arr, y_arr in zip(soc_list, val_list):
        u_list.append(fast_interp1d_extrap(x_arr, y_arr, soc))
    return fast_interp1d_extrap(rates_ext, u_list, rate_t)


def get_u_dyna_ref_fast(i_val: float, soc: float, tables: Dict[str, Any], capac: float) -> float:
    """Bidirectional negative reference potential lookup [mV]."""
    if i_val >= 0.0:
        rates_ext = tables['cc_rates_ext']
        soc_list = tables['cc_ref_soc']
        val_list = tables['cc_ref_val']
    else:
        rates_ext = tables['dc_rates_ext']
        soc_list = tables['dc_ref_soc']
        val_list = tables['dc_ref_val']

    u_list = [fast_interp1d_extrap(x_arr, y_arr, soc) for x_arr, y_arr in zip(soc_list, val_list)]
    if i_val == 0.0:
        return u_list[0]
    rate_t = abs(i_val) / capac
    return fast_interp1d_extrap(rates_ext, [u_list[0]] + u_list, rate_t)


def lookup_cc_map(T: float, soc: float, temp_grid: np.ndarray, soc_grid: np.ndarray,
                  map_values: np.ndarray, soh: float = 1.0,
                  pile_max_c: Optional[float] = None, cmap_t_thresh: float = 45.0) -> float:
    """2D lookup on CC_CMap (Temperature x SOC) with SOH scaling."""
    T_lookup = float(np.floor(T)) if T <= cmap_t_thresh else float(np.ceil(T))
    soc_lookup = max(0.0, min(1.0, float(np.floor(soc * 100.0) / 100.0)))
    T_lookup = max(float(temp_grid[0]), min(float(temp_grid[-1]), T_lookup))

    if T_lookup <= temp_grid[0]:
        t_idx0 = t_idx1 = 0
    elif T_lookup >= temp_grid[-1]:
        t_idx0 = t_idx1 = len(temp_grid) - 1
    else:
        t_idx1 = int(np.searchsorted(temp_grid, T_lookup))
        t_idx0 = t_idx1 - 1

    if soc_lookup <= soc_grid[0]:
        s_idx0 = s_idx1 = 0
    elif soc_lookup >= soc_grid[-1]:
        s_idx0 = s_idx1 = len(soc_grid) - 1
    else:
        s_idx1 = int(np.searchsorted(soc_grid, soc_lookup))
        s_idx0 = s_idx1 - 1

    x0, x1 = soc_grid[s_idx0], soc_grid[s_idx1]
    y0, y1 = temp_grid[t_idx0], temp_grid[t_idx1]
    z00, z01 = map_values[t_idx0, s_idx0], map_values[t_idx0, s_idx1]
    z10, z11 = map_values[t_idx1, s_idx0], map_values[t_idx1, s_idx1]

    if x1 == x0:
        z_t0, z_t1 = z00, z10
    else:
        frac_x = (soc_lookup - x0) / (x1 - x0)
        z_t0 = z00 + (z01 - z00) * frac_x
        z_t1 = z10 + (z11 - z10) * frac_x

    if y1 == y0:
        c_rate_raw = z_t0
    else:
        c_rate_raw = z_t0 + (z_t1 - z_t0) * (T_lookup - y0) / (y1 - y0)

    c_rate_soh = c_rate_raw * soh
    c_rate_final = min(c_rate_soh, pile_max_c) if pile_max_c is not None else c_rate_soh
    return float(np.round(c_rate_final, 2))


def solve_cv_crate(u_target: float, soc: float, u_stat: float, tables: Dict[str, Any],
                   r_factor: float, pile_max_c: float, capac: float,
                   norm_model: Any = None, T_cur: float = 25.0, T_amb: float = 25.0) -> float:
    """
    Invert the charge voltage curve at current SOC to find the C-rate that yields u_target.
    Used for optional CV (Constant Voltage) stage.
    """
    if u_target <= u_stat + 1e-6:
        return 0.0
    if norm_model is not None:
        # Bisection on [0, pile_max_c]
        lo, hi = 0.0, float(pile_max_c)
        for _ in range(28):
            mid = 0.5 * (lo + hi)
            v_mid = norm_model.predict_voltage(soc, mid * capac, T_cur, T_amb, mode='CC')
            v_aged = u_stat + r_factor * (v_mid - u_stat)
            if v_aged < u_target:
                lo = mid
            else:
                hi = mid
        return float(max(0.0, min(0.5 * (lo + hi), pile_max_c)))

    rates_ext = tables['cc_rates_ext']
    u_aged_list = [u_stat]
    for x_arr, y_arr in zip(tables['cc_v_soc'], tables['cc_v_val']):
        u_bol = fast_interp1d_extrap(x_arr, y_arr, soc)
        u_aged_list.append(u_stat + r_factor * (u_bol - u_stat))
    u_arr = np.asarray(u_aged_list, dtype=np.float64)
    order = np.argsort(u_arr)
    c_rate = fast_interp1d_extrap(u_arr[order], rates_ext[order], u_target)
    return float(max(0.0, min(c_rate, pile_max_c)))


# ==============================================================================
# 2. Degradation, Cooling & 0D Lumped Thermal Stepper
# ==============================================================================

def get_soh_by_cycle(cycle_idx: int, cfg: HGPConfig) -> float:
    return max(cfg.soh_min, cfg.soh_init - cfg.soh_fade_per_cycle * cycle_idx)


def get_resistance_factor_by_cycle(cycle_idx: int, soh: float, cfg: HGPConfig) -> float:
    return 1.0 + cfg.r_growth_per_cycle * cycle_idx


def update_cooling_state(temp: float, prev_cooling_on: bool, cfg: HGPConfig) -> bool:
    if not cfg.cooling_liquid:
        return False
    if temp >= cfg.t_cool_on:
        return True
    if temp <= cfg.t_cool_off:
        return False
    return prev_cooling_on


class Lumped0DThermalStepper:
    """Default 0D lumped single-node thermal model implementing the ThermalStepper protocol."""

    def __init__(self, cfg: HGPConfig):
        self.cfg = cfg
        self.T = float(cfg.t_init)
        self.T_amb = float(cfg.t_amb)
        self.cooling_on = update_cooling_state(self.T, False, cfg)

    def reset_temperature(self, T_val: float, T_amb_val: Optional[float] = None):
        self.T = float(T_val)
        if T_amb_val is not None:
            self.T_amb = float(T_amb_val)
        self.cooling_on = update_cooling_state(self.T, False, self.cfg)

    def step(self, q_tot: float, dt: float, t_now: float) -> Tuple[float, bool]:
        cfg = self.cfg
        self.cooling_on = update_cooling_state(self.T, self.cooling_on, cfg)
        if dt <= 0.0:
            return self.T, self.cooling_on

        if cfg.cooling_liquid:
            h_bottom = cfg.h_liquid_on if self.cooling_on else cfg.h_liquid_off
            T_sink_bottom = cfg.t_liquid_eff if self.cooling_on else self.T_amb
            tau_bottom = (cfg.cell_mass * cfg.cell_cp) / max(1e-30, h_bottom * cfg.cell_a_cool_bottom)
            dT = dt * (
                cfg.coeff_temp * q_tot / (cfg.cell_mass * cfg.cell_cp)
                - (self.T - self.T_amb) / cfg.tau_air
                - (self.T - T_sink_bottom) / tau_bottom
            )
        else:
            if cfg.legacy_air_compat:
                tau_bottom = (cfg.cell_mass * cfg.cell_cp) / max(1e-30, cfg.cell_h_conv_eff * cfg.cell_a_cool_bottom)
                dT = dt * (
                    cfg.coeff_temp * q_tot / (cfg.cell_mass * cfg.cell_cp)
                    - (self.T - self.T_amb) / cfg.tau_air
                    - (self.T - self.T_amb) / tau_bottom
                )
            else:
                # Total 6-face surface area without double-counting bottom
                total_area = 2.0 * (cfg.cell_l * cfg.cell_h + cfg.cell_w * cfg.cell_h + cfg.cell_l * cfg.cell_w)
                tau_all = (cfg.cell_mass * cfg.cell_cp) / max(1e-30, cfg.cell_h_conv_eff * total_area)
                dT = dt * (
                    cfg.coeff_temp * q_tot / (cfg.cell_mass * cfg.cell_cp)
                    - (self.T - self.T_amb) / tau_all
                )

        self.T = self.T + dT
        return self.T, self.cooling_on

    def finalize(self) -> Dict[str, Any]:
        return {'thermal_dim': '0D'}


# ==============================================================================
# 3. Excel Data Preloader
# ==============================================================================

def inspect_hgp_workbook(input_path: str) -> Dict[str, Any]:
    """Quickly inspect sheet names available in an HGP input Excel file."""
    xl = pd.ExcelFile(input_path)
    sheets = list(xl.sheet_names)
    return {
        'sheets': sheets,
        'has_ocv': 'OCV' in sheets,
        'has_dudt': 'dUdT' in sheets,
        'has_cc': 'CC' in sheets,
        'has_dc': 'DC' in sheets,
        'has_cc_cmap': 'CC_CMap' in sheets,
        'has_cc_cprofile': 'CC_CProfile' in sheets,
        'has_cc_pprofile': 'CC_PProfile' in sheets,
        'has_dc_cprofile': 'DC_CProfile' in sheets,
        'has_dc_pprofile': 'DC_PProfile' in sheets,
    }


def preload_all_data(input_path: str, cfg: Optional[HGPConfig] = None) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Preload OCV, dUdT, CC/DC rate tables, and optional profile/map sheets once."""
    if cfg is None:
        cfg = HGPConfig()
    xl = pd.ExcelFile(input_path)

    df_ocv = pd.read_excel(xl, sheet_name=cfg.sheet_ocv)
    df_dudt = pd.read_excel(xl, sheet_name=cfg.sheet_dudt)
    ocv_arr = df_ocv[['SOC', 'OCV']].dropna().sort_values(by='SOC').values
    dudt_arr = df_dudt[['SOC', 'dUdT']].dropna().sort_values(by='SOC').values
    ref_scale = 1000.0 if str(cfg.negref_unit).strip().lower() == 'v' else 1.0

    def parse_rate_sheet(sheet_name: str):
        df = pd.read_excel(xl, sheet_name=sheet_name)
        cols = list(df.columns)
        rate_starts = []
        for idx, c in enumerate(cols):
            if isinstance(c, bool) or pd.isna(c):
                continue
            try:
                rate_val = float(c)
                if np.isfinite(rate_val):
                    rate_starts.append((idx, rate_val))
            except (ValueError, TypeError):
                continue

        rates, v_data, ref_data = [], [], []
        for b_idx, (i, rate_val) in enumerate(rate_starts):
            next_i = rate_starts[b_idx + 1][0] if b_idx + 1 < len(rate_starts) else len(cols)
            if i + 3 >= next_i:
                continue
            soc_col = cols[i + 2]
            sub_df = df.iloc[:, [i + 2, i + 3]].dropna().drop_duplicates(subset=[soc_col]).sort_values(by=soc_col)
            rates.append(rate_val)
            v_data.append(sub_df.values.astype(float))

            if i + 6 < next_i:
                sub_ref = df.iloc[:, [i + 2, i + 6]].dropna().drop_duplicates(subset=[soc_col]).sort_values(by=soc_col)
                ref_vals = sub_ref.values.astype(float)
                ref_vals[:, 1] = ref_vals[:, 1] * ref_scale
                ref_data.append(ref_vals)
            else:
                zero_ref = sub_df.values.astype(float).copy()
                zero_ref[:, 1] = 0.0
                ref_data.append(zero_ref)
        return rates, v_data, ref_data

    cc_rates, cc_v_data, cc_ref_data = parse_rate_sheet(cfg.sheet_cc)
    dc_rates, dc_v_data, dc_ref_data = parse_rate_sheet(cfg.sheet_dc)

    tables = {
        'ocv_soc': np.ascontiguousarray(ocv_arr[:, 0], dtype=np.float64),
        'ocv_val': np.ascontiguousarray(ocv_arr[:, 1], dtype=np.float64),
        'dudt_soc': np.ascontiguousarray(dudt_arr[:, 0], dtype=np.float64),
        'dudt_val': np.ascontiguousarray(dudt_arr[:, 1], dtype=np.float64),
        'CCRates': cc_rates,
        'CCRateData': cc_v_data,
        'CCRateRefData': cc_ref_data,
        'DCRates': dc_rates,
        'DCRateData': dc_v_data,
        'DCRateRefData': dc_ref_data,
        'cc_rates_ext': np.ascontiguousarray([cfg.static_rate_anchor] + cc_rates, dtype=np.float64),
        'dc_rates_ext': np.ascontiguousarray([cfg.static_rate_anchor] + dc_rates, dtype=np.float64),
        'cc_v_soc': [np.ascontiguousarray(d[:, 0], dtype=np.float64) for d in cc_v_data],
        'cc_v_val': [np.ascontiguousarray(d[:, 1], dtype=np.float64) for d in cc_v_data],
        'cc_ref_soc': [np.ascontiguousarray(d[:, 0], dtype=np.float64) for d in cc_ref_data],
        'cc_ref_val': [np.ascontiguousarray(d[:, 1], dtype=np.float64) for d in cc_ref_data],
        'dc_v_soc': [np.ascontiguousarray(d[:, 0], dtype=np.float64) for d in dc_v_data],
        'dc_v_val': [np.ascontiguousarray(d[:, 1], dtype=np.float64) for d in dc_v_data],
        'dc_ref_soc': [np.ascontiguousarray(d[:, 0], dtype=np.float64) for d in dc_ref_data],
        'dc_ref_val': [np.ascontiguousarray(d[:, 1], dtype=np.float64) for d in dc_ref_data],
    }

    profiles: Dict[str, Any] = {}
    for key, sheet_name in [
        ('CC_CProfile', cfg.sheet_cc_cprofile),
        ('CC_PProfile', cfg.sheet_cc_pprofile),
        ('DC_CProfile', cfg.sheet_dc_cprofile),
        ('DC_PProfile', cfg.sheet_dc_pprofile),
        ('CC_CMap', cfg.sheet_cc_cmap),
    ]:
        if sheet_name in xl.sheet_names:
            profiles[key] = (pd.read_excel(xl, sheet_name=sheet_name), sheet_name)

    if 'CC_CMap' in profiles:
        df_map, _ = profiles['CC_CMap']
        profiles['CC_CMap_grids'] = (
            np.ascontiguousarray(df_map.iloc[:, 0].values, dtype=np.float64),
            np.array([float(col) for col in df_map.columns[1:]], dtype=np.float64),
            np.ascontiguousarray(df_map.iloc[:, 1:].values, dtype=np.float64),
        )

    return tables, profiles


# ==============================================================================
# 4. Condition Normalization & Stage Execution
# ==============================================================================

def normalize_condition(cond_dict: Dict[str, Any], cfg: HGPConfig) -> Dict[str, Any]:
    """Parse flexible condition dictionary into canonical stage specification."""
    spec = {
        'category': None,    # 'CC', 'Rest', or 'DC'
        'mode': None,        # 'CC_CProfile', 'CC_PProfile', 'CC_CMap', 'CC_NegRef', 'CC', 'CV', 'Rest', 'DC_CProfile', 'DC_PProfile', 'DC'
        'c_rate': None,
        'soc_end': None,
        'soc_init': cond_dict.get('SOC_Init', cond_dict.get('soc_init', None)),
        'time_limit': None,
        'u_max': cfg.u_max,
        'u_min': cfg.u_min,
        'i_cut_c': float(cond_dict.get('I_Cut_C', cfg.cv_i_cut_c)),
    }

    if 'Rest_Cond' in cond_dict or cond_dict.get('type') == 'Rest':
        spec['category'] = 'Rest'
        spec['mode'] = 'Rest'
        t_val = cond_dict.get('Rest_Cond', cond_dict.get('time', cond_dict.get('Time', cond_dict.get('duration'))))
        spec['time_limit'] = float(t_val) if t_val is not None else 0.0
        return spec

    if 'CC_Cond' in cond_dict or cond_dict.get('type') == 'CC':
        spec['category'] = 'CC'
        raw_mode = cond_dict.get('CC_Cond', cond_dict.get('mode', 'CC'))
        if isinstance(raw_mode, (int, float)):
            spec['mode'] = 'CC'
            spec['c_rate'] = abs(float(raw_mode))
        else:
            spec['mode'] = str(raw_mode).strip()
            if spec['mode'] == 'CC':
                spec['c_rate'] = abs(float(cond_dict.get('C_rate', cond_dict.get('rate', 1.0))))
    elif 'DC_Cond' in cond_dict or cond_dict.get('type') == 'DC':
        spec['category'] = 'DC'
        raw_mode = cond_dict.get('DC_Cond', cond_dict.get('mode', 'DC'))
        if isinstance(raw_mode, (int, float)):
            spec['mode'] = 'DC'
            spec['c_rate'] = abs(float(raw_mode))
        else:
            spec['mode'] = str(raw_mode).strip()
            if spec['mode'] == 'DC':
                spec['c_rate'] = abs(float(cond_dict.get('C_rate', cond_dict.get('rate', 1.0))))
    else:
        raise ValueError(f"Unrecognized condition dictionary: {cond_dict}")

    for k in ('SOC', 'SOC_End', 'soc_end'):
        if k in cond_dict and cond_dict[k] is not None and str(cond_dict[k]).strip() != '':
            spec['soc_end'] = float(cond_dict[k])
            break
    for k in ('time', 'Time', 'duration'):
        if k in cond_dict and cond_dict[k] is not None and str(cond_dict[k]).strip() != '':
            spec['time_limit'] = float(cond_dict[k])
            break
    for k in ('U_Max', 'u_max', 'V_Max'):
        if k in cond_dict and cond_dict[k] is not None and str(cond_dict[k]).strip() != '':
            spec['u_max'] = float(cond_dict[k])
            break
    for k in ('U_Min', 'u_min', 'V_Min'):
        if k in cond_dict and cond_dict[k] is not None and str(cond_dict[k]).strip() != '':
            spec['u_min'] = float(cond_dict[k])
            break

    return spec


def _prepare_profile_grid(df_raw: pd.DataFrame, value_candidates: List[str], step_dt: float):
    """Deduplicate by time, compute dt = min(step_dt, min_positive_dt), and resample onto uniform grid."""
    df = df_raw.drop_duplicates(subset=['time'], keep='first').sort_values(by='time')
    t_raw = df['time'].values.astype(np.float64)
    diffs = np.diff(t_raw)
    pos_diffs = diffs[diffs > 0.0]
    dt_min = float(np.min(pos_diffs)) if len(pos_diffs) > 0 else step_dt
    dt = min(step_dt, dt_min)

    n_pts = max(2, int(round((t_raw[-1] - t_raw[0]) / dt)) + 1)
    t_grid = np.linspace(t_raw[0], t_raw[-1], n_pts)

    val_col = None
    for c in value_candidates:
        if c in df.columns:
            val_col = c
            break
    if val_col is None:
        raise KeyError(f"None of {value_candidates} found in sheet columns: {list(df.columns)}")

    val_grid = np.interp(t_grid, t_raw, df[val_col].values.astype(np.float64))

    ref_test = None
    if 'NegRefVoltage' in df.columns:
        ref_raw = df['NegRefVoltage'].values.astype(np.float64)
        if np.nanmax(np.abs(ref_raw)) < 10.0:
            ref_raw = ref_raw * 1000.0
        ref_test = np.interp(t_grid, t_raw, ref_raw)

    extra = {
        'has_soc': 'SOC' in df.columns,
        'soc_start': float(df['SOC'].iloc[0]) if 'SOC' in df.columns else None,
        'soc_end': float(df['SOC'].iloc[-1]) if 'SOC' in df.columns else None,
        'volt_test': np.interp(t_grid, t_raw, df['Voltage'].values.astype(np.float64)) if 'Voltage' in df.columns else None,
        'temp_test': np.interp(t_grid, t_raw, df['Temperature'].values.astype(np.float64)) if 'Temperature' in df.columns else None,
        'ref_test': ref_test,
    }
    return t_grid, dt, val_grid, extra


def _eval_voltage_bol(i_val: float, soc: float, T_cur: float, T_amb: float,
                      u_stat: float, capac: float, tables: Dict[str, Any],
                      cfg: HGPConfig, norm_model: Any = None) -> float:
    if cfg.voltage_model == 'norm_model' and norm_model is not None:
        if i_val == 0.0:
            return u_stat
        return norm_model.predict_voltage(soc, i_val, T_cur, T_amb)
    return get_u_dyna_fast(i_val, soc, tables, u_stat, capac)


def run_stage(
    spec: Dict[str, Any],
    state: Dict[str, Any],
    tables: Dict[str, Any],
    profiles: Dict[str, Any],
    cycle_idx: int,
    stage_idx: int,
    cfg: HGPConfig,
    thermal_stepper: Any,
    norm_model: Any = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Tuple[pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    """Execute one stage (CC, Rest, or DC) starting from current continuous state."""
    mode = spec['mode']
    category = spec['category']
    soh = get_soh_by_cycle(cycle_idx, cfg)
    r_factor = get_resistance_factor_by_cycle(cycle_idx, soh, cfg)
    capac = cfg.speci_capac * soh

    soc_cur = float(spec['soc_init']) if spec['soc_init'] is not None else float(state['soc'])
    T_cur = float(state['T'])
    T_amb = float(state['T_amb'])
    cooling_on = bool(state['cooling_on'])
    t_global_start = float(state['t_global'])
    u_dyna_prev = state['u_dyna']
    u_ref_prev = state['u_ref']

    ocv_soc, ocv_val = tables['ocv_soc'], tables['ocv_val']
    dudt_soc, dudt_val = tables['dudt_soc'], tables['dudt_val']

    times, socs, currents, powers = [], [], [], []
    ostats, udynas, urefs, hgps, q_revs, hgp_totals, temps = [], [], [], [], [], [], []
    cool_flags = []
    v_tests, t_tests, ref_tests = [], [], []
    stop_reason = 'Completed'

    def append_step(t_g, soc_v, i_v, p_v, u_s, u_d, u_r, q_ir, q_rv, q_tot, T_v, c_on,
                    vt=np.nan, tt=np.nan, rt=np.nan):
        times.append(t_g)
        socs.append(soc_v)
        currents.append(i_v)
        powers.append(p_v)
        ostats.append(u_s)
        udynas.append(u_d)
        urefs.append(u_r)
        hgps.append(q_ir)
        q_revs.append(q_rv)
        hgp_totals.append(q_tot)
        temps.append(T_v)
        cool_flags.append(1 if c_on else 0)
        v_tests.append(vt)
        t_tests.append(tt)
        ref_tests.append(rt)

    # ── CASE A: REST STAGE ────────────────────────────────────────────────────
    if category == 'Rest':
        duration = float(spec['time_limit'])
        dt_r = min(cfg.rest_dt, duration) if duration > 0.0 else cfg.rest_dt
        elapsed = 0.0
        if duration <= 0.0:
            stop_reason = 'ZeroDuration'
        else:
            stop_reason = 'Time_End'
            u_stat0 = fast_interp1d_extrap(ocv_soc, ocv_val, soc_cur)
            u_dyna0 = u_dyna_prev if u_dyna_prev is not None else u_stat0
            u_ref0 = (u_ref_prev if u_ref_prev is not None else get_u_dyna_ref_fast(0.0, soc_cur, tables, capac)) if cfg.ref_cal else np.nan
            append_step(t_global_start, soc_cur, 0.0, 0.0, u_stat0, u_dyna0, u_ref0, 0.0, 0.0, 0.0, T_cur, cooling_on)
        step_cnt = 0
        while elapsed < duration - 1e-9:
            if cancel_check and (step_cnt % 200 == 0) and cancel_check():
                stop_reason = 'Cancelled'
                break
            step_cnt += 1
            dt_step = min(dt_r, duration - elapsed)
            elapsed += dt_step
            t_g = t_global_start + elapsed
            u_stat = fast_interp1d_extrap(ocv_soc, ocv_val, soc_cur)
            w_flt = cfg.voltage_filter_weight
            u_dyna = (u_stat + u_dyna_prev * w_flt) / (w_flt + 1.0) if u_dyna_prev is not None else u_stat
            u_dyna_prev = u_dyna
            u_ref = get_u_dyna_ref_fast(0.0, soc_cur, tables, capac) if cfg.ref_cal else np.nan
            if cfg.ref_cal:
                u_ref = (u_ref + u_ref_prev * w_flt) / (w_flt + 1.0) if u_ref_prev is not None else u_ref
                u_ref_prev = u_ref
            T_cur, cooling_on = thermal_stepper.step(0.0, dt_step, t_g)
            append_step(t_g, soc_cur, 0.0, 0.0, u_stat, u_dyna, u_ref, 0.0, 0.0, 0.0, T_cur, cooling_on)

    # ── CASE B: PROFILE-BASED MODES (CProfile / PProfile) ─────────────────────
    elif mode in ('CC_CProfile', 'CC_PProfile', 'DC_CProfile', 'DC_PProfile'):
        if mode not in profiles:
            raise KeyError(f"Required sheet for mode '{mode}' not found in workbook.")
        df_raw, _ = profiles[mode]
        is_power = 'PProfile' in mode
        val_cols = ['Power/W', 'Power', 'Power(W)'] if is_power else ['Current', 'Current/A', 'Current(A)']
        t_grid, dt, val_grid, extra = _prepare_profile_grid(df_raw, val_cols, cfg.step_dt)

        soc_end_limit = spec['soc_end']
        if category == 'DC' and extra['has_soc']:
            soc_cur = float(extra['soc_start'])
            if soc_end_limit is None:
                soc_end_limit = float(extra['soc_end'])

        if extra['temp_test'] is not None and stage_idx == 0 and cycle_idx == 0:
            T_cur = float(extra['temp_test'][0])
            T_amb = T_cur
            thermal_stepper.reset_temperature(T_cur, T_amb)

        has_vt = extra['volt_test'] is not None
        has_tt = extra['temp_test'] is not None
        has_rt = extra['ref_test'] is not None
        t_base = t_grid[0]
        w_flt = cfg.voltage_filter_weight

        for idx in range(len(t_grid)):
            if cancel_check and (idx % 200 == 0) and cancel_check():
                stop_reason = 'Cancelled'
                break
            elapsed = float(t_grid[idx] - t_base)
            if spec['time_limit'] is not None and elapsed > spec['time_limit'] + 1e-9:
                stop_reason = 'TimeLimit'
                break

            if idx == 0 and (stage_idx > 0 or cycle_idx > 0):
                t_g = t_global_start
                dt_eff = 0.0
            elif idx == 0:
                t_g = t_global_start + elapsed
                dt_eff = 0.0
            else:
                dt_eff = float(t_grid[idx] - t_grid[idx - 1])
                t_g = t_global_start + elapsed

            u_stat = fast_interp1d_extrap(ocv_soc, ocv_val, soc_cur)
            u_ref_est = u_dyna_prev if u_dyna_prev is not None else u_stat

            if is_power:
                p_val = float(val_grid[idx])
                i_val = p_val / max(cfg.min_voltage_for_power, u_ref_est)
            else:
                i_val = float(val_grid[idx])

            u_dyna_bol = _eval_voltage_bol(i_val, soc_cur, T_cur, T_amb, u_stat, capac, tables, cfg, norm_model)
            u_dyna_aged = u_stat + r_factor * (u_dyna_bol - u_stat)
            u_dyna = (u_dyna_aged + u_dyna_prev * w_flt) / (w_flt + 1.0) if (u_dyna_prev is not None and idx > 0) else u_dyna_aged
            u_dyna_prev = u_dyna

            if is_power:
                p_val = float(val_grid[idx])
            else:
                p_val = i_val * u_dyna

            if cfg.ref_cal:
                u_ref_raw = get_u_dyna_ref_fast(i_val, soc_cur, tables, capac)
                u_ref = (u_ref_raw + u_ref_prev * w_flt) / (w_flt + 1.0) if (u_ref_prev is not None and idx > 0) else u_ref_raw
                u_ref_prev = u_ref
            else:
                u_ref = np.nan

            if dt_eff > 0.0:
                soc_cur = soc_cur + i_val * dt_eff / (capac * 3600.0)

            q_irrev = abs(i_val * (u_dyna - u_stat))
            q_rev = i_val * (T_cur + 273.15) * fast_interp1d_extrap(dudt_soc, dudt_val, soc_cur)
            q_tot = q_irrev + q_rev

            if dt_eff > 0.0:
                T_cur, cooling_on = thermal_stepper.step(q_tot, dt_eff, t_g)

            append_step(
                t_g, soc_cur, i_val, p_val, u_stat, u_dyna, u_ref, q_irrev, q_rev, q_tot, T_cur, cooling_on,
                vt=extra['volt_test'][idx] if has_vt else np.nan,
                tt=extra['temp_test'][idx] if has_tt else np.nan,
                rt=extra['ref_test'][idx] if has_rt else np.nan,
            )

            if category == 'CC':
                if soc_end_limit is not None and soc_cur >= soc_end_limit:
                    stop_reason = 'SOC_End'
                    break
                if u_dyna >= spec['u_max']:
                    stop_reason = 'U_Max'
                    break
            elif category == 'DC':
                if soc_end_limit is not None and soc_cur <= soc_end_limit:
                    stop_reason = 'SOC_End'
                    break
                if u_dyna <= spec['u_min']:
                    stop_reason = 'U_Min'
                    break

    # ── CASE C: STEP-BY-STEP MODES (CC, CV, DC, CC_CMap, CC_NegRef) ──────────
    else:
        if mode == 'CC_CMap':
            if 'CC_CMap_grids' not in profiles:
                raise KeyError(f"Required sheet '{cfg.sheet_cc_cmap}' not found in workbook.")
            temp_grid, soc_grid, map_values = profiles['CC_CMap_grids']
        else:
            temp_grid = soc_grid = map_values = None

        dt = float(cfg.step_dt)
        elapsed = 0.0
        is_const_ref = False
        w_flt = cfg.voltage_filter_weight

        for step in range(int(cfg.max_stage_steps)):
            if cancel_check and (step % 200 == 0) and cancel_check():
                stop_reason = 'Cancelled'
                break
            if spec['time_limit'] is not None and elapsed >= spec['time_limit'] - 1e-9:
                stop_reason = 'TimeLimit'
                break

            u_stat = fast_interp1d_extrap(ocv_soc, ocv_val, soc_cur)

            if mode == 'CC':
                i_val = spec['c_rate'] * capac
            elif mode == 'DC':
                i_val = -spec['c_rate'] * capac
            elif mode == 'CV':
                c_rate = solve_cv_crate(
                    spec['u_max'], soc_cur, u_stat, tables, r_factor, cfg.pile_max_c, capac,
                    norm_model=norm_model if cfg.voltage_model == 'norm_model' else None,
                    T_cur=T_cur, T_amb=T_amb,
                )
                i_val = c_rate * capac
            elif mode == 'CC_CMap':
                c_rate = lookup_cc_map(
                    T_cur, soc_cur, temp_grid, soc_grid, map_values,
                    soh=soh, pile_max_c=cfg.pile_max_c, cmap_t_thresh=cfg.cmap_t_thresh
                )
                i_val = c_rate * capac
            elif mode == 'CC_NegRef':
                if is_const_ref:
                    ref_potentials = [
                        fast_interp1d_extrap(x_arr, y_arr, soc_cur)
                        for x_arr, y_arr in zip(tables['cc_ref_soc'], tables['cc_ref_val'])
                    ]
                    sorted_idx = np.argsort(ref_potentials)
                    x_pot = np.array(ref_potentials)[sorted_idx]
                    y_rates = np.array(tables['CCRates'])[sorted_idx]
                    c_rate = fast_interp1d_extrap(x_pot, y_rates, cfg.ref_min)
                    c_rate = max(0.0, min(c_rate, cfg.pile_max_c))
                else:
                    c_rate = cfg.pile_max_c
                i_val = c_rate * capac
            else:
                raise ValueError(f"Unsupported mode: {mode}")

            u_dyna_bol = _eval_voltage_bol(i_val, soc_cur, T_cur, T_amb, u_stat, capac, tables, cfg, norm_model)
            u_dyna_aged = u_stat + r_factor * (u_dyna_bol - u_stat)
            if mode == 'CV':
                u_dyna = min(spec['u_max'], u_dyna_aged)
            else:
                u_dyna = (u_dyna_aged + u_dyna_prev * w_flt) / (w_flt + 1.0) if (u_dyna_prev is not None and step > 0) else u_dyna_aged
            u_dyna_prev = u_dyna

            p_val = i_val * u_dyna

            if cfg.ref_cal or mode == 'CC_NegRef':
                u_ref_raw = get_u_dyna_ref_fast(i_val, soc_cur, tables, capac)
                u_ref = (u_ref_raw + u_ref_prev * w_flt) / (w_flt + 1.0) if (u_ref_prev is not None and step > 0) else u_ref_raw
                u_ref_prev = u_ref
                if mode == 'CC_NegRef' and u_ref <= cfg.ref_min and not is_const_ref:
                    is_const_ref = True
            else:
                u_ref = np.nan

            q_irrev = abs(i_val * (u_dyna - u_stat))
            q_rev = i_val * (T_cur + 273.15) * fast_interp1d_extrap(dudt_soc, dudt_val, soc_cur)
            q_tot = q_irrev + q_rev

            t_g = t_global_start + elapsed
            append_step(t_g, soc_cur, i_val, p_val, u_stat, u_dyna, u_ref, q_irrev, q_rev, q_tot, T_cur, cooling_on)

            if category == 'CC':
                if mode == 'CV':
                    if (i_val / capac) <= spec['i_cut_c'] and step > 0:
                        stop_reason = 'I_Cut'
                        break
                    if spec['soc_end'] is not None and soc_cur >= spec['soc_end']:
                        stop_reason = 'SOC_End'
                        break
                    if soc_cur >= 1.0 and spec['soc_end'] is None:
                        stop_reason = 'SOC_Full'
                        break
                else:
                    if spec['soc_end'] is not None and soc_cur >= spec['soc_end']:
                        stop_reason = 'SOC_End'
                        break
                    if u_dyna >= spec['u_max']:
                        stop_reason = 'U_Max'
                        break
                    if soc_cur >= 1.0 and spec['soc_end'] is None:
                        stop_reason = 'SOC_Full'
                        break
            elif category == 'DC':
                if spec['soc_end'] is not None and soc_cur <= spec['soc_end']:
                    stop_reason = 'SOC_End'
                    break
                if u_dyna <= spec['u_min']:
                    stop_reason = 'U_Min'
                    break
                if soc_cur <= 0.0 and spec['soc_end'] is None:
                    stop_reason = 'SOC_Empty'
                    break

            elapsed += dt
            soc_cur = soc_cur + i_val * dt / (capac * 3600.0)
            T_cur, cooling_on = thermal_stepper.step(q_tot, dt, t_global_start + elapsed)

    # Package stage output
    n_pts = len(times)
    stage_df = pd.DataFrame({
        'time': times,
        'Cycle': np.full(n_pts, cycle_idx + 1, dtype=int),
        'Stage': np.full(n_pts, stage_idx + 1, dtype=int),
        'Mode': [mode] * n_pts,
        'SOC': socs,
        'Rate': np.array(currents, dtype=np.float64) / capac if n_pts else np.array([], dtype=np.float64),
        'Current': currents,
        'OCV': ostats,
        'Voltage': udynas,
        'DeltaU': np.abs(np.array(udynas, dtype=np.float64) - np.array(ostats, dtype=np.float64)) if n_pts else np.array([], dtype=np.float64),
        'HGP': hgps,
        'Q_rev': q_revs,
        'HGP_dUdT': hgp_totals,
        'T_calc': temps,
        'Power': powers,
        'Cooling_On': cool_flags,
        'SOH': np.full(n_pts, soh),
        'R_Factor': np.full(n_pts, r_factor),
    })
    if cfg.ref_cal:
        stage_df['REF_Voltage'] = urefs
    if any(not np.isnan(x) for x in v_tests):
        stage_df['Voltage_Test'] = v_tests
    if any(not np.isnan(x) for x in t_tests):
        stage_df['T_Test'] = t_tests
    if any(not np.isnan(x) for x in ref_tests):
        stage_df['REF_Voltage_Test'] = ref_tests

    t_arr = np.array(times, dtype=np.float64)
    if n_pts > 1:
        dt_arr = np.diff(t_arr)
        p_avg = (np.abs(np.array(powers)[1:]) + np.abs(np.array(powers)[:-1])) / 2.0
        q_avg = (np.array(hgp_totals)[1:] + np.array(hgp_totals)[:-1]) / 2.0
        t_avg_seg = (np.array(temps)[1:] + np.array(temps)[:-1]) / 2.0
        total_dur = float(t_arr[-1] - t_arr[0])
        energy_wh = float(np.sum(p_avg * dt_arr) / 3600.0)
        heat_j = float(np.sum(q_avg * dt_arr))
        stage_avg_temp = float(np.sum(t_avg_seg * dt_arr) / total_dur) if total_dur > 0.0 else float(np.mean(temps))
    else:
        energy_wh, heat_j = 0.0, 0.0
        stage_avg_temp = float(temps[0]) if n_pts else T_cur

    summary = {
        'Cycle': cycle_idx + 1,
        'Stage': stage_idx + 1,
        'Mode': mode,
        'Start_Time_s': float(t_arr[0]) if n_pts else t_global_start,
        'End_Time_s': float(t_arr[-1]) if n_pts else t_global_start,
        'Duration_s': float(t_arr[-1] - t_arr[0]) if n_pts > 1 else 0.0,
        'Start_SOC': float(socs[0]) if n_pts else soc_cur,
        'End_SOC': float(socs[-1]) if n_pts else soc_cur,
        'Start_Volt_V': float(udynas[0]) if n_pts else np.nan,
        'End_Volt_V': float(udynas[-1]) if n_pts else np.nan,
        'Min_Volt_V': float(np.min(udynas)) if n_pts else np.nan,
        'Max_Volt_V': float(np.max(udynas)) if n_pts else np.nan,
        'Start_Temp_degC': float(temps[0]) if n_pts else T_cur,
        'End_Temp_degC': float(temps[-1]) if n_pts else T_cur,
        'Max_Temp_degC': float(np.max(temps)) if n_pts else T_cur,
        'Stage_Avg_Temp_degC': stage_avg_temp,
        'Energy_Wh': energy_wh,
        'Heat_J': heat_j,
        'SOH': soh,
        'R_Factor': r_factor,
        'Stop_Reason': stop_reason,
    }

    next_state = {
        't_global': float(times[-1]) if n_pts else t_global_start,
        'soc': soc_cur,
        'T': T_cur,
        'T_amb': T_amb,
        'cooling_on': cooling_on,
        'u_dyna': u_dyna_prev,
        'u_ref': u_ref_prev,
    }
    return stage_df, summary, next_state


# ==============================================================================
# 5. Full Multi-Stage Simulation Pipeline
# ==============================================================================

def run_hgp_simulation(
    input_path: str,
    cond_list: List[Dict[str, Any]],
    cfg: HGPConfig,
    thermal_stepper: Optional[Any] = None,
    norm_model: Any = None,
    progress_cb: Optional[Callable[[str], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Dict[str, Any]:
    """Run full multi-stage / multi-cycle electro-thermal simulation."""
    t0 = time.perf_counter()
    if progress_cb:
        progress_cb(f"[HGP] 正在预加载输入数据: {os.path.basename(input_path)} ...")
    tables, profiles = preload_all_data(input_path, cfg)

    if thermal_stepper is None:
        thermal_stepper = Lumped0DThermalStepper(cfg)

    state = {
        't_global': 0.0,
        'soc': float(cfg.soc_init),
        'T': float(cfg.t_init),
        'T_amb': float(cfg.t_amb),
        'cooling_on': update_cooling_state(cfg.t_init, False, cfg),
        'u_dyna': None,
        'u_ref': None,
    }

    all_dfs: List[pd.DataFrame] = []
    summaries: List[Dict[str, Any]] = []
    cycle_idx = 0
    seen_dc_in_cycle = False

    n_stages = len(cond_list)
    if progress_cb:
        progress_cb(f"[HGP] 启动多阶段电-热耦合仿真 (共 {n_stages} 个阶段)...")

    for stage_idx, raw_cond in enumerate(cond_list):
        if cancel_check and cancel_check():
            if progress_cb:
                progress_cb("[HGP] 用户请求中止仿真。")
            break
        spec = normalize_condition(raw_cond, cfg)
        if spec['category'] == 'CC' and seen_dc_in_cycle:
            cycle_idx += 1
            seen_dc_in_cycle = False
        elif spec['category'] == 'DC':
            seen_dc_in_cycle = True

        stage_df, summary, state = run_stage(
            spec, state, tables, profiles, cycle_idx, stage_idx,
            cfg=cfg, thermal_stepper=thermal_stepper, norm_model=norm_model,
            cancel_check=cancel_check,
        )
        all_dfs.append(stage_df)
        summaries.append(summary)

        if progress_cb:
            progress_cb(
                f"  [阶段 {stage_idx + 1}/{n_stages} | 第 {summary['Cycle']} 圈 | {summary['Mode']}] "
                f"t: {summary['Start_Time_s']:.1f}->{summary['End_Time_s']:.1f}s, "
                f"SOC: {summary['Start_SOC']*100:.1f}%->{summary['End_SOC']*100:.1f}%, "
                f"V: {summary['Start_Volt_V']:.3f}->{summary['End_Volt_V']:.3f}V, "
                f"T: {summary['Start_Temp_degC']:.2f}->{summary['End_Temp_degC']:.2f}C, "
                f"Stop: {summary['Stop_Reason']}"
            )
        if summary['Stop_Reason'] == 'Cancelled':
            break

    out_df = pd.concat(all_dfs, ignore_index=True) if all_dfs else pd.DataFrame()
    summary_df = pd.DataFrame(summaries)

    if not summary_df.empty:
        cycle_avg_map = {}
        for cyc_id, grp in summary_df.groupby('Cycle', sort=False):
            tot_dur = grp['Duration_s'].sum()
            if tot_dur > 0:
                cyc_avg = float((grp['Stage_Avg_Temp_degC'] * grp['Duration_s']).sum() / tot_dur)
            else:
                cyc_avg = float(grp['Stage_Avg_Temp_degC'].mean())
            cycle_avg_map[cyc_id] = cyc_avg
        summary_df['Cycle_Avg_Temp_degC'] = summary_df['Cycle'].map(cycle_avg_map)

    thermal_extra = thermal_stepper.finalize() if hasattr(thermal_stepper, 'finalize') else {}
    elapsed = time.perf_counter() - t0
    if progress_cb:
        progress_cb(f"[HGP] 仿真完成，耗时 {elapsed:.2f} s，总数据点: {len(out_df)}")

    return {
        'mode': 'hgp',
        'out_df': out_df,
        'summary_df': summary_df,
        'elapsed': elapsed,
        'config': cfg,
        'thermal': thermal_extra,
    }


def save_hgp_results_to_excel(
    out_df: pd.DataFrame,
    summary_df: pd.DataFrame,
    output_path: str,
    max_rows: int = 1000000,
    overflow_mode: str = 'downsample',
    thermal_stats_df: Optional[pd.DataFrame] = None,
) -> bool:
    """Save CycleOut, CycleSummary, and optional 3D thermal stats to Excel."""
    dir_name = os.path.dirname(output_path)
    if dir_name and not os.path.exists(dir_name):
        os.makedirs(dir_name, exist_ok=True)

    sheets_to_write = []
    if out_df is not None and not out_df.empty:
        total_rows = len(out_df)
        if total_rows <= max_rows:
            sheets_to_write.append(('CycleOut', out_df))
        elif str(overflow_mode).strip().lower() == 'split_sheets':
            n_sheets = int(np.ceil(total_rows / max_rows))
            for idx in range(n_sheets):
                s = idx * max_rows
                e = min((idx + 1) * max_rows, total_rows)
                sheets_to_write.append((f'CycleOut_{idx + 1}', out_df.iloc[s:e]))
        else:
            stride = int(np.ceil(total_rows / max_rows))
            sheets_to_write.append(('CycleOut', out_df.iloc[::stride].reset_index(drop=True)))

    with pd.ExcelWriter(output_path, engine='openpyxl', mode='w') as writer:
        for s_name, s_df in sheets_to_write:
            s_df.to_excel(writer, sheet_name=s_name, index=False)
        if summary_df is not None and not summary_df.empty:
            summary_df.to_excel(writer, sheet_name='CycleSummary', index=False)
        if thermal_stats_df is not None and not thermal_stats_df.empty:
            thermal_stats_df.to_excel(writer, sheet_name='Thermal3D_Stats', index=False)
    return True
