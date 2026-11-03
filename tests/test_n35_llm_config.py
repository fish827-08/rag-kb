"""N35 运行时 LLM 配置验收测试（2026-11-03）。

覆盖：
1) GET /api/v1/llm 默认 off，且不回显 API Key 明文；
2) POST /api/v1/llm/config 保存 Ollama 本地配置并落盘；
3) 非法 mode / 非法 URL → 422；
4) 重启（新建 KBService）后沿用保存的配置；
5) Ollama 不可达时模型列表返回 ok=false 而不是 500。
"""
import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def client(env_isolated):
    from fastapi.testclient import TestClient
    from kb.api import create_app
    from kb.config import Settings
    # 显式清空鉴权与云端 Key：避免本机 .env 里的真实 Key 影响断言
    with TestClient(create_app(settings=Settings(
            api_key="", llm_api_key="", deepseek_api_key=""))) as c:
        yield c


def test_默认off且不回显key(client):
    r = client.get("/api/v1/llm")
    assert r.status_code == 200
    info = r.json()
    assert info["mode"] == "off"
    assert info["status"] == "disabled"
    assert info["cloud"]["api_key_set"] is False
    assert "llm_config.json" in info["config_file"]


def test_保存Ollama配置并落盘(client):
    r = client.post("/api/v1/llm/config", json={
        "mode": "local",
        "ollama_base_url": "http://127.0.0.1:11434",
        "local_model": "qwen3:1.7b",
    })
    assert r.status_code == 200
    info = r.json()
    assert info["mode"] == "local"
    assert info["local"]["model"] == "qwen3:1.7b"
    assert info["local"]["base_url"] == "http://127.0.0.1:11434"

    saved = Path(info["config_file"])
    assert saved.exists()
    data = json.loads(saved.read_text(encoding="utf-8"))
    assert data["llm_mode"] == "local"
    assert data["llm_model"] == "qwen3:1.7b"


def test_非法模式返回422(client):
    r = client.post("/api/v1/llm/config", json={"mode": "banana"})
    assert r.status_code == 422


def test_非法URL返回422(client):
    r = client.post("/api/v1/llm/config",
                    json={"mode": "local", "ollama_base_url": "ftp://x"})
    assert r.status_code == 422
    # 统一错误 JSON 的 dict detail 会被扁平化，这里直接读 message
    assert "http" in r.json()["message"]


def test_云端key只回是否设置(client):
    secret = "sk-test-abcdefgh12345678"
    r = client.post("/api/v1/llm/config", json={
        "mode": "cloud",
        "cloud_base_url": "https://api.deepseek.com",
        "cloud_model": "deepseek-chat",
        "cloud_api_key": secret,
    })
    assert r.status_code == 200
    info = client.get("/api/v1/llm").json()
    assert info["cloud"]["api_key_set"] is True
    assert secret not in json.dumps(info, ensure_ascii=False)


def test_重启后沿用配置(env_isolated):
    from kb.config import Settings, get_settings
    from kb.service import KBService

    get_settings.cache_clear()
    first = KBService(Settings(llm_api_key="", deepseek_api_key=""))
    first.reconfigure_llm({"mode": "local", "local_model": "qwen3:1.7b"})

    get_settings.cache_clear()
    second = KBService(Settings(llm_api_key="", deepseek_api_key=""))
    assert second.settings.llm_mode == "local"
    assert second.settings.llm_model == "qwen3:1.7b"
    assert second.llm_info()["mode"] == "local"


def test_云端基址拒绝私有地址(client):
    """云端 Base URL 指向回环/私有 IP 视为 SSRF 风险，直接 422。"""
    r = client.post("/api/v1/llm/config",
                    json={"mode": "cloud", "cloud_base_url": "http://127.0.0.1:9999"})
    assert r.status_code == 422
    assert "私有" in r.json()["message"] or "本机" in r.json()["message"]


def test_ollama不可达返回okfalse(client):
    r = client.get("/api/v1/llm/ollama/models",
                   params={"base_url": "http://127.0.0.1:9"})
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["models"] == []
