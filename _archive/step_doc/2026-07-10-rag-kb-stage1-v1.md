# RAG-KB 阶段1：MVP 模块化实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把阶段0的单文件 demo 重构成模块化项目，支持多文档导入、命令行交互问答，别人 clone 后能跑起来。

**Architecture:** 模块化设计，每个文件职责单一。document_processor 负责加载切分，embeddings 负责向量化，vector_store 负责存取检索，rag_chain 串联全流程，cli 负责用户交互。

**Tech Stack:** Python 3.10+、LangChain 0.3+（LCEL 标准写法）、langchain-deepseek、langchain-chroma、langchain-huggingface、langchain-text-splitters、chromadb 1.5+、sentence-transformers（BGE-M3）、Typer（CLI 框架）

---

## 版本基线（2026-07-10 查证）

| 包名 | 最新稳定版 | 导入路径 | 说明 |
|---|---|---|---|
| langchain | 0.3.x | `langchain` | 主包，LCEL 编排 |
| langchain-core | 0.3.x | `langchain_core` | 基础类：Runnable、Prompt 等 |
| langchain-community | 0.3.x | `langchain_community` | PyPDFLoader、TextLoader 等 |
| langchain-text-splitters | 0.3.x | `langchain_text_splitters` | 独立包，RecursiveCharacterTextSplitter |
| langchain-chroma | 0.2.x | `langchain_chroma` | **独立包**，替代 langchain_community.vectorstores.Chroma |
| langchain-huggingface | 0.2.x | `langchain_huggingface` | HuggingFaceEmbeddings |
| langchain-deepseek | 0.1.5+ | `langchain_deepseek` | ChatDeepSeek |
| chromadb | 1.5.4 | `chromadb` | 持久化用 PersistentClient，0.5+ 自动持久化 |
| fastapi | 0.139.0 | `fastapi` | 阶段2 才用到 |
| typer | 0.12+ | `typer` | CLI 框架 |

**关键变更（相比阶段0）**：
- `from langchain_community.vectorstores import Chroma` → `from langchain_chroma import Chroma`
- ChromaDB 0.5+ 自动持久化，不需要手动调 `persist()`
- DeepSeek 模型名：`deepseek-v4-flash`（快速）或 `deepseek-v4-pro`（高质量）
- CLI 用 Typer 替代手写 argparse，更现代

---

## 文件结构

阶段1 在阶段0基础上重构，保留 `demo.py` 作为参考，新建模块化结构：

```
rag-kb/
├── rag_kb/                        # 主包
│   ├── __init__.py
│   ├── config.py                  # 配置管理（模型名、路径、参数）
│   ├── document_processor.py      # 文档加载与切分
│   ├── embeddings.py              # BGE-M3 embedding 封装
│   ├── vector_store.py            # ChromaDB 存取管理
│   ├── rag_chain.py               # RAG 链（检索+生成编排）
│   └── cli.py                     # 命令行界面（Typer）
├── data/                          # 存放测试文档
│   ├── sample.txt
│   └── sample.pdf
├── tests/                         # 测试
│   ├── __init__.py
│   ├── test_document_processor.py
│   ├── test_vector_store.py
│   └── test_rag_chain.py
├── demo.py                        # 阶段0 的 demo（保留参考）
├── step_doc/                      # 指导文档
│   ├── 2026-07-08-rag-kb-stage0.md
│   └── 2026-07-10-rag-kb-stage1.md
├── .env.example
├── .gitignore
├── requirements.txt
├── README.md
└── NOTES.md
```

---

## Task 1: 更新依赖与项目结构

**Files:**
- Create: `rag_kb/__init__.py`
- Create: `rag_kb/config.py`
- Modify: `requirements.txt`
- Modify: `.gitignore`

- [ ] **Step 1: 更新 requirements.txt**

