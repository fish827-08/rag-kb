import pytest

pytestmark = pytest.mark.integration


def test_txt_md解析与切分(env_isolated):
    from kb.ingest import parse_file, chunk_text
    f = env_isolated / "note.txt"
    f.write_text("A" * 600 + "\n" + "B" * 600, encoding="utf-8")
    assert len(parse_file(f)) == 1201
    chunks = chunk_text(parse_file(f), size=500, overlap=100)
    assert all(len(c) <= 500 for c in chunks)
    assert len(chunks) >= 3  # 600+600 必然切出 3 块以上


def test_不支持的格式(env_isolated):
    from kb.ingest import parse_file, UnsupportedFormatError
    f = env_isolated / "x.exe"
    f.write_bytes(b"MZ...")
    with pytest.raises(UnsupportedFormatError):
        parse_file(f)


def test_docx解析(env_isolated):
    from docx import Document
    from kb.ingest import parse_file
    d = Document()
    d.add_paragraph("这是一段docx测试内容")
    p = env_isolated / "t.docx"; d.save(str(p))
    assert "docx测试内容" in parse_file(p)


def test_文档入库与删除级联(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    f = env_isolated / "book.txt"
    f.write_text("第一章：混合检索的原理。" * 30, encoding="utf-8")
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/documents", json={"path": str(f)})
        assert r.status_code == 200 and r.json()["chunks"] >= 1
        src = r.json()["source"]
        hits = c.post("/api/v1/search",
                      json={"query": "混合检索的原理", "mode": "keyword"}).json()["results"]
        assert hits
        assert c.delete(f"/api/v1/documents/{src}").json()["deleted"] >= 1
