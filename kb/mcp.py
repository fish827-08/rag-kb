"""MCP 服务：11 个工具的薄封装，全部委托 KBService；由 api.py 挂载进同一 ASGI 应用。

工具函数为模块级定义，可直接导入调用（测试与内部复用）；
服务实例通过模块级单例 _service 提供：create_mcp_server 注入时覆盖，
未注入时惰性自建 KBService()（指向当前配置环境，测试顺序无关）。

English: MCP service: a thin wrapper over 11 tools, all delegating to KBService; it is mounted
into the same ASGI app by api.py. Tool functions are module-level and importable directly.
The service instance is provided through the module-level singleton _service:
create_mcp_server overrides it when injected; otherwise a KBService() is lazily created.
"""
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context

from kb import __version__
from kb.links import LinkError
from kb.service import (EmbeddingUnavailableError, KBService, LLMDisabledError,
                        UnsupportedFormatError, WebFetchError, validate_agent_id,
                        validate_client, validate_project)
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
    """身份字段规约校验（v2.1：project 选择项目子库，client 仅审计归类）。

    - project 决定读写哪个项目子库（空=全局库），须通过目录名白名单校验。
    - client 仅用于存取审计归类，从 MCP clientInfo 自动识别。
    - 显式传入非法值拦截；返回 None=通过，否则返回错误消息。
    English: Validate identity fields (v2.1: project selects the project sub-store and
    client is audit-bucketing only). Explicit invalid values are rejected; returns None
    on pass, else an error message."""
    err = validate_client(client or _client_from_ctx(ctx))
    if err:
        return err
    return validate_project(project)


def _resolve_client_ctx(ctx, client: str | None) -> str:
    """统一取 client：显式传值优先，否则从 MCP clientInfo 自动识别。
    English: Resolve client: explicit value wins, else MCP clientInfo."""
    return client or _client_from_ctx(ctx) or "HTTP"


def _embedding_error(exc: EmbeddingUnavailableError) -> dict:
    """MCP 通道统一返回嵌入模型不可用错误（不把底层堆栈抛给客户端）。
    English: Unified MCP error for an unavailable embedding model (never leak the stack trace)."""
    return {"error": "EMBED_MODEL_UNAVAILABLE", "message": str(exc),
            "model": exc.model_name,
            "hint": "检查 KB_EMBED_MODEL；国内网络可设 HF_ENDPOINT=https://hf-mirror.com"}


def write_memory(content: str,
                 tags: list[str] | None = None,
                 project: str | None = None,
                 client: str | None = None,
                 ctx: Context | None = None) -> dict:
    """写入一条记忆短文本（事实/笔记/摘要），可选标签；返回 {"id": 记录ID}。
    内容为空串或纯空白时返回 {"error": "INVALID_ARGUMENT", "message": 原因}。
    project 指定项目子库（空=全局库）；client 仅用于审计归类（自动识别）；
    记录主键由服务端生成。
    English: Write a memory short text (fact/note/summary) with optional tags; returns {"id": record_id}.
    Returns {"error": "INVALID_ARGUMENT", "message": reason} when content is empty or blank.
    project selects the project sub-store (empty = global); client is audit-bucketing only
    (auto-detected). The record primary key is generated server-side."""
    if not content or not content.strip():
        return {"error": "INVALID_ARGUMENT", "message": "content 不能为空或纯空白"}
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    try:
        record = _svc().add_memory(content, tags=tags, agent_id=project or "default",
                                   client=c, project=project)
    except EmbeddingUnavailableError as exc:
        return _embedding_error(exc)
    return {"id": record.id}


def search_memory(query: str, top_k: int = 5,
                  project: str | None = None,
                  client: str | None = None,
                  ctx: Context | None = None) -> list[dict] | dict:
    """混合检索记忆与知识（向量语义 + BM25 关键词，归一化融合）；
    返回命中列表，每项含 id/content/score/type/source。
    project 指定项目子库时，检索范围 = 该项目子库 + 全局库（项目命中加分）；
    不传 project 时只检索全局库。
    top_k 小于 1 时返回 {"error": "INVALID_ARGUMENT", "message": 原因}。
    client：来源客户端（可选，缺省从 clientInfo 自动识别，仅审计）。
    English: Hybrid retrieval over memories and knowledge (vector semantics + BM25 keywords, normalized fusion);
    returns a hit list, each item having id/content/score/type/source.
    With project, the scope is that project's sub-store plus the global store (project hits boosted);
    without project, only the global store is searched.
    Returns {"error": "INVALID_ARGUMENT", "message": reason} when top_k is less than 1.
    client: source client (optional; auto-detected from clientInfo when omitted; audit only)."""
    if top_k < 1:
        return {"error": "INVALID_ARGUMENT", "message": "top_k 必须为不小于 1 的整数"}
    if not query or not query.strip():
        return {"error": "INVALID_ARGUMENT", "message": "query 不能为空或纯空白"}
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    try:
        return _svc().search(query, top_k=top_k, agent_id=project or "default",
                             client=c, project=project)
    except EmbeddingUnavailableError as exc:
        return _embedding_error(exc)


