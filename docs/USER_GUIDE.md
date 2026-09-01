# 用户使用手册 — rag-kb

> 面向人类用户的使用指南。AI 助手请改读 [AGENTS.md](../AGENTS.md) + [PROJECT.md](../PROJECT.md)。
>
> 版本：v1（2026-08-24）｜ 随版本迭代更新
>
> English: [USER_GUIDE_EN.md](USER_GUIDE_EN.md)

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

### 3.5 记忆治理（A3：语义去重 / 衰减 / 新鲜度）

记忆只增不减会越来越噪。A3 记忆治理提供三个可选机制，**全部默认关闭（零行为变化）**，按需在 `.env` 开启：

| 机制 | 开关 | 作用 |
|---|---|---|
| 语义去重（N22a） | `KB_DEDUP_ENABLED=true` | 写入前向量检索 top1，余弦相似度 ≥ `KB_DEDUP_THRESHOLD`（默认 0.92）即判重，**409 拦截不写入** |
| 访问频率衰减（N21b） | `KB_DECAY_ENABLED=true` | hybrid/vector 检索排序：长期未访问的记忆降权，高频访问的加权 |
| 新鲜度权重（N22b） | `KB_FRESHNESS_ENABLED=true` | hybrid/vector 检索排序：近期更新的内容最多加权 30% |

#### 3.5.1 语义去重：409 拦截（不写入）

开启后 `POST /api/v1/memories` 在写入前做去重检查，命中时返回 **409，新内容不落库**：

```json
HTTP 409 Conflict
Content-Type: application/json; charset=utf-8

{
  "error": "DUPLICATE",
  "message": "语义重复，已存在相似记录",
  "duplicate_of": "<已有记录 id>",
  "similarity": 0.9537
}
```

**调用方处理方式**：
- 从 409 响应取 `duplicate_of`（已有记录 id）与 `similarity`（相似度，保留 4 位小数）
- 想更新旧记录 → `PATCH /api/v1/memories/{duplicate_of}`；纯重复 → 直接跳过即可（幂等语义）
- 嵌入/检索异常时降级为不拦截（正常写入，服务日志记 WARNING），不因检查故障阻塞写入
- `KB_DEDUP_ENABLED` 关闭时写入行为与以前完全一致；阈值用 `KB_DEDUP_THRESHOLD` 调（调低更严、调高更宽）

#### 3.5.2 新鲜度权重与衰减（检索排序）

两者正交相乘、独立开关，**只影响 `hybrid`/`vector` 模式排序**（keyword/BM25 路径不受影响）；都关闭时排序完全不变：

- **新鲜度权重**（`KB_FRESHNESS_ENABLED`）：看 `updated_at`（内容新旧）——刚更新的记录最高加权 1+α 倍（默认 α=0.3，即最多 1.3 倍），加权随时间指数衰减（半衰期≈14 天，β=0.05）。调参：`KB_FRESHNESS_BETA` / `KB_FRESHNESS_ALPHA`
- **访问频率衰减**（`KB_DECAY_ENABLED`）：看 `last_accessed`/`access_count`（访问冷热）——长期未访问降权（半衰期≈35 天，λ=0.02），高频访问加权（γ=0.3，access_count=10 时约 2.0 倍）。调参：`KB_DECAY_LAMBDA` / `KB_DECAY_GAMMA`

#### 3.5.2a 遗忘机制怎么验证（不等半衰期也能确认真实有效）

衰减默认半衰期≈35 天，**不可能真等 35 天验收**。两条互补路径即可完成最接近真实的验证（2026-08-30 v2）：

**① 时钟注入逻辑验证（秒级，证明"机理正确"）**

衰减计算的时间基准可注入：`kb.models.decay_factor(..., now=<datetime>)` 直接传入"未来某天"，
测试/复现脚本可拨到第 35 天验证公式行为。示例（Python 或 pytest）：

```python
from datetime import datetime, timedelta
from kb.models import decay_factor
created = "2026-01-01T00:00:00"
later = datetime.fromisoformat(created) + timedelta(days=35)
f = decay_factor("", created, 0, now=later)   # λ=0.02 → exp(-0.02*35)≈0.497
assert abs(f - 0.4966) < 0.01                  # 35 天半衰期：分数≈0.5
```

这条验证衰减公式、热度加权的数值正确性——秒级完成，仓库 `tests/test_agent_isolation.py::test_decay时钟注入` 已覆盖。

**② 加速真实运行验证（2~3 天，证明"端到端真实有效"）**

