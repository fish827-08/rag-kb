# RAG-KB Stage 2 实施计划：API 化 + 检索优化 + 工程化

> **学习模式：** 前置知识 + 自测练习 → 项目需求 → 验收标准。不提供最终代码，自己写。

**目标：** 把 CLI 工具变成完整的工程化项目——REST API 服务 + 检索调优 + 容器化部署 + 可观测性

**技术栈：** FastAPI 0.137+、Pydantic v2、LangChain 0.3+（EnsembleRetriever / CrossEncoderReranker）、Docker、docker-compose、loguru/structlog

**预计时间：** 5-6 周（内容比 Stage 1 多，不要急）

**前置条件：** Stage 1 全部完成（Task 1-6 ✅），`format_docs` 的 `enumerate` bug 已修

---

## Stage 2 四个模块总览

| 模块 | 核心内容 | 预计时间 | 就业价值 |
|------|---------|---------|---------|
| 2.1 FastAPI 接口化 | CLI → REST API，异步处理，文件上传 | 1.5 周 | FastAPI + Pydantic 是 Python 后端核心技能 |
| 2.2 检索优化 | 混合检索、重排序、MMR、chunk 调优 | 1.5 周 | RAG 调优是 AI 应用岗的分水岭 |
| 2.3 工程化与部署 | Docker、环境分离、日志、CI/CD | 1.5 周 | DevOps 加分项，面试常问 |
| 2.4 数据层增强 | 文档管理、增量更新、元数据 | 1 周 | 产品完整度 |

**推进顺序：** 2.1 → 2.2（顺手重构 `ask_with_sources`）→ 2.3 → 2.4

**简历价值：** "RAG 系统 API 化，支持异步并发、混合检索 + 重排序、Docker 部署、CI/CD"

---

## 前置知识清单

### 模块 2.1：FastAPI + Pydantic v2

#### 知识点 1：FastAPI 基础与路由

**核心概念：**
- FastAPI 基于 Starlette（Web 层）+ Pydantic v2（数据校验），原生 async/await
- 路由用 `@app.get()` / `@app.post()` 装饰器定义，类型提示自动生成文档
- `APIRouter` 用于模块化路由拆分（类似蓝图的 concept）
- 当前最新稳定版 0.137+（2026-06），要求 Python >= 3.10

**关键 API：**
```python
from fastapi import FastAPI, APIRouter

app = FastAPI(title="RAG Knowledge Base API", version="2.0.0")
router = APIRouter(prefix="/api/v1", tags=["文档管理"])

@router.get("/health")
async def health_check():
    return {"status": "ok"}
```

**官方文档：**
- FastAPI 入门：https://fastapi.tiangolo.com/tutorial/first-steps/
- 路由参数：https://fastapi.tiangolo.com/tutorial/path-params/
- APIRouter：https://fastapi.tiangolo.com/tutorial/bigger-applications/

**自测方向：** 创建一个最小的 FastAPI 应用，定义 `/health` 和 `/echo` 两个路由，用 `fastapi dev main.py` 启动后 curl 测试。

---

#### 知识点 2：Pydantic v2 BaseModel 与字段校验

**核心概念：**
- Pydantic v2 用 Rust 重写内核，性能比 v1 快 5-50 倍
- `BaseModel` 定义数据模型，`model_config = ConfigDict(...)` 替代了 v1 的内部 `Config` 类
- v2 方法名变化：`dict()` → `model_dump()`、`parse_obj()` → `model_validate()`、`schema()` → `model_json_schema()`

**关键 API：**
```python
from pydantic import BaseModel, Field, ConfigDict
from typing import Annotated

class QuestionRequest(BaseModel):
    model_config = ConfigDict(str_max_length=1000)
    question: Annotated[str, Field(min_length=1, max_length=500, description="用户问题")]
    top_k: int = Field(default=3, ge=1, le=20)

class AnswerResponse(BaseModel):
    answer: str
    sources: list[str] = []
    tokens_used: int = 0
```

**v2 校验器变化（重要）：**
- `@validator` → `@field_validator`（字段级）
- `@root_validator` → `@model_validator`（模型级）
- 四种模式：`after`（默认，校验后执行）、`before`、`plain`、`wrap`

```python
from pydantic import field_validator

class QuestionRequest(BaseModel):
    question: str

    @field_validator("question")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("问题不能为空")
        return v.strip()
```

