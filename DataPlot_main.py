import os
import sys

# 解决 Windows 控制台打印中文乱码或 UnicodeEncodeError 的编码问题
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass
if hasattr(sys.stderr, 'reconfigure'):
    try:
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# Patch openpyxl datetime bug dynamically
try:
    import openpyxl.descriptors.base
    import datetime
    _orig_convert = openpyxl.descriptors.base._convert
    def _patched_convert(expected_type, value):
        if expected_type == datetime.datetime and isinstance(value, datetime.date) and not isinstance(value, datetime.datetime):
            return datetime.datetime(value.year, value.month, value.day)
        return _orig_convert(expected_type, value)
    openpyxl.descriptors.base._convert = _patched_convert
except Exception:
    pass

# Qt6 (PySide6) 原生支持 High DPI，无需额外调用 Windows API
# 但为向后兼容，仍设置 DPI awareness
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from DataPlot_app_gui import PlotterGUI

def resource_path(relative_path):
    """ 获取资源的绝对路径，兼容开发环境与PyInstaller打包后的环境 """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def main():
    app = QApplication(sys.argv)
    
    # 设置应用图标
    icon_path = resource_path('icon.ico')
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))
    
    window = PlotterGUI()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
