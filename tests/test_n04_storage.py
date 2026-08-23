import pytest

pytestmark = pytest.mark.integration

V = {"a": [1, 0, 0, 0, 0, 0, 0, 0], "b": [0, 1, 0, 0, 0, 0, 0, 0],
     "c": [0.9, 0.1, 0, 0, 0, 0, 0, 0]}


def _store(env_isolated):
    from kb.storage import ChromaStore
    from kb.models import Record, RecordType
    store = ChromaStore(env_isolated / "chroma")
    recs = [
        Record(content="苹果手机价格", tags=["数码"], source="doc1"),
        Record(content="香蕉的营养价值", tags=["水果"], source="doc1"),
        Record(content="苹果公司的历史", tags=["数码", "公司"], source="doc2"),
        Record(content="网页抓取笔记", type=RecordType.WEB_CHUNK, source="http://x.cn"),
    ]
    store.add(recs, [V["a"], V["b"], V["c"], V["b"]])
    return store, recs


def test_增查删(env_isolated):
    store, recs = _store(env_isolated)
    assert store.get(recs[0].id).content == "苹果手机价格"
    assert store.get("不存在") is None
    store.delete([recs[3].id])
    assert store.get(recs[3].id) is None


def test_按source级联删除(env_isolated):
    store, recs = _store(env_isolated)
    assert store.delete_by_source("doc1") == 2
    assert store.delete_by_source("doc1") == 0
    assert store.get(recs[0].id) is None


def test_列表过滤与分页(env_isolated):
    store, recs = _store(env_isolated)
    recs_all, total = store.list_records()
    assert total == 4
    _, t = store.list_records(type="web_chunk"); assert t == 1
    _, t = store.list_records(source="doc1"); assert t == 2
    _, t = store.list_records(q="苹果"); assert t == 2
    _, t = store.list_records(tag="数码"); assert t == 2  # Python 侧过滤
    page, total = store.list_records(limit=2, offset=2)
    assert len(page) == 2 and total == 4


def test_向量检索排序(env_isolated):
    store, _ = _store(env_isolated)
    hits = store.query(V["a"], top_k=3)
    assert hits[0][0].content == "苹果手机价格"      # 正交命中，score=1.0
    assert hits[0][1] > hits[1][1] > 0
    assert len(hits) == 3