import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        c.post("/api/v1/memories", json={"content": "检索端点测试记忆"})
        yield c


def test_search三种模式(client):
    for mode in ("hybrid", "vector", "keyword"):
        r = client.post("/api/v1/search", json={"query": "检索端点", "mode": mode})
        assert r.status_code == 200
        assert any("检索端点" in h["content"] for h in r.json()["results"])


def test_文档列表与删除(client):
    # 先造带 source 的数据（绕过 ingest，直接走 memories 带 source）
    client.post("/api/v1/memories",
                json={"content": "文档块一", "source": "doc_a.txt"})
    client.post("/api/v1/memories",
                json={"content": "文档块二", "source": "doc_a.txt"})
    docs = client.get("/api/v1/documents").json()["items"]
    target = [d for d in docs if d["source"] == "doc_a.txt"]
    assert target and target[0]["chunks"] == 2

    r = client.delete("/api/v1/documents/doc_a.txt")
    assert r.json()["deleted"] == 2
    assert client.get("/api/v1/documents").json()["items"] == []


def test_设备检测决策(env_isolated, monkeypatch):
    from kb.config import get_settings
    from kb.cli import resolve_device
    s = get_settings()
    # 1. 显式 env 最高优先
    monkeypatch.setenv("KB_DEVICE", "cpu"); get_settings.cache_clear()
    from kb.config import get_settings as gs
    assert resolve_device(gs(), interactive=True) == "cpu"
    # 2. 无 runtime.json、非交互 → cpu
    monkeypatch.delenv("KB_DEVICE"); get_settings.cache_clear()
    assert resolve_device(gs(), interactive=False) == "cpu"
    # 3. 交互输入 y → cuda 或 cpu（取决于显卡），且持久化
    choice = resolve_device(gs(), interactive=True, input_fn=lambda _: "y")
    assert choice in ("cuda", "cpu")
    assert (gs().runtime_file).exists()
