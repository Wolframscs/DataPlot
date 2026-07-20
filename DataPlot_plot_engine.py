import time
import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
from PySide6.QtWidgets import QMessageBox
from PySide6.QtCore import QTimer

class PlotEngineMixin:
    def clean_legend_label(self, label):
        """清洗图例标签：
        - 如果是 battery 模式，移除末尾的单位括号（如 (℃), (A), (V) 等）
        - 如果是其他模式，若包含“温度”，则删除“温度”及后面的字符
        """
        label_str = str(label)
        if self.file_type.get() == "battery":
            for char in ['(', '（']:
                if char in label_str:
                    pos = label_str.find(char)
                    return label_str[:pos].rstrip()
            return label_str
        else:
            if "温度" in label_str:
                pos = label_str.find("温度")
                return label_str[:pos].rstrip()
            return label_str

    def resolve_val(self, val_val, default_min, default_max):
        if val_val is None:
            return None
        val_str = str(val_val).strip()
        if not val_str:
            return None
        val_str_lower = val_str.lower()
        if val_str_lower == 'min':
            return default_min
        elif val_str_lower == 'max':
            return default_max
        else:
            try:
                return float(val_str_lower)
            except ValueError:
                return None

    def delayed_update(self, *args):
        """延迟更新图表（防抖）"""
        if getattr(self, '_is_loading_settings', False):
            return
        if hasattr(self, '_update_timer') and self._update_timer is not None:
            try:
                self._update_timer.stop()
            except Exception:
                pass
        self._update_timer = QTimer()
        self._update_timer.setSingleShot(True)
        self._update_timer.timeout.connect(self.update_plot)
        self._update_timer.start(300)

    def on_cycle_compare_toggle(self):
        self.update_file_type()
        self.update_listboxes()
        self.delayed_update()

    def set_compare_type(self, compare_type):
        self.current_compare_type.set(compare_type)
        self.delayed_update()

    def sync_compare_x(self, *args):
        if getattr(self, '_is_syncing_x', False):
            return
        self._is_syncing_x = True
        try:
            val = self.compare_x_var.get()
            if self.x_axis.get() != val:
                self.x_axis.set(val)
        finally:
            self._is_syncing_x = False

    def sync_regular_x(self, *args):
        if getattr(self, '_is_syncing_x', False):
            return
        if self.file_type.get() == "battery" and self.cycle_compare_var.get():
            self._is_syncing_x = True
            try:
                val = self.x_axis.get()
                if self.compare_x_var.get() != val:
                    self.compare_x_var.set(val)
            finally:
                self._is_syncing_x = False

    def plot_cycle_compare(self, plot_type='regular'):
        try:
            if self.result_df is None:
                return

            cycle_col_name = self.cycle_col.get()
            step_col_name = self.step_col.get()
            time_col_name = self.time_col.get()
            voltage_col_name = self.voltage_col.get()
            current_col_name = self.current_col.get()

            for name, col in [("循环列", cycle_col_name), ("工步列", step_col_name), 
                              ("时间列", time_col_name), ("电压列", voltage_col_name), 
                              ("电流列", current_col_name)]:
                if not col or col not in self.result_df.columns:
                    self.update_status(f"错误: 未找到{name} '{col}'，请检查映射关系。")
                    return

            try:
                max_cycle = int(pd.to_numeric(self.result_df[cycle_col_name], errors='coerce').max())
            except Exception:
                max_cycle = 1

            cycle_str = self.cycle_compare_range_var.get()
            cycles = self.parse_cycles(cycle_str, max_cycle)
            if not cycles:
                self.update_status("未指定要绘制的循环号。")
                return

            step_val = self.step_filter.get()

            try:
                time_step = float(self.time_step_var.get())
            except ValueError:
                time_step = 10.0
                self.time_step_var.set("10")

            filter_type = self.filter_type_var.get()
            try:
                w_size = int(self.filter_window_var.get())
            except ValueError:
                w_size = 15
                self.filter_window_var.set("15")
            if w_size % 2 == 0:
                w_size += 1
                self.filter_window_var.set(str(w_size))

            try:
                p_order = int(self.sg_poly_var.get())
            except ValueError:
                p_order = 2
                self.sg_poly_var.set("2")
            if p_order >= w_size:
                p_order = w_size - 1
                self.sg_poly_var.set(str(p_order))

            has_scipy = True
            try:
                from scipy.signal import savgol_filter
            except ImportError:
                has_scipy = False

            self.fig.clf()
            w_px = self.canvas.width()
            h_px = self.canvas.height()
            if w_px > 1 and h_px > 1:
                self.fig.set_size_inches(w_px / self.fig.dpi, h_px / self.fig.dpi, forward=False)
            
            self.ax = self.fig.add_subplot(111)

            font_size = int(self.safe_float_convert(self.font_size.get(), 18.0))
            font_family = self.font_family.get()
            plt.rcParams['font.sans-serif'] = [font_family] + [f for f in plt.rcParams['font.sans-serif'] if f != font_family]
            plt.rcParams['axes.unicode_minus'] = False

            def set_axis_style(ax):
                ax.tick_params(axis='both', direction='in', width=float(self.frame_width.get()), length=6, 
                              labelsize=font_size, color='black')
                for spine in ax.spines.values():
                    spine.set_linewidth(float(self.frame_width.get()))
                    spine.set_color('black')

            all_lines = []
            all_labels = []
            all_y_plots = []

            y1_data = self.y_selections[0]
            y2_data = self.y_selections[1]
            y3_data = self.y_selections[2]

            if plot_type == 'regular':
                if not any([y1_data, y2_data, y3_data]):
                    self.update_status("未选择Y轴绘图列。")
                    return
                set_axis_style(self.ax)
                ax2 = None
                ax3 = None
                if y2_data:
                    ax2 = self.ax.twinx()
                    ax2.spines['right'].set_position(('outward', 0))
                    set_axis_style(ax2)
                if y3_data:
                    ax3 = self.ax.twinx()
                    ax3.spines['right'].set_position(('outward', 70))
                    set_axis_style(ax3)
            else:
                set_axis_style(self.ax)

            all_x_points = []

            for idx, c in enumerate(cycles):
                df_c = self.result_df[self.result_df[cycle_col_name] == c].copy()
                # Apply scale factors if specified
                v_scale = self.safe_float_convert(self.voltage_scale_var.get(), 1.0)
                c_scale = self.safe_float_convert(self.current_scale_var.get(), 1.0)
                if v_scale != 1.0 and voltage_col_name in df_c.columns:
                    df_c[voltage_col_name] = pd.to_numeric(df_c[voltage_col_name], errors='coerce') * v_scale
                if c_scale != 1.0 and current_col_name in df_c.columns:
                    df_c[current_col_name] = pd.to_numeric(df_c[current_col_name], errors='coerce') * c_scale
                if step_val != "全部" and step_val != "":
                    df_c = self.compute_step_time(df_c, cycle_col_name, step_col_name, time_col_name)
                    df_c = df_c[df_c[step_col_name].astype(str) == str(step_val)]

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

                cc_polarity = self.cc_polarity_var.get()

                use_step_grouping = False
                if step_col_name in df_c.columns:
                    try:
                        unique_steps = df_c[step_col_name].dropna().unique()
                        if len(unique_steps) <= 100:
                            use_step_grouping = True
                    except Exception:
                        pass

                multiplier = self.get_current_multiplier(df_c, current_col_name, voltage_col_name, step_col_name, cc_polarity)

                cap_vals = np.zeros(len(df_c))
                t_rel = t_vals - t_vals[0]
                dt = np.diff(t_rel, prepend=0)
                curr_vals = pd.to_numeric(df_c[current_col_name], errors='coerce').fillna(0).values

                if use_step_grouping:
                    df_c_indices = np.arange(len(df_c))
                    for s in unique_steps:
                        mask = df_c[step_col_name] == s
                        if not np.any(mask):
                            continue
                        step_curr = curr_vals[mask]
                        step_dt = dt[mask]
                        
                        step_dq = (step_curr * multiplier * step_dt) / 3600.0
                        step_cap = np.cumsum(step_dq)
                        cap_vals[mask] = step_cap
                else:
                    dq = (curr_vals * multiplier * dt) / 3600.0
                    cap_vals = np.cumsum(dq)

                if plot_type in ['dqdv', 'dvdq']:
                    curr_vals_raw = pd.to_numeric(df_c[current_col_name], errors='coerce').fillna(0).values
                    non_zero_mask = curr_vals_raw != 0
                    if not np.any(non_zero_mask):
                        continue
                    
                    df_c = df_c[non_zero_mask]
                    cap_vals = cap_vals[non_zero_mask]
                    t_rel = t_rel[non_zero_mask]
                    
                    u_vals = pd.to_numeric(df_c[voltage_col_name], errors='coerce').values
                    
                    target_t = t_rel + time_step
                    j_indices = np.searchsorted(t_rel, target_t)
                    j_indices = np.minimum(j_indices, len(t_rel) - 1)
                    valid = target_t <= t_rel[-1]

                    if not np.any(valid):
                        continue

                    dq_diff = cap_vals[j_indices] - cap_vals
                    du_diff = u_vals[j_indices] - u_vals

                    if plot_type == 'dqdv':
                        y_raw = np.where(du_diff != 0, dq_diff / du_diff, np.nan)
                    else:
                        y_raw = np.where(dq_diff != 0, du_diff / dq_diff, np.nan)

                    y_valid = y_raw[valid]
                    t_valid = t_rel[valid]
                    cap_valid = cap_vals[valid]
                    u_valid = u_vals[valid]

                    s_series = pd.Series(y_valid).interpolate(limit_direction='both').fillna(0)
                    y_clean = s_series.values

                    if filter_type == "Savitzky-Golay":
                        if has_scipy:
                            local_w_size = w_size
                            local_p_order = p_order
                            if len(y_clean) < local_w_size:
                                local_w_size = len(y_clean)
                                if local_w_size % 2 == 0:
                                    local_w_size = max(1, local_w_size - 1)
                                if local_p_order >= local_w_size:
                                    local_p_order = max(0, local_w_size - 1)
                            
                            if local_w_size > local_p_order and local_w_size >= 3:
                                y_plot = savgol_filter(y_clean, window_length=local_w_size, polyorder=local_p_order)
                            else:
                                y_plot = pd.Series(y_clean).rolling(window=min(3, len(y_clean)), center=True, min_periods=1).mean().values
                        else:
                            self.update_status("警告: 未安装 scipy 库，Savitzky-Golay 自动降级为移动平均。")
                            y_plot = pd.Series(y_clean).rolling(window=w_size, center=True, min_periods=1).mean().values
                    elif filter_type == "移动平均":
                        y_plot = pd.Series(y_clean).rolling(window=w_size, center=True, min_periods=1).mean().values
                    elif filter_type == "中值滤波":
                        y_plot = pd.Series(y_clean).rolling(window=w_size, center=True, min_periods=1).median().values
                    else:
                        y_plot = y_clean

                    x_choice = self.compare_x_var.get()
                    if x_choice in ["容量", "容量（计算）"]:
                        x_plot = cap_valid
                        x_label = "Capacity / Ah"
                    elif x_choice in ["循环时间", "循环时间（计算）"]:
                        x_plot = t_valid
                        x_label = "Time / s"
                    elif x_choice == voltage_col_name:
                        x_plot = u_valid
                        x_label = f"{voltage_col_name} / V"
                    elif x_choice == "Index":
                        x_plot = np.arange(len(u_vals))[valid]
                        x_label = "Index"
                    elif x_choice in df_c.columns:
                        x_plot = pd.to_numeric(df_c[x_choice], errors='coerce').values[valid]
                        x_label = x_choice
                    else:
                        x_plot = cap_valid
                        x_label = "Capacity / Ah"

                    color_map = self.color_schemes_dict[self.color_schemes[0].get()]
                    color = plt.cm.tab10(idx % 10) if color_map is None else color_map(idx % color_map.N)
                    
                    line = self.ax.plot(x_plot, y_plot,
                                      label=f"Cycle {c}",
                                      linewidth=self.safe_float_convert(self.line_width.get(), 1.5),
                                      color=color,
                                      linestyle=self.line_styles_dict[self.line_styles[0].get()])
                    all_lines.extend(line)
                    all_labels.append(f"Cycle {c}")
                    all_y_plots.extend(y_plot)
                    all_x_points.extend(x_plot)
                else:
                    df_c_plot = df_c.copy()
                    df_c_plot['Capacity'] = cap_vals
                    df_c_plot['循环时间（计算）'] = t_rel
                    df_c_plot['容量（计算）'] = cap_vals

                    x_col = self.compare_x_var.get()
                    if x_col in ["容量", "容量（计算）"]:
                        x_col = "容量（计算）"
                    elif x_col in ["循环时间", "循环时间（计算）"]:
                        x_col = "循环时间（计算）"

                    if not x_col or x_col not in df_c_plot.columns:
                        self.update_status(f"错误: 未找到X轴 '{x_col}'。")
                        return
                    
                    all_x_points.extend(df_c_plot[x_col].dropna().values)

                    if y1_data:
                        color_map = self.color_schemes_dict[self.color_schemes[0].get()]
                        for i, col in enumerate(y1_data):
                            color_idx = idx * len(y1_data) + i
                            color = plt.cm.tab10(color_idx % 10) if color_map is None else color_map(color_idx % color_map.N)
                            cleaned_col = self.clean_legend_label(col)
                            line = self.ax.plot(df_c_plot[x_col], df_c_plot[col],
                                               label=f"C{c}_{cleaned_col}",
                                               linewidth=self.safe_float_convert(self.line_width.get(), 1.5),
                                               color=color,
                                               linestyle=self.line_styles_dict[self.line_styles[0].get()])
                            all_lines.extend(line)
                            all_labels.append(f"C{c}_{cleaned_col}")

                    if y2_data and ax2:
                        color_map = self.color_schemes_dict[self.color_schemes[1].get()]
                        for i, col in enumerate(y2_data):
                            color_idx = idx * len(y2_data) + i
                            color = plt.cm.tab10(color_idx % 10) if color_map is None else color_map(color_idx % color_map.N)
                            cleaned_col = self.clean_legend_label(col)
                            line = ax2.plot(df_c_plot[x_col], df_c_plot[col],
                                               label=f"C{c}_{cleaned_col}",
                                               linewidth=self.safe_float_convert(self.line_width.get(), 1.5),
                                               color=color,
                                               linestyle=self.line_styles_dict[self.line_styles[1].get()])
                            all_lines.extend(line)
                            all_labels.append(f"C{c}_{cleaned_col}")

                    if y3_data and ax3:
                        color_map = self.color_schemes_dict[self.color_schemes[2].get()]
                        for i, col in enumerate(y3_data):
                            color_idx = idx * len(y3_data) + i
                            color = plt.cm.tab10(color_idx % 10) if color_map is None else color_map(color_idx % color_map.N)
                            cleaned_col = self.clean_legend_label(col)
                            line = ax3.plot(df_c_plot[x_col], df_c_plot[col],
                                               label=f"C{c}_{cleaned_col}",
                                               linewidth=self.safe_float_convert(self.line_width.get(), 1.5),
                                               color=color,
                                               linestyle=self.line_styles_dict[self.line_styles[2].get()])
                            all_lines.extend(line)
                            all_labels.append(f"C{c}_{cleaned_col}")

            if plot_type in ['dqdv', 'dvdq']:
                self.ax.set_xlabel(x_label, fontsize=font_size, fontfamily=font_family, color='black')
                
                # 用户自定义标题
                user_dqdv_title = self.dqdv_title_var.get().strip()
                if user_dqdv_title:
                    y_label_str = user_dqdv_title
                else:
                    y_label_str = "dQ/dV / (Ah/V)" if plot_type == 'dqdv' else "dV/dQ / (V/Ah)"
                self.ax.set_ylabel(y_label_str, fontsize=font_size, fontfamily=font_family, color='black', labelpad=10)
                
                # 默认百分位限值计算
                ymin_default = None
                ymax_default = None
                if all_y_plots:
                    all_y_plots = np.array(all_y_plots)
                    all_y_plots = all_y_plots[np.isfinite(all_y_plots)]
                    if len(all_y_plots) > 0:
                        ymin_auto = np.percentile(all_y_plots, 0.2)
                        ymax_auto = np.percentile(all_y_plots, 99.8)
                        yrange = ymax_auto - ymin_auto
                        if yrange == 0:
                            yrange = 1.0
                        ymin_default = ymin_auto - 0.10 * yrange
                        ymax_default = ymax_auto + 0.10 * yrange
                
                # 应用用户配置的 Min/Max
                dqdv_min_str = self.dqdv_min_var.get().strip()
                dqdv_max_str = self.dqdv_max_var.get().strip()
                
                if dqdv_min_str or dqdv_max_str:
                    ymin_val = self.resolve_val(dqdv_min_str, ymin_default, ymax_default)
                    if ymin_val is None and not dqdv_min_str:
                        ymin_val = ymin_default
                    
                    ymax_val = self.resolve_val(dqdv_max_str, ymin_default, ymax_default)
                    if ymax_val is None and not dqdv_max_str:
                        ymax_val = ymax_default
                        
                    if ymin_val is not None and ymax_val is not None and ymin_val < ymax_val:
                        self.ax.set_ylim(ymin_val, ymax_val)
                    elif ymin_val is not None:
                        self.ax.set_ylim(bottom=ymin_val)
                    elif ymax_val is not None:
                        self.ax.set_ylim(top=ymax_val)
            else:
                self.ax.set_xlabel(self.x_title.get(), fontsize=font_size, fontfamily=font_family, color='black')
                
                # Check user title for comparative regular plot (which is now called "循环Y轴")
                user_y_title = self.dqdv_title_var.get().strip()
                if user_y_title:
                    self.ax.set_ylabel(user_y_title, fontsize=font_size, fontfamily=font_family, color='black', labelpad=10)
                else:
                    self.ax.set_ylabel(self.y_settings[0]['title'].get(), fontsize=font_size, fontfamily=font_family, color='black', labelpad=10)
                
                # Check user limits for comparative regular plot (which is now called "循环Y轴")
                dqdv_min_str = self.dqdv_min_var.get().strip()
                dqdv_max_str = self.dqdv_max_var.get().strip()
                
                ymin_default = None
                ymax_default = None
                if all_y_plots:
                    all_y_plots = np.array(all_y_plots)
                    all_y_plots = all_y_plots[np.isfinite(all_y_plots)]
                    if len(all_y_plots) > 0:
                        ymin_default = np.min(all_y_plots)
                        ymax_default = np.max(all_y_plots)
                
                if dqdv_min_str or dqdv_max_str:
                    ymin_val = self.resolve_val(dqdv_min_str, ymin_default, ymax_default)
                    if ymin_val is None and not dqdv_min_str:
                        ymin_val = ymin_default
                    
                    ymax_val = self.resolve_val(dqdv_max_str, ymin_default, ymax_default)
                    if ymax_val is None and not dqdv_max_str:
                        ymax_val = ymax_default
                        
                    if ymin_val is not None and ymax_val is not None and ymin_val < ymax_val:
                        self.ax.set_ylim(ymin_val, ymax_val)
                    elif ymin_val is not None:
                        self.ax.set_ylim(bottom=ymin_val)
                    elif ymax_val is not None:
                        self.ax.set_ylim(top=ymax_val)
                else:
                    # Fallback to the y_settings[0] limits
                    try:
                        ymin = float(self.y_settings[0]['min'].get())
                        ymax = float(self.y_settings[0]['max'].get())
                        if ymin < ymax:
                            self.ax.set_ylim(ymin, ymax)
                    except Exception:
                        pass

                if y2_data and ax2:
                    ax2.set_ylabel(self.y_settings[1]['title'].get(), fontsize=font_size, fontfamily=font_family, color='black', labelpad=0)
                    try:
                        ymin = float(self.y_settings[1]['min'].get())
                        ymax = float(self.y_settings[1]['max'].get())
                        if ymin < ymax:
                            ax2.set_ylim(ymin, ymax)
                    except Exception:
                        pass

                if y3_data and ax3:
                    ax3.set_ylabel(self.y_settings[2]['title'].get(), fontsize=font_size, fontfamily=font_family, color='black', labelpad=0)
                    try:
                        ymin = float(self.y_settings[2]['min'].get())
                        ymax = float(self.y_settings[2]['max'].get())
                        if ymin < ymax:
                            ax3.set_ylim(ymin, ymax)
                    except Exception:
                        pass

            if all_lines and self.legend_visible.get():
                legend_y = self.safe_float_convert(self.legend_y.get(), 1.02)
                positions = self.get_legend_positions()
                try:
                    legend_cols = int(self.legend_cols.get())
                except ValueError:
                    legend_cols = 1
                try:
                    legend_font_size = int(self.legend_font_size.get())
                except ValueError:
                    legend_font_size = 18

                if plot_type in ['dqdv', 'dvdq']:
                    self.ax.legend(all_lines, all_labels, loc='upper left', bbox_to_anchor=(positions[0], legend_y), ncol=legend_cols, frameon=False, fontsize=legend_font_size)
                else:
                    y1_end = len(y1_data) * len(cycles) if y1_data else 0
                    y2_end = y1_end + (len(y2_data) * len(cycles) if y2_data else 0)
                    y1_lines = all_lines[:y1_end]
                    y2_lines = all_lines[y1_end:y2_end]
                    y3_lines = all_lines[y2_end:]
                    y1_labels_s = all_labels[:y1_end]
                    y2_labels_s = all_labels[y1_end:y2_end]
                    y3_labels_s = all_labels[y2_end:]

                    if y1_lines:
                        leg1 = self.ax.legend(y1_lines, y1_labels_s, loc='upper left', bbox_to_anchor=(positions[0], legend_y), ncol=legend_cols, frameon=False, fontsize=legend_font_size)
                        self.ax.add_artist(leg1)
                    if y2_lines and ax2:
                        leg2 = ax2.legend(y2_lines, y2_labels_s, loc='upper left', bbox_to_anchor=(positions[1], legend_y), ncol=legend_cols, frameon=False, fontsize=legend_font_size)
                        ax2.add_artist(leg2)
                    if y3_lines and ax3:
                        leg3 = ax3.legend(y3_lines, y3_labels_s, loc='upper left', bbox_to_anchor=(positions[2], legend_y), ncol=legend_cols, frameon=False, fontsize=legend_font_size)
                        ax3.add_artist(leg3)

            right_margin, left_margin = self.get_dynamic_margins(y1_data, y2_data, y3_data)
            self.fig.subplots_adjust(right=right_margin, left=left_margin, top=0.90, bottom=0.08)
            # Apply X-axis limits if specified
            try:
                xmin_str = self.x_min_var.get().strip()
                xmax_str = self.x_max_var.get().strip()
                if xmin_str or xmax_str:
                    if len(all_x_points) > 0:
                        xmin_default = np.min(all_x_points)
                        xmax_default = np.max(all_x_points)
                        
                        xmin_val = self.resolve_val(xmin_str, xmin_default, xmax_default)
                        if xmin_val is None and not xmin_str:
                            xmin_val = xmin_default
                            
                        xmax_val = self.resolve_val(xmax_str, xmin_default, xmax_default)
                        if xmax_val is None and not xmax_str:
                            xmax_val = xmax_default
                            
                        if xmin_val is not None and xmax_val is not None and xmin_val < xmax_val:
                            self.ax.set_xlim(xmin_val, xmax_val)
            except Exception:
                pass
            self.canvas.draw()

        except Exception as e:
            QMessageBox.critical(self, "错误", f"循环对比绘图失败: {str(e)}")

    def update_font_and_plot(self):
        """更新绘图字体大小并刷新图表"""
        try:
            font_size = int(self.safe_float_convert(self.font_size.get(), 18.0))
            font_family = self.font_family.get()
            plt.rcParams['font.size'] = font_size
            plt.rcParams['axes.labelsize'] = font_size
            plt.rcParams['xtick.labelsize'] = font_size
            plt.rcParams['ytick.labelsize'] = font_size
            plt.rcParams['font.sans-serif'] = [font_family] + [f for f in plt.rcParams['font.sans-serif'] if f != font_family]
            plt.rcParams['axes.unicode_minus'] = False
            
            legend_font_size = self.safe_float_convert(self.legend_font_size.get(), 18.0)
            plt.rcParams['legend.fontsize'] = legend_font_size
            
            self.update_legend_only()
            
            if hasattr(self, 'ax'):
                self.ax.title.set_fontsize(font_size)
                self.ax.xaxis.label.set_fontsize(font_size)
                self.ax.yaxis.label.set_fontsize(font_size)
                for label in self.ax.get_xticklabels() + self.ax.get_yticklabels():
                    label.set_fontsize(font_size)
                
                for axis in self.fig.axes:
                    if axis != self.ax:
                        axis.yaxis.label.set_fontsize(font_size)
                        for label in axis.get_yticklabels():
                            label.set_fontsize(font_size)
                            
            self.update_plot()
        except Exception as e:
            print(f"更新字体失败: {str(e)}")

    def update_plot(self):
        """触发重新绘图"""
        current_time = time.time()
        if current_time - self._last_plot_time < 0.1:
            return
        self._last_plot_time = current_time
        if self.file_type.get() == "battery" and self.cycle_compare_var.get():
            self.plot_cycle_compare(plot_type=self.current_compare_type.get())
        else:
            self.plot_data()
            
        # 保存当前设置
        if hasattr(self, 'save_settings'):
            self.save_settings()

    def plot_y_axis(self, axis_index):
        """根据点击的按钮绘制对应的Y轴数据"""
        self.plot_data(clicked_axis=axis_index)

    def plot_data(self, clicked_axis=None):
        try:
            if self.result_df is None:
                return
            
            df_to_plot = self.result_df.copy()
            if self.file_type.get() == "battery":
                voltage_col_name = self.voltage_col.get()
                current_col_name = self.current_col.get()
                v_scale = self.safe_float_convert(self.voltage_scale_var.get(), 1.0)
                c_scale = self.safe_float_convert(self.current_scale_var.get(), 1.0)
                if v_scale != 1.0 and voltage_col_name in df_to_plot.columns:
                    df_to_plot[voltage_col_name] = pd.to_numeric(df_to_plot[voltage_col_name], errors='coerce') * v_scale
                if c_scale != 1.0 and current_col_name in df_to_plot.columns:
                    df_to_plot[current_col_name] = pd.to_numeric(df_to_plot[current_col_name], errors='coerce') * c_scale
            
            if self.file_type.get() == "battery":
                cycle_col_name = self.cycle_col.get()
                step_col_name = self.step_col.get()
                cycle_val = self.cycle_filter.get()
                step_val = self.step_filter.get()
                
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
                        df_to_plot = self.compute_step_time(df_to_plot, cycle_col_name, step_col_name, self.time_col.get())
                        try:
                            df_to_plot = df_to_plot[df_to_plot[step_col_name] == step_val]
                        except Exception:
                            try:
                                df_to_plot = df_to_plot[df_to_plot[step_col_name].astype(str) == str(step_val)]
                            except Exception:
                                pass
            
            if df_to_plot.empty:
                self.ax.clear()
                self.canvas.draw()
                return

            max_pts = self.max_plot_points.get()
            if max_pts and str(max_pts).strip().lower() != 'none':
                num_rows = len(df_to_plot)
                try:
                    max_pts_val = int(float(max_pts))
                    if num_rows > max_pts_val:
                        step = num_rows // max_pts_val
                        if step > 1:
                            df_to_plot = df_to_plot.iloc[::step]
                            self.update_status(f"提示：绘图数据已自动降采样（从 {num_rows} 行降至 {len(df_to_plot)} 行），以提升显示效率")
                except Exception as e:
                    self.logger.error(f"数据降采样失败: {str(e)}")
                
            def set_axis_style(ax):
                ax.tick_params(axis='both', direction='in', width=float(self.frame_width.get()), length=6, 
                              labelsize=int(self.font_size.get()), color='black')
                for spine in ax.spines.values():
                    spine.set_linewidth(float(self.frame_width.get()))
                    spine.set_color('black')
                    
            y1_data = self.y_selections[0]
            y2_data = self.y_selections[1]
            y3_data = self.y_selections[2]
            
            if not any([y1_data, y2_data, y3_data]):
                return
                
            if clicked_axis == 0 or clicked_axis is None:
                self.fig.clf()
                
                w_px = self.canvas.width()
                h_px = self.canvas.height()
                if w_px > 1 and h_px > 1:
                    self.fig.set_size_inches(w_px / self.fig.dpi, h_px / self.fig.dpi, forward=False)
                    
                self.ax = self.fig.add_subplot(111)
                if clicked_axis == 0:
                    y2_data = []
                    y3_data = []
            elif clicked_axis == 1:
                if not y1_data:
                    return
                y3_data = []
            elif clicked_axis == 2:
                if not y1_data or not y2_data:
                    return
                
            x_col = self.x_axis.get()
            font_size = int(self.safe_float_convert(self.font_size.get(), 18.0))
            font_family = self.font_family.get()
            plt.rcParams['font.sans-serif'] = [font_family] + [f for f in plt.rcParams['font.sans-serif'] if f != font_family]
            plt.rcParams['axes.unicode_minus'] = False
            
            if x_col and x_col in df_to_plot.columns:
                import pandas.api.types as ptypes
                if df_to_plot[x_col].dtype == 'object' or ptypes.is_string_dtype(df_to_plot[x_col]):
                    unique_count = df_to_plot[x_col].nunique()
                    if unique_count > 1000:
                        QMessageBox.warning(self, "警告", f"X轴 '{x_col}' 包含大量文本值 ({unique_count}个唯一值)，直接绘制会导致界面卡死。\n请选择时间差（例如包含'时间差(s)'的列）等数值列作为X轴。")
                        return

            all_lines = []
            all_labels = []
            
            if y1_data:
                set_axis_style(self.ax)
                color_map = self.color_schemes_dict[self.color_schemes[0].get()]
                for i, col in enumerate(y1_data):
                    color = plt.cm.tab10(i % 10) if color_map is None else color_map(i % color_map.N)
                    cleaned_label = self.clean_legend_label(col)
                    line = self.ax.plot(df_to_plot[x_col], df_to_plot[col],
                                      label=cleaned_label, 
                                      linewidth=self.safe_float_convert(self.line_width.get(), 1.5),
                                      color=color,
                                      linestyle=self.line_styles_dict[self.line_styles[0].get()])
                    all_lines.extend(line)
                    all_labels.append(cleaned_label)
                self.ax.set_ylabel(self.y_settings[0]['title'].get(), fontsize=font_size, fontfamily=font_family, color='black', labelpad=10)
                try:
                    ymin = float(self.y_settings[0]['min'].get())
                    ymax = float(self.y_settings[0]['max'].get())
                    if ymin < ymax:
                        self.ax.set_ylim(ymin, ymax)
                except Exception:
                    pass
                
            if y2_data:
                ax2 = self.ax.twinx()
                ax2.spines['right'].set_position(('outward', 0))
                set_axis_style(ax2)
                color_map = self.color_schemes_dict[self.color_schemes[1].get()]
                y2_lines_temp = []
                y2_labels_temp = []
                for i, col in enumerate(y2_data):
                    color = plt.cm.tab10(i % 10) if color_map is None else color_map(i % color_map.N)
                    cleaned_label = self.clean_legend_label(col)
                    line = ax2.plot(df_to_plot[x_col], df_to_plot[col],
                                      label=cleaned_label, 
                                      linewidth=self.safe_float_convert(self.line_width.get(), 1.5),
                                      color=color,
                                      linestyle=self.line_styles_dict[self.line_styles[1].get()])
                    y2_lines_temp.extend(line)
                    y2_labels_temp.append(cleaned_label)
                all_lines.extend(y2_lines_temp)
                all_labels.extend(y2_labels_temp)
                ax2.set_ylabel(self.y_settings[1]['title'].get(), fontsize=font_size, fontfamily=font_family, color='black', labelpad=0)
                try:
                    ymin = float(self.y_settings[1]['min'].get())
                    ymax = float(self.y_settings[1]['max'].get())
                    if ymin < ymax:
                        ax2.set_ylim(ymin, ymax)
                except Exception:
                    pass
                
            if y3_data:
                ax3 = self.ax.twinx()
                ax3.spines['right'].set_position(('outward', 70))
                set_axis_style(ax3)
                color_map = self.color_schemes_dict[self.color_schemes[2].get()]
                for i, col in enumerate(y3_data):
                    color = plt.cm.tab10(i % 10) if color_map is None else color_map(i % color_map.N)
                    cleaned_label = self.clean_legend_label(col)
                    line = ax3.plot(df_to_plot[x_col], df_to_plot[col],
                                      label=cleaned_label, 
                                      linewidth=self.safe_float_convert(self.line_width.get(), 1.5),
                                      color=color,
                                      linestyle=self.line_styles_dict[self.line_styles[2].get()])
                    all_lines.extend(line)
                    all_labels.append(cleaned_label)
                ax3.set_ylabel(self.y_settings[2]['title'].get(), fontsize=font_size, fontfamily=font_family, color='black', labelpad=0)
                try:
                    ymin = float(self.y_settings[2]['min'].get())
                    ymax = float(self.y_settings[2]['max'].get())
                    if ymin < ymax:
                        ax3.set_ylim(ymin, ymax)
                except Exception:
                    pass
                
            if all_lines and self.legend_visible.get():
                y1_end = len(y1_data) if y1_data else 0
                y2_end = y1_end + (len(y2_data) if y2_data else 0)
                y1_lines = all_lines[:y1_end] if y1_data else []
                y2_lines = all_lines[y1_end:y2_end] if y2_data else []
                y3_lines = all_lines[y2_end:] if y3_data else []
                y1_labels = all_labels[:y1_end] if y1_data else []
                y2_labels = all_labels[y1_end:y2_end] if y2_data else []
                y3_labels = all_labels[y2_end:] if y3_data else []
                
                legend_y = self.safe_float_convert(self.legend_y.get(), 1.02)
                positions = self.get_legend_positions()
                try:
                    legend_cols = int(self.legend_cols.get())
                except ValueError:
                    legend_cols = 1
                try:
                    legend_font_size = int(self.legend_font_size.get())
                except ValueError:
                    legend_font_size = 18
                
                for ax in self.fig.axes:
                    if ax.get_legend() is not None:
                        ax.get_legend().remove()
                    
                if y1_data:
                    leg1 = self.ax.legend(y1_lines, y1_labels, loc='upper left', bbox_to_anchor=(positions[0], legend_y), ncol=legend_cols, frameon=False, fontsize=legend_font_size)
                    self.ax.add_artist(leg1)
                if y2_data:
                    leg2 = self.ax.legend(y2_lines, y2_labels, loc='upper left', bbox_to_anchor=(positions[1], legend_y), ncol=legend_cols, frameon=False, fontsize=legend_font_size)
                    self.ax.add_artist(leg2)
                if y3_data:
                    leg3 = self.ax.legend(y3_lines, y3_labels, loc='upper left', bbox_to_anchor=(positions[2], legend_y), ncol=legend_cols, frameon=False, fontsize=legend_font_size)
                    self.ax.add_artist(leg3)
                    
            self.ax.set_xlabel(self.x_title.get(), fontsize=font_size, fontfamily=font_family, color='black')
                    
            try:
                self.ax.ticklabel_format(axis='x', style='sci', scilimits=(-3, 6))
                self.ax.xaxis.get_offset_text().set_fontsize(font_size)
                self.ax.xaxis.get_offset_text().set_fontfamily(font_family)
            except Exception:
                pass
                
            right_margin, left_margin = self.get_dynamic_margins(y1_data, y2_data, y3_data)
            self.fig.subplots_adjust(right=right_margin, left=left_margin, top=0.90, bottom=0.08)
            # Apply X-axis limits if specified
            try:
                xmin_str = self.x_min_var.get().strip()
                xmax_str = self.x_max_var.get().strip()
                if xmin_str or xmax_str:
                    x_col = self.x_axis.get()
                    if x_col in df_to_plot.columns:
                        x_values = pd.to_numeric(df_to_plot[x_col], errors='coerce').dropna()
                        if len(x_values) > 0:
                            xmin_default = x_values.min()
                            xmax_default = x_values.max()
                            
                            xmin_val = self.resolve_val(xmin_str, xmin_default, xmax_default)
                            if xmin_val is None and not xmin_str:
                                xmin_val = xmin_default
                                
                            xmax_val = self.resolve_val(xmax_str, xmin_default, xmax_default)
                            if xmax_val is None and not xmax_str:
                                xmax_val = xmax_default
                                
                            if xmin_val is not None and xmax_val is not None and xmin_val < xmax_val:
                                self.ax.set_xlim(xmin_val, xmax_val)
            except Exception as e:
                print(f"X-axis limits error: {e}")
            self.canvas.draw()
            
        except Exception as e:
            QMessageBox.critical(self, "错误", f"绘图失败: {str(e)}")

    def get_dynamic_margins(self, y1_data, y2_data, y3_data):
        """根据当前图纸的实际像素宽度和字体大小，动态计算并返回左右边距百分比"""
        try:
            fig_width_px = self.canvas.width()
            
            if fig_width_px <= 1:
                fig_width_px = self.fig.get_figwidth() * self.fig.dpi
            if fig_width_px <= 1:
                fig_width_px = 1200
                
            font_size = int(self.safe_float_convert(self.font_size.get(), 18.0))
            
            left_mult = float(self.safe_float_convert(self.adv_left_margin_mult.get(), 4.5))
            left_min_px = float(self.safe_float_convert(self.adv_left_margin_min_px.get(), 80.0))
            left_min_pct = float(self.safe_float_convert(self.adv_left_margin_min_pct.get(), 0.08))
            
            left_margin_px = max(left_min_px, int(font_size * left_mult))
            
            if y3_data:
                y3_mult = float(self.safe_float_convert(self.adv_y3_margin_mult.get(), 9.5))
                y3_min_px = float(self.safe_float_convert(self.adv_y3_margin_min_px.get(), 170.0))
                y3_max_right_pct = float(self.safe_float_convert(self.adv_y3_max_right_pct.get(), 0.83))
                
                right_margin_px = max(y3_min_px, int(font_size * y3_mult))
                max_right_percent = y3_max_right_pct
            elif y2_data:
                y2_mult = float(self.safe_float_convert(self.adv_y2_margin_mult.get(), 4.0))
                y2_min_px = float(self.safe_float_convert(self.adv_y2_margin_min_px.get(), 75.0))
                y2_max_right_pct = float(self.safe_float_convert(self.adv_y2_max_right_pct.get(), 0.93))
                
                right_margin_px = max(y2_min_px, int(font_size * y2_mult))
                max_right_percent = y2_max_right_pct
            else:
                y1_mult = float(self.safe_float_convert(self.adv_y1_margin_mult.get(), 1.5))
                y1_min_px = float(self.safe_float_convert(self.adv_y1_margin_min_px.get(), 20.0))
                y1_max_right_pct = float(self.safe_float_convert(self.adv_y1_max_right_pct.get(), 0.97))
                
                right_margin_px = max(y1_min_px, int(font_size * y1_mult))
                max_right_percent = y1_max_right_pct
                
            left_margin = max(left_min_pct, min(left_margin_px / fig_width_px, 0.15))
            right_margin = max(0.6, min(1.0 - (right_margin_px / fig_width_px), max_right_percent))
            return right_margin, left_margin
        except Exception:
            y3_pct = float(self.safe_float_convert(self.adv_y3_max_right_pct.get(), 0.83)) if hasattr(self, 'adv_y3_max_right_pct') else 0.83
            y2_pct = float(self.safe_float_convert(self.adv_y2_max_right_pct.get(), 0.93)) if hasattr(self, 'adv_y2_max_right_pct') else 0.93
            y1_pct = float(self.safe_float_convert(self.adv_y1_max_right_pct.get(), 0.97)) if hasattr(self, 'adv_y1_max_right_pct') else 0.97
            left_pct = float(self.safe_float_convert(self.adv_left_margin_min_pct.get(), 0.08)) if hasattr(self, 'adv_left_margin_min_pct') else 0.08
            if y3_data:
                return y3_pct, left_pct
            elif y2_data:
                return y2_pct, left_pct
            else:
                return y1_pct, left_pct

    def on_window_resize(self, event=None):
        if hasattr(self, 'fig') and hasattr(self, 'canvas'):
            w_px = self.canvas.width()
            h_px = self.canvas.height()
            if w_px > 1 and h_px > 1:
                self.fig.set_size_inches(w_px / self.fig.dpi, h_px / self.fig.dpi, forward=False)
                
            y1_data = self.y_selections[0]
            y2_data = self.y_selections[1]
            y3_data = self.y_selections[2]
            
            right_margin, left_margin = self.get_dynamic_margins(y1_data, y2_data, y3_data)
                
            self.fig.subplots_adjust(
                right=right_margin, 
                left=left_margin, 
                top=0.90,
                bottom=0.08,
                wspace=0.2
            )
            self.canvas.draw_idle()

    def update_legend_only(self):
        """只更新图例位置，不重新绘制整个图表（带防抖）"""
        if getattr(self, '_is_loading_settings', False):
            return
        if not hasattr(self, 'ax') or not self.legend_visible.get():
            return
            
        if hasattr(self, '_legend_timer') and self._legend_timer is not None:
            try:
                self._legend_timer.stop()
            except Exception:
                pass
                
        def do_update():
            try:
                legend_y = self.safe_float_convert(self.legend_y.get(), 1.02)
                font_size = self.safe_float_convert(self.font_size.get(), 18.0)
                try:
                    legend_cols = int(self.legend_cols.get())
                except ValueError:
                    legend_cols = 1
                try:
                    legend_font_size = int(self.legend_font_size.get())
                except ValueError:
                    legend_font_size = 18
                
                y1_data = self.y_selections[0]
                y2_data = self.y_selections[1]
                y3_data = self.y_selections[2]
                
                axes = [self.ax]
                for axis in self.ax.figure.axes:
                    if axis != self.ax:
                        axes.append(axis)
                
                for ax in axes[1:]:
                    if ax.get_legend() is not None:
                        ax.get_legend().remove()
                
                all_lines = []
                all_labels = []
                
                for ax in axes:
                    for line in ax.get_lines():
                        if line.get_visible():
                            all_lines.append(line)
                            all_labels.append(line.get_label())
                
                y1_end = len(y1_data) if y1_data else 0
                y2_end = y1_end + (len(y2_data) if y2_data else 0)
                
                y1_lines = all_lines[:y1_end] if y1_data else []
                y2_lines = all_lines[y1_end:y2_end] if y2_data else []
                y3_lines = all_lines[y2_end:] if y3_data else []
                
                y1_labels = all_labels[:y1_end] if y1_data else []
                y2_labels = all_labels[y1_end:y2_end] if y2_data else []
                y3_labels = all_labels[y2_end:] if y3_data else []
                
                positions = self.parse_legend_x_positions()
                if y1_lines:
                    leg1 = self.ax.legend(y1_lines, y1_labels,
                                        loc='upper left',
                                        bbox_to_anchor=(positions[0], legend_y),
                                        ncol=legend_cols,
                                        frameon=False,
                                        fontsize=legend_font_size)
                    self.ax.add_artist(leg1)
                
                if y2_lines:
                    leg2 = self.ax.legend(y2_lines, y2_labels,
                                        loc='upper left',
                                        bbox_to_anchor=(positions[1], legend_y),
                                        ncol=legend_cols,
                                        frameon=False,
                                        fontsize=legend_font_size)
                    self.ax.add_artist(leg2)
                
                if y3_lines:
                    leg3 = self.ax.legend(y3_lines, y3_labels,
                                        loc='upper left',
                                        bbox_to_anchor=(positions[2], legend_y),
                                        ncol=legend_cols,
                                        frameon=False,
                                        fontsize=legend_font_size)
                    self.ax.add_artist(leg3)
                
                self.canvas.draw()
                
            except Exception as e:
                self.logger.error(f"更新图例失败: {str(e)}")

        self._legend_timer = QTimer()
        self._legend_timer.setSingleShot(True)
        self._legend_timer.timeout.connect(do_update)
        self._legend_timer.start(300)

    def toggle_legend(self):
        """切换图例的显示状态"""
        try:
            if hasattr(self, 'ax'):
                axes = [self.ax]
                for axis in self.ax.figure.axes:
                    if axis != self.ax:
                        axes.append(axis)
                
                if self.legend_visible.get():
                    self.update_legend_only()
                else:
                    for ax in axes:
                        if ax.get_legend() is not None:
                            ax.get_legend().remove()
                
                self.canvas.draw()
        except Exception as e:
            self.logger.error(f"切换图例显示状态失败: {str(e)}")

    def parse_legend_x_positions(self):
        """解析水平图例位置，支持逗号或空格分隔"""
        import re
        try:
            val = self.legend_x_positions_str.get().strip()
            tokens = re.split(r'[\s,]+', val)
            positions = []
            for t in tokens:
                try:
                    positions.append(float(t))
                except ValueError:
                    pass
            default_pos = [0.0, 0.3, 0.6]
            while len(positions) < 3:
                positions.append(default_pos[len(positions)])
            return positions[:3]
        except Exception:
            return [0.0, 0.3, 0.6]

    def update_legend_positions(self):
        """更新图例位置（带防抖）"""
        if getattr(self, '_is_loading_settings', False):
            return
        if hasattr(self, '_legend_pos_timer') and self._legend_pos_timer is not None:
            try:
                self._legend_pos_timer.stop()
            except Exception:
                pass
                
        def do_update():
            try:
                positions = self.parse_legend_x_positions()
                if len(positions) >= 3:
                    self.update_legend_only()
            except Exception:
                pass

        self._legend_pos_timer = QTimer()
        self._legend_pos_timer.setSingleShot(True)
        self._legend_pos_timer.timeout.connect(do_update)
        self._legend_pos_timer.start(300)

    def get_legend_positions(self):
        """获取图例位置列表"""
        return self.parse_legend_x_positions()

    def update_y_axis(self, axis):
        """更新指定Y轴的范围和标题（带防抖）"""
        if getattr(self, '_is_loading_settings', False):
            return
            
        if not hasattr(self, '_y_axis_timers'):
            self._y_axis_timers = {}
            
        if axis in self._y_axis_timers and self._y_axis_timers[axis] is not None:
            try:
                self._y_axis_timers[axis].stop()
            except Exception:
                pass
                
        def do_update():
            try:
                if not hasattr(self, 'fig') or not self.fig.axes:
                    return
                
                axes = self.fig.axes
                target_ax = None
                
                if axis == 0:
                    target_ax = self.ax
                elif axis == 1:
                    for ax in axes:
                        if (ax != self.ax and 
                            ax.spines['right'].get_position()[0] == 'outward' and 
                            ax.spines['right'].get_position()[1] == 0):
                            target_ax = ax
                            break
                elif axis == 2:
                    for ax in axes:
                        if (ax != self.ax and 
                            ax.spines['right'].get_position()[0] == 'outward' and 
                            ax.spines['right'].get_position()[1] == 70):
                            target_ax = ax
                            break
                
                if target_ax:
                    try:
                        ymin = float(self.y_settings[axis]['min'].get())
                        ymax = float(self.y_settings[axis]['max'].get())
                        if ymin < ymax:
                            lines = target_ax.get_lines()
                            target_ax.set_ylim(ymin, ymax)
                            for line in lines:
                                line.set_visible(True)
                            
                            pad_val = 10 if axis == 0 else 15
                            target_ax.set_ylabel(self.y_settings[axis]['title'].get(),
                                               fontsize=int(self.safe_float_convert(self.font_size.get(), 18.0)),
                                               fontfamily=self.font_family.get(),
                                               color='black',
                                               labelpad=pad_val)
                            
                            self.canvas.draw()
                    except (ValueError, TypeError):
                        pass
                
            except Exception as e:
                print(f"更新Y{axis+1}轴失败: {str(e)}")

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(do_update)
        timer.start(300)
        self._y_axis_timers[axis] = timer
