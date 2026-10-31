@echo off
REM ========================================
REM  ProjectManager launcher
REM  Creates venv on first run, then starts
REM  the desktop application.
REM  Mirror fallback chain: aliyun -> tsinghua -> pypi.org
REM  (tsinghua is broken with pip>=26 on some networks,
REM   so aliyun comes first; any single dead mirror
REM   can no longer break the launcher.)
REM ========================================

cd /d "%~dp0"

set "PY=%~dp0.venv\Scripts\python.exe"

if not exist "%PY%" (
    echo [1/3] Creating virtual environment...
    python -m venv .venv 2>nul
    if errorlevel 1 py -3 -m venv .venv
    if errorlevel 1 (
        echo ERROR: python not found in PATH. Install Python 3.10+ first.
        pause
        exit /b 1
    )
)

echo [2/3] Checking dependencies...
call :pip_install https://mirrors.aliyun.com/pypi/simple/ && goto deps_ok
call :pip_install https://pypi.tuna.tsinghua.edu.cn/simple && goto deps_ok
call :pip_install https://pypi.org/simple && goto deps_ok
echo ERROR: dependency install failed. Check network connection.
pause
exit /b 1

:deps_ok
echo [3/3] Starting application...
"%PY%" run.py
exit /b 0

:pip_install
"%PY%" -m pip install -r requirements.txt --quiet --disable-pip-version-check -i %~1
exit /b %errorlevel%
