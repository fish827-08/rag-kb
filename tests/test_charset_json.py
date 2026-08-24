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