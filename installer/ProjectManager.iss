; ══════════════════════════════════════════════════════════════════
; ProjectManager 安装脚本（Inno Setup 6）
;
; 版本号由命令行注入：ISCC /DMyAppVersion=1.3.5 ProjectManager.iss
;   · 正式版：1.3.5          （MyVersionInfo 自动为 1.3.5.0）
;   · 调试版：1.3.5-debug.2  （MyVersionInfo 自动为 1.3.5.2，调试序号在第 4 段）
; 生成入口：scripts/release.py（或双击 release.bat）
;
; ── 正式版 / 调试版：两套可同时安装，互不覆盖 ────────────────
;   release.py 按构建类型注入下面几项（缺省值 = 正式版）：
;     /DMyAppId={GUID}         两套用不同 AppId → 系统视为两个独立程序，
;                              可同时安装、各有各的卸载项
;     /DMyAppTitle=...         显示名（开始菜单 / 快捷方式 / 卸载列表）
;     /DMyAppDir=...           安装目录名 → {autopf}\<MyAppDir>，目录也分开
;     /DDistDir=..\dist-debug  构建产物目录
;   安装包名 = ProjectManager_Setup_<完整版本标识>.exe，不再叠 _Debug 尾缀：
;     ProjectManager_Setup_1.3.5.exe
;     ProjectManager_Setup_1.3.5-debug.2.exe
;   版本标识里的 -debug.N 已经能区分两种包，再加后缀是冗余。
;   用户数据同样各自独立（app/paths.py 依 resources\variant.txt 判定）：
;     正式版 %LOCALAPPDATA%\ProjectManager\data
;     调试版 %LOCALAPPDATA%\ProjectManager Debug\data
;   两套之间搬数据走程序内 文件 → 导出全部数据 / 导入数据。
;
; ── 数据安全（"覆盖安装数据不会丢"的实现方式）──────────────
;   · 程序文件装到 {autopf}\<MyAppDir>（Program Files）
;   · 用户数据在 %LOCALAPPDATA%\<对应目录名>\data\
;       projects.db / settings.ini / reports\ 都由程序自己管理
;   · 本安装脚本【只写 {app}】，从不包含、从不删除用户数据目录
;   · 同版本覆盖安装 = 同 AppId 覆盖旧程序文件（ignoreversion）
;   · 安装/升级前自动关闭正在运行的 ProjectManager.exe（CloseApplications）
; ══════════════════════════════════════════════════════════════════

#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

; 4 段纯数字版本（Windows 版本资源 / 安装包属性只接受纯数字）。
; 语义化版本 → 数字版的映射（见 version.py:numeric_version）：
;     正式版 1.3.5        → 1.3.5.0
;     调试版 1.3.5-debug.2 → 1.3.5.2     （调试序号放第 4 段，构建号位）
; 由 release.py 的 /DMyVersionInfo=... 注入；缺省值仅用于手工单独编译 iss。
#ifndef MyVersionInfo
  #define MyVersionInfo "1.0.0.0"
#endif

; 构建产物目录（PyInstaller --distpath 的输出位置）。缺省 ..\dist；
; 当 dist 被正在运行的程序占用时，release.py 可用 --distpath 出到别的目录并覆盖此项。
; 调试版（release.py --debug）时由 release.py 传入 ..\dist-debug。
#ifndef DistDir
  #define DistDir "..\dist"
#endif

; ── 变体相关（调试版由 release.py 注入，缺省 = 正式版）──
; 正式版 AppId；调试版换成另一个 GUID（见 scripts/release.py DEBUG_APP_ID）
#ifndef MyAppId
  #define MyAppId "{8F3A6C51-2D74-4B9E-A1C8-7E5B90D42F63}"
#endif
#ifndef MyAppTitle
  #define MyAppTitle "ProjectManager"
#endif
#ifndef MyAppDir
  #define MyAppDir "ProjectManager"
#endif

; PyInstaller 产物目录名与 exe 名两个版本一致（都在 version.py 里）
#define MyAppName "ProjectManager"
#define MyAppExeName "ProjectManager.exe"
#define MyAppPublisher "SensorTools"

[Setup]
AppId={{#MyAppId}
AppName={#MyAppTitle}
; AppVersion / AppVerName / OutputBaseFilename 用完整版本标识（可含 -debug.N）；
; VersionInfoVersion 只接受 4 段纯数字，故单独用 MyVersionInfo
AppVersion={#MyAppVersion}
AppVerName={#MyAppTitle} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
VersionInfoVersion={#MyVersionInfo}
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppTitle} Setup
DefaultDirName={autopf}\{#MyAppDir}
DefaultGroupName={#MyAppTitle}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
OutputDir=..\release
OutputBaseFilename=ProjectManager_Setup_{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes
RestartApplications=no
SetupIconFile=..\resources\app.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppTitle}

[Languages]
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加图标："; Flags: unchecked

[Files]
; 程序文件（PyInstaller onedir 产物整体拷贝）
; ignoreversion：升级时无条件覆盖旧程序文件，避免"同名不同版"跳过导致的混合版本
Source: "{#DistDir}\{#MyAppName}\*"; DestDir: "{app}"; \
  Flags: ignoreversion recursesubdirs createallsubdirs

; ⚠ 此处绝不要添加用户数据目录（%LOCALAPPDATA%\...）的任何条目

[Icons]
Name: "{autodesktop}\{#MyAppTitle}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon
Name: "{group}\{#MyAppTitle}"; Filename: "{app}\{#MyAppExeName}"

[Run]
; 安装/升级完成后启动新版（静默安装时 postinstall 同样生效）
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppTitle}"; \
  Flags: nowait postinstall skipifsilent
