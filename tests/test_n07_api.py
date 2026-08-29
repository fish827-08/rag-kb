import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        yield c


def test_记忆CRUD全流程(client):
    r = client.post("/api/v1/memories",
                    json={"content": "API写入的记忆", "tags": ["t"]})
    assert r.status_code == 200
    rid = r.json()["id"]

    assert client.get(f"/api/v1/memories/{rid}").json()["content"] == "API写入的记忆"

    r = client.get("/api/v1/memories", params={"q": "API写入"})
    assert r.json()["total"] == 1 and r.json()["items"][0]["id"] == rid

    r = client.patch(f"/api/v1/memories/{rid}", json={"content": "API修改后的记忆"})
    assert r.json()["content"] == "API修改后的记忆"

    assert client.delete(f"/api/v1/memories/{rid}").status_code == 200
    assert client.get(f"/api/v1/memories/{rid}").status_code == 404


def test_错误路径(client):
    assert client.get("/api/v1/memories/不存在").status_code == 404
    assert client.delete("/api/v1/memories/不存在").json()["error"] == "NOT_FOUND"
    r = client.post("/api/v1/memories", json={})
    assert r.status_code == 422  # content 必填


def test_健康检查(client):
    r = client.get("/api/v1/healthz").json()
    assert r["status"] == "ok" and r["records"] == 0
    assert set(r) == {"status", "llm", "device", "records"}