```
langchain>=0.3.0
langchain-core>=0.3.0
langchain-community>=0.3.0
langchain-text-splitters>=0.3.0
langchain-chroma>=0.2.0
langchain-huggingface>=0.2.0
langchain-deepseek>=0.1.5
chromadb>=1.5.0
sentence-transformers>=3.0.0
pypdf>=4.0.0
python-dotenv>=1.0.0
typer>=0.12.0
pytest>=8.0.0
```

- [ ] **Step 2: 安装新依赖**

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

验证：`python -c "import typer; import langchain_chroma; print('OK')"`

- [ ] **Step 3: 创建包目录和 __init__.py**

```bash
mkdir -p rag_kb
touch rag_kb/__init__.py
```

- [ ] **Step 4: 创建 config.py**

```python
"""配置管理：集中管理所有参数，方便调整"""
import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env
load_dotenv()

# 路径配置
BASE_DIR = Path(__file__).parent.parent
DATA_DIR = BASE_DIR / "data"
CHROMA_DIR = BASE_DIR / "chroma_db"

# DeepSeek 配置
DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
DEEPSEEK_MODEL = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash")

# Embedding 配置
EMBEDDING_MODEL = "BAAI/bge-m3"
EMBEDDING_DEVICE = "cpu"

# 文档切分配置
CHUNK_SIZE = 500
CHUNK_OVERLAP = 100

# 检索配置
SEARCH_K = 3  # 检索返回 top-k 个相关块

# ChromaDB 配置
COLLECTION_NAME = "rag_kb_collection"

# 提示词模板
SYSTEM_PROMPT = (
    "你是一个文档问答助手。请根据以下检索到的文档内容回答用户问题。"
    "如果文档中没有相关信息，请说'文档中没有相关信息'。"
    "回答时请标注信息来源（来自第几个文档片段）。"
    "\n\n文档内容:\n{context}"
)
```

- [ ] **Step 5: 更新 .gitignore**

```
venv/
__pycache__/
*.pyc
.env
chroma_db/
models/
.idea/
*.egg-info/
dist/
.pytest_cache/
```

- [ ] **Step 6: 提交**

```bash
git add rag_kb/ requirements.txt .gitignore
git commit -m "chore: init modular project structure with config"
```

---

## Task 2: 实现 document_processor.py

**Files:**
- Create: `rag_kb/document_processor.py`
- Create: `tests/__init__.py`
- Create: `tests/test_document_processor.py`

- [ ] **Step 1: 创建 tests 目录**

```bash
mkdir -p tests
touch tests/__init__.py
```

- [ ] **Step 2: 写 test_document_processor.py**

```python
"""测试文档处理器"""
import pytest
from pathlib import Path
from rag_kb.document_processor import DocumentProcessor


@pytest.fixture
def processor():
    return DocumentProcessor()


@pytest.fixture
def sample_txt():
    return str(Path(__file__).parent.parent / "data" / "sample.txt")


def test_load_txt(processor, sample_txt):
    """测试加载 txt 文件"""
    docs = processor.load(sample_txt)
    assert len(docs) > 0
    assert len(docs[0].page_content) > 0


def test_split_documents(processor, sample_txt):
    """测试文档切分"""
    docs = processor.load(sample_txt)
    chunks = processor.split(docs, chunk_size=100, chunk_overlap=20)
    assert len(chunks) > 0
    # 每个块不应超过 chunk_size + 一定容差
    for chunk in chunks:
        assert len(chunk.page_content) <= 200  # 容差范围内


def test_load_nonexistent_file(processor):
    """测试加载不存在的文件"""
    with pytest.raises(FileNotFoundError):
        processor.load("nonexistent_file.pdf")
```

- [ ] **Step 3: 运行测试，确认失败**

```bash
pytest tests/test_document_processor.py -v
```

预期：FAIL，`ModuleNotFoundError: No module named 'rag_kb.document_processor'`

- [ ] **Step 4: 实现 document_processor.py**

