# kb 开发节点计划（串行门禁制）

> **给开发 AI**：本计划与设计文档 `docs/superpowers/specs/2026-08-23-kb-memory-service-design.md` 配套使用——设计文档定义"做什么"，本计划定义"按什么顺序做、做到什么程度算完成"。开始任何节点前，先完整阅读设计文档与 AGENTS.md。

**目标**：将设计文档落地为可运行的本地 Agent 记忆与知识服务（`python -m kb serve`，REST + MCP）。

**架构**：单进程 FastAPI 应用；`api/mcp/cli → service → (storage, retriever, ingest, llm) → (config, models)` 单向依赖；ChromaDB 嵌入式存储 + BGE-M3 向量 + BM25 关键词 + RRF 融合；LLM 智能路由（本地 qwen3:4b 优先，云端 DeepSeek 外援）。

**技术栈**：Python 3.10、FastAPI、ChromaDB、sentence-transformers、rank_bm25 + jieba、typer、httpx、mcp SDK、pydantic-settings、pytest。

---

## 总则（必读，违反即视为节点未完成）

1. **串行推进** N1 → N16。禁止并行开发多个节点；上一节点门禁未通过（测试全绿 + 提交 + tag + 人工确认），不得开始下一节点。
2. **门禁三件套**：① 该节点全部验收测试通过；② `pytest tests/ -v` 全量回归通过（防止破坏已完成节点）；③ git 提交（信息格式 `节点NN: 简述`）并打 tag `node-NN`。
3. **验收测试代码以本计划为准**，落地到 `tests/test_nNN_*.py` 时必须与计划一致；开发 AI 不得修改验收测试（发现测试缺陷 → 报告文档/测试 AI 修正）。开发 AI 可自由补充单元测试（`tests/unit/`）。
4. **实现与文档冲突**：停下，报告，等文档修订后再继续（AGENTS.md 第 0 节）。
5. **敏感数据红线**：任何密钥、token、密码严禁写入代码、配置模板、文档、提交记录。`.env` 不入库（`.gitignore` 已排除）。
6. **运行方式**：统一用项目 venv：`.\venv\Scripts\python.exe -m pytest tests/ -v`。Embedding 相关测试通过环境变量 `KB_EMBED_MODEL=BAAI/bge-small-zh-v1.5`（约 100MB 测试专用小模型，勿在生产配置默认值中改动）；首次下载需 `HF_ENDPOINT=https://hf-mirror.com`。
7. 每个节点的"契约"小节定义了模块间接口签名，**实现必须与契约一致**（后续节点按此调用）；如确需偏离，先在节点提交说明中写明理由并获人工确认。

## 节点总览

| 节点 | 内容 | 主要交付 | 里程碑 |
|---|---|---|---|
| N1 | 项目骨架与配置系统 | kb/ 包、config.py、.env.example、requirements.txt、pytest 基建 | M1 |
| N2 | 数据模型 | models.py（Record） | M1 |
| N3 | 嵌入器 | embedder.py（BGE-M3 延迟加载） | M1 |
| N4 | 存储层 | storage.py（VectorStore 抽象 + ChromaStore） | M1 |
| N5 | BM25 索引 | bm25.py（jieba 分词 + BM25） | M1 |
| N6 | 混合检索与服务编排 | retriever.py（RRF）、service.py、cli.py | M1 |
| N7 | REST API 骨架 | api.py（memories CRUD + healthz + 错误处理） | M2 |
| N8 | 检索端点与设备检测 | /search、/documents 列表删除、设备检测交互、serve 启动 | M2 |
| N9 | LLM 接入层 | llm.py（探测、模式解析、护栏参数、双客户端） | M3 |
| N10 | /ask 基础 RAG | 检索→拼装→生成→sources，上下文预算 | M3 |
| N11 | 智能路由 | 复杂度分类、上下文压缩、答案缓存、隐私隔离 | M3 |
| N12 | MCP 服务 | mcp.py（8 个 tools 挂载） | M3 |
| N13 | 文档摄取 | ingest.py 文档解析、切分、POST /documents | M4 |
| N14 | 网页摄取 | /ingest/web（httpx + trafilatura） | M4 |
| N15 | 目录监听 | watcher.py（去抖、删除级联） | M4 |
| N16 | 收尾与清理 | 删旧代码、重写 README、全量回归 | 全局 |

---

## N1：项目骨架与配置系统

**目标**：建立 `kb/` 包、全部配置项与测试基建，使后续每个节点都能跑 pytest。

**交付物**：
- `kb/__init__.py`（空）、`kb/config.py`
- `.env.example`（全部配置键名 + 空值/默认值注释，**不含任何真实密钥**）
- `requirements.txt`（设计文档第 3 节全部依赖；torch 已装勿重复指定版本）
- `tests/conftest.py`、`tests/test_n01_config.py`
- `pytest.ini`（testpaths=tests、markers：integration）

**契约**（`kb/config.py`）：

```python
from functools import lru_cache
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    """全部配置；环境变量前缀 KB_，支持 .env 文件。"""
    model_config = SettingsConfigDict(env_prefix="KB_", env_file=".env", extra="ignore")

    data_dir: Path = Path("kb_data")            # 运行数据根目录
    device: str = ""                            # 空=自动检测（见 N8）；显式设 cpu/cuda 覆盖
    embed_model: str = "BAAI/bge-m3"
    llm_mode: str = "auto"                      # local | auto | cloud
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "qwen3:4b"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-flash"
    chunk_size: int = 500
    chunk_overlap: int = 100
    watch_dir: Path = Path("data")
    api_host: str = "127.0.0.1"
    api_port: int = 8000
    context_token_limit: int = 2000             # /ask 检索上下文 token 硬上限
    llm_max_tokens: int = 800
    llm_temperature: float = 0.2
    cache_size: int = 100                       # /ask 答案缓存条数（LRU）
    cache_sim_threshold: float = 0.95           # 缓存命中相似度阈值
    sensitive_namespaces: str = ""              # 逗号分隔，敏感 namespace 强制本地

    @property
    def chroma_dir(self) -> Path: ...           # data_dir / "chroma"
    @property
    def runtime_file(self) -> Path: ...         # data_dir / "runtime.json"
    @property
    def sensitive_ns_list(self) -> list[str]: ...  # 拆分为列表，空串→[]

@lru_cache
def get_settings() -> Settings:
    """全局配置单例；测试用环境变量隔离时调用 get_settings.cache_clear()。"""
```

**验收测试**（`tests/conftest.py`）：

```python
import os
import pytest

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
# 全部测试统一使用小模型，避免下载 2GB 的 BGE-M3
os.environ["KB_EMBED_MODEL"] = "BAAI/bge-small-zh-v1.5"

@pytest.fixture
def env_isolated(monkeypatch, tmp_path):
    """每个测试独立的 KB_DATA_DIR，并清掉配置单例缓存。"""
    from kb import config
    monkeypatch.setenv("KB_DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    yield tmp_path
    config.get_settings.cache_clear()
```

**验收测试**（`tests/test_n01_config.py`，落地时保持一致）：

