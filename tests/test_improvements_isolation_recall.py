"""记忆范围 v3 检索验证：记忆与知识全共享，候选池全局。

核心场景：v3（2026-08-31）移除 (client, project) 隔离后，所有 memory 与
doc/web 同池检索——自己的记忆与"他人"的记忆都能被任何客户端召回；
Chroma 全量单查 + BM25 全量检索（默认无 filter_fn）。
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def svc(env_isolated):
    from kb.config import get_settings
    from kb.service import KBService
    get_settings.cache_clear()
    return KBService()


def test_三种模式_记忆全共享同池检索(svc):
    a = svc.add_memory("联合预算机密 甲专属标记", client="client-a", project="proj-a")
    b_ids = [
        svc.add_memory(f"联合预算机密 乙填充内容 {i}",
                       client="client-b", project="proj-b").id
        for i in range(25)
    ]
    for mode in ("keyword", "vector", "hybrid"):
        # 任意 client/project 视角结果完全一致（隔离过滤已移除）
        hits_a = svc.search("联合预算机密", top_k=10, mode=mode,
                            client="client-a", project="proj-a")
        ids_a = [r["id"] for r in hits_a]
        hits_b = svc.search("联合预算机密", top_k=10, mode=mode,
                            client="client-b", project="proj-b")
        assert [r["id"] for r in hits_b] == ids_a, \
            f"mode={mode}：检索结果不应随 client/project 变化"
        # 全共享同池：client-a 视角可召回他人（client-b）记忆
        assert any(x in ids_a for x in b_ids), f"mode={mode}：同池应召回他人记忆"


def test_bm25_全量检索不隔离():
    from kb.bm25 import BM25Index
    from kb.models import Record, RecordType
    idx = BM25Index()
    mine = Record(content="私有预算一百", client="c-a", project="p-a")
    other = Record(content="私有预算二百", client="c-b", project="p-b")
    shared = Record(content="共享预算三百", client="c-b", project="p-b",
                    type=RecordType.DOC_CHUNK)
    idx.add_many([mine, other, shared])

    # v3：BM25 全量检索（默认 filter_fn=None），他人 memory 与共享 doc 均命中
    ids = [h[0] for h in idx.search("预算", top_n=10)]
    assert mine.id in ids
    assert other.id in ids   # v3 全共享：他人 memory 不剔除
    assert shared.id in ids  # 共享 doc 同样命中
    assert idx.meta_of("不存在") is None