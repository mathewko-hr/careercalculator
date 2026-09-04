@echo off
cd /d "%~dp0"
echo Starting Streamlit application...
echo Press Ctrl+C in this window to stop the application.
python -m streamlit run app.py
if errorlevel 1 (
    echo.
    echo Application failed to start.
    echo Run install_once.bat first.
    pause
)
