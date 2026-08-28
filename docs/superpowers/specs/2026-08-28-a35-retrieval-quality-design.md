# A3.5 检索质量实施设计（reranker / 稀疏向量 / 评测基准 / N+1 修复）

- 日期：2026-08-28
- 状态：设计书（先文档后代码），随本 spec 逐节点 TDD 实施
- 依据：ROADMAP.md A3.5 节（评估报告第二优先级）；`kb/retriever.py`（RRF 双路检索 + 循环内逐条 `store.get`）；`kb/bm25.py`（纯内存索引，启动全量重建）；`kb/storage.py`（ChromaStore，无批量 get）；`kb/embedder.py`（BGE-M3 sentence-transformers 加载）
- 技术验证（2026-08-28 实测，本机）：
  - 本地 BGE-M3 快照为 sentence-transformers 格式（`pytorch_model.bin`，键前缀 `embeddings/encoder/pooler`），**不含 sparse 头**；sparse 头在独立文件 `sparse_linear.pt`（3.5KB，`{weight:(1,1024), bias:(1,)}` fp16，已下载入 HF 缓存）——三路检索可零新依赖实现
  - sentence-transformers 5.6.0 `CrossEncoder` 可用（bge-reranker-v2-m3 重排零新依赖；模型 ~2.3GB fp32 / ~600MB fp16 显存）
  - FlagEmbedding **不引入**（违反零新依赖约定；transformers 直载等价可达）

## 1. 需求

### 1.1 问题（评估报告结论）

- 纯双路 RRF 排序精度有上限：向量+BM25 融合后无交叉精排，细粒度相关性判断缺失
- BGE-M3 的稀疏（learned lexical）能力闲置：当前仅用稠密向量，语义+词法互补性未释放
- 检索质量**不可量化**：无评测基准，任何改动（衰减/rerank/稀疏）对排序的影响无客观数据
- 性能债：检索路径循环内逐条 `store.get(rid)`（N+1 查询，每条一次 Chroma 调用）；BM25 启动全量重建（jieba 分词随规模线性变慢）

### 1.2 目标

- reranker：RRF 融合后对 top-N 候选交叉重排（bge-reranker-v2-m3），显著提升首位命中
- 稀疏向量：BGE-M3 三路检索（稠密+BM25+learned sparse）RRF 融合，与稠密共享编码器（显存零增加）
- 评测基准：50 条中文 QA 固定数据集 + Recall@k / MRR 指标 + `kb eval` CLI，检索质量可量化回归
- N+1 修复：`store.get_many` 批量取记录；BM25（及稀疏索引）语料持久化，启动免全量分词

### 1.3 非目标（YAGNI）

- 不做 ColBERT 多向量检索（延迟与存储成本高，个人级收益不明）
- 不做评测数据集扩充到百条以上 / 引入公开基准（MIRACL 等）——先最小可用
- 不改 RRF 融合公式本身（K=60 维持），只扩路数
- 不做 reranker 量化/蒸馏（显存预算内够用）

## 2. 架构

### 2.1 检索管道（改造后）

```
query ──┬─ 稠密路：embed → Chroma top-N（N = 3 × top_k）
        ├─ 关键词路：jieba → BM25 top-N
        └─ 稀疏路（KB_SPARSE_ENABLED）：sparse encode → SparseIndex top-N
                     ↓
       RRF 融合（2 或 3 路）：score(d) = Σ 1/(60 + rank_i(d))
                     ↓
       候选 = 前 rerank_top_n 条（默认 20）
                     ↓
       [KB_RERANK_ENABLED] CrossEncoder(query, content) 重排 → top_k
       [关闭]                    直接截断 → top_k
                     ↓
       治理重排（衰减/新鲜度，A3 已有）→ 批量取记录（get_many）→ 过滤 → 返回
```

### 2.2 模块与依赖方向

```
kb/
├── reranker.py     # 新增：CrossEncoder 封装（懒加载，fp16 cuda）
├── sparse.py       # 新增：SparseEmbedder（共享编码器+sparse头）+ SparseIndex（倒排）
├── retriever.py    # 改造：多路 RRF + rerank 挂接 + get_many 批量取
├── storage.py      # 扩展：get_many(ids) 批量读取
├── bm25.py         # 扩展：语料持久化（save/load + id 集合校验）
├── eval.py         # 新增：评测框架（数据集加载/指标计算/报告）
├── service.py      # 改造：组装 sparse/reranker；写入路径同步稀疏索引
└── cli.py          # 扩展：eval 命令
```

