# RAG-KB 阶段0：跑通 Demo 实现计划（Windows）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在本地跑通一个基于 LangChain + DeepSeek + BGE-M3 + ChromaDB 的 RAG demo，能传入 PDF 文件并基于内容问答。

**Architecture:** 单文件脚本。离线流程：PDF → PyPDFLoader 加载 → RecursiveCharacterTextSplitter 切分 → BGE-M3 向量化 → 存入 ChromaDB。在线流程：用户提问 → ChromaDB 检索 top-k → 拼接上下文 → DeepSeek 生成回答。

**Tech Stack:** Python 3.10+、LangChain、langchain-deepseek、chromadb、sentence-transformers（BGE-M3）、pypdf

---

## 文件结构

阶段0 只有一个脚本文件 + 测试文档 + 环境配置：

```
rag-kb/
├── demo.py              # 阶段0 的单文件 demo 脚本
├── data/
│   └── sample.txt       # 用于测试的文本文件
├── .env                 # 存放 DEEPSEEK_API_KEY（gitignore）
├── .env.example         # 环境变量模板（上传 Gitee）
├── .gitignore
└── requirements.txt     # 依赖清单
```

---

## Task 1: 项目初始化与环境搭建

**Files:**
- Create: `rag-kb/requirements.txt`
- Create: `rag-kb/.gitignore`
- Create: `rag-kb/.env.example`

- [ ] **Step 1: 创建项目目录**

```bash
mkdir rag-kb/data
cd rag-kb
```

- [ ] **Step 2: 创建 Python 虚拟环境**

```bash
python3 -m venv venv
venv\Scripts\activate.bat
```

验证：命令行提示符前出现 `(venv)`

- [ ] **Step 3: 创建 requirements.txt**

```
langchain>=0.3.0
langchain-deepseek>=0.1.0
langchain-community>=0.3.0
chromadb>=0.5.0
sentence-transformers>=3.0.0
pypdf>=4.0.0
python-dotenv>=1.0.0
```

- [ ] **Step 4: 安装依赖**

```bash
pip install -r requirements.txt
```

验证：无报错。如果 BGE-M3 相关包安装慢，加镜像源：
```bash
pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/
```

- [ ] **Step 5: 创建 .gitignore**

```
venv/
__pycache__/
*.pyc
.env
chroma_db/
models/
```

- [ ] **Step 6: 创建 .env.example**

```
# DeepSeek API Key
# 去 https://platform.deepseek.com/ 注册并创建 API Key
DEEPSEEK_API_KEY=your-api-key-here
```

- [ ] **Step 7: 创建 .env（填入真实 Key，不上传）**

```
DEEPSEEK_API_KEY=sk-你真实的key
```

- [ ] **Step 8: 初始化 Git 仓库**

```bash
git init
git add .
git commit -m "chore: project initialization"
```

验证：`git log` 显示一条提交记录

---

## Task 2: 验证 DeepSeek API 连通性

**Files:**
- Create: `rag-kb/demo.py`

- [ ] **Step 1: 写最小验证脚本**

```python
import os
from dotenv import load_dotenv
from langchain_deepseek import ChatDeepSeek

# 加载环境变量
load_dotenv()

# 初始化 DeepSeek
llm = ChatDeepSeek(
    model="deepseek-v4-flash",
    api_key=os.getenv("DEEPSEEK_API_KEY"),
)

# 发一条消息测试
response = llm.invoke("你好，请用一句话介绍你自己。")
print(response.content)

```

- [ ] **Step 2: 运行脚本**

```bash
python demo.py
```

预期输出：DeepSeek 返回一段自我介绍的文字

- [ ] **Step 3: 如果报错，排查**

常见问题：
- `AuthenticationError`：API Key 错误或未填，检查 `.env` 文件
- `ConnectionError`：网络问题，检查能否访问 `api.deepseek.com`
- `ModuleNotFoundError`：依赖未装全，重新 `pip install -r requirements.txt`

- [ ] **Step 4: 确认连通后提交**

```bash
git add demo.py .env.example .gitignore requirements.txt
git commit -m "feat: verify DeepSeek API connectivity"
```

---

## Task 3: 验证 BGE-M3 Embedding 可用

**Files:**
- Modify: `rag-kb/demo.py`

- [ ] **Step 1: 在 demo.py 末尾追加 embedding 测试代码**

```python
# ===== Embedding 测试 =====
from langchain_huggingface import HuggingFaceEmbeddings

# 初始化 BGE-M3（首次运行会下载模型，约2GB，需要几分钟）
embeddings = HuggingFaceEmbeddings(
    model_name="BAAI/bge-m3",
    model_kwargs={"device": "cpu"},
)

# 测试：把两句话转向量，看维度
vec1 = embeddings.embed_query("网络安全")
vec2 = embeddings.embed_query("信息安全")

print(f"向量维度: {len(vec1)}")
print(f"两个相似词的向量前5位: {vec1[:5]}")
print(f"对比词的向量前5位: {vec2[:5]}")
```

