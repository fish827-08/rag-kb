"""改进项 3 验证：检索隔离下推到 ChromaDB where 与 BM25 排序前过滤。

核心场景：大量其他客户端的相似记忆不再挤占候选池——下推前 candidate=3*top_k
（top_k=5 → 15）会被 25 条他人记忆占满，过滤后自己的记忆消失；下推后
向量路与 BM25 路在取候选阶段就只剩自己的 memory + 共享 doc/web。
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def svc(env_isolated):
    from kb.config import get_settings
    from kb.service import KBService
    get_settings.cache_clear()
    return KBService()


def test_三种模式_隔离下推_不被他人记忆挤掉(svc):
    a = svc.add_memory("联合预算机密 甲专属标记", client="client-a", project="proj-a")
    b_ids = [
        svc.add_memory(f"联合预算机密 乙填充内容 {i}",
                       client="client-b", project="proj-b").id
        for i in range(25)
    ]
    for mode in ("keyword", "vector", "hybrid"):
        hits = svc.search("联合预算机密", top_k=5, mode=mode,
                          client="client-a", project="proj-a")
        ids = [r["id"] for r in hits]
        assert a.id in ids, f"mode={mode}：自己的记忆被他人候选挤掉"
        assert not any(bid in ids for bid in b_ids), f"mode={mode}：泄露他人记忆"


def test_bm25_filter_fn_隔离过滤():
    from kb.bm25 import BM25Index
    from kb.models import Record, RecordType
    idx = BM25Index()
    mine = Record(content="私有预算一百", client="c-a", project="p-a")
    other = Record(content="私有预算二百", client="c-b", project="p-b")
    shared = Record(content="共享预算三百", client="c-b", project="p-b",
                    type=RecordType.DOC_CHUNK)
    idx.add_many([mine, other, shared])

    def visible(rid: str) -> bool:
        meta = idx.meta_of(rid)
        if meta is None:
            return True
        c, p, t = meta
        if t != RecordType.MEMORY.value:
            return True  # doc/web 共享
        return c == "c-a" and p == "p-a"

    ids = [h[0] for h in idx.search("预算", top_n=10, filter_fn=visible)]
    assert mine.id in ids
    assert other.id not in ids   # 他人 memory 在排序前被剔除
    assert shared.id in ids      # 共享 doc 不被隔离
    assert idx.meta_of("不存在") is None
