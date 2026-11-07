# kb 项目全景导读（2026-11-03）

> 面向第一次接手或准备面试讲解的人。读完全文大约 40 分钟；只求跑起来看效果，读第 0、1、6 节即可。
> 设计依据：`docs/superpowers/specs/2026-08-23-kb-memory-service-design.md`（基础）、
> `2026-10-20-kb-v2.1-design.md`（项目分库）、`2026-11-03-kb-webui-design.md`（本次 Web UI 与长文档）。
> 本文是**导读**，不替代设计文档；两者冲突以 spec 为准。

---

## 0. 一句话定位

**一个本地优先、离线可用的 Agent 记忆与知识服务**：单个 Python 进程（`python -m kb serve`）同时提供
REST、MCP 和浏览器界面，负责记忆存取、文档/网页入库、混合检索（向量 + BM25 归一化加权融合）与 RAG 问答。
不配置 LLM 时，写入、检索、上传全部可用，只有 `/ask` 会返回 503。

三个刻意坚持的边界，面试时可以直接讲：

- **零常驻外部依赖**：不引入 Qdrant / Redis / 消息队列，向量库用嵌入式 Chroma，关键词索引用进程内 BM25 + 落盘语料。
- **无 LLM 也能用**：检索能力不依赖大模型，这决定了它在 6GB 显存的机器上仍然可用。
- **两种身份并存**：既是「Agent 外置记忆」，也是「个人/项目知识库」——同一个 `Record` 模型、同一套检索，靠 `type` 与 `project` 区分。

---

## 1. 三条主线

**入库链路**：文件/网页 → 解析 → 切块 → 向量编码 → Chroma + BM25。

**问答链路**：提问 → 作用域 → 双路召回 → 融合 → 预算截断 → 护栏 prompt → LLM。

**模块分层**：入口 / 编排 / 检索 / 存储 / 基础。

依赖方向只有一个：**入口 → 编排（KBService）→ 检索 → 存储**。基础层被所有人依赖，存储层不反向依赖上层的任何东西。
这条规则是整个项目可测试性的来源——`retriever.py` 只认 `store + bm25 + embedder` 三个接口，所以可以在无 GPU、无 Chroma 的环境里用替身测试。

---

## 2. 模块地图

| 文件 | 行数 | 职责 | 值得注意的点 |
|---|---:|---|---|
| `kb/config.py` | 143 | 全部配置（`KB_*` 环境变量 + `.env`） | `Settings` 是唯一配置源，禁止硬编码 |
| `kb/models.py` | 102 | `Record` 统一模型 + Chroma metadata 互转 | Chroma metadata 不支持 list，`tags` 用逗号拼接 |
| `kb/embedder.py` | 265 | 向量编码、懒加载、空闲卸载 | 模型不可用时抛 `EmbeddingUnavailableError` → 503 |
| `kb/storage.py` | 204 | `ChromaStore`（`VectorStore` 抽象） | 单 client 多 collection；`get_many` 消 N+1 |
| `kb/bm25.py` | 144 | jieba 分词 + BM25 内存索引 | `add_many` 批量只重建一次；语料落盘免启动全量分词 |
| `kb/retriever.py` | 281 | 双路召回 + `weighted_fuse` / `rrf_fuse` | 每路取 `max(30, 3*top_k)` 候选 |
| `kb/service.py` | 1155 | **核心编排**：CRUD、入库、检索、问答、审计、治理接入 | 写锁、作用域合并、`_ask_auto` 路由都在这里 |
| `kb/scopes.py` | 209 | 全局库 / 项目子库的物理隔离 | 所有读写入口必须经过 `get/locate`，禁止绕过 |
| `kb/ingest.py` | 122 | 解析（txt/md/pdf/docx/markitdown）+ 切块 + 网页正文 | 可选依赖缺失时给「装哪个 extras」的明确错误 |
| `kb/tables.py` | — | **v2.4** CSV/XLSX 解析、逐行文本化、确定性表格问答 | 行文本 `列名=值 \| ...`；无 LLM 也能精准答单元格 |
| `kb/llm.py` | 155 | 本地 Ollama / 云端 OpenAI 兼容客户端 | `think:false` 直传；`stream:false` 显式关闭 |
| `kb/api.py` | 1062 | FastAPI 工厂、REST 端点、鉴权/字符集/日志中间件 | 统一错误 JSON `{error, message}` |
| `kb/mcp.py` | 365 | MCP 工具注册（写/查/读/改/删/关联/入库/问答） | 与 REST 共享同一个 `KBService` 单例 |
| `kb/cli.py` | 960 | `kb add/search/ask/serve/eval/forget/dedup/audit/where` | Typer + rich |
| `kb/audit.py` | 263 | 存取审计 JSON（按 client+project 分文件） | 用户的「谁存过什么」入口 |
| `kb/governance.py` | 188 | 衰减 / 去重 / 新鲜度（纯函数） | 默认全关，开启后行为变化点集中 |
| `kb/links.py` | — | 记忆显式关联（SQLite 边） | 一跳检索扩展，默认开但无数据时零变化 |
| `kb/reranker.py` / `kb/sparse.py` | 61 / 238 | 交叉精排 / BGE-M3 稀疏第三路 | 都默认关，失败自动降级双路 |
| `kb/eval.py` | 136 | Recall@1/@5 + MRR 评测 | 配合 `tests/eval_zh_50.jsonl` |
| `kb/static/index.html` | — | **新增**浏览器界面 | 单文件、无构建、离线可用 |

