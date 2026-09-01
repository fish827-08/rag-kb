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
from kb.i18n import mcp_instructions

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


def _check_identity(ctx, client: str | None,
                    project: str | None) -> str | None:
    """身份字段规约校验（v3：client/project 仅审计归类，不参与隔离）。

    - v3（2026-08-31）：记忆全共享，client/project 不再承担隔离语义，
      仅用于存取审计归类与记录元数据；仍校验显式传入值的格式。
    - client/project 可空缺省（client 空=自动识别），显式传入非法值拦截。
    返回 None=通过，否则返回错误消息。
    English: Validate the identity fields (v3: client/project are audit-bucketing only,
    never used for isolation). Only explicitly passed values are format-checked;
    empty client = auto-detected from MCP clientInfo. Returns None on pass, else an error message."""
    err = validate_client(client or _client_from_ctx(ctx))
    if err:
        return err
    return validate_project(project)


def _resolve_client_ctx(ctx, client: str | None) -> str:
    """统一取 client：显式传值优先，否则从 MCP clientInfo 自动识别。
    English: Resolve client: explicit value wins, else MCP clientInfo."""
    return client or _client_from_ctx(ctx) or "HTTP"


def write_memory(content: str,
                 tags: list[str] | None = None,
                 project: str | None = None,
                 client: str | None = None,
                 ctx: Context | None = None) -> dict:
    """写入一条记忆短文本（事实/笔记/摘要），可选标签；返回 {"id": 记录ID}。
    内容为空串或纯空白时返回 {"error": "INVALID_ARGUMENT", "message": 原因}。
    v3（2026-08-31）：所有记忆全共享——client（MCP 握手 clientInfo 自动识别）与
    project（可选）仅用于审计归类，不再隔离读写；记录主键由服务端生成。
    English: Write a memory short text (fact/note/summary) with optional tags; returns {"id": record_id}.
    Returns {"error": "INVALID_ARGUMENT", "message": reason} when content is empty or blank.
    v3: all memories are fully shared — client (auto-detected from the MCP clientInfo) and
    project (optional) are audit-bucketing only and never isolate access. The record primary
    key is generated server-side."""
    if not content or not content.strip():
        return {"error": "INVALID_ARGUMENT", "message": "content 不能为空或纯空白"}
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    record = _svc().add_memory(content, tags=tags, agent_id=project or "default",
                               client=c, project=project)
    return {"id": record.id}


def search_memory(query: str, top_k: int = 5,
                  project: str | None = None,
                  client: str | None = None,
                  ctx: Context | None = None) -> list[dict] | dict:
    """混合检索记忆与知识（向量语义 + BM25 关键词，RRF 融合）；
    返回命中列表，每项含 id/content/score/type/source。
    v3（2026-08-31）：所有记忆与知识全共享，不限 client/project。
    top_k 小于 1 时返回 {"error": "INVALID_ARGUMENT", "message": 原因}。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    English: Hybrid retrieval over memories and knowledge (vector semantics + BM25 keywords, RRF-fused);
    returns a hit list, each item having id/content/score/type/source.
    v3: all memories and knowledge are fully shared, unrestricted by client/project.
    Returns {"error": "INVALID_ARGUMENT", "message": reason} when top_k is less than 1.
    client: source client (optional; auto-detected from clientInfo when omitted)."""
    if top_k < 1:
        return {"error": "INVALID_ARGUMENT", "message": "top_k 必须为不小于 1 的整数"}
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    return _svc().search(query, top_k=top_k, agent_id=project or "default",
                         client=c, project=project)


def read_memory(record_id: str,
                project: str | None = None,
                client: str | None = None,
                ctx: Context | None = None) -> dict:
    """按 ID 读取单条记忆完整内容；记录不存在返回 {"error": "NOT_FOUND"}。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    project：项目归属（可选，仅审计归类）。
    v3（2026-08-31）：所有记忆全共享，无 FORBIDDEN。
    English: Read the full content of a single memory by ID; returns {"error": "NOT_FOUND"} when absent.
    v3: all memories are fully shared — there is no FORBIDDEN."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    record = _svc().get_memory(record_id, agent_id=project or "default",
                               client=c, project=project)
    if record is None:
        return {"error": "NOT_FOUND"}
    return record.model_dump()


def update_memory(record_id: str, content: str,
                  project: str | None = None,
                  client: str | None = None,
                  ctx: Context | None = None) -> dict:
    """按 ID 更新记忆内容（变更后自动重新嵌入）；记录不存在返回 {"error": "NOT_FOUND"}；
    内容为空串或纯空白时返回 {"error": "INVALID_ARGUMENT", "message": 原因}。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    v3（2026-08-31）：所有记忆全共享，无 FORBIDDEN。
    English: Update a memory's content by ID (auto re-embed on change); returns {"error": "NOT_FOUND"}
    when absent; empty content returns {"error": "INVALID_ARGUMENT", "message": reason}.
    v3: all memories are fully shared — there is no FORBIDDEN."""
    if not content or not content.strip():
        return {"error": "INVALID_ARGUMENT", "message": "content 不能为空或纯空白"}
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    record = _svc().update_memory(record_id, content=content,
                                  agent_id=project or "default",
                                  client=c, project=project)
    if record is None:
        return {"error": "NOT_FOUND"}
    return record.model_dump()


def delete_memory(record_id: str,
                  project: str | None = None,
                  client: str | None = None,
                  ctx: Context | None = None) -> dict:
    """按 ID 删除一条记忆；成功返回 {"ok": true}，记录不存在返回 {"error": "NOT_FOUND"}。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    v3（2026-08-31）：所有记忆全共享，无 FORBIDDEN。
    English: Delete a memory by ID; returns {"ok": true} on success and {"error": "NOT_FOUND"} when
    it does not exist. v3: all memories are fully shared — there is no FORBIDDEN."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    if not _svc().delete_memory(record_id, agent_id=project or "default",
                                client=c, project=project):
        return {"error": "NOT_FOUND"}
    return {"ok": True}


