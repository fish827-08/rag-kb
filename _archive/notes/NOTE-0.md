# RAG-KB 阶段0 学习笔记

> 项目仓库：https://gitee.com/little-fishy/rag-kb.git
>
> 本笔记基于 `demo.py` 逐行解读，并回答 Stage0 计划 Task 8 中提出的疑问。

---

## 一、demo.py 逐行代码解读

### 1. 文件头部与环境设置

```python
# -*- coding: utf-8 -*-
```
声明文件编码为 UTF-8，确保中文注释和字符串正常显示。

```python
"""
@Time ： 2026/7/9 下午 11:22
@Auth ： Yu
@File ：demo.py
@IDE ：PyCharm
@Intro :
"""
```
PyCharm 自动生成的文件头注释，记录创建时间、作者、文件名等信息，对程序运行无影响。

---

### 2. HuggingFace 镜像设置（Task 3 Step 4）

```python
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
```
- `import os`：导入 Python 标准库，用于操作环境变量和文件系统。
- `os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"`：设置环境变量 `HF_ENDPOINT`，将 HuggingFace 的模型下载地址从官方源（`huggingface.co`）切换到国内镜像（`hf-mirror.com`）。
- **原因**：国内直连 HuggingFace 官方源很慢甚至超时，BGE-M3 模型约 2GB，走镜像源可大幅加速。
- **位置要求**：必须在所有 HuggingFace 相关的 `import` 之前执行，否则环境变量不会生效。

---

### 3. DeepSeek LLM 初始化（Task 2）

```python
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

load_dotenv()
```
- `from dotenv import load_dotenv`：导入 `python-dotenv` 库的 `load_dotenv` 函数。
- `from langchain_deepseek import ChatDeepSeek`：导入 LangChain 的 DeepSeek 集成包中的 `ChatDeepSeek` 类，这是 LangChain 封装的 DeepSeek 聊天模型接口。
- `load_dotenv()`：读取当前目录下的 `.env` 文件，将其中的键值对加载为环境变量。这样后续 `os.getenv("DEEPSEEK_API_KEY")` 就能取到 `.env` 中填写的 API Key。

```python
llm = ChatDeepSeek(
    model="deepseek-v4-flash",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
)
```
- 创建一个 `ChatDeepSeek` 实例，赋值给变量 `llm`。
- `model="deepseek-v4-flash"`：指定使用 DeepSeek V4 Flash 模型（旧名 `deepseek-chat`，将于 2026/07/24 弃用，新名 `deepseek-v4-flash` 是其非思考模式）。
- `api_key=os.getenv("DEEPSEEK_API_KEY")`：从环境变量中读取 API Key 传入。也可以不传 `api_key` 参数，`ChatDeepSeek` 会自动读取 `DEEPSEEK_API_KEY` 环境变量。

```python
response = llm.invoke("你好，请用一句话介绍你自己。")
print(response.content)
```
- `llm.invoke("...")`：向 DeepSeek 发送一条消息，返回一个 `AIMessage` 对象。
- `response.content`：取出返回对象中的文本内容。
- 这是 Task 2 的连通性验证，确认 API Key 和网络都正常。

---

### 4. BGE-M3 Embedding 测试（Task 3）

```python
from langchain_huggingface import HuggingFaceEmbeddings

embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-m3",
    model_kwargs={"device": "cpu"},
)
```
- `HuggingFaceEmbeddings`：LangChain 封装的 HuggingFace 嵌入模型接口。
- `model_name="BAAI/bge-m3"`：指定使用北京智源人工智能研究院（BAAI）的 BGE-M3 模型。首次运行会自动从 HuggingFace（或镜像）下载，约 2GB。
- `model_kwargs={"device": "cpu"}`：指定在 CPU 上运行（如果没有 GPU 就用 CPU）。
- `embeddings` 对象后续被用于：把文本转成向量（`embed_query`）和给 ChromaDB 做向量化存储。

