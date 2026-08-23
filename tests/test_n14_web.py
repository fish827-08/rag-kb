import pytest

pytestmark = pytest.mark.integration

HTML = """<html><head><title>测试页</title></head><body>
<nav>导航噪声 导航噪声</nav>
<article><h1>混合检索指南</h1><p>向量检索与关键词检索通过 RRF 融合，"
"能同时兼顾语义与精确匹配，是本地知识库的主流方案。</p></article>
<footer>页脚噪声</footer></body></html>"""


def test_网页抓取入库(env_isolated, monkeypatch):
    from kb.service import KBService
    import kb.ingest as ingest

    def fake_fetch(url: str) -> str:
        assert url == "https://example.com/rag"
        return HTML
    monkeypatch.setattr(ingest, "_fetch_html", fake_fetch)
    s = KBService()
    r = s.add_webpage("https://example.com/rag")
    assert r["source"] == "https://example.com/rag" and r["chunks"] >= 1
    hits = s.search("RRF 融合", mode="keyword")
    assert hits and hits[0]["type"] == "web_chunk"


def test_抓取失败转400(env_isolated, monkeypatch):
    import kb.ingest as ingest
    monkeypatch.setattr(ingest, "_fetch_html",
                        lambda url: (_ for _ in ()).throw(Exception("404")))
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/ingest/web", json={"url": "https://bad.com"})
        assert r.status_code == 400 and "error" in r.json()
