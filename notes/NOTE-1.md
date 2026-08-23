# RAG-KB 阶段1 学习笔记

> 项目仓库：https://gitee.com/little-fishy/rag-kb.git
>
> 本笔记基于阶段1 各模块代码逐行解读，记录每个 Task 的前置知识点、代码实现细节和踩坑经验。
>
> 阶段1 目标：把阶段0 的单文件 `demo.py` 重构成模块化项目，支持多文档导入、命令行交互问答。

---

## 目录

- [Task 1: 项目结构 + config.py](#task-1-项目结构--configpy)
- [Task 2: document_processor.py](#task-2-document_processorpy)
- [Task 3: embeddings.py](#task-3-embeddingspy)
- [Task 4: vector_store.py](#task-4-vector_storepy)
- [Task 5: rag_chain.py](#task-5-rag_chainpy)
- [Task 6: cli.py](#task-6-clipy)
- [踩坑汇总](#踩坑汇总)

---

## 模块化架构总览

阶段1 把单文件拆成 6 个模块，每个模块只做一件事：

```
rag_kb/
├── __init__.py            # 包初始化，设置 HuggingFace 镜像
├── config.py              # 配置：所有参数集中管理
├── document_processor.py  # 文档处理：加载文件 + 切分文本
├── embeddings.py          # 向量化：文本 → 向量（延迟加载单例）
├── vector_store.py        # 存储：向量存取 + 检索
├── rag_chain.py           # 编排：把上面四个串成完整问答链
└── cli.py                 # 入口：用户交互（Typer 命令行）
```

**依赖方向：**

```
cli.py → rag_chain.py → ┬── document_processor.py
                        ├── vector_store.py → embeddings.py
                        └── (config.py 被所有人依赖)
```

config 被所有人依赖，但不依赖别人——"底层不依赖上层"原则。

---

## Task 1: 项目结构 + config.py

### 1.1 核心知识点

#### pathlib 路径操作

`pathlib` 是 Python 3.4+ 引入的面向对象路径库，比传统的 `os.path` 字符串拼接更安全、更直观。

```python
from pathlib import Path

Path.cwd()                    # 当前工作目录（运行时目录，不一定是脚本所在目录）
Path(__file__)                # 当前脚本的路径
Path(__file__).resolve()      # 规范化路径，补齐为绝对路径，去掉 ./
Path(__file__).resolve().parent      # 父目录（脚本所在目录）
Path(__file__).resolve().parent.parent  # 爷目录（项目根目录）
Path("/data").suffix           # '.gz'，取最后一个后缀
Path("test.PDF").suffix.lower() # '.pdf'，统一转小写方便比较
Path("./data").exists()        # 判断文件/目录是否存在
BASE_DIR / "data"             # 用 / 运算符拼接路径，跨平台兼容
```

**为什么用 pathlib 而不用字符串拼接？**

```python
# ❌ 字符串拼接，Windows 会出问题
data_dir = BASE_DIR + "/data"   # Windows 路径分隔符是 \ 不是 /

# ✅ pathlib 自动处理跨平台分隔符
data_dir = BASE_DIR / "data"    # Windows 上自动变成 BASE_DIR \ "data"
```

#### .env 加载环境变量

```python
from dotenv import load_dotenv
import os

load_dotenv()  # 读取当前目录下的 .env 文件，把键值对加载为环境变量
api_key = os.getenv("DEEPSEEK_API_KEY")  # 从环境变量读取
```

- `load_dotenv()` 默认查找**当前工作目录**下的 `.env` 文件（不是脚本所在目录）。
- `.env` 文件内容格式：`KEY=VALUE`，每行一个，不加引号。
- `os.getenv("KEY")` 读取环境变量，不存在返回 `None`；`os.environ["KEY"]` 不存在会抛 `KeyError`。
- **安全**：`.env` 含敏感信息（API Key），必须在 `.gitignore` 中排除，不上传 Git。

#### Python 包和 `__init__.py`

一个文件夹加上 `__init__.py` 就变成 Python 的"包"（package），可以被 `import`：

```
rag_kb/           # 文件夹
├── __init__.py   # 这个文件存在 → rag_kb 就是一个包
├── config.py
└── ...
```

```python
from rag_kb import config       # 找到 rag_kb 包，导入 config 模块
from rag_kb.config import Config  # 直接导入类
```

`__init__.py` 可以是空文件，也可以写初始化代码。本项目中 `__init__.py` 做了环境变量设置：

```python
# rag_kb/__init__.py
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
os.environ["HF_HUB_OFFLINE"] = "1"  # 离线模式，跳过在线检查，直接用缓存
```

**关键**：`__init__.py` 在包第一次被 import 时自动执行，所以放在这里的代码会在所有模块导入前运行。这就是为什么 HF 镜像设置放在这里——保证在任何模块 import HuggingFace 之前，镜像已生效。

### 1.2 config.py 代码解读

```python
import os
from dotenv import load_dotenv
from pathlib import Path

load_dotenv()  # 加载 .env
```

#### Config 类设计

```python
class Config:
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    if not DEEPSEEK_API_KEY:
        import warnings
        warnings.warn("DEEPSEEK_API_KEY 未设置，LLM 相关功能不可用")
    DEEPSEEK_MODEL = "deepseek-v4-flash"
    EMBEDDING_MODEL = "BAAI/bge-m3"
    EMBEDDING_DEVICE = "cpu"
    CHUNK_SIZE = 500       # 切分大小
    CHUNK_OVERLAP = 100    # 重叠大小
    SEARCH_K = 3           # 检索返回数量
    BASE_DIR = Path(__file__).resolve().parent.parent  # 项目根目录
    DATA_DIR = BASE_DIR / "data"
    CHROMA_DIR = BASE_DIR / "chroma_db"
    COLLECTION_NAME = "rag_kb_collection"
```

**知识点——类变量 vs 实例变量：**

```python
class Config:
    CHUNK_SIZE = 500  # 类变量：定义在类体中，所有实例共享同一个值

# 访问方式：不需要创建实例，直接用类名访问
print(Config.CHUNK_SIZE)  # 500

# 与实例变量对比
class DocumentProcessor:
    def __init__(self):
        self.chunk_size = 500  # 实例变量：每个实例可以有不同值

dp = DocumentProcessor()
print(dp.chunk_size)  # 500，需要先创建实例
```

Config 全部用类变量，因为配置是全局共享的，不需要每个实例存一份。

**知识点——if 在类体中直接执行：**

```python
class Config:
    DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY")
    if not DEEPSEEK_API_KEY:          # 类体中的 if，在类定义时就会执行
        import warnings
        warnings.warn("...")          # 相当于"编译时检查"
```

类体中的代码在类被定义时（import 时）就执行，不是在创建实例时。这里用来检查 API Key 是否配置。

#### 系统提示词的字符串拼接

```python
SYSTEM_PROMPT = \
    "你是一个文档问答助手。请根据以下检索到的文档内容回答用户问题。" \
    "如果文档中没有相关信息，请说'文档中没有相关信息'。" \
    "如果文档中有相关信息，请标注信息来源。" \
    "\n\n文档内容:\n{context}"
```

- `\` 是行继续符，把多行字符串拼接成一行。
- `{context}` 是占位符，后续被 `ChatPromptTemplate` 替换为检索到的文档内容。
- 相邻字符串字面量会自动拼接：`"hello" "world"` 等价于 `"helloworld"`。

#### @classmethod 和 get_all 方法

```python
@classmethod
def get_all(cls) -> dict:
    """返回所有配置的字典（过滤掉内置属性和方法）"""
    return {
        k: v
        for k, v in vars(cls).items()
        if not k.startswith(("_", "get")) and not callable(v)
    }
```

**知识点——@classmethod：**

- 普通方法第一个参数是 `self`（实例），需要创建实例才能调用。
- `@classmethod` 第一个参数是 `cls`（类本身），不需要创建实例，直接用 `Config.get_all()` 调用。
- 相当于 Java 中的静态方法，但能访问类变量。

**知识点——字典推导式 + vars()：**

```python
vars(Config)  # 返回类的所有属性和方法的字典 {'CHUNK_SIZE': 500, 'get_all': <method>, ...}

# 字典推导式：遍历 vars(cls) 的键值对，过滤掉：
# - 以 _ 开头的（如 __module__、__doc__ 等内置属性）
# - 以 get 开头的（过滤掉自己的 get_all、get_keys、get_values 方法）
# - callable 的（过滤掉方法，只留数据）
```

#### 模块级 `__getattr__` 兼容旧写法

```python
def __getattr__(name):
    try:
        return getattr(Config, name)
    except AttributeError:
        raise AttributeError(
            f"module {__name__!r} has no attribute {name!r}"
        ) from None
```

- 这是 Python 3.7+ 的**模块级 `__getattr__`**，当 `config.CHUNK_SIZE` 找不到模块属性时，会自动转发到 `Config.CHUNK_SIZE`。
- 这样既可以用 `config.Config.CHUNK_SIZE`（新写法），也可以用 `config.CHUNK_SIZE`（旧写法），兼容性更好。
- `f"..."` 是 f-string，`{__name__!r}` 中的 `!r` 表示用 `repr()` 格式化（会带引号）。

### 1.3 `if __name__ == "__main__"` 的使用

```python
if __name__ == "__main__":
    print("BASE_DIR:" + str(Config.BASE_DIR))
    print("DATA_DIR:" + str(Config.DATA_DIR))
```

- `__name__` 是 Python 内置变量：直接运行脚本时值为 `"__main__"`，被 import 时值为模块名（如 `"config"`）。
- 这行代码的作用：**只有直接运行此文件时才执行测试代码，被 import 时不执行**。
- 好处：可以在文件底部写测试代码，不影响其他模块 import。

---

## Task 2: document_processor.py

### 2.1 核心知识点

#### Python 导包

```python
from langchain_core.documents import Document       # 从包的模块导入类
from langchain_community.document_loaders import TextLoader, PyPDFLoader  # 一次导入多个
from langchain_text_splitters import RecursiveCharacterTextSplitter
from rag_kb import config  # 从自己的包导入模块
```

- `from 包 import 模块`：导入整个模块，用 `config.CHUNK_SIZE` 访问。
- `from 包.模块 import 类`：直接导入类，用 `Document()` 直接调用。
- 导入顺序：标准库 → 第三方库 → 本地包。

#### Document 数据结构

LangChain 中所有文档的统一格式：

```python
from langchain_core.documents import Document

doc = Document(
    page_content="这是文档的文本内容",
    metadata={"source": "sample.pdf", "page": 1}
)

print(doc.page_content)  # 文本内容
print(doc.metadata)      # 元数据（来源、页码等）
```

- `page_content`：字符串，文档的实际文本。
- `metadata`：字典，存储来源信息（文件名、页码等），检索时可用来标注引用来源。

#### 异常抛出（raise）

```python
# 文件不存在 → 抛 FileNotFoundError
if not Path(file_path).exists():
    raise FileNotFoundError(f"{file_path}不存在")

# 不支持的格式 → 抛 ValueError
fmt = "/".join(self.supported_formats)
raise ValueError(f"不支持的格式: {suffix}，仅支持 {fmt}")
```

- `raise` 主动抛出异常，程序立即中断当前执行流，由调用者捕获处理。
- 常见异常类型：`FileNotFoundError`（文件不存在）、`ValueError`（值不合法）、`TypeError`（类型不对）。
- `f"..."` 是 f-string 格式化，把变量值嵌入字符串。

#### list 的 join 和 enumerate

```python
# join：把列表元素用指定分隔符拼接成字符串
self.supported_formats = [".pdf", ".txt", ".md"]
fmt = "/".join(self.supported_formats)  # ".pdf/.txt/.md"

# enumerate：遍历时同时获取索引和值
for i, doc in enumerate(docs, start=1):
    print(f"[片段{i}]{doc.page_content}")
```

- `"/".join(list)` 以 `"/"` 为分隔符，把列表拼接成字符串。
- `enumerate(iterable, start=1)` 返回 `(index, value)` 元组，`start` 指定索引起始值。
- 列表切片：`list[1:3]` 取索引 1 到 2（左闭右开），`list[:5]` 取前 5 个，`list[::2]` 每隔 2 个取一个。

#### 类的变量和构造方法

```python
class DocumentProcessor:
    # 类变量：定义在类体中，所有实例共享
    supported_formats = [".pdf", ".txt", ".md"]

    def __init__(self, chunk_size, chunk_overlap):
        # 实例变量：在 __init__ 中用 self.xxx 创建，每个实例独立
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
```

**类变量 vs 实例变量对比：**

| 特性 | 类变量 | 实例变量 |
|------|--------|----------|
| 定义位置 | 类体中，`__init__` 外 | `__init__` 中，用 `self.` |
| 访问方式 | `类名.变量` 或 `实例.变量` | 只能 `实例.变量` |
| 是否共享 | 所有实例共享同一个值 | 每个实例独立一份 |
| 适用场景 | 常量、配置 | 每个实例不同的状态 |

```python
dp1 = DocumentProcessor(500, 100)
dp2 = DocumentProcessor(300, 50)

# 类变量共享
print(dp1.supported_formats)  # ['.pdf', '.txt', '.md']
print(dp2.supported_formats)  # 同一个列表对象

# 实例变量独立
print(dp1.chunk_size)  # 500
print(dp2.chunk_size)  # 300
```

### 2.2 document_processor.py 代码解读

#### 类变量定义

```python
class DocumentProcessor:
    supported_formats = [".pdf", ".txt", ".md"]
```

支持的格式列表是类变量——所有实例共享，不因实例不同而变化。

#### 构造方法

```python
def __init__(self, chunk_size: int = config.CHUNK_SIZE, chunk_overlap: int = config.CHUNK_OVERLAP):
    self.chunk_size = chunk_size
    self.chunk_overlap = chunk_overlap
```

- 参数默认值从 `config` 读取，也可以在创建实例时覆盖。
- **Python 默认参数陷阱**：不要用可变对象（如 list）作为默认参数，因为默认参数只创建一次，所有实例共享。这里用 `int` 没问题。

#### load 方法——根据后缀自动选择 loader

```python
def load(self, file_path: str) -> list[Document]:
    if not Path(file_path).exists():
        raise FileNotFoundError(f"{file_path}不存在")

    suffix = Path(file_path).suffix.lower()
    if suffix in self.supported_formats:
        if suffix == ".pdf":
            return PyPDFLoader(file_path).load()
        elif suffix in (".md", ".txt"):
            return TextLoader(file_path, encoding="utf-8").load()
    else:
        fmt = "/".join(self.supported_formats)
        raise ValueError(f"不支持的格式: {suffix}，仅支持 {fmt}")
```

**执行流程：**

1. 检查文件是否存在，不存在抛 `FileNotFoundError`。
2. 获取文件后缀并转小写（`.PDF` 和 `.pdf` 统一处理）。
3. 根据后缀选择对应的 loader：
   - `.pdf` → `PyPDFLoader`（用 pypdf 库解析 PDF）
   - `.md` / `.txt` → `TextLoader`（指定 `encoding="utf-8"`，Windows 默认 GBK 会报错）
4. 不支持的格式抛 `ValueError`，提示支持哪些格式。

**知识点——`in` 判断成员关系：**

```python
suffix in self.supported_formats  # 判断 suffix 是否在列表中
suffix in (".md", ".txt")        # 也可以用元组，in 对 list 和 tuple 都适用
```

#### split 方法——切分文档

```python
def split(self, documents: list[Document], chunk_size: int = None, chunk_overlap: int = None) -> list[Document]:
    if chunk_size is None:
        chunk_size = self.chunk_size
    if chunk_overlap is None:
        chunk_overlap = self.chunk_overlap

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )
    return splitter.split_documents(documents)
```

- 用 `None` 作为默认值而不是直接用 `config.CHUNK_SIZE`，是为了允许调用者传入自定义值。
- `if chunk_size is None:` 判断是否传入了值——`None` 用 `is` 判断而不是 `==`（`None` 是单例对象）。
- `RecursiveCharacterTextSplitter` 的切分原理在阶段0 笔记中已详细记录。

#### load_and_split 便捷方法

```python
def load_and_split(self, file_path: str) -> list[Document]:
    return self.split(self.load(file_path))
```

一行代码串联 load 和 split，体现了方法的组合复用。

### 2.3 类型注解

```python
def load(self, file_path: str) -> list[Document]:
```

- `file_path: str`：参数类型注解，提示 `file_path` 应该是字符串。
- `-> list[Document]`：返回值类型注解，提示返回 `Document` 列表。
- **注意**：类型注解只是提示，Python 运行时不强制检查。但 IDE（如 PyCharm）会据此提供代码补进和类型检查。

---

## Task 3: embeddings.py

### 3.1 核心知识点

#### 延迟加载（Lazy Initialization）+ 单例模式

BGE-M3 模型首次加载要下载 2GB 文件，耗时几分钟。如果在模块导入时就初始化，每次 `import` 都会触发加载——非常浪费。

**解决方案：延迟加载 + 单例模式。**

```python
_embedding = None  # 全局变量，初始为空

def get_embeddings() -> HuggingFaceEmbeddings:
    global _embedding
    if _embedding is None:           # 第一次调用：还没创建
        _embedding = HuggingFaceEmbeddings(...)  # 创建实例
    return _embedding                # 后续调用：直接返回已有实例
```

**执行过程：**

1. 模块导入时：`_embedding = None`，不加载模型。
2. 第一次调用 `get_embeddings()`：`_embedding is None` 为真 → 创建实例 → 存入 `_embedding`。
3. 第二次调用：`_embedding is None` 为假 → 直接返回已有实例，不重新加载。

**对比——如果不用延迟加载：**

```python
# ❌ 模块导入时就加载（每次 import 都触发）
embedding = HuggingFaceEmbeddings(model_name="BAAI/bge-m3", ...)

# 其他文件只要 from rag_kb import embeddings 就会触发模型加载
# 即使用户只是想看个配置，也得等几分钟加载模型
```

#### global 关键字

```python
_embedding = None  # 模块级全局变量

def get_embeddings():
    global _embedding  # 声明：我要修改全局变量 _embedding
    _embedding = HuggingFaceEmbeddings(...)  # 修改全局变量
    return _embedding
```

- `global` 告诉 Python：函数内的 `_embedding` 是全局变量，不是局部变量。
- 不加 `global` 的话，`_embedding = ...` 会创建一个局部变量，全局变量不变。
- **只读取全局变量不需要 global**，只有修改（赋值）才需要。

### 3.2 embeddings.py 代码解读

```python
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"

from langchain_huggingface import HuggingFaceEmbeddings
from rag_kb import config

_embedding = None  # 全局变量，单例模式

def get_embeddings() -> HuggingFaceEmbeddings:
    """获取 BGE-M3 embedding 实例（延迟加载，单例）"""
    global _embedding
    if _embedding is None:
        _embedding = HuggingFaceEmbeddings(
            model_name=config.EMBEDDING_MODEL,      # "BAAI/bge-m3"
            model_kwargs={"device": config.EMBEDDING_DEVICE},  # {"device": "cpu"}
        )
    return _embedding
```

- `os.environ["HF_ENDPOINT"]` 必须在 `from langchain_huggingface import ...` 之前设置。
- `model_kwargs={"device": "cpu"}` 指定在 CPU 上运行（没有 GPU 时的选择）。
- BGE-M3 输出 1024 维向量，首次运行下载约 2GB 模型文件。

### 3.3 测试代码解读

```python
if __name__ == "__main__":
    em = get_embeddings()
    print(em)
    ei = get_embeddings()
    print(ei)
    print(ei is em)  # True，验证单例——两次调用返回同一个对象

    vec1 = ei.embed_query("网络安全")
    print(vec1)
    print(f"向量维度: {len(vec1)}")  # 1024
```

- `ei is em`：`is` 判断两个变量是否指向同一个对象（身份比较），`==` 判断值是否相等。
- `embed_query("...")`：把字符串转成 1024 维的浮点数列表。

---

## Task 4: vector_store.py

### 4.1 核心知识点

#### @property 装饰器——把方法当属性用

```python
class VectorStore:
    def __init__(self):
        self._chroma = None  # 内部状态，初始为空

    @property
    def chroma(self) -> Chroma:
        """初始化Chroma"""
        if self._chroma is None:
            self._client = chromadb.PersistentClient(path=self.persist_directory)
            self._chroma = Chroma(
                collection_name=self.collection_name,
                embedding_function=embeddings.get_embeddings(),
                persist_directory=self.persist_directory,
                client=self._client
            )
        return self._chroma
```

- `@property` 把方法变成"只读属性"，用 `vs.chroma` 访问，不需要加括号 `vs.chroma()`。
- 第一次访问时创建 Chroma 实例（延迟初始化），后续访问直接返回已有实例。
- **命名约定**：内部变量用下划线前缀 `_chroma`，表示"私有"（约定，不是强制）。

**对比 `@property` 和单例函数：**

| 方式 | 写法 | 调用 | 适用场景 |
|------|------|------|----------|
| `@property` | `@property def chroma(self)` | `vs.chroma` | 类内部延迟初始化 |
| 单例函数 | `def get_embeddings()` | `get_embeddings()` | 模块级单例 |

两者本质相同——都是"第一次访问时创建，后续返回已有实例"。

#### ChromaDB 的核心 API

```python
import chromadb
from langchain_chroma import Chroma

# 创建持久化客户端（数据自动写入磁盘）
client = chromadb.PersistentClient(path="./chroma_db")

# 创建 LangChain 封装的 Chroma 实例
chroma = Chroma(
    collection_name="rag_kb_collection",     # 集合名
    embedding_function=embeddings.get_embeddings(),  # 向量化函数
    persist_directory="./chroma_db",          # 持久化目录
    client=client                             # 传入自定义客户端
)

# 存入文档
chroma.add_documents(documents)   # 存入 Document 列表
chroma.add_texts(["文本1", "文本2"])  # 存入纯文本

# 检索
results = chroma.similarity_search("查询词", k=3)  # 返回 list[Document]
results = chroma.similarity_search_with_score("查询词", k=3)  # 返回 list[(Document, float)]

# 转为 retriever
retriever = chroma.as_retriever(search_kwargs={"k": 3})
docs = retriever.invoke("查询词")  # 返回 list[Document]
```

**similarity_search vs as_retriever：**

| 方法 | 返回类型 | 用途 |
|------|----------|------|
| `similarity_search(query, k)` | `list[Document]` | 直接检索，适合单独使用 |
| `similarity_search_with_score(query, k)` | `list[(Document, float)]` | 带分数检索，分数越小越相关（距离） |
| `as_retriever().invoke(query)` | `list[Document]` | 转为 Runnable，可接入 LCEL 管道 |

`as_retriever()` 的作用：返回的对象实现了 LangChain 的 `Runnable` 接口，可以无缝接入 `retriever | format_docs` 这样的 LCEL 管道。

#### clear 方法的资源清理

```python
def clear(self) -> None:
    if self._client is not None:
        self._client.close()  # 显式关闭 SQLite 连接 → 释放文件句柄
        self._client = None
    self._chroma = None

    if Path(self.persist_directory).exists():
        shutil.rmtree(self.persist_directory)  # 删除整个目录
```

**为什么要先 close 再删除？**

ChromaDB 底层用 SQLite 存储数据，SQLite 会锁定数据库文件。如果不先关闭连接就删除目录，Windows 上会报 `PermissionError`（文件被占用）。所以必须：

1. `self._client.close()` → 关闭 SQLite 连接，释放文件句柄。
2. `self._chroma = None` → 重置内部状态，下次访问会重新初始化。
3. `shutil.rmtree()` → 删除整个持久化目录。

### 4.2 vector_store.py 代码解读

#### 构造方法

```python
def __init__(self, persist_directory: str = str(config.CHROMA_DIR),
             collection_name: str = config.COLLECTION_NAME):
    self.persist_directory = persist_directory
    self.collection_name = collection_name
    self._chroma = None   # 延迟初始化
    self._client = None  # 延迟初始化
```

- `str(config.CHROMA_DIR)` 把 Path 对象转成字符串（ChromaDB 需要字符串路径）。
- 构造时不创建 Chroma 实例，等到真正需要时（访问 `chroma` 属性）才创建。

#### 封装的方法

```python
def add_documents(self, documents: list[Document]) -> None:
    self.chroma.add_documents(documents)  # 直接调用 Chroma 的方法

def search(self, query: str, k: int = config.SEARCH_K) -> list[Document]:
    return self.chroma.similarity_search(query, k)

def as_retriever(self, k: int = config.SEARCH_K):
    return self.chroma.as_retriever(search_kwargs={"k": k})
```

- VectorStore 类是对 Chroma 的**简单封装**，把常用操作集中管理。
- `self.chroma` 会触发 `@property` 延迟初始化。
- `search_kwargs={"k": k}` 是 as_retriever 的参数，控制检索返回数量。

### 4.3 测试代码解读

```python
if __name__ == "__main__":
    vs = VectorStore()
    doc = document_processor.DocumentProcessor()
    pdf_docs = doc.load_and_split(config.DATA_DIR / "sample.pdf")
    vs.add_documents(pdf_docs)

    # 带分数检索
    ans_list = vs.search_with_scores("什么是零信任架构", 2)
    for t in ans_list:
        print(t[0].page_content)  # Document 的文本
        print(t[0].metadata)      # Document 的元数据
        print(t[1])                # 相似度分数（距离，越小越相关）

    # as_retriever 测试
    ret = vs.as_retriever()
    ret_docs = ret.invoke("什么是社会工程学攻击")
    print("as_retriever num: " + str(len(ret_docs)))

    vs.clear()  # 清空
```

---

## Task 5: rag_chain.py

### 5.1 核心知识点

#### LCEL 管道（LangChain Expression Language）

LCEL 是 LangChain 的表达式语言，用管道符 `|` 把多个组件串联成一条链：

```python
rag_chain = (
    {"context": retriever | format_docs, "input": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)
```

**执行流程（以 `rag_chain.invoke("什么是渗透测试")` 为例）：**

1. **输入**：用户问题 `"什么是渗透测试"` 进入管道。
2. **并行分发**：构造字典：
   - `"context"` 键：输入传给 `retriever`（检索相关文档）→ 再经过 `format_docs`（拼接成字符串）。
   - `"input"` 键：`RunnablePassthrough()` 把原始输入原样透传。
3. **填充模板**：字典传给 `prompt`，`{context}` 被替换为文档内容，`{input}` 被替换为用户问题。
4. **调用模型**：填充后的 messages 传给 `llm`（DeepSeek），生成回答。
5. **解析输出**：`StrOutputParser()` 把 `AIMessage` 对象解析为纯字符串。

**核心组件：**

| 组件 | 作用 |
|------|------|
| `RunnablePassthrough()` | 透传输入，不做任何处理。在并行分发时保留原始输入 |
| `ChatPromptTemplate` | 聊天提示词模板，用占位符填充变量 |
| `StrOutputParser()` | 把 `AIMessage` 解析成纯字符串 |

**管道符 `|` 的含义**：`A | B` 表示 A 的输出作为 B 的输入，类似 Unix 管道。

#### @property 延迟初始化（LLM 和 chain）

```python
@property
def llm(self):
    if self._llm is None:
        self._llm = ChatDeepSeek(model=config.DEEPSEEK_MODEL, api_key=config.DEEPSEEK_API_KEY)
    return self._llm

@property
def rag_chain(self):
    if self._rag_chain is None:
        self._rag_chain = (...)  # 构建 LCEL 链
    return self._rag_chain
```

- LLM 实例和 chain 实例都是延迟创建——只有在第一次调用 `ask()` 时才创建。
- 好处：如果用户只导入文档不提问，就不会创建 LLM 实例（省资源）。
- 与 Task 3 的 `get_embeddings()` 和 Task 4 的 `chroma` 属性是同一种模式。

#### format_docs 函数与 enumerate

```python
def format_docs(docs: list[Document]) -> str:
    return "\n\n".join(
        f"[片段{i}]{doc.page_content}"
        for i, doc in enumerate(docs, start=1)
    )
```

- `enumerate(docs, start=1)`：遍历时同时产出索引（从 1 开始）和文档。
- `f"[片段{i}]{doc.page_content}"`：给每个块编号，方便模型引用来源。
- `"\n\n".join(...)`：用两个换行符把所有块拼接成一个字符串。
- 这是一个**生成器表达式**（`for` 在 `join` 括号内，不加 `[]`），比列表推导式更省内存。

### 5.2 rag_chain.py 代码解读

#### 构造方法

```python
class RAGChain:
    def __init__(self, chunk_size=config.CHUNK_SIZE, chunk_overlap=config.CHUNK_OVERLAP,
                 persist_directory=str(config.CHROMA_DIR), collection_name=config.COLLECTION_NAME,
                 k=config.SEARCH_K):
        self.document_processor = document_processor.DocumentProcessor(chunk_size, chunk_overlap)
        self.vector_store = vector_store.VectorStore(persist_directory, collection_name)
        self.k = k
        self._retriever = None
        self._llm = None
        self._rag_chain = None
        self.prompt = ChatPromptTemplate.from_messages([
            ("system", config.SYSTEM_PROMPT),
            ("human", "{input}")
        ])
```

- RAGChain 是**编排层**：它持有 `DocumentProcessor` 和 `VectorStore` 的实例，但自己不处理具体逻辑。
- `ChatPromptTemplate.from_messages()` 构建聊天提示词模板：
  - `("system", config.SYSTEM_PROMPT)`：系统消息，包含角色设定和 `{context}` 占位符。
  - `("human", "{input}")`：用户消息，包含 `{input}` 占位符。
- `("system", "...")` 是**元组**语法，元组用 `()` 定义，可以省略括号。

#### ask 和 ask_with_sources

```python
def ask(self, question: str) -> str:
    """提问，返回回答字符串"""
    return self.rag_chain.invoke(question)

def ask_with_sources(self, question: str) -> dict:
    """提问，返回 {"answer": str, "sources": list[Document]}"""
    answer_str = self.ask(question)
    sources_list = "文档中没有相关信息。"
    if not answer_str.__eq__("文档中没有相关信息。"):
        sources_list = self.retriever.invoke(question)
    return {
        "answer": answer_str,
        "sources": sources_list
    }
```

- `ask()`：调用 RAG 链，返回纯字符串回答。
- `ask_with_sources()`：先获取回答，如果回答不是"没有相关信息"，再检索一遍拿到来源文档。
- `answer_str.__eq__("...")`：等价于 `answer_str == "..."`，`__eq__` 是相等比较的魔法方法。
- 返回字典 `{"answer": ..., "sources": ...}`，调用者可以分别获取回答和来源。

#### 批量导入

```python
def add_documents(self, file_paths: list[str]) -> int:
    all_num: int = 0
    for fp in file_paths:
        docs = self.document_processor.load_and_split(fp)
        self.vector_store.add_documents(docs)
        all_num += len(docs)
    return all_num
```

- 遍历文件路径列表，逐个加载、切分、存入向量库，累计总块数。
- `all_num: int = 0`：带类型注解的变量声明。

### 5.3 测试代码解读

```python
if __name__ == "__main__":
    chain = RAGChain()
    file_pdf = config.DATA_DIR / "sample.pdf"
    print(format_docs(chain.document_processor.load_and_split(str(file_pdf))))

    file_pa = config.DATA_DIR / "sample.txt"
    chain.add_document(str(file_pa))
    print(chain.ask("常见的Web漏洞有哪些？"))
    print(chain.ask("python是什么？"))
    print(chain.ask_with_sources("常见的Web漏洞有哪些？"))
```

- 先打印 PDF 切分结果（验证文档加载和切分是否正确）。
- 导入 TXT 文件到向量库。
- 测试不同问题：文档内问题、文档外问题、带来源的提问。

---

## Task 6: cli.py

### 6.1 核心知识点

#### Typer 基础

Typer 是基于 Click 的 CLI 框架，用类型注解定义命令参数：

```python
import typer

app = typer.Typer()  # 创建应用实例

@app.command()  # 把函数注册为命令
def add(file_path: str):  # str 类型 → 必填位置参数
    """导入单个文件"""
    ...

@app.command()
def ask(question: str = typer.Argument(None)):  # typer.Argument(None) → 可选位置参数
    """单次提问"""
    ...

if __name__ == "__main__":
    app()  # 启动 CLI
```

**Typer 参数类型：**

| 写法 | 类型 | 调用方式 | 说明 |
|------|------|----------|------|
| `name: str` | 必填参数 | `python cli.py hello World` | 直接接在命令后面 |
| `name: str = typer.Argument(None)` | 可选参数 | `python cli.py ask` 或 `python cli.py ask "问题"` | 不传则用默认值 |
| `name: str = typer.Option("default", "--name")` | 选项 | `python cli.py cmd --name World` | 用 `--` 前缀 |

**重要区别——`question: str = None` vs `question: str = typer.Argument(None)`：**

```python
# ❌ question: str = None
# Typer 会把 None 默认值的参数当作 Option（选项），用户需要 --question 传入
# 直接 python cli.py ask "问题" 会报 "Got unexpected extra argument(s)"

# ✅ question: str = typer.Argument(None)
# 明确声明这是位置参数（Argument），用户可以直接 python cli.py ask "问题"
```

Typer 通过类型注解自动推断参数类型。`typer.Argument()` 声明位置参数，`typer.Option()` 声明选项参数。

#### Rich Console——带颜色的终端输出

```python
from rich.console import Console
from rich.table import Table

console = Console()

console.print("成功消息", style="green")
console.print("错误消息", style="red")
console.print("提示信息", style="yellow")
console.print("普通信息", style="cyan")
```

Rich 支持的常用颜色样式：`green`、`red`、`yellow`、`cyan`、`magenta`、`white`、`bold`（加粗）。

#### Rich Table——表格输出

```python
table = Table(title="当前配置", show_header=True, header_style="bold magenta")
table.add_column("配置项", style="bold yellow")
table.add_column("值", style="white")

for key, value in config.Config.get_all().items():
    table.add_row(str(key), str(value))

console.print(table)
```

- `Table(title=...)`：创建表格，设置标题。
- `add_column(列名, style=...)`：添加列。
- `add_row(值1, 值2)`：添加行。
- Rich 自动处理表格对齐和边框。

#### typer.prompt 和 typer.confirm

```python
# 交互式输入
text = typer.prompt("请输入你的问题")  # 等待用户输入，返回字符串

# 确认对话框
if typer.confirm("确定清除向量数据库吗？"):  # 返回 True/False
    chain.vector_store.clear()
```

### 6.2 cli.py 代码解读

#### 全局实例

```python
console = Console()
chain = rag_chain.RAGChain()  # 全局 RAG 链实例
app = typer.Typer()
```

- `chain` 在模块导入时就创建——这意味着 import 时就会创建 `RAGChain` 实例（但不创建 LLM 和 chain，因为是延迟初始化）。

#### add 命令——导入单个文件

```python
@app.command()
def add(file_path: str):
    """导入单个文件，支持 .pdf / .txt / .md"""
    path = config.Config.DATA_DIR / f"{file_path}"  # 先尝试在 data 目录下找
    if path.exists():
        file_path = path  # 找到就用 data 目录下的文件
    chunk_nums = chain.add_document(file_path)  # 不在就用用户传入的路径
    console.print("导入文件成功！"
                  f"被切分为{chunk_nums}个区块", style="green")
```

- 先尝试在 `data/` 目录下找文件，找不到再用用户传入的绝对/相对路径。
- 这种设计让用户可以 `python -m rag_kb add sample.txt`（只需文件名）或 `python -m rag_kb add /full/path/to/file.pdf`。

#### add_dir 命令——批量导入

```python
@app.command()
def add_dir(directory: str):
    """批量导入目录，自动扫描 .pdf/.txt/.md 文件"""
    files = os.listdir(directory)  # 获取目录下所有文件名，返回 list[str]
    fmt = chain.document_processor.supported_formats
    all_chunk_nums = 0
    for file in files:
        suf = Path(file).suffix.lower()
        if suf in fmt:  # 只处理支持的格式
            all_chunk_nums += chain.add_document(str(Path(directory) / f"{file}"))
    console.print(f"批量导入文件成功！总共被切分为{all_chunk_nums}个区块", style="green")
```

- `os.listdir(directory)` 返回目录下所有文件名列表（不含路径，不含子目录递归）。
- 遍历文件，按后缀过滤，逐个导入。

#### ask 命令——单次提问

```python
@app.command()
def ask(question: str = typer.Argument(None)):
    """单次提问"""
    if question is None:
        question = typer.prompt("请输入你的问题")
    response = chain.ask_with_sources(question)
    answer = response.get("answer", "文档中没有相关信息")
    sources = response.get("sources", "文档中没有相关信息")
    console.print(f"答案：{answer}", style="green")
    console.print(f"来源：{sources}", style="green")
```

- `typer.Argument(None)` 声明可选位置参数：可以直接 `ask "问题"`，也可以不传参数交互式输入。
- `dict.get(key, default)`：安全获取字典值，key 不存在返回 default。

#### chat 命令——交互式连续问答

```python
@app.command()
def chat():
    """交互式连续问答"""
    console.print("进入交互式问答...help 展示命令，输入 quit 退出", style="yellow")
    while True:
        text = str(typer.prompt("> ")).strip()
        if not text:
            continue
        if should_quit(text):
            console.print("再见！", style="cyan")
            break
        ask(text)
```

- `while True` 无限循环，直到用户输入退出命令。
- `str(typer.prompt("> ")).strip()`：获取输入并去除首尾空白。
- `should_quit(text)` 判断是否输入了退出命令。

```python
def should_quit(text: str) -> bool:
    return text.strip().lower() in ("quit", "exit", "q", "bye")
```

- `strip()` 去除首尾空白，`lower()` 转小写，统一处理大小写和空格。
- `in ("quit", "exit", "q", "bye")` 判断是否在退出命令列表中。

#### info 命令——显示配置

```python
@app.command()
def info():
    """以表格的方式显示当前配置"""
    table = Table(title="当前配置", show_header=True, header_style="bold magenta")
    table.add_column("配置项", style="bold yellow")
    table.add_column("值", style="white")

    for key, value in config.Config.get_all().items():
        table.add_row(str(key), str(value))

    console.print(table)
```

- 调用 `config.Config.get_all()` 获取所有配置项。
- 用 Rich Table 以表格形式展示，直观清晰。

#### clear 命令——清空向量库

```python
@app.command()
def clear():
    """清除向量库数据"""
    if typer.confirm("确定清除向量数据库吗？"):  # 二次确认
        chain.vector_store.clear()
    else:
        console.print("取消操作！", style="cyan")
```

- `typer.confirm()` 弹出确认对话框，输入 y/yes 返回 True，n/no 返回 False。
- 清空操作不可逆，需要二次确认防止误操作。

---

## 踩坑汇总

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `Got unexpected extra argument(s)` | `question: str = None` 被 Typer 当作 Option | 改用 `question: str = typer.Argument(None)` 明确声明位置参数 |
| Windows 下 `clear()` 报 `PermissionError` | SQLite 连接未关闭，文件被占用 | 先 `self._client.close()` 关闭连接，再 `shutil.rmtree()` |
| HuggingFace 模型下载慢 | 国内直连官方源超时 | `os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"` 设置镜像 |
| HF 缓存检查慢 | 每次启动都检查在线更新 | `os.environ["HF_HUB_OFFLINE"] = "1"` 离线模式 |
| TextLoader 中文报 `UnicodeDecodeError` | Windows 默认 GBK 编码 | `TextLoader(path, encoding="utf-8")` 指定 UTF-8 |
| ChromaDB 数据重复 | 重复运行脚本追加数据 | 每次运行前 `clear()` 或检查是否已有数据 |
| `clear()` 后产生空目录残留 | ChromaDB 内部创建临时目录 | 关闭 client 后再删除，必要时递归清理 |
| 模块导入时加载模型 | 在模块顶层直接创建实例 | 用延迟加载（`@property` 或单例函数），首次调用时才创建 |

---

## 阶段1 总结

### 核心设计模式

1. **集中配置**（config.py）：所有参数集中管理，改一处全项目生效。
2. **单一职责**（每个模块只做一件事）：加载、切分、向量化、存储、检索、编排、交互各自独立。
3. **延迟加载**（Lazy Initialization）：模型、LLM、chain 实例都是在第一次真正需要时才创建，避免不必要的资源消耗。
4. **单例模式**（Singleton）：embedding 模型全局只创建一次，后续复用同一实例。
5. **封装**（Encapsulation）：VectorStore 封装 ChromaDB 操作，RAGChain 封装编排逻辑，CLI 封装用户交互。

### Python 知识点回顾

| 知识点 | 所在 Task | 关键代码 |
|--------|-----------|----------|
| `pathlib.Path` 路径操作 | Task 1 | `Path(__file__).resolve().parent.parent` |
| `.env` 环境变量加载 | Task 1 | `load_dotenv()` + `os.getenv()` |
| 类变量 vs 实例变量 | Task 1/2 | `supported_formats` vs `self.chunk_size` |
| `@classmethod` | Task 1 | `Config.get_all()` |
| 模块级 `__getattr__` | Task 1 | 兼容 `config.CHUNK_SIZE` 旧写法 |
| `if __name__ == "__main__"` | Task 1-5 | 防止测试代码被 import 时执行 |
| 异常抛出 `raise` | Task 2 | `raise FileNotFoundError(...)` |
| `enumerate` 遍历 | Task 5 | `enumerate(docs, start=1)` |
| `"/".join(list)` | Task 2 | `"/".join(self.supported_formats)` |
| `global` 关键字 | Task 3 | `global _embedding` |
| 延迟加载 + 单例 | Task 3 | `if _embedding is None: ...` |
| `@property` 装饰器 | Task 4/5 | `@property def chroma(self)` |
| LCEL 管道 `\|` | Task 5 | `retriever \| format_docs \| prompt \| llm` |
| `typer.Argument` vs `typer.Option` | Task 6 | 位置参数 vs 选项参数 |
| Rich Console / Table | Task 6 | `console.print(..., style="green")` |