**官方文档：**
- Pydantic Models：https://docs.pydantic.dev/latest/concepts/models/
- Fields：https://docs.pydantic.dev/latest/concepts/fields/
- Validators：https://docs.pydantic.dev/latest/concepts/validators/
- v1→v2 迁移：https://docs.pydantic.dev/latest/migration/

**自测方向：** 定义 `QuestionRequest` 和 `AnswerResponse` 两个模型，手动构造合法/非法输入测试校验是否生效，打印 `model_json_schema()` 看 OpenAPI 输出。

---

#### 知识点 3：依赖注入（Depends）

**核心概念：**
- FastAPI 最强大的机制——依赖注入，用 `Depends()` 声明
- 三种形式：普通函数、生成器（yield，带清理）、类（带查询参数）
- 依赖可嵌套，FastAPI 自动解析整个依赖树
- 同一请求内相同依赖默认只调用一次（缓存），可用 `use_cache=False` 关闭

**关键 API：**
```python
from fastapi import Depends
from typing import Annotated

# 复用 RAGChain 实例（单例）
def get_rag_chain():
    from rag_kb.rag_chain import RAGChain
    return RAGChain()

# Annotated 复用（推荐）
ChainDep = Annotated[RAGChain, Depends(get_rag_chain)]

@app.post("/ask")
async def ask(question: str, chain: ChainDep):
    return {"answer": chain.ask(question)}
```

**官方文档：**
- 依赖注入：https://fastapi.tiangolo.com/tutorial/dependencies/
- 带 yield 的依赖：https://fastapi.tiangolo.com/tutorial/dependencies/dependencies-with-yield/

**自测方向：** 写一个 `get_rag_chain` 依赖，两个路由共享同一个实例，用 `print(id(chain))` 验证是否真的只创建了一次。

---

#### 知识点 4：文件上传（UploadFile）

**核心概念：**
- 需安装 `python-multipart`
- `UploadFile` 使用 spooled file：小文件存内存，大文件自动转磁盘
- 异步方法：`await file.read()`、`await file.seek(0)`
- 上传文件和 JSON Body 不能在同一个请求里混用（HTTP 协议限制）

**关键 API：**
```python
from fastapi import UploadFile, File, HTTPException
import shutil
from pathlib import Path

@app.post("/documents/upload")
async def upload_document(file: UploadFile):
    allowed_suffix = {".pdf", ".txt", ".md"}
    suffix = Path(file.filename).suffix.lower()
    if suffix not in allowed_suffix:
        raise HTTPException(400, detail=f"不支持的格式: {suffix}")

    save_path = Path("uploads") / file.filename
    with save_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"filename": file.filename, "size": save_path.stat().st_size}
```

**官方文档：**
- 文件上传：https://fastapi.tiangolo.com/tutorial/request-files/
- 表单 + 文件：https://fastapi.tiangolo.com/tutorial/request-forms-and-files/

**自测方向：** 写一个上传接口，用 curl 的 `-F "file=@test.txt"` 测试上传，验证文件保存成功。

---

#### 知识点 5：lifespan 事件（替代 on_event）

**核心概念：**
- `@app.on_event("startup")` 已废弃，改用 `lifespan` 上下文管理器
- 启动和关闭逻辑在同一函数内，资源配对清晰
- `yield` 后的清理代码无论是否异常都会执行

**关键 API：**
```python
from contextlib import asynccontextmanager
from fastapi import FastAPI

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动：初始化 RAGChain、embedding 等重资源
    app.state.chain = RAGChain()
    yield
    # 关闭：清理资源
    del app.state.chain

app = FastAPI(lifespan=lifespan)
```

**官方文档：**
- lifespan 事件：https://fastapi.tiangolo.com/advanced/events/

**自测方向：** 用 lifespan 在启动时 print "启动"、关闭时 print "关闭"，启动服务后 Ctrl+C 验证关闭逻辑是否执行。

---

#### 知识点 6：异常处理与统一响应

**核心概念：**
- `HTTPException` 是最常用的异常，`raise` 而非 `return`
- `@app.exception_handler()` 注册自定义异常处理器
- 可覆盖默认的 422 校验错误处理器，统一错误响应格式

