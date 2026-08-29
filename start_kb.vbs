' ============================================================
'  kb 一键启动（无控制台窗口，后台常驻，pythonw.exe 无黑框）
'  用法：双击 start_kb.vbs 或 start_kb.bat（后者会调本 vbs）
'
'  日志：logs/kb_serve.log（相对仓库根；uvicorn 输出 + 我们的 structured log 都在这里）
'  停止：运行 stop_kb.bat 或任务管理器杀 pythonw.exe（8000 端口）
' ============================================================
Option Explicit

Dim fso, shell, here, venvPath, pywPath, logDir, logPath, launchCmd

Set fso  = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

here = fso.GetParentFolderName(WScript.ScriptFullName) & "\"

' --- 找虚拟环境：优先 .venv，其次 venv；都没有弹 MsgBox 退出 ---
If fso.FileExists(here & ".venv\Scripts\pythonw.exe") Then
    venvPath = here & ".venv"
ElseIf fso.FileExists(here & "venv\Scripts\pythonw.exe") Then
    venvPath = here & "venv"
Else
    MsgBox "未找到虚拟环境（.venv\Scripts\pythonw.exe 或 venv\Scripts\pythonw.exe）。" & vbCrLf & _
           "请先在仓库根目录执行：" & vbCrLf & _
           "  python -m venv .venv" & vbCrLf & _
           "  .venv\Scripts\Activate.ps1" & vbCrLf & _
           "  pip install -r requirements.txt" & vbCrLf & _
           "  pip install -e .", _
           48, "kb 启动失败"
    WScript.Quit 1
End If

pywPath = """" & venvPath & "\Scripts\pythonw.exe"""
logDir  = here & "logs"
logPath = """" & logDir & "\kb_serve.log"""

If Not fso.FolderExists(logDir) Then fso.CreateFolder(logDir)

' --- 默认国内 HF 镜像；kb serve 启动；stdout/stderr 都进 log ---
launchCmd = "cmd /c set HF_ENDPOINT=https://hf-mirror.com && cd /d """ & here & """ && " & _
            pywPath & " -m kb serve > " & logPath & " 2>&1"

' windowstyle=0 隐藏窗口；bWaitOnReturn=False 不阻塞
shell.Run launchCmd, 0, False
