@echo off
title StudyBuddy 2.0

cd /d "%~dp0"

:: Find Python - try known paths first, then PATH
set PYTHON=

:: Known paths (prefer over PATH to avoid old Anaconda Python)
if exist "C:\Users\%USERNAME%\.real\.bin\python-3.12-windows-x64\python.exe" (
    set "PYTHON=C:\Users\%USERNAME%\.real\.bin\python-3.12-windows-x64\python.exe"
)
if not defined PYTHON (
    if exist "C:\Python312\python.exe" (
        set "PYTHON=C:\Python312\python.exe"
    )
)

:: Fallback to system PATH
if not defined PYTHON (
    for /f "delims=" %%f in ('where python 2^>nul') do (
        if not defined PYTHON set "PYTHON=%%f"
    )
)
if not defined PYTHON (
    for /f "delims=" %%f in ('where python3 2^>nul') do (
        if not defined PYTHON set "PYTHON=%%f"
    )
)

if not defined PYTHON (
    echo [ERROR] Python not found. Please install Python 3.11+
    echo Download: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo Python: %PYTHON%

:: Check and install deps
"%PYTHON%" -c "import PyQt6, fastapi, uvicorn, requests" >nul 2>&1
if errorlevel 1 (
    echo Installing dependencies...
    "%PYTHON%" -m pip install -r "%~dp0requirements.txt" -i https://pypi.tuna.tsinghua.edu.cn/simple --quiet
    if errorlevel 1 (
        echo Mirror failed, retrying with default PyPI...
        "%PYTHON%" -m pip install -r "%~dp0requirements.txt" --quiet
        if errorlevel 1 (
            echo [ERROR] pip install failed
            pause
            exit /b 1
        )
    )
    echo Done.
)

:: Launch
echo Starting StudyBuddy...
"%PYTHON%" "src\main.py"

if errorlevel 1 (
    echo.
    echo [ERROR] App crashed. See logs\app.log for details.
)

pause