**关键 API：**
```python
from fastapi import HTTPException
from fastapi.responses import JSONResponse

class RAGError(Exception):
    def __init__(self, message: str, code: str = "RAG_ERROR"):
        self.message = message
        self.code = code

@app.exception_handler(RAGError)
async def rag_error_handler(request, exc: RAGError):
    return JSONResponse(
        status_code=500,
        content={"error": exc.code, "message": exc.message}
    )

# 统一错误响应格式
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    return JSONResponse(
        status_code=500,
        content={"error": "INTERNAL_ERROR", "message": str(exc)}
    )
```

**官方文档：**
- 异常处理：https://fastapi.tiangolo.com/tutorial/handling-errors/

**自测方向：** 定义自定义异常 `RAGError`，在某个路由中 `raise RAGError("测试错误")`，验证返回的 JSON 格式。

---

### 模块 2.2：检索优化

#### 知识点 1：EnsembleRetriever（混合检索）

**核心概念：**
- 混合检索 = BM25 稀疏检索 + 向量稠密检索，用 RRF（倒数排名融合）合并
- 解决单一检索的局限：向量擅长语义但漏关键词，BM25 擅长精确匹配但无语义理解
- RRF 公式：`score(d) = Σ 1/(k + rank_r(d))`，只看排名不看原始分数

**关键 API：**
```python
from langchain_community.retrievers import BM25Retriever
# EnsembleRetriever：0.3 用 langchain.retrievers，最新用 langchain_classic.retrievers
from langchain.retrievers import EnsembleRetriever

# BM25（需 pip install rank_bm25）
bm25_retriever = BM25Retriever.from_documents(all_docs, k=5)

# 向量检索
dense_retriever = vector_store.as_retriever(search_kwargs={"k": 5})

# 混合
ensemble = EnsembleRetriever(
    retrievers=[bm25_retriever, dense_retriever],
    weights=[0.5, 0.5],  # 生产常用 0.3/0.7 偏向稠密
)
```

**注意：** BM25Retriever 在 `langchain_community`，需额外安装 `rank_bm25`。中文需配合 jieba 分词作为 `preprocess_func`。

**官方文档：**
- EnsembleRetriever：https://python.langchain.com/docs/how_to/ensemble_retriever/
- BM25：https://python.langchain.com/docs/integrations/retrievers/bm25/

**自测方向：** 用 `langchain_community.retrievers.BM25Retriever` 检索一段中文文本，对比纯向量检索和 BM25 检索的结果差异。观察关键词精确匹配时哪个更准。

---

#### 知识点 2：Cross-encoder 重排序

**核心概念：**
- Bi-Encoder（向量检索）：query 和 doc 分别编码，算余弦相似度，无深度交互
- Cross-encoder：query 和 doc 拼接成单一序列送入模型，自注意力充分交互，精度更高但每对都要一次推理
- 工业范式：Bi-Encoder 初筛 Top-20 → Cross-encoder 精排到 Top-5
- 模型选型：`BAAI/bge-reranker-v2-m3`（568M，多语言，多数场景的默认选择）

**关键 API：**
```python
# CrossEncoderReranker：0.3 用 langchain.retrievers.document_compressors，最新用 langchain_classic
from langchain.retrievers.document_compressors import CrossEncoderReranker
from langchain_community.cross_encoders import HuggingFaceCrossEncoder
from langchain.retrievers.contextual_compression import ContextualCompressionRetriever

cross_encoder = HuggingFaceCrossEncoder(model_name="BAAI/bge-reranker-v2-m3")
reranker = CrossEncoderReranker(model=cross_encoder, top_n=3)

# 检索 → 重排
compression_retriever = ContextualCompressionRetriever(
    base_compressor=reranker,
    base_retriever=vector_store.as_retriever(search_kwargs={"k": 20}),  # 先召回多
)
# 或配合 EnsembleRetriever
compression_retriever = ContextualCompressionRetriever(
    base_compressor=reranker,
    base_retriever=ensemble_retriever,
)
```

**注意：** `CrossEncoderReranker` 不在 `langchain_community.document_compressors`（旧路径会报错）。第一次运行会下载约 600MB 模型。

**官方文档：**
- Cross encoder reranker：https://docs.langchain.com/oss/python/integrations/document_transformers/cross_encoder_reranker
- Contextual Compression：https://python.langchain.com/docs/how_to/contextual_compression/

**自测方向：** 用 `HuggingFaceCrossEncoder` 加载 `BAAI/bge-reranker-v2-m3`，对 5 个 (query, doc) 对打分，观察分数排序是否合理。

