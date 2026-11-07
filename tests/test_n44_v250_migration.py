"""v2.5 嵌入模型迁移（只读窗口）：每模型独立 collection + 镜像同步 + 切换/回滚。

English: v2.5 embedding-model migration (read-only window): per-model collections,
mirror sync, switch/rollback.
"""
import threading
import time

import pytest

from kb.embedder import Embedder
from kb.models import Record
from kb.service import MigrationBusyError

NEW_MODEL = "BAAI/bge-base-zh-v1.5"
OLD_MODEL = "BAAI/bge-small-zh-v1.5"


@pytest.fixture(autouse=True)
def fake_embed(monkeypatch):
    """不加载真实模型：按模型维度返回确定性向量。"""
    monkeypatch.setattr(
        Embedder, "embed_texts",
        lambda self, texts: [[0.1] * self.dimension for _ in texts])


def _seed(svc, n=3, project=None):
    """直接写库（绕过写门禁），并同步计数。"""
    scope = svc.scopes.get(project)
    records = [Record(content=f"记录 {i}", source="seed.txt") for i in range(n)]
    scope.store.add(records, [[0.1] * svc.embedder.dimension] * n)
    svc.scopes.sync_count(scope)
    return records


def _wait(svc, timeout=15.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        info = svc.migration_info()
        if info["status"] != "running":
            return info
        time.sleep(0.05)
    raise AssertionError("迁移超时")


def test_迁移成功_数据与模型都切换(env_isolated):
    from kb.service import KBService

    svc = KBService()
    _seed(svc, 3)
    assert svc.embed_info()["migration_required"] is True

    svc.start_migration(NEW_MODEL)
    info = _wait(svc)
    assert info["status"] == "done", info
    assert info["done"] == 3
    assert svc.settings.embed_model == NEW_MODEL
    assert svc.embed_info()["dimension"] == 768
    assert svc.store.count() == 3
    # 旧模型数据保留，可在设置面板里手动删除
    models = {m["model"]: m for m in svc.embed_info()["models"]}
    assert models[OLD_MODEL]["records"] == 3
    assert models[NEW_MODEL]["records"] == 3
    assert models[NEW_MODEL]["active"] is True


def test_迁移后重启仍用新模型且数据完整(env_isolated):
    from kb.service import KBService

    svc = KBService()
    _seed(svc, 2)
    svc.start_migration(NEW_MODEL)
    assert _wait(svc)["status"] == "done"

    restarted = KBService()
    assert restarted.settings.embed_model == NEW_MODEL
    assert restarted.store.count() == 2
    assert restarted.embed_info()["dimension"] == 768


def test_迁移期间写入被拒_读取降级关键词(env_isolated, monkeypatch):
    from kb.service import KBService

    svc = KBService()
    _seed(svc, 2)
    gate = threading.Event()
    entered = threading.Event()

    def slow_batch(dst, records, embedder):
        entered.set()
        gate.wait(timeout=10)
        return original(dst, records, embedder)

    original = svc._migrate_batch
    monkeypatch.setattr(svc, "_migrate_batch", slow_batch)
    seen_mode = {}
    monkeypatch.setattr(
        svc.retriever, "search",
        lambda query, **kw: seen_mode.update(mode=kw.get("mode")) or [])

    svc.start_migration(NEW_MODEL)
    assert entered.wait(timeout=10), "迁移线程未进入批次写入"
    with pytest.raises(MigrationBusyError):
        svc.add_memory("迁移期间的新记忆")
    svc.search("任意查询")
    assert seen_mode["mode"] == "keyword"
    gate.set()
    assert _wait(svc)["status"] == "done"


def test_迁移失败保留旧库_可重试并清理残留(env_isolated, monkeypatch):
    from kb.scopes import collection_for
    from kb.service import KBService
    from kb.storage import ChromaStore

    svc = KBService()
    _seed(svc, 3)
    original = svc._migrate_batch

    def boom(dst, records, embedder):
        raise RuntimeError("模拟显存不足")

    monkeypatch.setattr(svc, "_migrate_batch", boom)
    svc.start_migration(NEW_MODEL)
    info = _wait(svc)
    assert info["status"] == "failed"
    assert "模拟显存不足" in info["error"]
    # 旧模型与旧数据保持可用
    assert svc.settings.embed_model == OLD_MODEL
    assert svc.store.count() == 3

    # 目标 collection 里塞一条残留记录，重试时应被镜像删除
    target = ChromaStore(
        svc.settings.chroma_dir,
        collection_for(svc.settings.data_dir, NEW_MODEL))
    target.add([Record(content="上次失败的残留")], [[0.1] * 768])

    monkeypatch.setattr(svc, "_migrate_batch", original)
    svc.start_migration(NEW_MODEL)
    assert _wait(svc)["status"] == "done"
    fresh = ChromaStore(
        svc.settings.chroma_dir,
        collection_for(svc.settings.data_dir, NEW_MODEL))
    assert fresh.count() == 3


def test_项目子库一起迁移(env_isolated):
    from kb.service import KBService

    svc = KBService()
    _seed(svc, 1, project="proj")
    _seed(svc, 2)
    svc.start_migration(NEW_MODEL)
    assert _wait(svc)["status"] == "done"
    assert svc.scopes.get("proj").store.count() == 1
    assert svc.store.count() == 2
    assert svc.scopes.get("proj").model == NEW_MODEL


def test_删除非活跃模型数据(env_isolated):
    from kb.service import KBService

    svc = KBService()
    _seed(svc, 2)
    svc.start_migration(NEW_MODEL)
    assert _wait(svc)["status"] == "done"

    with pytest.raises(ValueError, match="正在使用"):
        svc.delete_model_data(NEW_MODEL)
    assert svc.delete_model_data(OLD_MODEL) == 2
    models = [m["model"] for m in svc.embed_info()["models"]]
    assert models == [NEW_MODEL]
    assert svc.store.count() == 2


def test_空库切换模型不进入只读窗口(env_isolated):
    from kb.service import KBService

    svc = KBService()
    svc.start_migration(NEW_MODEL)
    assert svc.migration_info()["status"] == "idle"
    assert svc.settings.embed_model == NEW_MODEL
    assert svc.embed_info()["models"][0]["records"] == 0


def test_迁移接口状态查询(env_isolated):
    from fastapi.testclient import TestClient

    from kb.api import create_app

    with TestClient(create_app(), base_url="http://127.0.0.1:8000") as c:
        status = c.get("/api/v1/embed/migrate").json()
        assert status["status"] == "idle"
        resp = c.post("/api/v1/embed/migrate", json={"model": ""})
        assert resp.status_code == 422
