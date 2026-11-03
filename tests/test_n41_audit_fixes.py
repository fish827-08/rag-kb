"""审计报告修复回归（2026-11-03）：表格劫持、检索阈值、计数、API 校验、SSRF 等。"""
import pytest

from kb.models import Record, RecordType


# ---- 表格问答收紧 ----

def test_表格无关问题不劫持():
    from kb.tables import query_rows
    rows = [{"城市": "北京", "人口": "2189", "面积": "16410"}]
    assert query_rows(rows, "北京有哪些旅游景点") is None


def test_表格前缀只取最长命中():
    from kb.tables import query_rows
    rows = [{"用户": "用户5", "分数": "10"},
            {"用户": "用户55", "分数": "20"},
            {"用户": "用户550", "分数": "30"}]
    assert query_rows(rows, "用户550的分数") == "用户550：分数=30"


def test_单元格换行不丢字段():
    from kb.tables import parse_row_text, row_to_text
    text = row_to_text(["姓名", "电话", "地址"],
                       ["李雷", "123", "北京市\n海淀区"])
    assert "电话=123" in text and "地址=北京市 海淀区" in text
    assert parse_row_text(text)["电话"] == "123"


def test_XLSX公式列不丢(tmp_path):
    openpyxl = pytest.importorskip("openpyxl")
    from kb.tables import parse_table_file
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["姓名", "语文", "数学", "总分"])
    ws.append(["钱七", 90, 80])
    ws["D2"] = "=SUM(B2:C2)"
    p = tmp_path / "公式.xlsx"
    wb.save(p)
    tables = parse_table_file(p)
    assert tables[0]["headers"][-1] == "总分"
    assert tables[0]["rows"][0][-1].startswith("=SUM")


# ---- 检索阈值 / 过滤顺序 ----

class _Rec:
    def __init__(self, rid, score_type="memory", tags=None):
        self.id = rid
        self.content = f"内容 {rid}"
        self.type = RecordType(score_type)
        self.tags = tags or []
        self.source = "s"
        self.created_at = "2026-01-01T00:00:00"
        self.updated_at = self.created_at
        self.access_count = 0
        self.last_accessed = ""
        self.agent_id = "default"


class _Store:
    def __init__(self, rows):
        self.rows = rows

    def query(self, vec, top_k=10, where=None):
        return self.rows[:top_k]

    def get_many(self, ids):
        return {r.id: r for r, _ in self.rows if r.id in ids}

    def increment_access(self, ids):
        pass


class _Embedder:
    def embed_query(self, query):
        return [0.1, 0.2]


class _BM25:
    def search(self, query, top_n=10, filter_fn=None):
        return []

    def meta_of(self, rid):
        return None


class _Settings:
    def __init__(self, min_vec=0.30):
        self.min_vector_score = min_vec
        self.decay_enabled = False
        self.freshness_enabled = False
        self.sparse_enabled = False
        self.rerank_enabled = False

    def __getattr__(self, name):
        return 0


def test_向量低于阈值不返回():
    from kb.retriever import HybridRetriever
    rows = [(_Rec("low"), 0.10)]
    ret = HybridRetriever(_Store(rows), _BM25(), _Embedder(),
                          settings=_Settings())
    assert ret.search("无关xyz", top_k=3, mode="vector") == []


def test_type过滤在截断前生效():
    from kb.retriever import HybridRetriever
    rows = [(_Rec("mem"), 0.90), (_Rec("doc", "doc_chunk"), 0.80)]
    ret = HybridRetriever(_Store(rows), _BM25(), _Embedder(),
                          settings=_Settings())
    hits = ret.search("查询", top_k=1, mode="vector", type="doc_chunk")
    assert [h["id"] for h in hits] == ["doc"]


# ---- 项目计数 / 更新安全 ----

@pytest.mark.integration
def test_项目记录数重启不丢(env_isolated):
    from kb.service import KBService
    svc = KBService()
    svc.add_memory("项目计数记忆", project="cnt")
    del svc
    svc2 = KBService()
    assert svc2.stats()["records"] >= 1


