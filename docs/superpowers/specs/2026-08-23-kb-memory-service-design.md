# kb：本地 Agent 记忆与知识服务 — 设计文档

- 日期：2026-08-23
- 状态：✅ 已实施完成（2026-08-24 收口）：N1-N16 全部节点通过验收，M1-M4 里程碑全交付，人工终验通过
- 项目性质变更：从学习型项目（rag-kb）转向实用产品。旧发展路线（ROADMAP.md 的阶段规划）不再约束本项目。

## 1. 背景与定位

一个**本地优先、完全免费的 Agent 记忆与知识服务**：常驻 Windows 终端的后台服务，通过 REST + MCP 双协议向 Claude Code、Cursor、自建 Agent 及普通脚本提供记忆写入、知识导入与混合检索能力。对标 Mem0 / Zep 的自建开源简化版，差异化在于：本地优先、MCP 原生、零运维、无 LLM 依赖也能完整运行。

## 2. 需求

### 2.1 功能需求

| 优先级 | 需求 | 说明 |
|---|---|---|
| P0 | 记忆条目 CRUD | 事实/笔记/摘要等短文本的写入、读取、更新、删除 |
| P0 | 文档导入 | PDF / TXT / MD / DOCX 切分入库 |
| P0 | 混合检索 | 向量语义 + BM25 关键词（jieba 分词），RRF 融合 |
| P0 | REST + MCP 双协议 | REST 通用接入；MCP 供 Claude Code / Cursor 挂载 |
| P0 | LLM 分层接入 | 优先本地 Ollama（OpenAI 兼容），降级云 API（DeepSeek）；均不可用时 LLM 禁用，存取检索不受影响 |
| P0 | 硬件自适应 | 检测独立显卡 → 首次运行交互询问是否 GPU 加速，选择持久化；否则 CPU |
| P1 | 网页抓取入库 | URL → 正文提取 → 入库 |
| P1 | 目录监听 | watchdog 监听目录，新增/修改文件自动索引，文件删除同步清理 |
| P1 | 文档管理 | 列表 / 按文档整体删除 / 重新索引 |
| P2 | 记忆高级特性 | 重要性评分、衰减遗忘、摘要压缩 |
| P2 | 多 Agent 隔离启用 | 数据模型预留 namespace 字段，初期不启用 |
| P2 | 向量库可替换 | 预留 VectorStore 抽象接口，未来可换 Qdrant 等 |

### 2.2 非功能需求

- **免费优先**：全部开源组件；唯一可能产生费用的是降级云 LLM 调用。
- **本地优先**：模型预下载后，断网可完整运行存取与检索。
- **平台**：仅 Windows（当前阶段），代码不引入平台锁定依赖。
- **目标硬件**：6GB 显存 + 16GB 内存——本地模型选型与显存预算的基准约束。
- **性能**：个人级数据（< 10 万条记录），混合检索 < 500ms。
- **可扩展**：存储 / embedding / 检索器均有抽象边界，规模上升可替换实现。

### 2.3 成功标准

1. Claude Code 通过 MCP 挂载后，能写入并跨会话检索到记忆。
2. 无 API Key、断网状态下，服务正常启动并完成存取与混合检索。
3. 导入 100 份文档后，关键词与语义两类查询均能正确命中。

## 3. 技术选型（方案 B：去框架化）

放弃 LangChain 全家桶，仅保留独立小包 `langchain-text-splitters`（切分器久经考验、无传递依赖负担），其余全部直接使用底层库。理由：服务核心是"存取 + 检索"，LCEL 编排与框架包装是净负担；直接使用底层库可控性最好。

| 组件 | 选型 | 许可证 |
|---|---|---|
| Web 框架 | FastAPI + uvicorn | MIT |
| MCP | 官方 `mcp` Python SDK（mcp 2.0.0 的 MCPServer，挂载进同一 ASGI 应用；SDK 2.0 已移除 1.x 的 FastMCP 类，能力等价） | MIT |
| 向量库 | ChromaDB（嵌入式，进程内直连） | Apache 2.0 |
| 关键词检索 | rank_bm25 + jieba | Apache 2.0 / MIT |
| Embedding | sentence-transformers 直载 BGE-M3（默认），可配置小模型（如 bge-small-zh） | MIT |
| LLM 客户端 | openai SDK（OpenAI 兼容：Ollama / DeepSeek） | Apache 2.0 |
| 文档解析 | pypdf、python-docx、markitdown（Office 全格式兜底） | MIT |
| 网页抓取 | httpx + trafilatura | Apache 2.0 |
| 目录监听 | watchdog | Apache 2.0 |
| 配置 | pydantic-settings | MIT |
| CLI | typer + rich | MIT |

