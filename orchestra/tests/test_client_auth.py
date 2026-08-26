# -*- coding: utf-8 -*-
"""N20 客户端带 key 适配单测（TASK-0064）。

三态：空 key 不带头 / 有 key 带 X-API-Key 头 / 401 提示检查 KB_API_KEY；
另测 _load_api_key 加载器（环境变量优先 / .env 最小解析）。
全部 mock urllib.request.urlopen，不打真服务。
"""
import io
import json
import urllib.error
import urllib.request

import pytest

import client


class _FakeResp:
    """模拟 urlopen 返回的响应对象（支持 with 语句）。"""

    def __init__(self, payload: dict):
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


@pytest.fixture
def captured(monkeypatch):
    """拦截 urllib.request.urlopen：记录 Request 对象，返回预置响应。"""
    box = {"req": None}

    def fake_urlopen(req, timeout=None):
        box["req"] = req
        return _FakeResp({"ok": True})

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("KB_API_KEY", raising=False)
    return box


def test_空key_不带头(captured, monkeypatch):
    monkeypatch.setattr(client, "_load_api_key", lambda env_file=None: "")
    result = client._request("GET", "/memories")
    assert result == {"ok": True}
    assert "X-API-Key" not in captured["req"].headers
    assert captured["req"].get_header("Content-type") == "application/json"


def test_有key_带X_API_Key头(captured, monkeypatch):
    monkeypatch.setattr(client, "_load_api_key", lambda env_file=None: "test-key-123")
    client._request("GET", "/memories")
    assert captured["req"].get_header("X-api-key") == "test-key-123"


def test_401_错误信息含检查KB_API_KEY提示(captured, monkeypatch):
    def fake_urlopen(req, timeout=None):
        raise urllib.error.HTTPError(
            req.full_url, 401, "Unauthorized", {},
            io.BytesIO(b'{"error":"UNAUTHORIZED","message":"missing or invalid api key"}'))

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client, "_load_api_key", lambda env_file=None: "")
    with pytest.raises(RuntimeError, match="检查 KB_API_KEY"):
        client._request("GET", "/memories")


def test_load_环境变量优先(monkeypatch):
    monkeypatch.setenv("KB_API_KEY", "env-key")
    assert client._load_api_key() == "env-key"


def test_load_环境变量空白_回落env文件(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_API_KEY", "  ")
    env_file = tmp_path / ".env"
    env_file.write_text(
        '# 注释行\nKB_LLM_MODE=auto\nKB_API_KEY="file-key-456"\nKB_DEVICE=\n',
        encoding="utf-8")
    assert client._load_api_key(env_file) == "file-key-456"


def test_load_无env文件_返回空串(tmp_path, monkeypatch):
    monkeypatch.delenv("KB_API_KEY", raising=False)
    assert client._load_api_key(tmp_path / ".env") == ""


def test_load_env文件无该键_返回空串(tmp_path, monkeypatch):
    monkeypatch.delenv("KB_API_KEY", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("KB_API_KEY=\nKB_LLM_MODE=auto\n", encoding="utf-8")
    assert client._load_api_key(env_file) == ""
