# RAG-KB 阶段1：MVP 模块化实现计划（自学版）

> **重要：这份文档不提供最终代码。** 每个 Task 会告诉你"要做什么、为什么、需要先学什么"，然后由你自己写代码实现。代码没有标准答案，只要满足需求即可。

**Goal:** 把阶段0的单文件 demo 重构成模块化项目，支持多文档导入、命令行交互问答，别人 clone 后能跑起来。

**核心原则：** 先学前置知识 → 自测验证 → 再写项目代码。不是边抄边学，是先学会再应用。

---

## 版本基线（2026-07-10 查证）

| 包名 | 导入路径 | 说明 |
|---|---|---|
| langchain-core | `langchain_core` | Runnable、Prompt 等基础类 |
| langchain-community | `langchain_community` | PyPDFLoader、TextLoader |
| langchain-text-splitters | `langchain_text_splitters` | RecursiveCharacterTextSplitter（独立包） |
| langchain-chroma | `langchain_chroma` | Chroma 向量库集成（独立包，替代 langchain_community.vectorstores） |
| langchain-huggingface | `langchain_huggingface` | HuggingFaceEmbeddings |
| langchain-deepseek | `langchain_deepseek` | ChatDeepSeek |
| chromadb | `chromadb` | 1.5+，自动持久化 |
| typer | `typer` | CLI 框架 |

---

## 整体架构：为什么要拆成这些模块

阶段0 是一个 `demo.py` 从头写到尾，所有逻辑混在一起。阶段1 要把它拆开。

**拆分原则：每个模块只做一件事，模块之间通过明确的接口通信。**

```
rag_kb/
├── config.py              # 配置：所有参数集中管理
├── document_processor.py  # 文档处理：加载文件 + 切分文本
├── embeddings.py          # 向量化：文本 → 向量
├── vector_store.py        # 存储：向量存取 + 检索
├── rag_chain.py           # 编排：把上面四个串成完整问答链
└── cli.py                 # 入口：用户交互
```

**为什么这样拆：**

- `config.py` 单独出来：避免参数硬编码散落在各文件里，改一个参数不用翻遍所有代码
- `document_processor` 单独出来：文档加载和切分是一组逻辑，和"怎么存""怎么检索"无关
- `embeddings` 单独出来：模型加载很慢（2GB），要延迟到真正需要时才加载，单独封装方便控制
- `vector_store` 单独出来：ChromaDB 的操作（存、搜、清空）是一组逻辑，和"怎么生成回答"无关
- `rag_chain` 是编排者：它知道"先处理文档→再向量化→再存储→检索→生成"这个流程，但它不关心每步具体怎么做
- `cli` 只是入口：它调用 rag_chain，不包含任何业务逻辑

**依赖方向（谁依赖谁）：**

```
cli.py → rag_chain.py → ┬── document_processor.py
                        ├── vector_store.py → embeddings.py
                        └── (config.py 被所有人依赖)
```

config 被所有人依赖，但没有依赖别人。这就是"底层不依赖上层"的原则。

---

## Task 1: 项目结构 + config.py

### 1. 这个模块要干什么

创建一个 `config.py`，把所有配置参数集中在一个地方管理。其他模块从这里读取参数，不在代码里硬编码任何路径、模型名、参数值。

### 2. 为什么需要它

阶段0 里，`chunk_size=500` 写死在 demo.py 中间，`model="deepseek-v4-flash"` 写死在另一行。如果你想改 chunk_size，得翻代码找到那一行改。如果同一个参数在多处使用，你得改多处还可能漏。

集中管理后：改一处，全项目生效。

### 3. 前置知识

你需要先搞懂以下概念：

| 知识点 | 是什么 | 去哪学 |
|---|---|---|
| Python 包和 `__init__.py` | 为什么一个文件夹加 `__init__.py` 就变成"包"，`from rag_kb import config` 是怎么找到文件的 | Python 官方教程「Modules and Packages」章节 |
| `os.environ` 和 `os.getenv` | 环境变量的读取方式，为什么用环境变量而不是直接写死 | Python `os` 模块文档 |
| `python-dotenv` 的 `load_dotenv()` | `.env` 文件怎么被加载成环境变量的 | python-dotenv 的 README |
| `pathlib.Path` | 面向对象的路径操作，为什么比字符串拼接 `"/data/" + "file"` 好 | Python `pathlib` 文档 |