## 4. 架构

### 4.1 进程模型

单进程：`python -m kb serve` 启动常驻服务。FastAPI 为主框架，MCP（mcp 2.0.0 `MCPServer`，streamable http）挂载在同一 ASGI 应用（`/mcp/` 路径）；REST 与 MCP 共享同一 `KBService` 实例，不存在两套业务逻辑。

```
客户端（Claude Code / Cursor / 自建 Agent / 脚本）
        │ REST + MCP
        ▼
┌─ kb serve（单进程）─────────────────────┐
│  协议层：REST API ｜ MCP Server          │
│  核心层：KBService（统一后端）            │
│  组件层：存储 ｜ 检索 ｜ 摄取 ｜ LLM 接入 │
└──────────────────────────────────────┘
   │本地          │本地         ┆可选外部
   ▼              ▼             ▼
 ChromaDB      BGE-M3      Ollama / 云 LLM
```

无 LLM 时：`/ask`、`ask_kb` 返回 503；其余功能完整。

### 4.2 包结构

```
kb/
├── __init__.py
├── __main__.py     # python -m kb 入口
├── config.py       # pydantic-settings 配置
├── models.py       # Record 数据模型
├── service.py      # KBService 核心编排
├── storage.py      # ChromaDB 封装（VectorStore 抽象接口）
├── embedder.py     # BGE-M3 加载 + 设备检测
├── bm25.py         # BM25 索引 + jieba 分词
├── retriever.py    # 混合检索 + RRF 融合
├── ingest.py       # 文档 / 网页解析入库
├── watcher.py      # 目录监听（后台线程）
├── llm.py          # LLM 分层接入
├── api.py          # FastAPI 路由
├── mcp.py          # MCP tools
└── cli.py          # serve / add / search / info
```

依赖方向：`api.py / mcp.py / cli.py → service.py → (storage, retriever, ingest, llm) → (config, models)`。禁止跨层反向依赖。

### 4.3 数据模型（models.py）

记忆条目与文档 chunk 同库同 collection，`type` 区分：

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | str (uuid4) | 主键 |
| `content` | str | 正文 |
| `type` | `memory` / `doc_chunk` / `web_chunk` | 来源类型 |
| `namespace` | str，默认 `"default"` | 预留隔离字段，初期全部写默认值 |
| `source` | str 或 None | 文件名 / URL / 写入方标识 |
| `tags` | list[str] | 自由标签 |
| `importance` | float，默认 0.5 | 预留 P2 遗忘机制 |
| `created_at` / `updated_at` | str (ISO 8601) | 时间戳 |

入库规则：
- 记忆短文本（长度 < 500 字）不切分直接入库；超长记忆按文档切分规则处理。
- 文档按 chunk 切分（size 500 / overlap 100，配置可调），同 `source` 的 chunk 通过 metadata 关联，支持按文档整体删除。
- BM25 索引启动时从库内全量重建（个人级秒级）；写入时向内存语料追加并重建索引，删除时惰性重建，个人级规模下均为毫秒—秒级，不构成瓶颈。

## 5. API 设计

### 5.1 REST（前缀 `/api/v1`）

| 端点 | 方法 | 功能 |
|---|---|---|
| `/memories` | POST | 写入 `{content, tags?, source?, namespace?}` → `{id}` |
| `/memories` | GET | 列表；过滤参数 `type` / `tag` / `source` / `q`（内容包含）；分页 `limit` / `offset` |
| `/memories/{id}` | GET | 读取单条 |
| `/memories/{id}` | PATCH | 更新 `content` / `tags`（content 变更时重新嵌入） |
| `/memories/{id}` | DELETE | 删除 |
| `/search` | POST | `{query, top_k=5, mode=hybrid\|vector\|keyword, type?, tag?}` → 结果列表（含 score） |
| `/documents` | POST | 上传文件（multipart）或传本地路径导入 → `{source, chunks}` |
| `/documents` | GET | 文档列表（按 source 聚合：chunk 数、导入时间、总字符数） |
| `/documents/{source}` | DELETE | 删除整份文档的所有 chunk |
| `/ingest/web` | POST | `{url}` 抓取正文入库 → `{source, chunks}` |
| `/ask` | POST | `{question}` RAG 问答；LLM 未就绪时返回 503 与配置指引 |
| `/healthz` | GET | `{status, llm, device, records}` |

