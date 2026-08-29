@echo off
REM ============================================================
REM  kb-memory skill 一键安装到常用客户端的用户级 skill 目录。
REM
REM   kb-memory 是 kb 记忆服务的接入规约 skill（MCP 工具表 +
REM   agent_id 身份强制 + 存取审计 + HTTP 兜底），SKILL.md 为
REM   Anthropic 开格式，客户端无关。仓库内 skills\kb-memory\ 是
REM   唯一事实来源；本脚本把它复制到各客户端的用户级 skills 目录，
REM   让该客户端的任意项目会话都能识别/触发。
REM
REM   Usage:
REM     scripts\windows\install_skill_kb_memory.bat [all|trae|claude|cursor]
REM       默认 all：装到本机已检测到的客户端
REM       trae   -> %USERPROFILE%\.trae-cn\skills\kb-memory\
REM       claude -> %USERPROFILE%\.claude\skills\kb-memory\
REM       cursor -> %USERPROFILE%\.cursor\skills\kb-memory\
REM
REM   容错：只安装到「用户目录已存在」的客户端（例如没装 TraeWork
REM   就不创建 .trae-cn\skills 空目录，直接跳过并提示），其余客户端
REM   不受影响；本脚本不绑定启动脚本，也不会自动随服务启动执行。
REM
REM   注：各客户端用户级 skill 目录加载机制不同（TraeWork 自动发现；
REM   Claude Code 需 >= 某版本；Cursor 逐步支持），装完若未自动生效，
REM   重启对应客户端即可。最终兜底：直接复制粘贴 AGENT_PROMPT.md。
REM ============================================================
setlocal
cd /d "%~dp0..\.."

set "SRC=%CD%\skills\kb-memory\SKILL.md"
if not exist "%SRC%" (
    echo [ERROR] 源文件不存在: %SRC%
    echo   请确认仓库内 skills\kb-memory\SKILL.md 存在
    pause
    exit /b 1
)

set "TARGET=%~1"
if "%TARGET%"=="" set "TARGET=all"

REM --- 单一目标安装函数（bat 无函数，用标签）---
if /i "%TARGET%"=="all"   goto :install_all
if /i "%TARGET%"=="trae"  goto :install_trae
if /i "%TARGET%"=="claude" goto :install_claude
if /i "%TARGET%"=="cursor" goto :install_cursor
echo [ERROR] 未知目标: %TARGET%  ^(可选 all ^| trae ^| claude ^| cursor^)
pause
exit /b 1

:install_all
call :check_and_install "%USERPROFILE%\.trae-cn" "TraeWork"
call :check_and_install "%USERPROFILE%\.claude" "Claude Code"
call :check_and_install "%USERPROFILE%\.cursor" "Cursor"
goto :finish

:install_trae
call :check_and_install "%USERPROFILE%\.trae-cn" "TraeWork"
goto :finish

:install_claude
call :check_and_install "%USERPROFILE%\.claude" "Claude Code"
goto :finish

:install_cursor
call :check_and_install "%USERPROFILE%\.cursor" "Cursor"
goto :finish

:check_and_install
set "CLIENT_ROOT=%~1"
set "CLIENT_NAME=%~2"
if not exist "%CLIENT_ROOT%" (
    echo [SKIP] 未检测到 %CLIENT_NAME% 用户目录: %CLIENT_ROOT%
    echo         没装该客户端则跳过，不影响其他客户端。
    echo.
    exit /b 0
)
call :do_install "%CLIENT_ROOT%\skills\kb-memory"
exit /b 0

:do_install
set "DST_DIR=%~1"
if not exist "%DST_DIR%" mkdir "%DST_DIR%" >nul 2>&1
copy /Y "%SRC%" "%DST_DIR%\SKILL.md" >nul
if errorlevel 1 (
    echo [WARN] 安装失败（可能无权限）: %DST_DIR%
    echo       请以管理员权限重试，或手动复制:
    echo         copy /Y "%SRC%" "%DST_DIR%\SKILL.md"
) else (
    echo [OK] 已安装: %DST_DIR%\SKILL.md
)
exit /b 0

:finish
echo.
echo [DONE] kb-memory skill 安装完成。若客户端未自动识别，
echo   请重启对应客户端；兜底方案：
echo     TraeWork/Claude 等：新会话粘贴 docs\AGENT_PROMPT.md 全文即可。
pause
endlocal