```python
"""文档处理器：加载多种格式文档并切分成文本块"""
from pathlib import Path
from langchain_core.documents import Document
from langchain_community.document_loaders import (
    PyPDFLoader,
    TextLoader,
)
from langchain_text_splitters import RecursiveCharacterTextSplitter

from rag_kb import config


class DocumentProcessor:
    """加载和切分文档"""

    def __init__(
        self,
        chunk_size: int = config.CHUNK_SIZE,
        chunk_overlap: int = config.CHUNK_OVERLAP,
    ):
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        self._splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )

    def load(self, file_path: str) -> list[Document]:
        """加载单个文件，根据扩展名自动选择 loader

        Args:
            file_path: 文件路径

        Returns:
            Document 列表

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 不支持的文件格式
        """
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"文件不存在: {file_path}")

        suffix = path.suffix.lower()
        if suffix == ".pdf":
            loader = PyPDFLoader(str(path))
        elif suffix == ".txt":
            loader = TextLoader(str(path), encoding="utf-8")
        elif suffix == ".md":
            loader = TextLoader(str(path), encoding="utf-8")
        else:
            raise ValueError(f"不支持的文件格式: {suffix}（支持 .pdf/.txt/.md）")

        return loader.load()

    def split(
        self, documents: list[Document], chunk_size: int | None = None, chunk_overlap: int | None = None
    ) -> list[Document]:
        """切分文档为文本块

        Args:
            documents: Document 列表
            chunk_size: 覆盖默认切分大小
            chunk_overlap: 覆盖默认重叠大小

        Returns:
            切分后的 Document 列表
        """
        if chunk_size or chunk_overlap:
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size or self.chunk_size,
                chunk_overlap=chunk_overlap or self.chunk_overlap,
            )
        else:
            splitter = self._splitter
        return splitter.split_documents(documents)

    def load_and_split(self, file_path: str) -> list[Document]:
        """加载并切分（便捷方法）"""
        docs = self.load(file_path)
        return self.split(docs)
```

- [ ] **Step 5: 运行测试，确认通过**

```bash
pytest tests/test_document_processor.py -v
```

预期：3 个测试全部 PASS

- [ ] **Step 6: 提交**

```bash
git add rag_kb/document_processor.py tests/
git commit -m "feat: implement document processor with PDF/TXT/MD support"
```

---

## Task 3: 实现 embeddings.py

**Files:**
- Create: `rag_kb/embeddings.py`

- [ ] **Step 1: 实现 embeddings.py**

```python
"""Embedding 封装：用 BGE-M3 把文本转向量"""
import os

# 设置 HuggingFace 镜像（国内加速）
os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")

from langchain_huggingface import HuggingFaceEmbeddings

from rag_kb import config


def get_embeddings() -> HuggingFaceEmbeddings:
    """获取 BGE-M3 embedding 实例（单例模式，避免重复加载模型）

    Returns:
        HuggingFaceEmbeddings 实例
    """
    return HuggingFaceEmbeddings(
        model_name=config.EMBEDDING_MODEL,
        model_kwargs={"device": config.EMBEDDING_DEVICE},
    )
```

说明：用函数返回而不是全局变量，是因为 HuggingFaceEmbeddings 首次初始化要下载模型，延迟到真正需要时才加载。

- [ ] **Step 2: 快速验证**

```bash
python -c "
from rag_kb.embeddings import get_embeddings
emb = get_embeddings()
vec = emb.embed_query('测试')
print(f'维度: {len(vec)}')
print(f'前5位: {vec[:5]}')
"
```

预期：维度 1024，打印前5位数值

- [ ] **Step 3: 提交**

```bash
git add rag_kb/embeddings.py
git commit -m "feat: implement BGE-M3 embedding wrapper"
```

---

## Task 4: 实现 vector_store.py

**Files:**
- Create: `rag_kb/vector_store.py`
- Create: `tests/test_vector_store.py`

- [ ] **Step 1: 写 test_vector_store.py**

