# kb 开发者维护手册

> 面向项目维护者（人类）的架构与函数级手册。由代码 AST 全量扫描生成（2026-09-05，对应 v2.0.0；
> 2026-10-19 按交付加固后的代码校订）。
> 行号锚点格式 `kb/xxx.py:Lnn` 是生成时快照，代码演进后可能漂移；**以函数/类名在 IDE 搜索为准**，
> 代码改动后本手册以代码为准。
>
> **读法**：第 1-2 章建立架构认识（必读）；第 3 章函数索引（查阅用，不必通读）；
> 第 4 章"我要改 X 该动哪里"（维护时查）；第 5-6 章配置与红线（改代码前扫一眼）。

---

## 1. 架构总览

### 1.1 一句话架构

REST / MCP / CLI 三个接入壳 → 全部委托给唯一的业务中枢 `KBService`（service.py）→
检索走"向量 + BM25（+可选稀疏）双/三路融合，可选交叉重排"→ 存储只有 ChromaDB 一个实现，落盘 `kb_data/`。

### 1.2 分层地图

```
┌─ 接入层 ─────────────────────────────────────────────┐
│  api.py (REST, 915行)   mcp.py (MCP 8工具, 299行)   │
│  cli.py (命令行, 682行)                              │
└──────────────────────┬───────────────────────────────┘
                       ▼  全部委托（薄壳，无业务逻辑）
┌─ 服务层 ─────────────────────────────────────────────┐
│  service.py KBService 编排中枢 (910行)  ★精读        │
│  ingest.py 文档/网页入库    llm.py LLM 接入路由      │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌─ 检索层 ─────────────────────────────────────────────┐
│  retriever.py 混合融合 ★精读                         │
│  embedder.py 向量(默认 bge-small，可选 BGE-M3)        │
│  bm25.py 关键词                                     │
│  sparse.py 稀疏向量(可选)  reranker.py 重排(可选)    │
└──────────────────────┬───────────────────────────────┘
                       ▼
┌─ 存储层 ─────────────────────────────────────────────┐
│  storage.py ChromaStore（VectorStore 抽象接口）      │
└──────────────────────────────────────────────────────┘
支撑（横切，按需查阅）：config.py 配置 · models.py 数据模型 · audit.py 审计 ·
logging_setup.py 日志 · i18n.py 双语 · watcher.py 目录监听 · monitor.py 监控 ·
governance.py 治理(衰减/去重) · consolidation.py 智能归并 · eval.py 检索评测
```

### 1.3 关键设计决策（改了会牵一发动全身）

