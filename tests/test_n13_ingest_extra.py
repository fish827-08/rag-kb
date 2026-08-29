"""N13 补充测试（开发 AI 补充，非节点计划验收测试）。

覆盖：multipart 上传、API 400 错误路径、markitdown 兜底分发、MCP add_document 接通、空文档边界。

说明：markitdown 的真实 import 链在当前沙箱会触发系统 pyc 写入限制，
故 markitdown 兜底用 fake 模块验证分发逻辑（真实转换已人工验证 csv → markdown 表格）。
"""
import sys
import types

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        yield c


def test_multipart上传与删除级联(client):
    content = "第二章：向量与关键词的融合检索。" * 20
    r = client.post("/api/v1/documents",
                    files={"file": ("upload.txt", content.encode("utf-8"),
                                    "text/plain")})
    assert r.status_code == 200
    body = r.json()
    # source 用上传文件名（而非临时文件名），删除级联按该 source 生效
    assert body["source"] == "upload.txt" and body["chunks"] >= 1
    hits = client.post("/api/v1/search",
                       json={"query": "融合检索", "mode": "keyword"}).json()["results"]
    assert hits
    assert client.delete("/api/v1/documents/upload.txt").json()["deleted"] >= 1
    hits2 = client.post("/api/v1/search",
                        json={"query": "融合检索", "mode": "keyword"}).json()["results"]
    assert hits2 == []


def test_multipart不支持格式400(client):
    r = client.post("/api/v1/documents",
                    files={"file": ("x.exe", b"MZ...", "application/octet-stream")})
    assert r.status_code == 400
    assert r.json()["error"] == "UNSUPPORTED_FORMAT"


def test_JSON路径错误400(client, env_isolated):
    # 不支持的格式 → 400
    exe = env_isolated / "y.exe"
    exe.write_bytes(b"MZ...")
    r = client.post("/api/v1/documents", json={"path": str(exe)})
    assert r.status_code == 400 and r.json()["error"] == "UNSUPPORTED_FORMAT"
    # 文件不存在 → 400
    r = client.post("/api/v1/documents",
                    json={"path": str(env_isolated / "none.txt")})
    assert r.status_code == 400 and r.json()["error"] == "FILE_NOT_FOUND"
    # 缺参 → 400
    r = client.post("/api/v1/documents", json={})
    assert r.status_code == 400


def test_markitdown兜底分发(env_isolated, monkeypatch):
    fake = types.ModuleType("markitdown")

    class _FakeMarkItDown:
        def convert(self, path):
            return types.SimpleNamespace(text_content="fake表格内容")

    fake.MarkItDown = _FakeMarkItDown
    monkeypatch.setitem(sys.modules, "markitdown", fake)
    from kb.ingest import parse_file
    csv = env_isolated / "t.csv"
    csv.write_text("a,b\n1,2\n", encoding="utf-8")
    xlsx = env_isolated / "t.xlsx"
    xlsx.write_bytes(b"PK...")
    assert parse_file(csv) == "fake表格内容"
    assert parse_file(xlsx) == "fake表格内容"


def test_MCP_add_document接通(env_isolated):
    from kb.mcp import add_document, create_mcp_server
    from kb.service import KBService
    svc = KBService()
    create_mcp_server(svc)
    f = env_isolated / "mcp.txt"
    f.write_text("第三章：MCP 文档导入通道。" * 20, encoding="utf-8")
    r = add_document(str(f))  # v2：无 agent_id 入参
    assert r["source"] == "mcp.txt" and r["chunks"] >= 1
    assert svc.search("MCP 文档导入", mode="keyword")
    # 错误路径：文件不存在 / 格式不支持
    assert add_document(str(env_isolated / "none.txt"))["error"] == "FILE_NOT_FOUND"
    exe = env_isolated / "z.exe"
    exe.write_bytes(b"MZ...")
    assert add_document(str(exe))["error"] == "UNSUPPORTED_FORMAT"


def test_空文档返回0块(env_isolated):
    from kb.service import KBService
    f = env_isolated / "empty.txt"
    f.write_text("", encoding="utf-8")
    svc = KBService()
    assert svc.add_document(f) == {"source": "empty.txt", "chunks": 0}
    _, total = svc.list_memories(type="doc_chunk")
    assert total == 0