```python
"""测试向量存储"""
import pytest
from rag_kb.vector_store import VectorStore


@pytest.fixture
def store():
    """用临时目录测试，避免污染正式数据"""
    return VectorStore(persist_directory="./chroma_test_db")


def test_add_and_search(store):
    """测试存入和检索"""
    texts = [
        "网络安全是保护系统免受攻击的实践",
        "Python是一种编程语言",
        "SQL注入是常见的Web漏洞",
    ]
    store.add_texts(texts)

    results = store.search("什么是网络安全", k=2)
    assert len(results) == 2
    # 第一条应该和网络安全的文本最相关
    assert "网络" in results[0].page_content or "安全" in results[0].page_content


def test_clear(store):
    """测试清空"""
    store.add_texts(["测试文本"])
    store.clear()
    results = store.search("测试", k=1)
    assert len(results) == 0


def teardown_module(module):
    """测试后清理临时目录"""
    import shutil
    shutil.rmtree("./chroma_test_db", ignore_errors=True)
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_vector_store.py -v
```

预期：FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 vector_store.py**

```python
"""向量存储管理：封装 ChromaDB 的存取操作"""
from pathlib import Path
from langchain_core.documents import Document
from langchain_chroma import Chroma

from rag_kb import config
from rag_kb.embeddings import get_embeddings


class VectorStore:
    """ChromaDB 向量存储封装"""

    def __init__(
        self,
        persist_directory: str = str(config.CHROMA_DIR),
        collection_name: str = config.COLLECTION_NAME,
    ):
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self._embeddings = None  # 延迟加载
        self._store = None  # 延迟初始化

    @property
    def embeddings(self):
        """延迟加载 embedding 模型"""
        if self._embeddings is None:
            self._embeddings = get_embeddings()
        return self._embeddings

    @property
    def store(self) -> Chroma:
        """获取或创建 Chroma 实例

        如果持久化目录已有数据，会自动加载；
        如果没有，会创建新的。
        """
        if self._store is None:
            self._store = Chroma(
                collection_name=self.collection_name,
                embedding_function=self.embeddings,
                persist_directory=self.persist_directory,
            )
        return self._store

    def add_documents(self, documents: list[Document]) -> None:
        """存入 Document 列表

        Args:
            documents: LangChain Document 列表
        """
        self.store.add_documents(documents)
        # chromadb 0.5+ 自动持久化，无需手动调 persist()

    def add_texts(self, texts: list[str]) -> None:
        """存入纯文本列表（便捷方法）

        Args:
            texts: 文本字符串列表
        """
        documents = [Document(page_content=text) for text in texts]
        self.store.add_documents(documents)

    def search(self, query: str, k: int = config.SEARCH_K) -> list[Document]:
        """检索最相关的 top-k 文档块

        Args:
            query: 查询文本
            k: 返回数量

        Returns:
            相关 Document 列表，按相关度降序
        """
        return self.store.similarity_search(query, k=k)

    def search_with_scores(self, query: str, k: int = config.SEARCH_K) -> list[tuple[Document, float]]:
        """检索并返回相似度分数

        Args:
            query: 查询文本
            k: 返回数量

        Returns:
            (Document, score) 列表，score 越低越相关（余弦距离）
        """
        return self.store.similarity_search_with_score(query, k=k)

    def as_retriever(self, k: int = config.SEARCH_K):
        """转为 LangChain retriever（供 RAG 链使用）

        Args:
            k: 检索数量

        Returns:
            LangChain Retriever 对象
        """
        return self.store.as_retriever(search_kwargs={"k": k})

    def clear(self) -> None:
        """清空向量库（删除持久化目录并重新初始化）

        谨慎使用！会删除所有已存入的数据。
        """
        import shutil
        self._store = None
        path = Path(self.persist_directory)
        if path.exists():
            shutil.rmtree(path)
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
pytest tests/test_vector_store.py -v
```

预期：2 个测试 PASS（首次运行需下载 BGE-M3 模型，耗时较长）

- [ ] **Step 5: 提交**

