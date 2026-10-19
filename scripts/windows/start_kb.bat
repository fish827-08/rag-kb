@echo off
REM ============================================================
REM  kb one-click background start (no console window).
REM  Usage: double-click, or run scripts\windows\start_kb.bat
REM  Logs : logs\kb_serve.log / logs\kb_serve.log.err
REM  PID  : kb.pid (written by the service)
REM  Stop : scripts\windows\stop_kb.bat
REM
REM  Safety:
REM   1. Port pre-check: refuse to start when the port is taken.
REM   2. pythonw has no console; non-interactive GPU prompt is skipped
REM      by kb itself (defaults to CPU), so first run cannot hang.
REM   3. Poll /api/v1/healthz for up to 30 seconds and report failure.
REM ============================================================
setlocal
cd /d "%~dp0..\.."

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
set "KB_DIR=%CD%"

REM --- resolve port: environment KB_API_PORT > .env KB_API_PORT > 8000 ---
set "KB_PORT=8000"
set "ENV_KB_PORT="
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"KB_API_PORT=" ".env" 2^>nul`) do set "ENV_KB_PORT=%%B"
)
if defined ENV_KB_PORT set "ENV_KB_PORT=%ENV_KB_PORT:"=%"
if defined ENV_KB_PORT set "ENV_KB_PORT=%ENV_KB_PORT: =%"
if defined ENV_KB_PORT set "KB_PORT=%ENV_KB_PORT%"
if defined KB_API_PORT set "KB_PORT=%KB_API_PORT%"
set "KB_PORT=%KB_PORT:"=%"
set "KB_PORT=%KB_PORT: =%"
if not defined KB_PORT set "KB_PORT=8000"
set "HEALTH_URL=http://127.0.0.1:%KB_PORT%/api/v1/healthz"

REM --- port pre-check: refuse to start a second instance ---
netstat -ano | findstr LISTENING | findstr ":%KB_PORT% " >nul
if not errorlevel 1 (
    echo [ERROR] Port %KB_PORT% is already in use.
    echo         Run scripts\windows\stop_kb.bat, or change KB_API_PORT in .env.
    pause
    exit /b 1
)

echo [INFO] Starting kb serve in background (no window) ...
echo [INFO] Python: %PYW%
echo [INFO] Port  : %KB_PORT%

REM --- launch via PowerShell Start-Process, native strings, zero quoting hell ---
powershell -NoProfile -ExecutionPolicy Bypass -Command "$so=$env:KB_LOG; $se=$env:KB_LOG+'.err'; try{$p=Start-Process -FilePath $env:PYW -ArgumentList '-m','kb','serve' -WorkingDirectory $env:KB_DIR -RedirectStandardOutput $so -RedirectStandardError $se -WindowStyle Hidden -PassThru -ErrorAction Stop}catch{Write-Host ('[ERROR] '+$_.Exception.Message); exit 1}; Start-Sleep -Milliseconds 2500; if($p.HasExited){Write-Host ('[ERROR] kb serve exited code '+$p.ExitCode); if(Test-Path $se){Get-Content -Tail 10 $se}; exit 1}; Write-Host ('[INFO] process started, PID='+$p.Id)"
if errorlevel 1 (
    echo [ERROR] Failed to start kb serve. See messages above.
    pause
    exit /b 1
)

REM --- poll health for up to 30 seconds (15 x 2s) ---
echo [INFO] Waiting for health check: %HEALTH_URL%
set /a TRIES=0
:wait_loop
set "CODE="
for /f "delims=" %%C in ('curl.exe -s -o nul -w "%%{http_code}" --max-time 2 "%HEALTH_URL%" 2^>nul') do set "CODE=%%C"
if "%CODE%"=="200" goto healthy
set /a TRIES+=1
if %TRIES% GEQ 15 goto timeout
ping -n 3 127.0.0.1 >nul
goto wait_loop

:healthy
echo [OK] kb serve started (healthz 200). Log: %KB_LOG%
echo [DONE] kb is running in background. Stop it with stop_kb.bat
endlocal
exit /b 0

:timeout
echo [ERROR] kb serve did not become healthy within 30 seconds (%HEALTH_URL%).
echo [ERROR] Check the log tail: logs\kb_serve.log.err
powershell -NoProfile -Command "if(Test-Path ($env:KB_LOG+'.err')){Get-Content -Tail 10 ($env:KB_LOG+'.err')}"
pause
exit /b 1
