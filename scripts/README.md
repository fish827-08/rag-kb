# scripts — 一键启停脚本（Start/Stop Scripts）

启动/停止 kb 服务的一键脚本，按操作系统分类。
One-click scripts to start/stop the kb service, grouped by OS.

```
scripts/
├── windows/                 # Windows (.bat)
│   ├── start_kb.bat             无窗口后台启动（background, no window）
│   ├── start_kb_console.bat     前台调试窗口（foreground debug console）
│   ├── stop_kb.bat              停止服务（3 重保险）
│   └── install_skill_kb_memory.bat  kb-memory skill 一键装到用户级全局目录
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
| 安装 kb-memory skill 到全局 | `scripts\windows\install_skill_kb_memory.bat` |

- 日志 Logs：`logs\kb_serve.log`（后台模式）
- PID 文件 PID file：`kb.pid`（由 start 写、stop 读）
- 虚拟环境 Venv：优先 `.venv`，其次 `venv`

## kb-memory skill（客户端无关的接入规约）

仓库内 `skills/kb-memory/SKILL.md` 是 kb 接入规约（MCP 工具表、`agent_id` 身份强制、
存取审计、HTTP 兜底端点），采用 **Anthropic Skills 开格式**，与具体客户端无关，
供任意 AI 客户端（TraeWork / Claude Code / Cursor / 自建 Agent）作为可触发 skill 使用。
**仓库内 `skills/` 是唯一事实来源**；要让某客户端的任意项目都能自动识别，
把它装到该客户端的用户级 skills 目录。两种方式任选：

### 方式 A：一键脚本（推荐，可重复执行=更新）

```powershell
# 一键安装（默认 all：装到本机已检测到的 TraeWork / Claude Code / Cursor）
scripts\windows\install_skill_kb_memory.bat
# 或只装某个客户端
scripts\windows\install_skill_kb_memory.bat trae    # %USERPROFILE%\.trae-cn\skills\
scripts\windows\install_skill_kb_memory.bat claude  # %USERPROFILE%\.claude\skills\
scripts\windows\install_skill_kb_memory.bat cursor  # %USERPROFILE%\.cursor\skills\
```

> 该脚本是**独立的一次性安装工具**，不绑定启动脚本（`start_kb*.bat` / `stop_kb.bat`
> 均不含 skill 安装逻辑），也不会随服务启动自动执行。
> `all` 模式是容错的：只安装到「用户目录已存在」的客户端——没装 TraeWork 就不会创建
> `.trae-cn\skills` 空目录，打印 `[SKIP]` 后跳过，其余客户端不受影响。

### 方式 B：手动添加（直接复制，不需要脚本）

把仓库内的 `skills/kb-memory` **整个目录**复制到目标客户端的用户级 skills 目录：

```powershell
# 以 TraeWork 为例
copy /Y skills\kb-memory\SKILL.md %USERPROFILE%\.trae-cn\skills\kb-memory\SKILL.md
# 或一次性复制整个目录（可在资源管理器里直接复制粘贴）
xcopy /E /I /Y skills\kb-memory %USERPROFILE%\.trae-cn\skills\kb-memory
```

Linux / macOS：

```bash
mkdir -p ~/.claude/skills/kb-memory
cp skills/kb-memory/SKILL.md ~/.claude/skills/kb-memory/
```

> 每个 skill 是「**目录名 = skill 名**」的结构，目录名必须叫 `kb-memory`，
> 里面放 `SKILL.md`；缺目录或改名会导致客户端识别不到。

### 更新 / 更换（怎么换版本或去掉）

- **更新**：skill 内容更新后，重新执行方式 A，或再次手动复制覆盖 `SKILL.md` 即可（幂等覆盖，不会残留旧文件）。
- **换客户端**：装到哪个目录=哪个客户端生效；不想给某客户端用，删掉对应目录即可。
- **卸载 / 停用**：直接删除该客户端的用户级目录，如
  `%USERPROFILE%\.trae-cn\skills\kb-memory\`（删目录即停用，对服务无任何影响）。

### 使用说明（装好之后怎么生效）

> 各客户端对用户级 skills 目录的加载机制不同（TraeWork 自动发现；Claude Code 高版本支持；
> Cursor 逐步跟进）。装完后：

1. **重启对应客户端**（或新开一个会话），让 skill 被重新扫描。
2. 会话中一旦提到读写记忆 / RAG 问答 / 审计查询，客户端会自动触发本 skill 并按其规约行事。
3. 可用 `/skills`（或客户端管理 skills 的命令）确认 `kb-memory` 已被识别。
4. **最终兜底**：任何客户端都能用 `docs/AGENT_PROMPT.md` 的纯文本提示词
   （复制粘贴给 Agent 即接入），不依赖 skill 机制——skill 装不上或未触发就用它。
5. 版本不一致时以**仓库内 `skills/kb-memory/SKILL.md` 为准**（唯一事实来源），
   用户级目录只是它的副本，随时可重新覆盖。

### 常见问题：装了却未生效（0 字节空文件）

- **症状**：目录 `kb-memory\` 存在，但客户端不识别、`SKILL.md` 是 **0 字节**（或比仓库小）。
- **原因**：安装脚本/复制被静默失败（典型：以受限权限运行时 `copy` 没写进去，但目录已创建），
  旧版脚本只打 `[OK]` 不校验，造成"目录在、文件空"的假象。
- **解决**：用**修复后的脚本重跑**（脚本现在会对比源/目标大小，失败打 `[FAIL]`），
  或手动覆盖该文件后确认大小与 `skills\kb-memory\SKILL.md` 一致，再重启客户端。
- **自查**（PowerShell）：`(Get-Item "%USERPROFILE%\.trae-cn\skills\kb-memory\SKILL.md").Length` 应大于 0。

## 环境要求（Requirements）

- Python 3.10+ 虚拟环境（`.venv` 或 `venv`），依赖已装（`pip install -e .`）
- Windows 版依赖 `pythonw.exe`（后台模式）
- 嵌入模型默认 BGE-M3（约 2GB，需提前下载缓存）