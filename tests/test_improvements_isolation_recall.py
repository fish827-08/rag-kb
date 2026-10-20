"""记忆范围 v2.1 检索验证：项目子库物理隔离，项目内记忆与知识同池。

核心场景：带 project 的检索只覆盖该项目子库 + 全局库；不同项目互不可见。
BM25 在单个作用域内不做归属过滤（隔离由 ScopeManager 按库完成）。
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def svc(env_isolated):
    from kb.config import get_settings
    from kb.service import KBService
    get_settings.cache_clear()
    return KBService()


def test_三种模式_项目记忆互不可见(svc):
    a = svc.add_memory("联合预算机密 甲专属标记", client="client-a", project="proj-a")
    b_ids = [
        svc.add_memory(f"联合预算机密 乙填充内容 {i}",
                       client="client-b", project="proj-b").id
        for i in range(25)
    ]
    for mode in ("keyword", "vector", "hybrid"):
        ids_a = [r["id"] for r in svc.search(
            "联合预算机密", top_k=10, mode=mode, project="proj-a")]
        ids_b = [r["id"] for r in svc.search(
            "联合预算机密", top_k=10, mode=mode, project="proj-b")]
        assert a.id in ids_a, f"mode={mode}：项目 A 应召回自己的记忆"
        assert all(x not in ids_a for x in b_ids), \
            f"mode={mode}：项目 A 不应召回项目 B 的记忆"
        assert a.id not in ids_b, f"mode={mode}：项目 B 不应召回项目 A 的记忆"


def test_bm25_全量检索不隔离():
    from kb.bm25 import BM25Index
    from kb.models import Record, RecordType
    idx = BM25Index()
    mine = Record(content="私有预算一百", client="c-a", project="p-a")
    other = Record(content="私有预算二百", client="c-b", project="p-b")
    shared = Record(content="共享预算三百", client="c-b", project="p-b",
                    type=RecordType.DOC_CHUNK)
    idx.add_many([mine, other, shared])

    # 单作用域内 BM25 全量检索（默认 filter_fn=None）：memory 与 doc 均命中
    ids = [h[0] for h in idx.search("预算", top_n=10)]
    assert mine.id in ids
    assert other.id in ids
    assert shared.id in ids
    assert idx.meta_of("不存在") is None