---

#### 知识点 3：MMR（最大边际相关性）

**核心概念：**
- MMR 在"与 query 的相似度"和"已选文档间的多样性"之间做平衡
- 避免返回内容高度重复的 chunk
- `lambda_mult`：0=最大多样性，1=最大相关性

**关键 API：**
```python
retriever = vector_store.as_retriever(
    search_type="mmr",
    search_kwargs={
        "k": 4,
        "fetch_k": 20,        # 先召回的候选数（必须 >= k）
        "lambda_mult": 0.5,  # 平衡点
    },
)
```

**官方文档：**
- Vector store retriever：https://python.langchain.com/docs/how_to/vectorstore_retriever/

**自测方向：** 对比 `search_type="similarity"` 和 `"mmr"` 的检索结果，观察 MMR 是否减少了重复内容。

---

#### 知识点 4：chunk 参数调优方法论

**核心概念：**
- chunk 太小会切碎语义，太大会稀释相关性信号
- 调优不能拍脑袋，必须用评估集系统对比
- overlap 经验法则：约为 chunk_size 的 10%-20%

**调优步骤：**
1. 准备 5-10 个测试问题 + 预期答案
2. 跑三档配置：256/20、512/50（baseline）、1024/100
3. 对每个配置：切片 → 建库 → 检索 → 记录检索结果质量
4. 选最优配置

**自测方向：** 对同一个 PDF 用三种 chunk_size 切分，统计每种配置下的 chunk 数量和平均长度，观察内容边界是否合理。

---

### 模块 2.3：Docker 与工程化

#### 知识点 1：Dockerfile 多阶段构建

**核心概念：**
- 多阶段构建分离构建依赖和运行时依赖，镜像可从 1.2GB 降到 80MB
- `python:3.12-slim` 是体积与功能的平衡点
- 层缓存优化：先 `COPY requirements.txt` 再 `pip install`，依赖不变时复用缓存
- 非 root 用户运行（固定 UID 1000）
- exec 形式 `CMD` 确保优雅关闭

**关键配置示例：**
```dockerfile
# 阶段1：builder
FROM python:3.12-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir -r requirements.txt

# 阶段2：runtime
FROM python:3.12-slim
WORKDIR /app
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN useradd -m -u 1000 appuser
USER 1000
COPY --chown=appuser:appuser . /app
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
```

**官方文档：**
- Dockerfile 参考：https://docs.docker.com/reference/dockerfile/
- FastAPI Docker 部署：https://fastapi.tiangolo.com/deployment/docker/

**自测方向：** 写一个最小 Dockerfile，构建后 `docker run` 验证 FastAPI 能访问 `/docs`。

---

#### 知识点 2：docker-compose 多服务编排

**核心概念：**
- `healthcheck` 告知 Compose 如何判断服务就绪
- `depends_on` + `condition: service_healthy` 让 API 等待依赖服务
- named volumes 持久化数据，存活 `docker compose down`
- 大模型缓存：挂载 `HF_HOME` 目录，避免每次重启重下

**关键配置示例：**
```yaml
services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - HF_HOME=/data/hf_cache
    volumes:
      - model-cache:/data/hf_cache
      - ./data:/app/data
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:8000/health"]
      interval: 30s
      timeout: 3s
      retries: 3
      start_period: 40s

volumes:
  model-cache:
```

**官方文档：**
- Compose file 参考：https://docs.docker.com/compose/compose-file/
- Compose Quickstart：https://docs.docker.com/compose/gettingstarted/

**自测方向：** 写一个 docker-compose.yml，用 `docker compose up` 启动，验证 API 可访问。

---

#### 知识点 3：结构化日志（loguru）

**核心概念：**
- 生产日志必须结构化（JSON），方便 ELK/Loki 索引
- loguru 三行配置即可用，内置 rotation/retention/compression
- 请求追踪：入口生成 request_id，通过 contextvars 绑定

**关键配置示例：**
```python
from loguru import logger
import sys

logger.remove()
logger.add(sys.stdout, level="INFO",
           format="{time:YYYY-MM-DDTHH:mm:ss} | {level:<8} | {message}",
           colorize=True)
# 生产环境 JSON + 轮转
logger.add("logs/app.log", level="INFO",
           rotation="10 MB", retention="30 days", compression="gz",
           serialize=True, enqueue=True)
```