```python
vec1 = embeddings.embed_query("网络安全")
vec2 = embeddings.embed_query("信息安全")

print(f"向量维度: {len(vec1)}")
print(f"两个相似词的向量前5位: {vec1[:5]}")
print(f"对比词的向量前5位: {vec2[:5]}")
```
- `embed_query("...")`：把一个字符串转成 1024 维的浮点数向量。
- BGE-M3 输出维度为 1024，打印前 5 位用于验证模型正常工作。
- "网络安全"和"信息安全"是语义相近的词，向量值会有一定相似性，但不会完全相同。

---

### 5. ChromaDB 存取测试（Task 4）

```python
from langchain_community.vectorstores import Chroma

texts = [
    "网络安全是保护系统免受攻击的实践",
    "Python是一种编程语言，适合快速开发",
    "SQL注入是常见的Web漏洞",
]
```
- `from langchain_community.vectorstores import Chroma`：导入 LangChain 社区包中的 Chroma 向量数据库集成。
- `texts`：定义了 3 条测试文本，接下来会把它们向量化后存入 ChromaDB。

```python
vectorstore = Chroma.from_texts(
    texts=texts,
    embedding=embeddings,
    persist_directory="./chroma_db",
)
```
- `Chroma.from_texts(...)`：一步完成"向量化 + 存储"。
  - `texts`：要存储的文本列表。
  - `embedding=embeddings`：用前面创建的 BGE-M3 模型把文本转成向量。
  - `persist_directory="./chroma_db"`：指定本地持久化目录，数据会保存在 `./chroma_db` 文件夹中。
- **注意**：每次运行脚本都会往这个目录追加数据，不会自动清空。如果重复运行，数据会累积导致检索结果重复。

```python
results = vectorstore.similarity_search("什么是网络安全", k=2)
print("\n检索结果:")
for i, doc in enumerate(results):
    print(f"  {i+1}. {doc.page_content}")
```
- `similarity_search("...", k=2)`：用语义相似度搜索，返回与查询最接近的 2 条文本。
  - 先把查询字符串"什么是网络安全"转成向量。
  - 然后在 ChromaDB 中找到与之余弦相似度最高的 2 条记录。
- `doc.page_content`：取出返回的 Document 对象中的文本内容。
- 预期结果第一条是"网络安全是保护系统免受攻击的实践"，因为语义最接近。

---

### 6. 完整 RAG 流程（Task 5 & Task 6）

#### 6.1 加载文档

```python
from langchain_community.document_loaders import TextLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

# loader = TextLoader("./data/sample.txt", encoding="utf-8")
from langchain_community.document_loaders import PyPDFLoader
loader = PyPDFLoader("./data/sample.pdf")
docs = loader.load()
```
- `TextLoader` 被注释掉了（Task 5 用的是文本文件）。
- `PyPDFLoader`：Task 6 替换为 PDF 加载器，用 `pypdf` 库解析 PDF 文件。
- `loader.load()`：读取文件内容，返回一个 Document 对象列表（每个 Document 包含 `page_content` 文本和 `metadata` 元数据）。
- **注意**：`TextLoader` 加了 `encoding="utf-8"`，因为 Windows 默认用 GBK 编码读文件，读 UTF-8 的中文文件会报 `UnicodeDecodeError`。

#### 6.2 切分文档

```python
splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,
    chunk_overlap=100,
)
chunks = splitter.split_documents(docs)
print(f"\n文档切分: {len(chunks)} 个块")
```
- `RecursiveCharacterTextSplitter`：递归字符文本切分器，按指定大小把长文档切成小块。
- `chunk_size=500`：每个块最大 500 个字符。
- `chunk_overlap=100`：相邻块之间有 100 个字符的重叠，保证上下文不断裂。
- `split_documents(docs)`：对 Document 列表进行切分，返回切分后的 Document 列表。
- 切分的目的：大文档直接喂给模型会超 token 限制，且检索精度下降；切成小块后可以只取最相关的几块作为上下文。

