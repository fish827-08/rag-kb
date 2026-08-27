# rag-kb — 本地 Agent 记忆服务 + 多 Agent 协作系统

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[English](README_EN.md) | 中文

本仓库含两个子系统（[ROADMAP](ROADMAP.md)）：

| 子系统 | 一句话定位 | 状态 |
|---|---|---|
| **kb** | 本地优先、完全免费的 Agent 记忆与知识服务（核心产品，开发主线） | v1.0.1 生产可用 |
| **agent-orchestra** | 基于 kb 共享任务板的跨任务多 Agent 协作系统（❄️ 维护模式，自用脚手架） | B1-B3 收口冻结 |

> 开源协议：[Apache-2.0](LICENSE)（含专利授权，可商用）。

## kb — 本地优先的 Agent 记忆与知识服务

Windows 单进程常驻（`python -m kb serve`），REST + MCP 双协议，向 Claude Code /
Cursor / TraeWork / 自建 Agent 提供记忆写入、文档与网页入库、混合检索
（向量 + BM25/RRF 融合）与 RAG 问答。

**无 LLM 时存取与检索完整可用**——记忆写入、文档入库、混合检索不依赖任何大模型；
配置本地 Ollama 或云端 API 后，`/ask` 问答能力自动启用。

## 核心特性

- **单进程常驻**：一个 `python -m kb serve` 同时提供 REST API 与 MCP 端点，无需额外组件
- **混合检索**：BGE-M3 向量检索 + BM25 关键词检索，RRF 融合排序，中文分词友好
- **记忆管理**：写入 / 更新 / 删除 / 列表，支持 namespace、tags、type 过滤
- **知识入库**：本地文档（txt/md/pdf/docx 等）上传或路径导入，网页正文抓取入库
- **目录监听**：指定目录内新增/删除文件自动入库/清理（`KB_WATCH_DIR`）
- **隐私护栏**：敏感 namespace 强制本地回答不出网；`/ask` 智能路由（本地优先，难题可选云端）
- **断网可用**：模型与数据全部落本地，无网络时存取与检索功能完整

## 快速开始（Windows PowerShell）

```powershell
# 1. 创建并激活虚拟环境
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务（默认监听 http://127.0.0.1:8000）
python -m kb serve
```

启动后健康检查：

```powershell
curl http://127.0.0.1:8000/api/v1/healthz
```

> 首次启动会加载本地嵌入模型（默认 BAAI/bge-m3，约 2GB，需提前下载缓存）；
> 未配置 LLM 时服务照常启动，`/ask` 返回 503 与配置指引。

## MCP 挂载

MCP 端点（streamable HTTP）：`http://127.0.0.1:8000/mcp/`

**Claude Code**：本仓库已内置项目级 `.mcp.json`，在本目录启动 Claude Code 即自动挂载；
也可全局添加：

```powershell
claude mcp add --transport http kb http://127.0.0.1:8000/mcp/
```

**Cursor / TraeWork 及其他支持 MCP 的客户端**：在 MCP 配置中加入以下 JSON
（Cursor 放 `~/.cursor/mcp.json` 或项目 `.cursor/mcp.json`；TraeWork 在设置中添加 MCP 服务器）：

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

挂载后可用的 MCP 工具：`write_memory` / `search_memory` / `read_memory` /
`update_memory` / `delete_memory` / `add_document` / `add_webpage` / `ask_kb`。