### 4. 自测练习（验证你学会了前置知识）

在做项目代码之前，先写一个独立的小脚本 `test_prerequisite.py`（不放进项目里，只是练习），完成以下任务：

1. 创建一个 `.env` 文件，里面写 `TEST_KEY=hello_world`
2. 写一个 Python 脚本：用 `load_dotenv()` 加载它，用 `os.getenv("TEST_KEY")` 读取并打印
3. 在脚本里用 `pathlib.Path` 获取当前脚本所在目录，再拼出 `data/` 子目录的路径并打印
4. 用 `Path` 检查 `data/` 目录是否存在，不存在就创建

**自测标准：** 脚本能跑通，打印出 `hello_world` 和正确的路径。如果你能不看任何参考写出来，说明前置知识够了。

### 5. 项目需求

创建以下文件结构，并实现 `config.py`：

```
rag-kb/
├── rag_kb/
│   ├── __init__.py        # 空文件，让 rag_kb 成为包
│   └── config.py          # 配置模块
├── requirements.txt        # 更新依赖
└── .gitignore              # 更新
```

**config.py 需要暴露的配置项：**

| 配置项 | 类型 | 默认值 | 说明 |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | str | 从环境变量读取 | API Key |
| `DEEPSEEK_MODEL` | str | `"deepseek-v4-flash"` | 模型名 |
| `EMBEDDING_MODEL` | str | `"BAAI/bge-m3"` | Embedding 模型名 |
| `EMBEDDING_DEVICE` | str | `"cpu"` | 运行设备 |
| `CHUNK_SIZE` | int | 500 | 切分大小 |
| `CHUNK_OVERLAP` | int | 100 | 重叠大小 |
| `SEARCH_K` | int | 3 | 检索返回数量 |
| `BASE_DIR` | Path | 项目根目录 | 基础路径 |
| `DATA_DIR` | Path | BASE_DIR / "data" | 数据目录 |
| `CHROMA_DIR` | Path | BASE_DIR / "chroma_db" | 向量库目录 |
| `COLLECTION_NAME` | str | `"rag_kb_collection"` | ChromaDB collection 名 |
| `SYSTEM_PROMPT` | str | （见下） | 系统提示词 |

**SYSTEM_PROMPT 要求：** 告诉模型它是一个文档问答助手，基于检索到的文档内容回答，文档中没有的要说"没有相关信息"，回答时标注信息来源。

**requirements.txt 需要新增的依赖：**

```
langchain-chroma>=0.2.0
langchain-huggingface>=0.2.0
langchain-text-splitters>=0.3.0
typer>=0.12.0
rich>=13.0.0
pytest>=8.0.0
```

### 6. 验收标准

- [ ] `python -c "from rag_kb import config; print(config.CHUNK_SIZE)"` 能输出 `500`
- [ ] `python -c "from rag_kb import config; print(config.DEEPSEEK_API_KEY)"` 能输出你的 API Key（如果 .env 配好了）
- [ ] `config.py` 里没有任何硬编码的路径（都用 `Path` 相对计算）
- [ ] `.gitignore` 包含 `venv/`、`.env`、`chroma_db/`、`__pycache__/`、`.idea/`
- [ ] git 提交

---

## Task 2: document_processor.py

### 1. 这个模块要干什么

实现一个 `DocumentProcessor` 类，能加载 PDF/TXT/MD 文件，并切分成文本块。

### 2. 为什么需要它

阶段0 里，加载和切分是写死在主流程中的两行代码。但"加载"和"切分"是两个独立的关注点：
- 你可能想换加载方式（比如以后支持 Word）
- 你可能想换切分策略（比如以后用按句子切分）
- 你可能想单独测试"加载是否正确"而不走完整流程

拆成独立类后，每个关注点可以独立修改和测试。

### 3. 前置知识