```python
from pathlib import Path


def test_默认配置(env_isolated):
    from kb.config import Settings
    s = Settings()
    assert s.llm_mode == "auto"
    assert s.llm_model == "qwen3:4b"
    assert s.llm_temperature == 0.2
    assert s.llm_max_tokens == 800
    assert s.context_token_limit == 2000
    assert s.chroma_dir == Path(env_isolated) / "chroma"
    assert s.runtime_file == Path(env_isolated) / "runtime.json"
    assert s.sensitive_ns_list == []


def test_环境变量覆盖(env_isolated, monkeypatch):
    from kb.config import Settings
    monkeypatch.setenv("KB_LLM_MODE", "local")
    monkeypatch.setenv("KB_LLM_MODEL", "qwen3:1.7b")
    monkeypatch.setenv("KB_SENSITIVE_NAMESPACES", "private,diary")
    s = Settings()
    assert s.llm_mode == "local"
    assert s.llm_model == "qwen3:1.7b"
    assert s.sensitive_ns_list == ["private", "diary"]


def test_单例与环境模板(env_isolated):
    from kb.config import get_settings
    assert get_settings() is get_settings()
    example = Path(".env.example")
    assert example.exists()
    for line in example.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            # 模板只允许「键=空值」或注释，禁止出现真实密钥值
            key, _, value = line.partition("=")
            assert key.startswith("KB_") and value == "", f".env.example 含非空值: {line}"
```

**门禁**：上述测试全绿；`pip install -r requirements.txt` 可执行（不含 torch 版本钉死）；tag `node-01`。

---

## N2：数据模型

**目标**：Record 模型与 Chroma metadata 的互转（Chroma metadata 不支持 list，tags 用逗号拼接存储）。

**交付物**：`kb/models.py`、`tests/test_n02_models.py`

**契约**：

```python
from datetime import datetime
from enum import Enum
from uuid import uuid4
from pydantic import BaseModel, Field

class RecordType(str, Enum):
    MEMORY = "memory"
    DOC_CHUNK = "doc_chunk"
    WEB_CHUNK = "web_chunk"

class Record(BaseModel):
    id: str = Field(default_factory=lambda: uuid4().hex)
    content: str
    type: RecordType = RecordType.MEMORY
    namespace: str = "default"
    source: str | None = None
    tags: list[str] = Field(default_factory=list)
    importance: float = 0.5
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat())

    def to_metadata(self) -> dict:
        """content 不进 metadata（它是 Chroma 的 document）；tags 逗号拼接。"""

    @classmethod
    def from_chroma(cls, record_id: str, document: str, metadata: dict) -> "Record":
        """从 Chroma 的 id/document/metadata 还原；tags 按逗号拆分。"""
```

**验收测试**（`tests/test_n02_models.py`）：

```python
def test_往返序列化():
    from kb.models import Record, RecordType
    r = Record(content="张三的生日是3月15日", type=RecordType.MEMORY,
               tags=["人物", "生日"], source="test")
    meta = r.to_metadata()
    assert "content" not in meta
    assert meta["tags"] == "人物,生日"
    assert meta["type"] == "memory"
    r2 = Record.from_chroma(r.id, r.content, meta)
    assert r2 == r  # 全字段一致（含时间戳）


def test_默认值():
    from kb.models import Record, RecordType
    r = Record(content="x")
    assert r.type is RecordType.MEMORY
    assert r.namespace == "default"
    assert r.importance == 0.5
    assert r.tags == [] and r.source is None
    assert len(r.id) == 32  # uuid4().hex
```

**门禁**：测试全绿；tag `node-02`。

---

## N3：嵌入器

**目标**：BGE-M3 延迟加载 + 设备支持 + 归一化向量输出。实例化不加载模型，首次 embed 才加载（服务启动不阻塞）。

**交付物**：`kb/embedder.py`、`tests/test_n03_embedder.py`（marker=integration）

**契约**：

```python
class Embedder:
    def __init__(self, model_name: str, device: str = "cpu"):
        """只存参数不加载模型；_model 延迟到首次使用。"""
    def _ensure_loaded(self):
        """SentenceTransformer(model_name, device)；cuda 时 model.half()。"""
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """批量编码，normalize_embeddings=True（单位向量）。"""
    def embed_query(self, query: str) -> list[float]:
        """单条查询编码，同上归一化。"""
```

**验收测试**（`tests/test_n03_embedder.py`；conftest 已把 KB_EMBED_MODEL 指向 bge-small-zh-v1.5，512 维）：

```python
import pytest
import math

pytestmark = pytest.mark.integration


def test_延迟加载与维度归一化(env_isolated):
    from kb.embedder import Embedder
    from kb.config import get_settings
    e = Embedder(get_settings().embed_model, device="cpu")
    assert e._model is None            # 未使用不加载
    vecs = e.embed_texts(["苹果手机多少钱", "今天天气不错"])
    assert len(vecs) == 2 and len(vecs[0]) == 512
    for v in vecs:
        assert math.isclose(sum(x * x for x in v), 1.0, rel_tol=1e-3)  # 单位向量


def test_相似语义更近(env_isolated):
    from kb.embedder import Embedder
    from kb.config import get_settings
    e = Embedder(get_settings().embed_model, device="cpu")
    q = e.embed_query("机器学习入门教程")
    hits = e.embed_texts(["深度学习与神经网络基础", "今天中午吃什么"])
    def cos(a, b): return sum(x * y for x, y in zip(a, b))
    assert cos(q, hits[0]) > cos(q, hits[1])  # 语义相近者余弦更高
```

**门禁**：测试全绿（integration 也必须绿，允许首次运行下载小模型）；tag `node-03`。

---

## N4：存储层

**目标**：VectorStore 抽象接口 + ChromaDB 实现（余弦空间、按 source 级联删除、列表过滤分页）。

**交付物**：`kb/storage.py`、`tests/test_n04_storage.py`（marker=integration）

**契约**：

```python
from abc import ABC, abstractmethod
from collections.abc import Iterator

class VectorStore(ABC):
    """预留 P2 可替换接口；本期只有 ChromaStore 一个实现。"""

    @abstractmethod
    def add(self, records: list[Record], embeddings: list[list[float]]) -> None: ...
    @abstractmethod
    def get(self, record_id: str) -> Record | None: ...
    @abstractmethod
    def delete(self, ids: list[str]) -> None: ...
    @abstractmethod
    def delete_by_source(self, source: str) -> int:
        """删除该 source 的全部记录，返回删除数量。"""
    @abstractmethod
    def iter_all(self) -> Iterator[Record]:
        """全量遍历（BM25 启动重建用）。"""
    @abstractmethod
    def list_records(self, type: str | None = None, source: str | None = None,
                     tag: str | None = None, q: str | None = None,
                     limit: int = 100, offset: int = 0) -> tuple[list[Record], int]:
        """过滤+分页，返回 (记录列表, 总数)。type/source 走 Chroma where；
        q 走 where_document $contains；tag 在 Python 侧过滤（Chroma 不支持数组包含）。"""
    @abstractmethod
    def query(self, embedding: list[float], top_k: int = 5,
              where: dict | None = None) -> list[tuple[Record, float]]:
        """向量检索；score = 1 - 余弦距离 ∈ [-1, 1]，降序返回。"""

class ChromaStore(VectorStore):
    def __init__(self, persist_dir: Path, collection_name: str = "kb_records"):
        """PersistentClient + get_or_create_collection(metadata={"hnsw:space": "cosine"})。"""
```

