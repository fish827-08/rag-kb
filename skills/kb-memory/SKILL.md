---
name: kb-memory
description: kb 本地记忆与知识服务接入规约：MCP 8 工具 + agent_id 身份强制 + 存取审计（logs/agent-audit）。当会话需要读取/写入/检索 Agent 记忆、导入文档/网页做 RAG 问答、查询某 Agent 的存取审计，或用户提到"kb 记忆服务/MCP 工具"时使用。
---

# kb-memory：本地优先 Agent 记忆与知识服务接入规约

> 本 skill 面向**使用 kb 服务的任意 AI 客户端**（TraeWork / Claude Code / Cursor /
> 自建 Agent 等）。kb 是常驻本机的记忆服务（默认 `http://127.0.0.1:8000`），
> 本地优先、免费、断网可用。SKILL.md 是客户端无关的开格式（Anthropic Skills 规范）；
> 各客户端的安装方式见本项目 `scripts/README.md`（TraeWork / Claude Code / 其他）。
> 完整 HTTP 端点与 curl 示例见 AGENT_PROMPT.md / USER_GUIDE.md。

## 1. 服务定位

- **记忆**：写入会话中值得长期记住的事实/偏好/决策，日后语义检索召回。
- **知识**：本地文档、网页正文切分入库，之后混合检索 + RAG 问答。
- 写入与检索**不依赖大模型**；仅 `ask` 问答需要 LLM（默认 `KB_LLM_MODE=off` 不加载）。

## 2. 接入方式（按优先级）

1. **MCP（首选）**：环境配置了 kb 的 MCP 服务器则直接用 MCP 工具。
2. **HTTP（MCP 不可用时兜底）**：REST 端点（同地址），成功调用后优先回 MCP。
3. 接入前确认服务在跑：请求 `GET http://127.0.0.1:8000/api/v1/healthz` 返回 200。
   - **有代理时用 `curl --noproxy "*"`**（`HTTP_PROXY`/`HTTPS_PROXY` 会把 localhost 请求也转发导致连接失败，
     误判服务未启动）；PowerShell 可用 `Invoke-RestMethod`（默认不走代理直连本机）。

## 3. 身份规约（v2：环境承载，AI 不自报身份）

- **`client`（自动识别，AI 不传）**：服务端从 MCP 握手 clientInfo 自动识别
  （TraeWork / Claude Code / Cursor）；REST 默认 `HTTP`，CLI 默认 `CLI`。
- **`project`（可选，环境/配置承载）**：项目/任务归属（连接配置声明；
  不传=该客户端**默认桶**）。CLI 自动取当前目录名；REST 显式传。
- **隔离语义（2026-08-30 v2）**：**memory 按 (client, project) 双键隔离**——
  只可见/可操作自己客户端的、自己项目的记忆（他人 → REST 404 / MCP `FORBIDDEN`）；
  **doc/web 共享知识所有客户端可见**。
- **不再有 `agent_id` 入参**：记录主键由服务端生成，AI 无需（也不应）自报身份，只专注存取与查询。

## 4. MCP 工具（8 个，无 agent_id）

| 工具 | 参数 | 说明 |
|---|---|---|
| `write_memory` | `content`, `tags?`, `project?`, `client?` | 写入记忆（归属当前 client+project/默认桶），返回 `{id}` |
| `search_memory` | `query`, `top_k?=5`, `project?`, `client?` | 混合检索；memory 仅返回归属当前 (client, project) 的，doc/web 共享 |
| `read_memory` | `record_id`, `project?`, `client?` | 读单条；他人 memory → `FORBIDDEN` |
| `update_memory` | `record_id`, `content`, `project?`, `client?` | 更新记忆（自动重嵌入）；非归属 → `FORBIDDEN` |
| `delete_memory` | `record_id`, `project?`, `client?` | 删除记忆；非归属 → `FORBIDDEN` |
| `add_document` | `path`, `project?`, `client?` | 导入文档（PDF/DOCX/MD/TXT/Office）切分入库（共享知识） |
| `add_webpage` | `url`, `project?`, `client?` | 抓取网页正文切分入库（共享知识） |
| `ask_kb` | `question`, `project?`, `client?` | RAG 问答，返回 `{answer, sources}`；未配 LLM → `LLM_DISABLED` |