| 知识点 | 是什么 | 去哪学 |
|---|---|---|
| LangChain `Document` 数据结构 | `page_content` + `metadata` 两个字段，是 LangChain 里所有文档的统一格式 | LangChain 官方文档「Document」页面 |
| LangChain Loader 机制 | `PyPDFLoader`、`TextLoader` 怎么用，返回什么 | LangChain 官方文档「Document Loaders」 |
| `RecursiveCharacterTextSplitter` | 你阶段0 已用过，但需要理解 `chunk_size`、`chunk_overlap` 的关系和切分策略 | 阶段0 NOTES.md 已有记录 |
| Python 面向对象：`__init__`、`self`、实例方法 | 类的基本写法 | Python 官方教程「Classes」章节 |
| `Path.suffix` | 怎么获取文件扩展名 | `pathlib` 文档 |

### 4. 自测练习

写一个 `test_prerequisite.py`：

1. 用 `PyPDFLoader` 加载 `data/sample.pdf`，打印返回的 Document 列表长度和第一个 Document 的 `page_content[:100]`
2. 用 `TextLoader` 加载 `data/sample.txt`，注意 `encoding="utf-8"` 参数
3. 创建一个 `RecursiveCharacterTextSplitter(chunk_size=100, chunk_overlap=20)`，对加载的文档切分，打印切分后的块数量
4. 打印每个块的前 50 个字符，观察重叠部分
5. 用 `Path("test.pdf").suffix` 获取扩展名，验证返回 `.pdf`

**自测标准：** 能加载 PDF 和 TXT，能切分并看到重叠效果，能获取文件扩展名。

### 5. 项目需求

实现 `rag_kb/document_processor.py`，包含一个 `DocumentProcessor` 类：

**接口定义（你要实现这些方法）：**

```python
class DocumentProcessor:
    def __init__(self, chunk_size: int = config.CHUNK_SIZE, chunk_overlap: int = config.CHUNK_OVERLAP):
        """初始化切分器参数"""
        pass

    def load(self, file_path: str) -> list[Document]:
        """加载单个文件，根据扩展名自动选择 loader
        
        支持 .pdf / .txt / .md
        不支持的格式抛出 ValueError
        文件不存在抛出 FileNotFoundError
        """
        pass

    def split(self, documents: list[Document], chunk_size: int = None, chunk_overlap: int = None) -> list[Document]:
        """切分文档，可覆盖默认的 chunk_size 和 chunk_overlap"""
        pass

    def load_and_split(self, file_path: str) -> list[Document]:
        """便捷方法：加载并切分"""
        pass
```

**行为要求：**
- 根据 `.suffix` 自动选择 loader（PDF 用 PyPDFLoader，TXT/MD 用 TextLoader）
- TXT/MD 加载时必须指定 `encoding="utf-8"`（Windows 默认 GBK 会报错）
- `split` 方法支持可选参数覆盖默认的 chunk_size/chunk_overlap
- 不支持的文件格式抛出 `ValueError`，消息要写清楚支持哪些格式

### 6. 验收标准

- [ ] 加载 `data/sample.txt` 返回的 Document 列表不为空
- [ ] 加载 `data/sample.pdf` 返回的 Document 列表不为空
- [ ] 切分后每个块的 `page_content` 长度不超过 `chunk_size + chunk_overlap`（容差范围内）
- [ ] 传入不存在的文件抛出 `FileNotFoundError`
- [ ] 传入 `.docx` 文件抛出 `ValueError`
- [ ] git 提交

---

## Task 3: embeddings.py

### 1. 这个模块要干什么

封装 BGE-M3 embedding 模型，提供一个函数获取 embedding 实例。

### 2. 为什么需要它

BGE-M3 模型首次加载要下载 2GB 模型文件，耗时几分钟。如果在模块导入时就初始化，每次 import 都会触发下载，非常浪费。

**关键设计：延迟加载（Lazy Initialization）。** 不是在模块加载时就创建实例，而是提供一个函数，在真正需要用的时候才创建。而且只创建一次，后续调用返回同一个实例（单例）。

### 3. 前置知识

