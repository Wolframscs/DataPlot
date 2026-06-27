import json

class SettingsMixin:
    def save_settings(self):
        """保存当前设置"""
        settings = {
            'font_family': self.font_family.get(),
            'font_size': self.font_size.get(),
            'legend_y': self.legend_y.get(),
            'legend_x_positions': self.legend_x_positions_str.get(),
            'frame_width': self.frame_width.get(),
            'line_width': self.line_width.get(),
            'auto_downsample': self.auto_downsample.get(),
            'max_plot_points': self.max_plot_points.get(),
            'legend_font_size': self.legend_font_size.get(),
            'legend_cols': self.legend_cols.get()
        }
        try:
            with open('settings.json', 'w') as f:
                json.dump(settings, f)
        except Exception as e:
            self.logger.error(f"保存设置失败: {str(e)}")

    def load_settings(self):
        """加载保存的设置"""
        self._is_loading_settings = True
        try:
            with open('settings.json', 'r') as f:
                settings = json.load(f)
                self.font_family.set(settings.get('font_family', 'SimHei'))
                self.font_size.set(settings.get('font_size', '16'))
                self.legend_y.set(settings.get('legend_y', '1.02'))
                self.legend_x_positions_str.set(settings.get('legend_x_positions', "0, 0.3, 0.6"))
                self.frame_width.set(settings.get('frame_width', '1.5'))
                self.line_width.set(settings.get('line_width', '1.5'))
                self.auto_downsample.set(settings.get('auto_downsample', True))
                self.max_plot_points.set(settings.get('max_plot_points', '10000'))
                self.legend_font_size.set(settings.get('legend_font_size', '12'))
                self.legend_cols.set(settings.get('legend_cols', '1'))
        except FileNotFoundError:
            pass  # 使用默认设置
        except Exception as e:
            self.logger.error(f"加载设置失败: {str(e)}")
        finally:
            self._is_loading_settings = False