**官方文档：**
- loguru：https://loguru.readthedocs.io/

**自测方向：** 配置 loguru 输出到文件和终端，写几条不同 level 的日志，验证文件轮转。

---

#### 知识点 4：CI/CD（Gitee Go / GitHub Actions）

**核心概念：**
- 流水线三段：lint → test → build & push
- pip 缓存以 `hashFiles('requirements.txt')` 为 key，安装时间降 60-80%
- Gitee Go 配置文件在 `.workflow/` 目录

**Gitee Go 配置示例：**
```yaml
name: python-ci
displayName: Python CI
triggers:
  push:
    - branch: master
stages:
  - name: build
    steps:
      - step: build@python
        name: build_python
        displayName: Python 构建
        pythonVersion: "3.12"
        commands:
          - pip install -r requirements.txt
          - pip install pytest ruff
          - ruff check .
          - pytest tests/ -v
```

**官方文档：**
- Gitee Go：https://gitee.com/help/categories/86
- GitHub Actions Python：https://docs.github.com/en/actions/automating-builds-and-tests/building-and-testing-python

**自测方向：** 在 Gitee 仓库配置一个最简单的流水线，push 代码后查看是否自动执行。

---

#### 知识点 5：环境分离（pydantic-settings）

**核心概念：**
- `pydantic-settings` 的 `BaseSettings` 声明式管理配置
- 优先级：默认值 → .env 文件 → 环境变量 → 初始化参数
- 根据 `ENV` 环境变量选择 `.env.dev` / `.env.prod`

**关键配置示例：**
```python
from pydantic_settings import BaseSettings, SettingsConfigDict
import os

class Settings(BaseSettings):
    debug: bool = False
    deepseek_api_key: str
    model_cache_dir: str = "/data/hf_cache"

    model_config = SettingsConfigDict(
        env_file=f".env.{os.getenv('ENV', 'dev')}",
        env_file_encoding="utf-8",
        extra="ignore",
    )

settings = Settings()
```

**官方文档：**
- Pydantic Settings：https://docs.pydantic.com/latest/concepts/pydantic_settings/

**自测方向：** 定义 Settings 类，分别用 `ENV=dev` 和 `ENV=prod` 加载不同 .env 文件，验证配置切换。

---

### 模块 2.4：数据层增强

#### 知识点 1：文档元数据管理

**核心概念：**
- LangChain 的 `Document` 对象自带 `metadata` 字典
- 导入时可附加：文件名、导入时间、文件类型、自定义标签
- ChromaDB 的 `where` 条件可按 metadata 过滤检索

**关键 API：**
```python
from datetime import datetime
from langchain_core.documents import Document

doc = Document(
    page_content="...",
    metadata={
        "source": "sample.pdf",
        "page": 1,
        "imported_at": datetime.now().isoformat(),
        "file_type": "pdf",
    }
)

# ChromaDB 按 metadata 过滤
results = vector_store.similarity_search(
    query, k=3,
    filter={"file_type": "pdf"}  # 只在 PDF 中检索
)
```

**自测方向：** 给文档添加 metadata，用 `filter` 参数过滤检索，验证只返回符合条件的文档。

---

#### 知识点 2：文档列表与删除

**核心概念：**
- ChromaDB 的 collection 支持按 id 删除文档
- `collection.get()` 可查询已存储的文档列表
- 需维护文档索引（文件名 → chunk ids 的映射）

**关键 API：**
```python
# Chroma 底层 API
collection = chroma_client.get_collection("rag_kb")
# 查看所有文档的 id 和 metadata
results = collection.get(include=["metadatas"])
# 按 id 删除
collection.delete(ids=["chunk_1", "chunk_2"])
# 按 metadata 条件删除
collection.delete(where={"source": "old_doc.pdf"})
```

**自测方向：** 存入几个文档后，用 `collection.get()` 查看列表，用 `collection.delete()` 删除一个，验证检索结果变化。

---

## 详细 Task 分解

### Task 1：项目结构重组 + FastAPI 骨架

**做什么：** 把项目从 CLI 工具重构为 API 服务，保留 CLI 作为备用入口

**为什么：** API 化是工程化的第一步，后续所有功能都围绕 API 展开。同时保留 CLI 方便本地调试。

**前置知识：** 模块 2.1 知识点 1、2、3、5