| 知识点 | 是什么 | 去哪学 |
|---|---|---|
| 延迟初始化 / 懒加载 | 为什么不在导入时就初始化，而是等到第一次调用时才初始化 | 搜索「Python lazy initialization singleton」 |
| `HuggingFaceEmbeddings` API | `model_name`、`model_kwargs` 参数怎么传 | langchain-huggingface 文档 |
| HuggingFace 镜像设置 | `HF_ENDPOINT` 环境变量的作用 | 阶段0 NOTES.md 已有记录 |

### 4. 自测练习

写一个 `test_prerequisite.py`：

1. 设置 `HF_ENDPOINT` 镜像
2. 创建一个 `HuggingFaceEmbeddings` 实例
3. 用 `embed_query("测试文本")` 获取向量
4. 打印向量维度（应该是 1024）
5. 再调一次 `embed_query("另一个文本")`，打印向量维度，验证不会重新加载模型

**思考题（不用写代码）：** 如果你把 `embeddings = HuggingFaceEmbeddings(...)` 写在模块顶层（不在函数里），每次 `from rag_kb import embeddings` 会发生什么？会触发模型加载吗？为什么不好？

### 5. 项目需求

实现 `rag_kb/embeddings.py`，提供一个函数：

```python
def get_embeddings() -> HuggingFaceEmbeddings:
    """获取 BGE-M3 embedding 实例（延迟加载，单例）
    
    第一次调用时加载模型，后续调用返回同一实例。
    """
    pass
```

**行为要求：**
- 设置 HuggingFace 镜像（`HF_ENDPOINT`）
- 从 `config.py` 读取模型名和设备
- 第一次调用时初始化，后续调用返回同一个实例

### 6. 验收标准

- [ ] 第一次调用 `get_embeddings()` 能返回实例，`embed_query("测试")` 返回 1024 维向量
- [ ] 第二次调用 `get_embeddings()` 不会重新加载模型（加载时间明显短于第一次或为 0）
- [ ] git 提交

---

## Task 4: vector_store.py

### 1. 这个模块要干什么

封装 ChromaDB 的存取操作，提供存入文档、检索文档、清空等功能。

### 2. 为什么需要它

ChromaDB 有很多操作：创建实例、存入文档、存入纯文本、相似度检索、带分数检索、转成 retriever、清空。如果这些操作散落在各处，管理混乱。

封装成一个类后：所有向量库操作集中管理，修改存储策略只改这一个文件。

### 3. 前置知识

| 知识点 | 是什么 | 去哪学 |
|---|---|---|
| `langchain_chroma.Chroma` | 新的独立包，替代旧的 `langchain_community.vectorstores.Chroma` | langchain-chroma 文档或 PyPI 页面 |
| `Chroma` 的构造参数 | `collection_name`、`embedding_function`、`persist_directory` | 官方文档 |
| `add_documents()` / `similarity_search()` / `similarity_search_with_score()` | 存入和检索的方法 | 官方文档 |
| `as_retriever()` | 把 ChromaDB 转成 LangChain retriever，供 RAG 链使用 | LangChain retrievers 文档 |
| Python `@property` 装饰器 | 怎么把方法当属性用，实现延迟初始化 | Python 官方教程「property」 |
| chromadb 0.5+ 自动持久化 | 不需要手动调 `persist()`，数据自动写入磁盘 | 阶段0 NOTES.md 已有记录 |

### 4. 自测练习

写一个 `test_prerequisite.py`：

1. 用 `langchain_chroma.Chroma` 创建一个向量库（用临时目录 `./test_chroma`）
2. 用 `from_documents()` 存入 3 条文本
3. 用 `similarity_search("查询词", k=2)` 检索，打印结果
4. 用 `similarity_search_with_score("查询词", k=2)` 检索，打印结果和分数
5. 用 `as_retriever()` 转成 retriever，用 `invoke("查询词")` 调用，打印返回结果
6. 观察 `similarity_search` 和 `as_retriever().invoke()` 返回的类型有什么区别

**思考题：** `similarity_search_with_score` 返回的分数是距离还是相似度？分数越小越相关还是越大越相关？（提示：查 ChromaDB 文档，余弦距离 vs 余弦相似度）

### 5. 项目需求

实现 `rag_kb/vector_store.py`，包含一个 `VectorStore` 类：

**接口定义：**

