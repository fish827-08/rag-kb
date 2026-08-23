"""REST API：FastAPI 应用工厂 + memories CRUD + healthz + ask + 统一错误格式。"""
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.requests import Request
from pydantic import BaseModel

from kb.config import Settings, get_settings
from kb.service import KBService, LLMDisabledError


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


def create_app(settings: Settings | None = None) -> FastAPI:
    """应用工厂；全局单例 KBService 挂 app.state.kb。
    统一错误 JSON：{"error": "<CODE>", "message": "<人话>"}。"""
    kb = KBService(settings)
    app = FastAPI(title="kb memory service")
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

    @app.get("/api/v1/documents")
    def list_documents() -> dict:
        """按 source 聚合的文档列表。"""
        return {"items": kb.list_documents()}

    @app.delete("/api/v1/documents/{source}")
    def delete_document(source: str) -> dict:
        """按 source 删除文档全部记录。"""
        return {"deleted": kb.delete_document(source)}

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

    return app