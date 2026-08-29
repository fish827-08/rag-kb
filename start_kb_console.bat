@echo off
REM ============================================================
REM  kb 一键启动（控制台可见，日志输出到当前窗口）
REM  用法：双击或命令行运行 start_kb_console.bat
REM
REM  运行前：
REM    1) 仓库根目录的 venv/ 或 .venv/ 已建好（若未建，提示步骤）
REM    2) Windows 本机嵌入模型缓存（BGE-M3 约 2GB，首次写入会从 hf-mirror 自动下）
REM
REM  可选环境变量（在本 bat 顶部或系统环境里设置，以下为默认）：
REM    set HF_ENDPOINT=https://hf-mirror.com    :: 国内下载嵌入模型走镜像（已默认）
REM    set KB_LLM_MODEL=qwen3:1.7b              :: 本地 LLM 模型名
REM    set KB_LLM_MODE=auto                     :: local|auto|cloud
REM ============================================================
setlocal

cd /d "%~dp0"

REM --- 找虚拟环境：优先 .venv，其次 venv；都没有则提示并退出 ---
set VENV=
if exist ".venv\Scripts\python.exe" set "VENV=%~dp0.venv"
if not defined VENV if exist "venv\Scripts\python.exe" set "VENV=%~dp0venv"

if not defined VENV (
    echo [ERROR] 未找到虚拟环境（.venv\ 或 venv\）。请先在仓库根目录执行：
    echo     python -m venv .venv
    echo     .venv\Scripts\Activate.ps1
    echo     pip install -r requirements.txt
    echo     pip install -e .
    echo 然后再运行本脚本。
    echo.
    pause
    exit /b 1
)

REM --- 默认国内 HF 镜像（可在外部覆盖） ---
if "%HF_ENDPOINT%"=="" set "HF_ENDPOINT=https://hf-mirror.com"

echo [INFO] 虚拟环境: %VENV%
echo [INFO] HF_ENDPOINT=%HF_ENDPOINT%
echo [INFO] 启动 kb serve (监听 http://127.0.0.1:8000) ...
echo [INFO] Ctrl+C 停止服务
echo.

"%VENV%\Scripts\python.exe" -m kb serve

REM --- 非正常退出时保持窗口，便于看错误 ---
echo.
echo [WARN] kb serve 已退出，代码 %ERRORLEVEL%
pause
endlocal
