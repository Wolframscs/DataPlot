import os
import sys
import queue
import logging
import gc
import threading
import tkinter as tk
from tkinter import ttk, messagebox
import matplotlib.pyplot as plt
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg, NavigationToolbar2Tk
import pandas as pd
import openpyxl

# Import mixins with DataPlot_ prefix
from DataPlot_data_loader import DataLoaderMixin
from DataPlot_battery_math import BatteryMathMixin
from DataPlot_plot_engine import PlotEngineMixin
from DataPlot_excel_exporter import ExcelExporterMixin
from DataPlot_settings_manager import SettingsMixin

def resource_path(relative_path):
    """ 获取资源的绝对路径，兼容开发环境与PyInstaller打包后的环境 """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class PlotterGUI(DataLoaderMixin, BatteryMathMixin, PlotEngineMixin, ExcelExporterMixin, SettingsMixin):
    def __init__(self, root):
        self.root = root
        self.root.title("DataPlot")
        try:
            icon_path = resource_path('icon.ico')
            if os.path.exists(icon_path):
                self.root.iconbitmap(icon_path)
        except Exception:
            pass
        self.result_df = None
        self.CHUNK_SIZE = 100000
        self.msg_queue = queue.Queue()
        
        # 设置日志
        logging.basicConfig(
            filename='DataPlot.log',
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s'
        )
        self.logger = logging.getLogger('DataPlot')
        
        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)
        
        try:
            # 定义线型选项
            self.line_styles_dict = {
                '实线': '-',
                '虚线': '--',
                '点线': ':',
                '点划线': '-.',
            }
            
            self.line_styles = []
            
            screen_width = self.root.winfo_screenwidth()
            screen_height = self.root.winfo_screenheight()
            
            scale_factor = min(screen_width / 1920.0, screen_height / 1080.0)
            scale_factor = max(0.8, min(scale_factor, 2.0))
            
            window_width = int(screen_width * 0.9)
            window_height = int(screen_height * 0.8)
            
            window_width = max(1100, min(window_width, screen_width - 100))
            window_height = max(750, min(window_height, screen_height - 100))
            
            x_offset = (screen_width - window_width) // 2
            y_offset = (screen_height - window_height) // 2
            self.root.geometry(f"{window_width}x{window_height}+{x_offset}+{y_offset}")
            
            self.GUI_FONT_SIZE = max(10, min(18, int(12 * scale_factor)))
            default_font = ('Microsoft YaHei', self.GUI_FONT_SIZE)
            
            self.fig_w = max(8.0, min(16.0, 12.0 * scale_factor))
            self.fig_h = max(5.0, min(11.0, 8.0 * scale_factor))

            self.root.grid_rowconfigure(0, weight=1)
            self.root.grid_columnconfigure(0, weight=1)

            self.LABEL_WIDTH = 8
            self.SMALL_LABEL_WIDTH = 6
            self.Y_LABEL_WIDTH = 6
            self.ENTRY_WIDTH = 8
            self.COMBO_WIDTH = 6
            self.TITLE_ENTRY_WIDTH = 15
            self.SETTING_LABEL_WIDTH = 8
            self.LABEL_PADX = (8, 0)
            self.WIDGET_PADX = 1
            
            style = ttk.Style()
            style.configure('Big.TButton', font=default_font)
            style.configure('Custom.TLabelframe.Label', font=default_font)
            style.configure('Custom.TCheckbutton', font=default_font)
            
            self.root.option_add('*Font', default_font)
            label_style = {'width': 10, 'anchor': tk.W}
            
            self.version = "1.0.0"
            self._update_timer = None
            self._last_plot_time = 0
            self._is_loading_settings = False
            self._is_syncing_x = False
            self.auto_downsample = tk.BooleanVar(value=True)
            self.max_plot_points = tk.StringVar(value="10000")
            self.legend_font_size = tk.StringVar(value="12")
            self.legend_cols = tk.StringVar(value="1")
            self.x_min_var = tk.StringVar(value="")
            self.x_max_var = tk.StringVar(value="")
            
            # 创建主框架
            self.main_frame = ttk.Frame(root, padding="10")
            self.main_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
            
            self.main_frame.grid_rowconfigure(0, weight=1)
            self.main_frame.grid_columnconfigure(1, weight=1)
            
            # 创建左侧控制面板框架
            control_frame = ttk.Frame(self.main_frame)
            self.control_frame = control_frame
            control_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S), padx=5)
            control_frame.grid_columnconfigure(0, weight=1)
            control_frame.grid_columnconfigure(1, weight=1)
            control_frame.grid_columnconfigure(2, weight=1)
            
            style = ttk.Style()
            style.configure('Custom.TLabelframe.Label', font=('Microsoft YaHei', 12))
            
            file_type_frame = ttk.Frame(control_frame)
            file_type_frame.grid(row=0, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)
            
            ttk.Label(file_type_frame, text="文件类型:", **label_style).grid(row=0, column=0, sticky=tk.W)
            self.file_type = tk.StringVar(value="raw")
            
            tk.Radiobutton(file_type_frame, text="FLOEFD", variable=self.file_type, value="raw", 
                          command=self.update_file_type,
                          font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=1)
            tk.Radiobutton(file_type_frame, text="GENERAL", variable=self.file_type, value="processed", 
                          command=self.update_file_type,
                          font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=2)
            tk.Radiobutton(file_type_frame, text="BATTERY", variable=self.file_type, value="battery", 
                          command=self.update_file_type,
                          font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=3)
            
            # 统一输入控制表格（对齐 浏览、csv2xlsx、读取 按钮）
            input_grid_frame = ttk.Frame(control_frame)
            input_grid_frame.grid(row=1, column=0, columnspan=3, sticky=(tk.W, tk.E), pady=5)
            
            # Row 0: 文件路径
            ttk.Label(input_grid_frame, text="文件路径:", **label_style).grid(row=0, column=0, sticky=tk.W)
            self.file_path = tk.StringVar()
            self.file_entry = ttk.Entry(input_grid_frame, textvariable=self.file_path, width=30,
                                      font=('Microsoft YaHei', self.GUI_FONT_SIZE))
            self.file_entry.grid(row=0, column=1, padx=10, sticky=tk.W)
            self.browse_btn = ttk.Button(input_grid_frame, text="浏览", command=self.browse_file,
                      style='Big.TButton', width=10)
            self.browse_btn.grid(row=0, column=2, sticky=tk.W)
            
            # Row 1: 表格名称
            ttk.Label(input_grid_frame, text="表格名称:", **label_style).grid(row=1, column=0, sticky=tk.W)
            self.sheet_name = tk.StringVar()
            self.sheet_combo = ttk.Combobox(input_grid_frame, textvariable=self.sheet_name,
                                          width=30, state='readonly', 
                                          font=('Microsoft YaHei', self.GUI_FONT_SIZE))
            self.sheet_combo.grid(row=1, column=1, sticky=tk.W, padx=10)
            self.process_btn = ttk.Button(input_grid_frame, text="读取", command=self.process_data,
                                          style='Big.TButton', width=10)
            self.process_btn.grid(row=1, column=2, sticky=tk.W)
            
            # Row 2: 参数与 csv2xlsx 按钮
            self.start_row_frame = ttk.Frame(input_grid_frame)
            self.start_row_frame.grid(row=2, column=0, columnspan=2, sticky=tk.W)
            
            # SKIP (只对 raw 可见)
            self.skip_label = ttk.Label(self.start_row_frame, text="SKIP:", width=5, anchor=tk.W)
            self.skip_rows_var = tk.StringVar(value="3")
            self.skip_entry = ttk.Entry(self.start_row_frame, textvariable=self.skip_rows_var, width=6)
            
            # NULL (只对 raw 可见)
            self.null_label = ttk.Label(self.start_row_frame, text="NULL:", width=6, anchor=tk.W)
            self.start_skip_var = tk.StringVar(value="8")
            self.null_entry = ttk.Entry(self.start_row_frame, textvariable=self.start_skip_var, width=6)
            
            # 起始行
            self.start_row_label = ttk.Label(self.start_row_frame, text="起始行:", width=8, anchor=tk.W)
            self.start_row = tk.StringVar(value="1")
            self.start_row_entry = ttk.Entry(self.start_row_frame, textvariable=self.start_row, width=6)
            
            # csv2xlsx 按钮 (与 读取 垂直对齐)
            self.csv2xlsx_btn = ttk.Button(input_grid_frame, text="csv2xlsx", command=self.convert_csv_to_xlsx,
                                           style='Big.TButton', width=10)
            self.csv2xlsx_btn.grid(row=2, column=2, sticky=tk.W)
            
            # 电池数据配置框架
            self.battery_filter_frame = ttk.LabelFrame(control_frame, text="电池数据配置", padding="5", style='Custom.TLabelframe')
            
            self.cycle_col = tk.StringVar()
            self.step_col = tk.StringVar()
            self.time_col = tk.StringVar()
            self.voltage_col = tk.StringVar()
            self.current_col = tk.StringVar()
            self.cycle_compare_var = tk.BooleanVar(value=False)
            self.cycle_compare_range_var = tk.StringVar(value="1, max, 10")
            self.time_step_var = tk.StringVar(value="10")
            self.filter_type_var = tk.StringVar(value="无")
            self.filter_window_var = tk.StringVar(value="15")
            self.sg_poly_var = tk.StringVar(value="2")
            self.compare_x_var = tk.StringVar(value="容量（计算）")
            self.current_compare_type = tk.StringVar(value="regular")
            self.cc_polarity_var = tk.StringVar(value="正")
            self.dqdv_min_var = tk.StringVar(value="")
            self.dqdv_max_var = tk.StringVar(value="")
            self.dqdv_title_var = tk.StringVar(value="")
            self.voltage_scale_var = tk.StringVar(value="1")
            self.current_scale_var = tk.StringVar(value="1")
            
            ttk.Label(self.battery_filter_frame, text="循环列:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=0, sticky=tk.W, padx=5)
            self.cycle_col_combo = ttk.Combobox(self.battery_filter_frame, textvariable=self.cycle_col, state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=10)
            self.cycle_col_combo.grid(row=0, column=1, sticky=tk.W, padx=5)
            self.cycle_col_combo.bind('<<ComboboxSelected>>', self.on_cycle_col_changed)
            
            ttk.Label(self.battery_filter_frame, text="工步列:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=2, sticky=tk.W, padx=5)
            self.step_col_combo = ttk.Combobox(self.battery_filter_frame, textvariable=self.step_col, state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=10)
            self.step_col_combo.grid(row=0, column=3, sticky=tk.W, padx=5)
            self.step_col_combo.bind('<<ComboboxSelected>>', self.on_step_col_changed)
            
            ttk.Label(self.battery_filter_frame, text="时间列:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=4, sticky=tk.W, padx=5)
            self.time_col_combo = ttk.Combobox(self.battery_filter_frame, textvariable=self.time_col, state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=10)
            self.time_col_combo.grid(row=0, column=5, sticky=tk.W, padx=5)
            self.time_col_combo.bind('<<ComboboxSelected>>', self.on_time_col_changed)

            ttk.Label(self.battery_filter_frame, text="循环筛选:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=1, column=0, sticky=tk.W, padx=5)
            self.cycle_filter = tk.StringVar(value="全部")
            self.cycle_filter_combo = ttk.Combobox(self.battery_filter_frame, textvariable=self.cycle_filter, state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=10)
            self.cycle_filter_combo.grid(row=1, column=1, sticky=tk.W, padx=5)
            
            ttk.Label(self.battery_filter_frame, text="工步筛选:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=1, column=2, sticky=tk.W, padx=5)
            self.step_filter = tk.StringVar(value="全部")
            self.step_filter_combo = ttk.Combobox(self.battery_filter_frame, textvariable=self.step_filter, state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=10)
            self.step_filter_combo.grid(row=1, column=3, sticky=tk.W, padx=5)
            self.step_filter_combo.bind('<<ComboboxSelected>>', lambda e: [self.update_listboxes(), self.delayed_update()])
            
            self.battery_calc_btn = ttk.Button(self.battery_filter_frame, text="应用", command=self.update_plot,
                       style='Big.TButton', width=10)
            self.battery_calc_btn.grid(row=1, column=4, columnspan=2, sticky=tk.W, padx=5)

            self.cycle_compare_cb = tk.Checkbutton(self.battery_filter_frame, text="启用循环对比", 
                                                   variable=self.cycle_compare_var, 
                                                   command=self.on_cycle_compare_toggle,
                                                   font=('Microsoft YaHei', self.GUI_FONT_SIZE))
            self.cycle_compare_cb.grid(row=2, column=0, columnspan=2, sticky=tk.W, padx=5)

            ttk.Label(self.battery_filter_frame, text="电压列:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=2, column=2, sticky=tk.W, padx=5)
            self.voltage_col_combo = ttk.Combobox(self.battery_filter_frame, textvariable=self.voltage_col, state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=10)
            self.voltage_col_combo.grid(row=2, column=3, sticky=tk.W, padx=5)
            self.voltage_col_combo.bind('<<ComboboxSelected>>', lambda e: self.delayed_update())

            ttk.Label(self.battery_filter_frame, text="电流列:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=2, column=4, sticky=tk.W, padx=5)
            self.current_col_combo = ttk.Combobox(self.battery_filter_frame, textvariable=self.current_col, state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=10)
            self.current_col_combo.grid(row=2, column=5, sticky=tk.W, padx=5)
            self.current_col_combo.bind('<<ComboboxSelected>>', lambda e: self.delayed_update())

            # Row 3: 电压/电流比例因子
            ttk.Label(self.battery_filter_frame, text="电压比例:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=3, column=2, sticky=tk.W, padx=5)
            self.voltage_scale_entry = ttk.Entry(self.battery_filter_frame, textvariable=self.voltage_scale_var, font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=10)
            self.voltage_scale_entry.grid(row=3, column=3, sticky=tk.W, padx=5)
            self.voltage_scale_entry.bind('<Return>', lambda e: self.delayed_update())
            
            ttk.Label(self.battery_filter_frame, text="电流比例:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=3, column=4, sticky=tk.W, padx=5)
            self.current_scale_entry = ttk.Entry(self.battery_filter_frame, textvariable=self.current_scale_var, font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=10)
            self.current_scale_entry.grid(row=3, column=5, sticky=tk.W, padx=5)
            self.current_scale_entry.bind('<Return>', lambda e: self.delayed_update())

            # 循环对比配置框架
            self.cycle_compare_frame = ttk.LabelFrame(control_frame, text="循环对比配置", padding="5", style='Custom.TLabelframe')
            
            ttk.Label(self.cycle_compare_frame, text="对比范围:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=0, sticky=tk.W, padx=5)
            self.cycle_range_entry = ttk.Entry(self.cycle_compare_frame, textvariable=self.cycle_compare_range_var, font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=12)
            self.cycle_range_entry.grid(row=0, column=1, sticky=tk.W, padx=5)
            self.cycle_range_entry.bind('<Return>', lambda e: self.delayed_update())
            
            ttk.Label(self.cycle_compare_frame, text="步长(TimeStep):", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=2, sticky=tk.W, padx=5)
            self.time_step_entry = ttk.Entry(self.cycle_compare_frame, textvariable=self.time_step_var, font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=6)
            self.time_step_entry.grid(row=0, column=3, sticky=tk.W, padx=5)
            self.time_step_entry.bind('<Return>', lambda e: self.delayed_update())

            ttk.Label(self.cycle_compare_frame, text="CC极性:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=4, sticky=tk.W, padx=5)
            self.cc_polarity_combo = ttk.Combobox(self.cycle_compare_frame, textvariable=self.cc_polarity_var, values=["正", "负"], state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=4)
            self.cc_polarity_combo.grid(row=0, column=5, sticky=tk.W, padx=5)
            self.cc_polarity_combo.bind('<<ComboboxSelected>>', lambda e: self.delayed_update())
            
            ttk.Label(self.cycle_compare_frame, text="滤波方式:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=1, column=0, sticky=tk.W, padx=5)
            self.filter_type_combo = ttk.Combobox(self.cycle_compare_frame, textvariable=self.filter_type_var, values=["无", "Savitzky-Golay", "移动平均", "中值滤波"], state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=12)
            self.filter_type_combo.grid(row=1, column=1, sticky=tk.W, padx=5)
            self.filter_type_combo.bind('<<ComboboxSelected>>', lambda e: self.delayed_update())
            
            ttk.Label(self.cycle_compare_frame, text="窗口大小:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=1, column=2, sticky=tk.W, padx=5)
            self.filter_window_entry = ttk.Entry(self.cycle_compare_frame, textvariable=self.filter_window_var, font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=6)
            self.filter_window_entry.grid(row=1, column=3, sticky=tk.W, padx=5)
            self.filter_window_entry.bind('<Return>', lambda e: self.delayed_update())
            
            ttk.Label(self.cycle_compare_frame, text="SG阶数:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=1, column=4, sticky=tk.W, padx=5)
            self.sg_poly_combo = ttk.Combobox(self.cycle_compare_frame, textvariable=self.sg_poly_var, values=["2", "3", "4"], state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=4)
            self.sg_poly_combo.grid(row=1, column=5, sticky=tk.W, padx=5)
            self.sg_poly_combo.bind('<<ComboboxSelected>>', lambda e: self.delayed_update())
            
            # Row 2: 循环X轴 直接网格对齐
            ttk.Label(self.cycle_compare_frame, text="循环X轴:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=2, column=0, sticky=tk.W, padx=5)
            self.compare_x_combo = ttk.Combobox(self.cycle_compare_frame, textvariable=self.compare_x_var, state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=12)
            self.compare_x_combo.grid(row=2, column=1, sticky=tk.W, padx=5)
            self.compare_x_combo.bind('<<ComboboxSelected>>', lambda e: self.delayed_update())
            
            # 对比类型选择子框架
            compare_type_frame = ttk.Frame(self.cycle_compare_frame)
            compare_type_frame.grid(row=2, column=2, columnspan=4, sticky=tk.W, pady=2, padx=5)
            
            tk.Radiobutton(compare_type_frame, text="常规对比", variable=self.current_compare_type, value="regular", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=0, padx=5)
            tk.Radiobutton(compare_type_frame, text="dQ/dV", variable=self.current_compare_type, value="dqdv", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=1, padx=5)
            tk.Radiobutton(compare_type_frame, text="dV/dQ", variable=self.current_compare_type, value="dvdq", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=2, padx=5)
            
            # Row 3: 循环Y轴 直接网格对齐 + 内部输入参数的子框架
            ttk.Label(self.cycle_compare_frame, text="循环Y轴:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=3, column=0, sticky=tk.W, padx=5)
            
            compare_row3_inputs_frame = ttk.Frame(self.cycle_compare_frame)
            compare_row3_inputs_frame.grid(row=3, column=1, columnspan=5, sticky=tk.W, pady=2)
            
            ttk.Label(compare_row3_inputs_frame, text="Min:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=0, sticky=tk.W, padx=(5,0))
            self.dqdv_min_entry = ttk.Entry(compare_row3_inputs_frame, textvariable=self.dqdv_min_var, font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=6)
            self.dqdv_min_entry.grid(row=0, column=1, sticky=tk.W, padx=2)
            self.dqdv_min_entry.bind('<Return>', lambda e: self.update_plot())
            
            ttk.Label(compare_row3_inputs_frame, text="Max:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=2, sticky=tk.W, padx=(5,0))
            self.dqdv_max_entry = ttk.Entry(compare_row3_inputs_frame, textvariable=self.dqdv_max_var, font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=6)
            self.dqdv_max_entry.grid(row=0, column=3, sticky=tk.W, padx=2)
            self.dqdv_max_entry.bind('<Return>', lambda e: self.update_plot())
            
            ttk.Label(compare_row3_inputs_frame, text="标题:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=4, sticky=tk.W, padx=(5,0))
            self.dqdv_title_entry = ttk.Entry(compare_row3_inputs_frame, textvariable=self.dqdv_title_var, font=('Microsoft YaHei', self.GUI_FONT_SIZE), width=10)
            self.dqdv_title_entry.grid(row=0, column=5, sticky=tk.W, padx=2)
            self.dqdv_title_entry.bind('<Return>', lambda e: self.update_plot())
            
            self.compare_apply_btn = ttk.Button(compare_row3_inputs_frame, text="应用", command=self.update_plot, style='Big.TButton', width=5)
            self.compare_apply_btn.grid(row=0, column=6, padx=(10,2), sticky=tk.W)

            # 单Y轴绘图选项框架
            self.plot_frame = ttk.LabelFrame(control_frame, text="绘图选项", padding="5", style='Custom.TLabelframe')
            self.plot_frame.grid(row=5, column=0, columnspan=3, pady=5, sticky=(tk.W, tk.E))
            
            self.plot_scrollbar = ttk.Scrollbar(self.plot_frame, orient="vertical")
            self.plot_canvas = tk.Canvas(self.plot_frame, borderwidth=0, highlightthickness=0, yscrollcommand=self.plot_scrollbar.set, height=360)
            self.plot_scrollbar.configure(command=self.plot_canvas.yview)
            
            self.plot_canvas.grid(row=0, column=0, sticky=(tk.N, tk.S, tk.E, tk.W))
            self.plot_scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
            self.plot_frame.grid_columnconfigure(0, weight=1)
            self.plot_frame.grid_rowconfigure(0, weight=1)
            
            self.plot_content_frame = ttk.Frame(self.plot_canvas)
            canvas_window = self.plot_canvas.create_window((0, 0), window=self.plot_content_frame, anchor="nw")
            
            self.plot_content_frame.bind("<Configure>", lambda e: self.plot_canvas.configure(scrollregion=self.plot_canvas.bbox("all")))
            self.plot_canvas.bind("<Configure>", lambda e: self.plot_canvas.itemconfig(canvas_window, width=e.width))
            
            def _on_mousewheel(event):
                self.plot_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
            def _bind_mousewheel(event):
                self.plot_canvas.bind_all("<MouseWheel>", _on_mousewheel)
            def _unbind_mousewheel(event):
                self.plot_canvas.unbind_all("<MouseWheel>")
            self.plot_canvas.bind("<Enter>", _bind_mousewheel)
            self.plot_canvas.bind("<Leave>", _unbind_mousewheel)
            
            plot_frame = self.plot_content_frame
            
            # X轴控制行子框架 (独立列网格，防止被宽列表框拉伸而截断)
            x_control_frame = ttk.Frame(plot_frame)
            x_control_frame.grid(row=0, column=0, columnspan=4, sticky=(tk.W, tk.E), pady=5)
            
            # X轴选择与范围控制
            ttk.Label(x_control_frame, text="X轴:", 
                     font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=0, sticky=tk.W)
            self.x_axis = tk.StringVar()
            self.x_combo = ttk.Combobox(x_control_frame, textvariable=self.x_axis, 
                                      state='readonly', font=('Microsoft YaHei', self.GUI_FONT_SIZE),
                                      width=10) # 减少下拉框宽度
            self.x_combo.grid(row=0, column=1, sticky=tk.W, padx=5)
            
            # X轴范围 Min / Max
            ttk.Label(x_control_frame, text="Min:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=2, padx=(5,0), sticky=tk.W)
            self.x_min_entry = ttk.Entry(x_control_frame, textvariable=self.x_min_var, width=6,
                                         font=('Microsoft YaHei', self.GUI_FONT_SIZE))
            self.x_min_entry.grid(row=0, column=3, padx=2, sticky=tk.W)
            
            ttk.Label(x_control_frame, text="Max:", font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=4, padx=(5,0), sticky=tk.W)
            self.x_max_entry = ttk.Entry(x_control_frame, textvariable=self.x_max_var, width=6,
                                         font=('Microsoft YaHei', self.GUI_FONT_SIZE))
            self.x_max_entry.grid(row=0, column=5, padx=2, sticky=tk.W)
            
            # X轴标题
            ttk.Label(x_control_frame, text="标题:").grid(row=0, column=6, padx=(5,0), sticky=tk.W)
            self.x_title = ttk.Entry(x_control_frame, width=10, font=('Microsoft YaHei', self.GUI_FONT_SIZE)) # 减少标题宽度
            self.x_title.grid(row=0, column=7, padx=2, sticky=tk.W)
            self.x_title.insert(0, "Time/s")
            
            # 应用按钮
            self.x_apply_btn = ttk.Button(x_control_frame, text="应用", command=self.update_plot,
                                          style='Big.TButton', width=5)
            self.x_apply_btn.grid(row=0, column=8, padx=(5,2), sticky=tk.W)
            
            # Y轴选择和按钮
            y_frame = ttk.Frame(plot_frame)
            y_frame.grid(row=1, column=0, columnspan=4, sticky=(tk.W, tk.E), padx=5)
            ttk.Label(y_frame, text="Y轴:", 
                     font=('Microsoft YaHei', self.GUI_FONT_SIZE)).grid(row=0, column=0, sticky=tk.W)
            
            self.y_listboxes = []
            self.y_selections = [[], [], []]
            
            for i in range(3):
                frame = ttk.Frame(y_frame)
                frame.grid(row=0, column=i+1, padx=2)
                
                ttk.Label(frame, text=f"Y{i+1}:", 
                         font=('Microsoft YaHei', self.GUI_FONT_SIZE)
                         ).grid(row=0, column=0, columnspan=2, sticky=tk.W)
                
                listbox = tk.Listbox(frame, selectmode=tk.EXTENDED, height=5, width=12,
                                   exportselection=False,
                                   font=('Microsoft YaHei', self.GUI_FONT_SIZE))
                listbox.grid(row=1, column=0, sticky=(tk.N, tk.S, tk.W, tk.E))
                listbox.bind('<<ListboxSelect>>', self.on_selection_change)
                self.y_listboxes.append(listbox)
                
                scrollbar = ttk.Scrollbar(frame, orient="vertical", command=listbox.yview)
                scrollbar.grid(row=1, column=1, sticky=(tk.N, tk.S))
                listbox.configure(yscrollcommand=scrollbar.set)
            
            button_frame = ttk.Frame(y_frame)
            button_frame.grid(row=0, column=4, padx=(25, 5))
            
            self.clear_btn = ttk.Button(button_frame, text="清除选择", 
                      command=self.clear_all_selections,
                      style='Big.TButton', width=10)
            self.clear_btn.grid(row=0, column=0, pady=2)
            
            self.select_all_btn = ttk.Button(button_frame, text="全部选择", 
                      command=self.select_all_y,
                      style='Big.TButton', width=10)
            self.select_all_btn.grid(row=1, column=0, pady=2)
            
            self.plot_btn = ttk.Button(button_frame, text="绘制图表", 
                      command=self.delayed_update,
                      style='Big.TButton', width=10)
            self.plot_btn.grid(row=2, column=0, pady=2)
            
            self.save_btn = ttk.Button(button_frame, text="保存数据", 
                      command=self.save_plot_data,
                      style='Big.TButton', width=10)
            self.save_btn.grid(row=3, column=0, pady=2)
            
            # 图例设置
            self.font_family = tk.StringVar(value="SimHei")
            legend_frame = ttk.Frame(plot_frame)
            legend_frame.grid(row=3, column=0, columnspan=4, sticky=(tk.W, tk.E), padx=5)
            ttk.Label(legend_frame, text="图例设置:", width=self.SETTING_LABEL_WIDTH).grid(
                row=0, column=0, sticky=tk.W)
            
            ttk.Label(legend_frame, text="垂直:", width=self.Y_LABEL_WIDTH).grid(
                row=0, column=1, sticky=tk.W, padx=self.LABEL_PADX)
            self.legend_y = tk.StringVar(value="1.02")
            ttk.Entry(legend_frame, textvariable=self.legend_y, width=self.ENTRY_WIDTH).grid(
                row=0, column=2, sticky=tk.W, padx=self.WIDGET_PADX)

            ttk.Label(legend_frame, text="水平:", width=self.Y_LABEL_WIDTH).grid(
                row=0, column=3, sticky=tk.W, padx=self.LABEL_PADX)
            self.legend_x_positions_str = tk.StringVar(value="0, 0.3, 0.7")
            ttk.Entry(legend_frame, textvariable=self.legend_x_positions_str, width=self.ENTRY_WIDTH).grid(
                row=0, column=4, sticky=tk.W, padx=self.WIDGET_PADX)
            self.legend_x_positions_str.trace_add('write', lambda *args: self.update_legend_positions())

            self.legend_visible = tk.BooleanVar(value=True)
            ttk.Checkbutton(legend_frame, text="显示图例", 
                          variable=self.legend_visible,
                          command=self.toggle_legend,
                          style='Custom.TCheckbutton').grid(
                row=0, column=5, padx=self.WIDGET_PADX, sticky=tk.W)

            ttk.Label(legend_frame, text="字体:", width=self.Y_LABEL_WIDTH).grid(
                row=1, column=1, sticky=tk.W, padx=self.LABEL_PADX)
            font_combo = ttk.Combobox(legend_frame, textvariable=self.font_family,
                                    values=["SimHei", "SimSun", "KaiTi", "FangSong", "Arial"],
                                    state='readonly', width=self.COMBO_WIDTH)
            font_combo.grid(row=1, column=2, padx=self.WIDGET_PADX, sticky=tk.W)

            ttk.Label(legend_frame, text="大小:", width=self.Y_LABEL_WIDTH).grid(
                row=1, column=3, sticky=tk.W, padx=self.LABEL_PADX)
            font_sizes = [str(size) for size in range(8, 25, 1)]
            legend_size_combo = ttk.Combobox(legend_frame, textvariable=self.legend_font_size,
                                           values=font_sizes,
                                           state='readonly', width=self.COMBO_WIDTH)
            legend_size_combo.grid(row=1, column=4, padx=self.WIDGET_PADX, sticky=tk.W)

            cols_subframe = ttk.Frame(legend_frame)
            cols_subframe.grid(row=1, column=5, padx=self.WIDGET_PADX, sticky=tk.W)
            ttk.Label(cols_subframe, text="列数:", width=4).grid(row=0, column=0, sticky=tk.W)
            legend_cols_combo = ttk.Combobox(cols_subframe, textvariable=self.legend_cols,
                                           values=["1", "2", "3", "4", "5"],
                                           state='readonly', width=3)
            legend_cols_combo.grid(row=0, column=1, padx=(2, 0), sticky=tk.W)

            # Y轴范围设置和标题
            y_ranges_frame = ttk.Frame(plot_frame)
            y_ranges_frame.grid(row=4, column=0, columnspan=4, sticky=(tk.W, tk.E), padx=5)
            
            self.y_settings = []
            default_y_configs = [
                {'min': '20', 'max': '60', 'title': 'Temperature/℃'},
                {'min': '20', 'max': '60', 'title': 'Temperature/℃'},
                {'min': '0', 'max': '150', 'title': 'HeatingPower/W'}
            ]
            for i in range(3):
                config = default_y_configs[i]
                settings = {
                    'min': tk.StringVar(value=config['min']),
                    'max': tk.StringVar(value=config['max']),
                    'title': tk.StringVar(value=config['title'])
                }
                self.y_settings.append(settings)
                
                ttk.Label(y_ranges_frame, text=f"Y{i+1}轴范围:", width=self.LABEL_WIDTH).grid(
                    row=i, column=0, sticky=tk.W)
                ttk.Label(y_ranges_frame, text="Min:", width=self.SMALL_LABEL_WIDTH).grid(
                    row=i, column=1, sticky=tk.W, padx=self.LABEL_PADX)
                ttk.Entry(y_ranges_frame, textvariable=self.y_settings[i]['min'], 
                         width=self.ENTRY_WIDTH).grid(row=i, column=2, sticky=tk.W, padx=self.WIDGET_PADX)
                ttk.Label(y_ranges_frame, text="Max:", width=self.SMALL_LABEL_WIDTH).grid(
                    row=i, column=3, sticky=tk.W, padx=self.LABEL_PADX)
                ttk.Entry(y_ranges_frame, textvariable=self.y_settings[i]['max'], 
                         width=self.ENTRY_WIDTH).grid(row=i, column=4, sticky=tk.W, padx=self.WIDGET_PADX)
                ttk.Label(y_ranges_frame, text="标题:", width=self.SMALL_LABEL_WIDTH).grid(
                    row=i, column=5, sticky=tk.W, padx=self.LABEL_PADX)
                ttk.Entry(y_ranges_frame, textvariable=self.y_settings[i]['title'], 
                         width=self.TITLE_ENTRY_WIDTH).grid(row=i, column=6, sticky=tk.W, padx=self.WIDGET_PADX)
                
                self.y_settings[i]['min'].trace_add('write', lambda *args, axis=i: self.update_y_axis(axis))
                self.y_settings[i]['max'].trace_add('write', lambda *args, axis=i: self.update_y_axis(axis))
                self.y_settings[i]['title'].trace_add('write', lambda *args, axis=i: self.update_y_axis(axis))

            ttk.Frame(plot_frame, height=10).grid(row=5, column=0, columnspan=4, pady=5)

            # 统一的绘图设置表格框架（对齐 大小、Y3 等控件）
            settings_grid_frame = ttk.Frame(plot_frame)
            settings_grid_frame.grid(row=7, column=0, columnspan=4, sticky=(tk.W, tk.E), padx=5, pady=5)
            
            # 第一行：线型设置 (框线, 曲线, 大小)
            ttk.Label(settings_grid_frame, text="线型设置:", width=self.SETTING_LABEL_WIDTH).grid(
                row=0, column=0, sticky=tk.W)
            
            ttk.Label(settings_grid_frame, text="框线:", width=self.Y_LABEL_WIDTH).grid(
                row=0, column=1, sticky=tk.W, padx=self.LABEL_PADX)
            self.frame_width = tk.StringVar(value="1.5")
            frame_width_combo = ttk.Combobox(settings_grid_frame, textvariable=self.frame_width,
                                           values=["0.5", "1.0", "1.5", "2.0", "2.5", "3.0"],
                                           state='readonly', width=self.COMBO_WIDTH)
            frame_width_combo.grid(row=0, column=2, padx=self.WIDGET_PADX, sticky=tk.W)
            
            ttk.Label(settings_grid_frame, text="曲线:", width=self.Y_LABEL_WIDTH).grid(
                row=0, column=3, sticky=tk.W, padx=self.LABEL_PADX)
            self.line_width = tk.StringVar(value="1.5")
            line_width_combo = ttk.Combobox(settings_grid_frame, textvariable=self.line_width,
                                           values=["0.5", "1.0", "1.5", "2.0", "2.5", "3.0"],
                                           state='readonly', width=self.COMBO_WIDTH)
            line_width_combo.grid(row=0, column=4, padx=self.WIDGET_PADX, sticky=tk.W)
            
            ttk.Label(settings_grid_frame, text="大小:", width=self.Y_LABEL_WIDTH).grid(
                row=0, column=5, sticky=tk.W, padx=self.LABEL_PADX)
            self.font_size = tk.StringVar(value="15")
            font_sizes = [str(size) for size in range(8, 25, 1)]
            font_size_combo = ttk.Combobox(settings_grid_frame, textvariable=self.font_size,
                                         values=font_sizes,
                                         state='readonly', width=self.COMBO_WIDTH)
            font_size_combo.grid(row=0, column=6, padx=self.WIDGET_PADX, sticky=tk.W)
            
            # 第二行：线型类型 (Y1, Y2, Y3)
            ttk.Label(settings_grid_frame, text="线型类型:", width=self.SETTING_LABEL_WIDTH).grid(
                row=1, column=0, sticky=tk.W)
            
            for i in range(3):
                ttk.Label(settings_grid_frame, text=f"Y{i+1}:", width=self.Y_LABEL_WIDTH).grid(
                    row=1, column=2*i+1, sticky=tk.W, padx=self.LABEL_PADX)
                default_style = '点划线' if i == 2 else list(self.line_styles_dict.keys())[i]
                style_var = tk.StringVar(value=default_style)
                style_combo = ttk.Combobox(settings_grid_frame, textvariable=style_var,
                                         values=list(self.line_styles_dict.keys()),
                                         state='readonly', width=self.COMBO_WIDTH)
                style_combo.grid(row=1, column=2*i+2, padx=self.WIDGET_PADX, sticky=tk.W)
                self.line_styles.append(style_var)
                style_var.trace_add('write', lambda *args: self.update_plot())
                
            # 第三行：绘图配色 (Y1, Y2, Y3)
            ttk.Label(settings_grid_frame, text="绘图配色:", width=self.SETTING_LABEL_WIDTH).grid(
                row=2, column=0, sticky=tk.W)
            
            self.color_schemes_dict = {
                '默认': None,
                'Set1': plt.cm.Set1,
                'Set2': plt.cm.Set2,
                'Set3': plt.cm.Set3,
                'Paired': plt.cm.Paired,
                'Dark2': plt.cm.Dark2,
                'Accent': plt.cm.Accent,
                'Pastel1': plt.cm.Pastel1,
                'Pastel2': plt.cm.Pastel2
            }
            self.color_schemes = []
            for i in range(3):
                ttk.Label(settings_grid_frame, text=f"Y{i+1}:", width=self.Y_LABEL_WIDTH).grid(
                    row=2, column=2*i+1, sticky=tk.W, padx=self.LABEL_PADX)
                default_scheme = 'Dark2' if i == 2 else list(self.color_schemes_dict.keys())[i]
                scheme_var = tk.StringVar(value=default_scheme)
                scheme_combo = ttk.Combobox(settings_grid_frame, textvariable=scheme_var,
                                          values=list(self.color_schemes_dict.keys()),
                                          state='readonly', width=self.COMBO_WIDTH)
                scheme_combo.grid(row=2, column=2*i+2, padx=self.WIDGET_PADX, sticky=tk.W)
                self.color_schemes.append(scheme_var)
                scheme_var.trace_add('write', lambda *args: self.update_plot())

            # 性能优化设置
            perf_frame = ttk.Frame(plot_frame)
            perf_frame.grid(row=9, column=0, columnspan=4, sticky=(tk.W, tk.E), padx=5)
            ttk.Label(perf_frame, text="性能优化:", width=self.SETTING_LABEL_WIDTH).grid(
                row=0, column=0, sticky=tk.W)
            
            self.auto_downsample_cb = ttk.Checkbutton(perf_frame, text="数据降频", 
                                                      variable=self.auto_downsample,
                                                      command=self.update_plot,
                                                      style='Custom.TCheckbutton')
            self.auto_downsample_cb.grid(row=0, column=1, padx=self.LABEL_PADX, sticky=tk.W)
            
            ttk.Label(perf_frame, text="数据上限:", width=8).grid(
                row=0, column=2, sticky=tk.W, padx=self.LABEL_PADX)
            self.max_plot_points_combo = ttk.Combobox(perf_frame, textvariable=self.max_plot_points,
                                                      values=["5e4", "10e4", "20e4", "50e4", "Unlimited"],
                                                      state='readonly', width=10)
            self.max_plot_points_combo.grid(row=0, column=3, padx=self.WIDGET_PADX, sticky=tk.W)
            self.max_plot_points_combo.bind('<<ComboboxSelected>>', lambda e: self.update_plot())

            # 状态信息显示区域
            status_frame = ttk.Frame(control_frame)
            status_frame.grid(row=10, column=0, columnspan=3, sticky=(tk.W, tk.E, tk.S), pady=5)
            control_frame.grid_rowconfigure(10, weight=1)
            
            self.status_text = tk.Text(status_frame, height=6, width=40, font=default_font)
            self.status_text.grid(row=0, column=0, sticky=(tk.W, tk.E))
            
            scrollbar = ttk.Scrollbar(status_frame, orient="vertical", command=self.status_text.yview)
            scrollbar.grid(row=0, column=1, sticky=(tk.N, tk.S))
            self.status_text.configure(yscrollcommand=scrollbar.set)
            status_frame.grid_columnconfigure(0, weight=1)

            # 图表显示框架
            plot_display_frame = ttk.Frame(self.main_frame)
            plot_display_frame.grid(row=0, column=1, sticky=(tk.W, tk.E, tk.N, tk.S), padx=5)
            plot_display_frame.grid_rowconfigure(0, weight=1)
            plot_display_frame.grid_columnconfigure(0, weight=1)
            
            canvas_frame = ttk.Frame(plot_display_frame)
            canvas_frame.grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))
            canvas_frame.grid_rowconfigure(0, weight=1)
            canvas_frame.grid_columnconfigure(0, weight=1)

            self.fig = plt.figure(figsize=(self.fig_w, self.fig_h))
            self.ax = self.fig.add_subplot(111)
            self.canvas = FigureCanvasTkAgg(self.fig, master=canvas_frame)
            self.canvas.draw()
            self.canvas.get_tk_widget().grid(row=0, column=0, sticky=(tk.W, tk.E, tk.N, tk.S))

            # 创建工具栏
            toolbar_frame = ttk.Frame(canvas_frame)
            toolbar_frame.grid(row=1, column=0, sticky=(tk.W, tk.E))
            self.toolbar = NavigationToolbar2Tk(self.canvas, toolbar_frame)
            self.toolbar.update()
            
            self.axes = {'y1': self.ax}
            self.current_axes = {'y1': self.ax}
            
            self.root.bind('<Configure>', self.on_window_resize)
            self.x_title.bind('<Return>', lambda e: self.update_plot())
            self.x_min_entry.bind('<Return>', lambda e: self.update_plot())
            self.x_max_entry.bind('<Return>', lambda e: self.update_plot())
            
            min_width = min(1200, int(window_width * 0.9))
            min_height = min(800, int(window_height * 0.85))
            self.root.minsize(min_width, min_height)

            self.font_family.trace_add('write', lambda *args: self.update_font_and_plot())
            self.font_size.trace_add('write', lambda *args: self.update_font_and_plot())
            self.legend_y.trace_add('write', lambda *args: self.update_legend_only())
            self.legend_font_size.trace_add('write', lambda *args: self.update_legend_only())
            self.legend_cols.trace_add('write', lambda *args: self.update_legend_only())
            self.compare_x_var.trace_add('write', self.sync_compare_x)
            self.x_axis.trace_add('write', self.sync_regular_x)
            self.current_compare_type.trace_add('write', self.on_compare_type_changed)
            self.x_combo.bind('<<ComboboxSelected>>', lambda e: self.delayed_update())

            self.load_settings()
            self._last_file_type = self.file_type.get()

            # 动态测量并固定左侧面板的最小宽度
            try:
                self.root.update_idletasks()
                self.battery_filter_frame.grid(row=5, column=0, columnspan=3, pady=5, sticky=(tk.W, tk.E))
                self.cycle_compare_frame.grid(row=6, column=0, columnspan=3, pady=5, sticky=(tk.W, tk.E))
                self.plot_frame.grid(row=7, column=0, columnspan=3, pady=5, sticky=(tk.W, tk.E))
                self.root.update_idletasks()
                
                max_req_width = self.plot_frame.master.winfo_reqwidth()
                self.main_frame.grid_columnconfigure(0, weight=0, minsize=max_req_width + 10)
            except Exception as e:
                self.logger.error(f"动态计算左侧控制栏宽度失败: {str(e)}")
                self.main_frame.grid_columnconfigure(0, weight=0, minsize=623)
            finally:
                self.update_file_type()
            
            self.check_queue()
            self.logger.info("应用程序启动成功")
        except Exception as e:
            self.logger.error(f"初始化失败: {str(e)}")
            raise

    def check_queue(self):
        """检查消息队列以在主线程中安全地更新 UI"""
        try:
            while True:
                msg = self.msg_queue.get_nowait()
                msg_type = msg.get('type')
                if msg_type == 'status':
                    message = msg.get('message')
                    clear = msg.get('clear', False)
                    if clear:
                        self.status_text.delete(1.0, tk.END)
                    self.status_text.insert(tk.END, message + "\n")
                    self.status_text.see(tk.END)
                elif msg_type == 'done':
                    df = msg.get('df')
                    self.result_df = df
                    
                    cols_list = list(df.columns)
                    self.cycle_col_combo['values'] = cols_list
                    self.step_col_combo['values'] = cols_list
                    self.time_col_combo['values'] = cols_list
                    self.voltage_col_combo['values'] = cols_list
                    self.current_col_combo['values'] = cols_list
                    
                    self.cycle_col.set(msg.get('cycle_default', ''))
                    self.step_col.set(msg.get('step_default', ''))
                    self.time_col.set(msg.get('time_default', ''))
                    self.voltage_col.set(msg.get('voltage_default', ''))
                    self.current_col.set(msg.get('current_default', ''))
                    
                    self.cycle_filter_combo['values'] = msg.get('cycles', ['全部'])
                    self.cycle_filter_combo.set('全部')
                    
                    self.step_filter_combo['values'] = msg.get('steps', ['全部'])
                    self.step_filter_combo.set('全部')
                    
                    self.update_listboxes()
                    self.update_status(msg.get('message', "文件读取和处理完成"))
                    self.set_buttons_state(True)
                elif msg_type == 'raw_done':
                    df = msg.get('df')
                    self.result_df = df
                    self.update_listboxes()
                    self.update_status(msg.get('message', "数据处理完成"))
                    self.set_buttons_state(True)
                elif msg_type == 'time_diff_done':
                    self.update_listboxes()
                    self.update_status(msg.get('message'))
                    self.set_buttons_state(True)
                    self.delayed_update()
                elif msg_type == 'error':
                    error_msg = msg.get('message')
                    self.update_status(error_msg, clear=True)
                    self.set_buttons_state(True)
                self.msg_queue.task_done()
        except queue.Empty:
            pass
        self.root.after(100, self.check_queue)

    def set_buttons_state(self, enabled=True):
        """控制交互按钮状态与光标形状，避免重复并发操作"""
        state = 'normal' if enabled else 'disabled'
        self.process_btn.configure(state=state)
        self.browse_btn.configure(state=state)
        self.battery_calc_btn.configure(state=state)
        self.clear_btn.configure(state=state)
        self.select_all_btn.configure(state=state)
        self.plot_btn.configure(state=state)
        self.save_btn.configure(state=state)
        self.csv2xlsx_btn.configure(state=state)
        if hasattr(self, 'compare_apply_btn'):
            self.compare_apply_btn.configure(state=state)
        
        cursor = "" if enabled else "watch"
        self.root.configure(cursor=cursor)

    def update_file_type(self):
        current_type = self.file_type.get()
        if not hasattr(self, '_last_file_type') or current_type != self._last_file_type:
            self._last_file_type = current_type
            if current_type == "raw":
                self.skip_rows_var.set("3")
                self.start_skip_var.set("8")
                self.start_row.set("1")
            else:
                self.skip_rows_var.set("0")
                self.start_skip_var.set("0")
                self.start_row.set("1")

            if current_type == "battery":
                self.y_settings[0]['min'].set('20')
                self.y_settings[0]['max'].set('60')
                self.y_settings[0]['title'].set('Temperature/℃')
                
                self.y_settings[1]['min'].set('0')
                self.y_settings[1]['max'].set('600')
                self.y_settings[1]['title'].set('Current/A')
                
                self.y_settings[2]['min'].set('2')
                self.y_settings[2]['max'].set('4')
                self.y_settings[2]['title'].set('Voltage/V')
            else:
                self.y_settings[0]['min'].set('20')
                self.y_settings[0]['max'].set('60')
                self.y_settings[0]['title'].set('Temperature/℃')
                
                self.y_settings[1]['min'].set('20')
                self.y_settings[1]['max'].set('60')
                self.y_settings[1]['title'].set('Temperature/℃')
                
                self.y_settings[2]['min'].set('0')
                self.y_settings[2]['max'].set('150')
                self.y_settings[2]['title'].set('HeatingPower/W')

        # Always grid in the same order: 起始行 (leftmost), SKIP, NULL
        self.start_row_label.grid(row=0, column=0, sticky=tk.W, padx=(2, 2))
        self.start_row_entry.grid(row=0, column=1, sticky=tk.W, padx=(2, 10))
        self.skip_label.grid(row=0, column=2, sticky=tk.W, padx=(2, 2))
        self.skip_entry.grid(row=0, column=3, sticky=tk.W, padx=(2, 10))
        self.null_label.grid(row=0, column=4, sticky=tk.W, padx=(2, 2))
        self.null_entry.grid(row=0, column=5, sticky=tk.W, padx=(2, 10))

        if self.file_type.get() == "battery":
            self.plot_canvas.configure(height=360)
            self.battery_filter_frame.grid(row=5, column=0, columnspan=3, pady=5, sticky=(tk.W, tk.E))
            if self.cycle_compare_var.get():
                self.cycle_compare_frame.grid(row=6, column=0, columnspan=3, pady=5, sticky=(tk.W, tk.E))
                self.plot_frame.grid(row=8, column=0, columnspan=3, pady=5, sticky=(tk.W, tk.E)) # 下移至 row 8
            else:
                self.cycle_compare_frame.grid_forget()
                self.plot_frame.grid(row=7, column=0, columnspan=3, pady=5, sticky=(tk.W, tk.E)) # 下移至 row 7
        else:
            self.plot_canvas.configure(height=520)
            self.battery_filter_frame.grid_forget()
            self.cycle_compare_frame.grid_forget()
            self.plot_frame.grid(row=7, column=0, columnspan=3, pady=5, sticky=(tk.W, tk.E)) # 下移至 row 7

        if self.file_type.get() == "raw":
            self.sheet_combo.configure(state='readonly')
        else:
            if self.file_path.get().endswith('.xlsx'):
                self.sheet_combo.configure(state='readonly')
            else:
                self.sheet_combo.configure(state='disabled')

        # Self-learning minimum panel width to prevent jitter when switching panels
        try:
            self.root.update_idletasks()
            req_width = self.control_frame.winfo_reqwidth()
            current_min = self.main_frame.grid_columnconfigure(0)['minsize']
            if isinstance(current_min, str):
                current_min = int(current_min)
            new_min = max(current_min, req_width + 10)
            self.main_frame.grid_columnconfigure(0, minsize=new_min)
        except Exception:
            pass

    def on_selection_change(self, event):
        """当任何Listbox的选择改变时更新状态"""
        widget = event.widget
        for i, listbox in enumerate(self.y_listboxes):
            if listbox == widget:
                selected_items = [listbox.get(idx) for idx in listbox.curselection()]
                self.y_selections[i] = selected_items
                break
        self.delayed_update()

    def on_compare_type_changed(self, *args):
        ctype = self.current_compare_type.get()
        if ctype == 'dqdv':
            self.dqdv_title_var.set("dQ/dV(Ah/V)")
        elif ctype == 'dvdq':
            self.dqdv_title_var.set("dV/dQ(V/Ah)")
        else:
            self.dqdv_title_var.set("")

    def on_cycle_col_changed(self, event=None):
        if self.result_df is not None:
            cycle_col_name = self.cycle_col.get()
            if cycle_col_name in self.result_df.columns:
                try:
                    unique_vals = self.result_df[cycle_col_name].dropna().unique()
                    def safe_sort_key(val):
                        try:
                            return (0, float(val))
                        except (ValueError, TypeError):
                            return (1, str(val))
                    unique_vals = sorted(unique_vals, key=safe_sort_key)
                    cycles = ['全部']
                    for x in unique_vals:
                        try:
                            if float(x).is_integer():
                                cycles.append(str(int(x)))
                            else:
                                cycles.append(str(x))
                        except ValueError:
                            cycles.append(str(x))
                except Exception:
                    cycles = ['全部'] + [str(x) for x in self.result_df[cycle_col_name].dropna().unique()]
                self.cycle_filter_combo['values'] = cycles
                self.cycle_filter_combo.set('全部')
                self.delayed_update()

    def on_step_col_changed(self, event=None):
        if self.result_df is not None:
            step_col_name = self.step_col.get()
            if step_col_name in self.result_df.columns:
                steps = ['全部'] + [str(x) for x in self.result_df[step_col_name].dropna().unique()]
                self.step_filter_combo['values'] = steps
                self.step_filter_combo.set('全部')
                self.delayed_update()

    def on_time_col_changed(self, event=None):
        if self.result_df is not None:
            time_col_name = self.time_col.get()
            if time_col_name in self.result_df.columns:
                new_col_name = f"{time_col_name}_时间差(s)"
                if new_col_name not in self.result_df.columns:
                    self.set_buttons_state(False)
                    self.update_status(f"正在后台计算 {time_col_name} 的时间差(s)...")
                    threading.Thread(target=self._bg_calc_time_diff, args=(time_col_name, new_col_name), daemon=True).start()
                    return
            self.update_listboxes()
            self.delayed_update()

    def clear_all_selections(self):
        """清除所有选择"""
        for i, listbox in enumerate(self.y_listboxes):
            listbox.selection_clear(0, tk.END)
            self.y_selections[i] = []
        if hasattr(self, 'fig'):
            self.fig.clf()
            self.ax = self.fig.add_subplot(111)
            self.canvas.draw()

    def select_all_y(self):
        """全部选择Y1"""
        if not self.x_axis.get():
            return
        
        self.clear_all_selections()
        x_col = self.x_axis.get()
        
        listbox = self.y_listboxes[0]
        for i in range(listbox.size()):
            if listbox.get(i) != x_col:
                listbox.selection_set(i)
        self.y_selections[0] = [listbox.get(i) for i in listbox.curselection()]
        self.update_plot()

    def safe_float_convert(self, value, default=0.0):
        """安全地转换浮点数"""
        try:
            return float(value)
        except (ValueError, TypeError):
            return default

    def validate_data(self):
        """验证数据有效性"""
        if not hasattr(self, 'result_df') or self.result_df is None:
            return False
        if not any(self.y_selections):
            return False
        return True

    def clear_memory(self):
        """清理内存"""
        try:
            if hasattr(self, 'result_df'):
                del self.result_df
            if hasattr(self, 'fig'):
                plt.close('all')
            gc.collect()
        except Exception as e:
            print(f"清理内存时出错: {str(e)}")

    def __del__(self):
        self.clear_memory()

    def update_status(self, message, clear=False):
        """更新状态信息"""
        if clear:
            self.status_text.delete(1.0, tk.END)
        self.status_text.insert(tk.END, message + "\n")
        self.status_text.see(tk.END)
        self.root.update()

    def update_listboxes(self):
        """更新所有列表框的内容"""
        try:
            self.result_df = self.result_df.dropna(axis=1, how='all')
            cols_to_drop = []
            for col in self.result_df.columns:
                if self.result_df[col].dtype == 'object':
                    non_nulls = self.result_df[col].dropna()
                    if len(non_nulls) > 0:
                        sample = non_nulls.head(1000).astype(str).str.strip()
                        if (sample != '').any():
                            continue
                        if non_nulls.astype(str).str.strip().eq('').all():
                            cols_to_drop.append(col)
            if cols_to_drop:
                self.result_df = self.result_df.drop(columns=cols_to_drop)
            
            self.result_df['Index'] = self.result_df.index
            columns = ['Index'] + [col for col in self.result_df.columns if col != 'Index']
            
            is_compare = (self.file_type.get() == "battery" and self.cycle_compare_var.get())
            if is_compare:
                if '容量（计算）' not in columns:
                    columns.append('容量（计算）')
                if '循环时间（计算）' not in columns:
                    columns.append('循环时间（计算）')
                    
            if self.file_type.get() == "battery":
                step_val = self.step_filter.get()
                if step_val != "全部" and step_val != "":
                    if '工步时间（计算）' not in columns:
                        columns.append('工步时间（计算）')
            
            self.x_combo['values'] = columns
            if is_compare:
                self.compare_x_combo['values'] = columns
                if self.compare_x_var.get() not in columns:
                    self.compare_x_combo.set('容量（计算）')
            
            if self.file_type.get() == "battery" and hasattr(self, 'time_col') and self.time_col.get():
                if is_compare:
                    self.x_combo.set('容量（计算）')
                else:
                    time_col_name = self.time_col.get()
                    time_diff_col = f"{time_col_name}_时间差(s)"
                    if time_diff_col in self.result_df.columns:
                        self.x_combo.set(time_diff_col)
                    elif time_col_name in self.result_df.columns:
                        self.x_combo.set(time_col_name)
                    else:
                        self.x_combo.set(columns[0])
            else:
                self.x_combo.set(columns[0])
            
            for i, listbox in enumerate(self.y_listboxes):
                listbox.delete(0, tk.END)
                for col in self.result_df.columns:
                    if col != 'Index':
                        listbox.insert(tk.END, col)
                    
        except Exception as e:
            self.update_status(f"更新列表失败: {str(e)}")

    def on_closing(self):
        """窗口关闭时的清理工作"""
        try:
            self.clear_memory()
            self.save_settings()
            logging.shutdown()
            self.root.destroy()
            os._exit(0)
        except Exception as e:
            print(f"关闭程序时出错: {str(e)}")
            os._exit(1)
