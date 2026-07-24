import os
import sys
import queue
import logging
import gc
import threading
from PySide6.QtWidgets import (
    QMainWindow, QWidget, QFrame, QVBoxLayout, QHBoxLayout, QGridLayout,
    QFormLayout, QLabel, QLineEdit, QPushButton, QComboBox, QRadioButton,
    QCheckBox, QListWidget, QListWidgetItem, QScrollArea, QTextEdit, QMessageBox, QFileDialog,
    QButtonGroup, QSplitter, QGroupBox, QSizePolicy, QLayout
)
from PySide6.QtCore import Qt, QTimer, Slot, QEvent, QObject
from PySide6.QtGui import QIcon, QFont

class CustomLineEdit(QLineEdit):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
    def get(self):
        return self.text()

# Redirect QLineEdit to CustomLineEdit to support Tkinter get() method
QLineEdit = CustomLineEdit

import matplotlib
matplotlib.use('QtAgg')
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas, NavigationToolbar2QT as NavigationToolbar

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

class Var:
    def __init__(self, value=None):
        self._value = value
        self._callbacks = []

    def get(self):
        return self._value

    def set(self, val):
        self._value = val
        for cb in list(self._callbacks):
            try:
                cb()
            except Exception:
                pass

    def trace_add(self, mode, callback):
        self._callbacks.append(callback)
        return callback

class CustomComboBox(QComboBox):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.currentIndexChanged.connect(self._update_combo_tooltip)
    
    def _update_combo_tooltip(self):
        self.setToolTip(self.currentText())
        
    def addItem(self, text, userData=None):
        super().addItem(text, userData)
        self.setItemData(self.count() - 1, text, Qt.ToolTipRole)
        if self.count() == 1:
            self._update_combo_tooltip()

    def addItems(self, texts):
        for text in texts:
            self.addItem(text)

    def __setitem__(self, key, value):
        if key == 'values':
            self.clear()
            self.addItems([str(v) for v in value])

    def set(self, value):
        val_str = str(value)
        idx = self.findText(val_str)
        if idx >= 0:
            self.setCurrentIndex(idx)
        else:
            self.setCurrentText(val_str)
        self._update_combo_tooltip()

class CustomListWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self._callbacks = []
        self.itemSelectionChanged.connect(self._on_selection_changed)

    def bind(self, event, callback):
        if event == '<<ListboxSelect>>':
            self._callbacks.append(callback)

    def _on_selection_changed(self):
        class MockEvent:
            def __init__(self, widget):
                self.widget = widget
        event = MockEvent(self)
        for cb in self._callbacks:
            try:
                cb(event)
            except Exception:
                pass

    def selection_clear(self, start=0, end=None):
        self.clearSelection()

    def selection_set(self, index):
        item = self.item(index)
        if item:
            item.setSelected(True)

    def curselection(self):
        indices = []
        for i in range(self.count()):
            if self.item(i).isSelected():
                indices.append(i)
        return tuple(indices)

    def get(self, index):
        item = self.item(index)
        return item.text() if item else ""

    def size(self):
        return self.count()

    def delete(self, start=0, end=None):
        self.clear()

    def insert(self, index, item_text):
        item = QListWidgetItem(item_text)
        item.setToolTip(item_text)
        self.addItem(item)

def bind_lineedit(widget, var):
    widget.setText(str(var.get() if var.get() is not None else ""))
    widget.textChanged.connect(var.set)
    var.trace_add('write', lambda: widget.setText(str(var.get())) if widget.text() != str(var.get()) else None)

def bind_checkbox(widget, var):
    widget.setChecked(bool(var.get()))
    widget.toggled.connect(var.set)
    var.trace_add('write', lambda: widget.setChecked(bool(var.get())) if widget.isChecked() != bool(var.get()) else None)

def bind_combobox(widget, var):
    def on_combobox_changed(text):
        var.set(text)
    widget.currentTextChanged.connect(on_combobox_changed)
    def on_var_changed():
        val = str(var.get() if var.get() is not None else "")
        idx = widget.findText(val)
        if idx >= 0 and widget.currentIndex() != idx:
            widget.setCurrentIndex(idx)
    var.trace_add('write', on_var_changed)
    on_var_changed()

class CanvasResizeFilter(QObject):
    def __init__(self, gui):
        super().__init__()
        self.gui = gui

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Resize:
            if hasattr(self.gui, 'plot_display_widget') and obj == self.gui.plot_display_widget:
                if getattr(self.gui, '_is_plotting', False) or getattr(self.gui, '_is_syncing_splitter', False) or getattr(self.gui, '_is_loading_settings', False):
                    return super().eventFilter(obj, event)
                w = event.size().width()
                saved = getattr(self.gui, '_saved_canvas_width', 0)
                if w > 50 and abs(w - saved) > 2:
                    self.gui._saved_canvas_width = w
                    if hasattr(self.gui, 'canvas_width_var'):
                        self.gui.canvas_width_var.set(str(w))
                    self.gui._is_syncing_splitter = True
                    try:
                        if hasattr(self.gui, 'canvas_width_entry') and self.gui.canvas_width_entry:
                            if self.gui.canvas_width_entry.text() != str(w):
                                self.gui.canvas_width_entry.setText(str(w))
                    finally:
                        self.gui._is_syncing_splitter = False
        return super().eventFilter(obj, event)

