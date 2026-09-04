@echo off
cd /d "%~dp0"
echo [1/2] Installing required Python packages...
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo Installation failed.
    echo Check that Python is installed and available in PATH.
    pause
    exit /b 1
)
echo.
echo [2/2] Installation completed.
echo Next time, run run_app.bat.
pause