### 5.2 MCP tools（mcp.py）

`write_memory(content, tags?)` / `search_memory(query, top_k?)` / `read_memory(id)` / `update_memory(id, content)` / `delete_memory(id)` / `add_document(path)` / `add_webpage(url)` / `ask_kb(question)`

全部为 KBService 的薄封装。

## 6. 混合检索（retriever.py）

```
query ──┬─ 向量路：embed → Chroma top-N（N = 3 × top_k）
        └─ 关键词路：jieba 分词 → BM25 top-N
                     ↓
       RRF 融合：score(d) = Σ 1/(60 + rank_i(d))
                     ↓
       按 id 去重 → 输出 top_k
```

- `mode` 参数：`hybrid`（默认）/ `vector` / `keyword`。
- 输出统一结构：`[{id, content, score, type, source, tags, created_at}]`。
- Chroma 余弦距离归一化为相似度分数；BM25 分数仅用于排名，不与向量分数直接比较（RRF 只看排名）。

## 7. LLM 智能路由（llm.py）

### 7.1 架构：本地守门，云端外援

`/ask` 采用智能路由——本地模型先判断问题复杂度与内容敏感度：简单问题本地直接答，复杂问题压缩上下文后发云端，敏感内容强制本地。

```
问题 → 混合检索（无 LLM 成本）
         ↓
    本地模型路由判断（简单 / 复杂 / 敏感）
      ↓         ↓         ↓
    简单       复杂       敏感 / 断网
      ↓         ↓         ↓
   本地直答  压缩上下文→  本地直答
             云端生成    （数据不出网）
```

本地模型在路由架构中的职责：
1. **复杂度路由**：判断问题走本地还是云（输出约 5 token，开销可忽略）
2. **上下文压缩**：检索结果 2000 token → 压缩至约 500 token 再发云，省约 60% 输入 token
3. **简单问题直答**：记忆场景大头是单事实查询，本地消化，零云成本
4. **隐私隔离**：标记敏感的 namespace / 记录强制本地，数据不出机器
5. **离线兜底**：断网 / API 失败 / 额度耗尽时降级本地，服务不瘫
6. **后台批处理**：记忆打标签、重要性评分、摘要压缩（P2 功能）全走本地

路由 / 压缩 / 回答统一使用当前配置的单个默认模型，不搞双模型同载（两模型 + embedding 会超 6GB 显存）。

### 7.2 模型档位（2026-08-23 基准实测定型）

RAG 护栏配置（关思考 / temp 0.2 / num_ctx 4096 / 强约束 prompt）下的实测：

| 档位 | 模型 | 实测显存 | 实测速度 | 基准结果（4 项） |
|---|---|---|---|---|
| **默认** | `qwen3:4b` | 3.2GB（含 4096 ctx） | 80.2 tok/s | 全过；摘要质量更优（含细节与机制） |
| 低配 | `qwen3:1.7b` | 约 1.8GB | 101.4 tok/s | 全过；摘要要点齐但细节少 |

长上下文补充测试（2026-08-23，800 token 参考文档，事实埋于首/中/尾三处）：

| 模型 | 输入处理 | 生成 | 总耗时 | 首中尾事实抽取 |
|---|---|---|---|---|
| `qwen3:4b` | 8982 tok/s | 64 tok/s | 2.5s | 3/3 全对，无"迷失在中间" |
| `qwen3:1.7b` | 14831 tok/s | 83 tok/s | 2.4s | 3/3 全对 |

结论：输入处理极快（2000 token 检索上下文仅增加约 0.2s），真实 `/ask` 场景端到端约 2.5s，交互体验良好；两档模型均远超 8 tok/s 达标线。

关键发现：前期无约束交互测评暴露的幻觉、会话污染问题，在 RAG 护栏（强约束 system prompt + 单轮无状态 + 上下文硬上限）下**未复现**——`/ask` 质量主要靠工程护栏而非模型规模。会话污染对无状态的 `/ask` 天然免疫。

### 7.3 护栏参数（硬编码为 kb/llm.py 默认值）