class PlotterGUI(QMainWindow, DataLoaderMixin, BatteryMathMixin, PlotEngineMixin, ExcelExporterMixin, SettingsMixin):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DataPlot v1.0.4")
        
        icon_path = resource_path('icon.ico')
        if os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
            
        self.result_df = None
        self.CHUNK_SIZE = 100000
        self.msg_queue = queue.Queue()
        
        # 设置日志（指定 UTF-8 编码防止中文乱码）
        logging.basicConfig(
            filename='DataPlot.log',
            level=logging.INFO,
            format='%(asctime)s - %(levelname)s - %(message)s',
            encoding='utf-8'
        )
        self.logger = logging.getLogger('DataPlot')
        
        # 定义线型选项
        self.line_styles_dict = {
            '实线': '-',
            '虚线': '--',
            '点线': ':',
            '点划线': '-.',
        }
        
        self.line_styles = []
        self.color_schemes = []
        
        self.version = "1.0.4"
        self._update_timer = None
        self._last_plot_time = 0
        self._is_loading_settings = False
        self._is_syncing_x = False
        
        # Initialize Variables using Var wrapper
        self.auto_downsample = Var(True)
        self.max_plot_points = Var("None")
        self.legend_font_size = Var("18")
        self.legend_cols = Var("1")
        self.x_min_var = Var("")
        self.x_max_var = Var("")
        
        self.file_type = Var("raw")
        self.file_path = Var("")
        self.sheet_name = Var("")
        self.skip_rows_var = Var("3")
        self.start_skip_var = Var("8")
        self.start_row = Var("1")
        
        self.cycle_col = Var("")
        self.step_col = Var("")
        self.time_col = Var("")
        self.voltage_col = Var("")
        self.current_col = Var("")
        self.cycle_compare_var = Var(False)
        self.cycle_compare_range_var = Var("1, max, 10")
        self.time_step_var = Var("10")
        self.filter_type_var = Var("无")
        self.filter_window_var = Var("15")
        self.sg_poly_var = Var("2")
        self.compare_x_var = Var("循环时间（计算）")
        self.current_compare_type = Var("regular")
        self.cc_polarity_var = Var("正")
        self.dqdv_min_var = Var("")
        self.dqdv_max_var = Var("")
        self.dqdv_title_var = Var("")
        self.voltage_scale_var = Var("1")
        self.current_scale_var = Var("1")
        self.x_axis = Var("")
        
        self.cycle_filter = Var("全部")
        self.step_filter = Var("全部")
        
        self.font_family = Var("Microsoft YaHei")
        self.legend_y = Var("1.02")
        self.legend_x_positions_str = Var("0, 0.3, 0.7")
        self.legend_visible = Var(True)
        self.font_size = Var("18")
        self.frame_width = Var("1.5")
        self.line_width = Var("1.5")
        
        # Settings Panel Font & Layout
        self.panel_font_family = Var("Microsoft YaHei")
        self.panel_font_size = Var("13")
        self.panel_width_var = Var("560")
        self.canvas_width_var = Var("1000")
        self.canvas_bg_var = Var("默认(白色)")
        
        # Advanced margin variables
        self.adv_left_margin_mult = Var("4.5")
        self.adv_left_margin_min_px = Var("80")
        self.adv_left_margin_min_pct = Var("0.08")
        
        self.adv_y3_margin_mult = Var("9.5")
        self.adv_y3_margin_min_px = Var("170")
        self.adv_y3_max_right_pct = Var("0.83")
        
        self.adv_y2_margin_mult = Var("4.0")
        self.adv_y2_margin_min_px = Var("75")
        self.adv_y2_max_right_pct = Var("0.93")
        
        self.adv_y1_margin_mult = Var("1.5")
        self.adv_y1_margin_min_px = Var("20")
        self.adv_y1_max_right_pct = Var("0.97")
        
        self.y_settings = []
        default_y_configs = [
            {'min': '20', 'max': '60', 'title': 'Temperature/℃'},
            {'min': '20', 'max': '60', 'title': 'Temperature/℃'},
            {'min': '0', 'max': '150', 'title': 'HeatingPower/W'}
        ]
        for i in range(3):
            config = default_y_configs[i]
            settings = {
                'min': Var(config['min']),
                'max': Var(config['max']),
                'title': Var(config['title'])
            }
            self.y_settings.append(settings)

        self.init_ui()
        self.load_settings()
        QTimer.singleShot(0, self.apply_loaded_panel_and_canvas_width)
        self._last_file_type = self.file_type.get()
        
        self.update_file_type()
        self.update_font_and_plot()
        
        # Connect Variable Traces
        self.font_family.trace_add('write', lambda *args: self.update_font_and_plot())
        self.font_size.trace_add('write', lambda *args: self.update_font_and_plot())
        self.panel_font_family.trace_add('write', lambda *args: self.update_panel_font())
        self.panel_font_size.trace_add('write', lambda *args: self.update_panel_font())
        self.legend_y.trace_add('write', lambda *args: self.update_legend_only())
        self.legend_font_size.trace_add('write', lambda *args: self.update_legend_only())
        self.legend_cols.trace_add('write', lambda *args: self.update_legend_only())
        self.compare_x_var.trace_add('write', self.sync_compare_x)
        self.x_axis.trace_add('write', self.sync_regular_x)
        self.current_compare_type.trace_add('write', self.on_compare_type_changed)
        
        QTimer.singleShot(100, self.check_queue)
        self.logger.info("应用程序启动成功")

    def init_ui(self):
        from PySide6.QtWidgets import QApplication
        screen = QApplication.primaryScreen()
        if screen:
            screen_geometry = screen.availableGeometry()
            width = int(screen_geometry.width() * 0.85)
            height = int(screen_geometry.height() * 0.85)
            width = max(1600, min(width, screen_geometry.width()))
            height = max(900, min(height, screen_geometry.height()))
            self.resize(width, height)
        else:
            self.resize(1600, 900)
        self.setMinimumSize(1200, 800)
        
        self.setStyleSheet("""
            QWidget {
                font-family: "Microsoft YaHei", "Segoe UI", Arial;
                font-size: 13px;
                color: #2c3e50;
            }
            QMainWindow {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #cbe5ff, stop:1 #f0f7ff);
            }
            QScrollArea {
                background: transparent;
            }
            QScrollArea > QWidget > QWidget {
                background: transparent;
            }
            QSplitter {
                background: transparent;
            }
            QWidget#controlWidget {
                background: transparent;
            }
            QGroupBox {
                font-weight: bold;
                font-size: 14px;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                margin-top: 12px;
                padding-top: 14px;
                background-color: #ffffff;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 8px;
                color: #1e293b;
            }
            QGroupBox::indicator {
                width: 14px;
                height: 14px;
            }
            QPushButton {
                background-color: #3b82f6;
                color: white;
                border-radius: 6px;
                border: none;
                padding: 6px 14px;
                font-weight: bold;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
            QPushButton:disabled {
                background-color: #cbd5e1;
                color: #94a3b8;
            }
            QLineEdit, QComboBox {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                padding: 5px 8px;
                background-color: #ffffff;
                font-size: 13px;
            }
            QLineEdit:focus, QComboBox:focus {
                border: 1px solid #3b82f6;
                outline: none;
            }
            QListWidget {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                background-color: #ffffff;
                padding: 4px;
            }
            QListWidget::item {
                padding: 1px 6px;
                border-radius: 4px;
            }
            QListWidget::item:selected {
                background-color: #eff6ff;
                color: #2563eb;
            }
            QTextEdit {
                border: 1px solid #cbd5e1;
                border-radius: 6px;
                background-color: #ffffff;
                color: #2c3e50;
                font-family: 'Consolas', 'Fira Code', Monaco, monospace;
                font-size: 14px;
                padding: 8px;
            }
            QScrollBar:vertical {
                border: none;
                background: #f1f5f9;
                width: 8px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical {
                background: #cbd5e1;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: #94a3b8;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
        """)

        central_widget = QWidget(self)
        self.setCentralWidget(central_widget)
        
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        
        splitter = QSplitter(Qt.Horizontal, central_widget)
        self.splitter = splitter
        self.splitter.setCollapsible(0, False)
        self.splitter.setCollapsible(1, False)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.splitterMoved.connect(self.on_splitter_moved)
        main_layout.addWidget(splitter)
        
        # Left Panel (Controls) Scroll Area
        left_scroll = QScrollArea(splitter)
        left_scroll.setWidgetResizable(True)
        left_scroll.setFrameShape(QFrame.NoFrame)
        left_scroll.setMinimumWidth(500)
        splitter.addWidget(left_scroll)
        
        control_widget = QWidget()
        control_widget.setObjectName("controlWidget")
        control_widget.setMinimumWidth(480)
        self.control_widget = control_widget
        left_scroll.setWidget(control_widget)
        
        control_layout = QVBoxLayout(control_widget)
        control_layout.setContentsMargins(5, 5, 5, 5)
        
        # 1. File Type Selector
        file_type_groupbox = QGroupBox("文件类型")
        file_type_layout = QHBoxLayout(file_type_groupbox)
        
        self.file_type_group = QButtonGroup(self)
        self.radio_raw = QRadioButton("FLOEFD")
        self.radio_processed = QRadioButton("GENERAL")
        self.radio_battery = QRadioButton("BATTERY")
        
        file_type_layout.addWidget(self.radio_raw)
        file_type_layout.addWidget(self.radio_processed)
        file_type_layout.addWidget(self.radio_battery)
        
        self.file_type_group.addButton(self.radio_raw, 0)
        self.file_type_group.addButton(self.radio_processed, 1)
        self.file_type_group.addButton(self.radio_battery, 2)
        
        # Radio Sync
        def on_radio_toggled(btn, checked):
            if checked:
                val = "raw" if btn == self.radio_raw else "processed" if btn == self.radio_processed else "battery"
                self.file_type.set(val)
                self.update_file_type()
        
        self.radio_raw.toggled.connect(lambda c: on_radio_toggled(self.radio_raw, c))
        self.radio_processed.toggled.connect(lambda c: on_radio_toggled(self.radio_processed, c))
        self.radio_battery.toggled.connect(lambda c: on_radio_toggled(self.radio_battery, c))
        
        # Set default selection
        if self.file_type.get() == "raw":
            self.radio_raw.setChecked(True)
        elif self.file_type.get() == "processed":
            self.radio_processed.setChecked(True)
        else:
            self.radio_battery.setChecked(True)
            
        control_layout.addWidget(file_type_groupbox)
        
        # 2. Input Configuration Grid
        input_groupbox = QGroupBox("输入配置")
        input_groupbox.setCheckable(True)
        input_groupbox.setChecked(True)
        
        input_content = QWidget()
        input_grid = QGridLayout(input_content)
        input_grid.setContentsMargins(0, 5, 0, 0)
        
        input_grid.addWidget(QLabel("文件路径:"), 0, 0)
        self.file_entry = QLineEdit()
        bind_lineedit(self.file_entry, self.file_path)
        input_grid.addWidget(self.file_entry, 0, 1)
        
        self.browse_btn = QPushButton("浏览")
        self.browse_btn.clicked.connect(self.browse_file)
        input_grid.addWidget(self.browse_btn, 0, 2)
        
        input_grid.addWidget(QLabel("表格名称:"), 1, 0)
        self.sheet_combo = CustomComboBox()
        bind_combobox(self.sheet_combo, self.sheet_name)
        input_grid.addWidget(self.sheet_combo, 1, 1)
        
        self.process_btn = QPushButton("读取")
        self.process_btn.clicked.connect(self.process_data)
        input_grid.addWidget(self.process_btn, 1, 2)
        
        # Row 2: Sub-parameters (IniRow, SKIP, NULL, 数据上限)
        sub_param_widget = QWidget()
        sub_param_layout = QHBoxLayout(sub_param_widget)
        sub_param_layout.setContentsMargins(0, 0, 0, 0)
        
        sub_param_layout.addWidget(QLabel("IniRow:"))
        self.start_row_entry = QLineEdit()
        self.start_row_entry.setFixedWidth(40)
        bind_lineedit(self.start_row_entry, self.start_row)
        sub_param_layout.addWidget(self.start_row_entry)
        
        self.skip_label = QLabel("SKIP:")
        self.skip_entry = QLineEdit()
        self.skip_entry.setFixedWidth(40)
        bind_lineedit(self.skip_entry, self.skip_rows_var)
        sub_param_layout.addWidget(self.skip_label)
        sub_param_layout.addWidget(self.skip_entry)
        
        self.null_label = QLabel("NULL:")
        self.null_entry = QLineEdit()
        self.null_entry.setFixedWidth(40)
        bind_lineedit(self.null_entry, self.start_skip_var)
        sub_param_layout.addWidget(self.null_label)
        sub_param_layout.addWidget(self.null_entry)
        
        # Add "数据上限" next to NULL
        self.max_plot_points_label = QLabel("上限:")
        self.max_plot_points_combo = CustomComboBox()
        self.max_plot_points_combo.addItems(["5e4", "10e4", "20e4", "50e4", "100e4", "None"])
        self.max_plot_points_combo.setFixedWidth(70)
        bind_combobox(self.max_plot_points_combo, self.max_plot_points)
        self.max_plot_points_combo.currentIndexChanged.connect(lambda: self.update_plot())
        sub_param_layout.addWidget(self.max_plot_points_label)
        sub_param_layout.addWidget(self.max_plot_points_combo)
        
        input_grid.addWidget(sub_param_widget, 2, 0, 1, 2)
        
        self.csv2xlsx_btn = QPushButton("csv2xlsx")
        self.csv2xlsx_btn.clicked.connect(self.convert_csv_to_xlsx)
        input_grid.addWidget(self.csv2xlsx_btn, 2, 2)
        
        input_box_lay = QVBoxLayout(input_groupbox)
        input_box_lay.setContentsMargins(10, 0, 10, 10)
        input_box_lay.addWidget(input_content)
        input_groupbox.toggled.connect(input_content.setVisible)
        
        control_layout.addWidget(input_groupbox)
        
        # 3. Battery Data Configuration GroupBox
        self.battery_filter_frame = QGroupBox("电池数据配置")
        self.battery_filter_frame.setCheckable(True)
        self.battery_filter_frame.setChecked(True)
        
        battery_content = QWidget()
        battery_grid = QGridLayout(battery_content)
        battery_grid.setContentsMargins(0, 5, 0, 0)
        
        # Row 0: Voltage Column, Current Column, Scale
        battery_grid.addWidget(QLabel("电压列:"), 0, 0)
        self.voltage_col_combo = CustomComboBox()
        bind_combobox(self.voltage_col_combo, self.voltage_col)
        self.voltage_col_combo.currentIndexChanged.connect(lambda: self.delayed_update())
        battery_grid.addWidget(self.voltage_col_combo, 0, 1)
        
        battery_grid.addWidget(QLabel("电流列:"), 0, 2)
        self.current_col_combo = CustomComboBox()
        bind_combobox(self.current_col_combo, self.current_col)
        self.current_col_combo.currentIndexChanged.connect(lambda: self.delayed_update())
        battery_grid.addWidget(self.current_col_combo, 0, 3)
        
        battery_grid.addWidget(QLabel("比例:"), 0, 4)
        scale_widget = QWidget()
        scale_layout = QHBoxLayout(scale_widget)
        scale_layout.setContentsMargins(0, 0, 0, 0)
        scale_layout.setSpacing(4)
        scale_layout.setAlignment(Qt.AlignLeft)
        
        self.voltage_scale_entry = QLineEdit()
        self.voltage_scale_entry.setFixedWidth(45)
        bind_lineedit(self.voltage_scale_entry, self.voltage_scale_var)
        self.voltage_scale_entry.textChanged.connect(lambda: self.delayed_update())
        self.voltage_scale_entry.returnPressed.connect(lambda: self.delayed_update())
        
        self.current_scale_entry = QLineEdit()
        self.current_scale_entry.setFixedWidth(45)
        bind_lineedit(self.current_scale_entry, self.current_scale_var)
        self.current_scale_entry.textChanged.connect(lambda: self.delayed_update())
        self.current_scale_entry.returnPressed.connect(lambda: self.delayed_update())
        
        scale_layout.addWidget(self.voltage_scale_entry)
        scale_layout.addWidget(QLabel("/"))
        scale_layout.addWidget(self.current_scale_entry)
        scale_layout.addStretch()
        battery_grid.addWidget(scale_widget, 0, 5)

        # Row 1: Cycle Column, Step Column, Time Column
        battery_grid.addWidget(QLabel("循环列:"), 1, 0)
        self.cycle_col_combo = CustomComboBox()
        bind_combobox(self.cycle_col_combo, self.cycle_col)
        self.cycle_col_combo.currentIndexChanged.connect(self.on_cycle_col_changed)
        battery_grid.addWidget(self.cycle_col_combo, 1, 1)
        
        battery_grid.addWidget(QLabel("工步列:"), 1, 2)
        self.step_col_combo = CustomComboBox()
        bind_combobox(self.step_col_combo, self.step_col)
        self.step_col_combo.currentIndexChanged.connect(self.on_step_col_changed)
        battery_grid.addWidget(self.step_col_combo, 1, 3)
        
        battery_grid.addWidget(QLabel("时间列:"), 1, 4)
        self.time_col_combo = CustomComboBox()
        bind_combobox(self.time_col_combo, self.time_col)
        self.time_col_combo.currentIndexChanged.connect(self.on_time_col_changed)
        battery_grid.addWidget(self.time_col_combo, 1, 5)
        
        # Row 2: Cycle Filter, Step Filter
        battery_grid.addWidget(QLabel("循环筛选:"), 2, 0)
        self.cycle_filter_combo = CustomComboBox()
        bind_combobox(self.cycle_filter_combo, self.cycle_filter)
        self.cycle_filter_combo.currentIndexChanged.connect(lambda: self.on_cycle_filter_changed())
        battery_grid.addWidget(self.cycle_filter_combo, 2, 1)
        
        battery_grid.addWidget(QLabel("工步筛选:"), 2, 2)
        self.step_filter_combo = CustomComboBox()
        bind_combobox(self.step_filter_combo, self.step_filter)
        self.step_filter_combo.currentIndexChanged.connect(lambda: self.on_step_filter_changed())
        battery_grid.addWidget(self.step_filter_combo, 2, 3)
        
        battery_box_lay = QVBoxLayout(self.battery_filter_frame)
        battery_box_lay.setContentsMargins(10, 0, 10, 10)
        battery_box_lay.addWidget(battery_content)
        self.battery_filter_frame.toggled.connect(battery_content.setVisible)
        
        control_layout.addWidget(self.battery_filter_frame)
        
        # 4. Cycle Compare Configuration GroupBox
        self.cycle_compare_frame = QGroupBox("启用循环对比")
        self.cycle_compare_frame.setCheckable(True)
        self.cycle_compare_frame.setChecked(bool(self.cycle_compare_var.get()))
        
        compare_content = QWidget()
        compare_content.setVisible(self.cycle_compare_frame.isChecked())
        compare_grid = QGridLayout(compare_content)
        compare_grid.setContentsMargins(0, 5, 0, 0)
        
        compare_grid.addWidget(QLabel("对比范围:"), 0, 0)
        self.cycle_range_entry = QLineEdit()
        bind_lineedit(self.cycle_range_entry, self.cycle_compare_range_var)
        self.cycle_range_entry.returnPressed.connect(lambda: self.delayed_update())
        compare_grid.addWidget(self.cycle_range_entry, 0, 1)
        
        compare_grid.addWidget(QLabel("步长(Time):"), 0, 2)
        self.time_step_entry = QLineEdit()
        self.time_step_entry.setFixedWidth(50)
        bind_lineedit(self.time_step_entry, self.time_step_var)
        self.time_step_entry.returnPressed.connect(lambda: self.delayed_update())
        compare_grid.addWidget(self.time_step_entry, 0, 3)
        
        compare_grid.addWidget(QLabel("CC极性:"), 0, 4)
        self.cc_polarity_combo = CustomComboBox()
        self.cc_polarity_combo.addItems(["正", "负"])
        bind_combobox(self.cc_polarity_combo, self.cc_polarity_var)
        self.cc_polarity_combo.currentIndexChanged.connect(lambda: self.delayed_update())
        compare_grid.addWidget(self.cc_polarity_combo, 0, 5)
        
        compare_grid.addWidget(QLabel("滤波方式:"), 1, 0)
        self.filter_type_combo = CustomComboBox()
        self.filter_type_combo.addItems(["无", "Savitzky-Golay", "移动平均", "中值滤波"])
        bind_combobox(self.filter_type_combo, self.filter_type_var)
        self.filter_type_combo.currentIndexChanged.connect(lambda: self.delayed_update())
        compare_grid.addWidget(self.filter_type_combo, 1, 1)
        
        compare_grid.addWidget(QLabel("窗口大小:"), 1, 2)
        self.filter_window_entry = QLineEdit()
        self.filter_window_entry.setFixedWidth(50)
        bind_lineedit(self.filter_window_entry, self.filter_window_var)
        self.filter_window_entry.returnPressed.connect(lambda: self.delayed_update())
        compare_grid.addWidget(self.filter_window_entry, 1, 3)
        
        compare_grid.addWidget(QLabel("SG阶数:"), 1, 4)
        self.sg_poly_combo = CustomComboBox()
        self.sg_poly_combo.addItems(["2", "3", "4"])
        bind_combobox(self.sg_poly_combo, self.sg_poly_var)
        self.sg_poly_combo.currentIndexChanged.connect(lambda: self.delayed_update())
        compare_grid.addWidget(self.sg_poly_combo, 1, 5)
        
        compare_grid.addWidget(QLabel("循环X轴:"), 2, 0)
        self.compare_x_combo = CustomComboBox()
        bind_combobox(self.compare_x_combo, self.compare_x_var)
        self.compare_x_combo.currentIndexChanged.connect(lambda: self.delayed_update())
        compare_grid.addWidget(self.compare_x_combo, 2, 1)
        
        compare_type_widget = QWidget()
        compare_type_layout = QHBoxLayout(compare_type_widget)
        compare_type_layout.setContentsMargins(0, 0, 0, 0)
        self.compare_type_group = QButtonGroup(self)
        self.radio_regular = QRadioButton("常规对比")
        self.radio_dqdv = QRadioButton("dQ/dV")
        self.radio_dvdq = QRadioButton("dV/dQ")
        compare_type_layout.addWidget(self.radio_regular)
        compare_type_layout.addWidget(self.radio_dqdv)
        compare_type_layout.addWidget(self.radio_dvdq)
        self.compare_type_group.addButton(self.radio_regular, 0)
        self.compare_type_group.addButton(self.radio_dqdv, 1)
        self.compare_type_group.addButton(self.radio_dvdq, 2)
        
        def on_compare_type_toggled(btn, checked):
            if checked:
                val = "regular" if btn == self.radio_regular else "dqdv" if btn == self.radio_dqdv else "dvdq"
                self.current_compare_type.set(val)
                self.on_compare_type_changed()
                self.delayed_update()
        
        self.radio_regular.toggled.connect(lambda c: on_compare_type_toggled(self.radio_regular, c))
        self.radio_dqdv.toggled.connect(lambda c: on_compare_type_toggled(self.radio_dqdv, c))
        self.radio_dvdq.toggled.connect(lambda c: on_compare_type_toggled(self.radio_dvdq, c))
        self.radio_regular.setChecked(True)
        
        compare_grid.addWidget(compare_type_widget, 2, 2, 1, 4)
        
        # Row 3 Y axis inputs
        compare_y_widget = QWidget()
        compare_y_layout = QHBoxLayout(compare_y_widget)
        compare_y_layout.setContentsMargins(0, 0, 0, 0)
        
        compare_y_layout.addWidget(QLabel("Min:"))
        self.dqdv_min_entry = QLineEdit()
        self.dqdv_min_entry.setFixedWidth(50)
        bind_lineedit(self.dqdv_min_entry, self.dqdv_min_var)
        self.dqdv_min_entry.returnPressed.connect(lambda: self.update_plot())
        compare_y_layout.addWidget(self.dqdv_min_entry)
        
        compare_y_layout.addWidget(QLabel("Max:"))
        self.dqdv_max_entry = QLineEdit()
        self.dqdv_max_entry.setFixedWidth(50)
        bind_lineedit(self.dqdv_max_entry, self.dqdv_max_var)
        self.dqdv_max_entry.returnPressed.connect(lambda: self.update_plot())
        compare_y_layout.addWidget(self.dqdv_max_entry)
        
        compare_y_layout.addWidget(QLabel("标题:"))
        self.dqdv_title_entry = QLineEdit()
        bind_lineedit(self.dqdv_title_entry, self.dqdv_title_var)
        self.dqdv_title_entry.returnPressed.connect(lambda: self.update_plot())
        compare_y_layout.addWidget(self.dqdv_title_entry)
        
        self.compare_apply_btn = QPushButton("应用")
        self.compare_apply_btn.clicked.connect(self.update_plot)
        compare_y_layout.addWidget(self.compare_apply_btn)
        
        compare_grid.addWidget(QLabel("循环Y轴:"), 3, 0)
        compare_grid.addWidget(compare_y_widget, 3, 1, 1, 5)
        
        compare_box_lay = QVBoxLayout(self.cycle_compare_frame)
        compare_box_lay.setContentsMargins(10, 0, 10, 10)
        compare_box_lay.addWidget(compare_content)
        
        def on_compare_group_toggled(checked):
            if self.cycle_compare_var.get() != checked:
                self.cycle_compare_var.set(checked)
            compare_content.setVisible(checked)
            self.on_cycle_compare_toggle()

        self.cycle_compare_frame.toggled.connect(on_compare_group_toggled)
        self.cycle_compare_var.trace_add('write', lambda *args: self.cycle_compare_frame.setChecked(bool(self.cycle_compare_var.get())))
        
        control_layout.addWidget(self.cycle_compare_frame)
        
        # 5. Plot Frame (direct layout inside left layout, collapsible)
        self.plot_frame = QGroupBox("绘图选项")
        self.plot_frame.setCheckable(True)
        self.plot_frame.setChecked(True)
        
        plot_content = QWidget()
        plot_layout = QVBoxLayout(plot_content)
        plot_layout.setContentsMargins(0, 5, 0, 0)
        
        # X Axis selection & bounds (optimized to a single horizontal layout)
        x_axis_widget = QWidget()
        x_axis_layout = QHBoxLayout(x_axis_widget)
        x_axis_layout.setContentsMargins(0, 0, 0, 0)
        x_axis_layout.setSpacing(4)
        
        x_axis_layout.addWidget(QLabel("X轴:"))
        self.x_combo = CustomComboBox()
        self.x_combo.setFixedWidth(110)
        bind_combobox(self.x_combo, self.x_axis)
        self.x_combo.currentIndexChanged.connect(lambda: self.delayed_update())
        x_axis_layout.addWidget(self.x_combo)
        
        x_axis_layout.addWidget(QLabel("Min:"))
        self.x_min_entry = QLineEdit()
        self.x_min_entry.setFixedWidth(50)
        bind_lineedit(self.x_min_entry, self.x_min_var)
        self.x_min_entry.returnPressed.connect(lambda: self.update_plot())
        x_axis_layout.addWidget(self.x_min_entry)
        
        x_axis_layout.addWidget(QLabel("Max:"))
        self.x_max_entry = QLineEdit()
        self.x_max_entry.setFixedWidth(50)
        bind_lineedit(self.x_max_entry, self.x_max_var)
        self.x_max_entry.returnPressed.connect(lambda: self.update_plot())
        x_axis_layout.addWidget(self.x_max_entry)
        
        x_axis_layout.addWidget(QLabel("标题:"))
        self.x_title = QLineEdit("")
        self.x_title.setFixedWidth(100)
        self.x_title.textChanged.connect(lambda: self.delayed_update())
        self.x_title.returnPressed.connect(lambda: self.update_plot())
        x_axis_layout.addWidget(self.x_title)
        
        plot_layout.addWidget(x_axis_widget)
        
        # Y Axis list boxes
        y_axis_widget = QWidget()
        y_axis_widget.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
        y_axis_layout = QHBoxLayout(y_axis_widget)
        y_axis_layout.setContentsMargins(0, 0, 0, 0)
        
        self.y_listboxes = []
        self.y_filter_entries = []
        self.y_selections = [[], [], []]
        
        for i in range(3):
            pane = QWidget()
            pane.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            pane_lay = QVBoxLayout(pane)
            pane_lay.setContentsMargins(2, 2, 2, 2)
            
            header_lay = QHBoxLayout()
            header_lay.setContentsMargins(0, 0, 0, 0)
            lbl = QLabel(f"Y{i+1}:")
            lbl.setFixedWidth(24)
            header_lay.addWidget(lbl)

            filter_entry = QLineEdit()
            filter_entry.setPlaceholderText("筛选...")
            filter_entry.textChanged.connect(lambda text, idx=i: self.filter_y_listbox(idx, text))
            header_lay.addWidget(filter_entry)
            self.y_filter_entries.append(filter_entry)

            pane_lay.addLayout(header_lay)
            
            listbox = CustomListWidget()
            listbox.bind('<<ListboxSelect>>', self.on_selection_change)
            listbox.setMinimumHeight(150)
            listbox.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Expanding)
            pane_lay.addWidget(listbox)
            self.y_listboxes.append(listbox)
            y_axis_layout.addWidget(pane, 3) # Stretch factor 3 for listbox columns
            
        y_buttons_widget = QWidget()
        y_buttons_lay = QVBoxLayout(y_buttons_widget)
        y_buttons_lay.setContentsMargins(0, 0, 0, 0)
        
        self.clear_btn = QPushButton("清除")
        self.clear_btn.setFixedWidth(60)
        self.clear_btn.clicked.connect(self.clear_all_selections)
        y_buttons_lay.addWidget(self.clear_btn)
        
        self.select_all_btn = QPushButton("全选")
        self.select_all_btn.setFixedWidth(60)
        self.select_all_btn.clicked.connect(self.select_all_y)
        y_buttons_lay.addWidget(self.select_all_btn)
        
        self.plot_btn = QPushButton("绘制")
        self.plot_btn.setFixedWidth(60)
        self.plot_btn.clicked.connect(self.delayed_update)
        y_buttons_lay.addWidget(self.plot_btn)
        
        self.save_btn = QPushButton("保存")
        self.save_btn.setFixedWidth(60)
        self.save_btn.clicked.connect(self.save_plot_data)
        y_buttons_lay.addWidget(self.save_btn)
        
        y_axis_layout.addWidget(y_buttons_widget, 1) # Stretch factor 1 for button column
        plot_layout.addWidget(y_axis_widget)
        
        # Y Range Configuration (Collapsible)
        y_range_groupbox = QGroupBox("坐标轴范围")
        y_range_groupbox.setCheckable(True)
        y_range_groupbox.setChecked(True)
        
        y_range_content = QWidget()
        y_range_content_lay = QGridLayout(y_range_content)
        y_range_content_lay.setContentsMargins(0, 5, 0, 0)
        
        for i in range(3):
            y_range_content_lay.addWidget(QLabel(f"Y{i+1}轴范围 Min:"), i, 0)
            ymin_entry = QLineEdit()
            ymin_entry.setFixedWidth(50)
            bind_lineedit(ymin_entry, self.y_settings[i]['min'])
            y_range_content_lay.addWidget(ymin_entry, i, 1)
            
            y_range_content_lay.addWidget(QLabel("Max:"), i, 2)
            ymax_entry = QLineEdit()
            ymax_entry.setFixedWidth(50)
            bind_lineedit(ymax_entry, self.y_settings[i]['max'])
            y_range_content_lay.addWidget(ymax_entry, i, 3)
            
            y_range_content_lay.addWidget(QLabel("标题:"), i, 4)
            ytitle_entry = QLineEdit()
            bind_lineedit(ytitle_entry, self.y_settings[i]['title'])
            y_range_content_lay.addWidget(ytitle_entry, i, 5)
            
            self.y_settings[i]['min'].trace_add('write', lambda *args, axis=i: self.update_y_axis(axis))
            self.y_settings[i]['max'].trace_add('write', lambda *args, axis=i: self.update_y_axis(axis))
            self.y_settings[i]['title'].trace_add('write', lambda *args, axis=i: self.update_y_axis(axis))
            
        y_range_box_lay = QVBoxLayout(y_range_groupbox)
        y_range_box_lay.setContentsMargins(10, 0, 10, 10)
        y_range_box_lay.addWidget(y_range_content)
        y_range_groupbox.toggled.connect(y_range_content.setVisible)
        
        plot_layout.addWidget(y_range_groupbox)

        # Combined Plot Configuration GroupBox (Collapsible)
        plot_config_groupbox = QGroupBox("绘图配置")
        plot_config_groupbox.setCheckable(True)
        plot_config_groupbox.setChecked(True)
        
        plot_config_content = QWidget()
        plot_config_content_lay = QGridLayout(plot_config_content)
        plot_config_content_lay.setContentsMargins(0, 5, 0, 0)
        
        # Legend vertical/horizontal pos & visibility
        self.legend_visible_cb = QCheckBox("显示图例")
        bind_checkbox(self.legend_visible_cb, self.legend_visible)
        self.legend_visible_cb.toggled.connect(lambda c: self.toggle_legend())
        plot_config_content_lay.addWidget(self.legend_visible_cb, 0, 0)
        
        legend_pos_widget = QWidget()
        legend_pos_lay = QHBoxLayout(legend_pos_widget)
        legend_pos_lay.setContentsMargins(0, 0, 0, 0)
        legend_pos_lay.setSpacing(6)
        
        legend_pos_lay.addWidget(QLabel("垂直位置:"))
        self.legend_y_entry = QLineEdit()
        self.legend_y_entry.setFixedWidth(50)
        bind_lineedit(self.legend_y_entry, self.legend_y)
        legend_pos_lay.addWidget(self.legend_y_entry)
        
        legend_pos_lay.addWidget(QLabel("水平位置:"))
        self.legend_x_entry = QLineEdit()
        self.legend_x_entry.setFixedWidth(100)
        bind_lineedit(self.legend_x_entry, self.legend_x_positions_str)
        legend_pos_lay.addWidget(self.legend_x_entry)
        
        legend_pos_lay.addStretch(1) # Keep inputs tightly grouped next to labels, push reset button to the right
        
        self.reset_plot_config_btn = QPushButton("重置")
        self.reset_plot_config_btn.setFixedWidth(60)
        self.reset_plot_config_btn.clicked.connect(self.reset_plot_config)
        legend_pos_lay.addWidget(self.reset_plot_config_btn)
        
        plot_config_content_lay.addWidget(legend_pos_widget, 0, 1, 1, 5)
        
        # Legend Font, Size (QLineEdit), Column count
        plot_config_content_lay.addWidget(QLabel("图例字体:"), 1, 0)
        self.font_combo = CustomComboBox()
        self.font_combo.addItems(["Microsoft YaHei", "SimHei", "SimSun", "KaiTi", "FangSong", "Arial", "Calibri", "Times New Roman", "Segoe UI", "Tahoma"])
        bind_combobox(self.font_combo, self.font_family)
        plot_config_content_lay.addWidget(self.font_combo, 1, 1)
        
        plot_config_content_lay.addWidget(QLabel("图例列数:"), 1, 2)
        self.legend_cols_combo = CustomComboBox()
        self.legend_cols_combo.addItems(["1", "2", "3", "4", "5"])
        bind_combobox(self.legend_cols_combo, self.legend_cols)
        plot_config_content_lay.addWidget(self.legend_cols_combo, 1, 3)
        
        plot_config_content_lay.addWidget(QLabel("图例字号:"), 1, 4)
        self.legend_size_entry = QLineEdit()
        self.legend_size_entry.setFixedWidth(50)
        bind_lineedit(self.legend_size_entry, self.legend_font_size)
        self.legend_size_entry.returnPressed.connect(lambda: self.update_plot())
        plot_config_content_lay.addWidget(self.legend_size_entry, 1, 5)
        
        # Frame width, Line width, Axis Font Size (QLineEdit)
        plot_config_content_lay.addWidget(QLabel("轴线宽度:"), 2, 0)
        self.frame_width_combo = CustomComboBox()
        self.frame_width_combo.addItems(["0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0"])
        bind_combobox(self.frame_width_combo, self.frame_width)
        plot_config_content_lay.addWidget(self.frame_width_combo, 2, 1)
        
        plot_config_content_lay.addWidget(QLabel("曲线宽度:"), 2, 2)
        self.line_width_combo = CustomComboBox()
        self.line_width_combo.addItems(["0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5", "5.0"])
        bind_combobox(self.line_width_combo, self.line_width)
        plot_config_content_lay.addWidget(self.line_width_combo, 2, 3)
        
        plot_config_content_lay.addWidget(QLabel("轴线字号:"), 2, 4)
        self.font_size_entry = QLineEdit()
        self.font_size_entry.setFixedWidth(50)
        bind_lineedit(self.font_size_entry, self.font_size)
        self.font_size_entry.returnPressed.connect(lambda: self.update_plot())
        plot_config_content_lay.addWidget(self.font_size_entry, 2, 5)
        
        # Color schemes mapping
        self.color_schemes_dict = {
            '默认': None,
            'Tab10': plt.cm.tab10,
            'Set1': plt.cm.Set1,
            'Set2': plt.cm.Set2,
            'Set3': plt.cm.Set3,
            'Paired': plt.cm.Paired,
            'Dark2': plt.cm.Dark2,
            'Accent': plt.cm.Accent,
            'Pastel1': plt.cm.Pastel1,
            'Pastel2': plt.cm.Pastel2
        }

        # Y1-Y3 line styles and colors
        for i in range(3):
            row = 3 + i
            
            # Y1-Y3 Style
            style_var = Var('点划线' if i == 2 else list(self.line_styles_dict.keys())[i])
            style_combo = CustomComboBox()
            style_combo.addItems(list(self.line_styles_dict.keys()))
            bind_combobox(style_combo, style_var)
            plot_config_content_lay.addWidget(QLabel(f"Y{i+1}线型:"), row, 0)
            plot_config_content_lay.addWidget(style_combo, row, 1)
            self.line_styles.append(style_var)
            style_var.trace_add('write', lambda *args: self.update_plot())
            
            # Y1-Y3 Color
            if i == 0:
                default_scheme = 'Tab10'
            elif i == 1:
                default_scheme = 'Set1'
            else:
                default_scheme = 'Dark2'
            scheme_var = Var(default_scheme)
            scheme_combo = CustomComboBox()
            scheme_combo.addItems(list(self.color_schemes_dict.keys()))
            bind_combobox(scheme_combo, scheme_var)
            plot_config_content_lay.addWidget(QLabel(f"Y{i+1}配色:"), row, 2)
            plot_config_content_lay.addWidget(scheme_combo, row, 3, 1, 3)
            self.color_schemes.append(scheme_var)
            scheme_var.trace_add('write', lambda *args: self.update_plot())
            
        plot_config_box_lay = QVBoxLayout(plot_config_groupbox)
        plot_config_box_lay.setContentsMargins(10, 0, 10, 10)
        plot_config_box_lay.addWidget(plot_config_content)
        plot_config_groupbox.toggled.connect(plot_config_content.setVisible)
        
        plot_layout.addWidget(plot_config_groupbox)
        
        plot_box_lay = QVBoxLayout(self.plot_frame)
        plot_box_lay.setContentsMargins(10, 0, 10, 10)
        plot_box_lay.addWidget(plot_content)
        self.plot_frame.toggled.connect(plot_content.setVisible)
        
        control_layout.addWidget(self.plot_frame)
        
        # 5.5 Advanced Configuration GroupBox (Collapsible, default collapsed)
        self.adv_groupbox = QGroupBox("高级配置")
        self.adv_groupbox.setCheckable(True)
        self.adv_groupbox.setChecked(False) # Collapsed by default
        
        adv_content = QWidget()
        adv_grid = QGridLayout(adv_content)
        adv_grid.setContentsMargins(0, 5, 0, 0)
        adv_grid.setColumnStretch(0, 0)
        adv_grid.setColumnStretch(1, 1)
        adv_grid.setColumnStretch(2, 0)
        adv_grid.setColumnStretch(3, 1)
        adv_grid.setColumnStretch(4, 0)
        adv_grid.setColumnStretch(5, 1)
        
        # Row 0: Left Margin
        adv_grid.addWidget(QLabel("左侧边距 倍数:"), 0, 0)
        self.adv_left_mult_entry = QLineEdit()
        bind_lineedit(self.adv_left_mult_entry, self.adv_left_margin_mult)
        self.adv_left_mult_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_left_mult_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_left_mult_entry, 0, 1)
        
        adv_grid.addWidget(QLabel("最小像素:"), 0, 2)
        self.adv_left_min_px_entry = QLineEdit()
        bind_lineedit(self.adv_left_min_px_entry, self.adv_left_margin_min_px)
        self.adv_left_min_px_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_left_min_px_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_left_min_px_entry, 0, 3)
        
        adv_grid.addWidget(QLabel("最小比例:"), 0, 4)
        self.adv_left_min_pct_entry = QLineEdit()
        bind_lineedit(self.adv_left_min_pct_entry, self.adv_left_margin_min_pct)
        self.adv_left_min_pct_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_left_min_pct_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_left_min_pct_entry, 0, 5)
        
        # Row 1: Y3 Right Margin
        adv_grid.addWidget(QLabel("Y3右边距 倍数:"), 1, 0)
        self.adv_y3_mult_entry = QLineEdit()
        bind_lineedit(self.adv_y3_mult_entry, self.adv_y3_margin_mult)
        self.adv_y3_mult_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_y3_mult_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_y3_mult_entry, 1, 1)
        
        adv_grid.addWidget(QLabel("最小像素:"), 1, 2)
        self.adv_y3_min_px_entry = QLineEdit()
        bind_lineedit(self.adv_y3_min_px_entry, self.adv_y3_margin_min_px)
        self.adv_y3_min_px_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_y3_min_px_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_y3_min_px_entry, 1, 3)
        
        adv_grid.addWidget(QLabel("最大比例:"), 1, 4)
        self.adv_y3_max_pct_entry = QLineEdit()
        bind_lineedit(self.adv_y3_max_pct_entry, self.adv_y3_max_right_pct)
        self.adv_y3_max_pct_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_y3_max_pct_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_y3_max_pct_entry, 1, 5)
        
        # Row 2: Y2 Right Margin
        adv_grid.addWidget(QLabel("Y2右边距 倍数:"), 2, 0)
        self.adv_y2_mult_entry = QLineEdit()
        bind_lineedit(self.adv_y2_mult_entry, self.adv_y2_margin_mult)
        self.adv_y2_mult_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_y2_mult_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_y2_mult_entry, 2, 1)
        
        adv_grid.addWidget(QLabel("最小像素:"), 2, 2)
        self.adv_y2_min_px_entry = QLineEdit()
        bind_lineedit(self.adv_y2_min_px_entry, self.adv_y2_margin_min_px)
        self.adv_y2_min_px_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_y2_min_px_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_y2_min_px_entry, 2, 3)
        
        adv_grid.addWidget(QLabel("最大比例:"), 2, 4)
        self.adv_y2_max_pct_entry = QLineEdit()
        bind_lineedit(self.adv_y2_max_pct_entry, self.adv_y2_max_right_pct)
        self.adv_y2_max_pct_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_y2_max_pct_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_y2_max_pct_entry, 2, 5)
        
        # Row 3: Y1 Right Margin
        adv_grid.addWidget(QLabel("Y1右边距 倍数:"), 3, 0)
        self.adv_y1_mult_entry = QLineEdit()
        bind_lineedit(self.adv_y1_mult_entry, self.adv_y1_margin_mult)
        self.adv_y1_mult_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_y1_mult_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_y1_mult_entry, 3, 1)
        
        adv_grid.addWidget(QLabel("最小像素:"), 3, 2)
        self.adv_y1_min_px_entry = QLineEdit()
        bind_lineedit(self.adv_y1_min_px_entry, self.adv_y1_margin_min_px)
        self.adv_y1_min_px_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_y1_min_px_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_y1_min_px_entry, 3, 3)
        
        adv_grid.addWidget(QLabel("最大比例:"), 3, 4)
        self.adv_y1_max_pct_entry = QLineEdit()
        bind_lineedit(self.adv_y1_max_pct_entry, self.adv_y1_max_right_pct)
        self.adv_y1_max_pct_entry.textChanged.connect(lambda: self.delayed_update())
        self.adv_y1_max_pct_entry.returnPressed.connect(lambda: self.update_plot())
        adv_grid.addWidget(self.adv_y1_max_pct_entry, 3, 5)
        
        # Row 4: Panel Font Family, Size & Width
        adv_grid.addWidget(QLabel("面板字体:"), 4, 0)
        self.panel_font_combo = CustomComboBox()
        self.panel_font_combo.addItems(["Microsoft YaHei", "SimHei", "SimSun", "KaiTi", "FangSong", "Arial", "Calibri", "Times New Roman", "Segoe UI", "Tahoma"])
        bind_combobox(self.panel_font_combo, self.panel_font_family)
        adv_grid.addWidget(self.panel_font_combo, 4, 1)
        
        adv_grid.addWidget(QLabel("面板字号:"), 4, 2)
        self.panel_size_combo = CustomComboBox()
        self.panel_size_combo.addItems(["10", "11", "12", "13", "14", "15", "16", "17", "18"])
        bind_combobox(self.panel_size_combo, self.panel_font_size)
        adv_grid.addWidget(self.panel_size_combo, 4, 3)

        adv_grid.addWidget(QLabel("面板宽度:"), 4, 4)
        self.panel_width_entry = QLineEdit()
        bind_lineedit(self.panel_width_entry, self.panel_width_var)
        self.panel_width_entry.editingFinished.connect(self.on_panel_width_entry_changed)
        self.panel_width_entry.returnPressed.connect(self.on_panel_width_entry_changed)
        adv_grid.addWidget(self.panel_width_entry, 4, 5)

        # Row 5: Canvas Width & Canvas Background (Auto-applied)
        adv_grid.addWidget(QLabel("画布宽度:"), 5, 0)
        self.canvas_width_entry = QLineEdit()
        bind_lineedit(self.canvas_width_entry, self.canvas_width_var)
        self.canvas_width_entry.editingFinished.connect(self.on_canvas_width_entry_changed)
        self.canvas_width_entry.returnPressed.connect(self.on_canvas_width_entry_changed)
        adv_grid.addWidget(self.canvas_width_entry, 5, 1)

        adv_grid.addWidget(QLabel("画布背景:"), 5, 2)
        self.canvas_bg_combo = CustomComboBox()
        self.canvas_bg_combo.addItems(["默认(白色)", "灰色", "黑色"])
        bind_combobox(self.canvas_bg_combo, self.canvas_bg_var)
        self.canvas_bg_var.trace_add('write', lambda *args: [self.apply_canvas_background(), self.update_plot()])
        adv_grid.addWidget(self.canvas_bg_combo, 5, 3, 1, 3)

        # Row 6: Buttons Layout (默认设置 & 保存设置)
        btn_layout = QHBoxLayout()
        self.adv_reset_btn = QPushButton("默认设置")
        self.adv_reset_btn.clicked.connect(self.reset_advanced_settings)
        btn_layout.addWidget(self.adv_reset_btn)

        self.adv_save_btn = QPushButton("保存设置")
        self.adv_save_btn.clicked.connect(self.save_advanced_settings)
        btn_layout.addWidget(self.adv_save_btn)

        adv_grid.addLayout(btn_layout, 6, 0, 1, 6)
        
        adv_box_lay = QVBoxLayout(self.adv_groupbox)
        adv_box_lay.setContentsMargins(10, 0, 10, 10)
        adv_box_lay.addWidget(adv_content)
        self.adv_groupbox.toggled.connect(adv_content.setVisible)
        adv_content.setVisible(False) # Collapsed by default
        
        control_layout.addWidget(self.adv_groupbox)

        # 6. Status console widget
        console_groupbox = QGroupBox("状态日志")
        console_groupbox.setCheckable(True)
        console_groupbox.setChecked(True)
        
        console_content = QWidget()
        console_lay = QVBoxLayout(console_content)
        console_lay.setContentsMargins(0, 5, 0, 0)
        
        self.status_text = QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setFixedHeight(120)
        console_lay.addWidget(self.status_text)
        
        console_box_lay = QVBoxLayout(console_groupbox)
        console_box_lay.setContentsMargins(10, 0, 10, 10)
        console_box_lay.addWidget(console_content)
        console_groupbox.toggled.connect(console_content.setVisible)
        
        control_layout.addWidget(console_groupbox)

        # Right Panel (Matplotlib Canvas)
        plot_display_widget = QWidget(splitter)
        self.plot_display_widget = plot_display_widget
        self.canvas_resize_filter = CanvasResizeFilter(self)
        plot_display_widget.installEventFilter(self.canvas_resize_filter)
        plot_display_lay = QVBoxLayout(plot_display_widget)
        plot_display_lay.setContentsMargins(5, 5, 5, 5)
        
        self.fig = plt.figure(figsize=(10, 8))
        self.ax = self.fig.add_subplot(111)
        self.ax.set_xticks([])
        self.ax.set_yticks([])
        self.canvas = FigureCanvas(self.fig)
        plot_display_lay.addWidget(self.canvas)
        
        # Navigation toolbar
        self.toolbar = NavigationToolbar(self.canvas, plot_display_widget)
        plot_display_lay.addWidget(self.toolbar)
        
        self.axes = {'y1': self.ax}
        self.current_axes = {'y1': self.ax}
        
        splitter.addWidget(plot_display_widget)
        splitter.setSizes([560, 1000])   # Left panel width 560, right canvas width 1000
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        self.resize(1600, 900)

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
                        self.status_text.clear()
                    self.status_text.append(message)
                    self.status_text.ensureCursorVisible()
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
        QTimer.singleShot(100, self.check_queue)

    def set_buttons_state(self, enabled=True):
        """控制交互按钮状态与光标形状，避免重复并发操作"""
        self.process_btn.setEnabled(enabled)
        self.browse_btn.setEnabled(enabled)
        if hasattr(self, 'battery_calc_btn'):
            self.battery_calc_btn.setEnabled(enabled)
        self.clear_btn.setEnabled(enabled)
        self.select_all_btn.setEnabled(enabled)
        self.plot_btn.setEnabled(enabled)
        self.save_btn.setEnabled(enabled)
        self.csv2xlsx_btn.setEnabled(enabled)
        if hasattr(self, 'compare_apply_btn'):
            self.compare_apply_btn.setEnabled(enabled)
        
        if enabled:
            self.unsetCursor()
        else:
            self.setCursor(Qt.WaitCursor)

    def update_file_type(self):
        if not hasattr(self, 'battery_filter_frame') or not hasattr(self, 'cycle_compare_frame') or not hasattr(self, 'sheet_combo'):
            return
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

        # Visibility controls
        if self.file_type.get() == "battery":
            self.battery_filter_frame.setVisible(True)
            self.cycle_compare_frame.setVisible(True)
        else:
            self.battery_filter_frame.setVisible(False)
            self.cycle_compare_frame.setVisible(False)

        if self.file_type.get() == "raw":
            self.sheet_combo.setEnabled(True)
        else:
            if self.file_path.get() and self.file_path.get().endswith('.xlsx'):
                self.sheet_combo.setEnabled(True)
            else:
                self.sheet_combo.setEnabled(False)

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
        if self.result_df is not None and hasattr(self, 'cycle_col_combo'):
            cycle_col_name = self.cycle_col_combo.currentText()
            if hasattr(self, 'cycle_col'):
                self.cycle_col.set(cycle_col_name)
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
                
                time_col_name = self.time_col_combo.currentText() if hasattr(self, 'time_col_combo') else None
                step_col_name = self.step_col_combo.currentText() if hasattr(self, 'step_col_combo') else None
                self.recompute_all_time_diffs(self.result_df, time_col_name, cycle_col_name, step_col_name)
                self.update_listboxes()
                self.delayed_update()

    def on_step_col_changed(self, event=None):
        if self.result_df is not None and hasattr(self, 'step_col_combo'):
            step_col_name = self.step_col_combo.currentText()
            if hasattr(self, 'step_col'):
                self.step_col.set(step_col_name)
            if step_col_name in self.result_df.columns:
                steps = ['全部'] + [str(x) for x in self.result_df[step_col_name].dropna().unique()]
                self.step_filter_combo['values'] = steps
                self.step_filter_combo.set('全部')

                time_col_name = self.time_col_combo.currentText() if hasattr(self, 'time_col_combo') else None
                cycle_col_name = self.cycle_col_combo.currentText() if hasattr(self, 'cycle_col_combo') else None
                self.recompute_all_time_diffs(self.result_df, time_col_name, cycle_col_name, step_col_name)
                self.update_listboxes()
                self.delayed_update()

    def on_time_col_changed(self, event=None):
        if self.result_df is not None and hasattr(self, 'time_col_combo'):
            time_col_name = self.time_col_combo.currentText()
            if hasattr(self, 'time_col'):
                self.time_col.set(time_col_name)
            self._time_col_just_changed = True
            if time_col_name in self.result_df.columns:
                base_time_name = time_col_name.replace('_时间差(s)', '')
                new_col_name = f"{base_time_name}_时间差(s)"
                self.set_buttons_state(False)
                self.update_status(f"正在后台计算 {time_col_name} 的时间差(s)...")
                threading.Thread(target=self._bg_calc_time_diff, args=(time_col_name, new_col_name), daemon=True).start()
                return
            self.update_listboxes()
            self.delayed_update()

    def clear_canvas_and_update(self):
        if hasattr(self, 'fig'):
            self.fig.clf()
            self.ax = self.fig.add_subplot(111)
            self.ax.set_xticks([])
            self.ax.set_yticks([])
            self.canvas.draw()
        self.update_listboxes()
        self.delayed_update()

    def clear_all_selections(self):
        """清除所有选择"""
        for i, listbox in enumerate(self.y_listboxes):
            listbox.selection_clear()
            self.y_selections[i] = []
        if hasattr(self, 'fig'):
            self.fig.clf()
            self.ax = self.fig.add_subplot(111)
            self.ax.set_xticks([])
            self.ax.set_yticks([])
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

    def filter_y_listbox(self, idx, text):
        """筛选 Y1/Y2/Y3 列表框中的列名"""
        if hasattr(self, 'y_listboxes') and idx < len(self.y_listboxes):
            listbox = self.y_listboxes[idx]
            filter_txt = str(text).strip().lower()
            for row in range(listbox.count()):
                item = listbox.item(row)
                if item:
                    if not filter_txt or filter_txt in item.text().lower():
                        item.setHidden(False)
                    else:
                        item.setHidden(True)

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
            self.status_text.clear()
        self.status_text.append(message)
        self.status_text.ensureCursorVisible()

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
            
            if self.file_type.get() == "battery":
                time_col_name = self.time_col.get() if hasattr(self, 'time_col') else None
                cycle_col_name = self.cycle_col.get() if hasattr(self, 'cycle_col') else None
                step_col_name = self.step_col.get() if hasattr(self, 'step_col') else None
                if time_col_name and time_col_name in self.result_df.columns:
                    self.recompute_all_time_diffs(self.result_df, time_col_name, cycle_col_name, step_col_name)

            self.result_df['Index'] = self.result_df.index
            columns = ['Index'] + [col for col in self.result_df.columns if col != 'Index']
            
            is_compare = (self.file_type.get() == "battery" and self.cycle_compare_var.get())
            if is_compare:
                if '容量（计算）' not in columns:
                    columns.append('容量（计算）')
                if '循环时间（计算）' not in columns:
                    columns.append('循环时间（计算）')
                    
            if self.file_type.get() == "battery":
                step_val = self.step_filter_combo.currentText() if hasattr(self, 'step_filter_combo') else self.step_filter.get()
                if step_val != "全部" and step_val != "":
                    if '工步时间（计算）' not in columns:
                        columns.append('工步时间（计算）')
            
            self.x_combo.blockSignals(True)
            try:
                self.x_combo['values'] = columns
            finally:
                self.x_combo.blockSignals(False)

            if is_compare:
                self.compare_x_combo.blockSignals(True)
                try:
                    self.compare_x_combo['values'] = columns
                    target_cmp_x = self.compare_x_var.get()
                    if not target_cmp_x or target_cmp_x not in columns:
                        target_cmp_x = '循环时间（计算）'
                    self.compare_x_combo.set(target_cmp_x)
                    self.compare_x_var.set(target_cmp_x)
                finally:
                    self.compare_x_combo.blockSignals(False)
            
            if self.file_type.get() == "battery" and hasattr(self, 'time_col') and self.time_col.get():
                time_col_name = self.time_col.get()
                base_time_name = time_col_name.replace('_时间差(s)', '')
                time_diff_col = f"{base_time_name}_时间差(s)"

                if is_compare:
                    cur_cmp_x = self.compare_x_var.get()
                    if cur_cmp_x and cur_cmp_x in columns:
                        self.x_combo.set(cur_cmp_x)
                    else:
                        self.x_combo.set('循环时间（计算）')
                else:
                    target_x = None
                    if getattr(self, '_time_col_just_changed', False):
                        if time_diff_col in columns:
                            target_x = time_diff_col
                        elif time_col_name in columns:
                            target_x = time_col_name
                        self._time_col_just_changed = False

                    if not target_x:
                        prev_x = getattr(self, '_prev_regular_x_col', '')
                        if prev_x and prev_x in columns:
                            target_x = prev_x
                        elif self.x_axis.get() and self.x_axis.get() in columns:
                            target_x = self.x_axis.get()
                        elif time_diff_col in columns:
                            target_x = time_diff_col
                        elif time_col_name in columns:
                            target_x = time_col_name
                        else:
                            target_x = columns[0]

                    self.x_combo.set(target_x)
                    self.x_axis.set(target_x)
            else:
                self.x_combo.set(columns[0])
            
            saved_selections = [list(sel) for sel in self.y_selections]
            for i, listbox in enumerate(self.y_listboxes):
                listbox.blockSignals(True)
                try:
                    listbox.delete()
                    target_sel = saved_selections[i] if i < len(saved_selections) else []
                    new_sel = []
                    filter_txt = self.y_filter_entries[i].text().strip().lower() if (hasattr(self, 'y_filter_entries') and i < len(self.y_filter_entries)) else ""
                    for col in self.result_df.columns:
                        if col != 'Index':
                            listbox.insert('end', col)
                            idx = listbox.count() - 1
                            item = listbox.item(idx)
                            if item:
                                if col in target_sel:
                                    item.setSelected(True)
                                    new_sel.append(col)
                                if filter_txt and filter_txt not in col.lower():
                                    item.setHidden(True)
                    self.y_selections[i] = new_sel
                finally:
                    listbox.blockSignals(False)
                    
        except Exception as e:
            self.update_status(f"更新列表失败: {str(e)}")

    def reset_plot_config(self):
        self.legend_visible.set(True)
        self.legend_y.set("1.02")
        self.legend_x_positions_str.set("0, 0.3, 0.6")
        self.legend_font_size.set("18")
        self.legend_cols.set("1")
        self.frame_width.set("1.5")
        self.line_width.set("1.5")
        self.font_size.set("18")
        
        # Color schemes & line styles defaults
        if len(self.color_schemes) >= 3:
            self.color_schemes[0].set("Tab10")
            self.color_schemes[1].set("Set1")
            self.color_schemes[2].set("Dark2")
        if len(self.line_styles) >= 3:
            self.line_styles[0].set("实线")
            self.line_styles[1].set("虚线")
            self.line_styles[2].set("点划线")
            
        self.update_font_and_plot()

    def update_panel_font(self):
        """更新左侧配置面板字号与字体类型"""
        font_fam = self.panel_font_family.get() if hasattr(self, 'panel_font_family') and self.panel_font_family.get() else 'Microsoft YaHei'
        font_sz = self.panel_font_size.get() if hasattr(self, 'panel_font_size') and self.panel_font_size.get() else '13'
        try:
            font_sz_int = int(font_sz)
        except ValueError:
            font_sz_int = 13
        
        style = f"""
            QWidget#controlWidget, QWidget#controlWidget QLabel, QWidget#controlWidget QLineEdit, 
            QWidget#controlWidget QComboBox, QWidget#controlWidget QCheckBox, QWidget#controlWidget QRadioButton, 
            QWidget#controlWidget QPushButton, QWidget#controlWidget QGroupBox, QWidget#controlWidget QListWidget {{
                font-family: "{font_fam}", "Microsoft YaHei", sans-serif;
                font-size: {font_sz_int}px;
            }}
            QWidget#controlWidget QGroupBox {{
                font-weight: bold;
                font-size: {font_sz_int + 1}px;
            }}
        """
        if hasattr(self, 'control_widget') and self.control_widget:
            self.control_widget.setStyleSheet(style)

    def get_default_panel_width(self):
        """默认面板宽度为 560px"""
        return 560

    def moveEvent(self, event):
        super().moveEvent(event)
        cur_w = self.width()
        last_w = getattr(self, '_last_win_width', cur_w)
        if cur_w == last_w:
            geom = self.frameGeometry()
            self._last_frame_left = geom.left()
            self._last_frame_right = geom.right()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if getattr(self, '_is_syncing_splitter', False):
            return
        if self.isMaximized():
            return
        
        old_sz = event.oldSize()
        new_sz = event.size()
        cur_geom = self.frameGeometry()
        cur_left = cur_geom.left()
        cur_right = cur_geom.right()

        old_left = getattr(self, '_last_frame_left', cur_left)
        old_right = getattr(self, '_last_frame_right', cur_right)

        self._last_frame_left = cur_left
        self._last_frame_right = cur_right
        self._last_win_width = new_sz.width()

        if old_sz.isValid() and old_sz.width() > 0:
            dw = new_sz.width() - old_sz.width()
            if dw != 0:
                left_shift = abs(cur_left - old_left)
                right_shift = abs(cur_right - old_right)

                # 只有当【左外框在移动】且【右外框未动】时，才判定为拖动最左侧外框
                is_left_drag = (left_shift >= 2 and right_shift <= 2)

                if is_left_drag:
                    # 拖动左外框：面板宽度应随之变化，画布宽度保持不变
                    # setStretchFactor 对当前 resizeEvent 无效（Qt 已完成本帧布局），
                    # 必须用 setSizes 手动校正。
                    sizes = self.splitter.sizes()
                    if sizes and len(sizes) >= 2:
                        cur_c = sizes[1]  # 使用当前实际画布宽度
                        new_p = max(200, sizes[0] + dw)
                        self._is_syncing_splitter = True
                        try:
                            self.splitter.setSizes([new_p, cur_c])
                        finally:
                            self._is_syncing_splitter = False
                        self._saved_panel_width = new_p
                        self._saved_canvas_width = cur_c
                else:
                    # 拖动右外框：stretch factor (0,1) 原生将增量分配给画布，面板不变
                    # 确保 stretch factor 正确（中间拖拽后可能被改变）
                    self.splitter.setStretchFactor(0, 0)
                    self.splitter.setStretchFactor(1, 1)
                    sizes = self.splitter.sizes()
                    if sizes and len(sizes) >= 2:
                        self._saved_panel_width = sizes[0]
                        self._saved_canvas_width = sizes[1]

                # 同步显示
                self._sync_size_display()

    def _sync_size_display(self):
        """同步面板/画布宽度显示到输入框"""
        p = int(getattr(self, '_saved_panel_width', 560))
        c = int(getattr(self, '_saved_canvas_width', 1000))
        if hasattr(self, 'panel_width_var'):
            self.panel_width_var.set(str(p))
        if hasattr(self, 'canvas_width_var'):
            self.canvas_width_var.set(str(c))
        if hasattr(self, 'panel_width_entry') and self.panel_width_entry:
            if self.panel_width_entry.text() != str(p):
                self.panel_width_entry.setText(str(p))
        if hasattr(self, 'canvas_width_entry') and self.canvas_width_entry:
            if self.canvas_width_entry.text() != str(c):
                self.canvas_width_entry.setText(str(c))

    def on_splitter_moved(self, pos, index):
        if getattr(self, '_is_syncing_splitter', False):
            return
        if hasattr(self, 'splitter') and self.splitter:
            sizes = self.splitter.sizes()
            if sizes and len(sizes) >= 2:
                panel_w, canvas_w = sizes[0], sizes[1]
                saved_p = getattr(self, '_saved_panel_width', 560)
                if abs(panel_w - saved_p) >= 2:
                    self._saved_panel_width = panel_w
                    self._saved_canvas_width = canvas_w
                    # 中间分隔条拖拽结束后，恢复 stretch factor (0,1)
                    # 保证后续拖动右外框时，增量全部分配给画布
                    self.splitter.setStretchFactor(0, 0)
                    self.splitter.setStretchFactor(1, 1)
                    self._sync_size_display()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.WindowStateChange:
            if hasattr(self, 'splitter') and self.splitter:
                self._apply_proportional_splitter_sizes()

    def _apply_proportional_splitter_sizes(self):
        """窗口最大化或状态改变时：按当前面板与画布的比值，结合当前屏幕/窗口尺寸重新分配宽度"""
        if not hasattr(self, 'splitter') or not self.splitter:
            return
            
        cur_p = int(getattr(self, '_saved_panel_width', 560))
        cur_c = int(getattr(self, '_saved_canvas_width', 1000))
        
        self._is_syncing_splitter = True
        try:
            if self.isMaximized():
                from PySide6.QtWidgets import QApplication
                screen_geom = QApplication.primaryScreen().availableGeometry()
                avail_w = max(1000, screen_geom.width() - 40)
                total_saved = max(1, cur_p + cur_c)
                ratio = cur_p / total_saved
                
                new_p = int(avail_w * ratio)
                new_c = avail_w - new_p
            else:
                new_p = cur_p
                new_c = cur_c

            self.splitter.setSizes([new_p, new_c])
            self.splitter.setStretchFactor(0, 0)
            self.splitter.setStretchFactor(1, 1)

            if hasattr(self, 'panel_width_var'):
                self.panel_width_var.set(str(new_p))
            if hasattr(self, 'canvas_width_var'):
                self.canvas_width_var.set(str(new_c))
        finally:
            self._is_syncing_splitter = False

        if hasattr(self, 'on_window_resize'):
            self.on_window_resize()

    def on_splitter_moved(self, pos, index):
        if hasattr(self, 'splitter') and self.splitter:
            sizes = self.splitter.sizes()
            if sizes and len(sizes) >= 2:
                panel_w, canvas_w = sizes[0], sizes[1]
                saved_p = getattr(self, '_saved_panel_width', 560)
                if abs(panel_w - saved_p) >= 3 and not getattr(self, '_is_syncing_splitter', False):
                    self._saved_panel_width = panel_w
                    self._saved_canvas_width = canvas_w
                    self._is_syncing_splitter = True
                    try:
                        self.splitter.setStretchFactor(0, 0)
                        self.splitter.setStretchFactor(1, 1)
                        if hasattr(self, 'panel_width_var'):
                            self.panel_width_var.set(str(panel_w))
                        if hasattr(self, 'canvas_width_var'):
                            self.canvas_width_var.set(str(canvas_w))
                        if hasattr(self, 'panel_width_entry') and self.panel_width_entry:
                            self.panel_width_entry.setText(str(panel_w))
                        if hasattr(self, 'canvas_width_entry') and self.canvas_width_entry:
                            self.canvas_width_entry.setText(str(canvas_w))
                    finally:
                        self._is_syncing_splitter = False

    def on_step_filter_changed(self):
        """工步筛选改变：更新X轴下拉列，并立刻重新绘制当前视图"""
        if hasattr(self, 'step_filter_combo') and self.step_filter_combo:
            self.step_filter.set(self.step_filter_combo.currentText())
        self.update_file_type()
        if self.file_type.get() == "battery" and self.cycle_compare_var.get():
            step_val = self.step_filter.get()
            if step_val == "全部":
                self.compare_x_var.set("循环时间（计算）")
            # 如果 step_val != "全部"，100% 保留用户在 compare_x_var 中自主选择的列（例如 "容量（计算）"），绝不清空或重置为循环时间！
        self.update_plot()

    def on_cycle_filter_changed(self):
        """循环筛选改变：立刻重绘当前视图"""
        if hasattr(self, 'cycle_filter_combo') and self.cycle_filter_combo:
            self.cycle_filter.set(self.cycle_filter_combo.currentText())
        self.update_plot()

    def save_advanced_settings(self):
        """点击高级设置面板中的【保存设置】按钮：将高级设置参数写入 settings.json 持久化保存"""
        try:
            p_val = int(self.panel_width_var.get())
            if p_val >= 200:
                self._saved_panel_width = p_val
        except ValueError:
            pass

        try:
            c_val = int(self.canvas_width_var.get())
            if c_val >= 200:
                self._saved_canvas_width = c_val
        except ValueError:
            pass

        self.on_panel_width_entry_changed()
        self.on_canvas_width_entry_changed()

        if hasattr(self, 'save_settings'):
            self.save_settings()
        self.update_status("高级设置已保存成功")

    def on_panel_width_entry_changed(self):
        """修改【面板宽度】输入框：保持画布宽度不变，调整主窗口总宽度以匹配新面板宽度"""
        if getattr(self, '_is_syncing_splitter', False):
            return
        try:
            new_panel_w = int(self.panel_width_var.get())
            if new_panel_w >= 200 and hasattr(self, 'splitter') and self.splitter:
                try:
                    cur_c = int(self.canvas_width_var.get())
                except Exception:
                    cur_c = 1000
                
                self._saved_panel_width = new_panel_w
                self._saved_canvas_width = cur_c

                self._is_syncing_splitter = True
                try:
                    if not self.isMaximized():
                        self.resize(new_panel_w + cur_c, self.height())
                    self.splitter.setSizes([new_panel_w, cur_c])
                    self.splitter.setStretchFactor(0, 0)
                    self.splitter.setStretchFactor(1, 1)
                finally:
                    self._is_syncing_splitter = False

                if hasattr(self, 'on_window_resize'):
                    self.on_window_resize()
        except ValueError:
            pass

    def on_canvas_width_entry_changed(self):
        """修改【画布宽度】输入框：保持面板宽度不变，调整主窗口总宽度以匹配新画布宽度"""
        if getattr(self, '_is_syncing_splitter', False):
            return
        try:
            new_canvas_w = int(self.canvas_width_var.get())
            if new_canvas_w >= 200 and hasattr(self, 'splitter') and self.splitter:
                try:
                    cur_p = int(self.panel_width_var.get())
                except Exception:
                    cur_p = 560

                self._saved_panel_width = cur_p
                self._saved_canvas_width = new_canvas_w

                self._is_syncing_splitter = True
                try:
                    if not self.isMaximized():
                        self.resize(cur_p + new_canvas_w, self.height())
                    self.splitter.setSizes([cur_p, new_canvas_w])
                    self.splitter.setStretchFactor(0, 0)
                    self.splitter.setStretchFactor(1, 1)
                finally:
                    self._is_syncing_splitter = False

                if hasattr(self, 'on_window_resize'):
                    self.on_window_resize()
        except ValueError:
            pass

    def apply_loaded_panel_and_canvas_width(self):
        """加载 settings.json 后：应用保存的面板宽度和画布宽度到主窗口及 splitter"""
        if not hasattr(self, 'splitter') or not self.splitter:
            return
        pw = int(getattr(self, '_saved_panel_width', 560))
        cw = int(getattr(self, '_saved_canvas_width', 1000))
        self._is_syncing_splitter = True
        try:
            total_w = pw + cw + self.splitter.handleWidth() + 20
            if not self.isMaximized():
                self.resize(total_w, self.height())
            self.splitter.setSizes([pw, cw])
            self.splitter.setStretchFactor(0, 0)
            self.splitter.setStretchFactor(1, 1)
            if hasattr(self, 'panel_width_var'):
                self.panel_width_var.set(str(pw))
            if hasattr(self, 'canvas_width_var'):
                self.canvas_width_var.set(str(cw))
            if hasattr(self, 'panel_width_entry') and self.panel_width_entry:
                if self.panel_width_entry.text() != str(pw):
                    self.panel_width_entry.setText(str(pw))
            if hasattr(self, 'canvas_width_entry') and self.canvas_width_entry:
                if self.canvas_width_entry.text() != str(cw):
                    self.canvas_width_entry.setText(str(cw))
        finally:
            self._is_syncing_splitter = False

    def apply_advanced_settings(self):
        """点击高级配置中的应用按钮：立即应用高级参数设置"""
        self.on_panel_width_entry_changed()
        self.on_canvas_width_entry_changed()
        if hasattr(self, 'apply_canvas_background'):
            self.apply_canvas_background()
        self.update_plot()
    def reset_plot_config(self):
        """重置图表与图例配置参数"""
        self.font_family.set("Microsoft YaHei")
        self.font_size.set("18")
        self.legend_font_size.set("18")
        self.legend_cols.set("3")
        self.frame_width.set("1.5")
        self.line_width.set("1.5")
        self.legend_visible.set(True)
        self.legend_y.set("1.02")
        self.legend_x_positions_str.set("0, 0.3, 0.7")
        self.update_plot()

    def reset_advanced_settings(self):
        self.adv_left_margin_mult.set("4.5")
        self.adv_left_margin_min_px.set("80")
        self.adv_left_margin_min_pct.set("0.08")
        
        self.adv_y3_margin_mult.set("9.5")
        self.adv_y3_margin_min_px.set("170")
        self.adv_y3_max_right_pct.set("0.83")
        
        self.adv_y2_margin_mult.set("4.0")
        self.adv_y2_margin_min_px.set("75")
        self.adv_y2_max_right_pct.set("0.93")
        
        self.adv_y1_margin_mult.set("1.5")
        self.adv_y1_margin_min_px.set("20")
        self.adv_y1_max_right_pct.set("0.97")
        
        self.panel_font_family.set("Microsoft YaHei")
        self.panel_font_size.set("13")
        self.canvas_bg_var.set("默认(白色)")
        
        def_panel_w = 560
        def_canvas_w = 1000
        self._saved_panel_width = def_panel_w
        self._saved_canvas_width = def_canvas_w
        
        self.panel_width_var.set(str(def_panel_w))
        self.canvas_width_var.set(str(def_canvas_w))
        if hasattr(self, 'panel_width_entry') and self.panel_width_entry:
            self.panel_width_entry.setText(str(def_panel_w))
        if hasattr(self, 'canvas_width_entry') and self.canvas_width_entry:
            self.canvas_width_entry.setText(str(def_canvas_w))
            
        self._is_syncing_splitter = True
        try:
            self.resize(def_panel_w + def_canvas_w, self.height())
            if hasattr(self, 'splitter') and self.splitter:
                self.splitter.setSizes([def_panel_w, def_canvas_w])
        finally:
            self._is_syncing_splitter = False
                
        self.update_panel_font()
        self.apply_canvas_background()
        self.update_plot()

    def showEvent(self, event):
        super().showEvent(event)
        if not getattr(self, '_has_shown_once', False):
            self._has_shown_once = True
            def_p = int(getattr(self, '_saved_panel_width', 560))
            def_c = int(getattr(self, '_saved_canvas_width', 1000))
            self._is_syncing_splitter = True
            try:
                if hasattr(self, 'splitter') and self.splitter:
                    self.splitter.setSizes([def_p, def_c])
                if hasattr(self, 'panel_width_var'):
                    self.panel_width_var.set(str(def_p))
                if hasattr(self, 'canvas_width_var'):
                    self.canvas_width_var.set(str(def_c))
                if hasattr(self, 'panel_width_entry') and self.panel_width_entry:
                    self.panel_width_entry.setText(str(def_p))
                if hasattr(self, 'canvas_width_entry') and self.canvas_width_entry:
                    self.canvas_width_entry.setText(str(def_c))
            finally:
                self._is_syncing_splitter = False

    def closeEvent(self, event):
        """窗口关闭时的清理工作"""
        try:
            self.clear_memory()
            self.save_settings()
            logging.shutdown()
            event.accept()
            os._exit(0)
        except Exception as e:
            print(f"关闭程序时出错: {str(e)}")
            event.accept()
            os._exit(1)