#### 6.3 向量化并存入 ChromaDB

```python
vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory="./chroma_db",
)
```
- `Chroma.from_documents(...)`：与前面的 `from_texts` 类似，但输入是 Document 对象列表。
- 把切分后的文档块逐一转向量并存入 ChromaDB。

#### 6.4 构建检索器

```python
retriever = vectorstore.as_retriever(search_kwargs={"k": 2})
```
- `as_retriever()`：把 ChromaDB 向量库包装成一个"检索器"对象，方便接入 LangChain 的链。
- `search_kwargs={"k": 2}`：每次检索返回最相关的 2 个文档块。

#### 6.5 构建 RAG 链（LCEL 方式）

```python
from langchain_core.runnables import RunnablePassthrough
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
```
- `RunnablePassthrough`：LCEL 中的"透传"组件，把输入原样传递到下一步。
- `StrOutputParser`：把模型返回的 `AIMessage` 对象解析为纯字符串。
- `ChatPromptTemplate`：聊天提示词模板，用于构建发送给模型的 messages。

```python
system_prompt = (
    "你是一个文档问答助手。请根据以下检索到的文档内容回答用户问题。"
    "如果文档中没有相关信息，请说'文档中没有相关信息'。"
    "\n\n文档内容:\n{context}"
)

prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    ("human", "{input}"),
])
```
- `system_prompt`：系统提示词，告诉模型角色定位和规则。`{context}` 是占位符，后续会被检索到的文档内容填充。
- `ChatPromptTemplate.from_messages(...)`：构建一个聊天提示词模板，包含 system 和 human 两条消息。
  - system 消息：角色设定 + 文档内容 `{context}`。
  - human 消息：用户的问题 `{input}`。

```python
def format_docs(docs):
    return "\n\n".join(doc.page_content for doc in docs)
```
- 自定义函数：把检索器返回的多个 Document 对象的文本拼接成一个字符串，用 `\n\n` 分隔。
- 这样 `{context}` 占位符就能被替换成拼接后的文档内容。

```python
rag_chain = (
    {"context": retriever | format_docs, "input": RunnablePassthrough()}
    | prompt
    | llm
    | StrOutputParser()
)
```
- 这是 LCEL（LangChain Expression Language）的核心语法，用管道符 `|` 把多个步骤串联成一条链。
- 执行流程（以 `rag_chain.invoke("什么是渗透测试")` 为例）：
  1. **输入**：用户的问题字符串 `"什么是渗透测试"` 进入管道。
  2. **并行分发**：构造一个字典 `{"context": ..., "input": ...}`：
     - `"context"` 键：把输入问题传给 `retriever`（检索相关文档），再经过 `format_docs`（拼接成字符串）。
     - `"input"` 键：`RunnablePassthrough()` 把原始输入原样透传。
  3. **填充模板**：字典传给 `prompt`，`{context}` 被替换为文档内容，`{input}` 被替换为用户问题。
  4. **调用模型**：填充后的 messages 传给 `llm`（DeepSeek），模型生成回答。
  5. **解析输出**：`StrOutputParser()` 把 `AIMessage` 对象解析为纯字符串。
- **等价理解**：`A | B` 表示 A 的输出作为 B 的输入，类似 Unix 管道。

#### 6.6 提问与输出

```python
question = "零信任架构的核心原则是什么？"
response = rag_chain.invoke(question)

print(f"\n问题: {question}")
print(f"回答: {response}")
```
- `rag_chain.invoke(question)`：把问题字符串传入 RAG 链，经过检索 → 拼接 → 填充模板 → 调模型 → 解析，最终返回纯字符串回答。
- 后面的 `question2`、`question3` 同理，分别测试不同问题（包括文档外的问题，验证模型不会瞎编）。

---

## 二、Task 8 技术点答疑

### Q1：RecursiveCharacterTextSplitter 的切分逻辑是什么？

`RecursiveCharacterTextSplitter` 是一种递归切分策略，工作流程如下：

