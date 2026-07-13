@echo off
cd /d "%~dp0"

:: Auto-detect Python environment (desktop or laptop)
set PYTHON=

:: Path 1: Desktop (Marvis .real\.bin)
if exist "C:\Users\%USERNAME%\.real\.bin\python-3.12-windows-x64\python.exe" (
    set "PYTHON=C:\Users\%USERNAME%\.real\.bin\python-3.12-windows-x64\python.exe"
)

:: Path 2: Laptop (TRAE CN)
if not defined PYTHON (
    if exist "C:\Users\%USERNAME%\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe" (
        set "PYTHON=C:\Users\%USERNAME%\AppData\Roaming\TRAE SOLO CN\ModularData\ai-agent\vm\tools\python\python.exe"
    )
)

:: Path 3: System Python
if not defined PYTHON (
    if exist "C:\Python312\python.exe" set "PYTHON=C:\Python312\python.exe"
)

:: Fallback: PATH
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
    echo [ERROR] Python not found.
    pause
    exit /b 1
)

echo Python: "%PYTHON%"
"%PYTHON%" -m pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
if errorlevel 1 (
    "%PYTHON%" -m pip install -r requirements.txt
)
echo.
echo Done. Now run 启动.bat
pause
