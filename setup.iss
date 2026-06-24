; Inno Setup 脚本 - 用于将 DataPlot (PlotPostProcessing) 打包为 Windows 安装程序
; 使用前请下载并安装 Inno Setup (推荐版本 6.x)

#define MyAppName "DataPlot"
#define MyAppVersion "1.0"
#define MyAppPublisher "Company"
#define MyAppExeName "DataPlot.exe"

[Setup]
; AppId 是此程序的唯一标识符。请勿在其他安装程序中重复使用相同的 AppId。
AppId={{5A8E0F3D-4A2B-4A7F-B22D-C17FDEE88B9C}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DisableProgramGroupPage=yes
; 如果需要管理员权限安装，请更改为 admin。lowest 适合无管理员权限的普通用户安装。
PrivilegesRequired=lowest
OutputDir=installer_output
OutputBaseFilename=DataPlot_Setup
Compression=lzma
SolidCompression=yes
WizardStyle=modern
[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; 主执行文件
Source: "dist\DataPlot\{#MyAppExeName}"; DestDir: "{app}"; Flags: ignoreversion
; 依赖文件夹及全部内容
Source: "dist\DataPlot\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
; 开始菜单快捷方式
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
; 桌面快捷方式
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
; 安装完成后启动程序选项
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
