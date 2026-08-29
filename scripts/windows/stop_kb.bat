@echo off
REM ============================================================
REM  kb one-click stop.
REM  Kills in 3 passes:
REM    1. PID recorded in kb.pid by start_kb.bat (fast path)
REM    2. any process listening on port 8000
REM    3. any python/pythonw whose command line contains "kb serve"
REM ============================================================
setlocal
cd /d "%~dp0..\.."

echo [INFO] Stopping kb serve ...

REM --- pass 1: PID file ---
if exist "kb.pid" set /p OLD=<kb.pid
if defined OLD (
    taskkill /F /PID %OLD% >nul 2>&1 && (echo [OK] killed PID %OLD%) || (echo [WARN] PID %OLD% already gone)
)
if exist "kb.pid" del "kb.pid" >nul 2>&1

REM --- pass 2: port 8000 ---
for /f "tokens=5" %%P in ('netstat -ano ^| findstr ":8000 " ^| findstr LISTENING') do (
    taskkill /F /PID %%P >nul 2>&1 && (echo [OK] killed port-8000 PID %%P) || (echo [WARN] port PID %%P already gone)
)

REM --- pass 3: command-line fallback (python / pythonw running "kb serve") ---
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*kb serve*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('[OK] killed PID ' + $_.ProcessId) }"

echo.
echo [DONE] kb stopped. Verify: netstat -ano | findstr :8000
pause
endlocal