def read_memory(record_id: str,
                project: str | None = None,
                client: str | None = None,
                ctx: Context | None = None) -> dict:
    """按 ID 读取单条记忆完整内容；记录不存在返回 {"error": "NOT_FOUND"}。
    project：先查该项目子库，再查全局库；不传时先查全局库再查各项目子库。
    client：来源客户端（可选，缺省从 clientInfo 自动识别，仅审计）。
    English: Read the full content of a single memory by ID; returns {"error": "NOT_FOUND"} when absent.
    project: search that project's sub-store first, then the global store; when omitted,
    global first and then each project store. client is audit-only."""
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
    project：先查该项目子库，再查全局库；不传时先查全局库再查各项目子库。
    client：来源客户端（可选，缺省从 clientInfo 自动识别，仅审计）。
    English: Update a memory's content by ID (auto re-embed on change); returns {"error": "NOT_FOUND"}
    when absent; empty content returns {"error": "INVALID_ARGUMENT", "message": reason}.
    project: locate in that project's sub-store, then the global store; when omitted,
    global first and then each project store. client is audit-only."""
    if not content or not content.strip():
        return {"error": "INVALID_ARGUMENT", "message": "content 不能为空或纯空白"}
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    try:
        record = _svc().update_memory(record_id, content=content,
                                      agent_id=project or "default",
                                      client=c, project=project)
    except EmbeddingUnavailableError as exc:
        return _embedding_error(exc)
    if record is None:
        return {"error": "NOT_FOUND"}
    return record.model_dump()


def delete_memory(record_id: str,
                  project: str | None = None,
                  client: str | None = None,
                  ctx: Context | None = None) -> dict:
    """按 ID 删除一条记忆；成功返回 {"ok": true}，记录不存在返回 {"error": "NOT_FOUND"}。
    project：先查该项目子库，再查全局库；不传时先查全局库再查各项目子库。
    client：来源客户端（可选，缺省从 clientInfo 自动识别，仅审计）。
    English: Delete a memory by ID; returns {"ok": true} on success and {"error": "NOT_FOUND"} when
    it does not exist. project: locate in that project's sub-store then the global store;
    when omitted, global first and then each project store. client is audit-only."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    if not _svc().delete_memory(record_id, agent_id=project or "default",
                                client=c, project=project):
        return {"error": "NOT_FOUND"}
    return {"ok": True}


def link_memory(from_id: str, to_id: str, relation: str = "related",
                project: str | None = None,
                client: str | None = None,
                ctx: Context | None = None) -> dict:
    """建立两条记忆的关联（related/supersedes/depends_on/part_of/contradicts）；
    重复建边幂等（created=false）。仅同作用域的 memory 记录可关联，禁止自关联；
    失败返回 {"error": <CODE>, "message": 原因}（NOT_FOUND/SELF_LINK/...）。
    project：项目子库（空=全局库）；client：仅审计。
    English: Link two memories (related/supersedes/depends_on/part_of/contradicts);
    duplicate edges are idempotent (created=false). Only memory records in the same
    scope can be linked, and self-links are rejected; failures return
    {"error": <CODE>, "message": reason}."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    try:
        return _svc().link_memories(from_id, to_id, relation=relation,
                                    agent_id=project or "default",
                                    client=c, project=project)
    except LinkError as exc:
        return {"error": exc.code, "message": exc.message}


def unlink_memory(from_id: str, to_id: str, relation: str | None = None,
                  project: str | None = None,
                  client: str | None = None,
                  ctx: Context | None = None) -> dict:
    """取消两条记忆的关联（任一方向）；relation 为空时删除该对全部关系。
    返回 {"ok": true, "removed": 删除数}；记录不存在返回 {"error": "NOT_FOUND"}。
    English: Remove links between two memories in either direction; when relation is
    empty all relations of the pair are removed. Returns {"ok": true, "removed": n}."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    try:
        removed = _svc().unlink_memories(from_id, to_id, relation=relation,
                                         agent_id=project or "default",
                                         client=c, project=project)
    except LinkError as exc:
        return {"error": exc.code, "message": exc.message}
    return {"ok": True, "removed": removed}


