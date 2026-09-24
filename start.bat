@echo off
setlocal
cd /d "%~dp0"
set PYTHONUTF8=1
if not exist ".venv\Scripts\python.exe" (
    py -3.11 -m venv .venv 2>nul
    if errorlevel 1 python -m venv .venv
    if errorlevel 1 (
        echo Python 3.11 x64 is required.
        pause
        exit /b 1
    )
    ".venv\Scripts\python.exe" -m pip install --upgrade pip
    if errorlevel 1 goto :failed
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 goto :failed
)
".venv\Scripts\python.exe" app.py
if errorlevel 1 goto :failed
exit /b 0
:failed
echo Startup failed. Review the error above.
pause
exit /b 1
