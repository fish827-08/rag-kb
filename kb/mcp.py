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
from mcp.server.mcpserver.context import Context

from kb.service import (KBService, LLMDisabledError, UnsupportedFormatError,
                        WebFetchError, validate_agent_id, validate_client,
                        validate_project)

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


def _client_from_ctx(ctx) -> str:
    """从 MCP 请求上下文提取客户端名（clientInfo.name，如 TraeWork / Claude Code / Cursor）；
    直调（测试）或拿不到时回退 "default"。
    English: Extract the client name from the MCP request context (clientInfo.name);
    falls back to "default" on direct calls (tests) or when unavailable."""
    try:
        if ctx is None:
            return "default"
        params = ctx.request_context.session.client_params
        info = getattr(params, "client_info", None)
        name = getattr(info, "name", None)
        return name or "default"
    except Exception:
        return "default"


def _check_identity(ctx, agent_id: str, client: str | None,
                    project: str | None) -> str | None:
    """身份字段规约校验（agent_id/client/project）。

    - agent_id 必填且禁止 default/unknown 等占位（schema 同步必填，无默认值）；
      无论真实 MCP 会话还是直调，缺失/占位一律拦截。
    - client/project 可空缺省（client 空=自动识别），但显式传入非法值拦截。
    返回 None=通过，否则返回错误消息。
    English: Validate the identity fields. agent_id is REQUIRED (schema shows it as required with
    no default) and cannot be placeholders like default/unknown, on both MCP sessions and direct
    calls. client/project are optional (empty client = auto-detect) but malformed values are rejected."""
    err = validate_agent_id(agent_id, required=True)
    if err:
        return err
    err = validate_client(client or _client_from_ctx(ctx))
    if err:
        return err
    return validate_project(project)