- `think: false`（qwen3 思考模式在 RAG 场景无增益且拖慢响应）
- `temperature: 0.2`
- `num_ctx: 4096`
- `max_tokens: 800`（输出上限，控制成本）
- system prompt：仅依据参考文档回答，无相关信息则明确说明，禁止编造
- 检索上下文硬上限 2000 token，超出按相似度截断

### 7.4 接入与降级链

1. 启动探测 Ollama（`GET localhost:11434/v1/models`，超时 2s）→ 判定本地可用
2. 配置了 `DEEPSEEK_API_KEY`（模型 `deepseek-v4-flash`）→ 判定云端可用
3. `KB_LLM_MODE = local | auto | cloud`（默认 `auto`）：
   - `local`：全部本地答
   - `auto`：智能路由（推荐）
   - `cloud`：全部云端，本地仅做压缩与隐私隔离
4. 本地不可用且无 Key → LLM 禁用，`/ask` 返回 503 附配置指引；存取与检索不受影响

调用方式：本地走 Ollama 原生 `/api/chat`（`think` 与 `options` 全参数可控），云端走 openai SDK；`kb/llm.py` 内部抽象为统一接口，上层无感知。

### 7.5 Token 成本控制（对比全云端不压缩）

| 措施 | 效果 |
|---|---|
| 简单问题本地消化（auto 模式预估过半流量） | 该部分零云成本 |
| 本地压缩上下文 2000 → 500 token | 云输入 -60% |
| 相同问题缓存（问题向量相似命中直接返回） | 零 token |
| `max_tokens: 800` 输出封顶 | 成本可控 |
| **合计预估** | **约 -80%** |

## 8. 设备检测与显存预算（embedder.py + cli.py）

目标硬件：6GB 显存 + 16GB 内存（Windows）。

- `torch.cuda.is_available()` 为真：首次运行交互询问"检测到独立显卡，是否启用 GPU 加速？"，选择持久化到 `kb_data/runtime.json`；后续启动直接读取。
- `KB_DEVICE` 环境变量优先级最高，供非交互的服务模式覆盖。
- 默认 `cpu`。Embedding 模型在首次实际使用时延迟加载（服务启动不阻塞）。
- GPU 模式下 BGE-M3 以 fp16 加载（约 1.1GB 显存）；CPU 模式内存占用约 2.2GB，16GB 内存无压力。
- 显存预算（总计 6GB）：embedding fp16（~1.1GB）+ `qwen3:4b`（实测 3.2GB @4096 ctx）= 4.3GB，可共存（2026-08-23 验证）；若 OOM，将 `KB_DEVICE` 设为 `cpu`（embedding 转 CPU 后检索延迟个人级可接受），或 LLM 换低配档 `qwen3:1.7b`（~1.8GB）。

## 9. 摄取管道（ingest.py + watcher.py）

- 文档：`.pdf` → pypdf；`.docx` → python-docx；`.md` / `.txt` 直接读取；`.xlsx` / `.pptx` 等 → markitdown 兜底。不支持的扩展名返回 400。
- 网页：httpx 拉取（超时 15s，UA 伪装常规浏览器）→ trafilatura 提取正文 → 入库为 `web_chunk`，`source` 为 URL。抓取/解析失败返回 400 及原因。
- 目录监听：watchdog 后台线程监听配置目录（默认 `data/`）；创建/修改事件去抖 2s 后入库；文件删除时同步删除对应 `source` 的记录。

## 10. 错误处理

- 统一错误 JSON：`{"error": "<CODE>", "message": "<人话描述>"}`。
- 状态码语义：400 参数/文件格式错误；404 记录不存在；422 校验失败（FastAPI 默认，格式化）；503 LLM 未就绪。
- 全局异常处理器兜底未知异常为 500，不泄露堆栈给客户端，堆栈进日志。

## 11. 测试策略

| 层 | 工具 | 覆盖 |
|---|---|---|
| 单元 | pytest | RRF 融合排序、jieba 分词、Record 序列化/反序列化、配置加载 |
| 集成 | pytest + 临时目录 | Chroma CRUD 全流程、混合检索命中（关键词/语义各准备用例）、文档删除级联 |
| API | httpx TestClient | 全端点 happy path + 主要错误路径 |
| MCP | mcp 内存客户端 | 每个 tool 调用与返回结构 |

Embedding 相关测试用真实小模型（如 `bge-small-zh-v1.5`，约 100MB）保证可跑性；不 mock 向量行为。