## 5. 写入记忆的规范（每条写前过一遍）

硬规则：
- **写前先查重**：先 `search_memory`，已存在同类则跳过或 `update_memory` 覆盖，不新增重复。
- **纠错用更新**：旧记忆过时用 `update_memory` 改内容，不另写一条（避免同事实多版本矛盾）。
- **敏感信息不入库**：密钥、API Key、凭据、身份证号等严禁写入（落盘在 `kb_data/`）。

主动识别写入（`write_memory` 精炼 1~3 句）：
1. **用户偏好**：主题、工具链、代码风格、命名习惯、沟通方式。
2. **项目决策**：已拍板的选型/架构取舍/约定，写清背景与结论。
3. **事实与约定**：关键路径、命令、依赖、环境细节、版本约束等可复用事实。
4. **敏感约束**：不可做的红线、安全/隐私要求。
5. **当前任务进度**：复杂任务的关键进展与下一步，便于跨会话接力。

不写：纯寒暄、临时计算、可由代码/文档直接查到的实现细节。

**对用户提示**：写入/更新记忆成功时，只向用户轻提一句（如"已记住你的偏好"），
**不要展示**工具名、记录 ID、JSON、检索命中详情等操作细节；查重未命中与命中同理静默处理。

## 6. 检索与查询

- **检索时机**：任务开始、回答涉及历史决策/偏好、跨会话接力时，先 `search_memory` 主动召回。
- **检索技巧**：query 用自然语言描述"语义"（如"用户的主题偏好"），不只给关键词；
  命中后 `read_memory` 看全文。
- **存取审计**：每次 write/search/read/update/delete/ask/ingest 记 JSON 到
  `logs/agent-audit/<客户端>__<项目>.log`（按 client+project 分文件，身份由文件名承载）。
  查询入口：REST `GET /api/v1/audit?client=<客户端>&project=<项目>&action=&days=&limit=`
  或 CLI `kb audit --client <客户端> [--project <项目>]`。

## 7. HTTP 兜底端点速查（v2：无 agent_id，身份用 client/project）

- 健康检查：`GET /api/v1/healthz`
- 写记忆：`POST /api/v1/memories` `{"content","tags?","source?","namespace?","client?","project?"}`（client 默认 HTTP）
- 读/改/删：`GET/PATCH/DELETE /api/v1/memories/{id}?project=<项目>`（非归属 → 404）
- 检索：`POST /api/v1/search` `{"query","top_k?=5","mode?","client?","project?"}`
- 文档/网页入库：`POST /api/v1/documents`（multipart 或 `{"path","client?","project?"}`）、
  `POST /api/v1/ingest/web` `{"url"}`
- 问答：`POST /api/v1/ask` `{"question","client?","project?"}`（LLM 未配 → 503/LLM_DISABLED）
- 审计：`GET /api/v1/audit?client=<客户端>[&project=<项目>]`

## 8. 注意事项

- 服务未启动（healthz 失败）：先确认是否因**代理**拦截 localhost（改用 `curl --noproxy "*"` /
  `Invoke-RestMethod` 重试），确认真的未启动才提示 `python -m kb serve`；不臆造结果。
- 服务启用鉴权（`KB_API_KEY` 非空）：所有 HTTP 请求带 `Authorization: Bearer <key>`。
- `namespace` 仅 HTTP 端点支持（MCP 工具不含）；命中敏感 namespace 时 `ask` 强制本地不出网。
- `add_document`/`add_webpage` 入库后即可被检索；格式不支持返回 `UNSUPPORTED_FORMAT`。