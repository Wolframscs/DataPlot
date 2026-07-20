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
            'legend_cols': self.legend_cols.get(),
            
            # X & Y axes configurations
            'x_min': self.x_min_var.get(),
            'x_max': self.x_max_var.get(),
            'x_title': self.x_title.get() if hasattr(self, 'x_title') else 'Time/s',
            
            'y1_min': self.y_settings[0]['min'].get() if len(self.y_settings) > 0 else '20',
            'y1_max': self.y_settings[0]['max'].get() if len(self.y_settings) > 0 else '60',
            'y1_title': self.y_settings[0]['title'].get() if len(self.y_settings) > 0 else 'Temperature/℃',
            
            'y2_min': self.y_settings[1]['min'].get() if len(self.y_settings) > 1 else '20',
            'y2_max': self.y_settings[1]['max'].get() if len(self.y_settings) > 1 else '60',
            'y2_title': self.y_settings[1]['title'].get() if len(self.y_settings) > 1 else 'Temperature/℃',
            
            'y3_min': self.y_settings[2]['min'].get() if len(self.y_settings) > 2 else '0',
            'y3_max': self.y_settings[2]['max'].get() if len(self.y_settings) > 2 else '150',
            'y3_title': self.y_settings[2]['title'].get() if len(self.y_settings) > 2 else 'HeatingPower/W',
            
            # Advanced margin variables
            'adv_left_margin_mult': self.adv_left_margin_mult.get(),
            'adv_left_margin_min_px': self.adv_left_margin_min_px.get(),
            'adv_left_margin_min_pct': self.adv_left_margin_min_pct.get(),
            
            'adv_y3_margin_mult': self.adv_y3_margin_mult.get(),
            'adv_y3_margin_min_px': self.adv_y3_margin_min_px.get(),
            'adv_y3_max_right_pct': self.adv_y3_max_right_pct.get(),
            
            'adv_y2_margin_mult': self.adv_y2_margin_mult.get(),
            'adv_y2_margin_min_px': self.adv_y2_margin_min_px.get(),
            'adv_y2_max_right_pct': self.adv_y2_max_right_pct.get(),
            
            'adv_y1_margin_mult': self.adv_y1_margin_mult.get(),
            'adv_y1_margin_min_px': self.adv_y1_margin_min_px.get(),
            'adv_y1_max_right_pct': self.adv_y1_max_right_pct.get()
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
                self.font_size.set(settings.get('font_size', '18'))
                self.legend_y.set(settings.get('legend_y', '1.02'))
                self.legend_x_positions_str.set(settings.get('legend_x_positions', "0, 0.3, 0.6"))
                self.frame_width.set(settings.get('frame_width', '1.5'))
                self.line_width.set(settings.get('line_width', '1.5'))
                self.auto_downsample.set(settings.get('auto_downsample', True))
                max_pts = settings.get('max_plot_points', 'None')
                if max_pts not in ["5e4", "10e4", "20e4", "50e4", "100e4", "None"]:
                    max_pts = 'None'
                self.max_plot_points.set(max_pts)
                self.legend_font_size.set(settings.get('legend_font_size', '18'))
                self.legend_cols.set(settings.get('legend_cols', '1'))
                
                # Load X & Y axes configs
                self.x_min_var.set(settings.get('x_min', ''))
                self.x_max_var.set(settings.get('x_max', ''))
                if hasattr(self, 'x_title') and 'x_title' in settings:
                    self.x_title.setText(settings['x_title'])
                
                if len(self.y_settings) > 0:
                    self.y_settings[0]['min'].set(settings.get('y1_min', '20'))
                    self.y_settings[0]['max'].set(settings.get('y1_max', '60'))
                    self.y_settings[0]['title'].set(settings.get('y1_title', 'Temperature/℃'))
                if len(self.y_settings) > 1:
                    self.y_settings[1]['min'].set(settings.get('y2_min', '20'))
                    self.y_settings[1]['max'].set(settings.get('y2_max', '60'))
                    self.y_settings[1]['title'].set(settings.get('y2_title', 'Temperature/℃'))
                if len(self.y_settings) > 2:
                    self.y_settings[2]['min'].set(settings.get('y3_min', '0'))
                    self.y_settings[2]['max'].set(settings.get('y3_max', '150'))
                    self.y_settings[2]['title'].set(settings.get('y3_title', 'HeatingPower/W'))
                    
                # Load advanced margin variables
                self.adv_left_margin_mult.set(settings.get('adv_left_margin_mult', '4.5'))
                self.adv_left_margin_min_px.set(settings.get('adv_left_margin_min_px', '80'))
                self.adv_left_margin_min_pct.set(settings.get('adv_left_margin_min_pct', '0.08'))
                
                self.adv_y3_margin_mult.set(settings.get('adv_y3_margin_mult', '9.5'))
                self.adv_y3_margin_min_px.set(settings.get('adv_y3_margin_min_px', '170'))
                self.adv_y3_max_right_pct.set(settings.get('adv_y3_max_right_pct', '0.83'))
                
                self.adv_y2_margin_mult.set(settings.get('adv_y2_margin_mult', '4.0'))
                self.adv_y2_margin_min_px.set(settings.get('adv_y2_margin_min_px', '75'))
                self.adv_y2_max_right_pct.set(settings.get('adv_y2_max_right_pct', '0.93'))
                
                self.adv_y1_margin_mult.set(settings.get('adv_y1_margin_mult', '1.5'))
                self.adv_y1_margin_min_px.set(settings.get('adv_y1_margin_min_px', '20'))
                self.adv_y1_max_right_pct.set(settings.get('adv_y1_max_right_pct', '0.97'))
        except FileNotFoundError:
            pass  # 使用默认设置
        except Exception as e:
            self.logger.error(f"加载设置失败: {str(e)}")
        finally:
            self._is_loading_settings = False