def write_memory(content: str, agent_id: str,
                 tags: list[str] | None = None,
                 client: str | None = None,
                 project: str | None = None,
                 ctx: Context | None = None) -> dict:
    """写入一条记忆短文本（事实/笔记/摘要），可选标签与归属 Agent；返回 {"id": 记录ID}。
    内容为空串或纯空白时返回 {"error": "INVALID_ARGUMENT", "message": 原因}。
    agent_id：调用方 Agent 身份（推荐用任务名，如 TASK-xxx / worker-1；默认 "default"），
    写入后仅该 Agent 可检索/读/改/删该记忆。
    client：来源客户端（可选；不传时自动从 MCP 握手 clientInfo 识别，如 TraeWork/Claude Code）。
    共享知识（add_document/add_webpage）所有 Agent 可见。
    English: Write a memory short text (fact/note/summary) with optional tags and owning agent; returns {"id": record_id}.
    Returns {"error": "INVALID_ARGUMENT", "message": reason} when content is empty or blank.
    agent_id: the calling Agent identity (recommend a task name like TASK-xxx / worker-1; default "default");
    afterwards only this agent can search/read/update/delete the memory.
    client: source client (optional; when omitted it is auto-detected from the MCP clientInfo).
    Shared knowledge (add_document/add_webpage) is visible to all agents."""
    if not content or not content.strip():
        return {"error": "INVALID_ARGUMENT", "message": "content 不能为空或纯空白"}
    err = _check_identity(ctx, agent_id, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    record = _svc().add_memory(content, tags=tags, agent_id=agent_id,
                               client=client or _client_from_ctx(ctx),
                               project=project)
    return {"id": record.id}


def search_memory(query: str, agent_id: str, top_k: int = 5,
                  client: str | None = None,
                  project: str | None = None,
                  ctx: Context | None = None) -> list[dict] | dict:
    """混合检索记忆与知识（向量语义 + BM25 关键词，RRF 融合）；
    返回命中列表，每项含 id/content/score/type/source。
    agent_id：强制隔离——个人记忆（memory）只返回归属该 Agent 的；
    共享知识（doc/web chunk）所有 Agent 可见。top_k 小于 1 时返回
    {"error": "INVALID_ARGUMENT", "message": 原因}。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    English: Hybrid retrieval over memories and knowledge (vector semantics + BM25 keywords, RRF-fused);
    returns a hit list, each item having id/content/score/type/source.
    agent_id: mandatory isolation — memory records only return those owned by the calling agent;
    shared knowledge (doc/web chunks) is visible to all agents.
    Returns {"error": "INVALID_ARGUMENT", "message": reason} when top_k is less than 1.
    client: source client (optional; auto-detected from clientInfo when omitted)."""
    if top_k < 1:
        return {"error": "INVALID_ARGUMENT", "message": "top_k 必须为不小于 1 的整数"}
    err = _check_identity(ctx, agent_id, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    return _svc().search(query, top_k=top_k, agent_id=agent_id,
                         client=client or _client_from_ctx(ctx),
                         project=project)


def read_memory(record_id: str, agent_id: str,
                client: str | None = None,
                project: str | None = None,
                ctx: Context | None = None) -> dict:
    """按 ID 读取单条记忆完整内容；记录不存在返回 {"error": "NOT_FOUND"}；
    他人 memory（agent_id 不匹配）返回 {"error": "FORBIDDEN"}；共享知识可读。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    project：项目名（可选，仅用于审计文件名归类）。
    English: Read the full content of a single memory by ID; returns {"error": "NOT_FOUND"} when absent;
    another agent's memory (agent_id mismatch) returns {"error": "FORBIDDEN"}; shared knowledge is readable."""
    err = _check_identity(ctx, agent_id, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    record = _svc().get_memory(record_id, agent_id=agent_id,
                               client=client or _client_from_ctx(ctx),
                               project=project)
    if record is None:
        existing = _svc().store.get(record_id) if hasattr(_svc(), "store") else None
        if existing is not None and existing.type.value == "memory":
            return {"error": "FORBIDDEN"}
        return {"error": "NOT_FOUND"}
    return record.model_dump()


def update_memory(record_id: str, content: str,
                  agent_id: str,
                  client: str | None = None,
                  project: str | None = None,
                  ctx: Context | None = None) -> dict:
    """按 ID 更新记忆内容（变更后自动重新嵌入）；记录不存在返回 {"error": "NOT_FOUND"}；
    非归属 Agent（agent_id 不匹配）返回 {"error": "FORBIDDEN"}；
    内容为空串或纯空白时返回 {"error": "INVALID_ARGUMENT", "message": 原因}。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    English: Update a memory's content by ID (auto re-embed on change); returns {"error": "NOT_FOUND"}
    when absent; non-owner agents (agent_id mismatch) get {"error": "FORBIDDEN"}; empty content
    returns {"error": "INVALID_ARGUMENT", "message": reason}."""
    if not content or not content.strip():
        return {"error": "INVALID_ARGUMENT", "message": "content 不能为空或纯空白"}
    err = _check_identity(ctx, agent_id, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    record = _svc().update_memory(record_id, content=content, agent_id=agent_id,
                                  client=client or _client_from_ctx(ctx),
                                  project=project)
    if record is None:
        existing = _svc().store.get(record_id) if hasattr(_svc(), "store") else None
        if existing is not None and existing.type.value == "memory":
            return {"error": "FORBIDDEN"}
        return {"error": "NOT_FOUND"}
    return record.model_dump()


def delete_memory(record_id: str, agent_id: str,
                  client: str | None = None,
                  project: str | None = None,
                  ctx: Context | None = None) -> dict:
    """按 ID 删除一条记忆；成功返回 {"ok": true}，记录不存在返回 {"error": "NOT_FOUND"}；
    非归属 Agent（agent_id 不匹配）返回 {"error": "FORBIDDEN"}。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    English: Delete a memory by ID; returns {"ok": true} on success and {"error": "NOT_FOUND"} when
    it does not exist; non-owner agents (agent_id mismatch) get {"error": "FORBIDDEN"}."""
    err = _check_identity(ctx, agent_id, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    if not _svc().delete_memory(record_id, agent_id=agent_id,
                                client=client or _client_from_ctx(ctx),
                                project=project):
        existing = _svc().store.get(record_id) if hasattr(_svc(), "store") else None
        if existing is not None and existing.type.value == "memory":
            return {"error": "FORBIDDEN"}
        return {"error": "NOT_FOUND"}
    return {"ok": True}


def add_document(path: str, agent_id: str,
                 client: str | None = None,
                 project: str | None = None,
                 ctx: Context | None = None) -> dict:
    """导入本地文档（PDF/DOCX/MD/TXT 及 Office 格式）切分入库；
    返回 {"source": 文件名, "chunks": 块数}；文件不存在或格式不支持时
    返回 {"error": "FILE_NOT_FOUND" | "UNSUPPORTED_FORMAT", "message": 原因}。
    agent_id：记录归属仅用于审计；文档 chunk 为共享知识，所有 Agent 可检索。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    English: Import a local document (PDF/DOCX/MD/TXT and Office formats), split and ingest it;
    returns {"source": filename, "chunks": count}; when the file is missing or the format is unsupported
    returns {"error": "FILE_NOT_FOUND" | "UNSUPPORTED_FORMAT", "message": reason}.
    agent_id: recorded for audit only; document chunks are shared knowledge, searchable by all agents."""
    err = _check_identity(ctx, agent_id, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    try:
        return _svc().add_document(path, agent_id=agent_id,
                                   client=client or _client_from_ctx(ctx),
                                   project=project)
    except UnsupportedFormatError as exc:
        return {"error": "UNSUPPORTED_FORMAT", "message": str(exc)}
    except OSError as exc:
        return {"error": "FILE_NOT_FOUND", "message": f"文件无法读取：{exc}"}


def add_webpage(url: str, agent_id: str,
                client: str | None = None,
                project: str | None = None,
                ctx: Context | None = None) -> dict:
    """抓取网页正文并切分入库；返回 {"source": url, "chunks": 块数}；
    抓取/正文提取失败时返回 {"error": "WEB_FETCH_FAILED", "message": 原因}。
    agent_id：记录归属仅用于审计；web chunk 为共享知识，所有 Agent 可检索。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    English: Fetch a webpage body and ingest it after splitting; returns {"source": url, "chunks": count};
    on fetch/body-extraction failure returns {"error": "WEB_FETCH_FAILED", "message": reason}.
    agent_id: recorded for audit only; web chunks are shared knowledge, searchable by all agents."""
    err = _check_identity(ctx, agent_id, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    try:
        return _svc().add_webpage(url, agent_id=agent_id,
                                  client=client or _client_from_ctx(ctx),
                                  project=project)
    except WebFetchError as exc:
        return {"error": "WEB_FETCH_FAILED", "message": str(exc)}


def ask_kb(question: str, agent_id: str,
           client: str | None = None,
           project: str | None = None,
           ctx: Context | None = None) -> dict:
    """基于知识库的 RAG 问答（检索 → 上下文拼装 → 护栏生成），返回 answer 与 sources；
    agent_id：检索按该 Agent 隔离 memory（doc/web 共享），并写 ask 存取审计；
    client：来源客户端（可选，缺省从 clientInfo 自动识别）；
    LLM 不可用时返回 {"error": "LLM_DISABLED", "message": 配置指引}。
    English: Knowledge-base RAG Q&A (retrieve → build context → guarded generation), returning answer and sources;
    agent_id: retrieval isolates memory by this agent (doc/web shared); an ask access-audit is emitted;
    client: source client (optional; auto-detected from clientInfo when omitted);
    returns {"error": "LLM_DISABLED", "message": setup guidance} when the LLM is unavailable."""
    err = _check_identity(ctx, agent_id, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    try:
        return _svc().ask(question, agent_id=agent_id,
                          client=client or _client_from_ctx(ctx),
                          project=project)
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