---

## 3. 必看代码与阅读顺序

不要按目录顺序读，按**数据流**读。下面 6 个阶段，每个阶段读完都能回答一个问题——答不上来就回到该阶段的文件。

### 阶段 0：数据长什么样（20 分钟）

1. `kb/models.py` → `Record`、`RecordType`、`to_metadata/from_chroma`
2. `kb/config.py` → `Settings` 全部字段（重点 `chunk_size` / `context_token_limit` / `embed_model`）

**读完应能回答**：一条记忆和一块文档，在数据库里差在哪几个字段？删除一个文档时靠什么字段级联？（答：`type` 与 `source`）

### 阶段 1：请求怎么进来（30 分钟）

3. `kb/api.py` 的 `create_app()`：注意执行顺序——`setup_logging` → `KBService` → 路由 → `mount /ui` → 鉴权中间件 → charset → 访问日志
4. `kb/api.py` 的 `add_document` 端点：multipart 与 JSON 路径两条分支
5. `kb/cli.py` 的 `serve` 与 `add`
6. `kb/mcp.py` 的 `create_mcp_server`

**读完应能回答**：三种入口为什么能共享同一份状态？鉴权中间件为什么包在最外层而不是写成依赖注入？（答：要覆盖 mount 的 `/mcp` 子应用）

### 阶段 2：核心编排（60 分钟，最重要）

7. `kb/service.py` 的 `KBService.__init__`：`scopes`、`embedder`、`llm`、`_write_lock` 怎么组装
8. `add_memory` / `add_document` / `add_webpage`：注意都先算向量、再删旧、再写新（失败不留空库）
9. `_replace_source_locked`：为什么必须在锁内
10. `search`：如何合并「项目子库 + 全局库」并加 `project_boost`
11. `ask`：检索 → `_build_context` → `_build_messages` → `llm.chat`
12. `_ask_auto`：缓存 → 分类 → 敏感覆盖 → 分支

**读完应能回答**：`top_k` 从请求到检索经过哪几层？上下文预算按什么单位截断？（答：字符 = token × 2 粗估）

### 阶段 3：检索为什么准（40 分钟）

13. `kb/retriever.py` 的 `search` 与 `weighted_fuse`
14. `kb/bm25.py` 的 `tokenize` / `add_many` / 语料落盘
15. `kb/storage.py` 的 `query` 与 `get_many`