**自测练习：**
1. 用 FastAPI 创建一个 hello world 应用，定义 `/health` 路由，`fastapi dev` 启动后 curl 测试
2. 定义 `QuestionRequest`（Pydantic v2 BaseModel）模型，故意传非法输入看 422 错误
3. 用 `Depends()` 写一个依赖，两个路由共享，`print(id())` 验证只创建一次

**项目要求：**
- 新建 `app/` 目录，放 API 相关代码：`app/main.py`、`app/routers/`、`app/schemas.py`
- 保留 `rag_kb/` 原有模块不动，API 层调用它们
- `app/main.py`：FastAPI 实例 + lifespan（初始化 RAGChain）+ 注册路由
- `app/schemas.py`：定义所有请求/响应模型（QuestionRequest、AnswerResponse、DocumentInfo 等）
- `app/routers/documents.py`：文档相关路由
- `app/routers/qa.py`：问答相关路由
- `GET /health` 返回 `{"status": "ok"}`
- `GET /docs` 自动可用（FastAPI 自带）
- `requirements.txt` 加入 `fastapi[standard]`、`python-multipart`

**验收标准：**
- [ ] `fastapi dev app/main.py` 能启动
- [ ] 访问 `http://localhost:8000/docs` 看到 Swagger UI
- [ ] `/health` 返回正常
- [ ] Pydantic 模型出现在文档中
- [ ] 原有 `python -m rag_kb` CLI 仍然可用

---

### Task 2：文档管理 API

**做什么：** 实现文档导入、列表、删除的 REST 接口

**为什么：** 这是 API 化的核心功能，也是数据层增强的基础

**前置知识：** 模块 2.1 知识点 4（文件上传）、模块 2.4 知识点 1、2

**自测练习：**
1. 用 `UploadFile` 写一个文件上传接口，curl 的 `-F` 测试上传
2. 给 Document 加 metadata（source、imported_at），检索时用 `filter` 过滤
3. 用 `collection.get()` 查看 ChromaDB 中的文档列表

**项目要求：**
- `POST /api/v1/documents/upload`：上传文件，保存到 data 目录，触发导入，返回 `{"filename": ..., "chunks": N}`
- `POST /api/v1/documents/path`：传文件路径导入（复用已有 `add_document` 逻辑）
- `GET /api/v1/documents`：返回已导入文档列表（文件名、chunk 数、导入时间）
- `DELETE /api/v1/documents/{filename}`：删除指定文档的所有 chunk
- 导入时给每个 chunk 附加 metadata：`source`（文件名）、`imported_at`（时间戳）、`file_type`
- 文件类型校验：只允许 .pdf/.txt/.md，否则 400

**验收标准：**
- [ ] 上传 PDF/TXT/MD 三种文件都能成功
- [ ] `GET /documents` 能看到导入记录
- [ ] `DELETE` 后再检索，该文档内容不再出现
- [ ] 上传不支持的格式返回 400
- [ ] Swagger UI 中所有接口有描述

---

### Task 3：问答 API（含流式输出）

**做什么：** 把 `ask` 和 `ask_with_sources` 暴露为 API，支持 SSE 流式输出

**为什么：** 问答是核心功能，流式输出是 Stage 3 Agent 的前置技能

**前置知识：** 模块 2.1 知识点 6（异常处理）、LangChain 异步方法

**自测练习：**
1. 写一个 `POST /ask` 接口，返回 `AnswerResponse` 模型
2. 用 `chain.astream()` 写一个 SSE 流式接口，前端 `curl -N` 能看到逐字输出
3. 定义自定义异常 `RAGError`，注册全局异常处理器

**项目要求：**
- `POST /api/v1/ask`：接收 `QuestionRequest`，返回 `AnswerResponse`（answer + sources）
- `POST /api/v1/ask/stream`：SSE 流式返回，逐 token 输出
- 流式接口用 `StreamingResponse` + `chain.astream()`，media_type = `text/event-stream`
- **重构 `ask_with_sources`**：把检索从 chain 内拆出来（Stage 1 留的债），检索一次，结果同时用于生成和返回 sources
- 统一错误响应格式：`{"error": "CODE", "message": "..."}`
- API Key 未配置时返回 503 而非崩溃

**验收标准：**
- [ ] `POST /ask` 返回正确答案和来源
- [ ] `curl -N -X POST /ask/stream` 能看到逐字输出
- [ ] 不传 question 时返回 422 且错误信息清晰
- [ ] `ask_with_sources` 只检索一次（不是两次）
- [ ] 异常时返回统一格式的错误 JSON

