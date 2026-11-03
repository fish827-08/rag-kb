"""v2.4 页面修复：favicon 不再 404、LLM 配置不再强绑定 DeepSeek、图谱区块存在。

设计：docs/superpowers/specs/2026-11-03-kb-v2.4-design.md §5。
"""
import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        yield c


def test_favicon返回200(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert "svg" in r.headers["content-type"]


def test_导航页声明favicon(client):
    html = client.get("/").text
    assert "/favicon.ico" in html


def test_UI包含图谱区块(client):
    html = client.get("/ui").text
    assert "graphSvg" in html and "graph.title" in html
    assert "/api/v1/graph" in html


def test_LLM默认不绑定DeepSeek(env_isolated):
    from kb.config import Settings
    s = Settings(_env_file=None)
    assert s.llm_base_url == "" and s.llm_cloud_model == ""


def test_云端缺BaseURL保存失败(env_isolated, monkeypatch):
    """配置了 Key 却缺 Base URL/模型 → 422，前端据此显示失败提示。"""
    for key in ("KB_LLM_MODE", "KB_LLM_BASE_URL", "KB_LLM_CLOUD_MODEL",
                "KB_LLM_API_KEY", "KB_DEEPSEEK_BASE_URL",
                "KB_DEEPSEEK_MODEL", "KB_DEEPSEEK_API_KEY"):
        monkeypatch.setenv(key, "")
    from kb.config import get_settings
    get_settings.cache_clear()
    from fastapi.testclient import TestClient
    from kb.api import create_app
    with TestClient(create_app()) as c:
        r = c.post("/api/v1/llm/config",
                   json={"mode": "cloud", "cloud_api_key": "sk-test"})
    assert r.status_code == 422
    assert "Base URL" in r.json()["message"]
