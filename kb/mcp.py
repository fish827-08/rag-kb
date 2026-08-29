"""MCP 服务：8 个工具的薄封装，全部委托 KBService；由 api.py 挂载进同一 ASGI 应用。

工具函数为模块级定义，可直接导入调用（测试与内部复用）；
服务实例通过模块级单例 _service 提供：create_mcp_server 注入时覆盖，
未注入时惰性自建 KBService()（指向当前配置环境，测试顺序无关）。

English: MCP service: a thin wrapper over 8 tools, all delegating to KBService; it is mounted
into the same ASGI app by api.py. Tool functions are module-level and importable directly.
The service instance is provided through the module-level singleton _service:
create_mcp_server overrides it when injected; otherwise a KBService() is lazily created.
"""
from mcp.server.mcpserver import MCPServer

from kb.service import (KBService, LLMDisabledError, UnsupportedFormatError,
                        WebFetchError)

# 模块级服务单例：None 表示尚未注入，首次调用工具时惰性自建
_service: KBService | None = None


def _svc() -> KBService:
    """取当前服务单例；未注入时惰性自建 KBService（绑定当前配置环境）。
    English: Get the current service singleton; lazily create a KBService (bound to the current config
    environment) when none has been injected."""
    global _service
    if _service is None:
        _service = KBService()
    return _service


def write_memory(content: str, tags: list[str] | None = None) -> dict:
    """写入一条记忆短文本（事实/笔记/摘要），可选标签；返回 {"id": 记录ID}。
    内容为空串或纯空白时返回 {"error": "INVALID_ARGUMENT", "message": 原因}。
    English: Write a memory short text (fact/note/summary) with optional tags; returns {"id": record_id}.
    Returns {"error": "INVALID_ARGUMENT", "message": reason} when content is empty or blank."""
    if not content or not content.strip():
        return {"error": "INVALID_ARGUMENT", "message": "content 不能为空或纯空白"}
    record = _svc().add_memory(content, tags=tags)
    return {"id": record.id}


def search_memory(query: str, top_k: int = 5) -> list[dict] | dict:
    """混合检索记忆与知识（向量语义 + BM25 关键词，RRF 融合）；
    返回命中列表，每项含 id/content/score/type/source。
    top_k 小于 1 时返回 {"error": "INVALID_ARGUMENT", "message": 原因}。
    English: Hybrid retrieval over memories and knowledge (vector semantics + BM25 keywords, RRF-fused);
    returns a hit list, each item having id/content/score/type/source.
    Returns {"error": "INVALID_ARGUMENT", "message": reason} when top_k is less than 1."""
    if top_k < 1:
        return {"error": "INVALID_ARGUMENT", "message": "top_k 必须为不小于 1 的整数"}
    return _svc().search(query, top_k=top_k)


def read_memory(record_id: str) -> dict:
    """按 ID 读取单条记忆完整内容；记录不存在时返回 {"error": "NOT_FOUND"}。
    English: Read the full content of a single memory by ID; returns {"error": "NOT_FOUND"} when it does not exist."""
    record = _svc().get_memory(record_id)
    if record is None:
        return {"error": "NOT_FOUND"}
    return record.model_dump()


def update_memory(record_id: str, content: str) -> dict:
    """按 ID 更新记忆内容（变更后自动重新嵌入）；记录不存在时返回 {"error": "NOT_FOUND"}；
    内容为空串或纯空白时返回 {"error": "INVALID_ARGUMENT", "message": 原因}。
    English: Update a memory's content by ID (auto re-embed on change); returns {"error": "NOT_FOUND"}
    when the record does not exist; returns {"error": "INVALID_ARGUMENT", "message": reason} when content
    is empty or blank."""
    if not content or not content.strip():
        return {"error": "INVALID_ARGUMENT", "message": "content 不能为空或纯空白"}
    record = _svc().update_memory(record_id, content=content)
    if record is None:
        return {"error": "NOT_FOUND"}
    return record.model_dump()


def delete_memory(record_id: str) -> dict:
    """按 ID 删除一条记忆；成功返回 {"ok": true}，记录不存在时返回 {"error": "NOT_FOUND"}。
    English: Delete a memory by ID; returns {"ok": true} on success and {"error": "NOT_FOUND"} when it does not exist."""
    if not _svc().delete_memory(record_id):
        return {"error": "NOT_FOUND"}
    return {"ok": True}


def add_document(path: str) -> dict:
    """导入本地文档（PDF/DOCX/MD/TXT 及 Office 格式）切分入库；
    返回 {"source": 文件名, "chunks": 块数}；文件不存在或格式不支持时
    返回 {"error": "FILE_NOT_FOUND" | "UNSUPPORTED_FORMAT", "message": 原因}。
    English: Import a local document (PDF/DOCX/MD/TXT and Office formats), split and ingest it;
    returns {"source": filename, "chunks": count}; when the file is missing or the format is unsupported
    returns {"error": "FILE_NOT_FOUND" | "UNSUPPORTED_FORMAT", "message": reason}."""
    try:
        return _svc().add_document(path)
    except UnsupportedFormatError as exc:
        return {"error": "UNSUPPORTED_FORMAT", "message": str(exc)}
    except OSError as exc:
        return {"error": "FILE_NOT_FOUND", "message": f"文件无法读取：{exc}"}


def add_webpage(url: str) -> dict:
    """抓取网页正文并切分入库；返回 {"source": url, "chunks": 块数}；
    抓取/正文提取失败时返回 {"error": "WEB_FETCH_FAILED", "message": 原因}。
    English: Fetch a webpage body and ingest it after splitting; returns {"source": url, "chunks": count};
    on fetch/body-extraction failure returns {"error": "WEB_FETCH_FAILED", "message": reason}."""
    try:
        return _svc().add_webpage(url)
    except WebFetchError as exc:
        return {"error": "WEB_FETCH_FAILED", "message": str(exc)}


def ask_kb(question: str) -> dict:
    """基于知识库的 RAG 问答（检索 → 上下文拼装 → 护栏生成），返回 answer 与 sources；
    LLM 不可用时返回 {"error": "LLM_DISABLED", "message": 配置指引}。
    English: Knowledge-base RAG Q&A (retrieve → build context → guarded generation), returning answer and sources;
    returns {"error": "LLM_DISABLED", "message": setup guidance} when the LLM is unavailable."""
    try:
        return _svc().ask(question)
    except LLMDisabledError as exc:
        return {"error": "LLM_DISABLED", "message": str(exc)}


def create_mcp_server(service: KBService) -> MCPServer:
    """创建 MCP 服务器并注册全部 8 个工具（工具名即函数名）。

    注入 service 同时覆盖模块级单例，保证 MCP 工具直连调用与 REST 共享同一 KBService。
    English: Create the MCP server and register all 8 tools (tool names are the function names).
    Injecting the service also overrides the module-level singleton, so MCP tools and REST share the same KBService."""
    global _service
    _service = service
    mcp = MCPServer(name="kb")
    for fn in (write_memory, search_memory, read_memory, update_memory,
               delete_memory, add_document, add_webpage, ask_kb):
        mcp.tool()(fn)
    return mcp