- [ ] **Step 2: 安装额外依赖**

```bash
pip install langchain-huggingface
```

- [ ] **Step 3: 运行脚本**

```bash
python demo.py
```

预期：首次运行会下载 BGE-M3 模型（耗时较长），之后打印出向量维度（应为 1024）和前5位数值

- [ ] **Step 4: 如果模型下载慢/失败，用镜像源**

```python
# 在脚本最顶部加入
import os
os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
```

重新运行

- [ ] **Step 5: 提交**

```bash
git add demo.py requirements.txt
git commit -m "feat: verify BGE-M3 embedding"
```

---

## Task 4: 验证 ChromaDB 存取

**Files:**
- Modify: `rag-kb/demo.py`
- Create: `rag-kb/data/sample.txt`

- [ ] **Step 1: 创建测试文档**

```
网络安全是指保护网络系统中的硬件、软件和数据免受攻击、损坏或未经授权访问的实践。

常见的Web漏洞包括SQL注入、跨站脚本攻击（XSS）、跨站请求伪造（CSRF）等。

渗透测试是一种通过模拟攻击者行为来评估系统安全性的方法，常用工具有Burp Suite、Nmap、Metasploit。
```

- [ ] **Step 2: 在 demo.py 追加 ChromaDB 测试**

```python
# ===== ChromaDB 测试 =====
from langchain_community.vectorstores import Chroma

# 用前面的 embeddings 对象
texts = [
    "网络安全是保护系统免受攻击的实践",
    "Python是一种编程语言，适合快速开发",
    "SQL注入是常见的Web漏洞",
]

# 存入 ChromaDB（持久化到本地目录）
vectorstore = Chroma.from_texts(
    texts=texts,
    embedding=embeddings,
    persist_directory="./chroma_db",
)

# 检索测试
results = vectorstore.similarity_search("什么是网络安全", k=2)
print("\n检索结果:")
for i, doc in enumerate(results):
    print(f"  {i+1}. {doc.page_content}")
```

- [ ] **Step 3: 运行脚本**

```bash
python demo.py
```

预期：检索结果第一条是"网络安全是保护系统免受攻击的实践"

- [ ] **Step 4: 提交**

```bash
git add demo.py data/sample.txt
git commit -m "feat: verify ChromaDB storage and retrieval"
```

---

## Task 5: 跑通完整 RAG 链路（文本文件）

**Files:**
- Modify: `rag-kb/demo.py`

- [ ] **Step 1: 在 demo.py 追加完整 RAG 流程**

```python
# ===== 完整 RAG 流程 =====
from langchain_community.document_loaders import TextLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter

# 1. 加载文档
loader = TextLoader("./data/sample.txt")
docs = loader.load()

# 2. 切分文档
splitter = RecursiveCharacterTextSplitter(
    chunk_size=200,
    chunk_overlap=50,
)
chunks = splitter.split_documents(docs)
print(f"\n文档切分: {len(chunks)} 个块")

# 3. 向量化并存入 ChromaDB
vectorstore = Chroma.from_documents(
    documents=chunks,
    embedding=embeddings,
    persist_directory="./chroma_db",
)

# 4. 构建检索器
retriever = vectorstore.as_retriever(search_kwargs={"k": 2})

# 5. 构建 RAG 链
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate

# 定义提示词模板
system_prompt = (
    "你是一个文档问答助手。请根据以下检索到的文档内容回答用户问题。"
    "如果文档中没有相关信息，请说'文档中没有相关信息'。"
    "\n\n文档内容:\n{context}"
)

prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    ("human", "{input}"),
])

# 组合链
question_answer_chain = create_stuff_documents_chain(llm, prompt)
rag_chain = create_retrieval_chain(retriever, question_answer_chain)

# 6. 提问
question = "常见的Web漏洞有哪些？"
response = rag_chain.invoke({"input": question})

print(f"\n问题: {question}")
print(f"回答: {response['answer']}")

# 再问一个
question2 = "渗透测试是什么？"
response2 = rag_chain.invoke({"input": question2})
print(f"\n问题: {question2}")
print(f"回答: {response2['answer']}")
```

- [ ] **Step 2: 运行脚本**

```bash
python demo.py
```

预期输出：
- 回答1：列出 SQL 注入、XSS、CSRF 等漏洞（来自文档内容）
- 回答2：解释渗透测试是模拟攻击评估安全性（来自文档内容）

