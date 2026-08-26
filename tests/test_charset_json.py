"""v1.0.2 缺陷修复验收：JSON REST 端点响应头补 charset=utf-8。

背景：v1.0.1 仅给 MCP SSE 端点补了 charset（ASGI 中间件），JSON 端点
（如 /api/v1/healthz）漏掉，PowerShell Invoke-RestMethod 按默认编码
解码中文时出现乱码。本测试验证任意 JSON 端点响应头 Content-Type
均声明 utf-8。
"""
import pytest

pytestmark = pytest.mark.integration


def test_healthz_响应头含charset(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.get("/api/v1/healthz")
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/json" in ct and "charset=utf-8" in ct.lower(), \
            f"healthz 响应头应含 charset=utf-8：{ct}"


def test_search_响应头含charset(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/search", json={"query": "测试", "top_k": 1})
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "application/json" in ct and "charset=utf-8" in ct.lower(), \
            f"search 响应头应含 charset=utf-8：{ct}"


def test_错误响应_422也含charset(env_isolated):
    """校验失败（422）走的 JSONResponse 同样应带 charset。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/search", json={"query": "测试", "top_k": 0})
        assert r.status_code == 422
        ct = r.headers.get("content-type", "")
        assert "application/json" in ct and "charset=utf-8" in ct.lower(), \
            f"422 响应头应含 charset=utf-8：{ct}"


def test_MCP_SSE不回归(env_isolated):
    """v1.0.1 的 SSE charset 行为不得被破坏。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    init_req = {"jsonrpc": "2.0", "method": "initialize", "id": 1,
                "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "1.0"}}}
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as c:
        r = c.post("/mcp/", json=init_req,
                   headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "text/event-stream" in ct and "charset" in ct.lower(), \
            f"SSE 响应头应含 charset：{ct}"


# ---- TASK-0045：主要 JSON 端点 charset 断言补全（只加断言，不改业务代码） ----

def _assert_json_charset(ct: str, endpoint: str) -> None:
    """断言响应 Content-Type 为 application/json 且声明 charset=utf-8。"""
    assert "application/json" in ct and "charset=utf-8" in ct.lower(), \
        f"{endpoint} 响应头应含 charset=utf-8：{ct}"


def test_memories_POST响应头含charset(env_isolated):
    """POST /memories 写入成功（200）响应头应含 charset。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/memories",
                   json={"content": "charset断言测试记忆", "tags": ["charset"]})
        assert r.status_code == 200
        _assert_json_charset(r.headers.get("content-type", ""), "POST /memories")


def test_memories_GET响应头含charset(env_isolated):
    """GET /memories 列表（200）响应头应含 charset。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.get("/api/v1/memories")
        assert r.status_code == 200
        _assert_json_charset(r.headers.get("content-type", ""), "GET /memories")


def test_config_响应头含charset(env_isolated):
    """GET /config 前端只读配置（200）响应头应含 charset。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.get("/api/v1/config")
        assert r.status_code == 200
        _assert_json_charset(r.headers.get("content-type", ""), "GET /config")


def test_logs_响应头含charset(env_isolated):
    """GET /logs（N18）：日志文件缺失时返回空列表（200），响应头应含 charset。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.get("/api/v1/logs", params={"limit": 5})
        assert r.status_code == 200
        _assert_json_charset(r.headers.get("content-type", ""), "GET /logs")


def test_ask_503错误响应也含charset(env_isolated):
    """POST /ask：测试环境 LLM 探测不可达 → 503 LLM_DISABLED，
    错误 JSON 走同一中间件，响应头同样应含 charset。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/ask", json={"question": "测试"})
        assert r.status_code == 503  # env_isolated 下 Ollama 基址不可达，确定性走 503 分支
        _assert_json_charset(r.headers.get("content-type", ""), "POST /ask(503)")