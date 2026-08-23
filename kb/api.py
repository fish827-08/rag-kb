"""REST API：FastAPI 应用工厂 + memories CRUD + healthz + ask + 统一错误格式。"""
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.requests import Request
from pydantic import BaseModel

from kb.config import Settings, get_settings
from kb.mcp import create_mcp_server
from kb.service import (KBService, LLMDisabledError, UnsupportedFormatError,
                        WebFetchError)


class MemoryCreate(BaseModel):
    """创建记忆请求。"""
    content: str
    tags: list[str] = []
    source: str | None = None
    namespace: str = "default"


class MemoryUpdate(BaseModel):
    """更新记忆请求；至少一项非空。"""
    content: str | None = None
    tags: list[str] | None = None


class SearchRequest(BaseModel):
    """检索请求；query 必填，top_k/mode 带默认值，type/tag 可选过滤。"""
    query: str
    top_k: int = 5
    mode: str = "hybrid"
    type: str | None = None
    tag: str | None = None


class AskRequest(BaseModel):
    """问答请求；question 必填。"""
    question: str


class WebIngestRequest(BaseModel):
    """网页摄取请求；url 必填。"""
    url: str


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂；全局单例 KBService 挂 app.state.kb（REST 与 MCP 共享该实例）。
    统一错误 JSON：{"error": "<CODE>", "message": "<人话>"}。"""
    kb = KBService(settings)
    # MCP 服务器与 REST 共享同一 KBService 单例；先建 streamable http 应用
    # （内部懒创建会话管理器，随后由主应用 lifespan 托管启停）
    mcp_server = create_mcp_server(kb)
    mcp_app = mcp_server.streamable_http_app(streamable_http_path="/")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        """挂载的子应用 lifespan 不会自动执行，在此托管 MCP 会话管理器生命周期。"""
        async with mcp_server.session_manager.run():
            yield

    app = FastAPI(title="kb memory service", lifespan=lifespan)
    app.state.kb = kb

    @app.exception_handler(HTTPException)
    def http_exc_handler(request: Request, exc: HTTPException):
        """统一错误 JSON：detail 为 dict 时直接扁平返回（不包 detail 层）。"""
        if isinstance(exc.detail, dict):
            return JSONResponse(status_code=exc.status_code, content=exc.detail)
        return JSONResponse(status_code=exc.status_code,
                            content={"error": "HTTP_ERROR", "message": str(exc.detail)})

    @app.post("/api/v1/memories")
    def create_memory(body: MemoryCreate) -> dict:
        r = kb.add_memory(body.content, tags=body.tags,
                          source=body.source, namespace=body.namespace)
        return {"id": r.id, **r.model_dump()}

    @app.get("/api/v1/memories")
    def list_memories(type: str | None = None, tag: str | None = None,
                      source: str | None = None, q: str | None = None,
                      limit: int = 100, offset: int = 0) -> dict:
        records, total = kb.list_memories(type=type, tag=tag, source=source,
                                          q=q, limit=limit, offset=offset)
        return {"items": [r.model_dump() for r in records], "total": total}

    @app.get("/api/v1/memories/{record_id}")
    def get_memory(record_id: str) -> dict:
        r = kb.get_memory(record_id)
        if r is None:
            raise HTTPException(status_code=404,
                                detail={"error": "NOT_FOUND", "message": "记录不存在"})
        return r.model_dump()

    @app.patch("/api/v1/memories/{record_id}")
    def update_memory(record_id: str, body: MemoryUpdate) -> dict:
        r = kb.update_memory(record_id, content=body.content, tags=body.tags)
        if r is None:
            raise HTTPException(status_code=404,
                                detail={"error": "NOT_FOUND", "message": "记录不存在"})
        return r.model_dump()

    @app.delete("/api/v1/memories/{record_id}")
    def delete_memory(record_id: str) -> dict:
        if not kb.delete_memory(record_id):
            raise HTTPException(status_code=404,
                                detail={"error": "NOT_FOUND", "message": "记录不存在"})
        return {"ok": True}

    @app.post("/api/v1/search")
    def search(body: SearchRequest) -> dict:
        """混合检索；results 即 KBService.search 的返回。"""
        results = kb.search(body.query, top_k=body.top_k, mode=body.mode,
                            type=body.type, tag=body.tag)
        return {"results": results}

    @app.post("/api/v1/documents")
    async def add_document(request: Request) -> dict:
        """导入文档：multipart 文件上传（字段 file）或 JSON {"path": 本地路径}。

        返回 {"source": 文件名, "chunks": 块数}；
        缺参 / 文件不存在 / 格式不支持 → 400（统一错误 JSON）。
        上传文件保留扩展名落临时文件走统一解析管道，source 用上传文件名。
        """
        content_type = request.headers.get("content-type", "")
        try:
            if content_type.startswith("multipart/form-data"):
                form = await request.form()
                upload = form.get("file")
                if upload is None or isinstance(upload, str):
                    raise HTTPException(status_code=400, detail={
                        "error": "BAD_REQUEST",
                        "message": "multipart 上传需提供文件字段 file"})
                name = Path(upload.filename or "upload").name
                with tempfile.NamedTemporaryFile(
                        suffix=Path(name).suffix, delete=False) as tmp:
                    tmp.write(await upload.read())
                    tmp_path = Path(tmp.name)
                try:
                    return kb.add_document(tmp_path, source=name)
                finally:
                    tmp_path.unlink(missing_ok=True)
            try:
                body = await request.json()
            except Exception:
                body = None
            path = (body or {}).get("path")
            if not path:
                raise HTTPException(status_code=400, detail={
                    "error": "BAD_REQUEST",
                    "message": "需提供 multipart file 字段或 JSON path 字段"})
            if not Path(path).is_file():
                raise HTTPException(status_code=400, detail={
                    "error": "FILE_NOT_FOUND",
                    "message": f"文件不存在：{path}"})
            return kb.add_document(path)
        except UnsupportedFormatError as exc:
            raise HTTPException(status_code=400, detail={
                "error": "UNSUPPORTED_FORMAT", "message": str(exc)})

    @app.get("/api/v1/documents")
    def list_documents() -> dict:
        """按 source 聚合的文档列表。"""
        return {"items": kb.list_documents()}

    @app.delete("/api/v1/documents/{source}")
    def delete_document(source: str) -> dict:
        """按 source 删除文档全部记录。"""
        return {"deleted": kb.delete_document(source)}

    @app.post("/api/v1/ingest/web")
    def ingest_web(body: WebIngestRequest) -> dict:
        """抓取网页正文切分入库；抓取/正文提取失败返回 400 与原因。"""
        try:
            return kb.add_webpage(body.url)
        except WebFetchError as exc:
            raise HTTPException(status_code=400, detail={
                "error": "WEB_FETCH_FAILED", "message": str(exc)})

    @app.post("/api/v1/ask")
    def ask(body: AskRequest) -> dict:
        """基础 RAG 问答；LLM 禁用时返回 503 与配置指引。"""
        try:
            return kb.ask(body.question)
        except LLMDisabledError:
            raise HTTPException(status_code=503, detail={
                "error": "LLM_DISABLED",
                "message": "未检测到可用的 LLM：请安装并启动 Ollama"
                           "（https://ollama.com），或在 .env 配置 "
                           "KB_DEEPSEEK_API_KEY 启用云端"})

    @app.get("/api/v1/healthz")
    def healthz() -> dict:
        return {"status": "ok", **kb.stats()}

    # MCP streamable http 端点挂载在 /mcp（子路径 "/"，即完整路径 /mcp/）
    app.mount("/mcp", mcp_app)

    return app