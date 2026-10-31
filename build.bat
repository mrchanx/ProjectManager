@echo off
setlocal

cd /d "%~dp0"

echo ============================================================
echo   ProjectManager - Build (PyInstaller onedir)
echo ============================================================
echo.
echo 可选参数：build.bat --debug  出调试版（带控制台，报错日志可见）
echo   调试版输出 dist-debug\ProjectManager\，正式版输出 dist\ProjectManager\
echo   调试版版本标识 = ^<正式版^>-debug.^<序号^>，序号缺省取 release\ 里未出过的
echo   下一个号，也可用 build.bat --debug --debug-number 3 指定
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv 不存在，请先双击 run.bat 完成依赖安装。
    pause
    exit /b 1
)

echo [1/2] Building...
call ".venv\Scripts\python.exe" scripts\build.py %*
if errorlevel 1 (
    echo [ERROR] 构建失败，详见上方输出。
    pause
    exit /b 1
)

echo [2/2] Done.
echo.
pause
