# Agent 身份与存取审计设计（agent_id 隔离 + access-audit.log）

- 日期：2026-08-29
- 状态：设计书（先文档后代码），随本 spec 实施
- 依据：用户需求「多 Agent 协作时各 Agent 记忆需身份隔离，日志记载谁存取了什么，用户可查询」；`kb/models.py`（Record.namespace 已有但检索/读写未按身份隔离）；`kb/mcp.py`（8 工具未暴露身份）；`kb/api.py`（SearchRequest 无 namespace 过滤）；`kb/audit.py`（治理审计 JSON 行模式可复用）；`kb/storage.py`（ChromaStore 已支持 where 过滤与 list_records）
- 现状结论（2026-08-29 实测代码审读）：
  - Record 已含 `namespace`/`source` 字段，但**所有检索（search/ask/list）均不带 namespace 过滤，MCP 工具完全不暴露身份参数**——目前任何 Agent 能读到全部记忆，无法区分"谁的记忆"
  - 并行性：单进程 uvicorn + 线程池（默认 40 线程），REST/MCP 可多 Agent 并发调用；ChromaDB 读并行、写有线程序列化，个人项目量级足够
  - 存取过程零审计：请求日志只有 method/path/status，不含身份与内容

## 1. 需求

### 1.1 问题

1. **无身份**：Agent 写入记忆时不标明自己是谁，检索也不区分——多 Agent 共享记忆库互相"串记忆"
2. **无隔离**：任何 Agent 可检索/读取全部记忆，隐私与归属不清
3. **无审计**：谁存了什么、谁读了什么毫无记录，用户无法回溯
4. **无可查性**：本地运行的服务没有给用户提供"查某 Agent 存过/读过哪些记忆"的入口

### 1.2 目标

- **agent_id 字段**：每条 Record 都有归属 Agent（`agent_id`，默认 `default`），写入时由调用方携带
- **身份隔离**：个人记忆（memory 类型）检索/读取/更新/删除按 agent_id 强制隔离——只读自己的；共享知识（doc_chunk/web_chunk）所有 Agent 可见，但写入时记录归属者（用于审计）
- **存取审计**：每次写入/检索/读取/更新/删除/问答记一条 JSON 审计日志（`access-audit.log`），含 agent_id、操作、内容摘要、query 摘要、命中；敏感内容只记截断摘要（沿用日志设计红线）
- **可查性**：用户可用 REST `GET /api/v1/audit` 或 CLI `kb audit` 查询任意 Agent 的存取记录
- **向后兼容**：不传 agent_id 的既有调用默认 `default`，行为与现状兼容（文档块不受隔离影响）

### 1.3 非目标（YAGNI）

- 不做认证级身份校验（agent_id 是自声明标识，用于协作纪律，不作安全边界；真正的鉴权仍由 KB_API_KEY 承担）
- 不做 Web 看板的审计界面（先 REST + CLI；看板后续版本）
- 不做跨 Agent 记忆共享/访问控制列表（ACL）——隔离是"只读自己"，共享走 doc/web 知识库
- 不给 CLI/board.py 加独立审计（其操作经 REST/MCP 已覆盖）
- 不迁移既有数据改写（旧记录缺 agent_id 视为 `default`，零迁移）

## 2. 架构

### 2.1 身份与隔离语义

```
写入：write_memory(agent=A)  → Record.agent_id=A（memory 类型）
      add_document/add_webpage(agent=A) → Record.agent_id=A（但 doc/web 属共享知识）

检索：search/ask 带 agent_id 参数
      - memory 记录：仅返回 agent_id == 调用方 的记录（强制隔离）
      - doc_chunk / web_chunk：全部可见（共享知识库，不论归属）
读取：read_memory(id) 带 agent_id
      - memory：仅作者可读（非作者 → NOT_FOUND/403）
      - doc/web：可读
更新/删除：带 agent_id，仅作者可操作（非作者 → NOT_FOUND）
```