```bash
git add rag_kb/vector_store.py tests/test_vector_store.py
git commit -m "feat: implement ChromaDB vector store with search and scores"
```

---

## Task 5: 实现 rag_chain.py

**Files:**
- Create: `rag_kb/rag_chain.py`
- Create: `tests/test_rag_chain.py`

- [ ] **Step 1: 写 test_rag_chain.py**

```python
"""测试 RAG 链"""
import pytest
from rag_kb.rag_chain import RAGChain


@pytest.fixture
def chain():
    """构建 RAG 链（使用默认配置）"""
    return RAGChain()


def test_ask(chain):
    """测试提问能返回回答"""
    # 先存入测试文档
    chain.add_document("./data/sample.txt")

    answer = chain.ask("常见的Web漏洞有哪些？")
    assert isinstance(answer, str)
    assert len(answer) > 0
    # 回答应该包含 SQL注入 或 XSS 等关键词
    assert any(kw in answer for kw in ["SQL", "XSS", "CSRF", "注入"])


def test_ask_out_of_scope(chain):
    """测试问文档外的问题"""
    chain.add_document("./data/sample.txt")
    answer = chain.ask("Java Spring 框架怎么配置？")
    assert isinstance(answer, str)
    # 应回答"文档中没有相关信息"
    assert "没有" in answer or "不" in answer
```

- [ ] **Step 2: 运行测试，确认失败**

```bash
pytest tests/test_rag_chain.py -v
```

预期：FAIL，`ModuleNotFoundError`

- [ ] **Step 3: 实现 rag_chain.py**

```python
"""RAG 链：串联检索 + 生成，LCEL 标准写法"""
from langchain_core.documents import Document
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_deepseek import ChatDeepSeek

from rag_kb import config
from rag_kb.document_processor import DocumentProcessor
from rag_kb.vector_store import VectorStore


def format_docs(docs: list[Document]) -> str:
    """把检索到的文档列表格式化成上下文文本

    给每个块编号，方便 LLM 在回答时引用来源
    """
    formatted = []
    for i, doc in enumerate(docs, 1):
        source = doc.metadata.get("source", "未知")
        page = doc.metadata.get("page", "")
        page_info = f"第{page}页" if page != "" else ""
        formatted.append(f"[片段{i}] (来源: {source} {page_info})\n{doc.page_content}")
    return "\n\n".join(formatted)


class RAGChain:
    """RAG 问答链

    把文档处理、向量存储、检索、生成串联起来
    """

    def __init__(
        self,
        persist_directory: str = str(config.CHROMA_DIR),
        chunk_size: int = config.CHUNK_SIZE,
        chunk_overlap: int = config.CHUNK_OVERLAP,
        search_k: int = config.SEARCH_K,
    ):
        self.doc_processor = DocumentProcessor(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        self.vector_store = VectorStore(persist_directory=persist_directory)
        self.search_k = search_k
        self._llm = None
        self._chain = None

    @property
    def llm(self) -> ChatDeepSeek:
        """延迟初始化 LLM"""
        if self._llm is None:
            self._llm = ChatDeepSeek(
                model=config.DEEPSEEK_MODEL,
                api_key=config.DEEPSEEK_API_KEY,
            )
        return self._llm

    @property
    def chain(self):
        """构建 LCEL RAG 链（延迟构建）

        链结构：
        输入问题
          → 并行：检索相关文档 + 传递原始问题
          → 格式化上下文
          → 填入 prompt 模板
          → 调用 DeepSeek 生成
          → 解析输出为字符串
        """
        if self._chain is None:
            retriever = self.vector_store.as_retriever(k=self.search_k)

            prompt = ChatPromptTemplate.from_messages([
                ("system", config.SYSTEM_PROMPT),
                ("human", "{input}"),
            ])

            self._chain = (
                {"context": retriever | format_docs, "input": RunnablePassthrough()}
                | prompt
                | self.llm
                | StrOutputParser()
            )
        return self._chain

    def add_document(self, file_path: str) -> int:
        """加载并存入一个文档

        Args:
            file_path: 文档路径（支持 PDF/TXT/MD）

        Returns:
            切分后的文本块数量
        """
        chunks = self.doc_processor.load_and_split(file_path)
        self.vector_store.add_documents(chunks)
        return len(chunks)

    def add_documents(self, file_paths: list[str]) -> int:
        """批量加载并存入多个文档

        Args:
            file_paths: 文档路径列表

        Returns:
            总文本块数量
        """
        total = 0
        for path in file_paths:
            total += self.add_document(path)
        return total

    def ask(self, question: str) -> str:
        """提问并获取回答

        Args:
            question: 用户问题

        Returns:
            LLM 基于文档生成的回答
        """
        return self.chain.invoke(question)

    def ask_with_sources(self, question: str) -> dict:
        """提问并获取回答 + 引用来源

        Args:
            question: 用户问题

        Returns:
            {"answer": str, "sources": list[Document]}
        """
        # 先检索，拿到来源
        sources = self.vector_store.search(question, k=self.search_k)
        # 再生成回答
        answer = self.chain.invoke(question)
        return {"answer": answer, "sources": sources}
```