```python
class VectorStore:
    def __init__(self, persist_directory: str = None, collection_name: str = None):
        """初始化，参数从 config 读取默认值"""
        pass

    def add_documents(self, documents: list[Document]) -> None:
        """存入 Document 列表"""
        pass

    def add_texts(self, texts: list[str]) -> None:
        """存入纯文本列表（内部转成 Document）"""
        pass

    def search(self, query: str, k: int = None) -> list[Document]:
        """检索最相关的 top-k 文档块"""
        pass

    def search_with_scores(self, query: str, k: int = None) -> list[tuple[Document, float]]:
        """检索并返回相似度分数"""
        pass

    def as_retriever(self, k: int = None):
        """转为 LangChain retriever"""
        pass

    def clear(self) -> None:
        """清空向量库（删除持久化目录）"""
        pass
```

**行为要求：**
- embedding 实例用延迟加载（从 `embeddings.py` 的 `get_embeddings()` 获取）
- Chroma 实例也用延迟加载（`@property`，第一次访问时才创建）
- `clear()` 要删除持久化目录并重置内部状态
- `k` 参数有默认值（从 config 读取）

### 6. 验收标准

- [ ] 存入 3 条文本后，`search()` 能返回相关结果
- [ ] `search_with_scores()` 返回 (Document, float) 元组列表
- [ ] 程序重启后，用相同的 `persist_directory` 能加载已有数据
- [ ] `clear()` 后再检索，结果为空
- [ ] git 提交

---

## Task 5: rag_chain.py

### 1. 这个模块要干什么

把 document_processor、vector_store、DeepSeek LLM 串联起来，实现完整的"导入文档→提问→回答"流程。

### 2. 为什么需要它

这是编排层。前面三个模块各自独立，但用户不关心"先加载再切分再向量化再存储再检索"——用户只关心"我传个文件，问个问题，你给我答案"。

这个模块负责把散装的能力编排成一条完整的流水线。

### 3. 前置知识

| 知识点 | 是什么 | 去哪学 |
|---|---|---|
| LCEL 管道 `\|` 运算符 | LangChain Expression Language，把多个 Runnable 串联 | LangChain LCEL 文档 |
| `RunnablePassthrough` | 透传输入，在并行分发时用 | LangChain 文档 |
| `StrOutputParser` | 把 AIMessage 解析成字符串 | LangChain 文档 |
| `ChatPromptTemplate.from_messages()` | 构建聊天提示词模板 | LangChain Prompt 文档 |
| `ChatDeepSeek` | DeepSeek LLM 的 LangChain 封装 | langchain-deepseek 文档 |
| `@property` 延迟初始化 | LLM 实例和 chain 实例都要延迟创建 | Python property 文档 |

**重要提醒：** 你阶段0 已经用过 LCEL 写 RAG 链了，回顾你的 `demo.py` 第 236-241 行。这次是把它封装进类里。

### 4. 自测练习

写一个 `test_prerequisite.py`：

1. 创建一个 `ChatDeepSeek` 实例，用 `invoke("你好")` 测试连通性
2. 创建一个 `ChatPromptTemplate`，包含 system 和 human 两条消息，有 `{context}` 和 `{input}` 两个占位符
3. 用 LCEL 写一个简单链：`prompt | llm | StrOutputParser()`，用 `invoke({"context": "这是上下文", "input": "这是问题"})` 测试
4. 写一个 `format_docs` 函数，接收 Document 列表，返回拼接后的字符串。在拼接时给每个块编号：`[片段1] 内容...`
5. 测试 `format_docs` 输出格式是否正确

**思考题：** 在 LCEL 链 `{"context": retriever | format_docs, "input": RunnablePassthrough()}` 中，`retriever` 和 `format_docs` 是串行还是并行执行的？`RunnablePassthrough()` 拿到的是什么？

### 5. 项目需求

实现 `rag_kb/rag_chain.py`，包含一个 `RAGChain` 类：

**接口定义：**

