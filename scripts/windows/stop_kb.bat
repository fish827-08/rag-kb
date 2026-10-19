@echo off
REM ============================================================
REM  kb one-click stop.
REM  Kills in 3 passes:
REM    1. PID recorded in kb.pid, only if it is still a python process
REM    2. any process listening on the resolved port (.env aware)
REM    3. python/pythonw from THIS repo's venv whose command line
REM       contains "kb serve"
REM ============================================================
setlocal
cd /d "%~dp0..\.."

echo [INFO] Stopping kb serve ...

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

REM --- pass 1: PID file (validate it still belongs to python) ---
if exist "kb.pid" set /p OLD=<kb.pid
if defined OLD (
    tasklist /FI "PID eq %OLD%" 2>nul | findstr /I "python" >nul
    if not errorlevel 1 (
        taskkill /F /PID %OLD% >nul 2>&1 && (echo [OK] killed PID %OLD%) || (echo [WARN] PID %OLD% already gone)
    ) else (
        echo [WARN] PID %OLD% is not a python process; left it alone.
    )
)
if exist "kb.pid" del "kb.pid" >nul 2>&1

REM --- pass 2: resolved port ---
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":%KB_PORT% " ^| findstr LISTENING') do (
    taskkill /F /PID %%P >nul 2>&1 && (echo [OK] killed port-%KB_PORT% PID %%P) || (echo [WARN] port PID %%P already gone)
)

REM --- pass 3: command-line fallback restricted to this repo's venv ---
set "KB_REPO=%CD%"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$repo=$env:KB_REPO; Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*kb serve*' -and ($_.ExecutablePath -like ($repo + '\venv\*') -or $_.ExecutablePath -like ($repo + '\.venv\*')) } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('[OK] killed PID ' + $_.ProcessId) }"

echo.
echo [DONE] kb stopped. Verify: netstat -ano ^| findstr :%KB_PORT%
pause
endlocal
