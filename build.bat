@echo off

REM Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found. Install Python 3.10+ from https://python.org
    pause
    exit /b 1
)

REM Install dependencies
echo [1/4] Installing dependencies...
pip install -r requirements.txt
pip install pyinstaller
if errorlevel 1 (
    echo [ERROR] Failed to install dependencies.
    pause
    exit /b 1
)
echo.

REM Clean previous builds
echo [2/4] Cleaning previous builds...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist stalzone optimizer.spec del StalZoneOptimizer.spec
echo.

REM Build with PyInstaller
echo [3/4] Building executable (this may take 2-5 minutes)...
pyinstaller --noconfirm --windowed --icon=icon.ico ^
    --name "stalzone optimizer" ^
    --add-data "icon.ico;." ^
    --collect-all customtkinter ^
    --hidden-import psutil ^
    --hidden-import PIL ^
    main.py

if errorlevel 1 (
    echo [ERROR] Build failed.
    pause
    exit /b 1
)
echo.

REM Done
echo [4/4] Build complete!

pause
