@echo off
REM ============================================================
REM  kb one-click start: serve in BACKGROUND with NO window.
REM  Usage    : double-click this bat, or run from cmd
REM  Logs     : logs\kb_serve.log / logs\kb_serve.log.err
REM  PID file : kb.pid  (used by stop_kb.bat)
REM  Stop     : run stop_kb.bat
REM
REM  How it works:
REM   - Uses pythonw.exe (no console window).
REM   - Paths (may contain CJK/spaces) are passed to PowerShell via
REM     environment variables (SET), so there is NO nested quoting -
REM     the root cause of the old VBS/Start-Job failures.
REM ============================================================
setlocal
cd /d "%~dp0"

REM --- locate venv: prefer .venv, fallback venv ---
set "PYW="
if exist ".venv\Scripts\pythonw.exe" set "PYW=%CD%\.venv\Scripts\pythonw.exe"
if not defined PYW if exist "venv\Scripts\pythonw.exe" set "PYW=%CD%\venv\Scripts\pythonw.exe"
if not defined PYW (
    echo [ERROR] Virtualenv not found: .venv or venv.
    echo   Create it first:
    echo     python -m venv .venv
    echo     .\.venv\Scripts\Activate.ps1
    echo     pip install -r requirements.txt
    echo     pip install -e .
    pause
    exit /b 1
)

REM --- logs dir ---
if not exist "logs" mkdir "logs"

REM --- default HF mirror (only for this process tree) ---
if "%HF_ENDPOINT%"=="" set "HF_ENDPOINT=https://hf-mirror.com"

set "KB_LOG=%CD%\logs\kb_serve.log"
set "KB_PID=%CD%\kb.pid"
set "KB_DIR=%CD%"

echo [INFO] Starting kb serve in background (no window) ...
echo [INFO] Python: %PYW%

REM --- launch via PowerShell Start-Process, native strings, zero quoting hell ---
powershell -NoProfile -ExecutionPolicy Bypass -Command "$so=$env:KB_LOG; $se=$env:KB_LOG+'.err'; try{$p=Start-Process -FilePath $env:PYW -ArgumentList '-m','kb','serve' -WorkingDirectory $env:KB_DIR -RedirectStandardOutput $so -RedirectStandardError $se -WindowStyle Hidden -PassThru -ErrorAction Stop}catch{Write-Host ('[ERROR] '+$_.Exception.Message); exit 1}; Start-Sleep -Milliseconds 2500; if($p.HasExited){Write-Host ('[ERROR] kb serve exited code '+$p.ExitCode); if(Test-Path $se){Get-Content $se}; exit 1}; $p.Id | Out-File -FilePath $env:KB_PID -Encoding ascii; Write-Host ('[OK] kb serve started, PID='+$p.Id); Write-Host ('Log: '+$env:KB_LOG)"
if errorlevel 1 (
    echo [ERROR] Failed to start kb serve. See messages above.
    pause
    exit /b 1
)

echo [DONE] kb is running in background. Stop it with stop_kb.bat
endlocal