**验收测试**（`tests/test_n04_storage.py`，用 8 维手工向量，不依赖嵌入模型）：

```python
import pytest

pytestmark = pytest.mark.integration

V = {"a": [1, 0, 0, 0, 0, 0, 0, 0], "b": [0, 1, 0, 0, 0, 0, 0, 0],
     "c": [0.9, 0.1, 0, 0, 0, 0, 0, 0]}


def _store(env_isolated):
    from kb.storage import ChromaStore
    from kb.models import Record, RecordType
    store = ChromaStore(env_isolated / "chroma")
    recs = [
        Record(content="苹果手机价格", tags=["数码"], source="doc1"),
        Record(content="香蕉的营养价值", tags=["水果"], source="doc1"),
        Record(content="苹果公司的历史", tags=["数码", "公司"], source="doc2"),
        Record(content="网页抓取笔记", type=RecordType.WEB_CHUNK, source="http://x.cn"),
    ]
    store.add(recs, [V["a"], V["b"], V["c"], V["b"]])
    return store, recs


def test_增查删(env_isolated):
    store, recs = _store(env_isolated)
    assert store.get(recs[0].id).content == "苹果手机价格"
    assert store.get("不存在") is None
    store.delete([recs[3].id])
    assert store.get(recs[3].id) is None


def test_按source级联删除(env_isolated):
    store, recs = _store(env_isolated)
    assert store.delete_by_source("doc1") == 2
    assert store.delete_by_source("doc1") == 0
    assert store.get(recs[0].id) is None


def test_列表过滤与分页(env_isolated):
    store, recs = _store(env_isolated)
    recs_all, total = store.list_records()
    assert total == 4
    _, t = store.list_records(type="web_chunk"); assert t == 1
    _, t = store.list_records(source="doc1"); assert t == 2
    _, t = store.list_records(q="苹果"); assert t == 2
    _, t = store.list_records(tag="数码"); assert t == 2  # Python 侧过滤
    page, total = store.list_records(limit=2, offset=2)
    assert len(page) == 2 and total == 4


def test_向量检索排序(env_isolated):
    store, _ = _store(env_isolated)
    hits = store.query(V["a"], top_k=3)
    assert hits[0][0].content == "苹果手机价格"      # 正交命中，score=1.0
    assert hits[0][1] > hits[1][1] > 0
    assert len(hits) == 3
```

**门禁**：测试全绿；tag `node-04`。

---

## N5：BM25 索引

**目标**：jieba 分词 + 内存 BM25 索引，支持增量维护与查询排名。

**交付物**：`kb/bm25.py`、`tests/test_n05_bm25.py`

**契约**：

```python
import jieba
from rank_bm25 import BM25Okapi

def tokenize(text: str) -> list[str]:
    """jieba.cut_for_search（搜索引擎模式，含子词，中文关键词召回率更高）
    + 小写化 + 去空白/单字符标点。
    注：2026-08-23 人工确认偏离原契约 jieba.lcut——细粒度分词对关键词检索更优。"""

class BM25Index:
    def __init__(self) -> None:
        """内部维护 {record_id: tokens}；每次变更同步重建 BM25Okapi（个人级规模毫秒级）。"""
    def rebuild(self, records) -> None: ...
    def add(self, record: Record) -> None: ...
    def remove(self, record_id: str) -> None: ...
    def search(self, query: str, top_n: int = 10) -> list[tuple[str, float]]:
        """返回 (record_id, bm25分数) 降序；空索引返回 []。"""
```

**验收测试**（`tests/test_n05_bm25.py`）：

```python
def test_分词():
    from kb.bm25 import tokenize
    toks = tokenize("苹果Apple手机的价格")
    assert "苹果" in toks and "手机" in toks
    assert all(t.strip() for t in toks)


def test_检索排名与增删():
    from kb.bm25 import BM25Index
    from kb.models import Record
    idx = BM25Index()
    r1 = Record(content="苹果手机价格八千")   # 含"苹果"
    r2 = Record(content="香蕉苹果都很甜")      # 含"苹果"
    r3 = Record(content="今天天气不错")
    idx.rebuild([r1, r2, r3])
    hits = idx.search("苹果", top_n=2)
    ids = [h[0] for h in hits]
    assert r1.id in ids and r2.id in ids and r3.id not in ids
    assert hits[0][1] >= hits[1][1] > 0

    idx.remove(r1.id)
    ids = [h[0] for h in idx.search("苹果", top_n=5)]
    assert r1.id not in ids and r2.id in ids

    r4 = Record(content="苹果公司发布新品")
    idx.add(r4)
    assert r4.id in [h[0] for h in idx.search("苹果", top_n=5)]


def test_空索引():
    from kb.bm25 import BM25Index
    assert BM25Index().search("任意", top_n=3) == []
```

**门禁**：测试全绿；tag `node-05`。

---

## N6：混合检索与服务编排

**目标**：RRF 融合检索 + KBService 统一编排 + CLI（add/search/info），达成 M1 验收"CLI 可 add + search，混合检索三类查询命中"。

**交付物**：`kb/retriever.py`、`kb/service.py`、`kb/cli.py`、`tests/test_n06_retriever_service.py`（integration）

**契约**：

```python
# retriever.py
RRF_K = 60

def rrf_fuse(vector_hits: list[tuple[str, float]],   # (id, score) 各自降序
             keyword_hits: list[tuple[str, float]],
             top_k: int) -> list[tuple[str, float]]:
    """score(d) = Σ 1/(RRF_K + rank_i(d))，rank 从 1 起；按融合分降序取 top_k。"""

class HybridRetriever:
    def __init__(self, store, bm25: BM25Index, embedder: Embedder): ...
    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               type: str | None = None, tag: str | None = None) -> list[dict]:
        """mode: hybrid/vector/keyword；每路取 3*top_k 候选；
        type/tag 过滤在融合后进行（过滤后不足 top_k 属正常）；
        输出 [{id, content, score, type, source, tags, created_at}]，score 为 RRF 融合分。"""

# service.py
class KBService:
    def __init__(self, settings: Settings | None = None):
        """组装 embedder/store/bm25/retriever；启动时从 store.iter_all() 重建 BM25。"""
    def add_memory(self, content: str, tags: list[str] | None = None,
                   source: str | None = None, namespace: str = "default") -> Record: ...
    def get_memory(self, record_id: str) -> Record | None: ...
    def list_memories(self, **filters) -> tuple[list[Record], int]: ...
    def update_memory(self, record_id: str, content: str | None = None,
                      tags: list[str] | None = None) -> Record | None:
        """content 变更时重新嵌入并更新 updated_at。"""
    def delete_memory(self, record_id: str) -> bool: ...
    def search(self, query: str, top_k: int = 5, mode: str = "hybrid",
               type: str | None = None, tag: str | None = None) -> list[dict]: ...
    def stats(self) -> dict:
        """{"records": int, "device": str, "llm": str}；llm 在 N9 前恒为 "disabled"。"""

# cli.py（typer）
# kb add "内容" --tags a,b --source xxx
# kb search "查询" --top-k 5 --mode hybrid
# kb info
```

