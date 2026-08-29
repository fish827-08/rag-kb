"""N24 reranker 测试：CrossEncoder 精排（A3.5 spec §3.1）。

覆盖：
- Reranker.rerank：打分排序 / rerank_score 字段 / 异常降级原顺序
- retriever 挂接：默认关（零行为变化）/ 开启重排 / top_n 截断 / reranker 异常不中断
- 配置默认值
全部 mock 模型，不加载真实 CrossEncoder 权重。
"""
from unittest.mock import MagicMock

import pytest

from kb.bm25 import BM25Index
from kb.models import Record
from kb.retriever import HybridRetriever


class FakeReranker:
    """测试替身：按预设分数重排；可选抛异常验证降级。"""

    def __init__(self, scores=None, error=False):
        self.scores = scores or {}     # {content关键词: 分数}
        self.error = error
        self.calls = []

    def rerank(self, query, candidates, top_k):
        self.calls.append([c["id"] for c in candidates])
        if self.error:
            raise RuntimeError("CrossEncoder 加载失败")
        for c in candidates:
            c["rerank_score"] = self.scores.get(c["id"], 0.0)
        ranked = sorted(candidates, key=lambda c: c["rerank_score"], reverse=True)
        return ranked[:top_k]


class _FakeStore:
    """内存 store 替身：get_many 批量返回。"""

    def __init__(self, records):
        self._recs = {r.id: r for r in records}

    def get_many(self, ids):
        return {rid: self._recs[rid] for rid in ids if rid in self._recs}

    def get(self, rid):
        return self._recs.get(rid)

    def increment_access(self, ids):
        pass


class _FakeEmbedder:
    """embed_query 返回固定向量（本测试向量路不参与断言）。"""

    def embed_query(self, q):
        return [0.0]


def _mk(rid, content):
    return Record(id=rid, content=content)


def _mk_settings(**kw):
    from kb.config import Settings
    base = dict(decay_enabled=False, freshness_enabled=False,
                rerank_enabled=False, rerank_top_n=20)
    base.update(kw)
    return Settings(**base)


def _retriever(records, settings, reranker=None):
    bm25 = BM25Index()
    bm25.rebuild(records)
    return HybridRetriever(_FakeStore(records), bm25, _FakeEmbedder(),
                           settings=settings, reranker=reranker)


class TestRerankerUnit:
    """Reranker 单元（mock CrossEncoder）。"""

    def test_打分排序与rerank_score字段(self, monkeypatch):
        from kb.reranker import Reranker
        rr = Reranker("fake-model")
        # mock 懒加载与 predict
        fake_model = MagicMock()
        fake_model.predict.return_value = [0.1, 5.0, 2.0]
        monkeypatch.setattr(Reranker, "_ensure_loaded",
                            lambda self: setattr(self, "_model", fake_model))
        cands = [{"id": "a", "content": "x"}, {"id": "b", "content": "y"},
                 {"id": "c", "content": "z"}]
        out = rr.rerank("query", cands, top_k=2)
        assert [c["id"] for c in out] == ["b", "c"]     # 5.0 > 2.0 > 0.1
        assert out[0]["rerank_score"] == pytest.approx(5.0)
        assert len(out) == 2                             # top_k 截断

    def test_异常降级原顺序截断(self, monkeypatch):
        from kb.reranker import Reranker
        rr = Reranker("fake-model")

        def boom(self):
            raise RuntimeError("load failed")

        monkeypatch.setattr(Reranker, "_ensure_loaded", boom)
        cands = [{"id": "a", "content": "x"}, {"id": "b", "content": "y"}]
        out = rr.rerank("query", cands, top_k=1)
        assert [c["id"] for c in out] == ["a"]           # 原顺序截断，不中断

    def test_空候选返回空(self):
        from kb.reranker import Reranker
        assert Reranker("m").rerank("q", [], top_k=5) == []


class TestRetrieverRerank:
    """retriever 挂接。"""

    def test_默认关零行为变化(self):
        recs = [_mk("r1", "向量检索"), _mk("r2", "关键词检索"), _mk("r3", "混合检索")]
        s_off = _mk_settings(rerank_enabled=False)
        fake = FakeReranker()
        r = _retriever(recs, s_off, reranker=fake)
        hits = r.search("检索", top_k=2, mode="keyword")
        assert len(hits) == 2
        assert fake.calls == []                          # reranker 未被调用

    def test_开启后按rerank_score重排(self):
        recs = [_mk("r1", "向量检索"), _mk("r2", "关键词检索"), _mk("r3", "混合检索")]
        s_on = _mk_settings(rerank_enabled=True)
        # r3 给最高分 → r3 排第一
        fake = FakeReranker(scores={"r1": 0.1, "r2": 1.0, "r3": 9.0})
        r = _retriever(recs, s_on, reranker=fake)
        hits = r.search("检索", top_k=2, mode="keyword")
        assert hits[0]["id"] == "r3"
        assert hits[0]["score"] == pytest.approx(9.0)    # score 即 rerank_score
        assert len(hits) == 2

    def test_top_n限制参与精排候选数(self):
        recs = [_mk(f"r{i}", f"内容{i}检索") for i in range(30)]
        s_on = _mk_settings(rerank_enabled=True, rerank_top_n=5)
        fake = FakeReranker(scores={f"r{i}": float(i) for i in range(30)})
        r = _retriever(recs, s_on, reranker=fake)
        r.search("检索", top_k=3, mode="keyword")
        assert len(fake.calls[0]) == 5                   # 只精排前 5 候选

    def test_reranker异常不中断检索(self):
        recs = [_mk("r1", "向量检索"), _mk("r2", "关键词检索")]
        s_on = _mk_settings(rerank_enabled=True)
        fake = FakeReranker(error=True)
        r = _retriever(recs, s_on, reranker=fake)
        hits = r.search("检索", top_k=2, mode="keyword")  # 不抛异常
        assert len(hits) == 2                             # 原顺序返回

    def test_未注入reranker等同关闭(self):
        recs = [_mk("r1", "向量检索")]
        s_on = _mk_settings(rerank_enabled=True)
        r = _retriever(recs, s_on, reranker=None)         # 开关开但实例 None
        hits = r.search("检索", top_k=1, mode="keyword")
        assert len(hits) == 1


class TestConfig:
    """配置默认值。"""

    def test_默认全关(self):
        from kb.config import Settings
        s = Settings()
        assert s.rerank_enabled is False
        assert s.rerank_model == "BAAI/bge-reranker-v2-m3"
        assert s.rerank_top_n == 20