def add_document(path: str,
                 project: str | None = None,
                 client: str | None = None,
                 ctx: Context | None = None) -> dict:
    """导入本地文档（PDF/DOCX/MD/TXT 及 Office 格式）切分入库；
    返回 {"source": 文件名, "chunks": 块数}；文件不存在或格式不支持时
    返回 {"error": "FILE_NOT_FOUND" | "UNSUPPORTED_FORMAT", "message": 原因}。
    project/client：仅用于审计归类；文档 chunk 为共享知识，所有客户端可检索。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    English: Import a local document (PDF/DOCX/MD/TXT and Office formats), split and ingest it;
    returns {"source": filename, "chunks": count}; when the file is missing or the format is unsupported
    returns {"error": "FILE_NOT_FOUND" | "UNSUPPORTED_FORMAT", "message": reason}.
    project/client: audit bucketing only; document chunks are shared knowledge, searchable by all clients."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    try:
        return _svc().add_document(path, agent_id=project or "default",
                                   client=c, project=project)
    except UnsupportedFormatError as exc:
        return {"error": "UNSUPPORTED_FORMAT", "message": str(exc)}
    except OSError as exc:
        return {"error": "FILE_NOT_FOUND", "message": f"文件无法读取：{exc}"}


def add_webpage(url: str,
                project: str | None = None,
                client: str | None = None,
                ctx: Context | None = None) -> dict:
    """抓取网页正文并切分入库；返回 {"source": url, "chunks": 块数}；
    抓取/正文提取失败时返回 {"error": "WEB_FETCH_FAILED", "message": 原因}。
    project/client：仅用于审计归类；web chunk 为共享知识，所有客户端可检索。
    client：来源客户端（可选，缺省从 clientInfo 自动识别）。
    English: Fetch a webpage body and ingest it after splitting; returns {"source": url, "chunks": count};
    on fetch/body-extraction failure returns {"error": "WEB_FETCH_FAILED", "message": reason}.
    project/client: audit bucketing only; web chunks are shared knowledge, searchable by all clients."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    try:
        return _svc().add_webpage(url, agent_id=project or "default",
                                  client=c, project=project)
    except WebFetchError as exc:
        return {"error": "WEB_FETCH_FAILED", "message": str(exc)}


def ask_kb(question: str,
           project: str | None = None,
           client: str | None = None,
           ctx: Context | None = None) -> dict:
    """基于知识库的 RAG 问答（检索 → 上下文拼装 → 护栏生成），返回 answer 与 sources；
    v3：检索记忆与知识全共享（无 client/project 隔离），并写 ask 存取审计；
    client：来源客户端（可选，缺省从 clientInfo 自动识别）；
    LLM 不可用时返回 {"error": "LLM_DISABLED", "message": 配置指引}。
    English: Knowledge-base RAG Q&A (retrieve → build context → guarded generation), returning answer and sources;
    v3: retrieval is fully shared over memories and knowledge (no client/project isolation); an ask
    access-audit is emitted; client: source client (optional; auto-detected from clientInfo when omitted);
    returns {"error": "LLM_DISABLED", "message": setup guidance} when the LLM is unavailable."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    try:
        return _svc().ask(question, agent_id=project or "default",
                          client=c, project=project)
    except LLMDisabledError as exc:
        return {"error": "LLM_DISABLED", "message": str(exc)}


def create_mcp_server(service: KBService) -> MCPServer:
    """创建 MCP 服务器并注册全部 8 个工具（工具名即函数名）。

    注入 service 同时覆盖模块级单例，保证 MCP 工具直连调用与 REST 共享同一 KBService。
    English: Create the MCP server and register all 8 tools (tool names are the function names).
    Injecting the service also overrides the module-level singleton, so MCP tools and REST share the same KBService."""
    global _service
    _service = service
    # v3（2026-08-31）：MCPServer instructions 承载全局接入规约——任何客户端挂载 MCP 即自动
    # 注入 AI 上下文（跨客户端全局提示词，无需 skill/客户端全局规则）；中英双语按系统语言选一套。
    # 核心：不要求先做健康探测、反馈极简（记了/没记+原因）、记忆全共享。
    mcp = MCPServer(name="kb",
                    instructions=mcp_instructions())
    for fn in (write_memory, search_memory, read_memory, update_memory,
               delete_memory, add_document, add_webpage, ask_kb):
        mcp.tool()(fn)
    return mcp
