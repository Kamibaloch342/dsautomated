@echo off
rem ============================================================================
rem  Start_Windows.bat — double-click entry point for the Automations suite.
rem  First run: bootstraps everything (venv, libraries, Chrome/Tailscale checks)
rem  Later runs: starts the launcher GUI directly.
rem ============================================================================
setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

set "VENV_PY=%~dp0..\.venv\Scripts\python.exe"
set "VENV_PYW=%~dp0..\.venv\Scripts\pythonw.exe"

rem ── already bootstrapped? → straight to the launcher ────────────────────────
if exist "%VENV_PY%" (
    echo Starting launcher...
    if exist "%VENV_PYW%" (
        start "" "%VENV_PYW%" "%~dp0gui\launcher.py"
    ) else (
        start "" "%VENV_PY%" "%~dp0gui\launcher.py"
    )
    exit /b 0
)

rem ── first run: find a system Python to run the bootstrap ────────────────────
echo First-time setup — this installs the libraries and checks Chrome/Tailscale.
where py >nul 2>nul
if %errorlevel%==0 (
    py -3 "%~dp0scripts\windows_bootstrap.py"
    goto done
)
where python >nul 2>nul
if %errorlevel%==0 (
    python "%~dp0scripts\windows_bootstrap.py"
    goto done
)

echo.
echo   Python is not installed (or not on PATH).
echo   1. Install Python 3.10+ from https://www.python.org/downloads/
echo   2. IMPORTANT: tick "Add python.exe to PATH" in the installer.
echo   3. Double-click this file again.
echo.
start https://www.python.org/downloads/

:done
echo.
pause