```python
class RAGChain:
    def __init__(self, persist_directory: str = None, chunk_size: int = None, ...):
        """初始化，创建 document_processor 和 vector_store 实例"""
        pass

    def add_document(self, file_path: str) -> int:
        """加载并存入一个文档，返回切分后的块数量"""
        pass

    def add_documents(self, file_paths: list[str]) -> int:
        """批量加载并存入多个文档，返回总块数"""
        pass

    def ask(self, question: str) -> str:
        """提问，返回回答字符串"""
        pass

    def ask_with_sources(self, question: str) -> dict:
        """提问，返回 {"answer": str, "sources": list[Document]}"""
        pass
```

**行为要求：**
- LLM 实例和 chain 实例用 `@property` 延迟初始化
- `ask_with_sources` 要先检索拿到 sources，再生成回答
- `format_docs` 函数要给每个块编号并标注来源文件名和页码
- 从 `config.py` 读取所有默认参数

### 6. 验收标准

- [ ] 导入 `data/sample.txt` 后，`ask("常见的Web漏洞有哪些？")` 返回包含相关关键词的回答
- [ ] 问文档外的问题，回答包含"没有"或"不"等否定词
- [ ] `ask_with_sources()` 返回的 `sources` 列表不为空
- [ ] LLM 和 chain 都是延迟初始化（不调用 `ask` 时不创建）
- [ ] git 提交

---

## Task 6: cli.py（Typer 命令行界面）

### 1. 这个模块要干什么

用 Typer 构建一个命令行工具，让用户通过命令操作知识库：导入文档、提问、交互问答、清空、查看配置。

### 2. 为什么需要它

之前你只能通过改 `demo.py` 里的代码来提问，不友好。有了 CLI 后：
- `python -m rag_kb add data/sample.pdf` 就能导入文档
- `python -m rag_kb ask "问题"` 就能提问
- 别人 clone 你的项目后也能用

### 3. 前置知识

| 知识点 | 是什么 | 去哪学 |
|---|---|---|
| Typer 基础 | `@app.command()`、`typer.Argument()`、`typer.Option()`、`typer.confirm()` | Typer 官方教程（typer.tiangolo.com） |
| Rich Console | `Console.print()`、带颜色的输出 `[red]文字[/red]` | Rich 官方文档 |
| Rich Table | 创建表格输出 | Rich 文档 |
| `__main__.py` | 为什么 `python -m rag_kb` 能运行 | Python 文档「__main__」 |
| `typer.prompt()` | 交互式输入 | Typer 文档 |

### 4. 自测练习

写一个 `test_prerequisite.py`：

1. 用 Typer 创建一个 app，定义一个 `hello` 命令，接收一个 `name` 参数，打印 `Hello, {name}!`
2. 运行 `python test_prerequisite.py hello World` 验证输出
3. 加一个 `--count` 选项，控制打印几次
4. 用 Rich Console 打印带颜色的输出：绿色打印成功消息，红色打印错误消息
5. 用 Rich Table 创建一个表格，有 2 列 3 行数据，打印出来

**自测标准：** Typer 命令能跑，Rich 输出有颜色和表格。

### 5. 项目需求

实现 `rag_kb/cli.py` 和 `rag_kb/__main__.py`：

**需要实现的命令：**

| 命令 | 功能 | 参数 |
|---|---|---|
| `add` | 导入单个文档 | `file_path`（必填） |
| `add-dir` | 批量导入目录 | `directory`（必填），自动扫描 .pdf/.txt/.md 文件 |
| `ask` | 单次提问 | `question`（必填），显示回答+引用来源 |
| `chat` | 交互式连续问答 | 无参数，输入 quit 退出 |
| `clear` | 清空知识库 | 需要确认 |
| `info` | 显示当前配置 | 无参数，用表格展示 |

**交互要求：**
- 用 Rich 的颜色区分：问题用青色、回答用绿色、来源用灰色
- `chat` 模式下用 `typer.prompt` 接收输入
- `clear` 要用 `typer.confirm` 二次确认
- `add-dir` 要用 Rich Table 展示每个文件的导入结果
- `info` 要用 Rich Table 展示配置信息

**`__main__.py` 内容：**
```python
from rag_kb.cli import app

if __name__ == "__main__":
    app()
```

### 6. 验收标准