1. **尝试用第一级分隔符切分**：默认分隔符列表为 `["\n\n", "\n", " ", ""]`，即先按段落（双换行）切。
2. **如果某块仍超过 `chunk_size`**：对该块用下一级分隔符（单换行）继续切。
3. **继续递归**：如果还是超长，再用空格切，最后用字符切。
4. **合并小块**：切完后，把相邻的小块尽量合并，使每块接近 `chunk_size` 但不超过。
5. **加重叠**：相邻块之间保留 `chunk_overlap` 个字符的重叠，避免在切分边界丢失上下文。

**优点**：优先在自然语义边界（段落 → 行 → 词）处切分，尽量保持语义完整性，而不是机械地按固定字符数硬切。

**示例**：一篇 1000 字的文档，`chunk_size=500, chunk_overlap=100`，可能被切成：
- 块1：第 1-500 字
- 块2：第 401-900 字（与块1重叠 100 字）
- 块3：第 801-1000 字（与块2重叠 100 字）

---

### Q2：chunk_size 和 chunk_overlap 怎么影响效果？

| 参数 | 作用 | 太大的影响 | 太小的影响 |
|------|------|-----------|-----------|
| `chunk_size` | 每个文本块的最大字符数 | 块太大 → 检索精度下降（一个块包含太多不相关信息），也浪费 token | 块太小 → 语义不完整，模型拿到的上下文碎片化 |
| `chunk_overlap` | 相邻块之间的重叠字符数 | 重叠太大 → 存储冗余，数据量膨胀 | 重叠太小 → 切分边界丢失上下文，可能漏掉跨边界的答案 |

**经验值**：
- 纯文本：`chunk_size=200~500, chunk_overlap=50~100`
- PDF：`chunk_size=500~1000, chunk_overlap=100~200`（PDF 段落通常更长）
- 一般建议 `chunk_overlap` 设为 `chunk_size` 的 10%~20%

**调参思路**：如果发现检索结果不准，先尝试增大 `chunk_size`；如果发现回答缺少上下文，增大 `chunk_overlap`。

---

### Q3：similarity_search 的 k=2 是什么意思？

```python
results = vectorstore.similarity_search("什么是网络安全", k=2)
```

- `k=2` 表示只返回与查询**语义相似度最高的 2 个文档块**。
- 工作原理：
  1. 把查询字符串转成向量（用 BGE-M3）。
  2. 计算该向量与 ChromaDB 中所有文档块向量的余弦相似度。
  3. 按相似度从高到低排序，取前 2 个返回。
- **k 的选择**：
  - k 太小（如 k=1）：可能漏掉相关信息。
  - k 太大（如 k=10）：会引入太多无关内容，干扰模型回答，且浪费 token。
  - 一般 k=2~5 比较合适。

在 RAG 链中，`retriever = vectorstore.as_retriever(search_kwargs={"k": 2})` 的 `k=2` 也是同样含义——每次检索只取最相关的 2 个块作为上下文传给模型。

---

### Q4：create_retrieval_chain 和 create_stuff_documents_chain 分别做什么？

> **注意**：当前 `demo.py` 使用的是 LCEL 管道方式，没有直接用这两个函数。但理解它们的含义有助于对比新旧两种写法。

#### create_stuff_documents_chain

- **作用**：创建一个"文档填充链"，把检索到的多个 Document 对象自动拼接成一个字符串，填入提示词模板的 `{context}` 占位符，然后调用 LLM。
- **"stuff"策略**：把所有文档"塞"进一个 prompt 中（适合文档总量不大、不超过模型 token 限制的场景）。
- 等价于 LCEL 中的：
  ```python
  def format_docs(docs):
      return "\n\n".join(doc.page_content for doc in docs)
  # format_docs + prompt + llm
  ```

#### create_retrieval_chain

