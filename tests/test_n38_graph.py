"""v2.4 知识图谱接口（GET /api/v1/graph）与 RAG 内容分离。

覆盖：
1) memory 节点与关联边返回；
2) RAG 文档块不进入图谱；
3) 项目参数合并项目库与全局库；
4) limit 截断标记。
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        yield c


def _new_memory(client, content, **extra):
    body = {"content": content}
    body.update(extra)
    r = client.post("/api/v1/memories", json=body)
    assert r.status_code == 200
    return r.json()["id"]


def test_图谱返回节点与边(client):
    a = _new_memory(client, "记忆 A：项目使用 FastAPI")
    b = _new_memory(client, "记忆 B：检索使用向量与 BM25")
    r = client.post(f"/api/v1/memories/{a}/links",
                    json={"to_id": b, "relation": "related"})
    assert r.status_code == 200

    g = client.get("/api/v1/graph").json()
    ids = {n["id"] for n in g["nodes"]}
    assert a in ids and b in ids
    assert all(n["type"] == "memory" for n in g["nodes"])
    assert any(e["from"] == a and e["to"] == b for e in g["edges"])
    assert g["truncated"] is False


def test_图谱不含文档块(client, tmp_path):
    doc = tmp_path / "资料.txt"
    doc.write_text("这是一份 RAG 文档，不应出现在记忆图谱里。", encoding="utf-8")
    r = client.post("/api/v1/documents", json={"path": str(doc)})
    assert r.status_code == 200
    _new_memory(client, "唯一的记忆节点")

    g = client.get("/api/v1/graph").json()
    assert len(g["nodes"]) == 1
    assert g["nodes"][0]["label"].startswith("唯一的记忆节点")


def test_图谱按项目合并全局库(client):
    _new_memory(client, "全局记忆")
    _new_memory(client, "项目记忆", project="proj-graph")
    g = client.get("/api/v1/graph", params={"project": "proj-graph"}).json()
    assert len(g["nodes"]) == 2


def test_limit_截断标记(client):
    for i in range(3):
        _new_memory(client, f"记忆 {i}")
    g = client.get("/api/v1/graph", params={"limit": 2}).json()
    assert len(g["nodes"]) == 2
    assert g["truncated"] is True


def test_非法项目名422(client):
    assert client.get("/api/v1/graph",
                      params={"project": "bad/name"}).status_code == 422
