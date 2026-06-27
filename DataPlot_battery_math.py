import pandas as pd
import numpy as np

class BatteryMathMixin:
    def compute_step_time(self, df, cycle_col, step_col, time_col):
        """计算工步时间：在同一个循环内，每个连续的工步段从0开始计时"""
        try:
            df = df.copy()
            if not cycle_col or cycle_col not in df.columns or not step_col or step_col not in df.columns or not time_col or time_col not in df.columns:
                df['工步时间'] = 0.0
                return df
                
            # 保证按时间先后排序，以便差分
            df = df.sort_values(by=[cycle_col, time_col])
            
            # 判断循环或工步是否发生变化，以识别连续的工步段
            cycle_series = df[cycle_col]
            step_series = df[step_col]
            change = (cycle_series != cycle_series.shift()) | (step_series != step_series.shift())
            step_group = change.cumsum()
            
            time_diff_col = f"{time_col}_时间差(s)"
            t_col_to_use = time_diff_col if time_diff_col in df.columns else time_col
            
            df['工步时间'] = 0.0
            for g_id, group_df in df.groupby(step_group):
                # 用 ffill 填充缺失值，然后以 0 填充其它空值
                g_t = pd.to_numeric(group_df[t_col_to_use], errors='coerce').ffill().fillna(0).values
                if len(g_t) > 0:
                    df.loc[group_df.index, '工步时间'] = g_t - g_t[0]
            return df
        except Exception as e:
            self.logger.error(f"计算工步时间失败: {str(e)}")
            df['工步时间'] = 0.0
            return df

    def parse_cycles(self, cycle_str, max_cycle):
        """解析循环范围字符串，支持 '1, max, 10' 或者逗号分隔的列表"""
        parts = [p.strip() for p in cycle_str.split(',')]
        if len(parts) == 3:
            try:
                start = int(parts[0])
                end_val = parts[1].lower()
                if end_val == 'max':
                    end = int(max_cycle)
                else:
                    end = int(end_val)
                step = int(parts[2])
                return list(range(start, end + 1, step))
            except ValueError:
                pass
        
        cycles = []
        for p in parts:
            if p.lower() == 'max':
                cycles.append(int(max_cycle))
            else:
                try:
                    cycles.append(int(p))
                except ValueError:
                    pass
        return sorted(list(set(cycles)))

    def get_current_multiplier(self, df_c, current_col_name, voltage_col_name, step_col_name, cc_polarity):
        """Determine the current multiplier (+1.0 or -1.0) to match cc_polarity choice."""
        cc_sign = 1.0 if cc_polarity == "正" else -1.0
        best_charge_step_i = None
        max_dv = 0.0
        
        steps = [0]
        if step_col_name in df_c.columns:
            try:
                steps = df_c[step_col_name].dropna().unique()
            except Exception:
                pass
                
        if len(steps) > 1 and len(steps) <= 100:
            for s in steps:
                mask = df_c[step_col_name] == s
                sub_df = df_c[mask]
                if len(sub_df) < 2:
                    continue
                u_vals = pd.to_numeric(sub_df[voltage_col_name], errors='coerce').values
                i_vals = pd.to_numeric(sub_df[current_col_name], errors='coerce').fillna(0).values
                valid_u = ~np.isnan(u_vals)
                if np.sum(valid_u) >= 2:
                    u_clean = u_vals[valid_u]
                    dv = u_clean[-1] - u_clean[0]
                    avg_i = i_vals.mean()
                    if dv > max_dv and abs(avg_i) > 1e-4:
                        max_dv = dv
                        best_charge_step_i = avg_i
        
        if best_charge_step_i is None:
            u_vals = pd.to_numeric(df_c[voltage_col_name], errors='coerce').values
            i_vals = pd.to_numeric(df_c[current_col_name], errors='coerce').fillna(0).values
            valid_u = ~np.isnan(u_vals)
            if np.sum(valid_u) >= 2:
                u_clean = u_vals[valid_u]
                dv = u_clean[-1] - u_clean[0]
                avg_i = i_vals.mean()
                if dv > 0:
                    best_charge_step_i = avg_i
                else:
                    if abs(avg_i) > 1e-4:
                        return -cc_sign if avg_i > 0 else cc_sign
                    else:
                        return 1.0
            else:
                return 1.0

        if best_charge_step_i is not None and abs(best_charge_step_i) > 1e-4:
            return cc_sign if best_charge_step_i > 0 else -cc_sign
            
        return 1.0