关键决策：**memory（个人记忆）严格隔离，doc/web（知识库）共享**——既满足"Agent 只读取自己的记忆"，又不破坏"多 Agent 共享知识库做 RAG"的能力。

### 2.2 数据模型（models.py）

`Record` 新增字段：

```python
agent_id: str = "default"          # 写入方 Agent 身份；推荐用任务名（TASK-xxx / worker-1）；旧数据缺失视为 "default"
client: str = "default"            # 来源客户端（TraeWork / Claude Code / Cursor / CLI / HTTP）；旧数据缺失视为 "default"
```

- `to_metadata()`：增加 `"agent_id"` / `"client"` 两键
- `from_chroma()`：`metadata.get("agent_id", "default")` / `metadata.get("client", "default")`（旧记录缺键自动回落，零迁移）

客户端自动识别：MCP 工具不传 `client` 时，从 MCP 握手 `clientInfo.name` 提取（`kb/mcp.py::_client_from_ctx`）；
REST/CLI 由调用方显式传 `client`（默认 `default`/`CLI`）。

### 2.2a 身份字段规约（校验白名单，MCP/REST 统一执行）

`kb/service.py` 提供三个纯函数校验（MCP 工具与 REST pydantic validator 共用）：

| 字段 | 规则 | 违规处理 |
|---|---|---|
| `agent_id`（任务名） | 1~64 字符，仅字母/数字/中文/下划线/连字符；**MCP 下必填且禁止 `default`/`unknown` 等占位** | MCP：`INVALID_ARGUMENT`；REST：422 |
| `client`（来源客户端） | 额外允许空格/点（如 `Claude Code`）；空值=自动识别 | 同上（默认 default 视为未提供，放行） |
| `project`（项目名） | 同 agent_id 白名单；空值=未提供 | 同上 |

- 目的：阻止 AI 随意传任意字符串污染审计/隔离语义（如传 `"has space"`、`"bad!client"`）
- 向后兼容：REST/CLI 未传 `agent_id`/`client` 仍按 default 语义工作（zero-friction）；
  仅 MCP 通道强制 agent_id 非 default（否则审计 `default__default.log` 无法溯源）

### 2.3 检索隔离（retriever.py + service.py）

`search(query, top_k, mode, type, tag, agent_id="default")`：
- 融合后过滤阶段（现有 type/tag 过滤同处）追加：
  - `rec.type == memory` 且 `rec.agent_id != agent_id` → 跳过
  - doc/web 不过滤
- `ask` 内部检索同样透传 agent_id（AskRequest 增加可选字段）

### 2.4 存取审计（audit.py，复用现有 JSON 行模式，按 Agent 分文件）

按 Agent 分类落盘：`log_dir/agent-audit/<客户端名>__<项目名>__<任务名>.log`
（无项目则 `<客户端名>__<任务名>.log`；如 `TraeWork__kb__TASK-0076.log`、`Claude Code__worker-1.log`）。
身份（client/project/agent）由**文件名承载**，行内不重复记录——任务更名只需重命名对应文件；
查询侧（`service.query_access_audit`）从文件名解析补回身份字段。JSON 行按天轮转 30 天：

```json
{"timestamp":"...","action":"write","type":"memory","record_id":"...","namespace":"default","content":"前50字符摘要"}
{"timestamp":"...","action":"search","query":"前50字符摘要","hits":5}
{"timestamp":"...","action":"read","record_id":"...","content":"前50字符摘要"}
{"timestamp":"...","action":"update","record_id":"...","content":"前50字符摘要"}
{"timestamp":"...","action":"delete","record_id":"..."}
{"timestamp":"...","action":"ask","query":"前50字符摘要"}
{"timestamp":"...","action":"ingest","type":"doc_chunk","source":"a.pdf"}
```

