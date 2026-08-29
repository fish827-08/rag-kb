"""v1.0.0 收口后缺陷修复验收：参数校验 + MCP 工具入口校验 + SSE charset。

缺陷发现于 2026-08-24 综合测试（收口后压力与边界测试），修复范围：
- REST 层：top_k 下界 / mode 枚举 / content 非空 → pydantic 校验转 422
- MCP 层：write_memory / search_memory / update_memory 入口校验，
  非法参数返回 {"error": "INVALID_ARGUMENT", "message": 原因}
- MCP SSE 响应头补 charset=utf-8（ASGI 中间件，兼容按默认编码解码的客户端）
"""
import pytest

pytestmark = pytest.mark.integration

INIT_REQ = {"jsonrpc": "2.0", "method": "initialize", "id": 1,
            "params": {"protocolVersion": "2025-03-26", "capabilities": {},
                       "clientInfo": {"name": "test", "version": "1.0"}}}


def test_top_k_非法值返回422(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        for bad in (0, -1):
            r = c.post("/api/v1/search", json={"query": "测试", "top_k": bad})
            assert r.status_code == 422, f"top_k={bad} 应 422，实际 {r.status_code}"
        # 边界合法值不受影响
        r = c.post("/api/v1/search", json={"query": "测试", "top_k": 1})
        assert r.status_code == 200


def test_空内容拒绝入库(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        for bad in ("", "   ", "\n\t "):
            r = c.post("/api/v1/memories", json={"content": bad})
            assert r.status_code == 422, f"空内容 {bad!r} 应 422，实际 {r.status_code}"
        # 正常内容不受影响
        r = c.post("/api/v1/memories", json={"content": "正常记忆"})
        assert r.status_code == 200 and "id" in r.json()


def test_非法mode返回422(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/search", json={"query": "测试", "mode": "invalid"})
        assert r.status_code == 422
        # 三个合法 mode 不受影响
        for mode in ("hybrid", "vector", "keyword"):
            r = c.post("/api/v1/search", json={"query": "测试", "mode": mode})
            assert r.status_code == 200, f"mode={mode} 应 200，实际 {r.status_code}"


def test_MCP工具参数校验(env_isolated):
    from kb.service import KBService
    from kb.mcp import (create_mcp_server, write_memory, search_memory,
                        update_memory)
    create_mcp_server(KBService())  # 显式注入，绑定当前隔离环境
    # 空内容拒绝（v2：无 agent_id 入参，身份由环境承载）
    assert write_memory("")["error"] == "INVALID_ARGUMENT"
    assert write_memory("   ")["error"] == "INVALID_ARGUMENT"
    # top_k 下界
    assert search_memory("测试", top_k=0)["error"] == "INVALID_ARGUMENT"
    assert search_memory("测试", top_k=-3)["error"] == "INVALID_ARGUMENT"
    # 非法 client / project 拦截（v2：身份仅 client/project，格式校验生效）
    assert write_memory("MCP校验测试的非法客户端",
                        client="bad!chars")["error"] == "INVALID_ARGUMENT"
    assert write_memory("MCP校验测试的非法项目",
                        project="p/q")["error"] == "INVALID_ARGUMENT"
    # 正常调用不受影响；更新为空内容同样拒绝
    r = write_memory("MCP校验测试的正常记忆")
    assert "id" in r
    assert update_memory(r["id"], "")["error"] == "INVALID_ARGUMENT"


def test_MCP响应头含charset(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    # base_url 用 127.0.0.1：TestClient 默认 Host "testserver" 会被
    # MCP SDK 的 transport_security（DNS rebinding 防护）拒为 421
    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as c:
        r = c.post("/mcp/", json=INIT_REQ,
                   headers={"Accept": "application/json, text/event-stream"})
        assert r.status_code == 200
        ct = r.headers.get("content-type", "")
        assert "text/event-stream" in ct and "charset" in ct.lower(), \
            f"SSE 响应头应含 charset：{ct}"