依赖方向不变：`api/mcp/cli → service → (storage, retriever, sparse, reranker, ingest, llm) → (config, models)`。

### 2.3 三层质量能力与开关（默认全关 = 零行为变化）

| 能力 | 开关 | 默认 | 生效位置 |
|---|---|---|---|
| 稀疏第三路 | `KB_SPARSE_ENABLED` | false | RRF 融合前 |
| 交叉重排 | `KB_RERANK_ENABLED` | false | RRF 融合后、截断前 |
| 评测基准 | 无开关（显式 CLI 触发） | — | 独立进程命令 |

## 3. 组件设计

### 3.1 reranker（kb/reranker.py）

```python
class Reranker:
    """CrossEncoder 封装；构造存参数，首次 rerank 才加载模型（同 Embedder 模式）。"""
    def __init__(self, model_name, device="cpu")
    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]
```

- 模型：`BAAI/bge-reranker-v2-m3`（XLMRoberta cross-encoder，中英双语）
- 加载：`sentence_transformers.CrossEncoder(model_name, device=..., max_length=512)`；cuda 下 fp16（`model.half()`）
- 打分：`predict([(query, c["content"]) for c in candidates])` → sigmoid 不需要（排序只用相对大小，raw score 即可）
- `rerank` 返回按新分数降序的前 top_k，每条带 `rerank_score` 字段
- 失败降级：加载或打分异常 → 记 WARNING，返回原顺序截断（检索不中断）

### 3.2 稀疏向量（kb/sparse.py）

**SparseEmbedder（共享编码器，显存零增加）**：

```python
class SparseEmbedder:
    """BGE-M3 稀疏编码：复用 Embedder 的 SentenceTransformer 底层编码器 + 独立加载 sparse_linear.pt。"""
    def __init__(self, embedder: Embedder, model_name: str)
    def encode(self, texts: list[str]) -> list[dict[int, float]]   # {token_id: weight}
```

- 编码器复用：`embedder._model[0].auto_model`（SentenceTransformer 首模块 Transformer 的 XLMRobertaModel）与 `.tokenizer`——**不二次加载 2GB 编码器**；内部结构探测失败（非 BGE-M3 族模型）抛 `SparseUnavailableError`
- sparse 头：`hf_hub_download(model_name, "sparse_linear.pt")` 加载 Linear(1024, 1)；文件缺失（如 bge-small-zh）→ `SparseUnavailableError`
- 计算（对齐 FlagEmbedding BGEM3 语义）：
  ```
  h = encoder(**tokens).last_hidden_state          # (b, seq, 1024)
  w = relu(sparse_linear(h)).squeeze(-1)           # (b, seq)
  按 input_id 聚合：同 id 多位置取 max → {token_id: weight}
  ```
- 归一化：每条稀疏向量 L2 归一化（‖v‖=sqrt(Σw²)），查询与文档同规则

**SparseIndex（倒排索引）**：

```python
class SparseIndex:
    def add(self, record_id: str, sparse_vec: dict[int, float])   # 单条
    def remove(self, record_id: str)
    def rebuild(self, items: list[tuple[str, dict[int, float]]])  # 全量
    def search(self, query_vec: dict[int, float], top_n: int) -> list[tuple[str, float]]
```

- 倒排：`{token_id: {record_id: weight}}`；add/remove 增量维护（无重建成本）
- 打分（归一化后的点积即余弦）：
  ```
  score(q, d) = Σ_{tid ∈ q∩d} q_w[tid] × d_w[tid]
  ```
- 持久化：`data_dir/sparse_index.json`（`{record_id: {token_id: weight}}`），启动优先加载 + id 集合与 Chroma 一致性校验，不一致全量重建（见 §3.4）

**降级链**：SparseEmbedder 初始化失败（非 BGE-M3 / 文件缺失 / 加载异常）→ service 记 WARNING，稀疏路自动关闭，检索退回双路（行为等同 sparse_enabled=false），服务正常启动。

### 3.3 多路 RRF（retriever.py 改造）