- [ ] **Step 3: 验证回答确实来自文档**

试问一个文档中没有的问题：
```python
question3 = "Java的Spring框架怎么用？"
response3 = rag_chain.invoke({"input": question3})
print(f"\n问题: {question3}")
print(f"回答: {response3['answer']}")
```

预期：回答"文档中没有相关信息"（证明它不是瞎编，而是基于文档回答）

- [ ] **Step 4: 提交**

```bash
git add demo.py
git commit -m "feat: complete RAG pipeline with text file"
```

---

## Task 6: 替换为 PDF 文件

**Files:**
- Modify: `rag-kb/demo.py`
- Create: `rag-kb/data/sample.pdf`（用户自行准备一个 PDF，可用网安相关资料）

- [ ] **Step 1: 准备一个 PDF 文件**

找一个你感兴趣的网安 PDF（如 OWASP Top 10 说明文档、某工具的使用手册），放到 `data/sample.pdf`。如果没有现成的，可以用任意 PDF 测试。

- [ ] **Step 2: 修改 demo.py 的加载部分**

把 TextLoader 换成 PyPDFLoader：

```python
# 把这行
# loader = TextLoader("./data/sample.txt")

# 换成
from langchain_community.document_loaders import PyPDFLoader
loader = PyPDFLoader("./data/sample.pdf")
```

- [ ] **Step 3: 调整切分参数（PDF 通常需要更大的 chunk）**

```python
splitter = RecursiveCharacterTextSplitter(
    chunk_size=500,      # 从200调到500
    chunk_overlap=100,   # 从50调到100
)
```

- [ ] **Step 4: 运行脚本，针对 PDF 提问**

```bash
python demo.py
```

预期：能基于 PDF 内容回答问题

- [ ] **Step 5: 提交**

```bash
git add demo.py data/sample.pdf
git commit -m "feat: support PDF document loading"
```

---

## Task 7: 上传 Gitee

**Files:**
- 无新文件，操作 Git 远程仓库

- [ ] **Step 1: 注册 Gitee 账号**

访问 https://gitee.com 注册

- [ ] **Step 2: 创建新仓库**

在 Gitee 上创建仓库：
- 仓库名称：`rag-kb`
- 可见性：公开
- 不要勾选"初始化仓库"（本地已有 git）

- [ ] **Step 3: 添加远程仓库并推送**

```bash
git remote add origin https://gitee.com/你的用户名/rag-kb.git
git push -u origin main
```

如果本地分支是 master：
```bash
git branch -M main
git push -u origin main
```

验证：在浏览器打开 Gitee 仓库页面，能看到所有文件

- [ ] **Step 4: 确认 .env 没被上传**

检查 Gitee 仓库里没有 `.env` 文件（只有 `.env.example`）。如果误传了：
```bash
git rm --cached .env
git commit -m "fix: remove .env from tracking"
git push
```

---

## Task 8: 理解与记录

**Files:**
- Create: `rag-kb/NOTES.md`（学习笔记，记录不懂的点）

- [ ] **Step 1: 通读 demo.py 每一行代码**

对每一行，问自己：这行在干什么？如果不理解，用 AI 辅助解释。

- [ ] **Step 2: 记录不懂的技术点**

创建 `NOTES.md`，列出所有还没完全理解的概念，例如：
- RecursiveCharacterTextSplitter 的切分逻辑是什么？
- chunk_size 和 chunk_overlap 怎么影响效果？
- similarity_search 的 k=2 是什么意思？
- create_retrieval_chain 和 create_stuff_documents_chain 分别做什么？
- persist_directory 持久化的原理？

- [ ] **Step 3: 逐个攻克**

用 AI 辅助逐个理解，在 NOTES.md 里补充自己的理解

- [ ] **Step 4: 提交笔记**

```bash
git add NOTES.md
git commit -m "docs: add learning notes for stage 0"
git push
```

---

## 阶段0 完成验收清单

- [ ] `demo.py` 能跑通：传入 PDF → 基于内容问答
- [ ] 回答确实来自文档（问文档外的问题会说"没有相关信息"）
- [ ] 能用自己的话讲清楚离线流程和在线流程
- [ ] NOTES.md 记录了所有疑问和解答
- [ ] 代码已上传 Gitee，`.env` 未泄露
- [ ] Gitee 仓库 README 说明这是阶段0 的 demo

---

## 阶段0 不做的事

- ❌ 不自己设计架构（照抄教程结构）
- ❌ 不追求代码质量（能跑就行，阶段1 重构）
- ❌ 不拆分模块（单文件脚本）
- ❌ 不纠结不懂的原理（记到 NOTES.md，阶段1 攻克）