@pytest.mark.integration
def test_更新嵌入失败保留旧内容(env_isolated, monkeypatch):
    from kb.service import KBService
    svc = KBService()
    rec = svc.add_memory("原始内容")
    monkeypatch.setattr(svc.embedder, "embed_texts",
                        lambda texts: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        svc.update_memory(rec.id, content="新内容")
    assert svc.get_memory(rec.id).content == "原始内容"


# ---- tags / audit / API ----

def test_标签含逗号可往返():
    r = Record(content="x", tags=["a,b", "c"])
    meta = r.to_metadata()
    r2 = Record.from_chroma(r.id, r.content, meta)
    assert r2.tags == ["a,b", "c"]
    assert Record.from_chroma("id", "x", {"tags": "人物,生日"}).tags == ["人物", "生日"]


def test_审计文件名client点号():
    from kb.audit import parse_agent_file_name
    info = parse_agent_file_name("my.client__proj.log")
    assert info["client"] == "my.client" and info["project"] == "proj"


def test_非ASCII_KEY比较不崩():
    from kb.api import _keys_equal
    assert _keys_equal("中文密钥", "中文密钥")
    assert not _keys_equal("中文密钥", "错误密钥")


class _Client:
    @staticmethod
    def make(monkeypatch=None):
        from fastapi.testclient import TestClient
        from kb.api import create_app
        return TestClient(create_app())


@pytest.mark.integration
def test_PATCH空内容422(env_isolated):
    c = _Client.make()
    with c:
        rid = c.post("/api/v1/memories", json={"content": "有效"}).json()["id"]
        r = c.patch(f"/api/v1/memories/{rid}", json={"content": "   "})
        assert r.status_code == 422
        assert r.json()["error"] == "INVALID_ARGUMENT"


@pytest.mark.integration
def test_422统一错误格式与负分页(env_isolated):
    c = _Client.make()
    with c:
        r = c.post("/api/v1/search", json={"query": "x", "top_k": 0})
        assert r.status_code == 422
        assert set(r.json()) == {"error", "message"}
        assert c.get("/api/v1/memories", params={"limit": -1}).status_code == 422
        assert c.get("/api/v1/memories", params={"offset": -1}).status_code == 422


# ---- SSRF / 内部记录 ----

def test_SSRF拒绝回环与元数据地址():
    from kb.ingest import WebFetchError, _validate_public_url
    with pytest.raises(WebFetchError):
        _validate_public_url("http://127.0.0.1/x")
    with pytest.raises(WebFetchError):
        _validate_public_url("http://169.254.169.254/latest/meta-data")


def test_C_locale回退English(env_isolated, monkeypatch):
    import kb.i18n as i18n
    monkeypatch.setattr(i18n.locale, "getlocale", lambda: ("C", None))
    monkeypatch.setenv("LC_ALL", "C")
    monkeypatch.setenv("LANG", "")
    monkeypatch.setenv("KB_MEMORY_LANG", "auto")
    from kb.config import get_settings
    get_settings.cache_clear()
    assert i18n.memory_lang() == "English"


def test_UI语言切换不删除LLM下拉框():
    from pathlib import Path
    html = (Path(__file__).resolve().parents[1]
            / "kb" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'label data-i18n="llm.modeLabel"' not in html
    assert '<span data-i18n="llm.modeLabel"></span>' in html
    assert 'id="llmMode"' in html


@pytest.mark.integration
def test_旧版xls明确拒绝(env_isolated, tmp_path):
    from kb.service import KBService, UnsupportedFormatError
    p = tmp_path / "old.xls"
    p.write_bytes(b"not really xls")
    with pytest.raises(UnsupportedFormatError):
        KBService().add_document(p)


@pytest.mark.integration
def test_内部监控记录不出现在文档列表(env_isolated):
    from kb.service import KBService
    svc = KBService()
    svc.add_memory("监控摘要", source="kb-monitor")
    assert svc.list_documents() == []