def list_links(record_id: str, relation: str | None = None,
               project: str | None = None,
               client: str | None = None,
               ctx: Context | None = None) -> dict:
    """列某条记忆的一跳邻居，返回 {"record_id", "links"}；每项含
    id/content/type/tags/relation/direction（out/in/both）。
    记录不存在返回 {"error": "NOT_FOUND"}。
    English: List one-hop neighbors of a memory as {"record_id", "links"}; each item
    has id/content/type/tags/relation/direction (out/in/both)."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    try:
        return _svc().list_links(record_id, relation=relation, project=project)
    except LinkError as exc:
        return {"error": exc.code, "message": exc.message}


def add_document(path: str,
                 project: str | None = None,
                 client: str | None = None,
                 ctx: Context | None = None) -> dict:
    """导入本地文档（PDF/DOCX/MD/TXT 及 Office 格式）切分入库；
    返回 {"source": 文件名, "chunks": 块数}；文件不存在或格式不支持时
    返回 {"error": "FILE_NOT_FOUND" | "UNSUPPORTED_FORMAT", "message": 原因}。
    project：文档 chunk 落入该项目子库（空=全局库）；client：仅用于审计归类。
    English: Import a local document (PDF/DOCX/MD/TXT and Office formats), split and ingest it;
    returns {"source": filename, "chunks": count}; when the file is missing or the format is unsupported
    returns {"error": "FILE_NOT_FOUND" | "UNSUPPORTED_FORMAT", "message": reason}.
    project: chunks go to that project's sub-store (empty = global); client is audit-only."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    try:
        return _svc().add_document(path, agent_id=project or "default",
                                   client=c, project=project)
    except UnsupportedFormatError as exc:
        return {"error": "UNSUPPORTED_FORMAT", "message": str(exc)}
    except EmbeddingUnavailableError as exc:
        return _embedding_error(exc)
    except OSError as exc:
        return {"error": "FILE_NOT_FOUND", "message": f"文件无法读取：{exc}"}


def add_webpage(url: str,
                project: str | None = None,
                client: str | None = None,
                ctx: Context | None = None) -> dict:
    """抓取网页正文并切分入库；返回 {"source": url, "chunks": 块数}；
    抓取/正文提取失败时返回 {"error": "WEB_FETCH_FAILED", "message": 原因}。
    project：web chunk 落入该项目子库（空=全局库）；client：仅用于审计归类。
    English: Fetch a webpage body and ingest it after splitting; returns {"source": url, "chunks": count};
    on fetch/body-extraction failure returns {"error": "WEB_FETCH_FAILED", "message": reason}.
    project: chunks go to that project's sub-store (empty = global); client is audit-only."""
    err = _check_identity(ctx, client, project)
    if err:
        return {"error": "INVALID_ARGUMENT", "message": err}
    c = _resolve_client_ctx(ctx, client)
    try:
        return _svc().add_webpage(url, agent_id=project or "default",
                                  client=c, project=project)
    except WebFetchError as exc:
        return {"error": "WEB_FETCH_FAILED", "message": str(exc)}
    except EmbeddingUnavailableError as exc:
        return _embedding_error(exc)


def ask_kb(question: str,
           project: str | None = None,
           client: str | None = None,
           ctx: Context | None = None) -> dict:
    """基于知识库的 RAG 问答（检索 → 上下文拼装 → 护栏生成），返回 answer 与 sources；
    project：检索该项目子库 + 全局库（空=仅全局库），并写 ask 存取审计；
    client：来源客户端（可选，缺省从 clientInfo 自动识别，仅审计）；
    LLM 不可用时返回 {"error": "LLM_DISABLED", "message": 配置指引}。
    English: Knowledge-base RAG Q&A (retrieve → build context → guarded generation), returning answer and sources;
    project: search that project's sub-store plus the global store (empty = global only); an ask
    access-audit is emitted; client is audit-only;
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
    except EmbeddingUnavailableError as exc:
        return _embedding_error(exc)


def create_mcp_server(service: KBService) -> MCPServer:
    """创建 MCP 服务器并注册全部 11 个工具（工具名即函数名）。

    注入 service 同时覆盖模块级单例，保证 MCP 工具直连调用与 REST 共享同一 KBService。
    English: Create the MCP server and register all 11 tools (tool names are the function names).
    Injecting the service also overrides the module-level singleton, so MCP tools and REST share the same KBService."""
    global _service
    _service = service
    # v2.1：MCPServer instructions 承载接入规约——任何客户端挂载 MCP 即自动注入 AI 上下文；
    # 语言按 KB_MEMORY_LANG/系统语言选择，并注入用户自定义记忆规则（~/.kb/rules.md、
    # kb_data/rules.md）。核心：不要求先做健康探测、反馈极简、项目记忆按 project 隔离。
    mcp = MCPServer(name="kb", version=__version__,
                    instructions=mcp_instructions())
    for fn in (write_memory, search_memory, read_memory, update_memory,
               delete_memory, link_memory, unlink_memory, list_links,
               add_document, add_webpage, ask_kb):
        mcp.tool()(fn)
    return mcp
