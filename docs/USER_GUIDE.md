# 用户使用手册 — rag-kb

> 面向人类用户的使用指南。AI 助手请改读 [AGENTS.md](../AGENTS.md) + [PROJECT.md](../PROJECT.md)。
>
> 版本：v1（2026-08-24）｜ 随版本迭代更新

## 目录

1. [总览](#1-总览)
2. [kb 服务：安装与启动](#2-kb-服务安装与启动)
3. [kb 服务：日常使用](#3-kb-服务日常使用)
4. [orchestra：多 Agent 协作](#4-orchestra多-agent-协作)
5. [配置参考](#5-配置参考)
6. [常见问题与故障排查](#6-常见问题与故障排查)

---

## 1. 总览

本仓库两个子系统，一句话说清：

- **kb**：一个跑在你电脑上的"AI 记忆库"服务。你的 AI 助手（TraeWork / Claude Code / Cursor）挂载它之后，就有了跨会话的长期记忆——写入的记忆、入库的文档，下次对话随时可查。
- **agent-orchestra**：让多个 AI 助手协作开发的"任务板"。一个 AI 当协调者拆活，其他 AI 当干活的 worker，通过 kb 上的任务卡交接，你只负责发指令和验收。

**日常使用最小集**：kb 服务保持运行 → AI 客户端挂载 MCP → 对话即可。

## 2. kb 服务：安装与启动

### 2.1 安装（一次性）

```powershell
cd rag-kb
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2.2 启动服务

```powershell
python -m kb serve
```

- 默认监听 `http://127.0.0.1:8000`，启动时会加载本地嵌入模型（首次约 10 秒）
- 验证：浏览器或另开终端访问 `http://127.0.0.1:8000/api/v1/healthz`，返回 `{"status": "ok", ...}` 即正常
- **保持这个终端窗口开着**（或让它跑在后台）；关掉窗口 = 服务停止

### 2.3 停止与重启

- 停止：服务终端 `Ctrl+C`
- 重启后注意：已挂载 MCP 的 AI 客户端可能显示断连，在客户端 MCP 面板点刷新/重连，或新开会话

## 3. kb 服务：日常使用

### 3.1 AI 客户端挂载（推荐用法）

MCP 端点：`http://127.0.0.1:8000/mcp/`

| 客户端 | 挂载方法 |
|---|---|
| **TraeWork（桌面版）** | 设置 → MCP → 运行环境选**本地** → 创建/手动配置，粘贴下方 JSON |
| **Claude Code** | 本仓库已内置 `.mcp.json`，在仓库目录启动 `claude` 即自动挂载 |
| **Cursor** | `~/.cursor/mcp.json` 或项目 `.cursor/mcp.json` 加入下方 JSON |
| **其他 MCP 客户端** | 同下，Streamable HTTP 类型，URL 填 MCP 端点 |

```json
{
  "mcpServers": {
    "kb": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp/"
    }
  }
}
```

挂载成功后 AI 获得 8 个工具：`write_memory`（写记忆）、`search_memory`（混合检索）、
`read_memory` / `update_memory` / `delete_memory`、`add_document`（文档入库）、
`add_webpage`（网页入库）、`ask_kb`（RAG 问答）。

**使用示例**（在 AI 对话里直接说）：

```
把"用户偏好深色主题、主力语言 Python"记到记忆库
帮我搜一下之前存过的项目决策
把 D:\docs\设计规范.pdf 入库
问一下知识库：这个项目的检索方案是什么？
```

> 注意：TraeWork 需在**智能体（Agent）模式**下对话才会调用 MCP 工具；
> 网页版任务跑在云端连不到本机 127.0.0.1，须用桌面版 + 本地环境。

### 3.2 终端直用（不经 AI）

```powershell
# 写入记忆（支持 --tags 逗号分隔标签、--source 来源、namespace 命名空间）
python -m kb add "用户偏好深色主题" --tags "偏好,UI"

# 混合检索（--mode 可选 hybrid/vector/keyword）
python -m kb search "界面偏好" --top-k 5

# 运行信息（记录数、设备等）
python -m kb info
```

### 3.3 REST API（自建 Agent / 脚本集成）

完整端点表见 [README](../README.md#rest-端点速查)。常用示例：

```powershell
# 写入
curl -X POST http://127.0.0.1:8000/api/v1/memories -H "Content-Type: application/json" -d '{"content": "用户偏好深色主题", "tags": ["偏好"]}'

# 检索
curl -X POST http://127.0.0.1:8000/api/v1/search -H "Content-Type: application/json" -d '{"query": "界面偏好", "top_k": 5}'

# RAG 问答（需配置 LLM，见第 5 节）
curl -X POST http://127.0.0.1:8000/api/v1/ask -H "Content-Type: application/json" -d '{"question": "用户喜欢什么主题？"}'
```

#### 3.3.1 日志查看（N18，不开终端看日志）

两个只读端点，查看服务运行日志（含每次请求的 request.start/request.end 事件）：

```powershell
# 最近 20 条日志（limit 默认 100，上限 1000）
curl "http://127.0.0.1:8000/api/v1/logs?limit=20"

# 只看 request 事件（event 按消息子串匹配）
curl "http://127.0.0.1:8000/api/v1/logs?limit=20&event=request"

# 按级别过滤（DEBUG/INFO/WARNING/ERROR/CRITICAL，可缩写 warn/err/fatal）
curl "http://127.0.0.1:8000/api/v1/logs?limit=50&level=WARNING"

# 日志事件统计（按 level 与 logger 两维度）
curl "http://127.0.0.1:8000/api/v1/logs/events"
```

返回 `items`（按文件序、每条含 time/level/logger/message/line）与 `total`、`truncated`；`/logs/events` 返回 `by_level` / `by_logger` 统计。

### 3.4 知识入库

| 方式 | 操作 |
|---|---|
| 对 AI 说 | "把 D:\docs\xxx.pdf 入库" / "把这个网页存进知识库：<URL>" |
| REST | `POST /api/v1/documents`（multipart 文件或 JSON 路径）；`POST /api/v1/ingest/web`（URL） |
| 目录监听 | 设 `KB_WATCH_DIR`（默认 `data`）后，目录内新增文件自动入库、删除自动清理 |

支持格式：txt / md / pdf / docx。

## 4. orchestra：多 Agent 协作

### 4.1 它解决什么问题

不同 AI 会话之间互相不知道对方在干什么。orchestra 用 kb 当**共享任务板**：
任务卡片就是 kb 里的记录，所有 AI 都能看到同一块板——协调者拆卡、worker 领卡干活、
干完回写、协调者核验。**每个 AI 只在被唤醒时干活，平时挂载零消耗。**

### 4.2 三种角色

| 角色 | 谁 | 干什么 |
|---|---|---|
| **你** | 人类 | 提需求、开 worker 任务、最终验收 |
| **协调者** | 一个 AI 任务（如本会话） | 把需求拆成任务卡、分派给 worker、核验结果、提交代码 |
| **worker** | 其他 AI 任务（模型任选） | 领卡 → 执行 → 回写结果 → 停止，**单卡单轮** |

### 4.3 标准流程（五步）

**第 1 步：向协调者提需求**

在协调者任务里直接说，例如："给 orchestra 加一个 list-pending 子命令"。
协调者会拆卡（每张卡含标题/目标/输入/约束/验收标准五字段）并分派 assignee。

**第 2 步：开 worker 任务**

新开一个 TraeWork 任务（同一项目下，模型任选——这正是要验证的"任意模型当 worker"），
粘贴协调者给你的引导语，或自行运行：

```powershell
venv\Scripts\python.exe orchestra\board.py new-worker worker-1
```

把命令输出的引导语原样发给那个新任务。worker 会自动：查卡 → 认领 → 干活 → 回写 → 停止。

**第 3 步：观察进度**

在协调者任务（或任意终端）运行：

```powershell
venv\Scripts\python.exe orchestra\board.py status          # 看板：一行一卡
venv\Scripts\python.exe orchestra\board.py show TASK-0001  # 看某张卡的详情与结果
venv\Scripts\python.exe orchestra\board.py list-pending    # 只看待办
```

**第 4 步：多卡接力**

worker 单卡单轮纪律：干完一张卡就停。在 worker 任务里发"**继续**"，它领下一张。

**第 5 步：回协调者验收**

worker 全部完成后，回协调者任务说"**核验**"。协调者会独立检查（代码 diff、跑全量
测试、真服务复验），通过则 verify 流转（pending → claimed → done → verified）并统一
提交推送；不通过会 reject 退回重做。

### 4.4 多 worker 并行

现在就支持：

1. 协调者拆卡时按 assignee 分配（worker-1 改 A 文件、worker-2 写 B 文档、worker-3 跑测试）
2. 你开 N 个任务，各自粘贴对应 worker 的 `new-worker` 引导语
3. 各 worker 并行领自己的卡，互不干扰

**已知限制（MVP 阶段，诚实版）**：

- 唤醒靠你手动发消息（无自动调度）
- 无依赖图——卡片的先后顺序靠协调者拆卡时控制
- **并行卡不要改同一个文件**（拆卡时协调者会避开）
- 多 worker 并行尚未真机实测（单 worker 已验证通过），首次并行建议先小规模试

### 4.5 协作纪律（协议红线，worker 与协调者共同遵守）

- 单卡单轮：一次唤醒只做一张卡，做完停止
- 不轮询、不闲聊：挂载状态零消耗，只在被唤醒时行动
- 卡片字段有字符上限，结果写不下的落文件、卡里写路径
- worker 不直接 git commit/推送——统一由协调者核验后提交
- 测试纪律：先写验收测试（红）→ 实现（绿）→ 全量回归

## 5. 配置参考

复制 `.env.example` 为 `.env` 后按需填写（`.env` 不入库，密钥只放本机）：

| 场景 | 配置 |
|---|---|
| 只要记忆/检索（无问答） | 零配置，开箱即用 |
| 本地 RAG 问答 | 装 Ollama 并 `ollama pull` 模型（默认找 `qwen3:4b`）；设 `KB_LLM_MODE=local` |
| 云端问答降级 | `.env` 填 `KB_DEEPSEEK_API_KEY`；`KB_LLM_MODE=auto`（默认，本地优先） |
| 隐私隔离 | `KB_SENSITIVE_NAMESPACES=私人笔记` 等逗号分隔，命中强制本地回答不出网 |
| 性能调优 | `KB_DEVICE=cuda/cpu`；`KB_CHUNK_SIZE/KB_CHUNK_OVERLAP` 切分参数 |
| 局域网/多 Agent 访问鉴权 | `KB_API_KEY=<≥32随机字符>` 启用 Bearer/X-API-Key 鉴权（见 5.1） |

完整键名见 [`.env.example`](../.env.example)。

### 5.1 API Key 鉴权（N19）

本地回环默认零摩擦（`KB_API_KEY` 为空 = 不鉴权）。当需要把 kb 暴露到局域网、手机挂 MCP、或多 Agent 并行访问时，建议启用鉴权：

1. `.env` 填 `KB_API_KEY=<随机字符串>`（建议 ≥32 字符，如 `openssl rand -hex 16`）
2. 重启 `python -m kb serve`；启动日志会记"鉴权已启用"（不回显 key）
3. 所有客户端请求需携带 key（二选一）：
   - `Authorization: Bearer <key>`（推荐，MCP 客户端 headers 配置）
   - `X-API-Key: <key>`（脚本/ curl 便捷）
4. 唯一白名单：`GET /api/v1/healthz`（存活探针，协调者/监控无 key 可探活）
5. 缺失或错误 key 均返回 `401 {"error":"UNAUTHORIZED","message":"missing or invalid api key"}`（不区分，防探测）

空 key 时所有既有行为完全不变（v1.x 兼容）；key 比较用 `hmac.compare_digest` 防时序攻击。

### 5.2 客户端适配（N20：启用鉴权后各客户端如何带 key）

| 客户端 | 配法 |
|---|---|
| **orchestra 命令行**（`orchestra/board.py` 等） | 零改动自动适配：客户端从环境变量 `KB_API_KEY` 读取（其次仓库根 `.env`），非空自动加 `X-API-Key` 头，空则不鉴权；收到 401 会提示“检查 KB_API_KEY” |
| **MCP 客户端**（Claude Code / Cursor / TraeWork） | 连接配置加 `headers`（真实 key 不写进提交入库的 `.mcp.json`，本机另存或用环境变量/本地覆盖）： |
| **REST 脚本 / curl** | 加头 `X-API-Key: <key>` 或 `Authorization: Bearer <key>`（二选一，Bearer 优先） |
| **看板 `/dashboard`** | 启用 key 后看板前端需配置 key 才能加载数据（手动填入） |

MCP 配置带 key 示例（本机自用，**勿提交入库**）：

```json
{
  "mcpServers": {
    "kb": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp/",
      "headers": { "Authorization": "Bearer <你的key>" }
    }
  }
}
```

仓库内 `.mcp.json` 模板保持无 key（JSON 不支持注释，说明落本手册）；健康探针 `GET /api/v1/healthz` 永远无需 key。

## 6. 常见问题与故障排查

**Q：healthz 访问不通？**
服务没起来。检查：终端是否还开着、端口是否被占（`netstat -ano | findstr 8000`）、
换端口 `KB_API_PORT=8001 python -m kb serve`。

**Q：AI 说连不上 MCP / 工具列表为空？**
① 确认 kb serve 在跑；② 客户端 MCP 面板点刷新/重连（服务重启后旧连接会失效）；
③ TraeWork 必须桌面版 + 本地运行环境；④ 确认在智能体（Agent）模式对话。

**Q：Ollama 报错 / /ask 返回 503？**
① 确认 Ollama 已从**开始菜单/托盘**正常启动（不要从 AI 沙箱终端拉起，会数据库读写
失败）；② `ollama list` 确认模型名与配置一致（默认 `qwen3:4b`），不一致改 `.env` 的
`KB_LLM_MODEL` 或 `ollama cp` 改名；③ 不配 LLM 也不影响记忆存取与检索。

**Q：PowerShell 里 curl/Invoke-RestMethod 中文乱码？**
已知问题（JSON 响应缺 charset，v1.0.2 候选修复）：用 `curl.exe` 代替 `curl` 别名，
或用 Python 客户端，或在 PowerShell 7 中 `$OutputEncoding = [Text.Encoding]::UTF8`。

**Q：断网能用吗？**
能。模型与数据全部落本地，记忆写入/检索/本地问答断网完整可用（这是验收标准之一）。
仅云端降级（DeepSeek）需要网络。

**Q：数据存在哪？怎么备份？**
全部在 `kb_data/` 目录（ChromaDB + 运行时状态）。备份 = 复制该目录；恢复 = 覆盖回去。

**Q：想把记忆库清空重来？**
停止服务后删除 `kb_data/chroma` 目录再重启（不可逆，先备份）。

**Q：worker 领了卡不动/卡在 claimed？**
在 worker 任务里发"继续"唤醒它；若确认该任务已废，让协调者在看板上处理退卡。

---

**手册反馈**：使用中发现描述与实际不符，直接告诉任意一个 AI 任务"更新 USER_GUIDE 第 X 节"。