**读完应能回答**：为什么两路分数不能直接相加？`max(30, 3*top_k)` 解决什么问题？（答：量纲不同需 min-max；避免小 top_k 早截断）

### 阶段 4：底座能力（30 分钟）

16. `kb/embedder.py`：懒加载、`unload_if_idle`、错误转 503
17. `kb/ingest.py`：`parse_file` 分支、`chunk_text` 递归切分、`fetch_webpage`（trafilatura）
18. `kb/tables.py`：CSV/XLSX 解析、`row_to_text`、`query_rows`（确定性表格问答）
19. `kb/service.py` 的 `_table_answer` / `graph`：表格优先作答与图谱节点/边组装
20. `kb/llm.py`：`probe`、`chat`、本地/云端差异（`think:false`、`stream:false`）

**读完应能回答**：为什么服务启动后内存几乎为零？模型没下载时上传会报什么错？（答：懒加载；503 `EMBED_MODEL_UNAVAILABLE`）

### 阶段 5：外围能力（按需挑读）

19. `kb/scopes.py`（隔离与 `locate`）、`kb/audit.py`（审计文件布局）
20. `kb/governance.py`（三个开关的纯函数实现）
21. `kb/links.py`（关联边与一跳扩展）、`kb/consolidation.py`（巩固）
22. `kb/eval.py`（如何量化「准不准」）
23. `kb/watcher.py` / `kb/idle.py` / `kb/discovery.py`（后台与自启，非核心）

---

## 4. 五个必须讲清楚的设计决策

| 决策 | 为什么这么做 | 代价 / 边界 |
|---|---|---|
| 单进程 + 嵌入式 Chroma | 零运维、断网可用、6GB 机器可跑 | 不支持多进程并发写；写操作用 `_write_lock` 串行化 |
| 写入先算向量再删旧 | 嵌入失败时不会留下「文档已删、新块没写」的空窗 | 同一 source 重传会短暂占用双份内存 |
| 混合检索 + 归一化加权 | 语义与精确匹配互补，中文专有名词靠 BM25 兜底 | 两路口径不同，必须先 min-max 再融合 |
| 项目子库物理隔离 | 删目录即删项目记忆，符合「用户能自己掌控数据」 | 检索要合并两个库并处理 boost，逻辑比单库复杂 |
| 默认 `LLM_MODE=off` | 不配置也能用；成本与隐私默认最保守 | `/ask` 需要显式配置才有答案 |

---

## 5. Web UI（2026-11-03 新增）

- 页面：`kb/static/index.html`，挂载在 `/ui`；根路径 `/` 仍是极简导航，多了一条入口。
- 后端零新增数据接口，全部复用：`/api/v1/documents`（上传/列表/删除）、`/api/v1/ingest/web`、
  `/api/v1/search`、`/api/v1/ask`、`/api/v1/healthz`。
- 鉴权：`KB_API_KEY` 非空时，`GET /` 与 `GET /ui*` 免 Key（纯静态、无数据）；页面在「设置」里填 Key 后，
  后续请求自动带 `X-API-Key`。
- 打包：`pyproject.toml` 增加了 `[tool.setuptools.package-data] kb = ["static/*"]`，否则 pip 安装后 `/ui` 404。
- 问答新增 `top_k`（1~50，默认 5），从 `AskRequest` → 端点 → `KBService.ask` → `retriever` 全链路透传。
- **LLM 配置也能在页面里改**：右上角「设置」→ 模式 `off / local / auto / cloud`，
  本地填 Ollama 地址与模型（可一键读取模型列表），云端填 Base URL / 模型 / Key；
  保存后 `KBService.reconfigure_llm` 热重建客户端并清空答案缓存，配置落 `kb_data/llm_config.json`，
  优先级高于 `.env`，重启后沿用。Key 只回显「是否已设置」，改配置接口默认仅本机可调。
- **界面中英双语**：右上角 `EN / 中文` 切换，词条集中在页面脚本的 `I18N` 字典里。
- **不会「假卡死」**：问答最多等 120/180 秒（仅检索/调用 LLM），期间显示已等待秒数；
  超过 5 秒提示「首次查询可能在加载嵌入模型」，超时后明确报错而不是无限转圈。

