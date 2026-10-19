"""用户视角体验修复验收（第三轮）。

覆盖第二轮报告中的 P1：
- 空 search/ask 不再返回全部记录，而是 422；
- 网页等含斜杠的 source 可通过查询参数删除；
- 同一 source 重复入库替换旧记录，不产生重复 chunk；
- 嵌入模型不可用返回结构化 503（不再裸 500），healthz 暴露 embedder 状态；
- MCP 通道同样返回结构化错误；
- `kb eval` 隔离用户 dedup/decay/freshness 配置。
"""
import json

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        yield c


def test_空查询返回422(client):
    r = client.post("/api/v1/search", json={"query": "   "})
    assert r.status_code == 422
    r = client.post("/api/v1/ask", json={"question": "   "})
    assert r.status_code == 422


def test_网页来源可按查询参数删除(client):
    """URL 含斜杠，无法放进 path 参数；必须支持 ?source= 形式。"""
    url = "https://example.com/some/page"
    client.post("/api/v1/memories", json={"content": "网页正文块", "source": url})
    assert any(d["source"] == url
               for d in client.get("/api/v1/documents").json()["items"])

    r = client.delete("/api/v1/documents", params={"source": url})
    assert r.status_code == 200
    assert r.json()["deleted"] == 1
    assert all(d["source"] != url
               for d in client.get("/api/v1/documents").json()["items"])


def test_同一来源重复入库替换而非重复(client, env_isolated):
    f = env_isolated / "重复入库.txt"
    f.write_text("重复入库的正文内容。" * 60, encoding="utf-8")

    first = client.post("/api/v1/documents", json={"path": str(f)}).json()
    assert first["chunks"] >= 1 and first["replaced"] == 0

    second = client.post("/api/v1/documents", json={"path": str(f)}).json()
    assert second["replaced"] == first["chunks"]
    docs = [d for d in client.get("/api/v1/documents").json()["items"]
            if d["source"] == first["source"]]
    assert len(docs) == 1 and docs[0]["chunks"] == second["chunks"]


def test_重复入库嵌入失败不破坏旧数据(env_isolated, monkeypatch):
    from kb.config import get_settings
    from kb.embedder import EmbeddingUnavailableError
    from kb.service import KBService

    svc = KBService(get_settings())
    f = env_isolated / "保留原文.txt"
    f.write_text("原始正文。" * 50, encoding="utf-8")
    svc.add_document(str(f))
    before = len(list(svc.store.iter_all()))
    assert before >= 1

    def boom(_texts):
        raise EmbeddingUnavailableError(svc.settings.embed_model, "simulated failure")

    monkeypatch.setattr(svc.embedder, "embed_texts", boom)
    with pytest.raises(EmbeddingUnavailableError):
        svc.add_document(str(f))
    assert len(list(svc.store.iter_all())) == before, "嵌入失败时旧记录必须原样保留"


def test_嵌入模型不可用返回结构化503(env_isolated, monkeypatch):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.embedder import Embedder, EmbeddingUnavailableError

    def boom(_self, _texts):
        raise EmbeddingUnavailableError("bad/model", "download failed")

    monkeypatch.setattr(Embedder, "embed_texts", boom)
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/memories", json={"content": "会触发嵌入的写入"})
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "EMBED_MODEL_UNAVAILABLE"
    assert body["model"] == "bad/model"
    assert body["hint"]


def test_healthz暴露嵌入器状态(env_isolated, monkeypatch):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.embedder import Embedder

    monkeypatch.setattr(Embedder, "status", lambda _self: {
        "state": "error", "model": "bad/model", "error": "simulated"})
    with TestClient(create_app()) as c:
        body = c.get("/api/v1/healthz").json()
    assert body["embedder"]["state"] == "error"
    assert body["embedder"]["error"] == "simulated"


def test_mcp通道空查询与模型不可用返回结构化错误(env_isolated, monkeypatch):
    import kb.mcp as mcp_mod
    from kb.embedder import EmbeddingUnavailableError

    assert mcp_mod.search_memory("   ")["error"] == "INVALID_ARGUMENT"

    class FakeSvc:
        def add_memory(self, *_args, **_kwargs):
            raise EmbeddingUnavailableError("bad/model", "boom")

    monkeypatch.setattr(mcp_mod, "_svc", lambda: FakeSvc())
    out = mcp_mod.write_memory("hello")
    assert out["error"] == "EMBED_MODEL_UNAVAILABLE"
    assert out["model"] == "bad/model"


def test_eval隔离用户治理配置(env_isolated, monkeypatch):
    """用户 .env 开启 dedup 等治理后，评测仍必须可跑（写入评测语料不能被拦截）。"""
    from typer.testing import CliRunner
    from kb.cli import app

    monkeypatch.setenv("KB_DEDUP_ENABLED", "true")
    monkeypatch.setenv("KB_DECAY_ENABLED", "true")
    monkeypatch.setenv("KB_FRESHNESS_ENABLED", "true")

    dataset = env_isolated / "eval_mini.jsonl"
    rows = [
        {"qid": "q1", "question": "界面颜色偏好",
         "corpus": "用户偏好深色主题。", "tags": ["偏好"]},
        {"qid": "q2", "question": "本地记忆服务",
         "corpus": "kb 是本地优先的记忆与知识服务。", "tags": ["介绍"]},
    ]
    dataset.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8")

    result = CliRunner().invoke(
        app, ["eval", "--file", str(dataset), "--top-k", "1"])
    assert result.exit_code == 0, result.output
    assert "Recall@1" in result.output


def test_mcp_server版本号与包版本一致(env_isolated):
    """MCP initialize 的 serverInfo.version 必须可识别（客户端用于版本诊断）。"""
    from kb import __version__
    from kb.mcp import create_mcp_server
    from kb.service import KBService
    assert create_mcp_server(KBService()).version == __version__
