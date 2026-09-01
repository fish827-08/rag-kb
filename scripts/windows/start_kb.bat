@echo off
REM ============================================================
REM  kb 一键启动：后台无窗口常驻，以健康检查确认"真的起来了"。
REM  用法：双击，或从仓库根目录执行 scripts\windows\start_kb.bat
REM  日志   : logs\kb_serve.log / logs\kb_serve.log.err
REM  PID    : kb.pid 由服务进程自身写入/清理（本脚本不再代写）
REM  停止   : scripts\windows\stop_kb.bat
REM
REM  流程（任务 #2 修复：杜绝"进程活着但不监听"的挂死空壳）：
REM   1. 启动前端口预检：端口已被占用 → 提示并退出，杜绝双实例竞跑
REM   2. pythonw 后台拉起 kb serve（无控制台，绑定失败由服务侧预检兜住）
REM   3. 轮询 /api/v1/healthz 最多 30 秒，返回 200 才报 [OK]；
REM      超时则打印错误日志尾部并以非零码退出
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
REM --- 服务端口：与 .env 的 KB_API_PORT 保持一致（默认 8000） ---
set "KB_PORT=8000"
if defined KB_API_PORT set "KB_PORT=%KB_API_PORT%"
set "HEALTH_URL=http://127.0.0.1:%KB_PORT%/api/v1/healthz"

REM --- 启动前端口预检：已有实例在监听则拒绝再启，杜绝双跑 ---
netstat -ano | findstr LISTENING | findstr ":%KB_PORT% " >nul
if not errorlevel 1 (
    echo [ERROR] 端口 %KB_PORT% 已被占用，可能已有实例在运行。
    echo         请先执行 scripts\windows\stop_kb.bat，或确认端口占用后重试。
    pause
    exit /b 1
)

echo [INFO] Starting kb serve in background (no window) ...
echo [INFO] Python: %PYW%

REM --- launch via PowerShell Start-Process, native strings, zero quoting hell ---
REM     kb.pid 不再由本脚本写入：服务进程存活到真正启动后才自行写入（见 kb/cli.py）
powershell -NoProfile -ExecutionPolicy Bypass -Command "$so=$env:KB_LOG; $se=$env:KB_LOG+'.err'; try{$p=Start-Process -FilePath $env:PYW -ArgumentList '-m','kb','serve' -WorkingDirectory $env:KB_DIR -RedirectStandardOutput $so -RedirectStandardError $se -WindowStyle Hidden -PassThru -ErrorAction Stop}catch{Write-Host ('[ERROR] '+$_.Exception.Message); exit 1}; Start-Sleep -Milliseconds 2500; if($p.HasExited){Write-Host ('[ERROR] kb serve exited code '+$p.ExitCode); if(Test-Path $se){Get-Content -Tail 10 $se}; exit 1}; Write-Host ('[INFO] process started, PID='+$p.Id)"
if errorlevel 1 (
    echo [ERROR] Failed to start kb serve. See messages above.
    pause
    exit /b 1
)

REM --- 健康轮询：最多 30 秒（15 次 x 2 秒），/api/v1/healthz 返回 200 才算成功 ---
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
echo [ERROR] kb serve 未在 30 秒内通过健康检查（%HEALTH_URL%）。
echo [ERROR] 错误日志尾部（logs\kb_serve.log.err）：
powershell -NoProfile -Command "if(Test-Path ($env:KB_LOG+'.err')){Get-Content -Tail 10 ($env:KB_LOG+'.err')}"
pause
exit /b 1