本地验证：

```powershell
python -m kb serve          # 或 .\venv\Scripts\python.exe -m kb serve
# 浏览器打开 http://127.0.0.1:8000/ui
```

---

## 6. 长文档（以《红楼梦》为例）

体量不是问题：120 回约 73 万字，按 500 字 / 100 重叠 ≈ **1800+ 块**，向量约 3.7MB，CPU 入库约 1–2 分钟。
真正影响「答得准」的是四件事：

1. **按回拆文件**（推荐、零 schema 改动）：把全书拆成 120 个 txt，文件名写成「第三回 林黛玉抛父进京都」。
   `source` 自动变成回目，来源卡片就能直接定位到回，不用改 `models.py`。
2. **切块调大**：`.env` 里设 `KB_CHUNK_SIZE=800`、`KB_CHUNK_OVERLAP=150`（小说对话跨度大，500 字常切断上下文）。
3. **召回与预算**：页面把「召回条数」调到 8~10；`.env` 里 `KB_CONTEXT_TOKEN_LIMIT=4000`。
4. **精排**：`KB_RERANK_ENABLED=true`（bge-reranker-v2-m3 约 2GB，CPU 可跑；6GB 显存红线仍安全）。

### 6.1 一个必须知道的限制：单集合别超过约 800 块

实测（2026-11-03，Windows + chromadb 1.5.0/1.5.9 均复现）：**单个 Chroma 集合超过约 800 条后，
HNSW 索引只在磁盘留下 `index_metadata.pickle`，进程重启读库必失败**（`Error loading hnsw index`）。

| 集合规模 | 重启后能否读回 |
|---|---|
| 800 条 | ✅ |
| 965 条 / 1600 条 / 2337 条 | ❌ `Error loading hnsw index` |

大语料的正确姿势（任选其一）：

- **分项目子库**：如按 30 回一组拆成 4 个 project（每个约 240 块），检索时分别查，
  前端「项目库」切换即可；
- **加大切块**：`KB_CHUNK_SIZE=1800` 时整本红楼梦只有约 950 块，仍偏大，建议配合分库；
- **常驻不重启**：`kb serve` 一次跑到底没问题，但重启前要预期这个报错。

这是 chromadb 侧的限制，不是 kb 的逻辑问题；写进这里是为了避免面试现场重启后「库打不开」。

操作流程：

```powershell
# 1. 建项目子库（留空则进全局库）
#    浏览器里「项目库」填 hongloumeng，然后逐个上传 120 个 txt
# 2. 命令行等价写法
python -m kb serve
curl -F "file=@第三回 林黛玉抛父进京都.txt" -F "project=hongloumeng" http://127.0.0.1:8000/api/v1/documents

# 3. 问一句
curl -X POST http://127.0.0.1:8000/api/v1/ask ^
  -H "Content-Type: application/json" ^
  -d "{\"question\":\"宝玉和黛玉第一次见面在哪一回？\",\"project\":\"hongloumeng\",\"top_k\":10}"
```

想量化「准不准」，照 `tests/eval_zh_50.jsonl` 的格式写你自己的数据集（**每行自带语料，评测在独立临时库中跑，不碰生产数据**）：

```json
{"qid": 1, "question": "宝玉和黛玉第一次见面在哪一回？", "corpus": "第三回 林黛玉抛父进京都……", "tags": ["hongloumeng", "semantic"]}
```

然后：

```powershell
python -m kb eval --file tests/eval_hongloumeng.jsonl --top-k 10 --json eval_report.json
python -m kb eval --file tests/eval_hongloumeng.jsonl --top-k 10 --rerank   # 对比精排收益
```

注意两个不可逆点：**换嵌入模型要重建库**（维度不兼容；服务会自动沿用建库模型并告警），
**改 `chunk_size` 后要重新入库**（旧块不会自动重切）。

---

