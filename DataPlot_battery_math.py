import pandas as pd
import numpy as np

class BatteryMathMixin:
    def calculate_time_diff_series(self, df, time_col):
        """计算数据集从起点开始的相对时间差序列(秒)，支持数值、datetime.time对象、'MM:SS.s'/'HH:MM:SS.s'文本格式"""
        if not time_col or time_col not in df.columns:
            return None
        col_data = df[time_col]
        if col_data is None or col_data.dropna().empty:
            return None
            
        import pandas.api.types as ptypes
        import datetime
        non_nulls = col_data.dropna()
        non_null_count = len(non_nulls)
        
        # 1. 如果是 datetime64 序列 (优先转为秒数，避免 pd.to_numeric 将其误转为纳秒整型)
        if ptypes.is_datetime64_any_dtype(col_data):
            first_valid = non_nulls.iloc[0]
            return (col_data - first_valid).dt.total_seconds()

        # 2. 尝试直接作为数值解析
        numeric_col = pd.to_numeric(col_data, errors='coerce')
        if non_null_count > 0 and numeric_col.notna().sum() >= 0.95 * non_null_count:
            non_null_num = numeric_col.dropna()
            first_valid = non_null_num.iloc[0] if not non_null_num.empty else None
            if first_valid is not None:
                return numeric_col - first_valid

        # 2. 如果包含 datetime.time 对象 (Excel 单元格时间格式常见)
        sample_val = non_nulls.iloc[0]
        if isinstance(sample_val, datetime.time):
            def time_to_sec(t):
                if isinstance(t, datetime.time):
                    return t.hour * 3600 + t.minute * 60 + t.second + t.microsecond / 1e6
                try:
                    t_dt = pd.to_datetime(str(t))
                    return t_dt.hour * 3600 + t_dt.minute * 60 + t_dt.second + t_dt.microsecond / 1e6
                except Exception:
                    return np.nan
            sec_series = col_data.apply(time_to_sec)
            valid_secs = sec_series.dropna()
            first_valid = valid_secs.iloc[0] if not valid_secs.empty else None
            if first_valid is not None:
                sec_vals = sec_series.values
                diff = np.diff(sec_vals, prepend=sec_vals[0])
                # 自动识别跨午夜翻转 (当秒数突降超过 12 小时即 43200 秒时，累加 86400 秒)
                rollovers = np.cumsum(diff < -43200.0) * 86400.0
                unrolled_secs = sec_vals + rollovers
                first_unrolled = unrolled_secs[0]
                return pd.Series(unrolled_secs - first_unrolled, index=col_data.index)

        # 3. 如果是 datetime64 序列
        if ptypes.is_datetime64_any_dtype(col_data):
            first_valid = non_nulls.iloc[0]
            return (col_data - first_valid).dt.total_seconds()

        # 4. 如果是格式化字符串 (如 '93:33:34.69' 累计工程时间、'00:10:00.00'、'00:10.0')
        try:
            str_series = col_data.astype(str).str.strip()
            def fix_colon_fmt(s):
                if not isinstance(s, str) or not s:
                    return s
                parts = s.split(':')
                if len(parts) == 2:
                    return f"00:{s}"
                return s
                
            fixed_series = str_series.apply(fix_colon_fmt)
            td = pd.to_timedelta(fixed_series, errors='coerce')
            if td.notna().sum() >= 0.8 * non_null_count:
                sec_series = td.dt.total_seconds()
                valid_secs = sec_series.dropna()
                first_valid = valid_secs.iloc[0] if not valid_secs.empty else None
                if first_valid is not None:
                    return sec_series - first_valid
        except Exception:
            pass

        # 5. 自定义字符串拆分兜底 (精准兼容超过 24 小时的工程累计时间文本如 93:33:34.69)
        try:
            def split_to_sec(s):
                try:
                    parts = str(s).strip().split(':')
                    if len(parts) == 3:
                        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
                    elif len(parts) == 2:
                        return float(parts[0]) * 60 + float(parts[1])
                except Exception:
                    return np.nan
                return np.nan
                
            sec_series = col_data.apply(split_to_sec)
            if sec_series.notna().sum() >= 0.8 * non_null_count:
                valid_secs = sec_series.dropna()
                first_valid = valid_secs.iloc[0] if not valid_secs.empty else None
                if first_valid is not None:
                    return sec_series - first_valid
        except Exception:
            pass

        return None

    def compute_cycle_time(self, df, cycle_col, time_col):
        """计算循环时间：每个循环段从0开始计时"""
        try:
            df = df.copy()
            if not cycle_col or cycle_col not in df.columns or not time_col or time_col not in df.columns:
                df['循环时间'] = 0.0
                df['循环时间差(s)'] = 0.0
                return df

            time_diff_col = f"{time_col}_时间差(s)"
            t_col_to_use = time_diff_col if time_diff_col in df.columns else time_col

            df['循环时间'] = 0.0
            for g_id, group_df in df.groupby(cycle_col):
                g_t = pd.to_numeric(group_df[t_col_to_use], errors='coerce').ffill().fillna(0).values
                if len(g_t) > 0:
                    df.loc[group_df.index, '循环时间'] = g_t - g_t[0]
            df['循环时间差(s)'] = df['循环时间']
            return df
        except Exception as e:
            if hasattr(self, 'logger') and self.logger:
                self.logger.error(f"计算循环时间失败: {str(e)}")
            df['循环时间'] = 0.0
            df['循环时间差(s)'] = 0.0
            return df

    def compute_step_time(self, df, cycle_col, step_col, time_col):
        """计算工步时间：在同一个循环内，每个连续的工步段从0开始计时"""
        try:
            df = df.copy()
            if not cycle_col or cycle_col not in df.columns or not step_col or step_col not in df.columns or not time_col or time_col not in df.columns:
                df['工步时间'] = 0.0
                df['工步时间差(s)'] = 0.0
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
            df['工步时间差(s)'] = df['工步时间']
            return df
        except Exception as e:
            if hasattr(self, 'logger') and self.logger:
                self.logger.error(f"计算工步时间失败: {str(e)}")
            df['工步时间'] = 0.0
            df['工步时间差(s)'] = 0.0
            return df

    def recompute_all_time_diffs(self, df, time_col, cycle_col=None, step_col=None):
        """仅对有效的时间列生成相对时间差列，绝不删除表格原有的任何列，也不对非时间列生成后缀列"""
        if df is None or df.empty or not time_col or time_col not in df.columns:
            return df

        # 仅清理多重重复后缀如 '_时间差(s)_时间差(s)'，绝不删除原始 Excel 自带列
        for c in list(df.columns):
            if c.endswith('_时间差(s)_时间差(s)'):
                try:
                    df.drop(columns=[c], inplace=True)
                except Exception:
                    pass

        # 仅当 time_col 是真实的时间列（且非 循环列 / 工步列）时，才生成 对应时间列的时间差(s)
        is_valid_time = (time_col != cycle_col) and (time_col != step_col)
        if is_valid_time:
            base_time_name = time_col.replace('_时间差(s)', '')
            time_diff_col = f"{base_time_name}_时间差(s)"
            t_diff = self.calculate_time_diff_series(df, time_col)
            if t_diff is not None:
                df[time_diff_col] = t_diff

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