不牺牲正确性，临时缩半衰期跑真实服务：
- 方案 A（半衰期≈1.4 天）：`.env` 设 `KB_DECAY_ENABLED=true`、`KB_DECAY_LAMBDA=0.5`，
  跑 2~3 天真实写入+检索，观察：importance/检索排序随未访问天数下降、高频访问加权生效、
  `kb forget --stale` 能捞出预期记录、`governance-audit.log` 出现 `decay_applied` 事件。
- 方案 B（当天可见）：`KB_DECAY_LAMBDA=3`（半衰期≈几小时）+ 多写几条不同访问频率的记忆，
  数小时内即可在检索排序与治理统计中看到衰减效果。

验证指标：① importance 随时间下降；② access_count 加权生效；③ 遗忘候选（stale）与审计事件正确；
④ 恢复默认 λ 后行为回到基线。两条路径组合 = 逻辑正确性（秒级）+ 真实运行证据（短周期），
无需等 35 天实机。

#### 3.5.3 治理端点（只读）

```powershell
# 治理统计：总记录数 / 平均访问次数 / 超 90 天未命中数
curl.exe http://127.0.0.1:8000/api/v1/governance/stats
Invoke-RestMethod http://127.0.0.1:8000/api/v1/governance/stats

# 治理配置：当前衰减 + 新鲜度的开关与参数
curl.exe http://127.0.0.1:8000/api/v1/governance/config
Invoke-RestMethod http://127.0.0.1:8000/api/v1/governance/config
```

返回示例（字段结构）：

- `/governance/stats`：`{"total_count": 279, "avg_access_count": 1.25, "stale_90d_count": 3}`
- `/governance/config`：`{"decay_enabled": false, "decay_lambda": 0.02, "decay_gamma": 0.3, "freshness_enabled": false, "freshness_beta": 0.05, "freshness_alpha": 0.3}`

> CLI 维护命令（批量清理等，N23a）开发中，后续版本提供。

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

### 4.6 挂载常驻模式（B5，新增）

让 worker/designer 完成任务后**挂载监听**（空闲 15 分钟自动停机、有新卡自动继续），子协调者**持续挂载**，父协调者**按需唤醒**：

```powershell
# 挂载（worker/designer 默认 TTL=900 即 15 分钟；子协调者 --ttl 0 常驻）
venv\Scripts\python.exe orchestra\board.py mount worker-1 --role worker --ttl 900
venv\Scripts\python.exe orchestra\board.py mount designer-1 --role designer --ttl 900
venv\Scripts\python.exe orchestra\board.py mount subcoordinator --role subcoordinator --ttl 0

# 心跳 / 退出 / 看板 / 失联检测
venv\Scripts\python.exe orchestra\board.py heartbeat worker-1
venv\Scripts\python.exe orchestra\board.py unmount worker-1 --reason 空闲超时
venv\Scripts\python.exe orchestra\board.py mount-status            # 一行一 agent
venv\Scripts\python.exe orchestra\board.py mount-check --threshold 300

# 挂载循环内由 worker/designer 自行调用
venv\Scripts\python.exe orchestra\board.py mount-claim worker-1 --topic kb-A3
venv\Scripts\python.exe orchestra\board.py mount-idle worker-1
```

- **挂载循环**：worker/designer 挂载后自循环——查卡 → 有卡领卡干活回写 → `mount-idle`；无卡 `heartbeat`+sleep 60s；累计空闲满 TTL `unmount` 停机。
- **连续相关≤5**：同"主题"卡连续做到第 5 张，写 summary 后 `unmount` 上下文重置（防上下文膨胀）。
- 父协调者不挂载：用户唤醒后建"拆卡卡"（assignee=subcoordinator）派活，见 `orchestra/parent-coordinator-prompt.md`。
- 完整协议见 `orchestra/protocol.md` §15 与 `orchestra/docs/superpowers/specs/2026-08-28-orchestra-mount-design.md`。

## 5. 配置参考

复制 `.env.example` 为 `.env` 后按需填写（`.env` 不入库，密钥只放本机）：