> 启用 `KB_API_KEY` 鉴权后，MCP 客户端需在连接配置加 `headers`（`Authorization: Bearer <key>`）；
> 仓库内 `.mcp.json` 模板不含真实 key（JSON 不支持注释），配法见 [USER_GUIDE §5.2](docs/USER_GUIDE.md#52-客户端适配n20启用鉴权后各客户端如何带-key)。

## REST 端点速查

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/v1/memories` | 写入记忆 `{content, tags?, source?, namespace?}` |
| GET | `/api/v1/memories` | 记忆列表，支持 `type/tag/source/q/limit/offset` 过滤 |
| GET | `/api/v1/memories/{id}` | 读取单条记忆 |
| PATCH | `/api/v1/memories/{id}` | 更新内容或标签 |
| DELETE | `/api/v1/memories/{id}` | 删除单条记忆 |
| POST | `/api/v1/search` | 混合检索 `{query, top_k?, mode?, type?, tag?}`，mode: `hybrid/vector/keyword` |
| POST | `/api/v1/documents` | 文档入库：multipart `file` 字段或 JSON `{"path": "本地路径"}` |
| GET | `/api/v1/documents` | 已入库文档列表（按 source 聚合） |
| DELETE | `/api/v1/documents/{source}` | 按 source 删除该文档全部记录 |
| POST | `/api/v1/ingest/web` | 网页入库 `{url}`，抓取正文切分入库 |
| POST | `/api/v1/ask` | RAG 问答 `{question}`；未配置 LLM 返回 503 |
| GET | `/api/v1/healthz` | 健康检查与服务统计 |
| GET | `/api/v1/governance/stats` | 记忆治理统计：`total_count`/`avg_access_count`/`stale_90d_count`（只读） |
| GET | `/api/v1/governance/config` | 治理配置：衰减+新鲜度开关与参数（只读） |
| POST | `/api/v1/memories` → **409** | 启用语义去重（`KB_DEDUP_ENABLED=true`）后写入命中重复返回 409：`{"error":"DUPLICATE","duplicate_of":"<已有记录id>","similarity":<相似度>}`（不写入） |

> 记忆治理（去重/衰减/新鲜度）均默认关闭、零行为变化；用法见 [USER_GUIDE §3.5](docs/USER_GUIDE.md#35-记忆治理a3语义去重--衰减--新鲜度)。

示例：

```powershell
# 写入一条记忆
curl -X POST http://127.0.0.1:8000/api/v1/memories `
  -H "Content-Type: application/json" `
  -d '{"content": "用户偏好深色主题", "tags": ["偏好"]}'

# 混合检索
curl -X POST http://127.0.0.1:8000/api/v1/search `
  -H "Content-Type: application/json" `
  -d '{"query": "用户界面偏好", "top_k": 5}'

# RAG 问答（需配置 LLM）
curl -X POST http://127.0.0.1:8000/api/v1/ask `
  -H "Content-Type: application/json" `
  -d '{"question": "用户喜欢什么主题？"}'
```

## 配置项简表

全部配置以 `KB_` 前缀的环境变量或 `.env` 文件提供；完整键名见 [`.env.example`](.env.example)
（复制为 `.env` 后填写，`.env` 已被 gitignore，真实密钥只放本机，严禁入库）。

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `KB_LLM_MODE` | `auto` | LLM 模式：`local`（仅本地 Ollama）/ `auto`（本地优先，云端降级）/ `cloud` |
| `KB_DEVICE` | 空 | 嵌入模型设备：空=自动检测，可显式设 `cpu` / `cuda` |
| `KB_WATCH_DIR` | `data` | serve 模式监听目录，文件变动自动入库；空串或 `.` = 不启动 |
| `KB_DATA_DIR` | `kb_data` | 运行数据根目录（ChromaDB、运行时状态等） |
| `KB_API_HOST` / `KB_API_PORT` | `127.0.0.1` / `8000` | REST 与 MCP 监听地址 |
| `KB_EMBED_MODEL` | `BAAI/bge-m3` | 嵌入模型 |
| `KB_LLM_MODEL` | `qwen3:4b` | 本地 Ollama 模型名（以 `ollama list` 为准） |
| `KB_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 端点 |
| `KB_DEEPSEEK_API_KEY` | 空 | 云端降级 API Key（可选，只填在本机 `.env`） |
| `KB_CHUNK_SIZE` / `KB_CHUNK_OVERLAP` | `500` / `100` | 文档切分参数 |
| `KB_SENSITIVE_NAMESPACES` | 空 | 逗号分隔的敏感 namespace，命中强制本地回答不出网 |
| `KB_API_KEY` | 空 | 空=不鉴权（本地回环零摩擦）；非空=启用 Bearer/X-API-Key 鉴权；orchestra 客户端自动带 `X-API-Key` 头 |

## agent-orchestra — 多 Agent 协作系统（实验）

让多个 AI 助手（不同 TraeWork 任务 / Claude Code 会话，模型可不同）通过 kb 共享任务板
协作开发：协调者 AI 拆卡分发，worker AI 领卡执行、单卡单轮、回写结果，协调者核验流转。

```powershell
# 前置：kb serve 已运行。开一个新 TraeWork 任务，粘贴以下引导语即可唤醒一个 worker：
venv\Scripts\python.exe orchestra\board.py new-worker worker-1
```

完整使用方法（协调者怎么拆卡、多个 worker 怎么并行、协作纪律与已知限制）
见 [用户使用手册](docs/USER_GUIDE.md) 第 4 节。

## 目录结构

```
kb/            kb 服务源码（config / models / embedder / storage / bm25 / retriever /
               service / llm / ingest / watcher / api / mcp / cli）
tests/         kb 验收测试（65 项）
orchestra/     多 Agent 协作系统（board.py CLI + 协议三件套 + skill + 24 项测试）
docs/          设计文档、节点计划、用户使用手册
kb_data/       kb 运行数据（gitignore）
_archive/      旧学习项目归档（仅保留历史，禁止参考）
```

## 更多文档

- **用户使用手册（人类用户入口）**：[docs/USER_GUIDE.md](docs/USER_GUIDE.md)
- **AI 接力文档（AI 助手入口）**：[PROJECT.md](PROJECT.md)（项目状态 / 进度看板 / 接手指南）
- 设计文档（需求、架构、API、里程碑）：`docs/superpowers/specs/2026-08-23-kb-memory-service-design.md`
- P2 日志设计：`docs/superpowers/specs/2026-08-24-logging-design.md`
- P2 路线图：`docs/superpowers/plans/2026-08-24-p2-roadmap.md`
- 节点开发计划：`docs/superpowers/plans/2026-08-23-kb-dev-nodes.md`
- AI 协作规范：`AGENTS.md`
