import os
import sys
import tkinter as tk

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

# Enable high DPI awareness to support high resolution screen scaling
try:
    import ctypes
    ctypes.windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    try:
        import ctypes
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

from DataPlot_app_gui import PlotterGUI

def main():
    root = tk.Tk()
    app = PlotterGUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