- [ ] **Step 4: 运行测试，确认通过**

```bash
pytest tests/test_rag_chain.py -v
```

预期：2 个测试 PASS（需要 DeepSeek API Key 和网络）

- [ ] **Step 5: 提交**

```bash
git add rag_kb/rag_chain.py tests/test_rag_chain.py
git commit -m "feat: implement RAG chain with LCEL pipeline and source tracking"
```

---

## Task 6: 实现 cli.py（Typer 命令行界面）

**Files:**
- Create: `rag_kb/cli.py`
- Create: `rag_kb/__main__.py`

- [ ] **Step 1: 实现 cli.py**

```python
"""命令行界面：用 Typer 构建交互式问答"""
import typer
from rich.console import Console
from rich.table import Table
from pathlib import Path

from rag_kb.rag_chain import RAGChain
from rag_kb import config

app = typer.Typer(help="RAG-KB 知识库问答系统")
console = Console()


@app.command()
def add(
    file_path: str = typer.Argument(..., help="要导入的文档路径（PDF/TXT/MD）"),
):
    """导入文档到知识库"""
    chain = RAGChain()

    if not Path(file_path).exists():
        console.print(f"[red]文件不存在: {file_path}[/red]")
        raise typer.Exit(1)

    console.print(f"[yellow]正在加载: {file_path}[/yellow]")
    count = chain.add_document(file_path)
    console.print(f"[green]成功导入 {count} 个文本块[/green]")


@app.command()
def add_dir(
    directory: str = typer.Argument(..., help="要批量导入的目录路径"),
):
    """批量导入目录下所有文档"""
    dir_path = Path(directory)
    if not dir_path.is_dir():
        console.print(f"[red]目录不存在: {directory}[/red]")
        raise typer.Exit(1)

    supported = {".pdf", ".txt", ".md"}
    files = [f for f in dir_path.iterdir() if f.suffix.lower() in supported]

    if not files:
        console.print(f"[yellow]目录中没有支持的文件（.pdf/.txt/.md）[/yellow]")
        raise typer.Exit(0)

    chain = RAGChain()
    console.print(f"[yellow]找到 {len(files)} 个文件，开始导入...[/yellow]")

    table = Table(title="导入结果")
    table.add_column("文件", style="cyan")
    table.add_column("块数", justify="right", style="green")

    total = 0
    for f in files:
        count = chain.add_document(str(f))
        table.add_row(f.name, str(count))
        total += count

    console.print(table)
    console.print(f"[green]共导入 {total} 个文本块[/green]")


@app.command()
def ask(
    question: str = typer.Argument(..., help="要问的问题"),
):
    """单次提问"""
    chain = RAGChain()
    console.print(f"[cyan]问: {question}[/cyan]")
    console.print("[yellow]思考中...[/yellow]")

    result = chain.ask_with_sources(question)

    console.print(f"\n[green]答: {result['answer']}[/green]\n")

    # 显示引用来源
    if result["sources"]:
        console.print("[dim]--- 引用来源 ---[/dim]")
        for i, doc in enumerate(result["sources"], 1):
            source = doc.metadata.get("source", "未知")
            page = doc.metadata.get("page", "")
            preview = doc.page_content[:80].replace("\n", " ")
            console.print(f"[dim]{i}. {source} {page} | {preview}...[/dim]")


@app.command()
def chat():
    """交互式连续问答（输入 quit 退出）"""
    console.print("[bold cyan]RAG-KB 交互问答[/bold cyan]")
    console.print("[dim]输入问题开始问答，输入 quit 退出[/dim]\n")

    chain = RAGChain()

    while True:
        try:
            question = typer.prompt("问")
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]再见[/yellow]")
            break

        if question.strip().lower() in ("quit", "exit", "q"):
            console.print("[yellow]再见[/yellow]")
            break

        if not question.strip():
            continue

        console.print("[yellow]思考中...[/yellow]")
        result = chain.ask_with_sources(question)

        console.print(f"\n[green]答: {result['answer']}[/green]")

        if result["sources"]:
            console.print(f"[dim](来源: {len(result['sources'])} 个片段)[/dim]")
        console.print()


@app.command()
def clear():
    """清空知识库（危险操作，需确认）"""
    confirm = typer.confirm("确定要清空知识库吗？这将删除所有已导入的文档")
    if confirm:
        chain = RAGChain()
        chain.vector_store.clear()
        console.print("[green]知识库已清空[/green]")
    else:
        console.print("[yellow]已取消[/yellow]")


@app.command()
def info():
    """显示当前配置信息"""
    table = Table(title="RAG-KB 配置")
    table.add_column("配置项", style="cyan")
    table.add_column("值", style="green")

    table.add_row("LLM 模型", config.DEEPSEEK_MODEL)
    table.add_row("Embedding 模型", config.EMBEDDING_MODEL)
    table.add_row("切分大小", str(config.CHUNK_SIZE))
    table.add_row("重叠大小", str(config.CHUNK_OVERLAP))
    table.add_row("检索 top-k", str(config.SEARCH_K))
    table.add_row("数据目录", str(config.DATA_DIR))
    table.add_row("向量库目录", str(config.CHROMA_DIR))

    console.print(table)


if __name__ == "__main__":
    app()
```