| 决策 | 位置 | 后果 |
|---|---|---|
| 三协议共享一个 `KBService` 单例，挂 `app.state.kb` | [api.py create_app](file:///./kb/api.py) | 改 service 构造函数，三端同时受影响 |
| 记忆全共享 v3：无 (client,project) 隔离，仅审计归类 | spec 2026-08-31 | 不要再加隔离过滤逻辑 |
| `Record` 统一承载记忆与文档 chunk，靠 `type` 字段区分 | models.py L17 | 检索/删除都按 type 过滤 |
| Chroma metadata 不支持 None/空串/list | models.py L38、storage.py L47 | 新增字段必须过 `to_metadata`/`_clean_metadata` |
| 模型全部懒加载（实例化不加载，首次用才加载） | embedder/reranker/llm | 不要在 `__init__` 里加载模型 |
| 治理功能（衰减/去重/重排/稀疏/归并）默认全关，零行为变化 | config.py L57-79 | 改默认值前先想兼容 |

---

## 2. 三条核心调用链（维护时 90% 的问题在这三条链上）

### 2.1 写记忆

```
REST POST /api/v1/memories  api.py:L546 (MemoryCreate 校验 L160)
MCP write_memory            mcp.py:L73
        │
        ▼
KBService.add_memory        service.py:L253
   ├─ validate_agent_id     service.py:L44
   ├─ governance.check_duplicate  governance.py:L157（dedup_enabled 时）
   ├─ Embedder.embed_texts  embedder.py:L26（首次调用触发模型加载 L13）
   ├─ ChromaStore.add       storage.py:L74
   ├─ BM25Index.add         bm25.py:L57（同步重建索引）
   ├─ _sparse_add           service.py:L233（sparse_enabled 时）
   └─ _audit_access         service.py:L486 → audit.py:L223
```

### 2.2 混合检索

```
REST POST /api/v1/search    api.py:L605 (SearchRequest 校验 L207)
MCP search_memory           mcp.py:L98
        │
        ▼
KBService.search            service.py:L577
        ▼
HybridRetriever.search      retriever.py:L94   ★核心
   ├─ 向量路：embed_query(embedder.py:L34) → ChromaStore.query(storage.py:L154)
   ├─ 关键词路：BM25Index.search(bm25.py:L89)
   ├─ 稀疏路（可选）：SparseIndex.search(sparse.py:L197)
   ├─ 融合：weighted_fuse(retriever.py:L48，min-max 归一加权) 或 rrf_fuse(L13)
   ├─ 重排（可选）：Reranker.rerank(reranker.py:L36)
   └─ 每路取 max(30, 3*top_k) 候选
        │
        ▼
ChromaStore.increment_access  storage.py:L170（命中计数，异步）
```

### 2.3 RAG 问答

```
REST POST /api/v1/ask       api.py:L691
MCP ask_kb                  mcp.py:L235
        ▼
KBService.ask               service.py:L601
   ├─ 检索（同 2.2）
   ├─ _build_context        service.py:L772（≤2000 token 字符预算截断）
   ├─ 模式分支：
   │    local/cloud → LLMClient.chat           llm.py:L81
   │    auto       → _ask_auto                 service.py:L639
   │                  ├─ _cache_lookup         L745（余弦≥0.95 直接返回缓存）
   │                  ├─ _classify             L683（本地 LLM 判 SIMPLE/COMPLEX）
   │                  ├─ _is_sensitive         L693（命中敏感 ns → 强制本地）
   │                  └─ COMPLEX+有云 → _answer_complex_cloud L717
   └─ _build_messages       service.py:L791（护栏 prompt：think:false、温度0.2）
```

---

## 3. 模块函数索引（全量）

> 按文件行数降序。`★` = 建议精读，其余知道职责即可。私有函数（`_` 开头）是实现细节，一般不用管。

### 3.1 api.py（873 行）★ — REST 接入 + 应用工厂

| 函数/类 | 行 | 职责 |
|---|---|---|
| `create_app(settings, enable_watcher)` | L473 | **应用工厂**：建 FastAPI、挂中间件、建 KBService 单例挂 `app.state.kb`、挂载 MCP、注册全部路由 |
| `MemoryCreate / MemoryUpdate / SearchRequest / AskRequest / WebIngestRequest` | L160-261 | 请求体 pydantic 模型 + 校验（空白拒绝、身份字段白名单，非法→422） |
| `_wrap_request_log` | L351 | 访问日志中间件（ASGI 最外层） |
| `_wrap_api_key` | L390 | API Key 鉴权中间件（空 key 直通） |
| `_wrap_sse_charset / _wrap_json_charset` | L267/L307 | 响应头补 `charset=utf-8`（修过的中文乱码根因层） |
| `exposure_warning` | L34 | 非回环监听且无鉴权时启动警告 |
| `_parse_log_line / _read_log_tail / _normalize_level` | L63-110 | `/api/v1/logs` 日志查询端点的支撑函数 |

路由清单（全在 `create_app` 内注册）：

```
POST   /api/v1/memories            写记忆          GET  /api/v1/memories           列表
GET    /api/v1/memories/{id}       读单条          PATCH /api/v1/memories/{id}    更新
DELETE /api/v1/memories/{id}       删除            POST /api/v1/search           混合检索
POST   /api/v1/documents           文档入库        GET  /api/v1/documents         文档列表
DELETE /api/v1/documents/{source}  按源删除        POST /api/v1/ingest/web       网页入库
POST   /api/v1/ask                 RAG 问答        GET  /api/v1/audit             存取审计查询
GET    /healthz  /health           健康检查        POST /api/v1/monitor/summary  监控按需摘要
GET    /api/v1/config              配置查看        GET  /api/v1/governance/stats  治理统计
GET    /api/v1/governance/config   治理配置        GET  /api/v1/logs[/events]     日志查询/SSE
GET    /                           看板静态页
```

### 3.2 service.py（798 行）★★ — 业务编排中枢（全项目最该读的文件）

| 函数/类 | 行 | 职责 |
|---|---|---|
| `class KBService` | L125 | 核心类；`__init__` L129 组装 store/embedder/bm25/retriever/llm，BM25 启动优先加载持久化语料 |
| `.add_memory` | L253 | 写记忆（校验→去重→嵌入→入库→建索引→审计） |
| `.get_memory / .update_memory / .delete_memory` | L299/L326/L360 | 单条读/改（变更重新嵌入）/删 |
| `.list_memories / .list_records` | L316/L321 | 过滤分页列表（后者供 watcher/测试内部用） |
| `.add_document / .add_webpage` | L382/L422 | 解析→切分→逐 chunk 建 Record 入库 |
| `.list_documents / .delete_document` | L456/L473 | 按 source 聚合/级联删除 |
| `.search` | L577 | 检索入口（委托 retriever + 审计） |
| `.ask` | L601 | RAG 问答编排（见 2.3） |
| `.stats` | L593 | 运行统计 |
| `._ask_auto / ._classify / ._is_sensitive / ._answer_complex_cloud` | L639-717 | auto 智能路由四件套 |
| `._cache_lookup / ._cache_put` | L745/L761 | 问答缓存（向量相似度命中 + LRU） |
| `._build_context / ._build_messages` | L772/L791 | 上下文截断拼装 + 护栏 prompt |
| `._build_reranker / ._build_sparse / ._sparse_add / ._sparse_remove` | L176-243 | 可选组件组装与写路径维护 |
| `._audit_access / .query_access_audit` | L486/L510 | 审计写入开关与查询 |
| `validate_agent_id / validate_client / validate_project` | L44-85 | 身份字段格式校验（模块级纯函数） |
| `LLMDisabledError` | L98 | LLM 全不可用异常（API 层转 503） |

### 3.3 cli.py（657 行） — 命令行（typer）

| 函数 | 行 | 职责 |
|---|---|---|
| `serve / mcp_stdio` | L378/L410 | 启动 REST 常驻服务 / stdio 模式 MCP |
| `add / search / ask / info / stats` | L225-378 | 终端直写、检索、问答、信息、统计 |
| `forget / dedup` | L424/L472 | 治理维护：陈旧记忆清理、重复对扫描（dry-run 先行） |
| `audit` | L519 | 存取审计查询 |
| `eval_cmd` | L576 | 检索质量评测（独立临时库，不碰生产数据） |
| `resolve_device` | L139 | GPU 选择交互（显式配置 > runtime.json > 交互询问） |
| `is_port_in_use / write_pid_file / remove_pid_file` | L182-213 | 端口预检与 pid 文件（配合 start/stop_kb.bat） |

### 3.4 monitor.py（465 行） — 本地监控 Agent（默认关）

`MonitorAgent`（L369）常驻线程模式；`run_once_summary`（L421）按需摘要；`run_once_dispatch`（L306）异常调度；`_detect_anomalies`（L110）纯函数异常检测；`_fallback_*`（L265/L295）LLM 不可用时降级纯文本。**默认 monitor_enabled=False，维护优先级低。**

### 3.5 retriever.py（283 行）★ — 混合检索核心

| 函数/类 | 行 | 职责 |
|---|---|---|
| `class HybridRetriever.search` | L94 | **融合主流程**（见 2.2），mode=hybrid/vector/keyword |
| `rrf_fuse` | L13 | RRF 融合：score=Σ1/(K+rank) |
| `weighted_fuse` | L48 | min-max 归一加权融合（N32 修复件，保留分数区分度） |
| `_minmax_norm` | L31 | 单路分数归一化到 [0,1] |

### 3.6 mcp.py（274 行） — MCP 8 工具薄封装

8 个工具函数 `write_memory / search_memory / read_memory / update_memory / delete_memory / add_document / add_webpage / ask_kb`（L73-235），全部委托 KBService。`create_mcp_server`（L258）注册工具；`_client_from_ctx / _resolve_client_ctx`（L34/L67）从 MCP 握手自动识别客户端；`_check_identity`（L50）身份校验。**改工具签名=改三处：mcp.py + 设计文档 + 客户端规约。**

### 3.7 audit.py（263 行） — 审计日志

`log_access_event`（L223）存取审计 JSON 行（按 client__project 分文件，按天轮转）；`log_governance_event`（L75）治理操作审计；`_snippet`（L216）内容截断前 50 字符（**敏感红线：不落全文**）；`parse_agent_file_name`（L150）文件名解析。两个 `reset_*` 是测试专用。

### 3.8 sparse.py（238 行） — 稀疏第三路（可选，默认关）

`SparseEmbedder`（L74）：复用 Embedder 编码器 + 独立 sparse 头懒加载；`SparseIndex`（L154）：倒排索引 `{token_id: {record_id: weight}}`，增量维护，save/load 落盘；`aggregate_sparse`（L38）max 聚合纯函数。

### 3.9 consolidation.py（228 行） — 智能归并（可选，默认关）

`consolidate_pair`（L189）对一对记录调本地 LLM 返回归并决策；`consolidate_dry_run`（L215）预览不写库；`detect_conflict`（L76）启发式冲突检测（无 LLM 可用）；`_parse_llm_output`（L149）输出校验。

### 3.10 storage.py（199 行）★ — 存储层

| 函数/类 | 行 | 职责 |
|---|---|---|
| `class VectorStore` | L11 | 抽象接口（预留 P2 可替换；目前只有 ChromaStore 一个实现） |
| `class ChromaStore` | L59 | ChromaDB PersistentClient，余弦空间；`add/get/get_many/delete/delete_by_source/iter_all/list_records/query` |
| `.increment_access` | L170 | 命中计数 +1（检索后异步调用） |
| `.get_many` | L94 | 批量读取（消除检索路径 N+1） |
| `_clean_metadata` | L47 | 写前丢弃 None/空串（Chroma 限制） |

### 3.11 governance.py（188 行） — 治理纯函数（默认全关）

`compute_decay_factor`（L32）/ `apply_decay`（L58）/ `days_since`（L73）访问衰减；`freshness_boost`（L98）新鲜度加权；`check_duplicate`（L157）语义去重 top1 拦截；`compute_stats`（L123）治理统计。**全是纯函数，最好测。**

### 3.12 watcher.py（177 行） — 目录监听（serve 可选挂载）

`KBWatcher`（L47）：watchdog Observer + 后台去抖线程；文件创建/修改去抖入库，删除按 source 级联清理；`_is_temp_file`（L36）过滤 `~$` Office 锁文件等。

### 3.13 i18n.py（158 行） — 双语消息

`detect_lang`（L12）系统语言检测（KB_LANG 可覆盖）；`t(key)`（L90）取消息；`mcp_instructions()`（L155）**MCP server instructions 全文——客户端"挂上即用"的规约就在这里**。

### 3.14 llm.py（153 行）★ — LLM 接入

`LLMClient`（L21）统一接口：`probe`（L43）探测本地 Ollama；`status`（L62）当前模式可用性；`chat`（L81）按 prefer 路由 local/cloud/auto；`_chat_local`（L109）走 Ollama 原生 `/api/chat`（**传 think:false，openai SDK 不透传故不用 SDK**）；`_chat_cloud`（L133）OpenAI 兼容 SDK 延迟导入。

### 3.15 bm25.py（144 行） — 关键词检索

`tokenize`（L8）jieba 搜索引擎分词；`BM25Index`（L19）内存索引，变更同步重建；`add_many/remove_many`（L63/L82）批量只重建一次；`save_corpus/load_corpus`（L117/L126）语料落盘，启动免全量分词。

### 3.16 eval.py（136 行） — 检索评测

`load_dataset`（L30）JSONL 数据集；`run_eval`（L83）端到端跑 Recall@1/@5 + MRR；`compute_metrics`（L57）指标计算。**改检索逻辑后用它量化回归。**

### 3.17 小文件速览

| 文件 | 行数 | 职责 |
|---|---|---|
| config.py | 121 | `Settings` 全部配置（KB_ 前缀环境变量 + .env），`get_settings()` lru_cache 单例 |
| ingest.py | 105 | `parse_file`（PDF/DOCX/markitdown 兜底）→ `chunk_text`（递归切分）；`fetch_webpage`（httpx + trafilatura） |
| models.py | 101 | `Record` 统一模型 ↔ Chroma metadata 互转；`RecordType` 类型枚举 |
| logging_setup.py | 74 | `setup_logging` 控制台+轮转文件双 handler |
| reranker.py | 61 | `Reranker.rerank` 交叉编码精排（懒加载） |
| embedder.py | 92 | `Embedder.embed_texts/embed_query` 向量编码（默认 bge-small-zh-v1.5，可选 BGE-M3；懒加载、离线优先、cuda fp16、失败状态/503 错误） |

---

## 4. 干预指南：我要改 X，该动哪里？

| 场景 | 改这里 | 验证 |
|---|---|---|
| 加/改 REST 端点 | api.py 路由 + service.py 方法 + 请求模型 | 相关 API 测试 |
| 加/改 MCP 工具 | mcp.py + service.py + i18n.py(instructions) + 设计文档 | MCP 相关测试 |
| 检索不准/排序问题 | **retriever.py**（融合策略、候选池大小）；向量路看 embedder/storage.query，关键词路看 bm25.py | `kb eval` + test_n32 回归 |
| 回答质量问题 | service.py `_build_messages`（prompt）/ `_build_context`（预算）/ config.py 护栏参数 | ask 相关测试 + 人工对比 |
| 新增配置项 | config.py `Settings` 加字段（注释写用途），.env.example 补键名 | 配置相关测试 |
| 改入库格式支持 | ingest.py `parse_file` 加分支 | ingest 测试 |
| 改切分策略 | ingest.py `chunk_text`（chunk_size/overlap 在 config） | ingest 测试 + eval |
| 治理开关行为 | governance.py（纯函数）+ service.py 调用点 | governance 测试 |
| 换嵌入/重排模型 | config.py `embed_model`/`rerank_model` + AGENTS.md 硬件约束（6GB 显存红线） | 全量测试 |
| 性能问题 | 先看 storage.py get_many（N+1）、bm25 add_many（批量重建）、缓存 L745 | 基准测试 |

**通用改码流程（AGENTS.md 红线）**：

1. 改哪个模块，先读对应 spec（设计文档）；冲突时先改文档再改码。
2. TDD：先写/跑验收测试（红）→ 实现 → 绿 → 相关测试回归。
3. 小改动只跑相关测试；核心架构变更（service/retriever/storage）跑全量 `pytest tests/`；orchestra/ 永不测。
4. 提交信息中文 `节点NN: 简述` 或 `文档: 简述`；**敏感信息（key、硬件细节、用户名、本地路径）绝不入库**。
5. 依赖只加设计文档第 3 节列出的；禁 LangChain（langchain-text-splitters 除外）；禁 7B+ 本地模型；禁新常驻服务。

## 5. 配置速查（KB_* 环境变量 / .env）

| 键 | 默认 | 说明 |
|---|---|---|
| KB_DATA_DIR | kb_data | 数据根目录（Chroma/语料/稀疏索引都落这） |
| KB_DEVICE | 空=自动 | cpu / cuda 显式覆盖（OOM 时切 cpu） |
| KB_LLM_MODE | **off** | off=纯本地零成本 / local / auto / cloud |
| KB_LLM_MODEL | 空 | 本地 Ollama 模型名（以 `ollama list` 为准） |
| KB_LLM_API_KEY / KB_LLM_BASE_URL / KB_LLM_CLOUD_MODEL | 空/deepseek | 通用云端三键（任意 OpenAI 兼容商） |
| KB_API_KEY | 空 | 非空=启用 Bearer 鉴权 |
| KB_API_HOST / KB_API_PORT | 127.0.0.1:8000 | 监听地址 |
| KB_WATCH_DIR | data | 目录监听（空/"."=不启动） |
| KB_RERANK_ENABLED / KB_SPARSE_ENABLED | false/false | 重排 / 稀疏第三路开关 |
| KB_DECAY/FRESHNESS/DEDUP/CONSOLIDATION_ENABLED | 全 false | 治理开关，默认零行为变化 |
| KB_ACCESS_AUDIT_ENABLED | true | 存取审计开关 |
| KB_MONITOR_ENABLED | false | 监控常驻线程（默认按需触发） |
| KB_CONTEXT_TOKEN_LIMIT / KB_LLM_MAX_TOKENS / KB_LLM_TEMPERATURE | 2000/800/0.2 | 问答护栏 |
| KB_CACHE_SIZE / KB_CACHE_SIM_THRESHOLD | 100/0.95 | 问答缓存 |

## 6. 硬件与运维红线

- 6GB 显存 + 16GB 内存：默认 bge-small-zh-v1.5（百兆级内存）几乎不占显存；显式选 BGE-M3(1.1GB) 时与 qwen3:4b(3.2GB) 可共存；两 LLM 禁止同时加载。
- Ollama 必须从开始菜单/托盘启动，**不能从 AI 沙箱终端拉起**（文件锁继承会坏库）。
- qwen3 系必须 `think:false`（llm.py 已处理，别改丢）。
- HF 下载走 `HF_ENDPOINT=https://hf-mirror.com`；torch 用阿里云镜像 cu128。
- 断网可完整运行是验收标准（模型缓存 + Chroma 数据全本地）。

---

*生成方式：`venv/Scripts/python.exe` AST 扫描 kb/ 全包 + 人工架构整理。函数行号随代码演进会漂移，以符号名为准。*