---

### Task 4：检索优化 — 混合检索 + 重排序

**做什么：** 实现混合检索（BM25 + 向量）和 cross-encoder 重排序

**为什么：** 这是 RAG 调优的核心，也是简历亮点

**前置知识：** 模块 2.2 知识点 1、2

**自测练习：**
1. 用 `BM25Retriever.from_documents()` 建索引，检索一段中文文本
2. 用 `EnsembleRetriever` 合并 BM25 和向量检索，对比单一检索的结果
3. 加载 `BAAI/bge-reranker-v2-m3`，对 5 个 (query, doc) 对打分

**项目要求：**
- 新建 `rag_kb/retriever.py`：封装混合检索 + 重排序逻辑
- `HybridRetriever` 类：`add_documents()` 时同时更新 BM25 索引和向量库
- 可配置：纯向量、纯 BM25、混合、混合+rerank，四种模式
- rerank 模型用 `BAAI/bge-reranker-v2-m3`，延迟加载单例
- 混合检索的 weights 和 rerank 的 top_n 可通过 config 配置
- `config.py` 新增：`RETRIEVER_MODE`、`BM25_WEIGHT`、`DENSE_WEIGHT`、`RERANK_MODEL`、`RERANK_TOP_N`
- 检索结果去重：按 page_content 去重
- 中文 BM25 需配合 jieba 分词

**验收标准：**
- [ ] 四种检索模式都能正常工作
- [ ] 关键词精确匹配时，BM25 比纯向量更准
- [ ] 语义模糊查询时，向量比 BM25 更准
- [ ] 混合 + rerank 的结果质量最好（主观判断或简单对比）
- [ ] `requirements.txt` 加入 `rank_bm25`、`jieba`

---

### Task 5：检索优化 — MMR + chunk 调优

**做什么：** 加入 MMR 去重检索，系统对比不同 chunk 参数效果

**为什么：** MMR 解决重复内容问题，chunk 调优是 RAG 调优的基本功

**前置知识：** 模块 2.2 知识点 3、4

**自测练习：**
1. 对比 `search_type="similarity"` 和 `"mmr"` 的检索结果
2. 用三种 chunk_size 切分同一个 PDF，统计 chunk 数量和平均长度

**项目要求：**
- `vector_store.py` 的 `as_retriever` 支持 `search_type="mmr"` 参数
- `config.py` 新增：`SEARCH_TYPE`（similarity/mmr）、`MMR_FETCH_K`、`MMR_LAMBDA`
- 写一个调优脚本 `scripts/chunk_tuning.py`：
  - 对同一文档用 3 种配置切分（256/20、512/50、1024/100）
  - 每种配置建库 → 检索 5 个预设问题 → 记录检索结果的 chunk 内容
  - 输出对比表格（终端 Rich 表格或 Markdown）
- 在 `data/test_questions.json` 中准备 5-10 个测试问题

**验收标准：**
- [ ] MMR 模式检索结果重复内容减少
- [ ] 调优脚本能跑通，输出三种配置的对比
- [ ] 能从对比中选出相对最优的 chunk 配置

---

### Task 6：Docker 容器化

**做什么：** 写 Dockerfile 和 docker-compose.yml，一键启动服务

**为什么：** 容器化是部署的标准方式，也是面试常问

**前置知识：** 模块 2.3 知识点 1、2

**自测练习：**
1. 写一个最小 Dockerfile，构建后 `docker run` 验证 FastAPI 可访问
2. 写 docker-compose.yml，挂载模型缓存目录，`docker compose up` 启动

**项目要求：**
- `Dockerfile`：多阶段构建，python:3.12-slim，非 root 用户，exec 形式 CMD
- `.dockerignore`：排除 .git、venv、__pycache__、.env、chroma_db、models
- `docker-compose.yml`：
  - api 服务：构建并运行 FastAPI
  - 挂载 named volume 持久化 chroma_db 数据
  - 挂载 named volume 缓存 HuggingFace 模型（`HF_HOME=/data/hf_cache`）
  - healthcheck 检查 `/health`
  - 环境变量从 `.env` 读取
- 首次构建会下载 BGE-M3 模型（约 2GB），挂载缓存后二次启动秒级