| 场景 | 配置 |
|---|---|
| 只要记忆/检索（无问答） | 零配置，开箱即用（`KB_LLM_MODE=off` 默认，不加载不调用 LLM） |
| 本地 RAG 问答 | 装 Ollama 并 `ollama pull` 一个适合自己电脑的模型（按显存自选，无预设）；`.env` 设 `KB_LLM_MODE=local` + `KB_LLM_MODEL=<ollama list 实际名>` |
| 云端问答降级 | `.env` 填 `KB_LLM_API_KEY` / `KB_LLM_BASE_URL` / `KB_LLM_CLOUD_MODEL`（**任意 OpenAI 兼容服务商**，通用三键，不绑定 DeepSeek）；`KB_LLM_MODE=auto`（本地优先，云端降级） |
| 隐私隔离 | `KB_SENSITIVE_NAMESPACES=私人笔记` 等逗号分隔，命中强制本地回答不出网 |
| 性能调优 | `KB_DEVICE=cuda/cpu`；`KB_CHUNK_SIZE/KB_CHUNK_OVERLAP` 切分参数 |
| 局域网/多 Agent 访问鉴权 | `KB_API_KEY=<≥32随机字符>` 启用 Bearer/X-API-Key 鉴权（见 5.1） |
| 记忆治理 | `KB_DEDUP_ENABLED`（去重）/ `KB_DECAY_ENABLED`（衰减）/ `KB_FRESHNESS_ENABLED`（新鲜度），均默认关（见 3.5） |
| 记忆全共享 | v3（2026-08-31）：所有记忆/知识全共享、不按 (client, project) 隔离读写；client/project 仅审计归类（见 5.3） |
| Agent 存取审计 | `KB_ACCESS_AUDIT_ENABLED`（默认开）：每次存取写 `logs/agent-audit/<客户端>__<项目>.log` JSON 行；查询：REST `GET /api/v1/audit?client=<客户端>[&project=<项目>]` 或 `kb audit --client <客户端>`（见 5.3） |

完整键名见 [`.env.example`](../.env.example)。

### Agent 接入（客户端无关）

- **推荐**：用 `skills/kb-memory/SKILL.md`（Anthropic 开格式 skill，客户端无关），
  支持 skill 的客户端会自动触发；多客户端一键安装见 [scripts/README.md](../scripts/README.md)。
- **兜底**：`docs/AGENT_PROMPT.md` 纯文本提示词，复制粘贴给任意 Agent 即接入，
  不依赖 skill 机制。两处内容一致，同步更新。

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

### 5.3 记忆全共享与存取审计（v3：client + project 仅审计归类，2026-08-31）

**记忆全共享**（本地单用户定位，跨 agent/任务共享同一份记忆）：

- **所有记忆（memory）与知识（doc/web chunk）全共享**：任何客户端、任何任务、任何 AI 均可
  检索 / 读取 / 更新 / 删除全部记录——不再按 (client, project) 隔离读写，跨 agent、跨任务
  能读到同一份用户偏好与决策，不因来源不同而丢失记忆
- **v2 隔离已取消**（2026-08-31）：实测 (client, project) 双键隔离在本地单用户场景不可行——
  框架识别 client 再准，项目/任务归属仍靠连接/目录承载，读不到隔离键时记忆整体不可见；
  鉴权边界仍由 `KB_API_KEY` 承担，未来如需多租户再引入正式租户模型
- **client / project 仍可传**，仅用于**审计归类与元数据**（标注这条记录从哪个客户端/项目写入），
  不影响任何读写可见性；格式校验保留（非法值 REST 422 / MCP `INVALID_ARGUMENT`）
- 旧数据（v2 时期按项目隔离存储的记录）无需迁移，v3 起全部可检索

**存取审计**（谁在哪个客户端/项目下存了什么/读了什么）：

- **按 (client, project) 分文件**：每次 write/search/read/update/delete/ask/ingest 记一条 JSON 到
  `logs/agent-audit/<客户端>__<项目>.log`（project 为空 → `<客户端>__default.log`，
  如 `TraeWork__kb.log`、`Claude Code__default.log`）——每个 (client, project) 独立文件，
  不再混写；换项目只需改连接声明的 project
- 行内 JSON：`{"timestamp","action","type","record_id","namespace","content 前50摘要","query 前50摘要","hits"}`
  （client/project 由文件名承载，行内不重复记录；查询侧自动解析补回）
- 敏感红线：内容/查询只记前 50 字符摘要，全文不落日志
- 用户查询入口：
  ```powershell
  curl -X GET "http://127.0.0.1:8000/api/v1/audit?client=TraeWork&project=kb&action=write&days=7&limit=100"
  kb audit --client TraeWork --project kb --action write --days 7 --limit 100
  ```

## 6. 维护命令（forget / dedup）

N23a 新增两个维护 CLI（`python -m kb forget` / `dedup`），默认 **dry-run 安全优先**（只输出候选不修改数据），直接调用存储层遍历，不经过 REST/服务层。

### 6.1 forget — 扫描陈旧未命中记忆

```bash
# 预览超 90 天未命中的记录（dry-run 默认开启，不删除）
python -m kb forget --stale --days 90 --dry-run

# 确认后删除（需输入 yes 二次确认）
python -m kb forget --stale --days 90 --no-dry-run
```

