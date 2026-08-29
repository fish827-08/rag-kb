"""N27 性能修复测试：store.get_many 批量读取 + BM25 语料持久化（A3.5 spec §3.4）。

覆盖：
- get_many：存在/不存在混合 id、空列表
- BM25Index.save_corpus / load_corpus：落盘往返、id 集合校验、文件缺失/损坏降级
- service 启动持久化联动：第二次启动免全量分词重建
"""
import json
from pathlib import Path

import pytest

from kb.bm25 import BM25Index
from kb.models import Record


def _mk_record(rid: str, content: str) -> Record:
    return Record(id=rid, content=content)


class TestGetMany:
    """ChromaStore.get_many：批量读取。"""

    def test_混合存在与不存在id(self, env_isolated):
        from kb.storage import ChromaStore
        store = ChromaStore(env_isolated / "chroma")
        r1, r2 = _mk_record("a1", "向量检索"), _mk_record("a2", "关键词检索")
        store.add([r1, r2], [[0.1, 0.2], [0.3, 0.4]])
        out = store.get_many(["a1", "a2", "nope"])
        assert set(out.keys()) == {"a1", "a2"}
        assert out["a1"].content == "向量检索"
        assert out["a2"].content == "关键词检索"

    def test_空列表返回空dict(self, env_isolated):
        from kb.storage import ChromaStore
        store = ChromaStore(env_isolated / "chroma")
        assert store.get_many([]) == {}

    def test_全部不存在返回空dict(self, env_isolated):
        from kb.storage import ChromaStore
        store = ChromaStore(env_isolated / "chroma")
        assert store.get_many(["x1", "x2"]) == {}


class TestBM25CorpusPersistence:
    """BM25Index 语料持久化。"""

    def test_落盘往返检索一致(self, tmp_path):
        idx = BM25Index()
        idx.rebuild([_mk_record("r1", "余弦相似度向量检索"),
                     _mk_record("r2", "BM25 关键词打分")])
        cache = tmp_path / "bm25_corpus.json"
        idx.save_corpus(cache)
        assert cache.exists()
        # 新实例加载后检索行为一致
        idx2 = BM25Index()
        ok = idx2.load_corpus(cache, valid_ids=["r1", "r2"])
        assert ok is True
        assert idx2.search("向量检索", top_n=2) == idx.search("向量检索", top_n=2)
        assert idx2.search("关键词", top_n=2) == idx.search("关键词", top_n=2)

    def test_id集合不一致返回False(self, tmp_path):
        idx = BM25Index()
        idx.rebuild([_mk_record("r1", "内容一")])
        cache = tmp_path / "bm25_corpus.json"
        idx.save_corpus(cache)
        idx2 = BM25Index()
        # 库里多了 r2（漂移）→ 缓存不可信
        assert idx2.load_corpus(cache, valid_ids=["r1", "r2"]) is False
        # 库里少了 r1（漂移）→ 同样不可信
        assert idx2.load_corpus(cache, valid_ids=[]) is False

    def test_文件缺失返回False(self, tmp_path):
        idx = BM25Index()
        assert idx.load_corpus(tmp_path / "nope.json", valid_ids=["r1"]) is False

    def test_文件损坏按未命中处理(self, tmp_path):
        cache = tmp_path / "bm25_corpus.json"
        cache.write_text("{broken json", encoding="utf-8")
        idx = BM25Index()
        assert idx.load_corpus(cache, valid_ids=["r1"]) is False


class TestServicePersistence:
    """service 启动优先加载持久化语料，漂移/缺失时全量重建并回写。"""

    def test_第二次启动免全量分词重建(self, env_isolated, monkeypatch):
        from kb import service as service_mod
        from kb.config import Settings

        # 第一次启动：全量重建 + 落盘
        calls = {"rebuild": 0}
        orig_rebuild = BM25Index.rebuild

        def counting_rebuild(self, records):
            calls["rebuild"] += 1
            return orig_rebuild(self, records)

        monkeypatch.setattr(BM25Index, "rebuild", counting_rebuild)
        s1 = service_mod.KBService(Settings())
        assert calls["rebuild"] == 1
        s1.add_memory("余弦相似度检索测试", tags=[])
        cache = Path(s1.settings.data_dir) / "bm25_corpus.json"
        assert cache.exists()

        # 第二次启动（同 data_dir）：语料命中缓存，rebuild 不被调用
        calls["rebuild"] = 0
        s2 = service_mod.KBService(Settings())
        assert calls["rebuild"] == 0
        # 检索仍命中（语料来自持久化加载）
        hits = s2.search("余弦相似度", top_k=3, mode="keyword")
        assert any(h["content"] == "余弦相似度检索测试" for h in hits)

    def test_写操作同步回写缓存(self, env_isolated):
        from kb.config import Settings
        from kb.service import KBService
        s = KBService(Settings())
        cache = Path(s.settings.data_dir) / "bm25_corpus.json"
        rec = s.add_memory("甲内容", tags=[])
        data = json.loads(cache.read_text(encoding="utf-8"))
        assert rec.id in data                       # 新增：id 进入缓存
        # 删除后缓存同步收缩
        s.delete_memory(rec.id)
        data = json.loads(cache.read_text(encoding="utf-8"))
        assert rec.id not in data