- [ ] `python -m rag_kb --help` 显示所有命令
- [ ] `python -m rag_kb info` 用表格显示配置
- [ ] `python -m rag_kb add data/sample.txt` 导入成功，显示块数量
- [ ] `python -m rag_kb ask "常见Web漏洞有哪些"` 返回回答和来源
- [ ] `python -m rag_kb chat` 能连续问答，输入 quit 退出
- [ ] `python -m rag_kb clear` 需要确认才清空
- [ ] git 提交

---

## Task 7: 测试 + README

### 1. 这个模块要干什么

为前面写的模块写单元测试，并写完整的 README。

### 2. 为什么需要它

- **测试：** 确保你的代码是对的，以后改代码时不小心改坏了能立刻发现。这也是简历上能写的——"项目包含单元测试"。
- **README：** 别人 clone 你的项目，第一眼看 README 就知道怎么跑。没有 README 的项目没人愿意用。

### 3. 前置知识

| 知识点 | 是什么 | 去哪学 |
|---|---|---|
| pytest 基础 | `def test_xxx():`、`assert`、`@pytest.fixture` | pytest 官方教程 |
| pytest 常用断言 | `assert x == y`、`with pytest.raises(ValueError):` | pytest 文档 |
| `@pytest.fixture` | 测试前置数据的准备方式 | pytest 文档 |
| `teardown_module` | 测试后的清理 | pytest 文档 |
| Markdown 语法 | 标题、代码块、表格、列表 | 任意 Markdown 教程 |

### 4. 自测练习

写一个 `test_prerequisite.py`：

1. 写一个 `def add(a, b): return a + b` 函数
2. 用 pytest 写 3 个测试：`test_add_positive`、`test_add_negative`、`test_add_zero`
3. 用 `@pytest.fixture` 创建一个返回 `[1, 2, 3]` 列表的 fixture
4. 写一个测试用这个 fixture，验证列表长度为 3
5. 写一个测试 `with pytest.raises(TypeError): add("a", 1)`，验证类型错误
6. 运行 `pytest test_prerequisite.py -v` 全部通过

**自测标准：** 所有测试 PASS。

### 5. 项目需求

**测试文件结构：**

```
tests/
├── __init__.py
├── test_document_processor.py   # 测试加载和切分
├── test_vector_store.py         # 测试存取和检索
└── test_rag_chain.py            # 测试端到端问答
```

**每个测试文件至少覆盖：**

| 文件 | 测试点 |
|---|---|
| `test_document_processor.py` | 加载 TXT、加载 PDF、切分后块数、不存在的文件抛异常、不支持的格式抛异常 |
| `test_vector_store.py` | 存入和检索、带分数检索、清空后检索为空 |
| `test_rag_chain.py` | 提问返回回答、文档外问题返回否定、`ask_with_sources` 返回来源 |

**注意：** `test_vector_store.py` 和 `test_rag_chain.py` 需要加载 BGE-M3 模型和调用 DeepSeek API，测试前确保 `.env` 配好。测试用的持久化目录用 `./chroma_test_db`，测试后清理（`teardown_module`）。

**README.md 需要包含：**
- 项目简介（一句话说清是什么）
- 功能列表
- 技术栈表格
- 环境要求
- 安装步骤（clone → venv → pip install → 配 .env）
- 使用说明（每个 CLI 命令的示例）
- 项目结构说明
- 测试方法
- 开发计划（阶段0 ✅ → 阶段1 ✅ → 后续）

### 6. 验收标准

- [ ] `pytest tests/ -v` 全部通过
- [ ] README 有完整的安装和使用说明
- [ ] README 有至少一个使用示例
- [ ] git 提交并推送到 Gitee

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

---

## 学习方法提醒

1. **先做自测练习，再做项目代码。** 自测练习是验证你学会了前置知识。如果自测练习做不出来，说明前置知识还没掌握，先学懂再继续。
2. **用 AI 辅助但不让 AI 代写。** 问"LangChain 的 Chroma 怎么用"是可以的，问"帮我写 vector_store.py"是不行的。
3. **每个模块写完后，在 NOTES.md 里记录你学到了什么。** 用自己的话写 3-5 句。
4. **遇到报错先自己排查。** 看错误信息、查文档、用 AI 辅助分析。这是真正的学习过程。
5. **不要追求一次写对。** 写完能跑就行，后面再优化。
