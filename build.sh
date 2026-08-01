#!/bin/bash
set -e

# Check Python
if ! command -v python3 &> /dev/null; then
    echo "[ERROR] Python3 not found. Install Python 3.10+"
    exit 1
fi

# Install dependencies
echo "[1/4] Installing dependencies..."
pip3 install -r requirements.txt
pip3 install pyinstaller
echo

# Clean previous builds
echo "[2/4] Cleaning previous builds..."
rm -rf build dist StalZoneOptimizer.spec
echo

# Build with PyInstaller
echo "[3/4] Building executable (this may take 2-5 minutes)..."
pyinstaller --noconfirm --windowed --icon=icon.ico \
    --name "stalzone optimizer" \
    --add-data "icon.ico;." \
    --collect-all customtkinter \
    --hidden-import psutil \
    --hidden-import PIL \
    main.py
echo

# Done