- `rrf_fuse(*ranked_lists, top_k)` 泛化为可变参数（2 或 3 路，路数由开关决定）
- 融合候选 `candidate = 3 * top_k` 不变；稀疏路开启时三路各取 candidate 条融合
- 治理重排（衰减/新鲜度）应用点不变，仍在融合之后、rerank 之前（治理是软信号，rerank 是精排，精排最后）
- **rerank 挂接点**：治理重排后取前 `rerank_top_n`（默认 20）条送 Reranker，输出截断 top_k；关闭时直接截断（现行为）

### 3.4 N+1 修复与索引持久化（storage.py / bm25.py / retriever.py）

**get_many（storage.py）**：

```python
def get_many(self, record_ids: list[str]) -> dict[str, Record]:
    """批量读取（单次 Chroma get）；不存在的不在返回 dict 中。"""
```

retriever 两处循环取记录（治理重排循环、结果组装循环）改为一次 `get_many` 后内存处理。

**BM25 持久化（bm25.py）**：

- `save_corpus(path)`：`{record_id: tokens}` 序列化 JSON
- `load_corpus(path, valid_ids) -> bool`：文件存在且 id 集合 ⊆ valid_ids（与 Chroma 一致）→ 加载 tokens 重建 BM25Okapi，返回 True；否则 False（调用方走全量分词重建）
- service 启动：`data_dir/bm25_corpus.json` 存在且校验通过 → 免分词加载；否则全量重建后落盘
- 写入路径（add/remove/update/delete）同步维护内存索引并**异步防抖落盘**（简单实现：变更后延迟 5s 单线程写盘，进程退出前 flush；个人级规模直接同步写也可接受，取同步写，避免复杂度）

**稀疏索引持久化**：同模式（`sparse_index.json`），仅在 sparse 启用时读写。

### 3.5 评测基准（kb/eval.py）

**数据集**（`tests/eval_zh_50.jsonl`，随仓库分发）：

```jsonl
{"qid": 1, "question": "kb 服务的默认监听地址是什么？", "corpus": "kb 服务默认监听 127.0.0.1:8000，可通过 KB_API_HOST/KB_API_PORT 配置覆盖。", "tags": ["eval"]}
```

- 50 条中文 QA：corpus 为入库语料（每条独立成块），question 覆盖三类难度——关键词直配（BM25 路）/ 语义改写（向量路）/ 干扰项竞争（考验融合与精排）
- 语料主题混合：kb 项目知识、通用常识、虚构事实（防 LLM 先验污染，只考检索）

**评测流程（eval.py）**：

```
独立 KB_DATA_DIR（评测进程专用，不碰生产库）
  → 导入 50 条 corpus（记录 id 映射）
  → 逐条 question 检索（指定 mode/top_k，治理默认关）
  → 命中判定：corpus 记录 id ∈ top-k 结果
  → 指标：Recall@1 / Recall@5 / MRR = mean(1/rank)
  → 输出报告（JSON + 终端表格）：总体指标 + 分难度指标 + 未命中清单
```

**CLI**：`kb eval --file tests/eval_zh_50.jsonl --top-k 5 --mode hybrid [--rerank] [--sparse]`
- `--rerank/--sparse` 临时覆盖对应开关（对比开关前后指标，量化收益）
- 退出码 0（评测完成即成功，不设指标硬门槛——基线值首次实测后记入本 spec）

## 4. 显存与性能预算

| 项 | 预算 | 说明 |
|---|---|---|
| BGE-M3 稠密 fp16 | ~1.1GB | 现状 |
| BGE-M3 稀疏头 | ~0（共享编码器） | sparse_linear 1024×1，可忽略 |
| reranker fp16 | ~600MB | 与 embedding 同 device；总量 1.1+0.6+3.2(qwen3:4b)=4.9GB < 6GB ✅ |
| 检索延迟 | hybrid < 500ms 维持 | rerank 增量：20 对 × CrossEncoder，CPU ~200ms / GPU ~30ms（实测记入收口报告） |
| 启动 | BM25 免分词 | 持久化命中时启动从"全量 jieba"降为"JSON 加载" |

## 5. 里程碑拆分（N24-N27，每个可独立交付）

| 节点 | 内容 | 门禁 |
|---|---|---|
| **N24** | reranker.py + retriever 挂接 + 配置 + 单测（mock CrossEncoder）+ 真机延迟实测 | 标准 |
| **N25** | sparse.py（SparseEmbedder+SparseIndex）+ 三路 RRF + service 组装 + 持久化 + 单测（mock 稀疏向量）| 标准 |
| **N26** | eval.py + 50 条数据集 + `kb eval` CLI + 基线跑分（全组合：双路/三路/±rerank）| 标准 |
| **N27** | get_many 批量 + BM25 语料持久化 + retriever N+1 消除 + 回归 | 标准 |