### 11.1 职责分工

| 负责方 | 范围 |
|---|---|
| **AI（自动化）** | 上表全部 pytest 自动化；本地 LLM 部署验证与性能基准（见 11.2）；产出可复查的测试报告 |
| **人工（用户）** | 核心功能验收：2.3 节成功标准逐条验证、本地 vs 云端回答质量主观对比、Claude Code 实际挂载体验、GPU 加速交互选择流程 |

红线：AI 不得代替人工声称"验收已完成"；自动化测试全绿只是进入人工验收的前置条件。

### 11.2 本地 LLM 基准测试（AI 执行）

模型选型基准已于 2026-08-23 完成（结论见 7.2 节，两档模型均达标，`qwen3:4b` 定为默认）。M3 交付时补测 `/ask` 全链路指标：

1. 真实规模上下文（2000 token 检索片段）下的首 token 延迟与总耗时（小上下文基准已过，此项验证长输入）。
2. 智能路由正确性：简单/复杂/敏感三类问题各若干，验证路由决策与隐私隔离生效。
3. 压缩有效性：上下文压缩前后 token 数对比（目标 2000 → 500 左右）。
4. 缓存命中：重复相同问题的响应时间与 token 消耗（目标零消耗）。
5. 汇总为报告，若低于达标线（8 token/s / 首 token 5s）给出切云建议，人工最终决定。

**M3 全链路基准结果（2026-08-23，生产配置实测：cuda + BGE-M3 + auto 路由 + Ollama qwen3:4b，think/stream 关闭护栏，11 条记忆语料）**：

| 指标 | 实测 | 达标线 | 结论 |
|---|---|---|---|
| 混合检索延迟（hybrid，top_k=5，5 次） | 平均 26ms，最大 29ms | < 500ms | ✅ 大幅超标 |
| /ask 端到端（分类+生成，新问题 ×3） | 平均 1.95s，最大 2.67s | 参考 ~2.5s 预估 | ✅ 符合预估 |
| 路由 SIMPLE（"张三的生日是哪天"） | local 直答，答案正确，3.3s | 正确路由 | ✅ |
| 路由 COMPLEX（综合对比类问题） | 无云→本地直答，结构化总结，5.3s | 正确路由 | ✅ |
| 路由 SENSITIVE（问敏感 tag 记忆） | 强制 local，答案正确，1.6s | 不出云 | ✅ 隐私隔离生效 |
| 缓存命中（重复问题第 2/3 次） | 0.88s / 0.04s（零 LLM 调用） | 零 token 消耗 | ✅ |

补充说明：压缩有效性（目标 3）与云端对比需配置 DeepSeek Key 后由人工验收（Key 属敏感信息不入库）；首 token 延迟因非流式接口不单独计量，以端到端耗时为准。**基准过程发现并修复 N9 缺陷**：Ollama `/api/chat` 的 `stream` 默认为 true（返回 JSONL），未显式关闭导致生产配置下 `/ask` 全量 500——已修复（显式 `stream:false`）并以测试断言锁定（N12-hotfix）。

**结论：本地档全链路性能达标，无需切云。**

## 12. 环境准备清单（人工执行，开工前完成）

| # | 事项 | 说明 |
|---|---|---|
| 1 | Python 3.10+ 虚拟环境 | 沿用现有 venv 或新建 |
| 2 | 安装 CUDA 版 PyTorch | ✅ 已完成（2026-08-23）：torch 2.11.0+cu128，CUDA 可用已验证。要点：Python 3.10 的 CUDA 轮子最新为 2.11.0+cu128（cu124 已停发）；官方源国内仅 ~0.6MB/s，轮子从阿里云镜像 `mirrors.aliyun.com/pytorch-wheels/cu128/` 下载（curl 带 `-A "Mozilla/5.0"`，实测 ~6.5MB/s），再本地 `pip install` |
| 3 | 一次性下载 BGE-M3 模型 | ✅ 已缓存（4.25GB，位于 `~/.cache/huggingface/hub`）。新环境重装时：设置 `HF_ENDPOINT=https://hf-mirror.com` 后首次启动自动下载 |
| 4 | 安装 Ollama for Windows | ✅ 已完成（2026-08-23）：v0.32.15 安装，11434 端口正常。官网慢时用 GitHub Releases + 加速代理下载（可用前缀按序尝试 `gh-proxy.com` / `ghproxy.net` / `ghfast.top` / `ghproxy.cn`，旧域名 ghproxy.com 已停服），或 `winget install --id=Ollama.Ollama -e` |
| 5 | 拉取本地模型 | ✅ 已完成（2026-08-23）：`qwen3:1.7b` 与 `qwen3:4b` 均已拉取并改名，基准见 7.2 节。模型目录已迁移至 **`D:\ollama_models`**（用户级环境变量 `OLLAMA_MODELS` 已设置）。国内直连慢时从魔搭拉取：`ollama pull modelscope.cn/Qwen/Qwen3-4B-GGUF` 后 `ollama cp` 改短名 |
| 6 | DeepSeek API Key | ⬜ 待办：从 https://platform.deepseek.com 注册获取，写入 `.env` 的 `DEEPSEEK_API_KEY`，作为云降级备份 |
| 7 | 验证 GPU 可用 | ✅ 已完成（2026-08-23）：`torch.cuda.is_available()=True`，CUDA GPU 矩阵乘法测试通过 |