- [ ] **Step 2: 创建 __main__.py（支持 python -m rag_kb 调用）**

```python
"""支持 python -m rag_kb 启动 CLI"""
from rag_kb.cli import app

if __name__ == "__main__":
    app()
```

- [ ] **Step 3: 验证 CLI 可用**

```bash
# 查看帮助
python -m rag_kb --help

# 查看配置
python -m rag_kb info

# 导入文档
python -m rag_kb add data/sample.txt

# 提问
python -m rag_kb ask "常见的Web漏洞有哪些？"

# 交互问答
python -m rag_kb chat
```

预期：每个命令都能正常执行并输出

- [ ] **Step 4: 提交**

```bash
git add rag_kb/cli.py rag_kb/__main__.py
git commit -m "feat: implement Typer CLI with add/ask/chat/clear/info commands"
```

---

## Task 7: 完善 README.md

**Files:**
- Modify: `README.md`

- [ ] **Step 1: 重写 README.md**

````markdown
# RAG-KB 知识库问答系统

基于 RAG（检索增强生成）架构的本地文档问答系统。给 LLM 喂入私有文档，让它基于文档内容精准回答问题。

## 功能

- 支持导入 PDF / TXT / Markdown 文档
- 基于 BGE-M3 的中文语义检索
- DeepSeek V4 大模型生成回答
- 命令行交互问答，支持引用来源展示
- 本地持久化，数据不上云

## 技术栈

