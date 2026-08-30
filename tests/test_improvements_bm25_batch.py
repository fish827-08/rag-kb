"""改进项 1B 验证：BM25 批量增删（add_many / remove_many）结果与逐条等价。"""


def test_add_many_批量入库():
    from kb.bm25 import BM25Index
    from kb.models import Record
    idx = BM25Index()
    rs = [Record(content=f"苹果手机价格{i}") for i in range(5)]
    idx.add_many(rs)
    hits = idx.search("苹果", top_n=10)
    ids = [h[0] for h in hits]
    assert len(ids) == 5
    assert all(r.id in ids for r in rs)


def test_remove_many_批量删除():
    from kb.bm25 import BM25Index
    from kb.models import Record
    idx = BM25Index()
    rs = [Record(content=f"香蕉牛奶{i}") for i in range(6)]
    idx.add_many(rs)
    idx.remove_many([r.id for r in rs[:4]])
    ids = [h[0] for h in idx.search("香蕉", top_n=10)]
    assert all(r.id not in ids for r in rs[:4])
    assert all(r.id in ids for r in rs[4:])
    # 空列表删除不报错
    idx.remove_many([])