- **作用**：创建一个完整的检索增强生成链，把"检索器"和"文档填充链"组合起来。
- 工作流程：用户问题 → 检索器获取相关文档 → 文档填充链把文档拼入 prompt → 调用 LLM → 返回结果。
- 等价于 LCEL 中的：
  ```python
  rag_chain = (
      {"context": retriever | format_docs, "input": RunnablePassthrough()}
      | prompt | llm | StrOutputParser()
  )
  ```

#### 新旧对比

| 旧写法（已弃用） | 新写法（LCEL） |
|-------------------|----------------|
| `create_stuff_documents_chain(llm, prompt)` | `prompt | llm` |
| `create_retrieval_chain(retriever, qa_chain)` | `{"context": retriever \| format_docs, "input": RunnablePassthrough()} \| prompt \| llm` |
| `response['answer']` | `response`（直接是字符串） |

**为什么改用 LCEL**：LangChain 1.x 版本移除了 `langchain.chains` 模块，官方推荐用 LCEL 管道语法，更灵活、更直观、支持流式输出和异步调用。

---

### Q5：persist_directory 持久化的原理？

```python
vectorstore = Chroma.from_texts(
    texts=texts,
    embedding=embeddings,
    persist_directory="./chroma_db",
)
```

- `persist_directory` 指定 ChromaDB 数据的本地存储路径。
- **原理**：
  - ChromaDB 底层使用 SQLite 存储元数据（文档文本、ID 等），使用 DuckDB/Parquet 存储向量数据。
  - 当指定 `persist_directory="./chroma_db"` 后，所有数据会写入这个目录下的文件中。
  - 下次程序启动时，可以用 `Chroma(persist_directory="./chroma_db", embedding_function=embeddings)` 重新加载已有数据，不需要重新向量化。
- **注意事项**：
  - ChromaDB **不会自动去重**：每次调用 `from_texts` 或 `from_documents` 都会追加新数据。如果重复运行脚本，相同的数据会被存储多份，导致检索结果重复。
  - 解决方法：每次运行前先清空 `./chroma_db` 目录，或者在创建前检查是否已有数据。
  - `.gitignore` 中已配置 `chroma_db/`，不会上传到 Git。

---

## 三、遇到的坑与解决方案汇总

| 问题 | 原因 | 解决方案 |
|------|------|----------|
| `mkdir -p` 命令语法不正确 | Windows cmd 不支持 `-p` 参数 | 直接 `mkdir rag-kb\data`，Windows 默认递归创建 |
| `source` 不是内部命令 | Windows 不支持 `source` 命令 | 用 `venv\Scripts\activate.bat` 激活虚拟环境 |
| pip install 报 403 | URL 被反引号包裹 + 清华源限制 | 去掉反引号，换阿里云镜像源 |
| `git remote add` 报 not a git repository | git init 不在当前目录 | 在 `rag-kb` 目录下重新 `git init` |
| Gitee 出现两个分支 | Gitee 默认创建了 master，本地推送的是 main | 在 Gitee 网页上删除 master 分支 |
| HuggingFace 下载慢 | 国内直连官方源慢 | `os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"` |
| symlink 警告 | Windows 不支持符号链接 | 可忽略，或设置 `HF_HUB_DISABLE_SYMLINKS_WARNING=1` |
| `ModuleNotFoundError: langchain.text_splitter` | LangChain 新版拆包 | 改为 `from langchain_text_splitters import ...` |
| `ModuleNotFoundError: langchain.chains` | LangChain 1.x 移除了 chains 模块 | 改用 LCEL 管道语法（`RunnablePassthrough`、`StrOutputParser`） |
| `UnicodeDecodeError: 'gbk' codec` | Windows 默认 GBK 编码 | `TextLoader(path, encoding="utf-8")` |
| `'set' object has no attribute 'replace'` | `invoke({question})` 被解析为集合 | 改为 `invoke(question)` 去掉花括号 |
| 检索结果重复 | ChromaDB 数据累积未清空 | 运行前删除 `./chroma_db` 目录 |

---

## 四、RAG 整体架构理解

