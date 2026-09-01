---
name: kb-memory
description: kb 本地记忆与知识服务接入规约。当会话需要给用户写入/检索长期记忆、导入文档/网页做 RAG 问答、或用户提到"kb 记忆/MCP 工具"时使用。已挂载 kb MCP 的客户端自动获得全局规约（instructions），本 skill 主要用于未挂载 MCP 时的 HTTP 兜底。
---

# kb-memory：本地 Agent 记忆与知识服务接入规约

> kb 是本机常驻服务（默认 `http://127.0.0.1:8000`），本地优先、免费、断网可用，
> 写入/检索不依赖大模型（仅 `ask` 问答需要 LLM）。接入前**无需**探测服务是否在跑——
> 直接调用即可，一切正常就继续工作；只有工具/HTTP 调用报连接失败时才提示服务未启动。

## 1. 核心规约

- **记忆与知识全共享**：任何客户端/任务写入的记忆对所有会话可见，跨 agent 跨任务共享。
  不用传 client/project（仅审计归类），不传也完整可用；服务端自动识别来源客户端。
- **何时写入**（`write_memory`，精炼 1~3 句）：用户偏好 / 项目决策 / 事实与约定 / 敏感约束 / 任务进度。
  不写：寒暄、临时计算、代码文档里能查到的实现细节。
- **写前查重**：先 `search_memory`，已有同类则 `update_memory` 覆盖，不新增重复。
- **敏感不入库**：密钥、凭据、身份证号等严禁写入。
- **何时检索**：任务开始 / 回答涉及历史决策偏好 / 跨会话接力时，先 `search_memory` 主动召回。

## 2. 对用户反馈（极简）

- 写入/更新成功：只轻提一句（如"已记住你的偏好"）；失败才说明原因
  （如"没记：与已有记忆重复 / 内容敏感 / 格式不支持"）。
- **不要展示**：健康检查过程、工具名、记录 ID、JSON、检索命中详情。
- 仅在工具/HTTP 调用真正报连接失败时，简提"kb 服务未启动，需先运行 `python -m kb serve`"，不臆造结果。

## 3. 接入方式

1. **MCP（首选）**：`write_memory` / `search_memory` / `read_memory` / `update_memory` /
   `delete_memory` / `add_document` / `add_webpage` / `ask_kb`（8 个工具，全共享，无归属限制）。
2. **HTTP 兜底（未挂载 MCP 时）**：
   - 写：`POST /api/v1/memories` `{"content":"…","tags":["…"]}`
   - 检索：`POST /api/v1/search` `{"query":"…","top_k":5}`
   - 问答：`POST /api/v1/ask` `{"question":"…"}`（LLM 未配 → `LLM_DISABLED`）
   - 文档/网页入库：`POST /api/v1/documents`（multipart 或 `{"path":"…"}`）、`POST /api/v1/ingest/web` `{"url":"…"}`
   - HTTP 调用失败时**先用 `curl --noproxy "*"` / `Invoke-RestMethod` 重试**
     （系统代理会拦截 localhost 导致误判服务未启动），真正连不上才提示启动服务。