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
    """设备优先级全分支可测，不依赖运行测试的机器是否真有显卡。

    旧用例假定 torch.cuda.is_available() 为真；无显卡机器上询问分支被跳过，
    runtime.json 不会写入，测试必失败（第二轮报告 P1）。
    """
    import json
    import sys

    import torch

    from kb.config import get_settings
    from kb.cli import resolve_device

    class _Stdin:
        def __init__(self, tty):
            self._tty = tty

        def isatty(self):
            return self._tty

    # 1. 显式 env 最高优先
    monkeypatch.setenv("KB_DEVICE", "cpu")
    get_settings.cache_clear()
    assert resolve_device(get_settings(), interactive=True) == "cpu"

    # 2. 无 runtime.json、非交互 → cpu，且不写文件
    monkeypatch.delenv("KB_DEVICE")
    get_settings.cache_clear()
    s = get_settings()
    assert not s.runtime_file.exists()
    assert resolve_device(s, interactive=False) == "cpu"
    assert not s.runtime_file.exists()

    # 3. 有 CUDA 但 stdin 非终端（pythonw/一键脚本/CI）→ 静默 cpu，不询问、不写文件
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    monkeypatch.setattr(sys, "stdin", _Stdin(False))

    def forbidden_input(_prompt=""):
        raise AssertionError("非终端 stdin 不应触发交互询问")

    assert resolve_device(get_settings(), interactive=True,
                          input_fn=forbidden_input) == "cpu"
    assert not s.runtime_file.exists()

    # 4. 终端 + 有 CUDA + 用户输入 y → cuda 并持久化
    monkeypatch.setattr(sys, "stdin", _Stdin(True))
    assert resolve_device(get_settings(), interactive=True,
                          input_fn=lambda _: "y") == "cuda"
    assert json.loads(s.runtime_file.read_text(encoding="utf-8"))["device"] == "cuda"

    # 5. 终端 + 有 CUDA + 用户输入 n → cpu 并持久化
    s.runtime_file.unlink()
    assert resolve_device(get_settings(), interactive=True,
                          input_fn=lambda _: "n") == "cpu"
    assert json.loads(s.runtime_file.read_text(encoding="utf-8"))["device"] == "cpu"