其中 2、3 是 GPU 加速与首次启动的关键路径，其余可与开发并行。

## 13. 迁移与范围

- 归档：`rag_kb/`、`app/`、`demo.py`、`test_py/`、`step_doc/`、`notes/`、旧 `README.md`、`ROADMAP.md` 已于 2026-08-23 按人工指示移入 `_archive/`（保留历史，不删除；禁止参考其内容）。
- 新增：`AGENTS.md`（仓库根目录，AI 开发第一入口——声明文档有效性、硬件约束、开发约定与测试职责分工）。
- 重写：`README.md`（新定位、安装、Claude Code / Cursor 挂载 MCP 的配置示例）；`requirements.txt`。
- 保留：`data/` 样例文档（M4 摄取人工验证用）。
- 明确不做（本期）：鉴权与多用户、Qdrant 实际接入（仅留接口）、记忆遗忘/摘要（P2）、Web UI、Docker。

## 14. 里程碑

| 里程碑 | 内容 | 验收 | 状态 |
|---|---|---|---|
| M1 数据层+检索 | models / storage / embedder / bm25 / retriever / service / cli | CLI 可 add + search，混合检索三类查询命中 | ✅ 2026-08-23 完成（N1-N6 + 离线加载 hotfix） |
| M2 REST 服务 | api.py + 设备检测 + 错误处理 | memories CRUD / search / documents 列表与删除 / healthz 全部 curl 通过，断网启动正常（/documents 上传在 M4、/ask 在 M3 节点补齐） | ✅ 2026-08-23 完成（N7-N8，人工门禁通过） |
| M3 MCP + LLM | mcp.py + llm.py 智能路由 + /ask | Claude Code 挂载后可写/读记忆；local/auto/cloud 三模式与降级链正确；路由/压缩/缓存全链路基准报告（11.2 节）产出 | ✅ 2026-08-24 完成（N9-N12 + stream hotfix；TraeWork MCP 挂载实测通过，人工门禁通过） |
| M4 摄取增强 | 网页抓取 + watcher + 文档管理 + markitdown 全格式 | URL 入库、目录新增文件自动索引、按文档删除 | ✅ 2026-08-24 完成（N13-N16，人工终验通过，项目收口） |
| P2 追加（A3.5 检索质量，N24-N27） | 交叉重排（reranker.py）+ BGE-M3 稀疏第三路（sparse.py，三路 RRF）+ 评测基准（eval.py + 50 条中文 QA）+ N+1 修复与 BM25 持久化；rerank/sparse 默认关 | spec 见 `2026-08-28-a35-retrieval-quality-design.md`（含真实基线指标） | ✅ 2026-08-28 完成（分支 `feature/a-line-remaining`，全量回归 584 项绿） |
| P2 追加（A4 易用性，N28） | `kb stats`（类型分布/访问热度/陈旧分布）+ `kb ask`（终端 RAG 问答，LLM 缺席降级输出检索命中）；**Web UI 砍掉**（评估报告定论） | spec 见 `2026-08-28-a4-cli-design.md` | ✅ 2026-08-28 完成（分支 `feature/a-line-remaining`） |

每个里程碑附带对应测试。全量回归 584 项全绿（2026-08-28：tests/ 339 + orchestra/tests/ 245，tests/ 全目录）。