### 离线流程（数据准备）

```
PDF 文件
  │
  ▼ PyPDFLoader 加载
Document 对象（完整文本）
  │
  ▼ RecursiveCharacterTextSplitter 切分
多个小块 Document（chunk_size=500）
  │
  ▼ BGE-M3 向量化
每个块 → 1024 维向量
  │
  ▼ 存入 ChromaDB（persist_directory="./chroma_db"）
持久化到本地磁盘
```

### 在线流程（用户问答）

```
用户提问 "零信任架构的核心原则是什么？"
  │
  ▼ BGE-M3 向量化
问题 → 1024 维向量
  │
  ▼ ChromaDB 相似度检索（k=2）
返回最相关的 2 个文档块
  │
  ▼ format_docs 拼接
多个 Document → 一个字符串
  │
  ▼ 填充 ChatPromptTemplate
{context} ← 检索到的文档内容
{input}   ← 用户问题
  │
  ▼ DeepSeek V4 Flash 生成回答
返回基于文档内容的回答
  │
  ▼ StrOutputParser 解析
AIMessage → 纯字符串
  │
  ▼ 打印输出
```

### 核心思想

RAG（Retrieval-Augmented Generation，检索增强生成）的核心思想是：**不让模型靠自己的知识回答，而是先从文档中检索相关内容，再把检索到的内容作为上下文喂给模型，让模型"看着文档回答"**。

这样做的好处：
1. 模型可以回答最新、私有领域的知识（不需要重新训练）。
2. 回答有据可查，减少"幻觉"（模型编造内容）。
3. 如果文档中没有相关信息，模型会说"没有"，而不是瞎编。

---

## 五、核心原理深入理解（补充）

### 1. 文本转向量的原理

#### 什么是向量？

向量就是一组数字，比如 `[0.12, -0.34, 0.05, ...]`。BGE-M3 模型把每段文字映射成一个 1024 维的向量（即 1024 个数字组成的列表）。

#### 怎么把文字变成数字？

BGE-M3 是一个基于 Transformer 架构的神经网络模型，转换过程分三步：

1. **分词（Tokenization）**：把"网络安全"拆成模型认识的 Token（子词单元），比如 `["网络", "安全"]`。
2. **Transformer 编码**：每个 Token 经过多层自注意力机制（Self-Attention），模型会"理解"每个词的上下文含义，输出一个上下文相关的特征表示。
3. **池化（Pooling）**：把所有 Token 的特征聚合成一个固定维度的向量（1024 维），这个向量就代表了整句话的语义。

#### 关键直觉

你可以把向量想象成高维空间中的一个"点"。语义相近的文字在这个空间中的位置也相近：

```
"网络安全"  → [0.12, -0.34, 0.05, ...]  ← 位置 A
"信息安全"  → [0.11, -0.31, 0.07, ...]  ← 位置 B（离 A 很近）
"Python编程" → [-0.20, 0.45, -0.18, ...] ← 位置 C（离 A 很远）
```

模型在训练时见过海量文本，学会了"网络安全"和"信息安全"经常出现在类似语境中，所以把它们映射到相近的位置。这就是为什么向量能表示语义。

---

### 2. 余弦相似度：如何判断两个向量是否相近

#### 为什么要用余弦相似度？

有了向量后，需要一种方法来衡量"两个向量有多相似"。直接算距离（欧氏距离）也行，但余弦相似度更常用，因为它只看方向不看长度。

#### 公式

```
cos(θ) = (A · B) / (|A| × |B|)

A · B   = 向量 A 和 B 的点积（对应位置相乘再求和）
|A|     = 向量 A 的长度（模长）
|B|     = 向量 B 的长度（模长）
```

#### 直觉理解

把两个向量画成从原点出发的箭头，余弦相似度就是这两个箭头之间夹角的余弦值：

- **夹角 = 0°**（方向相同）→ cos(θ) = 1 → 完全相似
- **夹角 = 90°**（方向垂直）→ cos(θ) = 0 → 完全无关
- **夹角 = 180°**（方向相反）→ cos(θ) = -1 → 语义相反