**验收测试**（`tests/test_n06_retriever_service.py`）：

```python
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def svc(env_isolated):
    from kb.service import KBService
    s = KBService()
    s.add_memory("张三的生日是3月15日", tags=["人物"], source="t")
    s.add_memory("李四负责前端开发，使用 React 和 TypeScript", tags=["人物"], source="t")
    s.add_memory("项目例会每周五下午三点，会议室 B201", tags=["日程"], source="t")
    s.add_memory("The quick brown fox jumps over the lazy dog", tags=["en"], source="t")
    return s


def test_rrf融合数学():
    from kb.retriever import rrf_fuse
    v = [("a", 0.9), ("b", 0.8), ("c", 0.7)]
    k = [("b", 5.0), ("a", 4.0), ("d", 3.0)]
    out = rrf_fuse(v, k, top_k=4)
    # a: 1/61+1/62  b: 1/62+1/61  c: 1/63  d: 1/63 → a=b > c=d
    assert out[0][0] in ("a", "b") and out[1][0] in ("a", "b")
    assert dict(out)["c"] == pytest.approx(1 / (60 + 3), abs=1e-9)
    assert dict(out)["d"] == pytest.approx(1 / (60 + 3), abs=1e-9)


def test_关键词查询命中(svc):
    hits = svc.search("张三 生日", top_k=3, mode="keyword")
    assert hits and "张三" in hits[0]["content"]


def test_语义查询命中(svc):
    # 无字面重叠的语义近义查询
    hits = svc.search("敏捷的棕色狐狸跳过懒狗", top_k=2, mode="vector")
    assert hits and "fox" in hits[0]["content"]


def test_混合模式与类型过滤(svc):
    hits = svc.search("例会", top_k=3, mode="hybrid", tag="日程")
    assert hits and "B201" in hits[0]["content"]
    assert all("日程" in h["tags"] for h in hits)


def test_增改删全流程(svc):
    r = svc.add_memory("临时记忆内容")
    assert svc.get_memory(r.id).content == "临时记忆内容"
    updated = svc.update_memory(r.id, content="改后的记忆内容")
    assert updated.content == "改后的记忆内容"
    assert svc.search("改后的记忆内容", top_k=1, mode="keyword")
    assert svc.delete_memory(r.id) is True
    assert svc.get_memory(r.id) is None
    assert svc.delete_memory(r.id) is False


def test_cli_add与search(env_isolated):
    from typer.testing import CliRunner
    from kb.cli import app
    runner = CliRunner()
    assert runner.invoke(app, ["add", "CLI写入的测试记忆"]).exit_code == 0
    res = runner.invoke(app, ["search", "CLI写入", "--mode", "keyword"])
    assert res.exit_code == 0 and "CLI写入的测试记忆" in res.output
```

**门禁**：测试全绿（语义用例验证向量路、关键词用例验证 BM25 路、hybrid 验证融合）；`python -m kb info` 可运行（`kb/__main__.py` 转发 cli）；tag `node-06`。**M1 完成，人工确认后进入 M2。**

### N6-hotfix：嵌入器离线加载（M1 关闭的前置条件，2026-08-23 验收发现）

**问题**：CLI 直接运行（非 pytest 环境）加载默认模型 `BAAI/bge-m3` 时，huggingface_hub 先联网 HEAD 校验模型更新；直连 huggingface.co 失败后其重试逻辑抛出
`RuntimeError: Cannot send a request, as the client has been closed.`，模型加载崩溃。
影响：**断网/无代理环境下 CLI 与服务均无法启动**——违反设计文档"模型预下载后，断网可完整运行"的成功标准。
（测试未暴露的原因：conftest 设置了 `HF_ENDPOINT=https://hf-mirror.com`，镜像可达所以联网校验通过了。）

**修复要求**（`kb/embedder.py` 的 `_ensure_loaded`，离线优先 + 在线兜底）：

```python
def _ensure_loaded(self):
    """首次使用时加载模型；优先离线（缓存命中，不联网），失败再在线下载；cuda 下 fp16。"""
    if self._model is None:
        from sentence_transformers import SentenceTransformer
        try:
            self._model = SentenceTransformer(
                self.model_name, device=self.device, local_files_only=True)
        except Exception:
            # 缓存未命中（首次部署新模型），走在线下载；HF_ENDPOINT 镜像由环境变量提供
            self._model = SentenceTransformer(self.model_name, device=self.device)
        if self.device == "cuda":
            self._model.half()
```

已验证：本机 sentence-transformers 5.6.0 支持 `local_files_only`，BGE-M3 缓存离线加载正常（1024 维）。

**补充验收**（追加到 `tests/test_n03_embedder.py`，无需新增文件）：

```python
def test_断网环境加载(env_isolated, monkeypatch):
    """模拟断网：hf_hub 离线开关打开时仍能从缓存加载（回归 N6-hotfix）。"""
    monkeypatch.setenv("HF_HUB_OFFLINE", "1")
    from kb.embedder import Embedder
    from kb.config import get_settings
    e = Embedder(get_settings().embed_model, device="cpu")
    vecs = e.embed_texts(["离线加载测试"])
    assert len(vecs[0]) == 512
```

**门禁**：原 20 项全绿 + 上述补充用例绿；CLI 在**不设任何 HF 环境变量**的裸终端可 add/search；提交 `节点06-hotfix: ...`，tag `node-06`（原地重打或 `node-06.1`）。

---

## N7：REST API 骨架

**目标**：FastAPI 应用工厂 + memories CRUD + healthz + 统一错误格式。

**交付物**：`kb/api.py`、`kb/__main__.py`（补 serve）、`tests/test_n07_api.py`（integration）

**契约**：

```python
def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂；全局单例 KBService 挂 app.state.kb（测试注入：app.dependency_overrides）。
    统一错误 JSON：{"error": "<CODE>", "message": "<人话>"}；
    404 记录不存在；422 校验失败（FastAPI 默认）；500 兜底不泄堆栈。"""

# 路由（前缀 /api/v1）：
# POST   /api/v1/memories        {content, tags?, source?, namespace?} → {id, ...Record}
# GET    /api/v1/memories        过滤参数 type/tag/source/q + limit/offset → {items, total}
# GET    /api/v1/memories/{id}   → Record | 404
# PATCH  /api/v1/memories/{id}   {content?, tags?} → Record | 404
# DELETE /api/v1/memories/{id}   → {"ok": true} | 404
# GET    /api/v1/healthz         {status, llm, device, records}
```

**验收测试**（`tests/test_n07_api.py`；通过环境变量隔离数据目录——conftest 的 `env_isolated` 已做）：

