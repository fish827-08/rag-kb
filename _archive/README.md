# RAG-KB 本地文档问答系统

基于 RAG（检索增强生成）架构的本地知识库问答系统，导入 PDF/TXT/MD 文档，提问即可获得基于文档内容的回答，并标注引用来源。

## 功能列表

- 支持导入 PDF / TXT / MD 三种文档格式
- 文档自动切分为文本块并向量化存储
- 提问时检索最相关文档片段，交给 LLM 生成回答
- 单次提问和交互式连续问答两种模式
- 回答附带引用来源（文件名、页码、内容片段）
- 支持批量导入整个目录
- 向量库持久化，程序重启后数据不丢失

## 技术栈

| 组件 | 技术 | 说明 |
|---|---|---|
| LLM | DeepSeek + langchain-deepseek | 生成回答 |
| Embedding | BGE-M3 (BAAI/bge-m3) | 文本向量化，1024 维 |
| 向量库 | ChromaDB | 本地持久化向量存储与检索 |
| 框架 | LangChain 0.3 (LCEL) | RAG 链编排 |
| CLI | Typer + Rich | 命令行交互界面 |
| 文档加载 | PyPDFLoader / TextLoader | PDF / TXT / MD 加载 |
| 切分 | RecursiveCharacterTextSplitter | 递归字符切分 |

## 环境要求

- Python 3.10+
- DeepSeek API Key（去 https://platform.deepseek.com/ 注册获取）
- 首次运行需下载 BGE-M3 模型（约 2GB），请确保网络通畅

## 安装步骤

### 1. 克隆仓库

```bash
git clone https://gitee.com/little-fishy/rag-kb.git
cd rag-kb
```

### 2. 创建虚拟环境

```bash
# Windows
python -m venv venv
venv\Scripts\activate

# macOS / Linux
python3 -m venv venv
source venv/bin/activate
```

### 3. 安装依赖

```bash
pip install -r requirements.txt
```

> 如果下载慢，使用国内镜像：`pip install -r requirements.txt -i https://mirrors.aliyun.com/pypi/simple/`

### 4. 配置环境变量

复制 `.env.example` 为 `.env`，填入你的 DeepSeek API Key：

```bash
cp .env.example .env
```

编辑 `.env`：

```
DEEPSEEK_API_KEY=sk-你的真实key
```

## 使用说明

所有命令通过 `python -m rag_kb <命令>` 调用。查看帮助：

```bash
python -m rag_kb --help
```

### 导入单个文档

```bash
# 文件在 data 目录下，直接写文件名即可
python -m rag_kb add sample.txt

# 也可指定完整路径
python -m rag_kb add /path/to/your/document.pdf
```

### 批量导入目录

```bash
python -m rag_kb add-dir data
```

自动扫描目录下所有 `.pdf` / `.txt` / `.md` 文件并导入。

### 单次提问

```bash
# 直接带问题
python -m rag_kb ask "常见Web漏洞有哪些"

# 不带问题，会弹出输入提示
python -m rag_kb ask
```

输出包含回答和引用来源（文件名、页码、内容片段）。

### 交互式问答

```bash
python -m rag_kb chat
```

进入连续问答模式，输入 `quit` / `exit` / `q` / `bye` 退出。

### 清空向量库

```bash
python -m rag_kb clear
```

会弹出二次确认。

### 查看当前配置

```bash
python -m rag_kb info
```

以表格形式展示所有配置项。

## 项目结构

```
rag-kb/
├── rag_kb/                     # 核心包
│   ├── __init__.py             # 包初始化，设置 HuggingFace 镜像
│   ├── __main__.py             # 入口，支持 python -m rag_kb 运行
│   ├── config.py               # 配置管理（所有参数集中）
│   ├── document_processor.py   # 文档加载与切分
│   ├── embeddings.py           # BGE-M3 向量化（延迟加载单例）
│   ├── vector_store.py         # ChromaDB 存取与检索
│   ├── rag_chain.py            # RAG 链编排（LCEL）
│   └── cli.py                  # Typer 命令行界面
├── data/                       # 文档目录（放 PDF/TXT/MD）
│   ├── sample.pdf
│   ├── sample.txt
│   └── sample.docx
├── chroma_db/                  # 向量库持久化目录（自动生成）
├── .env.example                # 环境变量模板
├── .gitignore
├── requirements.txt
└── README.md
```

### 模块职责

| 模块 | 职责 |
|---|---|
| `config.py` | 集中管理所有配置参数，避免硬编码 |
| `document_processor.py` | 文档加载（PDF/TXT/MD）+ 文本切分 |
| `embeddings.py` | BGE-M3 模型封装，延迟加载，单例模式 |
| `vector_store.py` | ChromaDB 的存入、检索、清空操作 |
| `rag_chain.py` | 串联以上模块，构建 RAG 问答链 |
| `cli.py` | 命令行入口，调用 rag_chain 完成用户操作 |

依赖方向：

```
cli.py → rag_chain.py → document_processor.py
                     → vector_store.py → embeddings.py
                     → config.py（被所有模块依赖）
```

## 配置说明

所有配置集中在 `rag_kb/config.py` 的 `Config` 类中：

| 配置项 | 默认值 | 说明 |
|---|---|---|
| `DEEPSEEK_API_KEY` | 从 .env 读取 | DeepSeek API Key |
| `DEEPSEEK_MODEL` | deepseek-v4-flash | LLM 模型名 |
| `EMBEDDING_MODEL` | BAAI/bge-m3 | 向量化模型 |
| `EMBEDDING_DEVICE` | cpu | 运行设备（可改 cuda） |
| `CHUNK_SIZE` | 500 | 文本切分大小 |
| `CHUNK_OVERLAP` | 100 | 切分重叠大小 |
| `SEARCH_K` | 3 | 检索返回的文档数量 |
| `COLLECTION_NAME` | rag_kb_collection | ChromaDB collection 名 |

## 开发计划

- 阶段0：跑通单文件 Demo（已完成）
- 阶段1：模块化重构，支持 CLI 交互（已完成）
- 后续：支持更多文档格式、对话历史记忆、Web 界面
