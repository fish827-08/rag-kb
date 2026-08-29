def test_分词():
    from kb.bm25 import tokenize
    toks = tokenize("苹果Apple手机的价格")
    assert "苹果" in toks and "手机" in toks
    assert all(t.strip() for t in toks)


def test_检索排名与增删():
    from kb.bm25 import BM25Index
    from kb.models import Record
    idx = BM25Index()
    r1 = Record(content="苹果手机价格八千")   # 含"苹果"
    r2 = Record(content="香蕉苹果都很甜")      # 含"苹果"
    r3 = Record(content="今天天气不错")
    idx.rebuild([r1, r2, r3])
    hits = idx.search("苹果", top_n=2)
    ids = [h[0] for h in hits]
    assert r1.id in ids and r2.id in ids and r3.id not in ids
    assert hits[0][1] >= hits[1][1] > 0

    idx.remove(r1.id)
    ids = [h[0] for h in idx.search("苹果", top_n=5)]
    assert r1.id not in ids and r2.id in ids

    r4 = Record(content="苹果公司发布新品")
    idx.add(r4)
    assert r4.id in [h[0] for h in idx.search("苹果", top_n=5)]


def test_空索引():
    from kb.bm25 import BM25Index
    assert BM25Index().search("任意", top_n=3) == []