实施顺序：N27（纯性能修复，先还债）→ N24 → N25 → N26（评测最后做，可量化验证 N24/N25 收益）。

## 6. 测试策略

### 6.1 单元测试（mock 模型，不加载真实权重）

```python
# N24
def test_rerank_default_disabled():          # rerank_enabled=false → 检索结果与现行为完全一致（回归锚点）
def test_rerank_reorders_candidates():       # mock Reranker 打分逆转顺序 → 输出按新分数降序
def test_rerank_top_n_limits_candidates():   # 融合候选超 rerank_top_n 时只精排前 N 条
def test_rerank_failure_falls_back():        # reranker 抛异常 → 原顺序截断返回（不中断）

# N25
def test_sparse_agg_same_token_max():        # 同 token_id 多位置取 max 聚合
def test_sparse_index_search_dot_product():  # 倒排点积打分 + L2 归一化正确性
def test_sparse_index_add_remove():          # 增量维护
def test_three_way_rrf_fuses():              # 三路排名融合结果正确（构造固定排名）
def test_sparse_unavailable_degrades():      # SparseUnavailableError → 双路照常工作

# N27
def test_get_many_batch():                   # 批量读取含存在/不存在混合 id
def test_bm25_corpus_roundtrip():            # save→load 后检索行为一致
def test_bm25_corpus_id_mismatch_rebuilds(): # 持久化 id 集合与库不一致 → 全量重建
```

### 6.2 集成测试

- 稀疏路端到端（bge-small-zh 无 sparse 头）：mock SparseEmbedder.encode 返回手工向量，走 service 完整检索路径断言三路生效
- 评测框架：用 5 条迷你数据集跑 `run_eval`，断言 Recall/MRR 数值正确（手工可算）

### 6.3 真机验收（AI 执行，报告入收口文档）

- `kb eval` 全组合基线：{双路, +稀疏, +rerank, 三路+rerank} × {cpu, cuda} 的 Recall@1/@5/MRR 与延迟
- reranker 真实加载（bge-reranker-v2-m3）延迟实测：20 候选精排耗时
- 启动耗时对比：BM25 持久化命中前后

## 7. 配置项清单

| 配置项 | 默认 | 说明 |
|---|---|---|
| `KB_RERANK_ENABLED` | false | 交叉重排开关（默认关，零行为变化） |
| `KB_RERANK_MODEL` | `BAAI/bge-reranker-v2-m3` | 重排模型 |
| `KB_RERANK_TOP_N` | 20 | 参与精排的融合候选数 |
| `KB_SPARSE_ENABLED` | false | 稀疏第三路开关（默认关） |

- 全部走 `kb/config.py` + `.env`；`.env.example` 补键名
- 默认全关：升级零行为变化（延续 A3 惯例）

## 8. 风险与避坑

| 风险 | 规避 |
|---|---|
| reranker 首次下载 2.3GB（国内网络慢） | HF_ENDPOINT 镜像 + `HF_HUB_DISABLE_XET=1`（hf-mirror 的 Xet CAS 401 实测坑）；断网时 rerank 关闭不影响核心功能 |
| CrossEncoder 显存 OOM（6GB 卡叠 qwen3:4b） | 显存预算 4.9GB < 6GB；OOM 时文档指引 `KB_RERANK_ENABLED=false` 或 LLM 换 1.7b |
| SentenceTransformer 内部结构依赖（`model[0].auto_model`） | hasattr 探测 + SparseUnavailableError 降级；sentence-transformers 升级破坏时稀疏路自动关闭，双路不受影响 |
| 稀疏索引与 Chroma 漂移（外部直改库） | 启动 id 集合一致性校验，不一致全量重建；写入路径同步维护 |
| BM25 持久化文件损坏 | JSON 解析失败按未命中处理，全量重建（安全降级） |
| 评测数据集泄露进生产库 | 评测独立 KB_DATA_DIR（env 隔离），进程退出数据留存本地（不删，便于复跑） |
| rerank 拖慢交互 | 默认关 + top_n=20 上限；延迟实测不达标（>500ms 总预算）在收口报告标注建议 |
