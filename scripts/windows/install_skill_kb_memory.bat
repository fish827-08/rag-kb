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
REM   安装自校验：复制后比对源/目标文件大小，源为空或复制失败都会
REM   打印 [FAIL] 而不是假 [OK]（防止 0 字节空文件装完却不生效）；
REM   装完目标文件大小应与仓库内 SKILL.md 一致。
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
set "DST_FILE=%DST_DIR%\SKILL.md"

REM --- 1. 确保目标目录存在（失败即报错，不再带病继续） ---
if not exist "%DST_DIR%" mkdir "%DST_DIR%" >nul 2>&1
if not exist "%DST_DIR%" (
    echo [FAIL] 无法创建目录: %DST_DIR%
    echo       无权限或该用户目录不可写，请以管理员身份重试，或手动复制。
    echo.
    exit /b 0
)

REM --- 2. 源文件非空校验（防复制空内容/损坏文件） ---
for %%F in ("%SRC%") do set "SRC_SIZE=%%~zF"
if not defined SRC_SIZE set "SRC_SIZE=0"
if "%SRC_SIZE%"=="0" (
    echo [FAIL] 源文件为空 ^(0 字节^)，未安装: %SRC%
    echo       请检查仓库内 skills\kb-memory\SKILL.md 是否完整。
    echo.
    exit /b 0
)

REM --- 3. 复制 ---
copy /Y "%SRC%" "%DST_FILE%" >nul 2>&1
if errorlevel 1 (
    echo [FAIL] 复制失败: %DST_FILE%
    echo       请以管理员身份重试，或手动复制:
    echo         copy /Y "%SRC%" "%DST_FILE%"
    echo.
    exit /b 0
)

REM --- 4. 校验：目标存在、非空、大小与源一致（防 0 字节/静默失败） ---
for %%F in ("%DST_FILE%") do set "DST_SIZE=%%~zF"
if not defined DST_SIZE set "DST_SIZE=0"
if not "%SRC_SIZE%"=="%DST_SIZE%" (
    echo [FAIL] 校验失败: 目标 %DST_FILE% 大小 %DST_SIZE% 与源 %SRC_SIZE% 不一致
    echo       安装未生效，请检查磁盘/权限后重试，或手动覆盖该文件。
    echo.
    exit /b 0
)

echo [OK] 已安装并校验通过: %DST_FILE%
exit /b 0

:finish
echo.
echo [DONE] kb-memory skill 安装完成。安装失败的客户端请按上面的 [FAIL] 提示处理；
echo   成功后若客户端未自动识别，请重启对应客户端；兜底方案：
echo     TraeWork/Claude 等：新会话粘贴 docs\AGENT_PROMPT.md 全文即可。
pause
endlocal