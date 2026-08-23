"""MCP 服务：8 个工具的薄封装，全部委托 KBService；由 api.py 挂载进同一 ASGI 应用。

工具函数为模块级定义，可直接导入调用（测试与内部复用）；
服务实例通过模块级单例 _service 提供：create_mcp_server 注入时覆盖，
未注入时惰性自建 KBService()（指向当前配置环境，测试顺序无关）。
"""
from mcp.server.mcpserver import MCPServer

from kb.service import KBService, LLMDisabledError, UnsupportedFormatError

# 模块级服务单例：None 表示尚未注入，首次调用工具时惰性自建
_service: KBService | None = None


def _svc() -> KBService:
    """取当前服务单例；未注入时惰性自建 KBService（绑定当前配置环境）。"""
    global _service
    if _service is None:
        _service = KBService()
    return _service


def write_memory(content: str, tags: list[str] | None = None) -> dict:
    """写入一条记忆短文本（事实/笔记/摘要），可选标签；返回 {"id": 记录ID}。"""
    record = _svc().add_memory(content, tags=tags)
    return {"id": record.id}


def search_memory(query: str, top_k: int = 5) -> list[dict]:
    """混合检索记忆与知识（向量语义 + BM25 关键词，RRF 融合）；
    返回命中列表，每项含 id/content/score/type/source。"""
    return _svc().search(query, top_k=top_k)


def read_memory(record_id: str) -> dict:
    """按 ID 读取单条记忆完整内容；记录不存在时返回 {"error": "NOT_FOUND"}。"""
    record = _svc().get_memory(record_id)
    if record is None:
        return {"error": "NOT_FOUND"}
    return record.model_dump()


def update_memory(record_id: str, content: str) -> dict:
    """按 ID 更新记忆内容（变更后自动重新嵌入）；记录不存在时返回 {"error": "NOT_FOUND"}。"""
    record = _svc().update_memory(record_id, content=content)
    if record is None:
        return {"error": "NOT_FOUND"}
    return record.model_dump()


def delete_memory(record_id: str) -> dict:
    """按 ID 删除一条记忆；成功返回 {"ok": true}，记录不存在时返回 {"error": "NOT_FOUND"}。"""
    if not _svc().delete_memory(record_id):
        return {"error": "NOT_FOUND"}
    return {"ok": True}


def add_document(path: str) -> dict:
    """导入本地文档（PDF/DOCX/MD/TXT 及 Office 格式）切分入库；
    返回 {"source": 文件名, "chunks": 块数}；文件不存在或格式不支持时
    返回 {"error": "FILE_NOT_FOUND" | "UNSUPPORTED_FORMAT", "message": 原因}。"""
    try:
        return _svc().add_document(path)
    except UnsupportedFormatError as exc:
        return {"error": "UNSUPPORTED_FORMAT", "message": str(exc)}
    except OSError as exc:
        return {"error": "FILE_NOT_FOUND", "message": f"文件无法读取：{exc}"}


def add_webpage(url: str) -> dict:
    """抓取网页正文并入库；网页摄取尚未就绪，当前返回 {"error": "NOT_READY"}。"""
    return {"error": "NOT_READY"}


def ask_kb(question: str) -> dict:
    """基于知识库的 RAG 问答（检索 → 上下文拼装 → 护栏生成），返回 answer 与 sources；
    LLM 不可用时返回 {"error": "LLM_DISABLED", "message": 配置指引}。"""
    try:
        return _svc().ask(question)
    except LLMDisabledError as exc:
        return {"error": "LLM_DISABLED", "message": str(exc)}


def create_mcp_server(service: KBService) -> MCPServer:
    """创建 MCP 服务器并注册全部 8 个工具（工具名即函数名）。

    注入 service 同时覆盖模块级单例，保证 MCP 工具直连调用与 REST 共享同一 KBService。
    """
    global _service
    _service = service
    mcp = MCPServer(name="kb")
    for fn in (write_memory, search_memory, read_memory, update_memory,
               delete_memory, add_document, add_webpage, ask_kb):
        mcp.tool()(fn)
    return mcp
