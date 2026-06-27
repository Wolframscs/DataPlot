# -*- coding: utf-8 -*-
"""
DataPlot 一键打包脚本：
1. 运行 DataPlot_gui_exe.py 生成绿色版可执行文件文件夹 (dist/DataPlot/)
2. 自动寻找系统及常见安装路径下的 Inno Setup 编译器 (ISCC.exe)
3. 自动编译 setup.iss 生成 Windows 安装程序包 (installer_output/DataPlot_Setup.exe)
"""

import os
import sys
import subprocess

def run_cmd(cmd, desc):
    print(f"\n==================================================")
    print(f">>> 开始执行: {desc}")
    print(f"命令: {' '.join(cmd) if isinstance(cmd, list) else cmd}")
    print(f"==================================================")
    try:
        # 运行并保留控制台交互流
        subprocess.run(cmd, check=True, shell=True)
        print(f"\n>>> {desc} 执行成功！\n")
        return True
    except subprocess.CalledProcessError as e:
        print(f"\n!!! {desc} 执行失败: {str(e)} !!!\n")
        return False

def main():
    # 1. 运行 PyInstaller 打包
    python_exe = sys.executable
    build_exe_cmd = [python_exe, "DataPlot_gui_exe.py"]
    
    if not run_cmd(build_exe_cmd, "使用 PyInstaller 打包绿色版可执行文件"):
        print("!!! 打包 EXE 失败，终止后续安装包构建流程。")
        sys.exit(1)
        
    # 2. 自动定位 Inno Setup 编译器 (ISCC.exe)
    iscc_path = None
    standard_paths = [
        "ISCC",  # 若已加入系统环境变量 PATH
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        r"C:\Program Files\Inno Setup 5\ISCC.exe",
    ]
    
    # 尝试在各位置进行测试
    for path in standard_paths:
        try:
            # 运行最基础的帮助输出测试其是否可用
            subprocess.run([path, "/?"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, shell=True)
            iscc_path = path
            break
        except Exception:
            continue
            
    # 3. 编译制作安装包
    if iscc_path:
        print(f"==> 成功找到 Inno Setup 编译器: {iscc_path}")
        # 注意路径带有空格时两边加上双引号
        build_setup_cmd = [f'"{iscc_path}"', "setup.iss"]
        if run_cmd(build_setup_cmd, "编译 Inno Setup 安装包"):
            print("\n==================================================")
            print(" 🎉 一键生成可执行文件及安装包成功！")
            print(f" 1. 绿色版程序目录: {os.path.abspath('dist/DataPlot/')}")
            print(f" 2. 安装向导安装包: {os.path.abspath('installer_output/DataPlot_Setup.exe')}")
            print("==================================================")
        else:
            print("!!! 编译安装向导失败。")
            sys.exit(1)
    else:
        print("\n==================================================")
        print(" 提示：EXE 可执行文件已成功打包至 dist/DataPlot/ 中！")
        print(" 警告：未在系统环境变量或常见路径下检测到 Inno Setup (ISCC.exe)。")
        print("       如果您需要自动生成 Setup.exe 一键安装程序：")
        print("       请下载并安装 Inno Setup 6 (https://jrsoftware.org/isdl.php)")
        print("       安装后重新双击运行此脚本即可自动检测并打包。")
        print("==================================================")

if __name__ == "__main__":
    main()
