@echo off
REM Build a Windows .exe bundle for Reaction-Diffusion Studio.
REM
REM Run this from a normal cmd.exe / PowerShell on Windows (NOT from WSL).
REM From PowerShell:  .\scripts\build_windows.cmd
REM Double-clicking from Explorer also works.
REM
REM Result: dist\Reaction-Diffusion Studio\Reaction-Diffusion Studio.exe
REM         plus a zip in dist\Reaction-Diffusion-Studio-windows.zip
REM
REM Requires a Python 3.10+ install reachable via the py launcher.

setlocal EnableExtensions EnableDelayedExpansion
cd /d "%~dp0\.."

REM --- 1. Pick a Python -------------------------------------------------
REM Prefer newest known-good versions first. The py launcher exits non-zero
REM with "[ERROR] No runtime installed that matches" if a version is missing,
REM so we walk the list explicitly. The bare `py` fallback uses whatever the
REM user has marked as default.
set "PY_CMD="
for %%V in (3.14 3.13 3.12 3.11 3.10) do (
    if not defined PY_CMD (
        py -%%V -c "import sys" >nul 2>&1
        if not errorlevel 1 set "PY_CMD=py -%%V"
    )
)
if not defined PY_CMD (
    py -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY_CMD=py"
)
if not defined PY_CMD (
    echo No usable Python found via the py launcher.
    echo Install Python 3.11+ from https://www.python.org/downloads/ ^(check "Add to PATH"^).
    exit /b 1
)
echo Using Python: !PY_CMD!

REM --- 2. venv ----------------------------------------------------------
if not exist .venv (
    echo Creating virtual environment in .venv ...
    !PY_CMD! -m venv .venv
    if errorlevel 1 (
        echo venv creation failed. Aborting before pip touches your system Python.
        exit /b 1
    )
)
if not exist .venv\Scripts\activate.bat (
    echo .venv exists but Scripts\activate.bat is missing — venv is broken.
    echo Delete the .venv folder and re-run this script.
    exit /b 1
)
call .venv\Scripts\activate.bat
if not defined VIRTUAL_ENV (
    echo venv activation failed — VIRTUAL_ENV not set. Aborting.
    exit /b 1
)
echo Active venv: %VIRTUAL_ENV%

REM --- 3. deps ----------------------------------------------------------
python -m pip install --upgrade pip
python -m pip install -e .[build]
if errorlevel 1 (
    echo Dependency install failed.
    exit /b 1
)

REM --- 4. build ---------------------------------------------------------
REM Invoke via `python -m PyInstaller` so we don't depend on the venv's
REM Scripts folder being on PATH — `python -m` always works.
python -m PyInstaller --noconfirm --clean rdstudio.spec
if errorlevel 1 (
    echo PyInstaller build failed.
    exit /b 1
)

REM --- 5. zip -----------------------------------------------------------
powershell -NoProfile -Command "Compress-Archive -Path 'dist\Reaction-Diffusion Studio\*' -DestinationPath 'dist\Reaction-Diffusion-Studio-windows.zip' -Force"

echo.
echo ============================================================
echo  Build complete.
echo  App folder: dist\Reaction-Diffusion Studio\
echo  Zip:        dist\Reaction-Diffusion-Studio-windows.zip
echo ============================================================
endlocal
