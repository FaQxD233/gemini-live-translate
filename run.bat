@echo off
setlocal
cd /d "%~dp0"

REM Quick launcher for development mode (uses the venv created by build.bat).

if not exist ".venv\Scripts\python.exe" (
    echo No .venv found. Creating one and installing deps...
    where python >nul 2>&1
    if errorlevel 1 (
        echo [ERROR] Python not found in PATH. Install Python 3.11+ first.
        pause
        exit /b 1
    )
    python -m venv .venv
    call ".venv\Scripts\activate.bat"
    pip install -r requirements.txt
) else (
    call ".venv\Scripts\activate.bat"
)

echo Starting gemini-live-translate...
python main.py

if errorlevel 1 (
    echo.
    echo [ERROR] gemini-live-translate exited with an error.
    pause
)
