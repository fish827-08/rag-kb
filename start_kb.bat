@echo off
REM ============================================================
REM  kb 一键启动（无窗口后台常驻，入口批处理，双击即运行 start_kb.vbs）
REM  日志：logs\kb_serve.log
REM  停止：stop_kb.bat
REM ============================================================
start "" "%~dp0start_kb.vbs"