- `--stale`：陈旧未命中模式（当前唯一模式，必传）
- `--days N`：未命中天数阈值，默认 90
- `--dry-run/--no-dry-run`：默认 dry-run（仅输出候选表：记录ID/内容摘要/最后命中时间/天数）；`--no-dry-run` 需输入 `yes` 确认后才删除
- 天数计算：`last_accessed` 为空时用 `created_at` 替代（从未命中=创建时间）

### 6.2 dedup — 扫描语义重复对

```bash
# 预览相似度 > 0.85 的重复对（dry-run 默认开启，不修改）
python -m kb dedup --dry-run

# 自定义阈值
python -m kb dedup --threshold 0.90 --dry-run
```

- `--threshold FLOAT`：余弦相似度阈值，默认 0.85
- `--dry-run/--no-dry-run`：默认 dry-run（输出候选对表：记录A/记录B/相似度/内容摘要）；`--no-dry-run` 暂未实现自动合并（N23c 智能层 consolidation 后续实现），提示人工审核后手动处理
- 逐条计算 embedding 后两两比较余弦相似度，记录数较多时耗时随 O(n²) 增长

## 7. 常见问题与故障排查

**Q：healthz 访问不通？**
服务没起来。检查：终端是否还开着、端口是否被占（`netstat -ano | findstr 8000`）、
换端口 `KB_API_PORT=8001 python -m kb serve`。

**Q：AI 说连不上 MCP / 工具列表为空？**
① 确认 kb serve 在跑；② 客户端 MCP 面板点刷新/重连（服务重启后旧连接会失效）；
③ TraeWork 必须桌面版 + 本地运行环境；④ 确认在智能体（Agent）模式对话。

**Q：Ollama 报错 / /ask 返回 503？**
① 确认 Ollama 已从**开始菜单/托盘**正常启动（不要从 AI 沙箱终端拉起，会数据库读写
失败）；② `ollama list` 确认模型名已拉取并写入 `.env` 的 `KB_LLM_MODEL`（模型无预设，
按自己显存选，如 qwen3:4b / qwen3:1.7b，不一致可 `ollama cp` 改名）；③ 云端 LLM 检查
`KB_LLM_API_KEY` 与 `KB_LLM_BASE_URL` 是否填对（任意 OpenAI 兼容服务商均可，见第 5 节
配置参考）；④ 不配 LLM 也不影响记忆存取与检索（`KB_LLM_MODE=off` 默认）。

**Q：PowerShell 里 curl/Invoke-RestMethod 中文乱码？**
已知问题（JSON 响应缺 charset，v1.0.2 候选修复）：用 `curl.exe` 代替 `curl` 别名，
或用 Python 客户端，或在 PowerShell 7 中 `$OutputEncoding = [Text.Encoding]::UTF8`。

**Q：调用 MCP（stdio/SSE）时终端中文乱码？**
kb 已在启动时强制 UTF-8 编解码并把 Windows 控制台切到 65001，服务端字节本身无乱码。
若仍出现，通常是**调用侧**编码问题，按调用方式排查：
- **PowerShell 调 REST/SSE**：用 `curl.exe`（不要用 `curl` 别名）、`Invoke-RestMethod` 加
  `-ContentType "application/json; charset=utf-8"`，或先 `chcp 65001`；
- **PowerShell 管道喂 MCP stdio**（`echo … | python -m kb mcp`）：先设
  `$OutputEncoding = [Text.Encoding]::UTF8; [Console]::OutputEncoding = [Text.Encoding]::UTF8`
  再执行，否则管道里的中文会按 GBK 传成乱码；
- **cmd 窗口**：先 `chcp 65001` 再运行，并确保用 Windows Terminal 等支持 UTF-8 的终端；
- **MCP 客户端（TraeWork/Claude Code/Cursor）**：客户端侧按协议 UTF-8 解码，一般无乱码；
  若客户端配置里允许指定编码，确保为 UTF-8。

**Q：断网能用吗？**
能。模型与数据全部落本地，记忆写入/检索/本地问答断网完整可用（这是验收标准之一）。
仅云端降级需要网络。

**Q：数据存在哪？怎么备份？**
全部在 `kb_data/` 目录（ChromaDB + 运行时状态）。备份 = 复制该目录；恢复 = 覆盖回去。

**Q：想把记忆库清空重来？**
停止服务后删除 `kb_data/chroma` 目录再重启（不可逆，先备份）。

**Q：worker 领了卡不动/卡在 claimed？**
在 worker 任务里发"继续"唤醒它；若确认该任务已废，让协调者在看板上处理退卡。

---

**手册反馈**：使用中发现描述与实际不符，直接告诉任意一个 AI 任务"更新 USER_GUIDE 第 X 节"。
