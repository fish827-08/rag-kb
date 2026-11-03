"""N33 Web UI 验收测试（2026-11-03 spec）。

覆盖：
1) /ui 单文件页面可访问且包含关键端点；
2) 根导航页包含 /ui 入口；
3) KB_API_KEY 非空时静态页免鉴权、数据端点仍 401；
4) /ask 的 top_k 参数校验（0 与 51 越界 → 422）；
5) KBService.ask(top_k=N) 确实把 N 透传给检索。
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client_no_key(env_isolated):
    """空 key（默认）：不鉴权。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.config import Settings
    with TestClient(create_app(settings=Settings(api_key=""))) as c:
        yield c


@pytest.fixture
def client_with_key(env_isolated):
    """有 key：启用鉴权。"""
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.config import Settings
    with TestClient(create_app(settings=Settings(api_key="secret123"))) as c:
        yield c


def test_UI页面可访问且含关键端点(client_no_key):
    """GET /ui 返回 HTML，页面里能看到上传与问答用到的两个端点。"""
    r = client_no_key.get("/ui")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")
    body = r.text
    assert "/api/v1/documents" in body
    assert "/api/v1/ask" in body


def test_根导航页含UI入口(client_no_key):
    """GET / 的极简导航页新增了 /ui 链接。"""
    r = client_no_key.get("/")
    assert r.status_code == 200
    assert 'href="/ui"' in r.text


def test_UI中英双语(client_no_key):
    """页面自带语言切换，且同时包含中英文文案（README 也有中英两份）。"""
    body = client_no_key.get("/ui").text
    assert 'id="langToggle"' in body
    assert "本地知识库" in body
    assert "Local knowledge base" in body


def test_UI支持文件夹导入与提示(client_no_key):
    """支持整文件夹选择（webkitdirectory）、保存提示 toast、内联 favicon（避免 404）。"""
    body = client_no_key.get("/ui").text
    assert "webkitdirectory" in body
    assert 'id="toast"' in body
    assert 'rel="icon"' in body and "data:image/svg+xml" in body


def test_有Key时静态页放行_数据端点仍鉴权(client_with_key):
    """静态页面本身不含数据，免 Key；数据端点必须带 Key。"""
    assert client_with_key.get("/ui").status_code == 200
    assert client_with_key.get("/").status_code == 200
    assert client_with_key.get("/api/v1/documents").status_code == 401


def test_ask_top_k_越界返回422(client_no_key):
    """top_k=0 与 top_k=51 均超出 [1,50]，pydantic 校验失败转 422。"""
    for bad in (0, 51):
        r = client_no_key.post("/api/v1/ask",
                               json={"question": "测试问题", "top_k": bad})
        assert r.status_code == 422


def test_ask_top_k_默认合法(client_no_key):
    """不传 top_k 时走默认 5；LLM 关闭返回 503 而不是 422（说明参数校验已通过）。"""
    r = client_no_key.post("/api/v1/ask", json={"question": "测试问题"})
    assert r.status_code in (200, 503)


def test_ask将top_k透传给检索(env_isolated, monkeypatch):
    """KBService.ask(top_k=8) 时，检索调用收到的 top_k 必须是 8（而不是写死的 5）。"""
    from kb.config import Settings, get_settings
    from kb.llm import LLMStatus
    from kb.service import KBService

    get_settings.cache_clear()
    settings = Settings(llm_mode="local")

    class FakeLLM:
        status = LLMStatus.LOCAL

        def chat(self, messages, max_tokens=None):
            return "stub-answer"

    svc = KBService(settings, llm=FakeLLM())
    captured = {}

    def fake_search(query, top_k=5, **kwargs):
        captured["top_k"] = top_k
        return []

    monkeypatch.setattr(svc, "search", fake_search)
    result = svc.ask("测试问题", top_k=8)
    assert captured["top_k"] == 8
    assert result["answer"] == "stub-answer"
    assert result["sources"] == []
