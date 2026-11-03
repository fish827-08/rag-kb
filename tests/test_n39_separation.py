"""v2.4 RAG 与记忆分离：衰减/新鲜度只作用于 memory，forget 默认 memory-only。

设计：docs/superpowers/specs/2026-11-03-kb-v2.4-design.md §4。
"""
from datetime import datetime, timedelta

import pytest

from kb.models import Record, RecordType


class _FakeStore:
    """mock Store：按插入顺序返回固定向量分。"""

    def __init__(self, records):
        self._records = {r.id: r for r in records}

    def query(self, vec, top_k=10, where=None):
        return [(r, 0.9 - i * 0.1)
                for i, r in enumerate(self._records.values())][:top_k]

    def increment_access(self, record_ids):
        pass

    def get(self, rid):
        return self._records.get(rid)

    def get_many(self, ids):
        return {rid: r for rid in ids if (r := self._records.get(rid)) is not None}

    def iter_all(self):
        return iter(self._records.values())


class _FakeEmbedder:
    def embed_query(self, query):
        return [0.1, 0.2, 0.3]


class _FakeBM25:
    def search(self, query, top_n=10, filter_fn=None):
        return []

    def meta_of(self, record_id):
        return None


class _Settings:
    def __init__(self, decay_enabled=True, freshness_enabled=True):
        self.decay_enabled = decay_enabled
        self.decay_lambda = 0.02
        self.decay_gamma = 0.3
        self.freshness_enabled = freshness_enabled
        self.freshness_beta = 0.05
        self.freshness_alpha = 0.3


def _retriever(records, **kwargs):
    from kb.retriever import HybridRetriever
    return HybridRetriever(_FakeStore(records), _FakeBM25(), _FakeEmbedder(),
                           settings=_Settings(**kwargs))


def _record(rid, rtype, days=90):
    old = (datetime.now() - timedelta(days=days)).isoformat()
    return Record(id=rid, content=f"内容 {rid}", type=rtype,
                  created_at=old, updated_at=old)


def test_衰减开启_文档块分数不变():
    """decay_enabled 时 doc_chunk 不乘衰减因子（分数与原始一致）。"""
    records = [_record("m", RecordType.MEMORY), _record("d", RecordType.DOC_CHUNK)]
    hits = _retriever(records).search("test", top_k=2, mode="vector")
    by_id = {h["id"]: h["score"] for h in hits}
    assert by_id["d"] == pytest.approx(0.8)   # 文档原始分 0.8，不衰减
    assert by_id["m"] < 0.2                    # 记忆 90 天衰减


def test_新鲜度开启_文档块不降权():
    """freshness_enabled 时 doc_chunk 分数不受 updated_at 影响。"""
    records = [_record("d", RecordType.DOC_CHUNK, days=200)]
    hits = _retriever(records, decay_enabled=False).search(
        "test", top_k=1, mode="vector")
    assert hits[0]["score"] == pytest.approx(0.9)


class _FakeScope:
    def __init__(self, records):
        self.store = _FakeStore(records)
        self.links = None


class _FakeScopes:
    def __init__(self, scope):
        self._scope = scope

    def get(self, name=None):
        return self._scope

    def project_names(self):
        return []

    def has_project(self, name):
        return False


class _FakeService:
    def __init__(self, records):
        self.scopes = _FakeScopes(_FakeScope(records))


def test_forget默认只清理memory():
    """collect_forget_candidates 默认不纳入文档/网页块。"""
    from kb.cli import collect_forget_candidates
    records = [_record("m", RecordType.MEMORY, days=60),
               _record("d", RecordType.DOC_CHUNK, days=60),
               _record("w", RecordType.WEB_CHUNK, days=60)]
    svc = _FakeService(records)
    candidates, stats = collect_forget_candidates(svc, days=30)
    assert [c.record.id for c in candidates] == ["m"]
    assert stats["documents_skipped"] == 2
