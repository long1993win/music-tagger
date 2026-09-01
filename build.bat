@echo off
title MusicTagger Build Script
echo ================================
echo   MusicTagger - Build Windows EXE
echo ================================
echo.

setlocal
cd /d "%~dp0"

REM ---- locate Python (try "py" launcher first, fallback "python") ----
set PY=python
py -3 --version >nul 2>&1 && set PY=py -3
%PY% --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Python not found.
    echo         Install Python 3.10+ from https://python.org
    echo         IMPORTANT: check "Add python.exe to PATH" during install.
    pause
    exit /b 1
)

echo [1/3] Installing dependencies...
%PY% -m pip install --upgrade pip -q
%PY% -m pip install -r requirements.txt -q
%PY% -m pip install pyinstaller -q

echo [2/3] Building EXE (about 1-2 minutes)...
%PY% -m PyInstaller --noconfirm --onefile --windowed --name MusicTagger --collect-all mutagen app.py
if errorlevel 1 (
    echo.
    echo [ERROR] Build failed. See messages above.
    pause
    exit /b 1
)

echo [3/3] Done!
echo.
echo     EXE file: dist\MusicTagger.exe
echo     Double-click to run. No Python needed.
echo.
pause
