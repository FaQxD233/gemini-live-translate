@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo   gemini-live-translate build script
echo   (PyInstaller --onefile)
echo ========================================
echo.

REM ---- Check Python ----
where python >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found in PATH.
    echo Install Python 3.11+ from https://www.python.org/downloads/
    echo Make sure to tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

REM ---- Create venv if missing ----
if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    python -m venv .venv
    if errorlevel 1 (
        echo [ERROR] Failed to create virtual environment.
        pause
        exit /b 1
    )
)

echo Activating venv...
call ".venv\Scripts\activate.bat"

REM ---- Install deps + PyInstaller ----
echo.
echo Installing dependencies + PyInstaller...
python -m pip install --upgrade pip >nul
pip install -r requirements.txt
if errorlevel 1 (
    echo [ERROR] Failed to install runtime dependencies.
    pause
    exit /b 1
)
pip install pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install PyInstaller.
    pause
    exit /b 1
)

REM ---- Clean previous build ----
echo.
echo Cleaning previous build output...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist gemini-live-translate.spec del gemini-live-translate.spec

REM ---- Build ----
echo.
echo Building gemini-live-translate.exe (this may take 1-2 minutes)...
pyinstaller --noconsole --onefile --noupx --name gemini-live-translate ^
  --collect-all PySide6 ^
  --collect-all pyaudiowpatch ^
  --collect-all numpy ^
  --collect-all scipy ^
  --hidden-import scipy.special._cdflib ^
  --exclude-module PySide6.QtSql ^
  --exclude-module PySide6.QtQml ^
  --exclude-module PySide6.QtQuick ^
  --exclude-module PySide6.QtQuick3D ^
  --exclude-module PySide6.QtQuickWidgets ^
  --exclude-module PySide6.QtCharts ^
  --exclude-module PySide6.QtDataVisualization ^
  --exclude-module PySide6.QtWebEngineCore ^
  --exclude-module PySide6.QtWebEngineWidgets ^
  --exclude-module PySide6.QtWebChannel ^
  --exclude-module PySide6.QtMultimedia ^
  --exclude-module PySide6.QtPdf ^
  --exclude-module PySide6.QtPdfWidgets ^
  --exclude-module PySide6.QtPrintSupport ^
  --exclude-module PySide6.QtSvg ^
  --exclude-module PySide6.QtSvgWidgets ^
  --exclude-module PySide6.QtTest ^
  --exclude-module PySide6.QtOpenGL ^
  --exclude-module PySide6.QtOpenGLWidgets ^
  --exclude-module PySide6.QtBluetooth ^
  --exclude-module PySide6.QtSerialPort ^
  --exclude-module PySide6.QtSensors ^
  --exclude-module PySide6.QtPositioning ^
  --exclude-module PySide6.QtNfc ^
  --exclude-module PySide6.QtRemoteObjects ^
  --exclude-module PySide6.QtScxml ^
  --exclude-module PySide6.QtStateMachine ^
  --exclude-module PySide6.QtUiTools ^
  --exclude-module PySide6.QtWebSockets ^
  --exclude-module PySide6.QtHttpServer ^
  main.py

if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See output above.
    pause
    exit /b 1
)

REM ---- Verify ----
if exist "dist\gemini-live-translate.exe" (
    echo.
    echo ========================================
    echo   SUCCESS
    echo ========================================
    echo Output: %~dp0dist\gemini-live-translate.exe
    echo.
    echo Copy dist\gemini-live-translate.exe to any machine and
    echo double-click to run. No Python install needed.
    echo Settings are stored in %%APPDATA%%\gemini-live-translate\
) else (
    echo [ERROR] dist\gemini-live-translate.exe not found after build.
)

echo.
pause
