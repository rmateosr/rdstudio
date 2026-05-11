@echo off
REM Build a Windows .exe bundle for Reaction-Diffusion Studio.
REM
REM Run this from a normal cmd.exe / PowerShell on Windows (NOT from WSL).
REM Double-clicking also works.
REM
REM Result: dist\Reaction-Diffusion Studio\Reaction-Diffusion Studio.exe
REM         plus a zip in dist\Reaction-Diffusion-Studio-windows.zip
REM
REM Requires a Python 3.10+ install reachable via the py launcher (`py -3.11`).

setlocal
cd /d "%~dp0\.."

REM --- 1. venv -----------------------------------------------------------
if not exist .venv (
    echo Creating virtual environment in .venv ...
    py -3.11 -m venv .venv
    if errorlevel 1 (
        py -3.12 -m venv .venv
    )
    if errorlevel 1 (
        py -m venv .venv
    )
    if errorlevel 1 (
        echo Could not create venv. Install Python 3.11 from python.org.
        exit /b 1
    )
)
call .venv\Scripts\activate.bat

REM --- 2. deps -----------------------------------------------------------
python -m pip install --upgrade pip
pip install -e .[build]
if errorlevel 1 (
    echo Dependency install failed.
    exit /b 1
)

REM --- 3. build ----------------------------------------------------------
pyinstaller --noconfirm --clean rdstudio.spec
if errorlevel 1 (
    echo PyInstaller build failed.
    exit /b 1
)

REM --- 4. zip ------------------------------------------------------------
REM PowerShell ships with Windows; use Compress-Archive for a portable zip.
powershell -NoProfile -Command "Compress-Archive -Path 'dist\Reaction-Diffusion Studio\*' -DestinationPath 'dist\Reaction-Diffusion-Studio-windows.zip' -Force"

echo.
echo ============================================================
echo  Build complete.
echo  App folder: dist\Reaction-Diffusion Studio\
echo  Zip:        dist\Reaction-Diffusion-Studio-windows.zip
echo ============================================================
endlocal
