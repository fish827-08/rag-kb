@echo off
REM ============================================================
REM  停止 kb 后台服务（按 8000 端口找进程，或按 python -m kb serve 找）
REM ============================================================
setlocal

echo [INFO] 查找监听 8000 端口的进程 ...

REM --- 优先用端口找（可靠，跨 venv） ---
for /f "tokens=5" %%P in ('netstat -ano 2^>nul ^| findstr ":8000 " ^| findstr LISTENING') do (
    echo [INFO] 找到 PID=%%P 监听 8000，终止中 ...
    taskkill /F /PID %%P >nul 2>&1
    if !errorlevel! equ 0 (echo [OK] 已终止 PID=%%P) else (echo [WARN] 终止 PID=%%P 失败，可能需管理员或已退出)
)

REM --- 兜底：按命令行找（8000 没绑上但进程还活着时用） ---
echo [INFO] 扫描 kb serve 进程 ...
for /f "delims=" %%L in ('wmic process where "commandline like '%%kb serve%%' and not commandline like '%%wmic%%'" get processid ^| findstr /r "[0-9]"') do (
    for /f %%P in ("%%L") do (
        echo [INFO] 找到 kb serve 进程 PID=%%P，终止中 ...
        taskkill /F /PID %%P >nul 2>&1
    )
)

echo.
echo [DONE] 若仍有残留，打开任务管理器看 pythonw.exe 或手动检查 8000 端口：netstat -ano ^| findstr :8000
pause
endlocal