```python
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        yield c


def test_记忆CRUD全流程(client):
    r = client.post("/api/v1/memories",
                    json={"content": "API写入的记忆", "tags": ["t"]})
    assert r.status_code == 200
    rid = r.json()["id"]

    assert client.get(f"/api/v1/memories/{rid}").json()["content"] == "API写入的记忆"

    r = client.get("/api/v1/memories", params={"q": "API写入"})
    assert r.json()["total"] == 1 and r.json()["items"][0]["id"] == rid

    r = client.patch(f"/api/v1/memories/{rid}", json={"content": "API修改后的记忆"})
    assert r.json()["content"] == "API修改后的记忆"

    assert client.delete(f"/api/v1/memories/{rid}").status_code == 200
    assert client.get(f"/api/v1/memories/{rid}").status_code == 404


def test_错误路径(client):
    assert client.get("/api/v1/memories/不存在").status_code == 404
    assert client.delete("/api/v1/memories/不存在").json()["error"] == "NOT_FOUND"
    r = client.post("/api/v1/memories", json={})
    assert r.status_code == 422  # content 必填


def test_健康检查(client):
    r = client.get("/api/v1/healthz").json()
    assert r["status"] == "ok" and r["records"] == 0
    assert set(r) == {"status", "llm", "device", "records"}
```

**门禁**：测试全绿；tag `node-07`。

---

## N8：检索端点与设备检测

**目标**：`/search`、`/documents` 列表与删除端点；GPU 设备检测交互与持久化；`serve` 命令。

**交付物**：`kb/api.py` 扩展、`kb/cli.py` 扩展（serve + 设备询问）、`tests/test_n08_api_ext.py`

**契约**：

```python
# POST /api/v1/search  {query, top_k=5, mode="hybrid", type?, tag?} → {results: [...]}
# GET  /api/v1/documents  → {items: [{source, chunks, chars, last_imported}]}（按 source 聚合 doc_chunk/web_chunk）
# DELETE /api/v1/documents/{source}  → {"deleted": n}（URL 编码的 source）

# 设备检测（cli.py 或独立函数，可测）：
def resolve_device(settings: Settings, interactive: bool, input_fn=input) -> str:
    """优先级：settings.device 显式值 > runtime.json 已存选择 > 交互询问（cuda 可用时）
    > 默认 cpu。interactive=False 时不询问直接 cpu。选择结果写入 runtime.json。"""
```

**验收测试**（`tests/test_n08_api_ext.py`）：

```python
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        c.post("/api/v1/memories", json={"content": "检索端点测试记忆"})
        yield c


def test_search三种模式(client):
    for mode in ("hybrid", "vector", "keyword"):
        r = client.post("/api/v1/search", json={"query": "检索端点", "mode": mode})
        assert r.status_code == 200
        assert any("检索端点" in h["content"] for h in r.json()["results"])


def test_文档列表与删除(client):
    # 先造带 source 的数据（绕过 ingest，直接走 memories 带 source）
    client.post("/api/v1/memories",
                json={"content": "文档块一", "source": "doc_a.txt"})
    client.post("/api/v1/memories",
                json={"content": "文档块二", "source": "doc_a.txt"})
    docs = client.get("/api/v1/documents").json()["items"]
    target = [d for d in docs if d["source"] == "doc_a.txt"]
    assert target and target[0]["chunks"] == 2

    r = client.delete("/api/v1/documents/doc_a.txt")
    assert r.json()["deleted"] == 2
    assert client.get("/api/v1/documents").json()["items"] == []


def test_设备检测决策(env_isolated, monkeypatch):
    from kb.config import get_settings
    from kb.cli import resolve_device
    s = get_settings()
    # 1. 显式 env 最高优先
    monkeypatch.setenv("KB_DEVICE", "cpu"); get_settings.cache_clear()
    from kb.config import get_settings as gs
    assert resolve_device(gs(), interactive=True) == "cpu"
    # 2. 无 runtime.json、非交互 → cpu
    monkeypatch.delenv("KB_DEVICE"); get_settings.cache_clear()
    assert resolve_device(gs(), interactive=False) == "cpu"
    # 3. 交互输入 y → cuda 或 cpu（取决于显卡），且持久化
    choice = resolve_device(gs(), interactive=True, input_fn=lambda _: "y")
    assert choice in ("cuda", "cpu")
    assert (gs().runtime_file).exists()
```

**门禁**：测试全绿；`python -m kb serve` 能启动（人工冒烟：起服务 → curl /api/v1/healthz → Ctrl+C）；tag `node-08`。**M2 完成，人工确认后进入 M3。**

---

## N9：LLM 接入层

**目标**：Ollama 探测、模式解析（local/auto/cloud/disabled）、护栏参数、本地/云端双客户端统一接口。**本节点用 mock HTTP 测试，不依赖真实 Ollama。**

**交付物**：`kb/llm.py`、`tests/test_n09_llm.py`

**契约**：

```python
class LLMStatus(str, Enum):
    LOCAL = "local"; CLOUD = "cloud"; DISABLED = "disabled"

class LLMClient:
    def __init__(self, settings: Settings, http_client: httpx.Client | None = None):
        """http_client 注入用于测试；默认自建。构造时探测。"""
    def probe(self) -> dict:
        """GET {ollama_base_url}/v1/models（2s 超时）→ local 可用性；
        deepseek_api_key 非空 → cloud 可用性。"""
    @property
    def status(self) -> LLMStatus:
        """local 模式：本地可用→LOCAL 否则 DISABLED；
        cloud 模式：有 Key→CLOUD 否则 DISABLED；
        auto 模式：本地可用→LOCAL（N11 接管后 auto 表示路由）；无本地有云→CLOUD；都无→DISABLED。"""
    def chat(self, messages: list[dict], max_tokens: int | None = None,
             prefer: str = "auto") -> str:
        """统一生成接口。prefer: local/cloud/auto（路由用）。
        本地：POST {ollama_base_url}/api/chat，body 含
          {"think": false, "options": {"temperature": 0.2, "num_ctx": 4096,
           "max_tokens": 800 或入参覆盖}}
        云端：openai SDK，model=deepseek_model，temperature 0.2，max_tokens 同上。
        调用失败抛 LLMError（service 层转 503/降级）。"""
```

**验收测试**（`tests/test_n09_llm.py`，用 `httpx.MockTransport`）：