- 敏感红线沿用日志设计第 5 节：content/query 仅记**前 50 字符**摘要；blocklist 字段不渲染
- 审计失败不阻塞主流程（记 WARNING，与现有 `log_governance_event` 同模式）
- 审计开关：`KB_ACCESS_AUDIT_ENABLED`（默认 `true`，本地量级可关）
- 文件名非法字符清理：白名单 `[\w\u4e00-\u9fff.\-· ]`，其余→`_`；连续下划线折叠，保证
  `__` 三段分隔可解析（轮转后缀 `.YYYY-MM-DD` 自动剥离）

### 2.5 查询入口

**REST：** `GET /api/v1/audit`

| 参数 | 说明 |
|---|---|
| `agent` | 必填，Agent 身份（如 `worker-1`） |
| `action` | 可选，过滤操作类型（write/search/read/update/delete/ask/ingest） |
| `days` | 可选，最近 N 天（默认查全部） |
| `limit` | 可选，条数上限（默认 100） |

返回 `{"items": [审计记录...], "total": N}`；严格验证 agent 非空。

**CLI：** `kb audit --agent worker-1 [--action write] [--days 7]`

读同一个 `agent-audit/` 目录（按文件名解析身份），终端表格输出。

## 3. API 变更

| 端点/工具 | 变更 |
|---|---|
| MCP `write_memory` | 参数 + `agent_id`（**必填，无默认**，schema required 同步） |
| MCP `search_memory` | 参数 + `agent_id`（**必填**） |
| MCP `read_memory` | 参数 + `agent_id`（**必填**）（memory 非作者 → `{"error":"FORBIDDEN"}`） |
| MCP `update_memory` / `delete_memory` | 参数 + `agent_id`（**必填**）（非作者 → FORBIDDEN） |
| MCP `ask_kb` | 参数 + `agent_id`（**必填**） |
| MCP `add_document` / `add_webpage` | 参数 + `agent_id`（**必填**，记录归属，检索不隔离） |
| `POST /api/v1/memories` | body + `agent_id`（可选，默认 default） |
| `GET /api/v1/memories` | query + `agent_id`（可选；缺省过 filter 由调用方声明） |
| `POST /api/v1/search` | body + `agent_id`（可选默认 default）；memory 强制隔离 |
| `POST /api/v1/ask` | body + `agent_id`（可选） |
| 新增 `GET /api/v1/audit` | 用户查询 Agent 存取记录 |

MCP 工具表从 8 → 8 个（参数扩展，不新增工具数）。

## 4. 配置

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `KB_ACCESS_AUDIT_ENABLED` | `true` | Agent 存取审计开关（JSON 行，access-audit.log） |

一律走 `kb/config.py`，禁止硬编码。

## 5. 测试（验收要点）

1. 写入隔离：agent-A 写 memory，agent-B 检索/读/改/删均不可见/不可操作（FORBIDDEN）
2. 共享知识：agent-A 入库 doc，agent-B 可检索（不受隔离影响）
3. 缺省兼容：不传 agent_id 的写/检默认 `default`，行为与旧版一致
4. 旧数据兼容：无 agent_id 元数据的旧记录视为 `default`
5. 审计闭环：write/search/read/update/delete/ask/ingest 各触发一条 JSON 行；content/query 截断 50 字符；无敏感全文
6. `GET /api/v1/audit` 与 `kb audit` 查询正确（按 agent/action/days 过滤）
7. 回归：既有测试全绿（隔离默认 default + 文档共享 = 零行为变化）

## 6. 实施顺序（节点）

| 节点 | 内容 | 门禁 |
|---|---|---|
| A1 | models + config + service/retriever 隔离 | 标准（测试 1/2/3/4） |
| A2 | mcp 8 工具 + REST 端点 agent_id | 标准（测试 1-4 通道层） |
| A3 | 存取审计（audit.py + access-audit.log）+ REST/CLI 查询 | 标准（测试 5/6） |
| A4 | 全量回归 + 文档同步（AGENT_PROMPT/USER_GUIDE/README/.env.example） | 标准（测试 7） |