## 7. 测试与验证

```powershell
pytest tests/ -q          # 全量：536 passed, 1 skipped（2026-11-03 实测，约 3 分钟）
pytest tests/test_n33_web_ui.py -q   # Web UI 的 8 项验收
pytest tests/test_n34_bulk_ingest.py -q  # 批量导入的 3 项验收
pytest tests/test_n35_llm_config.py -q   # 运行时 LLM 配置的 8 项验收
pytest tests/test_n37_tables.py -q       # v2.4 表格导入与精准问答
pytest tests/test_n38_graph.py -q        # v2.4 知识图谱接口
pytest tests/test_n39_separation.py -q   # v2.4 RAG 与记忆分离（不遗忘）
```

计数口径：`pytest tests/ --collect-only -q | Select-Object -Last 1` 会打印「537 tests collected」。
如果你的环境跑出别的数字，先看收集数——差异通常来自依赖缺失或不在同一 commit，而不是测试本身少跑。

唯一的 1 项跳过是 `tests/test_v21_runtime.py` 的「Linux 开机自启」用例（Windows 下启动项用 `.cmd` 实现），
属于平台差异而非缺陷。

测试分层值得记：`test_n01…n33` 按节点编号，`conftest.py` 的 `env_isolated` 给每个用例独立的 `KB_DATA_DIR`
并把 Ollama 指向不可达端口，因此**测试不依赖本机是否装了模型或开了 Ollama**。

---

## 8. 面试可讲的 10 个点

1. 单进程同时提供 REST + MCP + Web UI，三端共享一个 `KBService` 单例，状态一致性怎么保证。
2. 混合检索为什么不能直接加权求和：两路分数量纲不同，先 min-max 再按生效路数均权。
3. `max(30, 3*top_k)` 候选窗：小 `top_k` 时防止相关记录被早截断。
4. 写入顺序「先算向量 → 再删旧 → 再写新」如何避免半失败状态。
5. Chroma 写入与命中计数必须串行化，所以 `_write_lock` 覆盖读路径里的「写计数」这一步。
6. 项目隔离用物理子库（目录即边界），检索时「子库 + 全局」合并并加 boost。
7. 懒加载 + 空闲卸载，让服务启动后常驻内存趋零；模型不可用时把错误翻译成 503 而不是 500。
8. 默认关 LLM 的产品判断：检索能力独立于生成能力，成本与隐私默认最保守。
9. 审计与隐私护栏：敏感 namespace 强制本地；`/ask` auto 路由先查缓存再分类。
10. 长文档 RAG 的三个真实瓶颈：切块粒度、召回条数、上下文预算——对应可调的三个参数。

---

## 9. 常见坑

- **改完代码服务没生效**：`kb` 命令只装在 venv 里，新终端要先激活；或直接用 `python -m kb`。
- **`pytest` 用的是全局 Python 而不是 venv**：两者依赖版本可能不同，报错先确认 `sys.executable`。
- **首次上传很慢**：正在下载 `bge-small-zh-v1.5`（约 92MB），之后离线可用。
- **`/ask` 503**：`KB_LLM_MODE=off` 是默认值，不是故障；前端勾「仅检索」可先验证召回。
- **改嵌入模型后检索变差**：库里已有数据时会沿用建库模型，想真正切换必须重建库。
- **导入整本书后重启报 `Error loading hnsw index`**：单集合超过约 800 块会触发，见 6.1，改用分项目子库。
- **`kb import` 比逐个上传快得多**：226 个文件 2337 块从 367s 降到批量的一次重建；视觉上就是少了一堆「重建 BM25」。
- **首次查询要等十几秒**：`kb_data/runtime.json` 记录 cuda、但 torch 是 CPU 版时，旧版本会退回在线加载并卡在
  HuggingFace 重试；现在 `KBService` 会自动回退 cpu，且在线兜底超时已收紧。换 `bge-small-zh` 可把冷启动从
  ~23s / 2GB RSS 降到几秒 / 数百 MB（需重建库）。


