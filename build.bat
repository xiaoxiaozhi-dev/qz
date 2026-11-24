@echo off
REM ============================================================
REM  Qz Tools - build & verify
REM
REM  WHY THIS FILE IS PURE ASCII:
REM    cmd.exe reads a .bat file byte-by-byte using the console
REM    codepage that was active BEFORE the script started.
REM    The "chcp 65001" line below only takes effect from the
REM    NEXT line onward, so non-ASCII text in THIS file gets
REM    mangled into garbage. ASCII sidesteps the problem
REM    entirely -- no encoding dependency, no risk.
REM
REM  WHY .venv (and not "py -3"):
REM    The project venv was rebuilt on 2026-10-22 with
REM      py -3 -m venv --system-site-packages .venv
REM    so it inherits the system site-packages and therefore sees
REM    BOTH PySide6 and PyInstaller without downloading anything.
REM    Result: ONE interpreter for run / test / build.
REM
REM  Full guide: docs\build-guide.md
REM ============================================================

setlocal
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONDONTWRITEBYTECODE=1"

set "PY=.venv\Scripts\python.exe"
if not exist "%PY%" (
    echo.
    echo ==== SETUP NEEDED ====
    echo   .venv\Scripts\python.exe not found. Create it with:
    echo     py -3 -m venv --system-site-packages .venv
    echo.
    if /i not "%~1"=="--no-pause" pause
    exit /b 1
)

echo.
echo ==== BUILD, VERIFY, AND PACKAGE ====
"%PY%" -B build.py
if errorlevel 1 goto fail

echo.
echo ==== ALL PASSED - ready to ship ====
echo   Send the WHOLE dist\QzTools folder, not just the exe.
echo   Update ZIP and SHA256: dist\releases
echo   Build and verification logs: build\logs
if /i not "%~1"=="--no-pause" pause
exit /b 0

:fail
echo.
echo ==== BUILD FAILED ====
echo   See the error above.
echo   Common problems: docs\build-guide.md
if /i not "%~1"=="--no-pause" pause
exit /b 1
