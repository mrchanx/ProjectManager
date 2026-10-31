@echo off
REM ========================================
REM  ProjectManager - Release
REM  选正式/调试 -> 输入版本号（调试版只输序号） -> 生成 exe 安装包
REM  （不涉及 git；产物在 release\）
REM ========================================

cd /d "%~dp0"

echo ============================================================
echo   ProjectManager - 生成安装包（支持覆盖升级）
echo ============================================================
echo.
echo 将依次执行：
echo   1. 选择构建类型：正式版（默认）/ 调试版（带控制台，日志可见）
echo   2. 输入版本号
echo        正式版：MAJOR.MINOR.PATCH，如 1.3.5（直接回车 = 沿用当前版本）
echo        调试版：只输入调试序号，如 1（版本号自动为 1.3.5-debug.1，
echo                始终跟随正式版，出调试包不改动 version.py）
echo   3. PyInstaller onedir 构建
echo   4. Inno Setup 编译安装包
echo   5. 计算 SHA256
echo.
echo 版本命名（语义化版本 SemVer）：
echo   正式版  1.3.5          -^> ProjectManager_Setup_1.3.5.exe
echo   调试版  1.3.5-debug.2  -^> ProjectManager_Setup_1.3.5-debug.2.exe
echo   调试序号缺省 = release\ 里该正式版已出的最大号 + 1。
echo   安装包名 = ProjectManager_Setup_^<完整版本标识^>.exe，
echo   文件名里带 debug 的就是调试版（不再叠 _Debug 后缀）。
echo.
echo 注：构建与编译需要几分钟，请耐心等待。
echo 注：程序文件装到 Program Files，用户数据在
echo     %LOCALAPPDATA%\ProjectManager\data，升级不会丢。
echo 注：调试版与正式版可以同时安装：AppId / 安装目录 / 数据目录各自独立，
echo     互不覆盖，可同时打开；两套之间搬数据用程序内
echo     文件 -^> 导出全部数据 / 导入数据。
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv 不存在，请先双击 run.bat 完成依赖安装。
    pause
    exit /b 1
)

call ".venv\Scripts\python.exe" scripts\release.py %*
if errorlevel 1 (
    echo.
    echo [ERROR] 安装包生成失败，详见上方输出。
    pause
    exit /b 1
)

echo.
echo ============================================================
echo   完成！安装包：release\ProjectManager_Setup_*.exe
echo ============================================================
echo.
pause
