@echo off
REM ============================================================
REM  kb one-click start: FOREGROUND debug console.
REM  Usage    : double-click this bat, or run from cmd
REM  Behavior : kb serve logs directly to this window.
REM             Press Ctrl+C to stop. Window stays open after exit.
REM  Optional env: set KB_LLM_MODE / KB_LLM_MODEL before running.
REM ============================================================
setlocal
cd /d "%~dp0..\.."

REM --- locate venv: prefer .venv, fallback venv ---
set "PY="
if exist ".venv\Scripts\python.exe" set "PY=%CD%\.venv\Scripts\python.exe"
if not defined PY if exist "venv\Scripts\python.exe" set "PY=%CD%\venv\Scripts\python.exe"
if not defined PY (
    echo [ERROR] Virtualenv not found: .venv or venv.
    echo   Create it first:
    echo     python -m venv .venv
    echo     .\.venv\Scripts\Activate.ps1
    echo     pip install -r requirements.txt
    echo     pip install -e .
    pause
    exit /b 1
)

REM --- default HF mirror (if not set externally) ---
if "%HF_ENDPOINT%"=="" set "HF_ENDPOINT=https://hf-mirror.com"

echo [INFO] Virtualenv  : %PY%
echo [INFO] HF_ENDPOINT : %HF_ENDPOINT%
echo [INFO] Serving     : http://127.0.0.1:8000
echo [INFO] Press Ctrl+C to stop.
echo.

"%PY%" -m kb serve
set "RC=%ERRORLEVEL%"

echo.
echo [WARN] kb serve exited with code %RC%.
pause
endlocal