#### 在 RAG 中的作用

当用户提问"什么是渗透测试？"时：

1. BGE-M3 把问题转成查询向量 Q。
2. ChromaDB 把 Q 和所有文档块向量逐一算余弦相似度。
3. 按相似度从高到低排序，取前 k 个（`k=2`）最相关的块。
4. 这 k 个块就是"和问题语义最接近的文档片段"，作为上下文喂给模型。

#### 为什么不用欧氏距离？

余弦相似度只关注向量的方向（语义），不关注长度（文本长短）。一段长文档和一段短文档如果讲的是同一个主题，它们向量的方向相近，余弦相似度高；但如果用欧氏距离，长度差异会干扰结果。所以在语义检索场景下，余弦相似度更合适。

---

### 3. 递归切分策略图解

#### 为什么要切分？

一篇 PDF 可能几千上万字，直接整篇塞给模型有两个问题：
- token 限制（模型一次能接收的文本有上限）
- 检索精度差（整篇文档是一个向量，无法定位到具体段落）

所以要把文档切成小块，每个块独立向量化、独立检索。

#### 递归切分的逻辑

`RecursiveCharacterTextSplitter` 不是机械地每 500 字切一刀，而是**优先在自然语义边界处切**：

```
分隔符优先级（从高到低）:
  第一级: "\n\n"  （段落分隔，最优先）
  第二级: "\n"    （换行）
  第三级: " "     （空格/词边界）
  第四级: ""      （单字符，最后手段）
```

**执行流程**：

1. 先用 `\n\n`（段落）切，如果每个段落都不超过 `chunk_size`，切分完成。
2. 如果某个段落仍超过 `chunk_size`，对该段落用 `\n`（行）继续切。
3. 如果某行还超长，用空格切。
4. 最后如果还超长，才按单字符硬切。
5. 切完后合并相邻小块，使每块尽量接近 `chunk_size`。
6. 相邻块之间保留 `chunk_overlap` 个字符的重叠。

#### 图示

```
原文档
├── 段落A (300字) ✓ 不超限，保留
├── 段落B (600字) ⚠ 超过 chunk_size=500
│   ├── 用 \n 继续切
│   ├── 行B1 (200字) + 行B2 (200字) → 合并为块1 (400字)
│   └── 行B2 (重叠) + 行B3 (200字) → 合并为块2 (400字)
└── 段落C (100字) ✓ 不超限，保留
```

#### 为什么要重叠（chunk_overlap）？

假设文档里有一句话横跨切分边界：

```
...渗透测试是一种通过模拟攻击者行为来评估系统安全性|的方法...
                                              ↑ 切分边界
```

如果不重叠，前一个块有"渗透测试是一种通过模拟攻击者行为来评估系统安全性"，后一个块有"的方法"，两个块都不完整。如果问"渗透测试是什么"，可能只检索到不完整的前半句。

加了 `chunk_overlap=100` 后，边界附近的 100 个字符在两个块中都出现，保证跨边界的内容不会断裂。

---

### 4. 这些问题对当前阶段的重要性

Task 8 要求"通读 demo.py 每一行代码"和"记录不懂的技术点"，所以理解这三个概念是**任务的一部分**。但阶段0 计划明确说：

> ❌ 不纠结不懂的原理（记到 NOTES.md，阶段1 攻克）

所以当前目标是**理解到能用自己的话讲清楚即可**，不需要深入数学推导。以上三个概念构成了 RAG 的核心原理链：

```
切分文档（递归切分）
  → 向量化存储（BGE-M3）
    → 检索相似块（余弦相似度）
      → 喂给模型回答（DeepSeek）
```

理解到这个程度，阶段0 就算达标。更深的原理（如 BGE-M3 的训练过程、注意力机制的数学推导、向量索引算法 HNSW 等）留到阶段1 攻克。