**验收标准：**
- [ ] `docker compose up --build` 一键启动
- [ ] 容器内 `http://localhost:8000/docs` 可访问
- [ ] 重启容器后向量库数据不丢失
- [ ] 重启容器后不重新下载模型（缓存生效）
- [ ] 镜像大小合理（不含模型缓存应 < 500MB）

---

### Task 7：日志 + 环境分离 + CI/CD

**做什么：** 加结构化日志、环境配置分离、配置 CI 流水线

**为什么：** 这是工程化的最后一块拼图，可观测性和自动化是专业项目的标志

**前置知识：** 模块 2.3 知识点 3、4、5

**自测练习：**
1. 配置 loguru 输出 JSON 格式日志到文件，带 rotation
2. 用 `pydantic-settings` 定义 Settings，`ENV=dev`/`ENV=prod` 切换
3. 在 Gitee 仓库配置一个流水线文件，push 后查看执行结果

**项目要求：**
- 集成 loguru：
  - 请求日志：method、path、status、耗时
  - 错误日志：异常堆栈完整记录
  - 配置文件轮转（10MB、保留 30 天）
- 用 `pydantic-settings` 重构 `config.py`：
  - `Settings` 类继承 `BaseSettings`
  - 支持 `.env.dev` / `.env.prod` 切换
  - 保留原有配置项不破坏 CLI
- `.workflow/ci.yml`（Gitee Go）或 `.github/workflows/ci.yml`：
  - lint（ruff check）
  - test（pytest）
  - 至少能跑通

**验收标准：**
- [ ] API 请求有结构化日志输出
- [ ] `ENV=prod` 和 `ENV=dev` 加载不同配置
- [ ] push 代码后 CI 自动执行
- [ ] lint 和 test 在 CI 中通过

---

### Task 8：数据层增强 — 文档管理完善

**做什么：** 完善文档管理功能，支持增量更新和元数据查询

**为什么：** 产品完整度，也是 Stage 3 Agent 记忆系统的数据层基础

**前置知识：** 模块 2.4 知识点 1、2

**自测练习：**
1. 给文档加 metadata，用 `filter` 检索指定来源
2. 用 `collection.delete(where=...)` 按条件批量删除

**项目要求：**
- `GET /api/v1/documents/{filename}/info`：返回指定文档的 chunk 数、导入时间、总字符数
- `POST /api/v1/documents/reindex`：重新索引指定文档（删除旧 chunk + 重新导入）
- 导入时检查是否已存在同名文档，已存在则提示是否覆盖
- 文档列表支持按 file_type 过滤、按时间排序
- API 文档中所有接口有 description 和 example

**验收标准：**
- [ ] 同名文档重复导入有提示
- [ ] reindex 后旧内容被替换
- [ ] 文档列表可按类型过滤
- [ ] Swagger UI 中有完整的接口示例

---

## Stage 2 整体验收标准

- [ ] API 能被 Postman/curl 正常调用，Swagger UI 完整
- [ ] 有检索优化前后的效果对比数据（Task 5 调优脚本输出）
- [ ] `docker compose up` 一键启动服务
- [ ] 有 CI 自动跑 lint + test
- [ ] 四种检索模式可切换
- [ ] `ask_with_sources` 只检索一次（Stage 1 的债还清）
- [ ] 日志结构化，环境可分离
- [ ] 原有 CLI 不被破坏（向后兼容）

---

## 重要提醒

1. **import 路径变化：** LangChain 2026 年有重大变化，部分组件迁移到 `langchain_classic` 包。如果你的环境是 0.3.x，用 `langchain.retrievers` 旧路径；如果升级到最新，可能需要 `langchain_classic.retrievers`。**写代码前先用 `pip show langchain` 确认版本。**

2. **模型下载：** rerank 模型 `BAAI/bge-reranker-v2-m3` 约 600MB，首次运行需等待。确保 `HF_ENDPOINT=https://hf-mirror.com` 已设置。

3. **不要急：** Stage 2 内容比 Stage 1 多很多，每个 Task 给自己 3-5 天，遇到不懂的先查文档再问。

4. **测试意识：** Stage 1 的测试欠债这次要补上。至少给 `format_docs`、`config.get_all()`、检索逻辑写单元测试。

5. **重构时机：** Task 3 要重构 `ask_with_sources`，这是把 Stage 1 的设计债还清的好机会。理解清楚为什么当前设计会双检索，重构后为什么不会。