```python
import httpx
import pytest


def _client(env_isolated, monkeypatch, ollama_up=True, key=""):
    from kb.config import get_settings
    from kb.llm import LLMClient
    get_settings.cache_clear()
    monkeypatch.setenv("KB_DEEPSEEK_API_KEY", key)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            if not ollama_up:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"data": [{"id": "qwen3:4b"}]})
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            assert body["think"] is False                      # 护栏：关思考
            assert body["options"]["num_ctx"] == 4096          # 护栏：上下文
            assert body["options"]["temperature"] == 0.2
            return httpx.Response(200, json={
                "message": {"content": "本地回答"}, "eval_count": 5})
        raise AssertionError(f"意外请求: {request.url}")

    transport = httpx.MockTransport(handler)
    return LLMClient(get_settings(), http_client=httpx.Client(transport=transport,
                                                              base_url="http://test"))


import json


def test_本地可用与护栏参数(env_isolated, monkeypatch):
    c = _client(env_isolated, monkeypatch)
    assert c.status.value == "local"
    assert c.chat([{"role": "user", "content": "hi"}]) == "本地回答"


def test_模式解析矩阵(env_isolated, monkeypatch):
    from kb.llm import LLMStatus
    # local 模式 + Ollama 挂 → DISABLED
    monkeypatch.setenv("KB_LLM_MODE", "local")
    c = _client(env_isolated, monkeypatch, ollama_up=False)
    assert c.status is LLMStatus.DISABLED
    # cloud 模式 + 有 Key → CLOUD
    monkeypatch.setenv("KB_LLM_MODE", "cloud")
    get_settings.cache_clear()
    c = _client(env_isolated, monkeypatch, ollama_up=False, key="sk-test")
    assert c.status is LLMStatus.CLOUD
    # auto + 都无 → DISABLED
    monkeypatch.setenv("KB_LLM_MODE", "auto")
    get_settings.cache_clear()
    c = _client(env_isolated, monkeypatch, ollama_up=False)
    assert c.status is LLMStatus.DISABLED


def test_禁用时调用报错(env_isolated, monkeypatch):
    from kb.llm import LLMClient, LLMError
    c = _client(env_isolated, monkeypatch, ollama_up=False)
    with pytest.raises(LLMError):
        c.chat([{"role": "user", "content": "hi"}])
```

**门禁**：测试全绿；tag `node-09`。

---

## N10：/ask 基础 RAG

**目标**：检索 → 上下文拼装（token 预算截断）→ 护栏 prompt → 生成 → 附 sources。

**交付物**：`kb/service.py` 的 `ask()`、`kb/api.py` 的 `POST /api/v1/ask`、`tests/test_n10_ask.py`

**契约**：

```python
# service.py
RAG_SYSTEM_PROMPT = "仅依据参考文档回答，无相关信息则明确说明，禁止编造。"

class KBService:
    def ask(self, question: str) -> dict:
        """流程：search(question, top_k=5) → 按相似度降序拼上下文
        （字符预算 = context_token_limit * 2，超出截断）→ llm.chat →
        返回 {answer, sources: [{id, content, score, source}], llm: "local|cloud"}。
        LLM 禁用时抛 LLMDisabledError（API 层转 503 + 配置指引）。"""

# api.py
# POST /api/v1/ask  {question} → 200 {answer, sources, llm}
#                        LLM 禁用 → 503 {"error": "LLM_DISABLED", "message": 安装Ollama或配置Key的指引}
```

**验收测试**（`tests/test_n10_ask.py`，Fake LLM 注入：KBService 构造函数增加可选 `llm` 参数用于测试替身）：

```python
import pytest

pytestmark = pytest.mark.integration


class FakeLLM:
    def __init__(self, answer="模拟回答"):
        self.answer = answer; self.calls = []
    @property
    def status(self):
        from kb.llm import LLMStatus
        return LLMStatus.LOCAL
    def chat(self, messages, **kw):
        self.calls.append(messages); return self.answer


@pytest.fixture
def svc(env_isolated):
    from kb.service import KBService
    s = KBService(llm=FakeLLM())
    for i in range(8):
        s.add_memory(f"第{i}条超长记忆" + "背景说明。" * 200)  # 每条约 1000 字
    return s


def test_ask返回答案与来源(svc):
    r = svc.ask("第3条记忆")
    assert r["answer"] == "模拟回答"
    assert len(r["sources"]) >= 1 and r["llm"] == "local"
    assert all("id" in s and "score" in s for s in r["sources"])


def test_上下文预算截断(svc):
    from kb.config import get_settings
    svc.ask("第3条记忆")
    user_msg = svc.llm.calls[0][-1]["content"]
    budget = get_settings().context_token_limit * 2
    assert len(user_msg) <= budget + 200   # 允许模板外壳少量超出


def test_护栏系统提示(svc):
    svc.ask("任意问题")
    system_msg = svc.llm.calls[0][0]["content"]
    assert "禁止编造" in system_msg


def test_禁用转503(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/ask", json={"question": "测试"})
        assert r.status_code == 503
        assert r.json()["error"] == "LLM_DISABLED"
        assert "Ollama" in r.json()["message"]
```

**门禁**：测试全绿；tag `node-10`。

---

## N11：智能路由

**目标**：auto 模式下的复杂度分类、云端前置压缩、答案缓存、隐私隔离、云端失败降级本地。

**交付物**：`kb/llm.py` 扩展（classify/compress 方法）或独立 `kb/router.py`、`kb/service.py` 的 `ask()` 路由分支、`tests/test_n11_routing.py`

**契约**：

```python
# 分类提示（本地 LLM，max_tokens=5，temperature=0）：
# "判断问题类型，仅输出 SIMPLE 或 COMPLEX。问题：{q}"
# 判定规则补充：检索结果含 tag 'sensitive' 或 namespace 属于 sensitive_ns_list → SENSITIVE

# 压缩提示（本地 LLM）：
# "将以下检索内容压缩为要点，保留全部关键事实，500字内输出：\n{context}"

# service.ask 路由逻辑（mode=auto 时）：
#   SENSITIVE → 本地直答
#   SIMPLE   → 本地直答
#   COMPLEX  → 有云 → 本地压缩上下文 → 云端生成（失败则本地直答）
#              无云 → 本地直答
# 缓存：embed_query(问题) 与已缓存问题向量余弦 ≥ cache_sim_threshold → 直接返回缓存答案
#       （LRU，容量 cache_size；写缓存同时存 question embedding 与 sources）
```

**验收测试**（`tests/test_n11_routing.py`）：

