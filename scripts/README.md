# scripts — 一键启停脚本（Start/Stop Scripts）

启动/停止 kb 服务的一键脚本，按操作系统分类。
One-click scripts to start/stop the kb service, grouped by OS.

```
scripts/
├── windows/                 # Windows (.bat)
│   ├── start_kb.bat             无窗口后台启动（background, no window）
│   ├── start_kb_console.bat     前台调试窗口（foreground debug console）
│   └── stop_kb.bat              停止服务（3 重保险）
└── linux/                   # Linux / macOS (.sh) — 预留下，待补
```

## Windows 用法（Usage）

所有脚本在仓库任意位置双击/运行均可（自动定位仓库根）。
Works from anywhere in the repo (auto-resolves the repo root).

| 操作 Action | 命令 Command |
|---|---|
| 后台启动 Background start | `scripts\windows\start_kb.bat` |
| 调试窗启动 Debug console | `scripts\windows\start_kb_console.bat` |
| 停止 Stop | `scripts\windows\stop_kb.bat` |

- 日志 Logs：`logs\kb_serve.log`（后台模式）
- PID 文件 PID file：`kb.pid`（由 start 写、stop 读）
- 虚拟环境 Venv：优先 `.venv`，其次 `venv`

## 环境要求（Requirements）

- Python 3.10+ 虚拟环境（`.venv` 或 `venv`），依赖已装（`pip install -e .`）
- Windows 版依赖 `pythonw.exe`（后台模式）
- 嵌入模型默认 BGE-M3（约 2GB，需提前下载缓存）