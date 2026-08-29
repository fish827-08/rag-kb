# 通用 Agent 接入提示词（kb 记忆服务）

> 用途：把下面「提示词正文」整段复制粘贴给任意 AI Agent（TraeWork / Claude Code / Cursor / 自建 Agent 等），
> 该 Agent 即可接入 kb 记忆与知识服务。文本是纯文本，不依赖任何框架。

---

## 提示词正文（从这里开始复制）

你已接入「kb」——一个本地优先的 Agent 记忆与知识服务。请按本说明使用它：

### 一、服务定位

kb 是常驻在你本机的记忆服务（默认地址 `http://127.0.0.1:8000`），提供两类能力：

1. **记忆**：把会话中值得长期记住的事实、偏好、决策写入，日后可语义检索召回。
2. **知识**：把本地文档、网页正文切分入库，之后可对知识库做混合检索与 RAG 问答。

它完全本地、免费、断网可用；写入、检索不依赖任何大模型，`ask` 问答才需要 LLM。

### 二、接入方式（按优先级）

1. **MCP（首选）**：如果当前环境配置了 kb 的 MCP 服务器，直接用 MCP 工具。
2. **HTTP（MCP 不可用时兜底）**：当 MCP 工具不存在、调用报连接失败或超时时，改用 REST 端点（地址同上）；成功调用后优先回 MCP。
3. 接入前先确认服务在跑：请求 `GET http://127.0.0.1:8000/api/v1/healthz`，返回 200 即正常。

### 三、MCP 工具（8 个）

| 工具 | 参数 | 说明 |
|---|---|---|
| `write_memory` | `content: str`, `tags?: list[str]` | 写入一条记忆短文本，返回 `{id}` |
| `search_memory` | `query: str`, `top_k?: int=5` | 混合检索（向量+关键词融合），返回命中列表 `{id, content, score, type, source}` |
| `read_memory` | `record_id: str` | 按 ID 读单条记忆完整内容 |
| `update_memory` | `record_id: str`, `content: str` | 更新记忆内容（自动重新嵌入） |
| `delete_memory` | `record_id: str` | 删除单条记忆 |
| `add_document` | `path: str` | 导入本地文档（PDF/DOCX/MD/TXT/Office），切分入库 |
| `add_webpage` | `url: str` | 抓取网页正文切分入库 |
| `ask_kb` | `question: str` | 基于知识库的 RAG 问答，返回 `{answer, sources}`；未配 LLM 时返回 `LLM_DISABLED` |

> 注：MCP 工具仅暴露上表参数（`write_memory` 无 `namespace`/`source`）；`namespace` 等更细参数仅 HTTP 端点支持（见第四节）。

### 四、HTTP 兜底端点（MCP 不可用时用）

- 健康检查：`GET /api/v1/healthz`
- 写入记忆：`POST /api/v1/memories`，JSON `{"content": "…", "tags": ["偏好"], "source": "…", "namespace": "…"}`
- 读单条：`GET /api/v1/memories/{id}`
- 更新：`PATCH /api/v1/memories/{id}`，`{"content": "…"}`
- 删除：`DELETE /api/v1/memories/{id}`
- 列表：`GET /api/v1/memories?type=&tag=&q=&limit=`
- 混合检索：`POST /api/v1/search`，`{"query": "…", "top_k": 5, "mode": "hybrid"}`（mode: hybrid/vector/keyword）
- 文档入库：`POST /api/v1/documents`（multipart `file` 或 JSON `{"path": "本地路径"}`）
- 网页入库：`POST /api/v1/ingest/web`，`{"url": "…"}`
- RAG 问答：`POST /api/v1/ask`，`{"question": "…"}`

curl 示例（PowerShell）：

```powershell
# 写入
curl -X POST http://127.0.0.1:8000/api/v1/memories -H "Content-Type: application/json" -d '{"content": "用户偏好深色主题", "tags": ["偏好"]}'
# 检索
curl -X POST http://127.0.0.1:8000/api/v1/search -H "Content-Type: application/json" -d '{"query": "用户界面偏好", "top_k": 5}'
# 问答
curl -X POST http://127.0.0.1:8000/api/v1/ask -H "Content-Type: application/json" -d '{"question": "用户喜欢什么主题？"}'
```

### 五、写入记忆的规范（什么该写）

先记三条硬规则（每次写入前过一遍）：

- **写前先查重**：写入前先 `search_memory`；已存在同类内容则跳过，或 `update_memory` 覆盖，不新增重复记录。
- **纠错用更新**：发现旧记忆过时/错误时，用 `update_memory` 改内容，不要另写一条（避免同事实多版本互相矛盾）。
- **敏感信息不入库**：密钥、API Key、凭据、身份证号等敏感信息严禁写入记忆库（记忆库落盘在本机 `kb_data/`）。

在上述前提下，在会话中主动识别并写入以下内容（`write_memory`，内容精炼为 1~3 句）：

1. **用户偏好**：主题、工具链、代码风格、命名习惯、沟通方式等。
2. **项目决策**：已拍板的技术选型、架构取舍、约定；写清背景与结论。
3. **事实与约定**：关键路径、命令、依赖、环境细节、版本约束等可复用事实。
4. **敏感约束**：不可做的红线（如"禁止改设计文档"）、安全/隐私要求。
5. **当前任务进度**：复杂任务的关键进展、下一步，便于跨会话接力。

不写：纯寒暄、临时计算过程、可由代码/文档直接查到的实现细节（除非需跨会话记忆）。

**检索时机**：任务开始、回答涉及历史决策/偏好、跨会话接力时，先 `search_memory` 主动召回，不要等被问到。

**检索技巧**：`search_memory` 的 query 用自然语言描述你想找的"语义"（如"用户的主题偏好"），
不要只给关键词；混合检索会自动做语义匹配，命中后可 `read_memory` 看全文。

### 六、注意

- 服务未启动时（healthz 失败）：不要臆造结果，提示需要先启动 kb 服务（`python -m kb serve`）。
- 若服务启用了鉴权（`KB_API_KEY` 非空），所有 HTTP 请求需带 `Authorization: Bearer <key>`。
- `namespace` 仅 HTTP 端点支持（MCP 工具不含该参数）；命中敏感配置的 namespace 时，`ask` 强制本地回答不出网。
- `add_document`/`add_webpage` 入库后可被 `search_memory`/`ask_kb` 检索到；文件格式不支持会返回 `UNSUPPORTED_FORMAT`。

---