```python
import pytest

pytestmark = pytest.mark.integration


class ScriptedLLM:
    """按调用次序返回预设结果；记录每次收到的 messages。"""
    def __init__(self, script: list[str]):
        self.script = list(script); self.calls = []
    @property
    def status(self):
        from kb.llm import LLMStatus
        return LLMStatus.LOCAL
    def chat(self, messages, prefer="auto", **kw):
        self.calls.append({"messages": messages, "prefer": prefer})
        return self.script.pop(0)


@pytest.fixture
def routed_svc(env_isolated, monkeypatch):
    from kb.service import KBService
    get_settings.cache_clear()
    monkeypatch.setenv("KB_LLM_MODE", "auto")
    llm = ScriptedLLM(script=[])
    s = KBService(llm=llm)
    s.add_memory("普通事实：会议室在 B201")
    s.add_memory("密码：门禁是 8842", tags=["sensitive"])
    s._cloud_client = None   # 无云
    return s, llm


def test_敏感内容强制本地(routed_svc):
    svc, llm = routed_svc
    llm.script = ["SIMPLE", "门禁密码是8842"]
    r = svc.ask("门禁密码是多少")
    assert r["llm"] == "local"
    # 敏感问题即使 SIMPLE/COMPLEX 分类也不出云（无云调用发生）


def test_简单问题本地直答(routed_svc):
    svc, llm = routed_svc
    llm.script = ["SIMPLE", "会议室在B201"]
    r = svc.ask("会议室在哪")
    assert r["answer"] == "会议室在B201"
    assert all(c["prefer"] != "cloud" for c in llm.calls)


def test_缓存命中只调一次LLM(routed_svc):
    svc, llm = routed_svc
    llm.script = ["SIMPLE", "首次回答"]
    svc.ask("会议室在哪")
    svc.ask("会议室在哪里？")        # 相似问法
    svc.ask("会议室究竟在哪呀")      # 再次相似
    assert len(llm.calls) == 1       # 只有首次真正调用了分类+生成


def test_复杂问题无云走本地(routed_svc):
    svc, llm = routed_svc
    llm.script = ["COMPLEX", "本地兜底回答"]
    r = svc.ask("综合分析全部记忆并给出年度报告")
    assert r["answer"] == "本地兜底回答"


def test_复杂问题有云先压缩(env_isolated, monkeypatch):
    """云端路径：分类→压缩→云端生成；压缩后 prompt 必须短于原始上下文。"""
    from kb.service import KBService

    class FakeCloud:
        def __init__(self): self.received = []
        def chat(self, messages, **kw):
            self.received.append(messages); return "云端回答"

    llm = ScriptedLLM(["COMPLEX", "压缩后的要点"])
    svc = KBService(llm=llm)
    svc._cloud_client = FakeCloud()
    svc.add_memory("项目甲预算三百万" + "细节。" * 300)
    r = svc.ask("综合对比所有项目预算并分析")
    assert r["answer"] == "云端回答" and r["llm"] == "cloud"
    assert len(FakeCloud and svc._cloud_client.received) == 1
```

**门禁**：测试全绿；tag `node-11`。

---

## N12：MCP 服务

**目标**：FastMCP 挂载进 ASGI 应用，暴露 8 个 tools（设计文档 5.2 节），全部薄封装 KBService。

**交付物**：`kb/mcp.py`、`kb/api.py` 挂载（`/mcp` 路径）、`tests/test_n12_mcp.py`

**契约**：

```python
# kb/mcp.py：模块级定义 8 个函数（便于直接测试），再用 FastMCP 注册：
def write_memory(content: str, tags: list[str] | None = None) -> dict: ...      # → {id}
def search_memory(query: str, top_k: int = 5) -> list[dict]: ...
def read_memory(record_id: str) -> dict: ...
def update_memory(record_id: str, content: str) -> dict: ...
def delete_memory(record_id: str) -> dict: ...
def add_document(path: str) -> dict: ...        # N13 前返回 {"error": "NOT_READY"}
def add_webpage(url: str) -> dict: ...          # N14 前返回 {"error": "NOT_READY"}
def ask_kb(question: str) -> dict: ...

def create_mcp_server(service: KBService) -> FastMCP: ...
# api.py: app.mount("/mcp", mcp 的 streamable http app)
```

**验收测试**（`tests/test_n12_mcp.py`，直接调用函数层 + 应用挂载冒烟）：

```python
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def msvc(env_isolated):
    from kb.service import KBService
    return KBService()


def test_八个工具函数全部注册(msvc):
    from kb.mcp import create_mcp_server
    mcp = create_mcp_server(msvc)
    tools = {t.name for t in mcp._tool_manager.list_tools()}
    assert tools == {"write_memory", "search_memory", "read_memory", "update_memory",
                     "delete_memory", "add_document", "add_webpage", "ask_kb"}


def test_记忆工具链(msvc):
    from kb.mcp import write_memory, read_memory, update_memory, delete_memory, search_memory
    r = write_memory("MCP写入的记忆")
    assert "id" in r
    assert read_memory(r["id"])["content"] == "MCP写入的记忆"
    update_memory(r["id"], "MCP更新的记忆")
    hits = search_memory("MCP更新")
    assert hits and hits[0]["id"] == r["id"]
    assert delete_memory(r["id"])["ok"] is True


def test_未就绪工具(msvc):
    from kb.mcp import add_document, add_webpage
    assert add_document("x.pdf")["error"] == "NOT_READY"
    assert add_webpage("http://x")["error"] == "NOT_READY"


def test_应用挂载冒烟(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        assert c.get("/api/v1/healthz").status_code == 200
```

**门禁**：测试全绿；tag `node-12`；**M3 自动化完成**——人工验收：Claude Code 实际挂载 `/mcp` 写读记忆 + 11.2 节全链路基准报告（文档/测试 AI 执行）。确认后进入 M4。

---

## N13：文档摄取

**目标**：pdf/docx/md/txt 解析 + 切分入库 + markitdown 兜底 + `POST /api/v1/documents`。

**交付物**：`kb/ingest.py`（文档部分）、`kb/mcp.py` 的 add_document 接通、`tests/test_n13_ingest.py`

**契约**：

```python
# ingest.py
SUPPORTED = {".pdf": pypdf, ".docx": python-docx, ".md": 直读, ".txt": 直读}
# 其余（.xlsx/.pptx 等）→ markitdown；再不支持 → 抛 UnsupportedFormatError（API 转 400）

def parse_file(path: Path) -> str: ...
def chunk_text(text: str, size: int, overlap: int) -> list[str]:
    """langchain-text-splitters RecursiveCharacterTextSplitter。"""

# KBService.add_document(path) -> {"source": 文件名, "chunks": n}
#   type=doc_chunk，source=文件名，每 chunk 一条 Record
# API：POST /api/v1/documents  multipart 文件上传 或 {"path": 本地路径}
# MCP：add_document(path) 接通
```

**验收测试**（`tests/test_n13_ingest.py`）：

```python
import pytest

pytestmark = pytest.mark.integration


def test_txt_md解析与切分(env_isolated):
    from kb.ingest import parse_file, chunk_text
    f = env_isolated / "note.txt"
    f.write_text("A" * 600 + "\n" + "B" * 600, encoding="utf-8")
    assert len(parse_file(f)) == 1201
    chunks = chunk_text(parse_file(f), size=500, overlap=100)
    assert all(len(c) <= 500 for c in chunks)
    assert len(chunks) >= 3  # 600+600 必然切出 3 块以上


def test_不支持的格式(env_isolated):
    from kb.ingest import parse_file, UnsupportedFormatError
    f = env_isolated / "x.exe"
    f.write_bytes(b"MZ...")
    with pytest.raises(UnsupportedFormatError):
        parse_file(f)


def test_docx解析(env_isolated):
    from docx import Document
    from kb.ingest import parse_file
    d = Document()
    d.add_paragraph("这是一段docx测试内容")
    p = env_isolated / "t.docx"; d.save(str(p))
    assert "docx测试内容" in parse_file(p)


def test_文档入库与删除级联(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    f = env_isolated / "book.txt"
    f.write_text("第一章：混合检索的原理。" * 30, encoding="utf-8")
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/documents", json={"path": str(f)})
        assert r.status_code == 200 and r.json()["chunks"] >= 1
        src = r.json()["source"]
        hits = c.post("/api/v1/search",
                      json={"query": "混合检索的原理", "mode": "keyword"}).json()["results"]
        assert hits
        assert c.delete(f"/api/v1/documents/{src}").json()["deleted"] >= 1
```