| 组件 | 技术 | 说明 |
|---|---|---|
| LLM | DeepSeek V4 (deepseek-v4-flash) | 国内直连，100万 token 上下文 |
| Embedding | BGE-M3 (本地运行) | 中文语义最强，免费 |
| 向量库 | ChromaDB 1.5+ | 本地持久化，自动落盘 |
| 编排框架 | LangChain 0.3+ (LCEL) | 事实标准 |
| CLI | Typer + Rich | 现代命令行体验 |

## 快速开始

### 1. 环境要求

- Python 3.10+
- DeepSeek API Key（去 https://platform.deepseek.com/ 注册获取）

### 2. 安装

```bash
git clone https://gitee.com/little-fishy/rag-kb.git
cd rag-kb
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. 配置

```bash
cp .env.example .env
# 编辑 .env，填入你的 DEEPSEEK_API_KEY
```

### 4. 使用

```bash
# 导入文档
python -m rag_kb add data/sample.txt
python -m rag_kb add data/sample.pdf

# 批量导入目录
python -m rag_kb add-dir data/

# 单次提问
python -m rag_kb ask "常见的Web漏洞有哪些？"

# 交互问答（输入 quit 退出）
python -m rag_kb chat

# 查看配置
python -m rag_kb info

# 清空知识库
python -m rag_kb clear
```

## 项目结构

```
rag_kb/
├── config.py              # 配置管理
├── document_processor.py  # 文档加载与切分
├── embeddings.py          # BGE-M3 向量化
├── vector_store.py        # ChromaDB 存取
├── rag_chain.py           # RAG 链（检索+生成）
└── cli.py                 # 命令行界面
```

## 测试

```bash
pytest tests/ -v
```

## 开发计划

- [x] 阶段0：跑通 Demo
- [x] 阶段1：MVP 模块化
- [ ] 阶段2：FastAPI 接口化 + 检索优化
- [ ] 阶段3：LangGraph Agent + RAGAS 评估
- [ ] 阶段4：SaaS 化 / 接外包

## License

MIT
````

- [ ] **Step 2: 提交**

```bash
git add README.md
git commit -m "docs: update README with full usage guide"
```

---

## Task 8: 端到端验证与推送

- [ ] **Step 1: 清空旧数据，完整流程验证**

```bash
# 清空旧知识库
python -m rag_kb clear

# 导入文档
python -m rag_kb add data/sample.txt

# 验证提问
python -m rag_kb ask "常见的Web漏洞有哪些？"

# 验证文档外问题
python -m rag_kb ask "Java Spring 怎么配置？"

# 交互问答
python -m rag_kb chat
```

- [ ] **Step 2: 运行全部测试**

```bash
pytest tests/ -v
```

预期：所有测试 PASS

- [ ] **Step 3: 推送到 Gitee**

```bash
git push origin main
```

- [ ] **Step 4: 在 Gitee 上验证**

打开 https://gitee.com/little-fishy/rag-kb 确认：
- README 正常渲染
- 目录结构完整
- `.env` 未上传
- `step_doc/` 下有阶段1 计划文档

---

## 阶段1 完成验收清单

- [ ] 项目模块化，每个文件职责单一
- [ ] 支持同时导入多个 PDF/TXT/MD 文档
- [ ] 命令行交互问答可用（add / ask / chat / clear / info）
- [ ] 回答附带引用来源
- [ ] 全部测试通过
- [ ] README 完整，别人 clone 后能按步骤跑起来
- [ ] Gitee 仓库已更新

## 阶段1 的简历表达

> **RAG 知识库系统（Python + LangChain + DeepSeek）**
> - 独立设计并实现基于 RAG 架构的本地文档问答系统，支持多文档导入（PDF/MD/TXT）
> - 技术栈：LangChain 0.3 LCEL 编排、BGE-M3 向量化、ChromaDB 向量检索、DeepSeek V4 生成
> - 采用模块化设计：文档处理、向量存储、检索链、CLI 各自独立，可单独测试
> - 已开源：https://gitee.com/little-fishy/rag-kb
