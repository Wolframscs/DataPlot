import PyInstaller.__main__
import os

# 检查settings.json是否存在，如果不存在则创建一个空的
if not os.path.exists('settings.json'):
    with open('settings.json', 'w') as f:
        f.write('{}')

upx_dir = os.environ.get('UPX_DIR', '')
cmd = [
    'DataPlot_main.py',
    '--name=DataPlot',
    '--onedir',
    '--clean',
    '--noconfirm',
    '--hidden-import=pandas',
    '--hidden-import=numpy',
    '--hidden-import=matplotlib',
    '--hidden-import=openpyxl',
    '--hidden-import=et_xmlfile',
    '--hidden-import=python_calamine',
    '--hidden-import=polars',
    '--hidden-import=xlsxwriter',
    '--collect-all=openpyxl',
    '--optimize=2',
    '--noconsole',
    '--exclude-module=PyQt5',
    '--exclude-module=PyQt6',
    '--exclude-module=PySide2',
    '--exclude-module=PySide6',
    '--exclude-module=IPython',
    '--exclude-module=notebook',
    '--exclude-module=sqlite3',
    '--exclude-module=jinja2',
    '--exclude-module=tornado',
    '--exclude-module=pyarrow',
    '--exclude-module=lxml',
    '--exclude-module=matplotlib.tests',
    '--exclude-module=numpy.tests',
    '--exclude-module=pandas.tests',
    '--exclude-module=unittest',
    '--exclude-module=shiboken6',
    '--exclude-module=matplotlib.backends.backend_qtagg',
    '--exclude-module=matplotlib.backends.backend_qt5agg',
    '--exclude-module=matplotlib.backends.backend_qt',
    '--exclude-module=matplotlib.backends.backend_qt5',
    '--exclude-module=matplotlib.backends.backend_wx',
    '--exclude-module=matplotlib.backends.backend_gtk3',
    '--exclude-module=matplotlib.backends.backend_gtk4'
]

# 禁用 UPX。由于 UPX 压缩后的 PyInstaller 可执行文件极易被 Windows Defender 等杀毒软件误杀/隔离，这里默认禁用
cmd.append('--noupx')

if os.path.exists('icon.ico'):
    cmd.append('--icon=icon.ico')
    cmd.append('--add-data=icon.ico;.')

PyInstaller.__main__.run(cmd) 