**门禁**：测试全绿；tag `node-13`。

---

## N14：网页摄取

**目标**：URL 抓取 → trafilatura 正文提取 → web_chunk 入库。

**交付物**：`kb/ingest.py` 扩展、`kb/api.py` 的 `POST /api/v1/ingest/web`、`kb/mcp.py` 的 add_webpage 接通、`tests/test_n14_web.py`

**契约**：

```python
# KBService.add_webpage(url) -> {"source": url, "chunks": n}
#   httpx.Client(timeout=15, headers={"User-Agent": "Mozilla/5.0 ..."}, follow_redirects=True)
#   → trafilatura.extract(html) → None 时抛 WebFetchError（API 转 400 + 原因）
#   type=web_chunk，source=url，超 500 字切分入库
```

**验收测试**（`tests/test_n14_web.py`，httpx MockTransport 注入）：

```python
import pytest

pytestmark = pytest.mark.integration

HTML = """<html><head><title>测试页</title></head><body>
<nav>导航噪声 导航噪声</nav>
<article><h1>混合检索指南</h1><p>向量检索与关键词检索通过 RRF 融合，"
"能同时兼顾语义与精确匹配，是本地知识库的主流方案。</p></article>
<footer>页脚噪声</footer></body></html>"""


def test_网页抓取入库(env_isolated, monkeypatch):
    from kb.service import KBService
    import kb.ingest as ingest

    def fake_fetch(url: str) -> str:
        assert url == "https://example.com/rag"
        return HTML
    monkeypatch.setattr(ingest, "_fetch_html", fake_fetch)
    s = KBService()
    r = s.add_webpage("https://example.com/rag")
    assert r["source"] == "https://example.com/rag" and r["chunks"] >= 1
    hits = s.search("RRF 融合", mode="keyword")
    assert hits and hits[0]["type"] == "web_chunk"


def test_抓取失败转400(env_isolated, monkeypatch):
    import kb.ingest as ingest
    monkeypatch.setattr(ingest, "_fetch_html",
                        lambda url: (_ for _ in ()).throw(Exception("404")))
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/ingest/web", json={"url": "https://bad.com"})
        assert r.status_code == 400 and "error" in r.json()
```

**门禁**：测试全绿；tag `node-14`。

---

## N15：目录监听

**目标**：watchdog 后台线程监听目录，文件创建/修改去抖入库，删除同步清理。

**交付物**：`kb/watcher.py`、`kb/api.py` 启动时挂线程（serve 模式）、`tests/test_n15_watcher.py`

**契约**：

```python
class KBWatcher:
    def __init__(self, service: KBService, watch_dir: Path, debounce_seconds: float = 2.0): ...
    def start(self) -> None: ...   # watchdog Observer + 后台线程
    def stop(self) -> None: ...
    # 事件处理：创建/修改 → 去抖后 add_document（仅支持的扩展名，忽略临时文件）
    #           删除 → delete_by_source(文件名)
```

**验收测试**（`tests/test_n15_watcher.py`，去抖设 0.2s 加速）：

```python
import time
import pytest

pytestmark = pytest.mark.integration


def test_新增文件自动入库(env_isolated, monkeypatch):
    from kb.service import KBService
    from kb.watcher import KBWatcher
    watch = env_isolated / "watch"; watch.mkdir()
    s = KBService()
    w = KBWatcher(s, watch, debounce_seconds=0.2); w.start()
    try:
        (watch / "auto.txt").write_text("监听目录自动入库的内容", encoding="utf-8")
        deadline = time.time() + 10
        while time.time() < deadline:
            if any("自动入库" in h["content"] for h in s.search("自动入库", mode="keyword")):
                break
            time.sleep(0.2)
        hits = s.search("自动入库", mode="keyword")
        assert hits and hits[0]["source"] == "auto.txt"
    finally:
        w.stop()


def test_文件删除级联清理(env_isolated):
    from kb.service import KBService
    from kb.watcher import KBWatcher
    watch = env_isolated / "watch"; watch.mkdir()
    s = KBService()
    (watch / "gone.txt").write_text("将被删除的文档内容", encoding="utf-8")
    s.add_document(watch / "gone.txt")
    w = KBWatcher(s, watch, debounce_seconds=0.2); w.start()
    try:
        (watch / "gone.txt").unlink()
        deadline = time.time() + 10
        while time.time() < deadline and s.list_records(q="将被删除")[1] > 0:
            time.sleep(0.2)
        assert s.list_records(q="将被删除")[1] == 0
    finally:
        w.stop()
```

**门禁**：测试全绿；tag `node-15`。**M4 完成。**

---

## N16：收尾与清理

**目标**：删除旧代码、重写 README、全量回归，达到交付状态。

**交付物**：
- 删除：`rag_kb/`、`app/`、`demo.py`、`test_py/`、旧 `requirements.txt` 内容已替换
- 重写 `README.md`：新定位、快速开始（venv → pip install → python -m kb serve）、Claude Code / Cursor 挂载 MCP 的配置示例（`http://127.0.0.1:8000/mcp`）、REST 端点速查
- `tests/test_n16_regression.py`：导入所有模块冒烟

**验收测试**（`tests/test_n16_regression.py`）：

```python
def test_全部模块可导入():
    import kb.config, kb.models, kb.embedder, kb.storage, kb.bm25
    import kb.retriever, kb.service, kb.llm, kb.ingest, kb.watcher
    import kb.api, kb.mcp, kb.cli  # noqa: F401


def test_旧目录已删除():
    from pathlib import Path
    for p in ("rag_kb", "app", "demo.py", "test_py"):
        assert not Path(p).exists(), f"旧代码未删除: {p}"


def test_README含挂载示例():
    content = Path("README.md").read_text(encoding="utf-8")
    assert "python -m kb serve" in content
    assert "/mcp" in content
```

**门禁**：`pytest tests/ -v` 全量绿；`python -m kb serve` 启动后 curl 全端点冒烟通过；断网启动正常（人工）；tag `node-16`。

**人工终验清单**（文档/测试 AI 准备报告，人工执行）：
1. 设计文档 2.3 节成功标准三条逐条验证
2. Claude Code 挂载 MCP 实际写/读记忆
3. 本地 vs 云端回答质量主观对比
4. 导入 100 份文档后关键词与语义双路命中抽查

---

## 附：每节点标准工作流（开发 AI 执行模板）

1. 读本计划对应节点 + 设计文档相关章节
2. 落地该节点验收测试文件（与计划代码逐字一致）
3. 运行确认测试失败（红）
4. 实现代码（契约签名一致）
5. 运行节点测试通过（绿）
6. `pytest tests/ -v` 全量回归
7. `git add <文件> && git commit -m "节点NN: 简述" && git tag node-NN`
8. 报告：完成内容 / 测试结果 / 待人工验证项 → 等人工确认后进入下一节点
