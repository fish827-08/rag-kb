import httpx
import pytest


def _client(env_isolated, monkeypatch, ollama_up=True, key=""):
    from kb.config import get_settings
    from kb.llm import LLMClient
    get_settings.cache_clear()
    monkeypatch.setenv("KB_DEEPSEEK_API_KEY", key)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            if not ollama_up:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"data": [{"id": "qwen3:4b"}]})
        if request.url.path == "/api/chat":
            body = json.loads(request.content)
            assert body["think"] is False                      # 护栏：关思考
            assert body["options"]["num_ctx"] == 4096          # 护栏：上下文
            assert body["options"]["temperature"] == 0.2
            return httpx.Response(200, json={
                "message": {"content": "本地回答"}, "eval_count": 5})
        raise AssertionError(f"意外请求: {request.url}")

    transport = httpx.MockTransport(handler)
    return LLMClient(get_settings(), http_client=httpx.Client(transport=transport,
                                                              base_url="http://test"))


import json


def test_本地可用与护栏参数(env_isolated, monkeypatch):
    c = _client(env_isolated, monkeypatch)
    assert c.status.value == "local"
    assert c.chat([{"role": "user", "content": "hi"}]) == "本地回答"


def test_模式解析矩阵(env_isolated, monkeypatch):
    from kb.llm import LLMStatus
    from kb.config import get_settings
    # local 模式 + Ollama 挂 → DISABLED
    monkeypatch.setenv("KB_LLM_MODE", "local")
    c = _client(env_isolated, monkeypatch, ollama_up=False)
    assert c.status is LLMStatus.DISABLED
    # cloud 模式 + 有 Key → CLOUD
    monkeypatch.setenv("KB_LLM_MODE", "cloud")
    get_settings.cache_clear()
    c = _client(env_isolated, monkeypatch, ollama_up=False, key="sk-test")
    assert c.status is LLMStatus.CLOUD
    # auto + 都无 → DISABLED
    monkeypatch.setenv("KB_LLM_MODE", "auto")
    get_settings.cache_clear()
    c = _client(env_isolated, monkeypatch, ollama_up=False)
    assert c.status is LLMStatus.DISABLED


def test_禁用时调用报错(env_isolated, monkeypatch):
    from kb.llm import LLMClient, LLMError
    c = _client(env_isolated, monkeypatch, ollama_up=False)
    with pytest.raises(LLMError):
        c.chat([{"role": "user", "content": "hi"}])
