# rag-kb — 本地 Agent 记忆服务 + 多 Agent 协作系统

<!-- mcp-name: io.github.fish827-08/kb-memory -->

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Glama MCP score](https://glama.ai/mcp/servers/fish827-08/rag-kb/badges/score.svg)](https://glama.ai/mcp/servers/fish827-08/rag-kb)
[English](README_EN.md) | 中文

**本地优先、完全离线可用的 Agent 记忆与知识服务**——REST + MCP 双协议，混合检索（向量 + BM25/RRF），文档/网页入库与 RAG 问答；无 LLM 时存取与检索完整可用。

<!-- GitHub Topics: mcp, memory-service, rag, ai-agent, knowledge-base, local-first, llm, claude-code, hybrid-search, embedding -->

本仓库含两个子系统（[ROADMAP](ROADMAP.md)）：

| 子系统 | 一句话定位 | 状态 |
|---|---|---|
| **kb** | 本地优先、完全免费的 Agent 记忆与知识服务（核心产品，开发主线） | v2.0.0 生产可用 |
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
  - 可选精排：`KB_RERANK_ENABLED=true` 启用 bge-reranker-v2-m3 交叉重排（默认关）
  - 可选三路：`KB_SPARSE_ENABLED=true` 启用 BGE-M3 稀疏向量第三路（默认关，失败自动降级双路）
- **记忆管理**：写入 / 更新 / 删除 / 列表，支持 namespace、tags、type 过滤
- **记忆全共享（v3）**：所有记忆/知识与任何客户端、任务、AI 全共享——本地单用户定位，跨 agent、跨任务读到同一份用户记忆；client/project 仅用于审计归类与元数据（不再隔离读写）；主键服务端生成
- **存取审计**：每次写/读/改/删/检索/问答记 JSON 到 `logs/agent-audit/<客户端>__<项目>.log`（按 client+project 分文件）；用户可查：REST `GET /api/v1/audit?client=<客户端>[&project=<项目>]` 或 CLI `kb audit --client <客户端>`
- **知识入库**：本地文档（txt/md/pdf/docx 等）上传或路径导入，网页正文抓取入库
- **目录监听**：指定目录内新增/删除文件自动入库/清理（`KB_WATCH_DIR`）
- **CLI 工具**：`kb add/search/stats/ask/eval/forget/dedup`——终端直接完成写入、检索、统计与 RAG 问答
- **隐私护栏**：敏感 namespace 强制本地回答不出网；`/ask` 智能路由（本地优先，难题可选云端）
- **断网可用**：模型与数据全部落本地，无网络时存取与检索功能完整

## 快速开始（Windows / Linux / macOS）

**Windows PowerShell：**

```powershell
# 1. 创建并激活虚拟环境
python -m venv venv
.\venv\Scripts\Activate.ps1

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务（默认监听 http://127.0.0.1:8000）
python -m kb serve
```

**Linux / macOS：**

```bash
# 1. 创建并激活虚拟环境
python3 -m venv venv
source venv/bin/activate

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务
python -m kb serve
```

> 重要：`kb` 命令只装在虚拟环境内。**每个新终端都要先激活虚拟环境**
> （Windows `.\venv\Scripts\Activate.ps1`，Linux/macOS `source venv/bin/activate`），
> 否则会提示 `kb：未找到命令`。改用 `python -m kb <子命令>` 可绕过激活。
>
> 快速安装备选（免虚拟环境，Python 3.10+）：`pip install --user -r requirements.txt` 后
> 用 `python -m kb serve` 运行，但建议优先使用 venv 隔离依赖。

启动后健康检查：

```powershell
curl http://127.0.0.1:8000/api/v1/healthz
```

> 首次启动会加载本地嵌入模型（默认 BAAI/bge-m3，约 2GB，需提前下载缓存）；
> 未配置 LLM 时服务照常启动，`/ask` 返回 503 与配置指引。
>
> **LLM 默认关闭（`KB_LLM_MODE=off`）**：服务启动不会探测/加载/调用任何大模型，
> 零显存、零成本、纯离线；记忆写入、文档入库、混合检索完整可用。
> 需要 RAG 问答（`/ask`）时再按下面方式配置本地或云端 LLM。

### 配置 LLM（可选，`/ask` 问答需要）

`KB_LLM_MODE` 默认 `off`（不加载不调用）；四个档位：

| 档位 | 行为 |
|---|---|
| `off`（默认） | 完全不加载/不调用 LLM；记忆存取与检索完整可用 |
| `local` | 仅本地 Ollama（完全离线，隐私零出网） |
| `auto` | **本地优先，云端降级**：本地 Ollama 可用走本地，无本地但有云端 Key 走云端 |
| `cloud` | 全部走云端（本地仅做压缩与隐私隔离） |

**本地 LLM（Ollama）：**

```powershell
# 1. 安装并启动 Ollama（Windows 从开始菜单/托盘启动，不要从 AI 沙箱终端拉起）
# 2. 拉取一个适合你电脑的模型（按显存/内存选择，如 qwen3:4b 约 3.2GB、
#    qwen3:1.7b 约 1.8GB；国内可用魔搭加速，拉完 ollama cp 改成短名）
ollama pull <你的模型名>
# 3. 在 .env 配置后重启服务：
#    KB_LLM_MODE=local            # 仅本地
#    KB_LLM_MODEL=<你的模型名>    # 以 `ollama list` 输出的名称为准
#    KB_OLLAMA_BASE_URL=http://localhost:11434
```

**云端 LLM（任意 OpenAI 兼容服务商，不绑定 DeepSeek）：**
DeepSeek / OpenAI / 通义千问 / 硅基流动 / Moonshot 等均可，通用三键：

```ini
# .env
KB_LLM_MODE=auto                 # 本地优先、云端降级；或 cloud 全云端
KB_LLM_API_KEY=sk-xxx            # 服务商 API Key
KB_LLM_BASE_URL=https://api.deepseek.com   # 换成你所用服务商的 OpenAI 兼容端点
KB_LLM_CLOUD_MODEL=deepseek-v4-flash       # 云端模型名
```

验证：`GET /api/v1/healthz` 的 `llm` 字段——`local`/`cloud` 表示 LLM 已就绪，`disabled` 表示未启用。

### 常见问题：嵌入模型下载失败

- **中国大陆直连 `huggingface.co` 会超时**。设置 HF 镜像后重启即可：
  ```bash
  export HF_ENDPOINT=https://hf-mirror.com   # 或写入 ~/.bashrc 永久生效
  python -m kb serve
  ```
  模型会自动从镜像下载并缓存到 `~/.cache/huggingface/hub/`，之后断网也能离线加载。
- **模型已在本机缓存，但无外网**：`kb` 采用离线优先（先命中本地缓存，失败才联网），
  只要缓存目录完整即可完全离线运行。

## 让 Agent 接入 kb（客户端无关）

把 kb 的接入规约交给 AI 客户端（TraeWork / Claude Code / Cursor / 自建 Agent），
让它们知道怎么读写记忆、按什么身份规约、怎么查审计。**两种方式，任选其一**：

1. **skill（推荐，能自动触发）——可选的独立步骤**：仓库内
   [`skills/kb-memory/SKILL.md`](skills/kb-memory/SKILL.md)
   是客户端无关的 Anthropic 开格式 skill。把它安装到你所用客户端的用户级 skills 目录后，
   该客户端的任何项目会话都会在读写记忆/RAG 问答/审计查询时**自动识别并触发**。
   安装 = 把 `skills/kb-memory` 目录复制过去即可（有脚本，也可手动复制，无需任何依赖）；
   更新（重新覆盖）、卸载、以及装好后的使用说明见
   [`scripts/README.md`](scripts/README.md)。

   > **不装也不影响 kb 服务**：skill 只是给 AI 客户端的「提示词包装」，与服务的安装、
   > 启动无关——跳过这一步，服务照常运行，你随时可用方法 2 的纯文本提示词接入；
   > skill 安装是**一次性、按需、独立执行**的，不会随 `kb serve` 自动触发，
   > 也不会写入你的任何客户端目录以外的文件。
2. **纯文本提示词（兜底，任何客户端通用）**：整段复制
   [`docs/AGENT_PROMPT.md`](docs/AGENT_PROMPT.md) 粘贴给 Agent 即可，不依赖 skill 机制。

> **`.trae-cn/skills` / `.claude/skills` / `.cursor/skills` 是任何客户端都认的「标准」吗？——不是。**
> 这些只是各家客户端各自的**用户级约定目录**：`SKILL.md` 本身是统一的 Anthropic 开格式，
> 但「装到哪个目录、能否自动触发」由各客户端自行决定，支持程度不一：

| 客户端 | 用户级 skills 目录 | 自动加载 |
|---|---|---|
| TraeWork | `~/.trae-cn/skills/` | 自动发现 |
| Claude Code | `~/.claude/skills/` | 高版本支持 |
| Cursor | `~/.cursor/skills/` | 逐步跟进 |
| 其他 / 自建 Agent | 无统一约定 | 需手动加载或不支持 |

> 不存在「所有客户端都遵循」的统一目录；你的客户端若不支持 skill，**永远有方法 2 兜底**
> （粘贴 `AGENT_PROMPT.md`，纯文本任何客户端可用）。安装方法、各客户端目录差异与加载机制、
> 相互引用关系：详细见 [`scripts/README.md`](scripts/README.md)（此处不重复）。

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
| `KB_LLM_MODE` | `off` | LLM 模式：`off`（默认，不加载/不调用 LLM，零显存零成本）/ `local`（仅本地 Ollama）/ `auto`（本地优先，云端降级）/ `cloud` |
| `KB_DEVICE` | 空 | 嵌入模型设备：空=自动检测，可显式设 `cpu` / `cuda` |
| `KB_WATCH_DIR` | `data` | serve 模式监听目录，文件变动自动入库；空串或 `.` = 不启动 |
| `KB_DATA_DIR` | `kb_data` | 运行数据根目录（ChromaDB、运行时状态等） |
| `KB_API_HOST` / `KB_API_PORT` | `127.0.0.1` / `8000` | REST 与 MCP 监听地址 |
| `KB_EMBED_MODEL` | `BAAI/bge-m3` | 嵌入模型 |
| `KB_LLM_MODEL` | 空 | 本地 Ollama 模型名（默认空=不配；配 `KB_LLM_MODE=local/auto` 时须按自己电脑选模型，以 `ollama list` 为准） |
| `KB_OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama 端点 |
| `KB_LLM_API_KEY` / `KB_LLM_BASE_URL` / `KB_LLM_CLOUD_MODEL` | 空 | 云端 LLM（可选）：**任意 OpenAI 兼容服务商**（DeepSeek / OpenAI / 通义 / 硅基流动等），仅填在本机 `.env` |
| `KB_CHUNK_SIZE` / `KB_CHUNK_OVERLAP` | `500` / `100` | 文档切分参数 |
| `KB_SENSITIVE_NAMESPACES` | 空 | 逗号分隔的敏感 namespace，命中强制本地回答不出网 |
| `KB_API_KEY` | 空 | 空=不鉴权（本地回环零摩擦）；非空=启用 Bearer/X-API-Key 鉴权；orchestra 客户端自动带 `X-API-Key` 头 |
| `KB_RERANK_ENABLED` / `KB_RERANK_MODEL` / `KB_RERANK_TOP_N` | `false` / `BAAI/bge-reranker-v2-m3` / `20` | 检索精排（A3.5）：融合候选送 CrossEncoder 重排，默认关 |
| `KB_SPARSE_ENABLED` | `false` | 稀疏第三路（A3.5）：BGE-M3 稀疏向量 + 倒排索引参与 RRF 融合，默认关 |

## CLI 速查（无需启动服务）

```powershell
python -m kb add "记忆内容" --tags 偏好 --client TraeWork                       # 写入（project 缺省自动取当前目录名）
python -m kb search "查询词" --client TraeWork                                 # 混合检索（v3：全共享，不分客户端/项目）
python -m kb stats                            # 统计：类型分布 / 访问热度 / 陈旧分布
python -m kb ask "问题" --client TraeWork      # 终端 RAG 问答（LLM 不可用时输出检索命中）
python -m kb audit --client TraeWork --days 7   # 查某客户端/项目存过/读过什么
python -m kb eval --file tests/eval_zh_50.jsonl   # 检索质量评测（Recall@1/@5 + MRR）
python -m kb forget --stale --days 90         # 清理超期未命中记忆
python -m kb dedup --threshold 0.92           # 语义去重
```

> `kb ask` 直连本地服务逻辑（不经 HTTP）；建议 `serve` 停止时使用，避免双进程写库竞争。

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
               service / llm / ingest / watcher / api / mcp / cli + reranker / sparse / eval）
tests/         kb 验收测试（375 项，含 eval_zh_50.jsonl 检索评测数据集）
orchestra/     多 Agent 协作系统（board.py CLI + 协议三件套 + skill + 245 项测试）
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
