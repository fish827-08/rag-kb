def test_往返序列化():
    from kb.models import Record, RecordType
    r = Record(content="张三的生日是3月15日", type=RecordType.MEMORY,
               tags=["人物", "生日"], source="test")
    meta = r.to_metadata()
    assert "content" not in meta
    assert meta["tags"] == "人物,生日"
    assert meta["type"] == "memory"
    r2 = Record.from_chroma(r.id, r.content, meta)
    assert r2 == r  # 全字段一致（含时间戳）


def test_默认值():
    from kb.models import Record, RecordType
    r = Record(content="x")
    assert r.type is RecordType.MEMORY
    assert r.namespace == "default"
    assert r.importance == 0.5
    assert r.tags == [] and r.source is None
    assert len(r.id) == 32  # uuid4().hex