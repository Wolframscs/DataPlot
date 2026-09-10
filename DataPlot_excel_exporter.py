import os
import openpyxl
import pandas as pd
import numpy as np
from PySide6.QtWidgets import QMessageBox

class ExcelExporterMixin:
    def save_cycle_compare_data(self):
        """保存循环对比的数据到 Excel"""
        try:
            cycle_col_name = self.cycle_col.get()
            step_col_name = self.step_col.get()
            time_col_name = self.time_col.get()
            voltage_col_name = self.voltage_col.get()
            current_col_name = self.current_col.get()

            # Ensure columns exist
            for name, col in [("循环列", cycle_col_name), ("工步列", step_col_name), 
                              ("时间列", time_col_name), ("电压列", voltage_col_name), 
                              ("电流列", current_col_name)]:
                if not col or col not in self.result_df.columns:
                    QMessageBox.warning(self, "警告", f"未找到{name} '{col}'，无法计算保存。")
                    return

            try:
                max_cycle = int(pd.to_numeric(self.result_df[cycle_col_name], errors='coerce').max())
            except Exception:
                max_cycle = 1

            cycle_str = self.cycle_compare_range_var.get()
            cycles = self.parse_cycles(cycle_str, max_cycle)
            if not cycles:
                QMessageBox.warning(self, "警告", "未指定有效的循环号范围。")
                return

            step_val = self.step_filter.get()

            # 时长筛选 (tmin ~ tmax)
            tmin_str = self.time_filter_min_var.get().strip() if hasattr(self, 'time_filter_min_var') else ""
            tmax_str = self.time_filter_max_var.get().strip() if hasattr(self, 'time_filter_max_var') else ""
            filter_mode = self.time_filter_mode_var.get() if hasattr(self, 'time_filter_mode_var') else "保留区间"
            try:
                tmin_val = float(tmin_str) if tmin_str else None
            except ValueError:
                tmin_val = None
            try:
                tmax_val = float(tmax_str) if tmax_str else None
            except ValueError:
                tmax_val = None

            if tmin_val is not None or tmax_val is not None:
                kept_cycles, removed_reasons, _ = self.filter_cycles_by_duration(
                    self.result_df, cycle_col_name, step_col_name, time_col_name,
                    step_val, tmin_val, tmax_val, filter_mode, target_cycles=cycles
                )
                if removed_reasons and hasattr(self, 'update_status'):
                    removed_summary = ", ".join([f"C{c} ({reason})" for c, reason in removed_reasons.items()])
                    self.update_status(f"时间筛选：导出已剔除 {len(removed_reasons)} 个循环 -> {removed_summary}")
                cycles = kept_cycles
                if not cycles:
                    QMessageBox.warning(self, "警告", "经过时间筛选后，没有符合条件的循环可导出。")
                    return

            filter_type = self.filter_type_var.get()
            try:
                w_size = int(self.filter_window_var.get())
            except ValueError:
                w_size = 15
            if w_size % 2 == 0: w_size += 1
            try:
                p_order = int(self.sg_poly_var.get())
            except ValueError:
                p_order = 2
            if p_order >= w_size: p_order = w_size - 1

            v_scale = self.safe_float_convert(self.voltage_scale_var.get(), 1.0)
            c_scale = self.safe_float_convert(self.current_scale_var.get(), 1.0)
            cc_polarity = self.cc_polarity_var.get()

            plot_type = self.current_compare_type.get() if hasattr(self, 'current_compare_type') else 'regular'
            dqdv_mode = self.dqdv_mode_var.get() if hasattr(self, 'dqdv_mode_var') else "去重"

            # 如果是 dQ/dV 或 dV/dQ，预先计算全体选中循环的电压范围 (按 v_scale 缩放后)
            overall_u_min = float('inf')
            overall_u_max = float('-inf')
            if plot_type in ['dqdv', 'dvdq']:
                for c in cycles:
                    df_c_pre = self.result_df[self.result_df[cycle_col_name] == c]
                    if step_val != "全部" and step_val != "":
                        try:
                            exact_mask = (df_c_pre[step_col_name].astype(str) == str(step_val))
                            if not exact_mask.any():
                                exact_mask = df_c_pre[step_col_name].astype(str).str.contains(str(step_val), regex=False)
                            df_c_pre = df_c_pre[exact_mask]
                        except Exception:
                            df_c_pre = df_c_pre[df_c_pre[step_col_name].astype(str) == str(step_val)]
                    if not df_c_pre.empty and voltage_col_name in df_c_pre.columns:
                        u_series = pd.to_numeric(df_c_pre[voltage_col_name], errors='coerce').dropna() * v_scale
                        if not u_series.empty:
                            c_min = u_series.min()
                            c_max = u_series.max()
                            if c_min < overall_u_min: overall_u_min = c_min
                            if c_max > overall_u_max: overall_u_max = c_max
                if overall_u_min == float('inf') or overall_u_max == float('-inf'):
                    overall_u_min, overall_u_max = 2.5, 4.2

                vmin_str = self.dqdv_vmin_var.get().strip() if hasattr(self, 'dqdv_vmin_var') else ""
                vmax_str = self.dqdv_vmax_var.get().strip() if hasattr(self, 'dqdv_vmax_var') else ""
                npts_str = self.dqdv_npts_var.get().strip() if hasattr(self, 'dqdv_npts_var') else "100"
                try: vmin_val = float(vmin_str)
                except ValueError: vmin_val = overall_u_min
                try: vmax_val = float(vmax_str)
                except ValueError: vmax_val = overall_u_max
                try:
                    n_pts = int(npts_str)
                    if n_pts < 5: n_pts = 100
                except ValueError: n_pts = 100
                if vmin_val >= vmax_val:
                    vmin_val = overall_u_min
                    vmax_val = overall_u_max

            # 收集每个循环的数据表
            save_dfs = []
            for c in cycles:
                df_c = self.result_df[self.result_df[cycle_col_name] == c].copy()
                if v_scale != 1.0 and voltage_col_name in df_c.columns:
                    df_c[voltage_col_name] = pd.to_numeric(df_c[voltage_col_name], errors='coerce') * v_scale
                if c_scale != 1.0 and current_col_name in df_c.columns:
                    df_c[current_col_name] = pd.to_numeric(df_c[current_col_name], errors='coerce') * c_scale

                if step_val != "全部" and step_val != "":
                    df_c = self.compute_step_time(df_c, cycle_col_name, step_col_name, time_col_name)
                    exact_mask = (df_c[step_col_name].astype(str) == str(step_val))
                    if not exact_mask.any():
                        exact_mask = df_c[step_col_name].astype(str).str.contains(str(step_val), regex=False)
                    df_c = df_c[exact_mask]

                if df_c.empty:
                    continue

                df_c = df_c.sort_values(by=time_col_name)

                time_diff_col = f"{time_col_name}_时间差(s)"
                if time_diff_col in df_c.columns:
                    t_vals = pd.to_numeric(df_c[time_diff_col], errors='coerce').values
                else:
                    t_vals = pd.to_numeric(df_c[time_col_name], errors='coerce').values
                
                valid_t = ~np.isnan(t_vals)
                if not np.any(valid_t):
                    continue
                t_vals = t_vals[valid_t]
                df_c = df_c.iloc[valid_t]

                t_rel = t_vals - t_vals[0]

                # 工步时间
                if '工步时间' not in df_c.columns:
                    step_t_df = self.compute_step_time(df_c, cycle_col_name, step_col_name, time_col_name)
                    step_col_candidate = '工步时间差(s)' if '工步时间差(s)' in step_t_df.columns else ('工步时间' if '工步时间' in step_t_df.columns else None)
                    if step_col_candidate:
                        df_c['工步时间'] = step_t_df[step_col_candidate]
                    else:
                        df_c['工步时间'] = t_rel

                # Check if we should group by step
                use_step_grouping = False
                if step_col_name in df_c.columns:
                    try:
                        unique_steps = df_c[step_col_name].dropna().unique()
                        if len(unique_steps) <= 100:
                            use_step_grouping = True
                    except Exception:
                        pass

                multiplier = self.get_current_multiplier(df_c, current_col_name, voltage_col_name, step_col_name, cc_polarity)

                # Capacity integration
                cap_vals = np.zeros(len(df_c))
                dt = np.diff(t_rel, prepend=0)
                curr_vals = pd.to_numeric(df_c[current_col_name], errors='coerce').fillna(0).values

                if use_step_grouping:
                    for s in unique_steps:
                        mask = df_c[step_col_name] == s
                        if not np.any(mask):
                            continue
                        step_curr = curr_vals[mask]
                        step_dt = dt[mask]
                        step_dq = (step_curr * multiplier * step_dt) / 3600.0
                        cap_vals[mask] = np.cumsum(step_dq)
                else:
                    dq = (curr_vals * multiplier * dt) / 3600.0
                    cap_vals = np.cumsum(dq)

                curr_vals_raw = pd.to_numeric(df_c[current_col_name], errors='coerce').fillna(0).values
                non_zero_mask = curr_vals_raw != 0

                if plot_type in ['dqdv', 'dvdq']:
                    if not np.any(non_zero_mask):
                        continue
                    df_c_sub = df_c[non_zero_mask]
                    u_raw = pd.to_numeric(df_c_sub[voltage_col_name], errors='coerce').values
                    q_raw = cap_vals[non_zero_mask]
                    t_sub = t_rel[non_zero_mask]
                    step_t_sub = pd.to_numeric(df_c_sub['工步时间'], errors='coerce').fillna(0).values

                    valid_mask = ~np.isnan(u_raw) & ~np.isnan(q_raw)
                    u_valid = u_raw[valid_mask]
                    q_valid = q_raw[valid_mask]
                    t_valid_sub = t_sub[valid_mask]
                    step_t_valid_sub = step_t_sub[valid_mask]

                    if len(u_valid) < 5:
                        continue

                    # A. 去重网格插值求导 (与绘图引擎完全一致)
                    if dqdv_mode in ["去重", "对比"]:
                        _, unique_indices = np.unique(u_valid, return_index=True)
                        unique_indices = np.sort(unique_indices)
                        u_clean = u_valid[unique_indices]
                        q_clean = q_valid[unique_indices]
                        t_clean = t_valid_sub[unique_indices]
                        step_t_clean = step_t_valid_sub[unique_indices]

                        v_grid = np.linspace(vmin_val, vmax_val, n_pts)
                        sort_idx = np.argsort(u_clean)
                        u_sorted = u_clean[sort_idx]
                        q_sorted = q_clean[sort_idx]
                        t_sorted = t_clean[sort_idx]
                        step_t_sorted = step_t_clean[sort_idx]

                        try:
                            from scipy.interpolate import interp1d
                            q_interp = interp1d(u_sorted, q_sorted, kind='linear', fill_value="extrapolate")(v_grid)
                            t_interp = interp1d(u_sorted, t_sorted, kind='linear', fill_value="extrapolate")(v_grid)
                            step_t_interp = interp1d(u_sorted, step_t_sorted, kind='linear', fill_value="extrapolate")(v_grid)
                        except Exception:
                            q_interp = np.interp(v_grid, u_sorted, q_sorted)
                            t_interp = np.interp(v_grid, u_sorted, t_sorted)
                            step_t_interp = np.interp(v_grid, u_sorted, step_t_sorted)

                        dq_d = np.gradient(q_interp)
                        dv_d = np.gradient(v_grid)
                        with np.errstate(divide='ignore', invalid='ignore'):
                            y_raw_dqdv = dq_d / dv_d
                            y_raw_dvdq = dv_d / dq_d
                        y_clean_dqdv = np.nan_to_num(y_raw_dqdv, nan=0.0, posinf=0.0, neginf=0.0)
                        y_clean_dvdq = np.nan_to_num(y_raw_dvdq, nan=0.0, posinf=0.0, neginf=0.0)
                        dq_dv_smooth = self.apply_filtering(y_clean_dqdv, filter_type, w_size, p_order)
                        dv_dq_smooth = self.apply_filtering(y_clean_dvdq, filter_type, w_size, p_order)

                        prefix = f"C{c}_" if dqdv_mode == "去重" else f"C{c}_去重_"
                        c_dict = {
                            f"{prefix}循环号": [c] * len(v_grid),
                            f"{prefix}电压/V": v_grid,
                            f"{prefix}累计容量/Ah": q_interp,
                            f"{prefix}时间/s": t_interp,
                            f"{prefix}工步时间/s": step_t_interp,
                            f"{prefix}dQ_dV/(Ah_V)": dq_dv_smooth,
                            f"{prefix}dV_dQ/(V_Ah)": dv_dq_smooth
                        }
                        c_df = pd.DataFrame(c_dict)
                        save_dfs.append(c_df)

                    # B. 原始点求导
                    if dqdv_mode in ["原始", "对比"]:
                        dq_r = np.gradient(q_valid)
                        dv_r = np.gradient(u_valid)
                        with np.errstate(divide='ignore', invalid='ignore'):
                            y_raw_dqdv_r = dq_r / dv_r
                            y_raw_dvdq_r = dv_r / dq_r
                        y_clean_dqdv_r = np.nan_to_num(y_raw_dqdv_r, nan=0.0, posinf=0.0, neginf=0.0)
                        y_clean_dvdq_r = np.nan_to_num(y_raw_dvdq_r, nan=0.0, posinf=0.0, neginf=0.0)
                        dq_dv_r_smooth = self.apply_filtering(y_clean_dqdv_r, filter_type, w_size, p_order)
                        dv_dq_r_smooth = self.apply_filtering(y_clean_dvdq_r, filter_type, w_size, p_order)

                        prefix = f"C{c}_" if dqdv_mode == "原始" else f"C{c}_原始_"
                        c_dict = {
                            f"{prefix}循环号": [c] * len(u_valid),
                            f"{prefix}工步": df_c_sub[step_col_name].values[valid_mask],
                            f"{prefix}时间/s": t_valid_sub,
                            f"{prefix}工步时间/s": step_t_valid_sub,
                            f"{prefix}累计容量/Ah": q_valid,
                            f"{prefix}电压/V": u_valid,
                            f"{prefix}电流/A": curr_vals_raw[non_zero_mask][valid_mask],
                            f"{prefix}dQ_dV/(Ah_V)": dq_dv_r_smooth,
                            f"{prefix}dV_dQ/(V_Ah)": dv_dq_r_smooth
                        }
                        c_df = pd.DataFrame(c_dict)
                        save_dfs.append(c_df)
                else:
                    # 常规对比模式导出
                    dq_r = np.gradient(cap_vals)
                    u_vals = pd.to_numeric(df_c[voltage_col_name], errors='coerce').values
                    dv_r = np.gradient(u_vals)
                    with np.errstate(divide='ignore', invalid='ignore'):
                        y_raw_dqdv = dq_r / dv_r
                        y_raw_dvdq = dv_r / dq_r
                    y_clean_dqdv = np.nan_to_num(y_raw_dqdv, nan=0.0, posinf=0.0, neginf=0.0)
                    y_clean_dvdq = np.nan_to_num(y_raw_dvdq, nan=0.0, posinf=0.0, neginf=0.0)
                    dq_dv_smooth = self.apply_filtering(y_clean_dqdv, filter_type, w_size, p_order)
                    dv_dq_smooth = self.apply_filtering(y_clean_dvdq, filter_type, w_size, p_order)

                    c_dict = {
                        f"C{c}_循环号": [c] * len(df_c),
                        f"C{c}_工步": df_c[step_col_name].values,
                        f"C{c}_时间/s": t_rel,
                        f"C{c}_工步时间/s": df_c['工步时间'].values,
                        f"C{c}_累计容量/Ah": cap_vals,
                        f"C{c}_电压/V": u_vals,
                        f"C{c}_电流/A": pd.to_numeric(df_c[current_col_name], errors='coerce').values,
                        f"C{c}_dQ_dV/(Ah_V)": dq_dv_smooth,
                        f"C{c}_dV_dQ/(V_Ah)": dv_dq_smooth
                    }
                    # 包含用户在 Y1, Y2, Y3 中选中的其他可能列
                    all_selected_y = []
                    for y_list in self.y_selections:
                        for y_col in y_list:
                            if y_col and y_col not in all_selected_y:
                                all_selected_y.append(y_col)
                    for col in all_selected_y:
                        if col in df_c.columns and col not in [voltage_col_name, current_col_name, step_col_name, time_col_name]:
                            cleaned_col_name = self.clean_legend_label(col)
                            c_dict[f"C{c}_{cleaned_col_name}"] = df_c[col].values

                    c_df = pd.DataFrame(c_dict)
                    save_dfs.append(c_df)

            if not save_dfs:
                QMessageBox.warning(self, "警告", "没有可保存的计算数据。")
                return

            save_dfs_reset = [df.reset_index(drop=True) for df in save_dfs]
            final_df = pd.concat(save_dfs_reset, axis=1)

            opened_file = self.file_path.get()
            base_name = "BATTERY_Cycle_Compare_Data.xlsx"
            if opened_file and os.path.exists(opened_file):
                save_dir = os.path.dirname(os.path.abspath(opened_file))
                file_name = os.path.join(save_dir, base_name)
            else:
                file_name = base_name

            next_sheet = "sheet1"
            if os.path.exists(file_name):
                try:
                    wb = openpyxl.load_workbook(file_name, read_only=True, keep_links=False)
                    sheet_names = wb.sheetnames
                    wb.close()
                    import re
                    max_num = 0
                    for name in sheet_names:
                        match = re.match(r'^sheet(\d+)$', name, re.IGNORECASE)
                        if match:
                            num = int(match.group(1))
                            if num > max_num: max_num = num
                    next_sheet = f"sheet{max_num + 1}"
                except Exception:
                    pass

            if os.path.exists(file_name):
                with pd.ExcelWriter(file_name, mode='a', engine='openpyxl', if_sheet_exists='replace') as writer:
                    final_df.to_excel(writer, sheet_name=next_sheet, index=False)
            else:
                with pd.ExcelWriter(file_name, mode='w', engine='openpyxl') as writer:
                    final_df.to_excel(writer, sheet_name=next_sheet, index=False)

            QMessageBox.information(self, "成功", f"循环对比数据已保存至 {os.path.abspath(file_name)} 中的 {next_sheet}！")

        except Exception as e:
            QMessageBox.critical(self, "错误", f"保存循环对比数据失败: {str(e)}")

    def save_plot_data(self):
        """将当前绘图数据保存为 xlsx 文件"""
        try:
            if self.result_df is None:
                QMessageBox.warning(self, "警告", "当前无有效数据，请先载入并计算数据！")
                return
            
            if self.file_type.get() == "battery" and self.cycle_compare_var.get():
                self.save_cycle_compare_data()
                return
            
            x_col = self.x_axis.get()
            if not x_col:
                QMessageBox.warning(self, "警告", "请先选择 X 轴数据！")
                return
                
            y1_cols = self.y_selections[0]
            y2_cols = self.y_selections[1]
            y3_cols = self.y_selections[2]
            all_y_cols = y1_cols + y2_cols + y3_cols
            
            if not all_y_cols:
                QMessageBox.warning(self, "警告", "请先选择至少一个 Y 轴进行绘图！")
                return
                
            df_to_plot = self.result_df.copy()
            
            if self.file_type.get() == "battery":
                cycle_col_name = self.cycle_col.get()
                step_col_name = self.step_col.get()
                time_col_name = self.time_col.get()
                cycle_val = self.cycle_filter.get()
                step_val = self.step_filter.get()

                # 时长筛选 (tmin ~ tmax)
                tmin_str = self.time_filter_min_var.get().strip() if hasattr(self, 'time_filter_min_var') else ""
                tmax_str = self.time_filter_max_var.get().strip() if hasattr(self, 'time_filter_max_var') else ""
                filter_mode = self.time_filter_mode_var.get() if hasattr(self, 'time_filter_mode_var') else "保留区间"
                try:
                    tmin_val = float(tmin_str) if tmin_str else None
                except ValueError:
                    tmin_val = None
                try:
                    tmax_val = float(tmax_str) if tmax_str else None
                except ValueError:
                    tmax_val = None

                if tmin_val is not None or tmax_val is not None:
                    kept_cycles, removed_reasons, _ = self.filter_cycles_by_duration(
                        df_to_plot, cycle_col_name, step_col_name, time_col_name,
                        step_val, tmin_val, tmax_val, filter_mode
                    )
                    if removed_reasons and hasattr(self, 'update_status'):
                        removed_summary = ", ".join([f"C{c} ({reason})" for c, reason in removed_reasons.items()])
                        self.update_status(f"时间筛选：导出已剔除 {len(removed_reasons)} 个循环 -> {removed_summary}")
                    if cycle_col_name and cycle_col_name in df_to_plot.columns:
                        df_to_plot = df_to_plot[df_to_plot[cycle_col_name].isin(kept_cycles)]
                
                if cycle_col_name and cycle_col_name in df_to_plot.columns:
                    if cycle_val != "全部" and cycle_val != "":
                        try:
                            try:
                                target_val = int(cycle_val)
                                df_to_plot = df_to_plot[df_to_plot[cycle_col_name] == target_val]
                            except ValueError:
                                try:
                                    target_val = float(cycle_val)
                                    df_to_plot = df_to_plot[df_to_plot[cycle_col_name] == target_val]
                                except ValueError:
                                    df_to_plot = df_to_plot[df_to_plot[cycle_col_name].astype(str) == str(cycle_val)]
                        except Exception:
                            pass
                if step_col_name and step_col_name in df_to_plot.columns:
                    if step_val != "全部" and step_val != "":
                        df_to_plot = self.compute_step_time(df_to_plot, cycle_col_name, step_col_name, time_col_name)
                        try:
                            exact_mask = (df_to_plot[step_col_name].astype(str) == str(step_val))
                            if not exact_mask.any():
                                exact_mask = df_to_plot[step_col_name].astype(str).str.contains(str(step_val), regex=False)
                            df_to_plot = df_to_plot[exact_mask]
                        except Exception as e:
                            if hasattr(self, 'logger') and self.logger:
                                self.logger.error(f"工步导出筛选异常: {str(e)}")
            
            if df_to_plot.empty:
                QMessageBox.warning(self, "警告", "筛选后的绘图数据为空，无法保存！")
                return
                
            selected_cols = []
            if x_col and x_col in df_to_plot.columns:
                selected_cols.append(x_col)
            for col in all_y_cols:
                if col and col in df_to_plot.columns and col not in selected_cols:
                    selected_cols.append(col)
                    
            if not selected_cols:
                QMessageBox.warning(self, "警告", "未在数据中匹配到选中的列！")
                return
                
            save_df = df_to_plot[selected_cols].copy()
            save_df.columns = [self.clean_legend_label(col) for col in save_df.columns]
            
            opened_file = self.file_path.get()
            panel_prefix = "FLOEFD"
            if self.file_type.get() == "processed":
                panel_prefix = "GENERAL"
            elif self.file_type.get() == "battery":
                panel_prefix = "BATTERY"
                
            base_name = f"{panel_prefix}_Plot_Data.xlsx"
            if opened_file and os.path.exists(opened_file):
                save_dir = os.path.dirname(os.path.abspath(opened_file))
                file_name = os.path.join(save_dir, base_name)
            else:
                file_name = base_name
                
            next_sheet = "sheet1"
            
            if os.path.exists(file_name):
                try:
                    wb = openpyxl.load_workbook(file_name, read_only=True, keep_links=False)
                    sheet_names = wb.sheetnames
                    wb.close()
                    
                    import re
                    max_num = 0
                    for name in sheet_names:
                        match = re.match(r'^sheet(\d+)$', name, re.IGNORECASE)
                        if match:
                            num = int(match.group(1))
                            if num > max_num:
                                max_num = num
                    next_sheet = f"sheet{max_num + 1}"
                except Exception as e:
                    self.logger.error(f"读取已有 Excel 的 sheet 结构失败: {str(e)}")
            
            if os.path.exists(file_name):
                with pd.ExcelWriter(file_name, mode='a', engine='openpyxl', if_sheet_exists='replace') as writer:
                    save_df.to_excel(writer, sheet_name=next_sheet, index=False)
            else:
                with pd.ExcelWriter(file_name, mode='w', engine='openpyxl') as writer:
                    save_df.to_excel(writer, sheet_name=next_sheet, index=False)
                    
            QMessageBox.information(self, "成功", f"绘图数据已保存至 {os.path.abspath(file_name)} 中的 {next_sheet}！")
            if hasattr(self, 'logger') and self.logger:
                self.logger.info(f"保存绘图数据成功: {file_name} -> {next_sheet}")
            
        except Exception as e:
            if hasattr(self, 'logger') and self.logger:
                self.logger.error(f"保存绘图数据时发生异常: {str(e)}")
            QMessageBox.critical(self, "错误", f"保存数据失败: {str(e)}")
